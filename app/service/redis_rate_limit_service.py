"""基于 Redis 的限流、额度、并发与幂等实现，支持多实例共享状态。

保持与 ``InMemoryRateLimitService`` 相同的公共方法（allow / consume_daily /
acquire_concurrency / begin_idempotent），通过原子 Lua 脚本保证并发正确性，
从而在多实例部署下共享同一份限流/额度/幂等状态。
"""

import json
import math
import time
from typing import Any
from uuid import uuid4

from app.service.rate_limit_service import (
    IdempotencyDecision,
    RateLimitDecision,
)


# 滑动窗口限流：清理过期成员 -> 统计 -> 判定/入队
# 用 ZRANGE(取成员) + ZSCORE(取分数)，避免不同 Redis 客户端对 WITHSCORES 返回结构不一致。
_SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)
if count >= limit then
    local oldest_member = redis.call('ZRANGE', key, 0, 0)[1]
    local oldest = tonumber(redis.call('ZSCORE', key, oldest_member))
    local retry_after = math.ceil(oldest + window - now)
    return {0, retry_after}
end
redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, window)
return {1, limit - count - 1}
"""

# 当日额度：递增计数，首次设置过期时间（到第二天零点）
_DAILY_QUOTA_LUA = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])
local val = redis.call('INCR', key)
if val == 1 then
    redis.call('EXPIRE', key, ttl)
end
if val > limit then
    return {0, ttl}
end
return {1, limit - val}
"""

# 并发租约：SET NX + token（原子获取）
_CONCURRENCY_LUA = """
local key = KEYS[1]
local token = ARGV[1]
local ttl = tonumber(ARGV[2])
local ok = redis.call('SET', key, token, 'NX', 'EX', ttl)
if ok then
    return 1
end
return 0
"""

# 释放租约：仅当 token 匹配才删除
_RELEASE_LUA = """
local key = KEYS[1]
local token = ARGV[1]
if redis.call('GET', key) == token then
    redis.call('DEL', key)
    return 1
end
return 0
"""

# 幂等开始：SET NX 写入 in_progress，并回传判定
_IDEMPOTENT_BEGIN_LUA = """
local key = KEYS[1]
local fingerprint = ARGV[1]
local record_json = ARGV[2]
local ttl = tonumber(ARGV[3])
local exists = redis.call('EXISTS', key)
if exists == 0 then
    redis.call('SET', key, record_json, 'EX', ttl)
    return {'execute', 0}
end
local record = cjson.decode(redis.call('GET', key))
if record.fingerprint ~= fingerprint then
    return {'conflict', 0}
end
if record.status == 'in_progress' then
    return {'in_progress', record.quota_consumed and 1 or 0}
end
if record.status == 'completed' then
    return {'replay', record.quota_consumed and 1 or 0, record.response_body or '', record.user_message_id or ''}
end
-- failed：允许原请求安全重试
redis.call('SET', key, record_json, 'EX', ttl)
return {'execute', record.quota_consumed and 1 or 0}
"""

# 幂等更新（complete / fail / mark_quota_consumed），仅当 token 匹配
_IDEMPOTENT_UPDATE_LUA = """
local key = KEYS[1]
local token = ARGV[1]
local status = ARGV[2]
local response_body = ARGV[3]
local user_message_id = ARGV[4]
local quota_consumed = ARGV[5]
local raw = redis.call('GET', key)
if not raw then
    return 0
end
local record = cjson.decode(raw)
if record.token ~= token then
    return 0
end
if status ~= '' then record.status = status end
if response_body ~= '' then record.response_body = response_body end
if user_message_id ~= '' then record.user_message_id = tonumber(user_message_id) end
if quota_consumed ~= '' then record.quota_consumed = (quota_consumed == '1') end
record.updated_at = tonumber(ARGV[6])
redis.call('SET', key, cjson.encode(record))
return 1
"""


def _as_str(value) -> str | None:
    """把 redis 返回的 bytes 或 str 归一化为 str。"""
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _seconds_to_midnight(now: float) -> int:
    """返回从 now 到服务器本地时间下一个零点还剩多少秒。"""
    import datetime

    day_start = datetime.datetime.fromtimestamp(now).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    next_midnight = day_start + datetime.timedelta(days=1)
    return max(1, int(next_midnight.timestamp() - now))


