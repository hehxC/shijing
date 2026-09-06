"""可替换后端的用户级限流、额度、并发和幂等控制。

当前试用版本使用进程内、线程安全的存储。业务入口只依赖 ``RateLimitService``
公共接口，正式部署时可提供 Redis 后端而不修改 HTTP 路由的限流语义。
"""

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
import math
import os
from threading import RLock
import time
from typing import Callable, Literal


@dataclass(frozen=True)
class RateLimitDecision:
    """一次限流判断的稳定返回结构。"""

    allowed: bool
    remaining: int
    retry_after: int
    reason: str


@dataclass(frozen=True)
class IdempotencyDecision:
    """幂等请求应执行、等待、重放还是拒绝。"""

    action: Literal["execute", "in_progress", "replay", "conflict"]
    response_body: str | None = None
    user_message_id: int | None = None
    quota_consumed: bool = False


@dataclass
class _IdempotencyRecord:
    fingerprint: str
    status: Literal["in_progress", "completed", "failed"]
    created_at: float
    response_body: str | None = None
    user_message_id: int | None = None
    quota_consumed: bool = False


class InMemoryRateLimitBackend:
    """单实例试用环境使用的线程安全限流存储。"""

    def __init__(self):
        self.lock = RLock()
        self.windows: dict[str, deque[float]] = {}
        self.concurrent: dict[str, int] = {}
        self.daily_counts: dict[tuple[str, str], int] = {}
        self.idempotency: dict[str, _IdempotencyRecord] = {}


class ConcurrencyLease:
    """持有一次并发名额；释放操作幂等，适合放在流式响应 ``finally`` 中。"""

    def __init__(self, backend: InMemoryRateLimitBackend, key: str):
        self._backend = backend
        self._key = key
        self._released = False

    def release(self) -> None:
        with self._backend.lock:
            if self._released:
                return
            current = self._backend.concurrent.get(self._key, 0)
            if current <= 1:
                self._backend.concurrent.pop(self._key, None)
            else:
                self._backend.concurrent[self._key] = current - 1
            self._released = True


class IdempotencyReservation:
    """一次幂等请求的所有权，用于完成、失败及记录是否已扣额度。"""

    def __init__(
        self,
        backend: InMemoryRateLimitBackend,
        key: str,
        record: _IdempotencyRecord,
    ):
        self._backend = backend
        self._key = key
        self._record = record

    @property
    def quota_consumed(self) -> bool:
        with self._backend.lock:
            return self._record.quota_consumed

    def mark_quota_consumed(self) -> None:
        with self._backend.lock:
            self._record.quota_consumed = True

    def complete(self, response_body: str, *, user_message_id: int) -> None:
        with self._backend.lock:
            self._record.status = "completed"
            self._record.response_body = response_body
            self._record.user_message_id = user_message_id

    def fail(self) -> None:
        with self._backend.lock:
            if self._record.status == "in_progress":
                self._record.status = "failed"


@dataclass(frozen=True)
class RateLimitSettings:
    """试用版本的可配置限制；非法配置安全回退到建议默认值。"""

    login_per_minute: int = 10
    register_per_hour: int = 5
    chat_per_minute: int = 10
    vision_per_minute: int = 3
    effect_concurrency: int = 1
    effect_daily: int = 10
    upload_per_minute: int = 10
    idempotency_ttl_seconds: int = 86_400

    @classmethod
    def from_environment(cls) -> "RateLimitSettings":
        def positive_int(name: str, default: int) -> int:
            try:
                return max(1, int(os.getenv(name, str(default))))
            except ValueError:
                return default

        return cls(
            login_per_minute=positive_int("LOGIN_RATE_LIMIT_PER_MINUTE", 10),
            register_per_hour=positive_int("REGISTER_RATE_LIMIT_PER_HOUR", 5),
            chat_per_minute=positive_int("CHAT_RATE_LIMIT_PER_MINUTE", 10),
            vision_per_minute=positive_int("VISION_RATE_LIMIT_PER_MINUTE", 3),
            effect_concurrency=positive_int("EFFECT_GENERATION_CONCURRENCY", 1),
            effect_daily=positive_int("EFFECT_GENERATION_DAILY_LIMIT", 10),
            upload_per_minute=positive_int("IMAGE_UPLOAD_RATE_LIMIT_PER_MINUTE", 10),
            idempotency_ttl_seconds=positive_int("IDEMPOTENCY_TTL_SECONDS", 86_400),
        )


