"""add ai observation tables

Revision ID: 445b52e993ee
Revises: ec176e80cea0
Create Date: 2026-08-10 17:56:06.098539

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "445b52e993ee"
down_revision: Union[str, Sequence[str], None] = "ec176e80cea0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """创建用户级设计任务表和单次 AI 调用明细表。"""
    op.create_table(
        "design_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False, comment="设计任务内部主键"),
        sa.Column("request_id", sa.String(length=64), nullable=False, comment="贯穿 HTTP 请求、日志和模型调用的请求标识"),
        sa.Column("user_id", sa.Integer(), nullable=False, comment="发起设计任务的登录用户 ID"),
        sa.Column("session_id", sa.String(length=128), nullable=False, comment="任务所属设计会话的服务端会话标识"),
        sa.Column("operation", sa.Enum("chat", "effect_generation", "vision_analysis", "material_quote", name="design_run_operation", native_enum=False, length=32), nullable=False, comment="用户发起的顶层任务类型，如聊天、效果图生成或报价"),
        sa.Column("status", sa.Enum("running", "succeeded", "failed", "cancelled", name="design_run_status", native_enum=False, length=16), nullable=False, comment="设计任务当前状态：运行中、成功、失败或取消"),
        sa.Column("started_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False, comment="设计任务开始执行的时间"),
        sa.Column("completed_at", sa.DateTime(), nullable=True, comment="设计任务成功、失败或取消的结束时间"),
        sa.Column("total_latency_ms", sa.Integer(), nullable=True, comment="从任务开始到结束的总耗时，单位为毫秒"),
        sa.Column("total_estimated_cost", sa.Numeric(precision=12, scale=6), nullable=True, comment="任务内全部 AI 调用在发生时估算的总成本"),
        sa.Column("generated_image_count", sa.Integer(), server_default="0", nullable=False, comment="本次任务成功生成并保存的效果图数量"),
        sa.Column("failure_stage", sa.String(length=64), nullable=True, comment="任务失败时最后执行的稳定阶段名称"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False, comment="设计任务记录的创建时间"),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False, comment="设计任务记录的最后更新时间"),
        sa.CheckConstraint("generated_image_count >= 0", name="ck_design_run_image_count_nonnegative"),
        sa.CheckConstraint("total_estimated_cost IS NULL OR total_estimated_cost >= 0", name="ck_design_run_cost_nonnegative"),
        sa.CheckConstraint("total_latency_ms IS NULL OR total_latency_ms >= 0", name="ck_design_run_latency_nonnegative"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        comment="用户发起的一次完整 AI 设计任务及其汇总结果",
    )
    op.create_index(op.f("ix_design_runs_created_at"), "design_runs", ["created_at"], unique=False)
    op.create_index(op.f("ix_design_runs_operation"), "design_runs", ["operation"], unique=False)
    op.create_index(op.f("ix_design_runs_request_id"), "design_runs", ["request_id"], unique=True)
    op.create_index("ix_design_runs_session_created", "design_runs", ["session_id", "created_at"], unique=False)
    op.create_index(op.f("ix_design_runs_session_id"), "design_runs", ["session_id"], unique=False)
    op.create_index(op.f("ix_design_runs_status"), "design_runs", ["status"], unique=False)
    op.create_index("ix_design_runs_user_created", "design_runs", ["user_id", "created_at"], unique=False)
    op.create_index(op.f("ix_design_runs_user_id"), "design_runs", ["user_id"], unique=False)

    op.create_table(
        "ai_call_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False, comment="AI 调用记录内部主键"),
        sa.Column("request_id", sa.String(length=64), nullable=False, comment="关联 HTTP 请求、设计任务和结构化日志的请求标识"),
        sa.Column("design_run_id", sa.Integer(), nullable=True, comment="所属用户级设计任务 ID；独立后台调用可以为空"),
        sa.Column("user_id", sa.Integer(), nullable=False, comment="产生本次 AI 调用成本的登录用户 ID"),
        sa.Column("session_id", sa.String(length=128), nullable=False, comment="调用所属设计会话的服务端会话标识"),
        sa.Column("operation", sa.Enum("intent_routing", "text_chat", "sql_query", "rag_retrieval", "rag_embedding", "vision_analysis", "image_generation", name="ai_operation", native_enum=False, length=32), nullable=False, comment="调用类型，如意图路由、SQL 查询、视觉分析或图像生成"),
        sa.Column("provider", sa.String(length=64), nullable=False, comment="实际处理调用的模型供应商稳定名称"),
        sa.Column("model", sa.String(length=128), nullable=False, comment="调用发生时使用的完整模型名称"),
        sa.Column("status", sa.Enum("succeeded", "failed", "cancelled", name="ai_call_status", native_enum=False, length=16), nullable=False, comment="本次调用的最终状态：成功、失败或取消"),
        sa.Column("input_tokens", sa.Integer(), nullable=True, comment="供应商返回的输入 Token 数；无法获得时为空"),
        sa.Column("output_tokens", sa.Integer(), nullable=True, comment="供应商返回的输出 Token 数；无法获得时为空"),
        sa.Column("estimated_cost", sa.Numeric(precision=12, scale=6), nullable=True, comment="按调用发生时价格估算的成本；无法估算时为空"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, comment="从调用发出到结束的耗时，单位为毫秒"),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False, comment="同一逻辑调用在成功或最终失败前发生的重试次数"),
        sa.Column("error_type", sa.Enum("timeout", "rate_limited", "authentication_failed", "provider_unavailable", "invalid_request", "content_rejected", "database_error", "storage_error", "internal_error", "client_cancelled", name="ai_error_type", native_enum=False, length=32), nullable=True, comment="失败时用于统计和告警的稳定错误分类"),
        sa.Column("error_message", sa.Text(), nullable=True, comment="失败时经过脱敏和截断的错误摘要，不保存密钥或堆栈"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False, comment="AI 调用结束并写入观测记录的时间"),
        sa.CheckConstraint("estimated_cost IS NULL OR estimated_cost >= 0", name="ck_ai_call_cost_nonnegative"),
        sa.CheckConstraint("input_tokens IS NULL OR input_tokens >= 0", name="ck_ai_call_input_tokens_nonnegative"),
        sa.CheckConstraint("latency_ms >= 0", name="ck_ai_call_latency_nonnegative"),
        sa.CheckConstraint("output_tokens IS NULL OR output_tokens >= 0", name="ck_ai_call_output_tokens_nonnegative"),
        sa.CheckConstraint("retry_count >= 0", name="ck_ai_call_retry_count_nonnegative"),
        sa.ForeignKeyConstraint(["design_run_id"], ["design_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        comment="一次具体 AI 模型或 AI 工具调用的生产观测记录",
    )
    op.create_index(op.f("ix_ai_call_records_created_at"), "ai_call_records", ["created_at"], unique=False)
    op.create_index(op.f("ix_ai_call_records_design_run_id"), "ai_call_records", ["design_run_id"], unique=False)
    op.create_index(op.f("ix_ai_call_records_error_type"), "ai_call_records", ["error_type"], unique=False)
    op.create_index(op.f("ix_ai_call_records_model"), "ai_call_records", ["model"], unique=False)
    op.create_index(op.f("ix_ai_call_records_operation"), "ai_call_records", ["operation"], unique=False)
    op.create_index(op.f("ix_ai_call_records_provider"), "ai_call_records", ["provider"], unique=False)
    op.create_index("ix_ai_call_records_request_created", "ai_call_records", ["request_id", "created_at"], unique=False)
    op.create_index(op.f("ix_ai_call_records_request_id"), "ai_call_records", ["request_id"], unique=False)
    op.create_index("ix_ai_call_records_session_created", "ai_call_records", ["session_id", "created_at"], unique=False)
    op.create_index(op.f("ix_ai_call_records_session_id"), "ai_call_records", ["session_id"], unique=False)
    op.create_index(op.f("ix_ai_call_records_status"), "ai_call_records", ["status"], unique=False)
    op.create_index("ix_ai_call_records_user_created", "ai_call_records", ["user_id", "created_at"], unique=False)
    op.create_index(op.f("ix_ai_call_records_user_id"), "ai_call_records", ["user_id"], unique=False)


def downgrade() -> None:
    """先删除调用明细，再删除其依赖的设计任务表。"""
    op.drop_index(op.f("ix_ai_call_records_user_id"), table_name="ai_call_records")
    op.drop_index("ix_ai_call_records_user_created", table_name="ai_call_records")
    op.drop_index(op.f("ix_ai_call_records_status"), table_name="ai_call_records")
    op.drop_index(op.f("ix_ai_call_records_session_id"), table_name="ai_call_records")
    op.drop_index("ix_ai_call_records_session_created", table_name="ai_call_records")
    op.drop_index(op.f("ix_ai_call_records_request_id"), table_name="ai_call_records")
    op.drop_index("ix_ai_call_records_request_created", table_name="ai_call_records")
    op.drop_index(op.f("ix_ai_call_records_provider"), table_name="ai_call_records")
    op.drop_index(op.f("ix_ai_call_records_operation"), table_name="ai_call_records")
    op.drop_index(op.f("ix_ai_call_records_model"), table_name="ai_call_records")
    op.drop_index(op.f("ix_ai_call_records_error_type"), table_name="ai_call_records")
    op.drop_index(op.f("ix_ai_call_records_design_run_id"), table_name="ai_call_records")
    op.drop_index(op.f("ix_ai_call_records_created_at"), table_name="ai_call_records")
    op.drop_table("ai_call_records")
    op.drop_index(op.f("ix_design_runs_user_id"), table_name="design_runs")
    op.drop_index("ix_design_runs_user_created", table_name="design_runs")
    op.drop_index(op.f("ix_design_runs_status"), table_name="design_runs")
    op.drop_index(op.f("ix_design_runs_session_id"), table_name="design_runs")
    op.drop_index("ix_design_runs_session_created", table_name="design_runs")
    op.drop_index(op.f("ix_design_runs_request_id"), table_name="design_runs")
    op.drop_index(op.f("ix_design_runs_operation"), table_name="design_runs")
    op.drop_index(op.f("ix_design_runs_created_at"), table_name="design_runs")
    op.drop_table("design_runs")
