"""用户级 AI 设计任务的持久化模型。"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DesignRunOperation(StrEnum):
    """用户发起的顶层设计任务类型。"""

    CHAT = "chat"
    EFFECT_GENERATION = "effect_generation"
    VISION_ANALYSIS = "vision_analysis"
    MATERIAL_QUOTE = "material_quote"


class DesignRunStatus(StrEnum):
    """设计任务从接收到结束的生命周期状态。"""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DesignRun(Base):
    """一次用户可感知的完整 AI 设计任务。

    一次任务可以包含路由、检索、文本模型、视觉模型或图像生成等多次
    AI 调用；这些明细由 ``AiCallRecord`` 保存，并通过 ``design_run_id``
    关联到本记录。
    """

    __tablename__ = "design_runs"
    __table_args__ = (
        CheckConstraint(
            "total_latency_ms IS NULL OR total_latency_ms >= 0",
            name="ck_design_run_latency_nonnegative",
        ),
        CheckConstraint(
            "total_estimated_cost IS NULL OR total_estimated_cost >= 0",
            name="ck_design_run_cost_nonnegative",
        ),
        CheckConstraint(
            "generated_image_count >= 0",
            name="ck_design_run_image_count_nonnegative",
        ),
        Index("ix_design_runs_user_created", "user_id", "created_at"),
        Index("ix_design_runs_session_created", "session_id", "created_at"),
        {"comment": "用户发起的一次完整 AI 设计任务及其汇总结果"},
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
        comment="设计任务内部主键",
    )
    request_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
        comment="贯穿 HTTP 请求、日志和模型调用的请求标识",
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="发起设计任务的登录用户 ID",
    )
    session_id: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        index=True,
        comment="任务所属设计会话的服务端会话标识",
    )
    operation: Mapped[DesignRunOperation] = mapped_column(
        Enum(
            DesignRunOperation,
            name="design_run_operation",
            native_enum=False,
            length=32,
            values_callable=lambda enum: [item.value for item in enum],
        ),
        nullable=False,
        index=True,
        comment="用户发起的顶层任务类型，如聊天、效果图生成或报价",
    )
    status: Mapped[DesignRunStatus] = mapped_column(
        Enum(
            DesignRunStatus,
            name="design_run_status",
            native_enum=False,
            length=16,
            values_callable=lambda enum: [item.value for item in enum],
        ),
        nullable=False,
        default=DesignRunStatus.RUNNING,
        index=True,
        comment="设计任务当前状态：运行中、成功、失败或取消",
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        comment="设计任务开始执行的时间",
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
        comment="设计任务成功、失败或取消的结束时间",
    )
    total_latency_ms: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="从任务开始到结束的总耗时，单位为毫秒",
    )
    total_estimated_cost: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 6),
        nullable=True,
        comment="任务内全部 AI 调用在发生时估算的总成本",
    )
    generated_image_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="本次任务成功生成并保存的效果图数量",
    )
    failure_stage: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="任务失败时最后执行的稳定阶段名称",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        index=True,
        comment="设计任务记录的创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="设计任务记录的最后更新时间",
    )
