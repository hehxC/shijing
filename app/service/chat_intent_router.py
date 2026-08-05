from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import time

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_deepseek import ChatDeepSeek
from langchain_google_genai import ChatGoogleGenerativeAI

from app.service.image_generation_service import (
    is_effect_image_edit_request,
    needs_effect_image,
    references_generated_image,
)


load_dotenv()

# 路由决策采集日志路径：默认写入 data/ 目录（已加入 .gitignore），
# 可用环境变量 ROUTER_DECISION_LOG_PATH 覆盖，便于测试时隔离日志文件。
ROUTER_DECISION_LOG_PATH = Path(
    os.getenv("ROUTER_DECISION_LOG_PATH")
    or (Path(__file__).resolve().parents[2] / "data" / "router_decisions.jsonl")
)


def _log_router_decision(record: dict) -> None:
    """把一次路由决策以 JSON 行追加到采集日志。

    日志只用于攒评测数据，任何失败都不能影响聊天主流程，因此全程兜底。
    """
    try:
        # JSONL 格式：每行一条决策记录，追加写入
        path = ROUTER_DECISION_LOG_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:
        pass

VALID_INTENTS = {
    "generate_effect_image",
    "analyze_image",
    "estimate_price",
    "query_material",
    "general_chat",
}
VALID_IMAGE_SOURCES = {"uploaded", "reference", "generated", "none"}

PRICE_PATTERN = re.compile(r"(?:价格|多少钱|报价|估价|预估|预算|费用|单价|平米|㎡|m2|平方)")
MATERIAL_QUERY_PATTERN = re.compile(r"(?:石材|石头|材料|规格|颜色|色系|莱姆石|大理石|花岗岩|洞石)")
IMAGE_ANALYSIS_PATTERN = re.compile(
    r"(?:识图|识别.{0,8}(?:图|图片|照片|效果图)|分析.{0,8}(?:图|图片|照片|效果图)|"
    r"看(?:一下)?(?:这张|这幅|这个|上一张|上张|刚才).{0,8}(?:图|图片|照片|效果图)|"
    r"(?:图|图片|照片|效果图).{0,8}(?:里|中|上).{0,12}(?:有什么|有哪些|是什么|识别|分析))"
)


@dataclass(frozen=True)
class ChatIntent:
    intent: str
    use_image: str
    confidence: float
    reason: str = ""


@lru_cache(maxsize=4)
def _get_router_model(model_name: str):
    """Router 只做文本分类，不绑定工具，也不写入 checkpoint。"""
    if model_name.startswith("gemini"):
        return ChatGoogleGenerativeAI(
            model=model_name,
            api_key=os.getenv("GEMINI_KEY") or os.getenv("GOOGLE_API_KEY"),
        )

    if model_name == "qwen3.7-max-2026-06-08":
        return init_chat_model(
            model=model_name,
            model_provider="openai",
            base_url=os.getenv("DASHSCOPE_BASE_URL"),
            api_key=os.getenv("DASHSCOPE_API_KEY"),
        )

    return ChatDeepSeek(
        model=model_name,
        api_key=os.getenv("DEEPSEEK_API_KEY"),
    )


def _extract_json(text: str) -> dict | None:
    text = text.strip()
    if not text:
        return None

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*}", text, flags=re.S)
    if not match:
        return None

    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _normalize_intent(payload: dict | None, fallback: ChatIntent) -> ChatIntent:
    if not isinstance(payload, dict):
        return fallback

    intent = str(payload.get("intent") or "").strip()
    use_image = str(payload.get("use_image") or "").strip()
    reason = str(payload.get("reason") or "").strip()
    try:
        confidence = float(payload.get("confidence", fallback.confidence))
    except (TypeError, ValueError):
        confidence = fallback.confidence

    if intent not in VALID_INTENTS:
        return fallback
    if use_image not in VALID_IMAGE_SOURCES:
        use_image = fallback.use_image

    return ChatIntent(
        intent=intent,
        use_image=use_image,
        confidence=max(0.0, min(confidence, 1.0)),
        reason=reason[:200],
    )


def _fallback_route(
    message: str,
    *,
    has_uploaded_image: bool,
    has_reference_image: bool,
    has_generated_image: bool,
    has_material_analysis: bool,
) -> ChatIntent:
    """LLM Router 不可用时的安全降级，覆盖明确场景。"""
    text = message.strip()

    if IMAGE_ANALYSIS_PATTERN.search(text):
        image_source = "uploaded" if has_uploaded_image else "generated" if has_generated_image else "reference" if has_reference_image else "none"
        return ChatIntent("analyze_image", image_source, 0.8, "rule fallback: explicit image analysis")

    if needs_effect_image(text):
        is_edit_request = is_effect_image_edit_request(text)
        if has_uploaded_image:
            image_source = "uploaded"
        elif is_edit_request and has_generated_image:
            image_source = "generated"
        elif has_reference_image:
            image_source = "reference"
        elif has_generated_image:
            image_source = "generated"
        else:
            image_source = "none"
        reason = (
            "rule fallback: edit generated image"
            if is_edit_request and image_source == "generated"
            else "rule fallback: render intent"
        )
        return ChatIntent("generate_effect_image", image_source, 0.75 if is_edit_request else 0.65, reason)

    if references_generated_image(text):
        image_source = "uploaded" if has_uploaded_image else "generated" if has_generated_image else "none"
        return ChatIntent("analyze_image", image_source, 0.65, "rule fallback: image reference")

    if PRICE_PATTERN.search(text):
        return ChatIntent("estimate_price", "none", 0.6, "rule fallback: price intent")

    if MATERIAL_QUERY_PATTERN.search(text) or has_material_analysis:
        return ChatIntent("query_material", "none", 0.55, "rule fallback: material intent")

    if has_uploaded_image:
        return ChatIntent("analyze_image", "uploaded", 0.55, "rule fallback: uploaded image")

    return ChatIntent("general_chat", "none", 0.5, "rule fallback: default")


