from datetime import datetime

from sqlalchemy import DateTime, Integer, JSON, String, func
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DesignReferenceImage(Base):
    """A private image attached to one application-level design session."""

    __tablename__ = "design_reference_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    data_url: Mapped[str] = mapped_column(LONGTEXT, nullable=False)
    original_name: Mapped[str | None] = mapped_column(String(255))
    material_name: Mapped[str | None] = mapped_column(String(100))
    usages: Mapped[list[str] | None] = mapped_column(JSON)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
