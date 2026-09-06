"""单次 AI 模型或 AI 工具调用的观测模型。"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AiOperation(StrEnum):
    """生产链路中可独立计时和计费的 AI 调用类型。"""

    INTENT_ROUTING = "intent_routing"
    TEXT_CHAT = "text_chat"
    SQL_QUERY = "sql_query"
    VISION_ANALYSIS = "vision_analysis"
    IMAGE_GENERATION = "image_generation"


class AiCallStatus(StrEnum):
    """单次 AI 调用的最终状态。"""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AiErrorType(StrEnum):
    """用于聚合和告警的稳定 AI 调用错误分类。"""

    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    AUTHENTICATION_FAILED = "authentication_failed"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    INVALID_REQUEST = "invalid_request"
    CONTENT_REJECTED = "content_rejected"
    DATABASE_ERROR = "database_error"
    STORAGE_ERROR = "storage_error"
    INTERNAL_ERROR = "internal_error"
    CLIENT_CANCELLED = "client_cancelled"


class AiCallRecord(Base):
    """一次具体 AI 调用的成本、耗时、Token 和失败信息。"""

    __tablename__ = "ai_call_records"
    __table_args__ = (
        CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="ck_ai_call_input_tokens_nonnegative",
        ),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="ck_ai_call_output_tokens_nonnegative",
        ),
        CheckConstraint(
            "estimated_cost IS NULL OR estimated_cost >= 0",
            name="ck_ai_call_cost_nonnegative",
        ),
        CheckConstraint(
            "latency_ms >= 0",
            name="ck_ai_call_latency_nonnegative",
        ),
        CheckConstraint(
            "retry_count >= 0",
            name="ck_ai_call_retry_count_nonnegative",
        ),
        Index("ix_ai_call_records_request_created", "request_id", "created_at"),
        Index("ix_ai_call_records_user_created", "user_id", "created_at"),
        Index("ix_ai_call_records_session_created", "session_id", "created_at"),
        {"comment": "一次具体 AI 模型或 AI 工具调用的生产观测记录"},
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
        comment="AI 调用记录内部主键",
    )
    request_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        comment="关联 HTTP 请求、设计任务和结构化日志的请求标识",
    )
    design_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("design_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="所属用户级设计任务 ID；独立后台调用可以为空",
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="产生本次 AI 调用成本的登录用户 ID",
    )
    session_id: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        index=True,
        comment="调用所属设计会话的服务端会话标识",
    )
    operation: Mapped[AiOperation] = mapped_column(
        Enum(
            AiOperation,
            name="ai_operation",
            native_enum=False,
            length=32,
            values_callable=lambda enum: [item.value for item in enum],
        ),
        nullable=False,
        index=True,
        comment="调用类型，如意图路由、SQL 查询、视觉分析或图像生成",
    )
    provider: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        comment="实际处理调用的模型供应商稳定名称",
    )
    model: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        index=True,
        comment="调用发生时使用的完整模型名称",
    )
    status: Mapped[AiCallStatus] = mapped_column(
        Enum(
            AiCallStatus,
            name="ai_call_status",
            native_enum=False,
            length=16,
            values_callable=lambda enum: [item.value for item in enum],
        ),
        nullable=False,
        index=True,
        comment="本次调用的最终状态：成功、失败或取消",
    )
    input_tokens: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="供应商返回的输入 Token 数；无法获得时为空",
    )
    output_tokens: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="供应商返回的输出 Token 数；无法获得时为空",
    )
    estimated_cost: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 6),
        nullable=True,
        comment="按调用发生时价格估算的成本；无法估算时为空",
    )
    latency_ms: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="从调用发出到结束的耗时，单位为毫秒",
    )
    retry_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="同一逻辑调用在成功或最终失败前发生的重试次数",
    )
    error_type: Mapped[AiErrorType | None] = mapped_column(
        Enum(
            AiErrorType,
            name="ai_error_type",
            native_enum=False,
            length=32,
            values_callable=lambda enum: [item.value for item in enum],
        ),
        nullable=True,
        index=True,
        comment="失败时用于统计和告警的稳定错误分类",
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="失败时经过脱敏和截断的错误摘要，不保存密钥或堆栈",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        index=True,
        comment="AI 调用结束并写入观测记录的时间",
    )