def route_chat_intent(
    message: str,
    *,
    has_uploaded_image: bool,
    has_reference_image: bool,
    has_generated_image: bool,
    has_material_analysis: bool,
    text_chat_model: str,
) -> ChatIntent:
    """根据用户输入和应用层会话状态选择本轮处理流程。"""
    fallback = _fallback_route(
        message,
        has_uploaded_image=has_uploaded_image,
        has_reference_image=has_reference_image,
        has_generated_image=has_generated_image,
        has_material_analysis=has_material_analysis,
    )
    # 生成/分析类意图对"用哪张图"非常敏感，直接信任规则判断，不调用模型
    if fallback.intent in {"generate_effect_image", "analyze_image"}:
        result = fallback
        source = "rule_priority"
        latency_ms = 0.0
    else:
        # 其余意图交给模型判断；source 先记为 rule_fallback，模型成功后会覆盖
        source = "rule_fallback"
        latency_ms = None

    system_prompt = (
        "你是聊天请求路由器，只判断用户这一轮请求应该走哪个处理流程。"
        "只返回 JSON，不要解释，不要使用 Markdown。\n"
        "intent 只能是：generate_effect_image, analyze_image, estimate_price, query_material, general_chat。\n"
        "use_image 只能是：uploaded, reference, generated, none。\n"
        "规则：\n"
        "1. 用户要生成、再来一版、换风格、改成某种装修风格、按图改造，或在已有效果图上增加、添加、去掉、调整、替换画面元素/材料/局部设计，intent=generate_effect_image。\n"
        "2. 用户问图片/效果图里有什么石头、材料、地面、墙面、视觉内容，intent=analyze_image。\n"
        "3. 用户问面积、价格、预算、多少钱、费用估算，intent=analyze_image。\n"
        "4. 用户问某种石材的价格、规格、颜色、描述、数据库信息，intent=query_material。\n"
        "5. 普通闲聊或无法归类，intent=general_chat。\n"
        "图片选择：本轮上传图优先 use_image=uploaded；生成全新效果图且无上传图但有参考图时 use_image=reference；"
        "追改上一张效果图时 use_image=generated，例如“增加瓦片围边”；分析上一张效果图时 use_image=generated；文本查询 use_image=none。"
    )
    user_payload = {
        "message": message,
        "state": {
            "has_uploaded_image": has_uploaded_image,
            "has_reference_image": has_reference_image,
            "has_generated_image": has_generated_image,
            "has_material_analysis": has_material_analysis,
        },
        "required_output": {
            "intent": "generate_effect_image | analyze_image | estimate_price | query_material | general_chat",
            "use_image": "uploaded | reference | generated | none",
            "confidence": "0-1",
            "reason": "brief internal reason",
        },
    }

    # 只有非规则直返路径才真正调用模型，并记录模型决策耗时
    if source != "rule_priority":
        started = time.perf_counter()
        try:
            model = _get_router_model(text_chat_model)
            response = model.invoke(
                [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=json.dumps(user_payload, ensure_ascii=False)),
                ]
            )
            content = response.content if isinstance(response.content, str) else json.dumps(response.content, ensure_ascii=False)
            parsed = _normalize_intent(_extract_json(content), fallback)
            result = parsed
            # 模型返回了结果但解析/校验失败时，回退规则结果并标记来源
            source = "llm" if parsed is not fallback else "llm_normalize_fallback"
        except Exception:
            result = fallback
        latency_ms = round((time.perf_counter() - started) * 1000, 1)

    # 把本次决策写入采集日志（消息、状态、决策、来源、耗时），供后续评测使用
    _log_router_decision(
        {
            "timestamp": datetime.now().isoformat(timespec="milliseconds"),
            "message": message,
            "state": {
                "has_uploaded_image": has_uploaded_image,
                "has_reference_image": has_reference_image,
                "has_generated_image": has_generated_image,
                "has_material_analysis": has_material_analysis,
            },
            "model": text_chat_model,
            "intent": result.intent,
            "use_image": result.use_image,
            "confidence": result.confidence,
            "reason": result.reason,
            "source": source,
            "latency_ms": latency_ms,
        }
    )
    return result
