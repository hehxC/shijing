"""统一记录用户级设计任务和单次 AI 调用。

外部只使用 ``observe_design_run`` 与 ``observe_ai_call`` 两个接口。模块内部
负责上下文传播、计时、Token/成本汇总、错误分类、敏感信息脱敏和数据库
失败兜底。离线评测没有设计任务上下文时，AI 调用观测自动退化为空操作。
"""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from functools import lru_cache
import json
import logging
import os
import re
import time
from typing import Iterator

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.database import SessionLocal
from app.models.ai_call_record import (
    AiCallRecord,
    AiCallStatus,
    AiErrorType,
    AiOperation,
)
from app.models.design_run import DesignRun, DesignRunOperation, DesignRunStatus
from app.observability import log_event


logger = logging.getLogger(__name__)

# 当前设计任务通过 ContextVar 进入同步流式生成器和 LangGraph 节点，调用方无需
# 在每一层函数中重复传递 request_id、user_id 与 session_id。
_CURRENT_DESIGN_RUN: ContextVar["DesignRunObservation | None"] = ContextVar(
    "current_design_run",
    default=None,
)

# 错误摘要最多入库 1000 个字符，既保留定位信息，也避免供应商响应无限膨胀。
_MAX_ERROR_MESSAGE_LENGTH = 1000
_DATA_URL_PATTERN = re.compile(r"data:image/[^;,\s]+;base64,[A-Za-z0-9+/=]+", re.I)
_BEARER_PATTERN = re.compile(r"(?i)(authorization\s*[:=]\s*bearer|bearer)\s+[^\s,;]+")
_SECRET_PATTERN = re.compile(
    r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*[^\s,;]+"
)


@dataclass
class DesignRunObservation:
    """当前用户任务的只读身份与内部失败状态。"""

    request_id: str
    run_id: int | None
    user_id: int
    session_id: str
    operation: DesignRunOperation
    critical_failure_stage: str | None = None


@dataclass
class AiCallObservation:
    """调用期间收集供应商返回的用量和显式成本。"""

    enabled: bool
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost: Decimal | None = None
    retry_count: int = 0

    def capture_usage(self, message) -> None:
        """从 LangChain 消息的标准或供应商元数据中累加 Token 用量。"""
        usage = getattr(message, "usage_metadata", None) or {}
        response_metadata = getattr(message, "response_metadata", None) or {}
        token_usage = response_metadata.get("token_usage") or response_metadata.get("usage") or {}

        input_tokens = usage.get("input_tokens")
        if input_tokens is None:
            input_tokens = token_usage.get("prompt_tokens") or token_usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        if output_tokens is None:
            output_tokens = token_usage.get("completion_tokens") or token_usage.get("output_tokens")

        if isinstance(input_tokens, int) and input_tokens >= 0:
            self.input_tokens = (self.input_tokens or 0) + input_tokens
        if isinstance(output_tokens, int) and output_tokens >= 0:
            self.output_tokens = (self.output_tokens or 0) + output_tokens

    def set_estimated_cost(self, cost: Decimal | str | float | None) -> None:
        """保存供应商返回或调用方已知的本次调用成本。"""
        if cost is None:
            return
        try:
            parsed = Decimal(str(cost))
        except (InvalidOperation, ValueError):
            return
        if parsed >= 0:
            self.estimated_cost = parsed

    def record_retry(self, retry_count: int) -> None:
        """记录统一重试模块已经执行的实际重试次数。"""
        self.retry_count = max(self.retry_count, retry_count)


def current_design_run() -> DesignRunObservation | None:
    """返回当前设计任务上下文；主要供测试和诊断使用。"""
    return _CURRENT_DESIGN_RUN.get()


def _safe_database_write(action) -> bool:
    """执行观测写入；失败只记日志，不影响用户主流程。"""
    try:
        action()
    except Exception as exc:
        log_event(
            logger,
            "observation_database_write_failed",
            level=logging.ERROR,
            error_type=exc.__class__.__name__,
            error_message=str(exc),
        )
        return False
    return True


def _create_design_run(
    request_id: str,
    user_id: int,
    session_id: str,
    operation: DesignRunOperation,
) -> int | None:
    """创建运行中任务并返回主键；数据库不可用时返回空。"""
    run_id: int | None = None

    def write() -> None:
        nonlocal run_id
        with SessionLocal.begin() as db:
            row = DesignRun(
                request_id=request_id,
                user_id=user_id,
                session_id=session_id,
                operation=operation,
                status=DesignRunStatus.RUNNING,
            )
            db.add(row)
            db.flush()
            run_id = row.id

    persisted = _safe_database_write(write)
    log_event(
        logger,
        "design_run_started",
        request_id=request_id,
        user_id=user_id,
        session_id=session_id,
        operation=operation,
        design_run_id=run_id,
        persisted=persisted,
    )
    return run_id


