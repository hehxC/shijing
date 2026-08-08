import base64
import binascii
import mimetypes
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, func, select

from app.database import SessionLocal
from app.models.chat_session_context import ChatSessionContext
from app.models.chat_conversation import ChatConversation
from app.models.design_reference_image import DesignReferenceImage
from app.service.image_store import (
    bytes_to_data_url,
    data_url_to_bytes,
    get_image_store,
)


SPACE_IMAGE = "space"
MATERIAL_IMAGE = "material"
MAX_MATERIAL_IMAGES = 20
MAX_MATERIAL_USAGES = 7
MAX_IMAGE_BYTES = 10 * 1024 * 1024
RETENTION_DAYS = 30
ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
ALLOWED_MATERIAL_USAGES = {
    "地面铺装",
    "墙面",
    "台阶",
    "围边",
    "景墙",
    "水景",
    "汀步",
    "水景观",
    "驳岸",
    "其他",
}
GENERATED_DIR = Path(__file__).resolve().parents[2] / "static" / "generated"
_DATA_URL_PATTERN = re.compile(
    r"^data:(image/(?:jpeg|png|webp));base64,([A-Za-z0-9+/=\r\n]+)$",
    re.IGNORECASE,
)


class DesignSessionError(ValueError):
    pass


@dataclass(frozen=True)
class MaterialReference:
    id: int
    image: str
    original_name: str | None
    name: str | None
    usages: tuple[str, ...]
    position: int


