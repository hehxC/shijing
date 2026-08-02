import hashlib

from fastapi import APIRouter, Depends, HTTPException  # FastAPI 的路由器，用来把一组相关的接口组织在一起
from starlette.responses import StreamingResponse  # 流式响应：数据边生成边返回，不需要等全部处理完

from app.models.schemas import ChatRequest  # 请求体的 Pydantic 模型，定义了接口接收什么字段、什么类型
from app.models.user import User
from app.service.auth_service import get_current_user
from app.service.conversation_service import (
    begin_user_turn,
    complete_turn,
    fail_turn,
)
from app.service.chat_service import stream_chat  # 业务逻辑：向大模型发消息并获取流式回复
from app.garden_styles import get_garden_style, list_garden_styles
from app.service.design_session_service import save_selected_style

router = APIRouter()  # 创建一个路由器实例，main.py 会通过 include_router 把它挂载到主应用上


def scoped_session_id(user_id: int, session_id: str | None) -> str:
    raw = session_id or "default"
    value = f"user:{user_id}:{raw}"
    if len(value) <= 128:
        return value
    return f"user:{user_id}:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


@router.get("/api/styles", tags=["styles"])
def garden_styles():
    """返回内置庭院风格，前端和生成提示词共用同一份定义。"""
    return list_garden_styles()


@router.post("/chat")
async def chat(req: ChatRequest, current_user: User = Depends(get_current_user)):
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

    def persistent_stream():
        chunks: list[str] = []
        completed = False
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
            complete_turn(user_message.id, "".join(chunks))
            completed = True
        finally:
            if not completed:
                fail_turn(user_message.id)

    return StreamingResponse(
        persistent_stream(),
        media_type="text/plain",   # 告诉前端响应内容类型是纯文本
        headers={"X-User-Message-Id": str(user_message.id)},
    )
