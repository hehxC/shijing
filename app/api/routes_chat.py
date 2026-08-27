import hashlib
import json

from fastapi import APIRouter, Depends, Header, HTTPException  # FastAPI 的路由器，用来把一组相关的接口组织在一起
from starlette.responses import StreamingResponse  # 流式响应：数据边生成边返回，不需要等全部处理完

from app.models.schemas import ChatRequest  # 请求体的 Pydantic 模型，定义了接口接收什么字段、什么类型
from app.models.design_run import DesignRunOperation
from app.models.user import User
from app.observability import current_request_id
from app.service.auth_service import get_current_user
from app.service.conversation_service import (
    begin_user_turn,
    complete_turn,
    fail_turn,
)
from app.service.chat_service import stream_chat  # 业务逻辑：向大模型发消息并获取流式回复
from app.garden_styles import get_garden_style, list_garden_styles
from app.service.design_session_service import save_selected_style
from app.service.observation_service import observe_design_run
from app.api.rate_limit import enforce_rate_limit
from app.service.rate_limit_service import (
    RateLimitDecision,
    RateLimitService,
    RateLimitSettings,
    get_rate_limit_service,
)

router = APIRouter()  # 创建一个路由器实例，main.py 会通过 include_router 把它挂载到主应用上


def scoped_session_id(user_id: int, session_id: str | None) -> str:
    """把浏览器会话标识收敛为用户隔离的服务端设计会话标识。"""
    raw = session_id or "default"
    value = f"user:{user_id}:{raw}"
    if len(value) <= 128:
        return value
    return f"user:{user_id}:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def design_run_operation(req: ChatRequest) -> DesignRunOperation:
    """把前端消息类型映射为用户可感知的顶层设计任务类型。"""
    if req.generate_effect_image or req.message_type == "effect":
        return DesignRunOperation.EFFECT_GENERATION
    if req.message_type == "recognize":
        return DesignRunOperation.VISION_ANALYSIS
    if req.message_type == "quote":
        return DesignRunOperation.MATERIAL_QUOTE
    return DesignRunOperation.CHAT


@router.get("/api/styles", tags=["styles"])
def garden_styles():
    """返回内置庭院风格，前端和生成提示词共用同一份定义。"""
    return list_garden_styles()