def _finish_design_run(
    observation: DesignRunObservation,
    status: DesignRunStatus,
    latency_ms: int,
    failure_stage: str | None,
) -> None:
    """完成任务并从调用明细汇总成本和生成图片数量。"""
    if observation.run_id is None:
        log_event(
            logger,
            "design_run_completed",
            request_id=observation.request_id,
            user_id=observation.user_id,
            session_id=observation.session_id,
            operation=observation.operation,
            status=status,
            latency_ms=latency_ms,
            failure_stage=failure_stage,
            persisted=False,
        )
        return

    total_cost: Decimal | None = None
    generated_images = 0

    def write() -> None:
        nonlocal total_cost, generated_images
        with SessionLocal.begin() as db:
            row = db.get(DesignRun, observation.run_id)
            if row is None:
                return
            total_cost = db.scalar(
                select(func.sum(AiCallRecord.estimated_cost)).where(
                    AiCallRecord.design_run_id == observation.run_id
                )
            )
            generated_images = db.scalar(
                select(func.count(AiCallRecord.id)).where(
                    AiCallRecord.design_run_id == observation.run_id,
                    AiCallRecord.operation == AiOperation.IMAGE_GENERATION,
                    AiCallRecord.status == AiCallStatus.SUCCEEDED,
                )
            )
            row.status = status
            row.completed_at = datetime.now()
            row.total_latency_ms = latency_ms
            row.total_estimated_cost = total_cost
            generated_images = generated_images or 0
            row.generated_image_count = generated_images
            row.failure_stage = failure_stage

    persisted = _safe_database_write(write)
    log_event(
        logger,
        "design_run_completed",
        request_id=observation.request_id,
        user_id=observation.user_id,
        session_id=observation.session_id,
        operation=observation.operation,
        status=status,
        latency_ms=latency_ms,
        estimated_cost=total_cost,
        generated_image_count=generated_images,
        failure_stage=failure_stage,
        persisted=persisted,
    )


@contextmanager
def observe_design_run(
    *,
    request_id: str,
    user_id: int,
    session_id: str,
    operation: DesignRunOperation,
) -> Iterator[DesignRunObservation]:
    """观察一次用户可感知的完整设计任务。

    未捕获异常会被重新抛出；观测写入本身永远不会改变业务结果。被内部
    降级逻辑吞掉的关键 AI 调用错误会通过 ``critical_failure_stage`` 把任务
    标记为失败。
    """
    started = time.perf_counter()
    observation = DesignRunObservation(
        request_id=request_id,
        run_id=_create_design_run(request_id, user_id, session_id, operation),
        user_id=user_id,
        session_id=session_id,
        operation=operation,
    )
    # Starlette 会在多个复制的线程上下文中逐次推进同步流式生成器。ContextVar
    # Token 只能在创建它的原始 Context 中 reset，因此这里保存旧值，并在结束时
    # 直接 set 回去；这样跨上下文恢复生成器时不会让已经成功的响应提前断开。
    previous_observation = _CURRENT_DESIGN_RUN.get()
    _CURRENT_DESIGN_RUN.set(observation)
    status = DesignRunStatus.SUCCEEDED
    failure_stage: str | None = None
    try:
        yield observation
    except BaseException as exc:
        status = (
            DesignRunStatus.CANCELLED
            if _classify_error(exc) == AiErrorType.CLIENT_CANCELLED
            else DesignRunStatus.FAILED
        )
        failure_stage = observation.critical_failure_stage or "request"
        raise
    finally:
        if status == DesignRunStatus.SUCCEEDED and observation.critical_failure_stage:
            status = DesignRunStatus.FAILED
            failure_stage = observation.critical_failure_stage
        latency_ms = max(0, round((time.perf_counter() - started) * 1000))
        _CURRENT_DESIGN_RUN.set(previous_observation)
        _finish_design_run(observation, status, latency_ms, failure_stage)


def _sanitize_error(exc: BaseException) -> str:
    """把异常压缩为不含常见凭证和图片数据的安全摘要。"""
    message = str(exc) or exc.__class__.__name__
    message = _DATA_URL_PATTERN.sub("[image-data-redacted]", message)
    message = _BEARER_PATTERN.sub(r"\1 [redacted]", message)
    message = _SECRET_PATTERN.sub(r"\1=[redacted]", message)
    return message[:_MAX_ERROR_MESSAGE_LENGTH]


def _classify_error(exc: BaseException) -> AiErrorType:
    """将具体异常映射为稳定错误分类。"""
    if isinstance(exc, (GeneratorExit, asyncio.CancelledError, BrokenPipeError)):
        return AiErrorType.CLIENT_CANCELLED
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return AiErrorType.TIMEOUT
    if isinstance(exc, SQLAlchemyError):
        return AiErrorType.DATABASE_ERROR

    text = str(exc).lower()
    if "429" in text or "rate limit" in text or "限流" in text:
        return AiErrorType.RATE_LIMITED
    if any(marker in text for marker in ("401", "403", "api key", "authentication", "未配置")):
        return AiErrorType.AUTHENTICATION_FAILED
    if any(marker in text for marker in ("timeout", "timed out", "超时")):
        return AiErrorType.TIMEOUT
    if any(marker in text for marker in ("502", "503", "504", "unavailable", "连接失败")):
        return AiErrorType.PROVIDER_UNAVAILABLE
    if any(marker in text for marker in ("content policy", "safety", "内容安全", "拒绝")):
        return AiErrorType.CONTENT_REJECTED
    if any(marker in text for marker in ("invalid", "无效", "格式错误", "参数")):
        return AiErrorType.INVALID_REQUEST
    if any(marker in text for marker in ("storage", "保存失败", "文件不存在")):
        return AiErrorType.STORAGE_ERROR
    return AiErrorType.INTERNAL_ERROR


