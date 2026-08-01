from dataclasses import dataclass

from app.database import SessionLocal
from app.models.chat_session_context import ChatSessionContext
from app.service.design_session_service import mark_effect_generated, save_space_image


@dataclass(frozen=True)
class SessionContext:
    """供 Agent 使用的只读会话快照。"""

    session_id: str
    reference_image_data_url: str | None
    reference_image_request: str | None
    generated_image_url: str | None
    generation_request: str | None
    material_analysis: dict | None
    selected_style_id: str | None = None
    context_revision: int = 0
    effect_revision: int | None = None
    effect_is_current: bool = False


def _snapshot(row: ChatSessionContext) -> SessionContext:
    return SessionContext(
        session_id=row.session_id,
        reference_image_data_url=row.reference_image_data_url,
        reference_image_request=row.reference_image_request,
        generated_image_url=row.generated_image_url,
        generation_request=row.generation_request,
        material_analysis=row.material_analysis,
        selected_style_id=row.selected_style_id,
        context_revision=row.context_revision or 0,
        effect_revision=row.effect_revision,
        effect_is_current=bool(
            row.generated_image_url and row.effect_revision == (row.context_revision or 0)
        ),
    )


def get_session_context(session_id: str) -> SessionContext | None:
    """按前端提供的基础 session_id 读取跨模型共享记忆。"""
    with SessionLocal() as db:
        row = db.get(ChatSessionContext, session_id)
        return _snapshot(row) if row else None


def remember_reference_image(session_id: str, image: str, request: str) -> None:
    """保存用户最近一次上传的原始参考图，供后续效果图生成复用。"""
    if image.strip():
        save_space_image(session_id, image, request=request)


def remember_generated_image(session_id: str, image_url: str, request: str) -> None:
    """新效果图会替换旧效果图，并清空旧图的材料分析。"""
    mark_effect_generated(session_id, image_url, request)


def remember_material_analysis(
    session_id: str,
    analysis: str,
    source_model: str,
) -> None:
    """将视觉模型最终结论结构化后写入同一应用会话。"""
    cleaned_analysis = analysis.strip()
    if not cleaned_analysis:
        return

    with SessionLocal.begin() as db:
        row = db.get(ChatSessionContext, session_id)
        if row is None:
            return
        row.material_analysis = {
            "kind": "effect_image_material_analysis",
            "source_model": source_model,
            "analysis_text": cleaned_analysis[:8000],
        }
