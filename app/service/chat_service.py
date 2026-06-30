from collections.abc import Iterator
from functools import lru_cache
import json
import os
from pathlib import Path
from typing import TypedDict

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain_community.agent_toolkits import SQLDatabaseToolkit
from langchain_community.utilities import SQLDatabase
from langchain_deepseek import ChatDeepSeek
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import AIMessage, AIMessageChunk
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph

from app.database import engine
from app.garden_styles import GardenStyle, build_style_generation_request
from app.service.chat_intent_router import route_chat_intent
from app.service.image_generation_service import (
    ImageGenerationError,
    generated_image_as_data_url,
    generate_effect_image,
)
from app.service.session_context_service import (
    get_session_context,
    remember_generated_image,
    remember_material_analysis,
    remember_reference_image,
)

load_dotenv()

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "chat_agent.md"
TEXT_CHAT_MODEL = os.getenv("TEXT_CHAT_MODEL", "deepseek-chat")
IMAGE_CHAT_MODEL = os.getenv("IMAGE_CHAT_MODEL", "qwen-vl-max-latest")
DEFAULT_SESSION_ID = "default"
VISION_THREAD_SUFFIX = "vision"
TEXT_THREAD_SUFFIX = "text"
CHECKPOINTER = InMemorySaver()
SQL_DB = SQLDatabase(
    engine,
    include_tables=["materials"],
    sample_rows_in_table_info=0,
)


# 加载systemPrompt
@lru_cache(maxsize=1)
def load_chat_agent_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8").strip()


@lru_cache(maxsize=2)
def get_chat_agent(model_name: str):
    if model_name.startswith("gemini"):
        model = ChatGoogleGenerativeAI(
            model=model_name,
            api_key=os.getenv("GEMINI_KEY") or os.getenv("GOOGLE_API_KEY"),
        )
    elif model_name == "qwen3.7-max-2026-06-08":
        base_url = os.getenv("DASHSCOPE_BASE_URL")
        api_key = os.getenv("DASHSCOPE_API_KEY")
        model = init_chat_model(
            model=model_name,
            model_provider="openai",
            base_url=base_url,
            api_key=api_key
        )
    else:
        model = ChatDeepSeek(
            model=model_name,
            api_key=os.getenv("DEEPSEEK_API_KEY"),
        )

    # 把查询数据库作为工具
    toolkit = SQLDatabaseToolkit(
        db=SQL_DB,
        llm=model
    )
    tools = toolkit.get_tools()

    return create_agent(
        model,
        tools=tools,
        system_prompt=load_chat_agent_prompt(),
        checkpointer=CHECKPOINTER,
    )


def _build_user_content(message: str, image: str | None) -> str | list[dict]:
    text = message.strip() or "请分析这张图片。"
    if not image:
        return text

    return [
        {"type": "text", "text": text},
        {"type": "image_url", "image_url": {"url": image}},
    ]


def _extract_text_content(content) -> str:
    if isinstance(content, str):
        return content

    if not isinstance(content, list):
        return ""

    chunks: list[str] = []
    for block in content:
        if isinstance(block, str):
            chunks.append(block)
            continue
        if not isinstance(block, dict):
            continue

        block_type = block.get("type")
        if block_type in {"text", "output_text"} and isinstance(block.get("text"), str):
            chunks.append(block["text"])
        elif block_type not in {"tool_call", "tool_result", "image", "image_url"} and isinstance(block.get("content"),
                                                                                                 str):
            chunks.append(block["content"])

    return "".join(chunks)


def _checkpoint_thread_id(session_id: str, model_name: str) -> str:
    """按模型能力隔离 LangGraph 历史，防止图片消息进入纯文本模型。"""
    suffix = VISION_THREAD_SUFFIX if model_name == IMAGE_CHAT_MODEL else TEXT_THREAD_SUFFIX
    return f"{session_id}:{suffix}"


def _clear_session_checkpoints(session_id: str) -> None:
    """生成新效果图后清理旧方案的模型短期历史。
    应用层的 MySQL 会话上下文仍然保留并随后更新；这里只清理可能包含
    旧图片或旧估价的 LangGraph checkpoint。
    """
    for suffix in (VISION_THREAD_SUFFIX, TEXT_THREAD_SUFFIX):
        CHECKPOINTER.delete_thread(f"{session_id}:{suffix}")


class ChatAgentState(TypedDict):
    """应用层多 Agent 编排状态。

    这里保存的是本轮请求需要在各专业 Agent 之间传递的业务状态，不依赖某个模型自己的历史。
    """

    message: str
    image: str | None
    session_id: str
    selected_style: GardenStyle | None
    force_generate_effect_image: bool
    generated_context: object | None
    intent: object | None
    analyzing_generated_image: bool