class RateLimitService:
    """业务层统一使用的限流接口；存储实现可替换为 Redis。"""

    def __init__(
        self,
        backend: InMemoryRateLimitBackend | None = None,
        *,
        clock: Callable[[], float] = time.time,
    ):
        self._backend = backend or InMemoryRateLimitBackend()
        self._clock = clock

    def allow(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: int,
        reason: str,
    ) -> RateLimitDecision:
        """在滑动时间窗口内原子地消费一次请求额度。"""
        now = self._clock()
        limit = max(1, limit)
        window_seconds = max(1, window_seconds)
        cutoff = now - window_seconds

        with self._backend.lock:
            entries = self._backend.windows.setdefault(key, deque())
            while entries and entries[0] <= cutoff:
                entries.popleft()
            if len(entries) >= limit:
                retry_after = max(
                    1,
                    math.ceil(entries[0] + window_seconds - now),
                )
                return RateLimitDecision(False, 0, retry_after, reason)

            entries.append(now)
            return RateLimitDecision(
                True,
                limit - len(entries),
                0,
                reason,
            )

    def acquire_concurrency(
        self,
        key: str,
        *,
        limit: int,
        reason: str,
    ) -> tuple[RateLimitDecision, ConcurrencyLease | None]:
        """原子地取得一个并发名额，由调用方在任务结束时释放。"""
        limit = max(1, limit)
        with self._backend.lock:
            current = self._backend.concurrent.get(key, 0)
            if current >= limit:
                return RateLimitDecision(False, 0, 1, reason), None
            self._backend.concurrent[key] = current + 1
            decision = RateLimitDecision(True, limit - current - 1, 0, reason)
            return decision, ConcurrencyLease(self._backend, key)

    def consume_daily(
        self,
        key: str,
        *,
        limit: int,
        reason: str,
    ) -> RateLimitDecision:
        """按服务器本地自然日原子地消费一次用户额度。"""
        now_timestamp = self._clock()
        now = datetime.fromtimestamp(now_timestamp)
        day = now.date().isoformat()
        counter_key = (key, day)
        limit = max(1, limit)

        with self._backend.lock:
            count = self._backend.daily_counts.get(counter_key, 0)
            if count >= limit:
                next_day = datetime.combine(
                    now.date() + timedelta(days=1),
                    datetime.min.time(),
                )
                retry_after = max(
                    1,
                    math.ceil((next_day - now).total_seconds()),
                )
                return RateLimitDecision(False, 0, retry_after, reason)

            count += 1
            self._backend.daily_counts[counter_key] = count
            return RateLimitDecision(True, limit - count, 0, reason)

    def begin_idempotent(
        self,
        key: str,
        *,
        fingerprint: str,
        ttl_seconds: int = 86_400,
    ) -> tuple[IdempotencyDecision, IdempotencyReservation | None]:
        """原子地取得幂等请求所有权，或返回正在执行/可重放状态。"""
        now = self._clock()
        with self._backend.lock:
            record = self._backend.idempotency.get(key)
            if record and now - record.created_at >= max(1, ttl_seconds):
                self._backend.idempotency.pop(key, None)
                record = None

            if record is None:
                record = _IdempotencyRecord(
                    fingerprint=fingerprint,
                    status="in_progress",
                    created_at=now,
                )
                self._backend.idempotency[key] = record
                decision = IdempotencyDecision("execute")
                return decision, IdempotencyReservation(self._backend, key, record)

            if record.fingerprint != fingerprint:
                return IdempotencyDecision("conflict"), None
            if record.status == "in_progress":
                return IdempotencyDecision(
                    "in_progress",
                    quota_consumed=record.quota_consumed,
                ), None
            if record.status == "completed":
                return IdempotencyDecision(
                    "replay",
                    response_body=record.response_body,
                    user_message_id=record.user_message_id,
                    quota_consumed=record.quota_consumed,
                ), None

            # 失败的同一请求允许安全重试，并沿用已经消费的每日额度。
            record.status = "in_progress"
            record.created_at = now
            return (
                IdempotencyDecision(
                    "execute",
                    quota_consumed=record.quota_consumed,
                ),
                IdempotencyReservation(self._backend, key, record),
            )


# 应用进程共享同一后端，保证同实例内所有路由看到一致的额度与并发状态。
_RATE_LIMIT_SERVICE = RateLimitService()
_REDIS_SERVICE: "RedisRateLimitService | None" = None  # 惰性创建，复用连接池


def get_rate_limit_service():
    """按 ``RATE_LIMIT_BACKEND`` 返回限流服务（memory / redis）。

    ``memory``：单实例进程内实现；``redis``：多实例共享状态。
    Redis 客户端与限流服务均惰性创建、进程内单例，避免每请求新建连接。
    """
    backend = os.getenv("RATE_LIMIT_BACKEND", "memory").lower()
    if backend == "redis":
        global _REDIS_SERVICE
        if _REDIS_SERVICE is None:
            import redis

            from app.service.redis_rate_limit_service import RedisRateLimitService

            client = redis.Redis.from_url(
                os.getenv("REDIS_URL", "redis://localhost:6379/0"),
                decode_responses=True,
            )
            _REDIS_SERVICE = RedisRateLimitService(client)
        return _REDIS_SERVICE
    return _RATE_LIMIT_SERVICE