@router.post("/chat")
async def chat(
    req: ChatRequest,
    current_user: User = Depends(get_current_user),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    limiter: RateLimitService = Depends(get_rate_limit_service),
):
    """
    聊天接口：接收用户消息，返回模型回复的流式响应。

    请求体 JSON 格式:
    - 纯文本: {"message": "你好"}
    - 会话: {"message": "你好", "session_id": "browser-session-id"}
    响应: text/plain 类型的流式数据，逐 token 返回
    """
    if not req.session_id:
        raise HTTPException(status_code=400, detail="缺少会话标识")
    session_id = scoped_session_id(current_user.id, req.session_id)
    selected_style = get_garden_style(req.style_id)
    if req.generate_effect_image and selected_style is None:
        raise HTTPException(status_code=400, detail="请选择有效的庭院风格后再生成效果图")

    settings = RateLimitSettings.from_environment()
    concurrency_lease = None
    idempotency_reservation = None

    if req.generate_effect_image:
        normalized_key = (idempotency_key or "").strip()
        if normalized_key and len(normalized_key) > 128:
            raise HTTPException(status_code=400, detail="Idempotency-Key 不能超过 128 个字符")

        if normalized_key:
            fingerprint = hashlib.sha256(
                json.dumps(
                    req.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            idempotency, idempotency_reservation = limiter.begin_idempotent(
                f"effect:user:{current_user.id}:{normalized_key}",
                fingerprint=fingerprint,
                ttl_seconds=settings.idempotency_ttl_seconds,
            )
            if idempotency.action == "conflict":
                raise HTTPException(
                    status_code=409,
                    detail="同一个 Idempotency-Key 不能用于不同的效果图请求",
                )
            if idempotency.action == "in_progress":
                enforce_rate_limit(
                    RateLimitDecision(
                        False,
                        0,
                        1,
                        "相同的效果图请求正在处理中，请稍后查看结果",
                    )
                )
            if idempotency.action == "replay":
                headers = {"X-Idempotent-Replay": "true"}
                if idempotency.user_message_id is not None:
                    headers["X-User-Message-Id"] = str(idempotency.user_message_id)
                return StreamingResponse(
                    iter((idempotency.response_body or "",)),
                    media_type="text/plain",
                    headers=headers,
                )

        concurrency, concurrency_lease = limiter.acquire_concurrency(
            f"effect-concurrency:user:{current_user.id}",
            limit=settings.effect_concurrency,
            reason="已有一个效果图正在生成，请等待完成后再试",
        )
        if not concurrency.allowed:
            if idempotency_reservation is not None:
                idempotency_reservation.fail()
            enforce_rate_limit(concurrency)

        quota_already_consumed = bool(
            idempotency_reservation and idempotency_reservation.quota_consumed
        )
        if not quota_already_consumed:
            quota = limiter.consume_daily(
                f"effect-daily:user:{current_user.id}",
                limit=settings.effect_daily,
                reason="今日效果图生成额度已用完，请明天再试",
            )
            if not quota.allowed:
                concurrency_lease.release()
                if idempotency_reservation is not None:
                    idempotency_reservation.fail()
                enforce_rate_limit(quota)
            if idempotency_reservation is not None:
                idempotency_reservation.mark_quota_consumed()
    else:
        is_vision = req.message_type == "recognize"
        decision = limiter.allow(
            f"{'vision' if is_vision else 'chat'}:user:{current_user.id}",
            limit=(
                settings.vision_per_minute
                if is_vision
                else settings.chat_per_minute
            ),
            window_seconds=60,
            reason=(
                "视觉分析请求过于频繁，请稍后再试"
                if is_vision
                else "聊天请求过于频繁，请稍后再试"
            ),
        )
        enforce_rate_limit(decision)

    try:
        # 限流通过后才允许创建业务记录或调用模型。
        if req.generate_effect_image and selected_style is not None:
            save_selected_style(session_id, selected_style.id)

        logged_message = (req.display_message or req.message).strip()
        if req.generate_effect_image and selected_style is not None:
            suffix = f"：{req.message.strip()}" if req.message.strip() else ""
            logged_message = f"生成「{selected_style.name}」效果图{suffix}"

        user_message = begin_user_turn(
            user_id=current_user.id,
            session_id=session_id,
            client_session_id=req.session_id,
            display_content=logged_message,
            request_text=req.message,
            message_type=req.message_type,
            style_id=selected_style.id if selected_style else None,
        )
        # 中间件生成或接收的 request_id 会贯穿响应头、结构化日志和两张观测表。
        request_id = current_request_id()
    except BaseException:
        if idempotency_reservation is not None:
            idempotency_reservation.fail()
        if concurrency_lease is not None:
            concurrency_lease.release()
        raise

    def persistent_stream():
        chunks: list[str] = []
        completed = False
        try:
            with observe_design_run(
                request_id=request_id,
                user_id=current_user.id,
                session_id=session_id,
                operation=design_run_operation(req),
            ):
                try:
                    for chunk in stream_chat(
                        req.message,
                        None,
                        session_id,
                        selected_style=selected_style,
                        force_generate_effect_image=req.generate_effect_image,
                    ):
                        chunks.append(chunk)
                        yield chunk
                    response_body = "".join(chunks)
                    complete_turn(user_message.id, response_body)
                    completed = True
                    if idempotency_reservation is not None:
                        idempotency_reservation.complete(
                            response_body,
                            user_message_id=user_message.id,
                        )
                finally:
                    if not completed:
                        fail_turn(user_message.id)
                        if idempotency_reservation is not None:
                            idempotency_reservation.fail()
        finally:
            if concurrency_lease is not None:
                concurrency_lease.release()

    return StreamingResponse(
        persistent_stream(),
        media_type="text/plain",   # 告诉前端响应内容类型是纯文本
        headers={"X-User-Message-Id": str(user_message.id)},
    )