def _stream_model_agent(
        model: str,
        message: str,
        image: str | None,
        session_id: str,
) -> Iterator[str]:
    """调用具体模型 Agent，并保持原来的流式输出行为。"""
    agent = get_chat_agent(model)
    config = {
        "configurable": {
            "thread_id": _checkpoint_thread_id(session_id, model),
        }
    }

    for update in agent.stream(
            {"messages": [{"role": "user", "content": _build_user_content(message, image)}]},
            config=config,
            stream_mode="updates",
    ):
        # 工具调用前的模型草稿可能包含 SQL，只发送没有工具调用的最终模型回复。
        for msg in update.get("model", {}).get("messages", []):
            if not isinstance(msg, (AIMessage, AIMessageChunk)) or msg.tool_calls:
                continue
            text = _extract_text_content(msg.content)
            if text:
                yield text


def _build_agent_state(
        message: str,
        image: str | None,
        session_id: str | None,
        selected_style: GardenStyle | None,
        force_generate_effect_image: bool,
) -> ChatAgentState:
    current_session_id = session_id or DEFAULT_SESSION_ID

    # 原始上传图属于应用层记忆，供后续“换成中式 / 再来一版”等生成任务复用。
    if image:
        remember_reference_image(current_session_id, image, message)

    generated_context = get_session_context(current_session_id)
    return {
        "message": message,
        "image": image,
        "session_id": current_session_id,
        "selected_style": selected_style,
        "force_generate_effect_image": force_generate_effect_image,
        "generated_context": generated_context,
        "intent": None,
        "analyzing_generated_image": False,
    }


def _router_agent(state: ChatAgentState) -> ChatAgentState:
    """主控 Router Agent：只负责选择本轮交给哪个专业 Agent。"""
    if state["force_generate_effect_image"]:
        return state

    generated_context = state["generated_context"]
    state["intent"] = route_chat_intent(
        state["message"],
        has_uploaded_image=bool(state["image"]),
        has_reference_image=bool(generated_context and generated_context.reference_image_data_url),
        has_generated_image=bool(generated_context and generated_context.generated_image_url),
        has_material_analysis=bool(generated_context and generated_context.material_analysis),
        text_chat_model=TEXT_CHAT_MODEL,
    )
    return state


def _effect_image_agent(state: ChatAgentState) -> Iterator[str]:
    """效果图生成 Agent：只负责组织生成提示词、复用参考图、调用文生图模型。"""
    message = state["message"]
    image = state["image"]
    current_session_id = state["session_id"]
    selected_style = state["selected_style"]
    generated_context = state["generated_context"]
    intent = state["intent"]

    if state["force_generate_effect_image"]:
        if selected_style is None:
            yield "请选择有效的庭院风格后再生成效果图。"
            return
        generation_request = build_style_generation_request(selected_style, message)
        success_prefix = f"已生成「{selected_style.name}」效果图"
        image_alt = f"{selected_style.name}庭院效果图"
    else:
        generation_request = message
        success_prefix = "效果图已生成"
        image_alt = "AI 生成的装修效果图"

    try:
        if not image and generated_context:
            if (
                not state["force_generate_effect_image"]
                and intent
                and intent.use_image == "generated"
                and generated_context.generated_image_url
            ):
                image = generated_image_as_data_url(generated_context.generated_image_url)
                generation_request = (
                    "基于上一轮已生成的效果图进行局部修改，保持原有构图、透视、主体风格、"
                    f"已有材料和植物不变，只按用户新要求调整：{generation_request}"
                )
            elif generated_context.reference_image_data_url:
                image = generated_context.reference_image_data_url
                if not state["force_generate_effect_image"]:
                    generation_request = f"基于用户上次上传的原始参考图，{generation_request}"

        image_url = generate_effect_image(generation_request, image)
        _clear_session_checkpoints(current_session_id)
        remember_generated_image(current_session_id, image_url, generation_request)
        yield f"{success_prefix}：\n\n![{image_alt}]({image_url})"
    except ImageGenerationError as exc:
        yield f"效果图生成失败：{exc}"