@dataclass(frozen=True)
class DesignGenerationContext:
    space_image: str | None
    materials: tuple[MaterialReference, ...]
    generated_image_url: str | None
    selected_style_id: str | None
    effect_is_current: bool
    context_revision: int


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def validate_image_data_url(image: str) -> str:
    cleaned = image.strip()
    match = _DATA_URL_PATTERN.fullmatch(cleaned)
    if not match or match.group(1).lower() not in ALLOWED_MIME_TYPES:
        raise DesignSessionError("仅支持 JPEG、PNG 或 WebP 图片")
    try:
        decoded = base64.b64decode(match.group(2), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise DesignSessionError("图片数据无效") from exc
    if not decoded:
        raise DesignSessionError("图片不能为空")
    if len(decoded) > MAX_IMAGE_BYTES:
        raise DesignSessionError("图片不能超过 10MB")
    return cleaned


def normalize_material_metadata(
    name: str | None, usages: list[str] | tuple[str, ...] | None
) -> tuple[str | None, list[str]]:
    cleaned_name = name.strip() if isinstance(name, str) else None
    cleaned_name = cleaned_name or None
    unique_usages: list[str] = []
    for usage in usages or []:
        value = usage.strip()
        if value not in ALLOWED_MATERIAL_USAGES:
            raise DesignSessionError(f"不支持的石材用途：{value}")
        if value not in unique_usages:
            unique_usages.append(value)
        if len(unique_usages) > MAX_MATERIAL_USAGES:
            raise DesignSessionError(f"每种石材最多选择 {MAX_MATERIAL_USAGES} 个用途")
    return cleaned_name, unique_usages


def _get_or_create_context(db, session_id: str) -> ChatSessionContext:
    row = db.get(ChatSessionContext, session_id)
    if row is None:
        row = ChatSessionContext(session_id=session_id, context_revision=0)
        db.add(row)
        db.flush()
    return row


def _mark_changed(row: ChatSessionContext) -> None:
    row.context_revision = (row.context_revision or 0) + 1
    row.updated_at = utcnow()
    row.assets_expired_at = None


def _delete_generated_file(image_url: str | None) -> None:
    prefix = "/static/generated/"
    if not image_url or not image_url.startswith(prefix):
        return
    filename = image_url.removeprefix(prefix)
    get_image_store().delete(f"generated/{filename}")


def _save_image_to_store(kind: str, data_url: str) -> str:
    """把 data URL 解码后写入图片存储，返回对象 key（按 类型/日期/uuid 组织）。"""
    data, mime_type = data_url_to_bytes(data_url)
    extension = mimetypes.guess_extension(mime_type) or ".jpg"
    if extension in {".jpe", ".jpeg"}:
        extension = ".jpg"
    key = f"{kind}/{datetime.now().strftime('%Y%m%d')}/{uuid4().hex}{extension}"
    return get_image_store().save(key, data, content_type=mime_type)


def _resolve_image_data_url(row: DesignReferenceImage) -> str:
    """优先从对象存储读文件还原 data URL；旧数据回退到 data_url 列。"""
    if row.object_key:
        try:
            return bytes_to_data_url(row.object_key, get_image_store().get(row.object_key))
        except (OSError, ValueError):
            pass
    legacy = getattr(row, "data_url", None)
    return legacy or ""


def _serialize_image(row: DesignReferenceImage) -> dict:
    return {
        "id": row.id,
        "image": _resolve_image_data_url(row),
        "original_name": row.original_name,
        "name": row.material_name,
        "usages": list(row.usages or []),
        "position": row.position,
    }


def get_design_state(session_id: str) -> dict:
    with SessionLocal() as db:
        context = db.get(ChatSessionContext, session_id)
        images = list(
            db.scalars(
                select(DesignReferenceImage)
                .where(DesignReferenceImage.session_id == session_id)
                .order_by(DesignReferenceImage.position, DesignReferenceImage.id)
            )
        )
        space = next((row for row in images if row.kind == SPACE_IMAGE), None)
        materials = [row for row in images if row.kind == MATERIAL_IMAGE]
        return {
            "space_image": _serialize_image(space) if space else None,
            "materials": [_serialize_image(row) for row in materials],
            "selected_style_id": context.selected_style_id if context else None,
            "generated_image_url": context.generated_image_url if context else None,
            "effect_is_current": bool(
                context
                and context.generated_image_url
                and context.effect_revision == context.context_revision
            ),
            "expired": bool(context and context.assets_expired_at),
        }


def get_design_generation_context(session_id: str) -> DesignGenerationContext:
    state = get_design_state(session_id)
    materials = tuple(
        MaterialReference(
            id=item["id"],
            image=item["image"],
            original_name=item["original_name"],
            name=item["name"],
            usages=tuple(item["usages"]),
            position=item["position"],
        )
        for item in state["materials"]
    )
    with SessionLocal() as db:
        context = db.get(ChatSessionContext, session_id)
        revision = context.context_revision or 0 if context else 0
    return DesignGenerationContext(
        space_image=state["space_image"]["image"] if state["space_image"] else None,
        materials=materials,
        generated_image_url=state["generated_image_url"],
        selected_style_id=state["selected_style_id"],
        effect_is_current=state["effect_is_current"],
        context_revision=revision,
    )


def save_space_image(
    session_id: str, image: str, original_name: str | None = None, request: str = ""
) -> dict:
    cleaned = validate_image_data_url(image)
    object_key = _save_image_to_store(SPACE_IMAGE, cleaned)
    with SessionLocal.begin() as db:
        context = _get_or_create_context(db, session_id)
        previous = db.scalar(
            select(DesignReferenceImage).where(
                DesignReferenceImage.session_id == session_id,
                DesignReferenceImage.kind == SPACE_IMAGE,
            )
        )
        if previous is None:
            previous = DesignReferenceImage(
                session_id=session_id,
                kind=SPACE_IMAGE,
                position=0,
                object_key=object_key,
            )
            db.add(previous)
        else:
            # 替换旧图：先删旧文件（尽力而为），再写新 key
            if previous.object_key:
                get_image_store().delete(previous.object_key)
            previous.object_key = object_key
        previous.original_name = original_name.strip() if original_name else None
        # 上下文表不再冗余存 base64，只保留请求文本；图片从 design_reference_images 解析
        context.reference_image_data_url = None
        context.reference_image_request = request.strip()
        _mark_changed(context)
        db.flush()
        return _serialize_image(previous)


def delete_space_image(session_id: str) -> None:
    object_key: str | None = None
    with SessionLocal.begin() as db:
        context = _get_or_create_context(db, session_id)
        row = db.scalar(
            select(DesignReferenceImage).where(
                DesignReferenceImage.session_id == session_id,
                DesignReferenceImage.kind == SPACE_IMAGE,
            )
        )
        object_key = row.object_key if row else None
        deleted = db.execute(
            delete(DesignReferenceImage).where(
                DesignReferenceImage.session_id == session_id,
                DesignReferenceImage.kind == SPACE_IMAGE,
            )
        ).rowcount
        if deleted or context.reference_image_data_url:
            context.reference_image_data_url = None
            context.reference_image_request = None
            _mark_changed(context)
    if object_key:
        get_image_store().delete(object_key)


def add_material_reference(
    session_id: str,
    image: str,
    original_name: str | None = None,
    name: str | None = None,
    usages: list[str] | None = None,
) -> dict:
    cleaned = validate_image_data_url(image)
    object_key = _save_image_to_store(MATERIAL_IMAGE, cleaned)
    material_name, material_usages = normalize_material_metadata(name, usages)
    with SessionLocal.begin() as db:
        context = _get_or_create_context(db, session_id)
        count = db.scalar(
            select(func.count(DesignReferenceImage.id)).where(
                DesignReferenceImage.session_id == session_id,
                DesignReferenceImage.kind == MATERIAL_IMAGE,
            )
        )
        if int(count or 0) >= MAX_MATERIAL_IMAGES:
            raise DesignSessionError(f"石材参考图最多上传 {MAX_MATERIAL_IMAGES} 张")
        max_position = db.scalar(
            select(func.max(DesignReferenceImage.position)).where(
                DesignReferenceImage.session_id == session_id,
                DesignReferenceImage.kind == MATERIAL_IMAGE,
            )
        )
        row = DesignReferenceImage(
            session_id=session_id,
            kind=MATERIAL_IMAGE,
            object_key=object_key,
            original_name=original_name.strip() if original_name else None,
            material_name=material_name,
            usages=material_usages,
            position=int(max_position or 0) + 1,
        )
        db.add(row)
        _mark_changed(context)
        db.flush()
        return _serialize_image(row)


def update_material_reference(
    session_id: str, image_id: int, name: str | None, usages: list[str] | None
) -> dict:
    material_name, material_usages = normalize_material_metadata(name, usages)
    with SessionLocal.begin() as db:
        row = db.scalar(
            select(DesignReferenceImage).where(
                DesignReferenceImage.id == image_id,
                DesignReferenceImage.session_id == session_id,
                DesignReferenceImage.kind == MATERIAL_IMAGE,
            )
        )
        if row is None:
            raise DesignSessionError("石材参考图不存在")
        changed = row.material_name != material_name or list(row.usages or []) != material_usages
        row.material_name = material_name
        row.usages = material_usages
        if changed:
            _mark_changed(_get_or_create_context(db, session_id))
        db.flush()
        return _serialize_image(row)


def delete_material_reference(session_id: str, image_id: int) -> None:
    object_key: str | None = None
    with SessionLocal.begin() as db:
        row = db.scalar(
            select(DesignReferenceImage).where(
                DesignReferenceImage.id == image_id,
                DesignReferenceImage.session_id == session_id,
                DesignReferenceImage.kind == MATERIAL_IMAGE,
            )
        )
        if row is None:
            raise DesignSessionError("石材参考图不存在")
        object_key = row.object_key
        db.delete(row)
        _mark_changed(_get_or_create_context(db, session_id))
    if object_key:
        get_image_store().delete(object_key)


def clear_material_references(session_id: str) -> None:
    object_keys: list[str] = []
    with SessionLocal.begin() as db:
        object_keys = list(
            db.scalars(
                select(DesignReferenceImage.object_key).where(
                    DesignReferenceImage.session_id == session_id,
                    DesignReferenceImage.kind == MATERIAL_IMAGE,
                )
            )
        )
        deleted = db.execute(
            delete(DesignReferenceImage).where(
                DesignReferenceImage.session_id == session_id,
                DesignReferenceImage.kind == MATERIAL_IMAGE,
            )
        ).rowcount
        if deleted:
            _mark_changed(_get_or_create_context(db, session_id))
    for object_key in object_keys:
        if object_key:
            get_image_store().delete(object_key)


def save_selected_style(session_id: str, style_id: str) -> None:
    with SessionLocal.begin() as db:
        context = _get_or_create_context(db, session_id)
        if context.selected_style_id != style_id:
            context.selected_style_id = style_id
            _mark_changed(context)


def mark_effect_generated(session_id: str, image_url: str, request: str) -> None:
    with SessionLocal.begin() as db:
        context = _get_or_create_context(db, session_id)
        context.generated_image_url = image_url
        context.generation_request = request.strip()
        context.effect_revision = context.context_revision or 0
        context.updated_at = utcnow()


def reset_design_session(session_id: str) -> None:
    object_keys: list[str] = []
    with SessionLocal.begin() as db:
        context = db.get(ChatSessionContext, session_id)
        generated_image_url = context.generated_image_url if context else None
        object_keys = list(
            db.scalars(
                select(DesignReferenceImage.object_key).where(
                    DesignReferenceImage.session_id == session_id
                )
            )
        )
        db.execute(
            delete(DesignReferenceImage).where(
                DesignReferenceImage.session_id == session_id
            )
        )
        if context is not None:
            db.delete(context)
    _delete_generated_file(generated_image_url)
    for object_key in object_keys:
        if object_key:
            get_image_store().delete(object_key)


def cleanup_expired_design_assets(now: datetime | None = None) -> int:
    cutoff = (now or utcnow()) - timedelta(days=RETENTION_DAYS)
    expired_urls: list[str] = []
    cleaned = 0
    with SessionLocal.begin() as db:
        contexts = list(
            db.scalars(
                select(ChatSessionContext).where(
                    ChatSessionContext.updated_at < cutoff,
                    ChatSessionContext.assets_expired_at.is_(None),
                    ChatSessionContext.session_id.not_in(
                        select(ChatConversation.session_id)
                    ),
                )
            )
        )
        for context in contexts:
            if context.generated_image_url:
                expired_urls.append(context.generated_image_url)
            expired_keys = list(
                db.scalars(
                    select(DesignReferenceImage.object_key).where(
                        DesignReferenceImage.session_id == context.session_id
                    )
                )
            )
            db.execute(
                delete(DesignReferenceImage).where(
                    DesignReferenceImage.session_id == context.session_id
                )
            )
            for object_key in expired_keys:
                if object_key:
                    get_image_store().delete(object_key)
            context.reference_image_data_url = None
            context.reference_image_request = None
            context.generated_image_url = None
            context.generation_request = None
            context.effect_revision = None
            context.assets_expired_at = now or utcnow()
            cleaned += 1
    for image_url in expired_urls:
        _delete_generated_file(image_url)
    return cleaned


def material_scheme_summary(materials: tuple[MaterialReference, ...]) -> str:
    if not materials:
        return "未使用用户石材参考图"
    lines = []
    for index, material in enumerate(materials, 1):
        name = material.name or material.original_name or f"石材 {index}"
        usage = "、".join(material.usages) if material.usages else "由系统合理安排"
        lines.append(f"- {name}：{usage}")
    return "\n".join(lines)