class RedisRateLimitService:
    """Redis 后端实现；接口与内存版 ``RateLimitService`` 一致。"""

    def __init__(self, client, *, clock: Any = time.time):
        self.redis = client
        self._clock = clock

    # ------------------------------------------------------------------ #
    # 接口：限流
    # ------------------------------------------------------------------ #
    def allow(
        self,
        key: str,
        *,
        limit: int,
        window_seconds: int,
        reason: str,
    ) -> RateLimitDecision:
        now = self._clock()
        member = f"{now}-{uuid4().hex}"
        allowed, value = self.redis.eval(
            _SLIDING_WINDOW_LUA,
            1,
            key,
            now,
            max(1, window_seconds),
            max(1, limit),
            member,
        )
        if not bool(int(allowed)):
            return RateLimitDecision(False, 0, int(value), reason)
        return RateLimitDecision(True, int(value), 0, reason)

    # ------------------------------------------------------------------ #
    # 接口：当日额度
    # ------------------------------------------------------------------ #
    def consume_daily(
        self,
        key: str,
        *,
        limit: int,
        reason: str,
    ) -> RateLimitDecision:
        now = self._clock()
        import datetime

        day = datetime.datetime.fromtimestamp(now).date().isoformat()
        redis_key = f"quota:{key}:{day}"
        ttl = _seconds_to_midnight(now)
        allowed, value = self.redis.eval(
            _DAILY_QUOTA_LUA,
            1,
            redis_key,
            max(1, limit),
            ttl,
        )
        if not bool(int(allowed)):
            return RateLimitDecision(False, 0, ttl, reason)
        return RateLimitDecision(True, int(value), 0, reason)

    # ------------------------------------------------------------------ #
    # 接口：并发租约
    # ------------------------------------------------------------------ #
    def acquire_concurrency(
        self,
        key: str,
        *,
        limit: int,
        reason: str,
    ) -> tuple[RateLimitDecision, "RedisConcurrencyLease | None"]:
        # 用 token 区分请求；Redis 原子获得（limit 当前实现为 1）
        token = uuid4().hex
        acquired = self.redis.eval(
            _CONCURRENCY_LUA, 1, f"conc:{key}", token, 60
        )
        limit = max(1, limit)
        if not bool(int(acquired)):
            return RateLimitDecision(False, 0, 1, reason), None
        return (
            RateLimitDecision(True, limit - 1, 0, reason),
            RedisConcurrencyLease(self.redis, f"conc:{key}", token),
        )

    # ------------------------------------------------------------------ #
    # 接口：幂等
    # ------------------------------------------------------------------ #
    def begin_idempotent(
        self,
        key: str,
        *,
        fingerprint: str,
        ttl_seconds: int = 86_400,
    ) -> tuple[IdempotencyDecision, "RedisIdempotencyReservation | None"]:
        token = uuid4().hex
        record = {
            "token": token,
            "fingerprint": fingerprint,
            "status": "in_progress",
            "created_at": self._clock(),
            "response_body": None,
            "user_message_id": None,
            "quota_consumed": False,
        }
        result = self.redis.eval(
            _IDEMPOTENT_BEGIN_LUA,
            1,
            f"idem:{key}",
            fingerprint,
            json.dumps(record),
            max(1, ttl_seconds),
        )
        action = _as_str(result[0])
        quota_consumed = bool(int(result[1]) if len(result) > 1 else 0)

        if action == "execute":
            return (
                IdempotencyDecision("execute", quota_consumed=quota_consumed),
                RedisIdempotencyReservation(self.redis, f"idem:{key}", token),
            )
        if action == "in_progress":
            return (
                IdempotencyDecision("in_progress", quota_consumed=quota_consumed),
                None,
            )
        if action == "replay":
            body = _as_str(result[2]) if len(result) > 2 and result[2] else None
            message_id = result[3] if len(result) > 3 and result[3] else None
            return (
                IdempotencyDecision(
                    "replay",
                    response_body=body,
                    user_message_id=int(message_id) if message_id else None,
                    quota_consumed=quota_consumed,
                ),
                None,
            )
        return IdempotencyDecision("conflict"), None


class RedisConcurrencyLease:
    """持有一次 Redis 并发租约；释放操作幂等。"""

    def __init__(self, redis, key: str, token: str):
        self._redis = redis
        self._key = key
        self._token = token
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._redis.eval(_RELEASE_LUA, 1, self._key, self._token)
        self._released = True


class RedisIdempotencyReservation:
    """幂等请求的所有权，用于完成、失败及记录是否已扣额度。"""

    _STATUS_SKIP = ""  # 表示不修改该字段

    def __init__(self, redis, key: str, token: str):
        self._redis = redis
        self._key = key
        self._token = token

    @property
    def quota_consumed(self) -> bool:
        raw = self._redis.get(self._key)
        if not raw:
            return False
        record = json.loads(raw)
        return bool(record.get("quota_consumed", False))

    def mark_quota_consumed(self) -> None:
        self._update(status="", response_body="", user_message_id="", quota_consumed="1")

    def complete(self, response_body: str, *, user_message_id: int) -> None:
        self._update(
            status="completed",
            response_body=response_body,
            user_message_id=str(user_message_id),
            quota_consumed="",
        )

    def fail(self) -> None:
        if self.quota_consumed:
            # 已完成消费的失败请求才需要标记 failed，便于同键重试
            self._update(status="failed", response_body="", user_message_id="", quota_consumed="")

    def _update(self, *, status, response_body, user_message_id, quota_consumed) -> None:
        self._redis.eval(
            _IDEMPOTENT_UPDATE_LUA,
            1,
            self._key,
            self._token,
            status,
            response_body,
            user_message_id,
            quota_consumed,
            self._clock_now(),
        )

    def _clock_now(self) -> float:
        return time.time()
