from collections.abc import Iterator
from collections import deque
from functools import lru_cache
from operator import add
import os
from pathlib import Path
from typing import Annotated, TypedDict

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain_community.agent_toolkits import SQLDatabaseToolkit
from langchain_community.utilities import SQLDatabase
from langchain_deepseek import ChatDeepSeek
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from langgraph.config import get_stream_writer
from langgraph.graph import END, StateGraph

from app.database import engine
from app.garden_styles import GardenStyle, build_style_generation_request
from app.service.chat_intent_router import route_chat_intent
from app.service.image_generation_service import (
    ImageGenerationError,
    build_design_generation_prompt,
    generated_image_as_data_url,
    generate_effect_image,
)
from app.service.design_session_service import (
    get_design_generation_context,
    material_scheme_summary,
)
from app.service.session_context_service import (
    get_session_context,
    remember_generated_image,
    remember_reference_image,
)
from app.service.conversation_service import (
    load_recent_model_history,
    protected_generated_url,
)
from app.service.sql_tool_guard import guard_sql_tools

load_dotenv()

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "chat_agent.md"
TEXT_CHAT_MODEL = os.getenv("TEXT_CHAT_MODEL", "deepseek-chat")
IMAGE_CHAT_MODEL = os.getenv("IMAGE_CHAT_MODEL", "qwen-vl-max-latest")
DEFAULT_SESSION_ID = "default"
VISION_THREAD_SUFFIX = "vision"
TEXT_THREAD_SUFFIX = "text"
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
    # 包一层工具防护：拦截 img 列、SELECT * 和写操作，防止图片数据进入上下文
    tools = guard_sql_tools(toolkit.get_tools())

    return create_agent(
        model,
        tools=tools,
        system_prompt=load_chat_agent_prompt(),
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
    """Compatibility hook retained after moving model memory to MySQL."""
    return None


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
    history: list[dict]
    # 各节点追加输出的流式片段：挂 add reducer，保证多节点接力时追加而不是覆盖
    response_chunks: Annotated[list[str], add]


def _stream_model_agent(
        model: str,
        message: str,
        image: str | None,
        session_id: str,
        history: list[dict] | None = None,
) -> Iterator[str]:
    """调用具体模型 Agent，并注入最近 10 轮持久化成功历史。"""
    agent = get_chat_agent(model)
    messages = list(history or [])
    messages.append({"role": "user", "content": _build_user_content(message, image)})

    # stream_mode="messages" 提供 token 级流式，但工具调用前的"思考"文本（如"我先查询一下"）
    # 也混在流里。用滑动窗口缓冲：窗口内出现工具调用就把缓冲整段丢弃（那是思考），
    # 否则窗口填满后边收边发。窗口大小是取舍：越大越能吞掉思考，越小流式越即时。
    HELD_WINDOW = 12
    held: deque[str] = deque()
    for chunk, _metadata in agent.stream(
        {"messages": messages},
        stream_mode="messages",
    ):
        if isinstance(chunk, AIMessageChunk):
            if chunk.tool_call_chunks:
                # 该轮在构造工具调用：窗口内缓冲的文本是思考过程，整段丢弃
                held.clear()
                continue
            text = _extract_text_content(chunk.content)
            if text:
                held.append(text)
                if len(held) > HELD_WINDOW:
                    yield held.popleft()
        elif isinstance(chunk, AIMessage):
            # 兼容：个别实现可能一次性给出完整消息
            text = _extract_text_content(chunk.content)
            if text and not chunk.tool_calls:
                held.append(text)
        elif isinstance(chunk, ToolMessage):
            # 工具结果到来：窗口内缓冲的是思考文本，丢弃
            held.clear()
    while held:
        yield held.popleft()


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
    history = load_recent_model_history(current_session_id, limit_turns=10)
    return {
        "message": message,
        "image": image,
        "session_id": current_session_id,
        "selected_style": selected_style,
        "force_generate_effect_image": force_generate_effect_image,
        "generated_context": generated_context,
        "intent": None,
        "history": history,
        "response_chunks": [],
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
    )
    return state


def _stream_into_node(stream: Iterator[str]) -> dict:
    """把现有的生成器 Agent 包装成图节点：

    逐块通过 get_stream_writer 实时发出（stream_mode="custom" 消费），
    同时把块追加进 response_chunks，供图结束时拼出完整回复。
    """
    writer = get_stream_writer()
    chunks: list[str] = []
    for chunk in stream:
        writer({"type": "chunk", "content": chunk})
        chunks.append(chunk)
    return {"response_chunks": chunks}


def _effect_image_node(state: ChatAgentState) -> dict:
    """效果图生成节点：复用原生成逻辑，只做流式包装。"""
    return _stream_into_node(_effect_image_agent(state))


def _vision_analysis_node(state: ChatAgentState) -> dict:
    """图片分析节点：复用原分析逻辑，只做流式包装。"""
    return _stream_into_node(_vision_analysis_agent(state))


def _text_node(state: ChatAgentState) -> dict:
    """文本/查库/估价节点：复用原逻辑，只做流式包装。"""
    return _stream_into_node(_text_agent(state))


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
        try:
            design = get_design_generation_context(current_session_id)
        except Exception:
            # Keep the pre-design-session chat flow usable while an old database is migrating.
            design = None

        materials = design.materials if design else ()
        additional_images: list[str] = []
        editing_previous_effect = False

        if (
            not state["force_generate_effect_image"]
            and intent
            and intent.use_image == "generated"
            and generated_context
            and generated_context.generated_image_url
            and (
                design.effect_is_current
                if design and design.generated_image_url
                else True
            )
        ):
            image = generated_image_as_data_url(generated_context.generated_image_url)
            additional_images = [material.image for material in materials]
            editing_previous_effect = True
        else:
            if not image:
                if design and design.space_image:
                    image = design.space_image
                elif generated_context and generated_context.reference_image_data_url:
                    image = generated_context.reference_image_data_url
            material_images = [material.image for material in materials]
            if image and material_images and image == material_images[0]:
                additional_images = material_images[1:]
            else:
                additional_images = material_images
            if not image and material_images:
                image = material_images[0]
                additional_images = material_images[1:]

        generation_request = build_design_generation_prompt(
            generation_request,
            has_space_image=bool(design and design.space_image and not editing_previous_effect),
            materials=materials,
            editing_previous_effect=editing_previous_effect,
        )
        image_url = generate_effect_image(
            generation_request,
            image,
            additional_images=additional_images,
        )
        _clear_session_checkpoints(current_session_id)
        remember_generated_image(current_session_id, image_url, generation_request)
        summary = material_scheme_summary(materials)
        display_image_url = protected_generated_url(current_session_id, image_url)
        yield (
            f"{success_prefix}：\n\n![{image_alt}]({display_image_url})"
            f"\n\n**本次石材方案**\n{summary}"
        )
    except ImageGenerationError as exc:
        yield f"效果图生成失败：{exc}"


def _vision_analysis_agent(state: ChatAgentState) -> Iterator[str]:
    """图片分析 Agent：只负责选择要看的图片，并流式返回视觉分析结果。"""
    message = state["message"]
    image = state["image"]
    current_session_id = state["session_id"]
    generated_context = state["generated_context"]
    intent = state["intent"]

    if not image and intent and intent.use_image == "reference" and generated_context and generated_context.reference_image_data_url:
        image = generated_context.reference_image_data_url
        message = (
            f"用户正在追问上次上传的原始参考图。原始上传需求：{generated_context.reference_image_request or '未提供'}。\n"
            f"当前问题：{message}"
        )
    elif not image and generated_context and generated_context.generated_image_url:
        try:
            image = generated_image_as_data_url(generated_context.generated_image_url)
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
    for text in _stream_model_agent(
        IMAGE_CHAT_MODEL, message, image, current_session_id, state["history"]
    ):
        final_response_parts.append(text)
        yield text


def _text_agent(state: ChatAgentState) -> Iterator[str]:
    """文本/查库/估价 Agent：只接收文本上下文，不接收图片消息。"""
    message = state["message"]

    yield from _stream_model_agent(
        TEXT_CHAT_MODEL, message, None, state["session_id"], state["history"]
    )


def _stream_multi_agent_chat(
        message: str,
        image: str | None = None,
        session_id: str | None = None,
        *,
        selected_style: GardenStyle | None = None,
        force_generate_effect_image: bool = False,
) -> Iterator[str]:
    """主控 Agent：整张图在 LangGraph 内执行，节点通过 custom 流实时输出。"""
    state = _build_agent_state(
        message,
        image,
        session_id,
        selected_style,
        force_generate_effect_image,
    )
    for event in get_chat_orchestrator_graph().stream(state, stream_mode="custom"):
        # 各节点通过 get_stream_writer 发出 {"type": "chunk", "content": ...}
        if event.get("type") == "chunk":
            yield event["content"]


def _route_after_router(state: ChatAgentState) -> str:
    """条件边：根据意图把流程分发给对应的专业 Agent 节点。"""
    if state["force_generate_effect_image"] or state["intent"].intent == "generate_effect_image":
        return "effect_image_agent"
    if state["intent"].intent == "analyze_image":
        return "vision_analysis_agent"
    # query_material / estimate_price / general_chat 都交给文本 Agent
    return "text_agent"


@lru_cache(maxsize=1)
def get_chat_orchestrator_graph():
    """LangGraph 主控图：Router 分发 + 三个专业 Agent 节点 + 条件边。"""
    graph = StateGraph(ChatAgentState)
    graph.add_node("router_agent", _router_agent)
    graph.add_node("effect_image_agent", _effect_image_node)
    graph.add_node("vision_analysis_agent", _vision_analysis_node)
    graph.add_node("text_agent", _text_node)
    graph.set_entry_point("router_agent")
    # router_agent给state赋值，_route_after_router做转发
    graph.add_conditional_edges(
        "router_agent",
        _route_after_router,
        {
            "effect_image_agent": "effect_image_agent",
            "vision_analysis_agent": "vision_analysis_agent",
            "text_agent": "text_agent",
        },
    )
    graph.add_edge("effect_image_agent", END)
    graph.add_edge("vision_analysis_agent", END)
    graph.add_edge("text_agent", END)
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
