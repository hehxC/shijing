from sqlalchemy.exc import SQLAlchemyError

from app.database import SessionLocal
from app.models.chat_user_message import ChatUserMessage


def remember_user_message(
    *,
    session_id: str,
    message: str,
    image: str | None,
    user_id: int | None,
) -> None:
    """保存用户输入。失败时不影响聊天主流程。"""
    cleaned_message = message.strip()
    if not cleaned_message and not image:
        return

    record = ChatUserMessage(
        user_id=user_id,
        session_id=session_id,
        message=cleaned_message or "[图片]",
        has_image=1 if image else 0,
        image_size=len(image) if image else None,
    )

    try:
        with SessionLocal.begin() as db:
            db.add(record)
    except SQLAlchemyError:
        return
