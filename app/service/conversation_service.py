import re
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, func, select

from app.database import SessionLocal
from app.models.chat_conversation import ChatConversation
from app.models.chat_message import ChatMessage
from app.models.chat_session_context import ChatSessionContext
from app.models.design_reference_image import DesignReferenceImage
from app.service.image_generation_service import GENERATED_DIR


MESSAGE_PENDING = "pending"
MESSAGE_COMPLETE = "complete"
MESSAGE_FAILED = "failed"
_PROTECTED_IMAGE_PATTERN = re.compile(
    r"/api/conversations/[^/\s)]+/generated/([A-Za-z0-9._-]+)"
)


def local_now() -> datetime:
    # MySQL columns use naive local DATETIME via CURRENT_TIMESTAMP; keep writes consistent.
    return datetime.now()


def client_session_id_from_scoped(session_id: str) -> str:
    parts = session_id.split(":", 2)
    return parts[2] if len(parts) == 3 and parts[0] == "user" else session_id


def protected_generated_url(session_id: str, image_url: str) -> str:
    prefix = "/static/generated/"
    if not image_url.startswith(prefix):
        return image_url
    filename = image_url.removeprefix(prefix)
    client_session_id = client_session_id_from_scoped(session_id)
    return f"/api/conversations/{client_session_id}/generated/{filename}"


def _title_from_message(message: str) -> str:
    compact = " ".join(message.split()).strip()
    if not compact:
        return "未命名庭院设计"
    return compact if len(compact) <= 30 else f"{compact[:30]}…"