@lru_cache(maxsize=1)
def _model_pricing() -> dict:
    """读取模型价格配置；未配置或格式错误时不估算成本。

    ``MODEL_PRICING_JSON`` 示例：
    ``{"deepseek-chat":{"input_per_million":"1","output_per_million":"2"},
    "image-model":{"per_call":"0.03"}}``。
    """
    raw = os.getenv("MODEL_PRICING_JSON", "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("MODEL_PRICING_JSON is invalid; cost estimation disabled")
        return {}
    return value if isinstance(value, dict) else {}


def _estimate_cost(
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
) -> Decimal | None:
    """按调用发生时配置的每百万 Token 或每次调用价格估算成本。"""
    pricing = _model_pricing().get(model)
    if not isinstance(pricing, dict):
        return None
    try:
        if pricing.get("per_call") is not None:
            return Decimal(str(pricing["per_call"]))
        if input_tokens is None and output_tokens is None:
            return None
        input_price = Decimal(str(pricing.get("input_per_million", "0")))
        output_price = Decimal(str(pricing.get("output_per_million", "0")))
        return (
            Decimal(input_tokens or 0) * input_price
            + Decimal(output_tokens or 0) * output_price
        ) / Decimal(1_000_000)
    except (InvalidOperation, TypeError, ValueError):
        return None


def _save_ai_call(
    design: DesignRunObservation,
    *,
    operation: AiOperation,
    provider: str,
    model: str,
    status: AiCallStatus,
    observation: AiCallObservation,
    latency_ms: int,
    retry_count: int,
    error_type: AiErrorType | None,
    error_message: str | None,
) -> None:
    """把一次完成的 AI 调用写入数据库。"""
    cost = observation.estimated_cost
    if cost is None:
        cost = _estimate_cost(model, observation.input_tokens, observation.output_tokens)

    def write() -> None:
        with SessionLocal.begin() as db:
            db.add(
                AiCallRecord(
                    request_id=design.request_id,
                    design_run_id=design.run_id,
                    user_id=design.user_id,
                    session_id=design.session_id,
                    operation=operation,
                    provider=provider,
                    model=model,
                    status=status,
                    input_tokens=observation.input_tokens,
                    output_tokens=observation.output_tokens,
                    estimated_cost=cost,
                    latency_ms=latency_ms,
                    retry_count=max(0, retry_count),
                    error_type=error_type,
                    error_message=error_message,
                )
            )

    persisted = _safe_database_write(write)
    log_event(
        logger,
        "ai_call_completed",
        request_id=design.request_id,
        user_id=design.user_id,
        session_id=design.session_id,
        design_run_id=design.run_id,
        operation=operation,
        provider=provider,
        model=model,
        status=status,
        input_tokens=observation.input_tokens,
        output_tokens=observation.output_tokens,
        estimated_cost=cost,
        latency_ms=latency_ms,
        retry_count=max(0, retry_count),
        error_type=error_type,
        error_message=error_message,
        persisted=persisted,
    )


@contextmanager
def observe_ai_call(
    operation: AiOperation,
    *,
    provider: str,
    model: str,
    retry_count: int = 0,
    critical: bool = True,
) -> Iterator[AiCallObservation]:
    """观察一次具体 AI 调用，并在离开上下文时持久化最终结果。"""
    design = _CURRENT_DESIGN_RUN.get()
    observation = AiCallObservation(
        enabled=bool(design and design.run_id),
        retry_count=max(0, retry_count),
    )
    if design is None or design.run_id is None:
        yield observation
        return

    started = time.perf_counter()
    status = AiCallStatus.SUCCEEDED
    error_type: AiErrorType | None = None
    error_message: str | None = None
    try:
        yield observation
    except BaseException as exc:
        error_type = _classify_error(exc)
        status = (
            AiCallStatus.CANCELLED
            if error_type == AiErrorType.CLIENT_CANCELLED
            else AiCallStatus.FAILED
        )
        error_message = _sanitize_error(exc)
        if critical:
            design.critical_failure_stage = operation.value
        raise
    finally:
        latency_ms = max(0, round((time.perf_counter() - started) * 1000))
        _save_ai_call(
            design,
            operation=operation,
            provider=provider,
            model=model,
            status=status,
            observation=observation,
            latency_ms=latency_ms,
            retry_count=observation.retry_count,
            error_type=error_type,
            error_message=error_message,
        )
