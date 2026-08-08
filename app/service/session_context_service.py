from dataclasses import dataclass, replace

from sqlalchemy import select

from app.database import SessionLocal
from app.models.chat_session_context import ChatSessionContext
from app.models.design_reference_image import DesignReferenceImage
from app.service.design_session_service import (
    SPACE_IMAGE,
    mark_effect_generated,
    save_space_image,
)
from app.service.image_store import bytes_to_data_url, get_image_store


@dataclass(frozen=True)
class SessionContext:
    """供 Agent 使用的只读会话快照。"""

    session_id: str
    reference_image_data_url: str | None
    reference_image_request: str | None
    generated_image_url: str | None
    generation_request: str | None
    selected_style_id: str | None = None
    context_revision: int = 0
    effect_revision: int | None = None
    effect_is_current: bool = False


def _snapshot(row: ChatSessionContext) -> SessionContext:
    return SessionContext(
        session_id=row.session_id,
        # getattr 兼容旧库：迁移后 reference_image_data_url 列会被删除
        reference_image_data_url=getattr(row, "reference_image_data_url", None),
        reference_image_request=row.reference_image_request,
        generated_image_url=row.generated_image_url,
        generation_request=row.generation_request,
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
        if row is None:
            return None
        snapshot = _snapshot(row)
        # 参考图以 design_reference_images 为唯一事实来源：从对象存储解析 data URL
        reference_image = _resolve_reference_image(db, session_id)
        if reference_image is not None:
            snapshot = replace(snapshot, reference_image_data_url=reference_image)
        return snapshot


def _resolve_reference_image(db, session_id: str) -> str | None:
    """从 design_reference_images 读取空间图：优先对象存储，旧数据回退 data_url 列。"""
    space = db.scalar(
        select(DesignReferenceImage).where(
            DesignReferenceImage.session_id == session_id,
            DesignReferenceImage.kind == SPACE_IMAGE,
        )
    )
    if space is None:
        return None
    if space.object_key:
        try:
            return bytes_to_data_url(space.object_key, get_image_store().get(space.object_key))
        except (OSError, ValueError):
            pass
    return getattr(space, "data_url", None)


def remember_reference_image(session_id: str, image: str, request: str) -> None:
    """保存用户最近一次上传的原始参考图，供后续效果图生成复用。"""
    if image.strip():
        save_space_image(session_id, image, request=request)


def remember_generated_image(session_id: str, image_url: str, request: str) -> None:
    """新效果图会替换旧效果图，旧的生成记录随之作废。"""
    mark_effect_generated(session_id, image_url, request)