def _serialize_conversation(row: ChatConversation, preview: str | None = None) -> dict:
    return {
        "session_id": row.client_session_id,
        "title": row.title,
        "title_manually_edited": row.title_manually_edited,
        "preview": preview or "",
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _serialize_message(row: ChatMessage) -> dict:
    return {
        "id": row.id,
        "turn_id": row.turn_id,
        "sequence": row.sequence,
        "role": row.role,
        "content": row.content,
        "request_text": row.request_text,
        "message_type": row.message_type,
        "status": row.status,
        "style_id": row.style_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def begin_user_turn(
    *,
    user_id: int,
    session_id: str,
    client_session_id: str,
    display_content: str,
    request_text: str,
    message_type: str,
    style_id: str | None,
) -> ChatMessage:
    now = local_now()
    with SessionLocal.begin() as db:
        conversation = db.scalar(
            select(ChatConversation).where(
                ChatConversation.user_id == user_id,
                ChatConversation.client_session_id == client_session_id,
            )
        )
        if conversation is None:
            conversation = ChatConversation(
                user_id=user_id,
                session_id=session_id,
                client_session_id=client_session_id,
                title=_title_from_message(display_content),
                created_at=now,
                updated_at=now,
            )
            db.add(conversation)
            db.flush()

        last_sequence = db.scalar(
            select(func.max(ChatMessage.sequence)).where(
                ChatMessage.conversation_id == conversation.id
            )
        ) or 0
        message = ChatMessage(
            conversation_id=conversation.id,
            turn_id=str(uuid4()),
            sequence=last_sequence + 1,
            role="user",
            content=display_content.strip(),
            request_text=request_text.strip(),
            message_type=message_type,
            status=MESSAGE_PENDING,
            style_id=style_id,
            created_at=now,
        )
        conversation.updated_at = now
        db.add(message)
        db.flush()
        db.expunge(message)
        return message


def complete_turn(user_message_id: int, assistant_content: str) -> None:
    cleaned = assistant_content.strip()
    if not cleaned:
        fail_turn(user_message_id)
        return

    with SessionLocal.begin() as db:
        user_message = db.get(ChatMessage, user_message_id)
        if user_message is None or user_message.role != "user":
            return
        conversation = db.get(ChatConversation, user_message.conversation_id)
        if conversation is None:
            return

        user_message.status = MESSAGE_COMPLETE
        image_match = _PROTECTED_IMAGE_PATTERN.search(cleaned)
        assistant = ChatMessage(
            conversation_id=conversation.id,
            turn_id=user_message.turn_id,
            sequence=user_message.sequence + 1,
            role="assistant",
            content=cleaned,
            message_type=user_message.message_type,
            status=MESSAGE_COMPLETE,
            style_id=user_message.style_id,
            generated_image_filename=image_match.group(1) if image_match else None,
            created_at=local_now(),
        )
        conversation.updated_at = local_now()
        db.add(assistant)


def fail_turn(user_message_id: int) -> None:
    with SessionLocal.begin() as db:
        message = db.get(ChatMessage, user_message_id)
        if message is None or message.role != "user" or message.status == MESSAGE_COMPLETE:
            return
        message.status = MESSAGE_FAILED
        conversation = db.get(ChatConversation, message.conversation_id)
        if conversation is not None:
            conversation.updated_at = local_now()


def list_conversations(user_id: int) -> list[dict]:
    with SessionLocal() as db:
        rows = list(
            db.scalars(
                select(ChatConversation)
                .where(ChatConversation.user_id == user_id)
                .order_by(ChatConversation.updated_at.desc(), ChatConversation.id.desc())
            )
        )
        result = []
        for row in rows:
            preview = db.scalar(
                select(ChatMessage.content)
                .where(ChatMessage.conversation_id == row.id)
                .order_by(ChatMessage.sequence.desc())
                .limit(1)
            )
            result.append(_serialize_conversation(row, preview))
        return result


def get_conversation(user_id: int, client_session_id: str) -> dict | None:
    with SessionLocal() as db:
        conversation = db.scalar(
            select(ChatConversation).where(
                ChatConversation.user_id == user_id,
                ChatConversation.client_session_id == client_session_id,
            )
        )
        if conversation is None:
            return None
        messages = list(
            db.scalars(
                select(ChatMessage)
                .where(ChatMessage.conversation_id == conversation.id)
                .order_by(ChatMessage.sequence, ChatMessage.id)
            )
        )
        return {
            "conversation": _serialize_conversation(conversation),
            "messages": [_serialize_message(message) for message in messages],
        }


def rename_conversation(user_id: int, client_session_id: str, title: str) -> bool:
    cleaned = " ".join(title.split()).strip()
    if not cleaned:
        return False
    with SessionLocal.begin() as db:
        conversation = db.scalar(
            select(ChatConversation).where(
                ChatConversation.user_id == user_id,
                ChatConversation.client_session_id == client_session_id,
            )
        )
        if conversation is None:
            return False
        conversation.title = cleaned[:120]
        conversation.title_manually_edited = True
        conversation.updated_at = local_now()
        return True


def load_recent_model_history(session_id: str, limit_turns: int = 10) -> list[dict]:
    """Return the latest complete user/assistant pairs in chronological order."""
    try:
        with SessionLocal() as db:
            conversation = db.scalar(
                select(ChatConversation).where(ChatConversation.session_id == session_id)
            )
            if conversation is None:
                return []
            turn_rows = list(
                db.execute(
                    select(ChatMessage.turn_id, func.max(ChatMessage.sequence).label("last_sequence"))
                    .where(
                        ChatMessage.conversation_id == conversation.id,
                        ChatMessage.status == MESSAGE_COMPLETE,
                    )
                    .group_by(ChatMessage.turn_id)
                    .having(func.count(ChatMessage.id) == 2)
                    .order_by(func.max(ChatMessage.sequence).desc())
                    .limit(limit_turns)
                )
            )
            turn_ids = [row.turn_id for row in turn_rows]
            if not turn_ids:
                return []
            messages = list(
                db.scalars(
                    select(ChatMessage)
                    .where(
                        ChatMessage.conversation_id == conversation.id,
                        ChatMessage.turn_id.in_(turn_ids),
                        ChatMessage.status == MESSAGE_COMPLETE,
                    )
                    .order_by(ChatMessage.sequence)
                )
            )
            return [{"role": message.role, "content": message.content} for message in messages]
    except Exception:
        # Chat remains usable while a new database is being initialized.
        return []


def conversation_owns_generated_file(
    user_id: int, client_session_id: str, filename: str
) -> bool:
    with SessionLocal() as db:
        conversation = db.scalar(
            select(ChatConversation).where(
                ChatConversation.user_id == user_id,
                ChatConversation.client_session_id == client_session_id,
            )
        )
        if conversation is None:
            return False
        return db.scalar(
            select(func.count(ChatMessage.id)).where(
                ChatMessage.conversation_id == conversation.id,
                ChatMessage.generated_image_filename == filename,
            )
        ) > 0


def generated_file_path(filename: str) -> Path | None:
    path = (GENERATED_DIR / filename).resolve()
    root = GENERATED_DIR.resolve()
    return path if path.parent == root and path.is_file() else None


def delete_conversation(user_id: int, client_session_id: str) -> bool:
    filenames: set[str] = set()
    generated_image_url: str | None = None
    with SessionLocal.begin() as db:
        conversation = db.scalar(
            select(ChatConversation).where(
                ChatConversation.user_id == user_id,
                ChatConversation.client_session_id == client_session_id,
            )
        )
        if conversation is None:
            return False
        filenames.update(
            filename
            for filename in db.scalars(
                select(ChatMessage.generated_image_filename).where(
                    ChatMessage.conversation_id == conversation.id,
                    ChatMessage.generated_image_filename.is_not(None),
                )
            )
            if filename
        )
        context = db.get(ChatSessionContext, conversation.session_id)
        if context is not None:
            generated_image_url = context.generated_image_url
            db.delete(context)
        db.execute(
            delete(DesignReferenceImage).where(
                DesignReferenceImage.session_id == conversation.session_id
            )
        )
        db.execute(
            delete(ChatMessage).where(ChatMessage.conversation_id == conversation.id)
        )
        db.delete(conversation)

    if generated_image_url and generated_image_url.startswith("/static/generated/"):
        filenames.add(generated_image_url.removeprefix("/static/generated/"))
    for filename in filenames:
        path = generated_file_path(filename)
        if path is not None:
            path.unlink(missing_ok=True)
    return True
