from datetime import datetime

from sqlalchemy import JSON, DateTime, String, Text, func
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ChatSessionContext(Base):
    """应用层的跨模型会话记忆。

    这里保存的是业务上下文，不依赖某个模型自己的 checkpoint。
    reference_image_data_url 用于记住用户上传的原始参考图，便于后续继续生成不同风格效果图。
    """

    __tablename__ = "chat_session_contexts"

    session_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    reference_image_data_url: Mapped[str | None] = mapped_column(LONGTEXT)
    reference_image_request: Mapped[str | None] = mapped_column(Text)
    generated_image_url: Mapped[str | None] = mapped_column(String(512))
    generation_request: Mapped[str | None] = mapped_column(Text)
    material_analysis: Mapped[dict | None] = mapped_column(JSON)
    selected_style_id: Mapped[str | None] = mapped_column(String(64))
    context_revision: Mapped[int] = mapped_column(nullable=False, default=0)
    effect_revision: Mapped[int | None] = mapped_column()
    assets_expired_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