def _vision_analysis_agent(state: ChatAgentState) -> Iterator[str]:
    """图片分析 Agent：只负责选择要看的图片，并把视觉分析结果写入应用层上下文。"""
    message = state["message"]
    image = state["image"]
    current_session_id = state["session_id"]
    generated_context = state["generated_context"]
    intent = state["intent"]
    analyzing_generated_image = False

    if not image and intent and intent.use_image == "reference" and generated_context and generated_context.reference_image_data_url:
        image = generated_context.reference_image_data_url
        message = (
            f"用户正在追问上次上传的原始参考图。原始上传需求：{generated_context.reference_image_request or '未提供'}。\n"
            f"当前问题：{message}"
        )
    elif not image and generated_context and generated_context.generated_image_url:
        try:
            image = generated_image_as_data_url(generated_context.generated_image_url)
            analyzing_generated_image = True
            message = (
                f"用户正在追问上一轮生成的效果图。原始生成需求：{generated_context.generation_request or '未提供'}。\n"
                f"当前问题：{message}"
            )
        except ImageGenerationError as exc:
            yield f"暂时无法读取上一张效果图：{exc}。请重新生成或上传图片后再问。"
            return
    elif not image:
        yield "当前会话中没有找到可分析的图片，请重新生成或上传图片后再问。"
        return

    final_response_parts: list[str] = []
    for text in _stream_model_agent(IMAGE_CHAT_MODEL, message, image, current_session_id):
        final_response_parts.append(text)
        yield text

    if analyzing_generated_image and final_response_parts:
        remember_material_analysis(
            current_session_id,
            "".join(final_response_parts),
            IMAGE_CHAT_MODEL,
        )


def _text_agent(state: ChatAgentState) -> Iterator[str]:
    """文本/查库/估价 Agent：只接收文本上下文，不接收图片消息。"""
    message = state["message"]
    generated_context = state["generated_context"]

    if generated_context and generated_context.material_analysis:
        shared_context = {
            "effect_image_request": generated_context.generation_request,
            "material_analysis": generated_context.material_analysis,
        }
        message = (
            "以下 JSON 是同一会话中上一张效果图的应用层共享上下文。"
            "仅在与当前问题相关时使用；涉及材料价格时，必须根据其中的材料名称调用 SQL 工具查询，"
            "不要编造单价。不要向用户提及上下文注入或模型切换。\n"
            f"{json.dumps(shared_context, ensure_ascii=False)}\n"
            f"用户当前问题：{message}"
        )

    yield from _stream_model_agent(TEXT_CHAT_MODEL, message, None, state["session_id"])


def _stream_multi_agent_chat(
        message: str,
        image: str | None = None,
        session_id: str | None = None,
        *,
        selected_style: GardenStyle | None = None,
        force_generate_effect_image: bool = False,
) -> Iterator[str]:
    """主控 Agent：Router 负责分发，专业 Agent 负责各自能力。"""
    state = _build_agent_state(
        message,
        image,
        session_id,
        selected_style,
        force_generate_effect_image,
    )
    state = get_chat_orchestrator_graph().invoke(state)

    if force_generate_effect_image or state["intent"].intent == "generate_effect_image":
        yield from _effect_image_agent(state)
        return

    if state["intent"].intent == "analyze_image":
        yield from _vision_analysis_agent(state)
        return

    # query_material / estimate_price / general_chat 都交给文本 Agent；
    # SQL 工具仍挂在文本模型 Agent 上，图片内容只通过应用层 JSON 显式注入。
    yield from _text_agent(state)


@lru_cache(maxsize=1)
def get_chat_orchestrator_graph():
    """LangGraph 主控图。

    当前只把 Router Agent 放入图中，专业 Agent 继续使用生成器流式输出。
    这样既能形成清晰的多 Agent 编排入口，也不会破坏前端现有的流式响应。
    """
    graph = StateGraph(ChatAgentState)
    graph.add_node("router_agent", _router_agent)
    graph.set_entry_point("router_agent")
    graph.add_edge("router_agent", END)
    return graph.compile()


def stream_chat(
        message: str,
        image: str | None = None,
        session_id: str | None = None,
        *,
        selected_style: GardenStyle | None = None,
        force_generate_effect_image: bool = False,
) -> Iterator[str]:
    """当前聊天入口：使用轻量多 Agent 编排。

    旧的单函数 if/else 实现保留在 stream_chat_legacy()，不再由接口调用，方便回滚对比。
    """
    yield from _stream_multi_agent_chat(
        message,
        image,
        session_id,
        selected_style=selected_style,
        force_generate_effect_image=force_generate_effect_image,
    )


