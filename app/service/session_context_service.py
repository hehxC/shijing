from dataclasses import dataclass

from app.database import SessionLocal
from app.models.chat_session_context import ChatSessionContext


@dataclass(frozen=True)
class SessionContext:
    """供 Agent 使用的只读会话快照。"""

    session_id: str
    reference_image_data_url: str | None
    reference_image_request: str | None
    generated_image_url: str | None
    generation_request: str | None
    material_analysis: dict | None


def _snapshot(row: ChatSessionContext) -> SessionContext:
    return SessionContext(
        session_id=row.session_id,
        reference_image_data_url=row.reference_image_data_url,
        reference_image_request=row.reference_image_request,
        generated_image_url=row.generated_image_url,
        generation_request=row.generation_request,
        material_analysis=row.material_analysis,
    )


def get_session_context(session_id: str) -> SessionContext | None:
    """按前端提供的基础 session_id 读取跨模型共享记忆。"""
    with SessionLocal() as db:
        row = db.get(ChatSessionContext, session_id)
        return _snapshot(row) if row else None


def remember_reference_image(session_id: str, image: str, request: str) -> None:
    """保存用户最近一次上传的原始参考图，供后续效果图生成复用。"""
    cleaned_image = image.strip()
    if not cleaned_image:
        return

    with SessionLocal.begin() as db:
        row = db.get(ChatSessionContext, session_id)
        if row is None:
            row = ChatSessionContext(session_id=session_id)
            db.add(row)
        row.reference_image_data_url = cleaned_image
        row.reference_image_request = request.strip()


def remember_generated_image(session_id: str, image_url: str, request: str) -> None:
    """新效果图会替换旧效果图，并清空旧图的材料分析。"""
    with SessionLocal.begin() as db:
        row = db.get(ChatSessionContext, session_id)
        if row is None:
            row = ChatSessionContext(session_id=session_id)
            db.add(row)
        row.generated_image_url = image_url
        row.generation_request = request.strip()
        row.material_analysis = None


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
