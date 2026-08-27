"""用户级限流、额度、并发与幂等服务测试。"""

from datetime import datetime, timedelta
import unittest

from app.service.rate_limit_service import RateLimitService


class RateLimitServiceTests(unittest.TestCase):
    """通过公共服务接口验证限流决策，不依赖 HTTP 或真实时间。"""

    def test_window_limit_returns_complete_decision_and_recovers(self):
        now = [1_000.0]
        limiter = RateLimitService(clock=lambda: now[0])

        first = limiter.allow(
            "chat:user:1",
            limit=2,
            window_seconds=60,
            reason="普通聊天请求过于频繁",
        )
        second = limiter.allow(
            "chat:user:1",
            limit=2,
            window_seconds=60,
            reason="普通聊天请求过于频繁",
        )
        blocked = limiter.allow(
            "chat:user:1",
            limit=2,
            window_seconds=60,
            reason="普通聊天请求过于频繁",
        )

        self.assertTrue(first.allowed)
        self.assertEqual(1, first.remaining)
        self.assertTrue(second.allowed)
        self.assertEqual(0, second.remaining)
        self.assertFalse(blocked.allowed)
        self.assertEqual(0, blocked.remaining)
        self.assertEqual(60, blocked.retry_after)
        self.assertEqual("普通聊天请求过于频繁", blocked.reason)

        now[0] += 60
        recovered = limiter.allow(
            "chat:user:1",
            limit=2,
            window_seconds=60,
            reason="普通聊天请求过于频繁",
        )
        self.assertTrue(recovered.allowed)

    def test_concurrency_lease_blocks_duplicate_generation_until_release(self):
        limiter = RateLimitService(clock=lambda: 1_000.0)

        first, lease = limiter.acquire_concurrency(
            "effect:user:1",
            limit=1,
            reason="已有一个效果图正在生成",
        )
        blocked, blocked_lease = limiter.acquire_concurrency(
            "effect:user:1",
            limit=1,
            reason="已有一个效果图正在生成",
        )

        self.assertTrue(first.allowed)
        self.assertIsNotNone(lease)
        self.assertFalse(blocked.allowed)
        self.assertEqual(1, blocked.retry_after)
        self.assertIsNone(blocked_lease)

        lease.release()
        recovered, recovered_lease = limiter.acquire_concurrency(
            "effect:user:1",
            limit=1,
            reason="已有一个效果图正在生成",
        )
        self.assertTrue(recovered.allowed)
        recovered_lease.release()

    def test_daily_generation_quota_is_isolated_by_user_and_resets_next_day(self):
        now = [datetime(2026, 8, 27, 10, 0, 0).timestamp()]
        limiter = RateLimitService(clock=lambda: now[0])

        for _ in range(2):
            allowed = limiter.consume_daily(
                "effect:user:1",
                limit=2,
                reason="今日效果图生成额度已用完",
            )
            self.assertTrue(allowed.allowed)

        blocked = limiter.consume_daily(
            "effect:user:1",
            limit=2,
            reason="今日效果图生成额度已用完",
        )
        other_user = limiter.consume_daily(
            "effect:user:2",
            limit=2,
            reason="今日效果图生成额度已用完",
        )

        self.assertFalse(blocked.allowed)
        self.assertGreater(blocked.retry_after, 0)
        self.assertTrue(other_user.allowed)

        now[0] = (datetime.fromtimestamp(now[0]) + timedelta(days=1)).timestamp()
        next_day = limiter.consume_daily(
            "effect:user:1",
            limit=2,
            reason="今日效果图生成额度已用完",
        )
        self.assertTrue(next_day.allowed)

    def test_idempotency_key_blocks_in_flight_duplicate_and_replays_completion(self):
        limiter = RateLimitService(clock=lambda: 1_000.0)

        first, reservation = limiter.begin_idempotent(
            "effect:user:1:key-1",
            fingerprint="same-request",
        )
        in_flight, duplicate_reservation = limiter.begin_idempotent(
            "effect:user:1:key-1",
            fingerprint="same-request",
        )

        self.assertEqual("execute", first.action)
        self.assertIsNotNone(reservation)
        self.assertEqual("in_progress", in_flight.action)
        self.assertIsNone(duplicate_reservation)

        reservation.complete("效果图响应", user_message_id=42)
        replay, replay_reservation = limiter.begin_idempotent(
            "effect:user:1:key-1",
            fingerprint="same-request",
        )
        conflict, _ = limiter.begin_idempotent(
            "effect:user:1:key-1",
            fingerprint="different-request",
        )

        self.assertEqual("replay", replay.action)
        self.assertEqual("效果图响应", replay.response_body)
        self.assertEqual(42, replay.user_message_id)
        self.assertIsNone(replay_reservation)
        self.assertEqual("conflict", conflict.action)


if __name__ == "__main__":
    unittest.main()