def stream_chat_legacy(
        message: str,
        image: str | None = None,
        session_id: str | None = None,
        *,
        selected_style: GardenStyle | None = None,
        force_generate_effect_image: bool = False,
) -> Iterator[str]:
    current_session_id = session_id or DEFAULT_SESSION_ID
    analyzing_generated_image = False

    # 记住用户最近一次主动上传的原始图片。
    # 这张图作为后续“再生成一个中式/现代/欧式效果图”的参考图使用，
    # 不等同于 AI 生成后的效果图。
    if image:
        remember_reference_image(current_session_id, image, message)

    generated_context = get_session_context(current_session_id)

    # 风格选择由前端显式触发，直接进入文生图流程，不依赖意图路由判断。
    if force_generate_effect_image:
        if selected_style is None:
            yield "请选择有效的庭院风格后再生成效果图。"
            return

        generation_request = build_style_generation_request(selected_style, message)
        try:
            if not image and generated_context and generated_context.reference_image_data_url:
                image = generated_context.reference_image_data_url
            image_url = generate_effect_image(generation_request, image)
            _clear_session_checkpoints(current_session_id)
            remember_generated_image(current_session_id, image_url, generation_request)
            yield f"已生成「{selected_style.name}」效果图：\n\n![{selected_style.name}庭院效果图]({image_url})"
        except ImageGenerationError as exc:
            yield f"效果图生成失败：{exc}"
        return

    intent = route_chat_intent(
        message,
        has_uploaded_image=bool(image),
        has_reference_image=bool(generated_context and generated_context.reference_image_data_url),
        has_generated_image=bool(generated_context and generated_context.generated_image_url),
        has_material_analysis=bool(generated_context and generated_context.material_analysis),
        text_chat_model=TEXT_CHAT_MODEL,
    )

    # 效果图生成意图优先级最高：无论是否有参考图，都直接调用图片生成模型。
    if intent.intent == "generate_effect_image":
        try:
            if not image and generated_context and generated_context.reference_image_data_url:
                image = generated_context.reference_image_data_url
                message = f"基于用户上次上传的原始参考图，{message}"
            image_url = generate_effect_image(message, image)
            _clear_session_checkpoints(current_session_id)
            remember_generated_image(current_session_id, image_url, message)
            yield f"效果图已生成：\n\n![AI 生成的装修效果图]({image_url})"
        except ImageGenerationError as exc:
            yield f"效果图生成失败：{exc}"
        return

    # 图片分析意图：根据 Router 指定的图片来源恢复图片并交给视觉模型。
    if intent.intent == "analyze_image":
        if not image and intent.use_image == "reference" and generated_context and generated_context.reference_image_data_url:
            image = generated_context.reference_image_data_url
            message = (
                f"用户正在追问上次上传的原始参考图。原始上传需求：{generated_context.reference_image_request or '未提供'}。\n"
                f"当前问题：{message}"
            )
        elif not image and generated_context and generated_context.generated_image_url:
            try:
                image = generated_image_as_data_url(generated_context.generated_image_url)
                analyzing_generated_image = True
                message = (
                    f"用户正在追问上一轮生成的效果图。原始生成需求：{generated_context.generation_request or '未提供'}。\n"
                    f"当前问题：{message}"
                )
            except ImageGenerationError as exc:
                yield f"暂时无法读取上一张效果图：{exc}。请重新生成或上传图片后再问。"
                return
        elif not image:
            yield "当前会话中没有找到可分析的图片，请重新生成或上传图片后再问。"
            return
    else:
        # 文本类意图不要把本轮上传图透传给纯文本模型，避免误走视觉历史。
        image = None

    model = IMAGE_CHAT_MODEL if image else TEXT_CHAT_MODEL

    if model == TEXT_CHAT_MODEL:
        if generated_context and generated_context.material_analysis:
            # DeepSeek 与 Qwen 是两个独立 Agent，不能依赖各自的 checkpointer
            # 自动共享历史。这里显式传递同一 session_id 下的视觉分析结果。
            shared_context = {
                "effect_image_request": generated_context.generation_request,
                "qwen_material_analysis": generated_context.material_analysis,
            }
            message = (
                "以下 JSON 是同一会话中上一张效果图的已确认上下文。"
                "仅在与当前问题相关时使用；涉及材料价格时，必须根据其中的材料名称调用 SQL 工具查询，"
                "不要编造单价。不要向用户提及上下文注入或模型切换。\n"
                f"{json.dumps(shared_context, ensure_ascii=False)}\n"
                f"用户当前问题：{message}"
            )

    agent = get_chat_agent(model)

    # Qwen 历史包含 image_url，DeepSeek 只接受文本，因此必须使用不同 checkpoint。
    # 跨模型需要共享的材料结论通过上面的 JSON 上下文显式传递。
    config = {
        "configurable": {
            "thread_id": _checkpoint_thread_id(current_session_id, model),
        }
    }

    final_response_parts: list[str] = []
    for update in agent.stream(
            {"messages": [{"role": "user", "content": _build_user_content(message, image)}]},
            config=config,
            stream_mode="updates",
    ):
        # 工具调用前的模型草稿可能包含 SQL。只发送没有工具调用的最终模型回复。
        for msg in update.get("model", {}).get("messages", []):
            if not isinstance(msg, (AIMessage, AIMessageChunk)) or msg.tool_calls:
                continue
            text = _extract_text_content(msg.content)
            if text:
                final_response_parts.append(text)
                yield text

    # 视觉模型完成分析后，将最终回答保存为跨模型共享上下文。
    if analyzing_generated_image and final_response_parts:
        remember_material_analysis(
            current_session_id,
            "".join(final_response_parts),
            IMAGE_CHAT_MODEL,
        )
