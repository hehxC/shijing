"""集中定义 AI 调用的超时、有限重试和重试日志策略。

调用方只需要按 ``AiOperation`` 获取策略，或把一次同步调用/流式迭代交给本模块。
模型客户端自身的隐藏重试统一关闭，确保实际重试次数能够进入观测记录。
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
import logging
import os
import time
from typing import TypeVar
from urllib.error import HTTPError, URLError

from app.models.ai_call_record import AiOperation
from app.observability import log_event


logger = logging.getLogger(__name__)
T = TypeVar("T")


@dataclass(frozen=True)
class AiResiliencePolicy:
    """一种 AI operation 在生产链路中的稳定执行策略。"""

    timeout_seconds: float
    max_retries: int
    backoff_seconds: float


_DEFAULTS: dict[AiOperation, tuple[float, int]] = {
    AiOperation.INTENT_ROUTING: (10, 1),
    AiOperation.TEXT_CHAT: (60, 1),
    AiOperation.SQL_QUERY: (15, 1),
    AiOperation.VISION_ANALYSIS: (90, 1),
    AiOperation.IMAGE_GENERATION: (240, 1),
}


def policy_for(operation: AiOperation) -> AiResiliencePolicy:
    """读取 operation 策略；环境变量非法时安全回退到代码内默认值。"""
    default_timeout, default_retries = _DEFAULTS[operation]
    prefix = f"AI_{operation.value.upper()}"
    try:
        timeout_seconds = float(
            os.getenv(f"{prefix}_TIMEOUT_SECONDS", str(default_timeout))
        )
        max_retries = int(os.getenv(f"{prefix}_MAX_RETRIES", str(default_retries)))
        backoff_seconds = float(os.getenv("AI_RETRY_BACKOFF_SECONDS", "0.5"))
    except ValueError:
        timeout_seconds = default_timeout
        max_retries = default_retries
        backoff_seconds = 0.5
    return AiResiliencePolicy(
        timeout_seconds=max(0.1, timeout_seconds),
        max_retries=max(0, max_retries),
        backoff_seconds=max(0, backoff_seconds),
    )


def _exception_chain(exc: BaseException) -> Iterator[BaseException]:
    """遍历包装异常及其根因，兼容供应商 SDK 的多层异常包装。"""
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _is_retryable(exc: BaseException) -> bool:
    """只重试临时网络、限流、超时和 5xx 错误。"""
    for current in _exception_chain(exc):
        if isinstance(current, HTTPError):
            return current.code == 429 or current.code in {502, 503, 504}
        if isinstance(current, (TimeoutError, ConnectionError, URLError)):
            return True

        status_code = getattr(current, "status_code", None)
        if status_code == 429 or status_code in {502, 503, 504}:
            return True

        name = current.__class__.__name__.lower()
        text = str(current).lower()
        if any(
            marker in name
            for marker in ("timeout", "ratelimit", "connection", "serviceunavailable")
        ):
            return True
        if "429" in text or "rate limit" in text:
            return True
        if any(marker in text for marker in ("http 502", "http 503", "http 504")):
            return True
    return False


def _retry_delay(policy: AiResiliencePolicy, retry_number: int) -> float:
    """使用有上限的指数退避，避免供应商故障时立即重复施压。"""
    return min(policy.backoff_seconds * (2 ** (retry_number - 1)), 4.0)


def _before_retry(
    operation: AiOperation,
    policy: AiResiliencePolicy,
    retry_number: int,
    exc: BaseException,
    on_retry: Callable[[int], None] | None,
) -> None:
    delay = _retry_delay(policy, retry_number)
    if on_retry is not None:
        on_retry(retry_number)
    log_event(
        logger,
        "ai_call_retrying",
        operation=operation,
        retry_count=retry_number,
        max_retries=policy.max_retries,
        backoff_ms=round(delay * 1_000),
        error_type=exc.__class__.__name__,
        error_message=str(exc),
    )
    if delay:
        time.sleep(delay)


def run_with_resilience(
    operation: AiOperation,
    call: Callable[[], T],
    *,
    on_retry: Callable[[int], None] | None = None,
) -> T:
    """执行一次同步调用，只对可重试错误进行有限重试。"""
    policy = policy_for(operation)
    retry_number = 0
    while True:
        try:
            return call()
        except BaseException as exc:
            if retry_number >= policy.max_retries or not _is_retryable(exc):
                raise
            retry_number += 1
            _before_retry(operation, policy, retry_number, exc, on_retry)


def iterate_with_resilience(
    operation: AiOperation,
    stream_factory: Callable[[], Iterator[T]],
    *,
    on_retry: Callable[[int], None] | None = None,
) -> Iterator[T]:
    """迭代流式调用；一旦收到任意模型块便禁止从头重试，避免重复输出。"""
    policy = policy_for(operation)
    retry_number = 0
    while True:
        emitted_model_chunk = False
        try:
            for item in stream_factory():
                emitted_model_chunk = True
                yield item
            return
        except BaseException as exc:
            if (
                emitted_model_chunk
                or retry_number >= policy.max_retries
                or not _is_retryable(exc)
            ):
                raise
            retry_number += 1
            _before_retry(operation, policy, retry_number, exc, on_retry)
