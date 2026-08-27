"""任务五 HTTP 验收测试。"""

from contextlib import nullcontext
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes_auth import router as auth_router
from app.api.routes_chat import router as chat_router
from app.api.routes_design import design_session_id, router as design_router
from app.database import get_db
from app.service.auth_service import get_current_user
from app.service.rate_limit_service import RateLimitService, get_rate_limit_service


class RateLimitHttpTests(unittest.TestCase):
    """从真实 HTTP 路由验证模型调用前的限流与幂等行为。"""

    def setUp(self):
        self.user = SimpleNamespace(id=1)
        self.limiter = RateLimitService(clock=lambda: 1_000.0)
        app = FastAPI()
        app.include_router(chat_router)
        app.dependency_overrides[get_current_user] = lambda: self.user
        app.dependency_overrides[get_rate_limit_service] = lambda: self.limiter
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()

    @staticmethod
    def _effect_payload():
        return {
            "message": "增加一处休息区",
            "display_message": "生成新中式效果图",
            "message_type": "effect",
            "session_id": "session-1",
            "style_id": "new-chinese",
            "generate_effect_image": True,
        }

    def test_completed_idempotent_generation_is_replayed_without_new_model_call(self):
        def model_stream(*_args, **_kwargs):
            yield "效果图已生成"

        with (
            patch("app.api.routes_chat.stream_chat", side_effect=model_stream) as model,
            patch(
                "app.api.routes_chat.begin_user_turn",
                return_value=SimpleNamespace(id=42),
            ),
            patch("app.api.routes_chat.complete_turn"),
            patch("app.api.routes_chat.fail_turn"),
            patch("app.api.routes_chat.save_selected_style"),
            patch("app.api.routes_chat.observe_design_run", return_value=nullcontext()),
        ):
            first = self.client.post(
                "/chat",
                json=self._effect_payload(),
                headers={"Idempotency-Key": "generation-key-1"},
            )
            replay = self.client.post(
                "/chat",
                json=self._effect_payload(),
                headers={"Idempotency-Key": "generation-key-1"},
            )

        self.assertEqual(200, first.status_code)
        self.assertEqual("效果图已生成", first.text)
        self.assertEqual(200, replay.status_code)
        self.assertEqual("效果图已生成", replay.text)
        self.assertEqual("true", replay.headers["X-Idempotent-Replay"])
        self.assertEqual(1, model.call_count)

    def test_eleventh_generation_is_blocked_before_model_and_users_are_isolated(self):
        def model_stream(*_args, **_kwargs):
            yield "效果图已生成"

        with (
            patch.dict(os.environ, {"EFFECT_GENERATION_DAILY_LIMIT": "10"}),
            patch("app.api.routes_chat.stream_chat", side_effect=model_stream) as model,
            patch(
                "app.api.routes_chat.begin_user_turn",
                return_value=SimpleNamespace(id=42),
            ) as begin_turn,
            patch("app.api.routes_chat.complete_turn"),
            patch("app.api.routes_chat.fail_turn"),
            patch("app.api.routes_chat.save_selected_style"),
            patch("app.api.routes_chat.observe_design_run", return_value=nullcontext()),
        ):
            accepted = [
                self.client.post(
                    "/chat",
                    json=self._effect_payload(),
                    headers={"Idempotency-Key": f"user-1-generation-{index}"},
                )
                for index in range(10)
            ]
            blocked = self.client.post(
                "/chat",
                json=self._effect_payload(),
                headers={"Idempotency-Key": "user-1-generation-11"},
            )

            self.user.id = 2
            other_user = self.client.post(
                "/chat",
                json=self._effect_payload(),
                headers={"Idempotency-Key": "user-2-generation-1"},
            )

        self.assertTrue(all(response.status_code == 200 for response in accepted))
        self.assertEqual(429, blocked.status_code)
        self.assertGreater(int(blocked.headers["Retry-After"]), 0)
        self.assertEqual(
            "今日效果图生成额度已用完，请明天再试",
            blocked.json()["detail"]["reason"],
        )
        self.assertEqual(200, other_user.status_code)
        self.assertEqual(11, model.call_count)
        self.assertEqual(11, begin_turn.call_count)

    def test_generation_is_rejected_while_same_user_holds_concurrency_slot(self):
        decision, lease = self.limiter.acquire_concurrency(
            "effect-concurrency:user:1",
            limit=1,
            reason="已有一个效果图正在生成，请等待完成后再试",
        )
        self.assertTrue(decision.allowed)

        with (
            patch("app.api.routes_chat.stream_chat") as model,
            patch("app.api.routes_chat.begin_user_turn") as begin_turn,
            patch("app.api.routes_chat.save_selected_style"),
        ):
            response = self.client.post(
                "/chat",
                json=self._effect_payload(),
                headers={"Idempotency-Key": "concurrent-generation"},
            )

        lease.release()
        self.assertEqual(429, response.status_code)
        self.assertEqual("1", response.headers["Retry-After"])
        self.assertIn("正在生成", response.json()["detail"]["reason"])
        model.assert_not_called()
        begin_turn.assert_not_called()

    def test_failed_idempotent_request_can_retry_without_second_quota_charge(self):
        attempts = []

        def model_stream(*_args, **_kwargs):
            attempts.append("called")
            if len(attempts) == 1:
                raise TimeoutError("stream failed before completion")
            yield "重试成功"

        with (
            patch.dict(os.environ, {"EFFECT_GENERATION_DAILY_LIMIT": "1"}),
            patch("app.api.routes_chat.stream_chat", side_effect=model_stream),
            patch(
                "app.api.routes_chat.begin_user_turn",
                return_value=SimpleNamespace(id=42),
            ),
            patch("app.api.routes_chat.complete_turn"),
            patch("app.api.routes_chat.fail_turn"),
            patch("app.api.routes_chat.save_selected_style"),
            patch("app.api.routes_chat.observe_design_run", return_value=nullcontext()),
        ):
            with self.assertRaises(TimeoutError):
                self.client.post(
                    "/chat",
                    json=self._effect_payload(),
                    headers={"Idempotency-Key": "retry-same-generation"},
                )
            retry = self.client.post(
                "/chat",
                json=self._effect_payload(),
                headers={"Idempotency-Key": "retry-same-generation"},
            )
            new_request = self.client.post(
                "/chat",
                json=self._effect_payload(),
                headers={"Idempotency-Key": "different-generation"},
            )

        self.assertEqual(200, retry.status_code)
        self.assertEqual("重试成功", retry.text)
        self.assertEqual(429, new_request.status_code)
        self.assertEqual(2, len(attempts))

    def test_business_record_failure_releases_generation_slot(self):
        with (
            patch.dict(os.environ, {"EFFECT_GENERATION_DAILY_LIMIT": "1"}),
            patch(
                "app.api.routes_chat.begin_user_turn",
                side_effect=(RuntimeError("database unavailable"), SimpleNamespace(id=42)),
            ),
            patch("app.api.routes_chat.stream_chat", return_value=iter(("重试成功",))),
            patch("app.api.routes_chat.complete_turn"),
            patch("app.api.routes_chat.fail_turn"),
            patch("app.api.routes_chat.save_selected_style"),
            patch("app.api.routes_chat.observe_design_run", return_value=nullcontext()),
        ):
            with self.assertRaises(RuntimeError):
                self.client.post(
                    "/chat",
                    json=self._effect_payload(),
                    headers={"Idempotency-Key": "database-retry"},
                )
            retry = self.client.post(
                "/chat",
                json=self._effect_payload(),
                headers={"Idempotency-Key": "database-retry"},
            )

        self.assertEqual(200, retry.status_code)
        self.assertEqual("重试成功", retry.text)

    def test_chat_and_vision_limits_reject_before_business_record_creation(self):
        def model_stream(*_args, **_kwargs):
            yield "回答"

        with (
            patch.dict(
                os.environ,
                {
                    "CHAT_RATE_LIMIT_PER_MINUTE": "1",
                    "VISION_RATE_LIMIT_PER_MINUTE": "1",
                },
            ),
            patch("app.api.routes_chat.stream_chat", side_effect=model_stream),
            patch(
                "app.api.routes_chat.begin_user_turn",
                return_value=SimpleNamespace(id=42),
            ) as begin_turn,
            patch("app.api.routes_chat.complete_turn"),
            patch("app.api.routes_chat.fail_turn"),
            patch("app.api.routes_chat.observe_design_run", return_value=nullcontext()),
        ):
            chat_payload = {
                "message": "你好",
                "message_type": "chat",
                "session_id": "session-chat",
            }
            vision_payload = {
                "message": "分析图片",
                "message_type": "recognize",
                "session_id": "session-vision",
            }
            first_chat = self.client.post("/chat", json=chat_payload)
            blocked_chat = self.client.post("/chat", json=chat_payload)
            first_vision = self.client.post("/chat", json=vision_payload)
            blocked_vision = self.client.post("/chat", json=vision_payload)

        self.assertEqual(200, first_chat.status_code)
        self.assertEqual(429, blocked_chat.status_code)
        self.assertEqual(200, first_vision.status_code)
        self.assertEqual(429, blocked_vision.status_code)
        self.assertEqual(2, begin_turn.call_count)


class _LoginDatabase:
    """认证接口测试使用的最小数据库边界。"""

    def __init__(self):
        self.scalar_calls = 0

    def scalar(self, _query):
        self.scalar_calls += 1
        return None


class AuthRateLimitHttpTests(unittest.TestCase):
    def setUp(self):
        self.limiter = RateLimitService(clock=lambda: 1_000.0)
        self.database = _LoginDatabase()
        app = FastAPI()
        app.include_router(auth_router)
        app.dependency_overrides[get_rate_limit_service] = lambda: self.limiter
        app.dependency_overrides[get_db] = lambda: self.database
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()

    def test_login_limit_counts_failed_attempts_and_blocks_before_database(self):
        with patch.dict(os.environ, {"LOGIN_RATE_LIMIT_PER_MINUTE": "1"}):
            first = self.client.post(
                "/api/auth/login",
                json={"username": "tester", "password": "secret1"},
            )
            blocked = self.client.post(
                "/api/auth/login",
                json={"username": "tester", "password": "secret1"},
            )

        self.assertEqual(401, first.status_code)
        self.assertEqual(429, blocked.status_code)
        self.assertEqual(1, self.database.scalar_calls)

    def test_registration_limit_blocks_before_database(self):
        with patch.dict(os.environ, {"REGISTER_RATE_LIMIT_PER_HOUR": "1"}):
            self.limiter.allow(
                "register:ip:testclient",
                limit=1,
                window_seconds=3_600,
                reason="注册请求过于频繁，请稍后再试",
            )
            blocked = self.client.post(
                "/api/auth/register",
                json={"username": "tester", "password": "secret1"},
            )

        self.assertEqual(429, blocked.status_code)
        self.assertEqual(0, self.database.scalar_calls)


class UploadRateLimitHttpTests(unittest.TestCase):
    def setUp(self):
        self.user = SimpleNamespace(id=1)
        self.limiter = RateLimitService(clock=lambda: 1_000.0)
        app = FastAPI()
        app.include_router(design_router)
        app.dependency_overrides[get_current_user] = lambda: self.user
        app.dependency_overrides[design_session_id] = lambda: "user:1:test-session"
        app.dependency_overrides[get_rate_limit_service] = lambda: self.limiter
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()

    def test_space_and_material_uploads_share_user_upload_limit(self):
        payload = {
            "image": "data:image/png;base64,aW1hZ2U=",
            "original_name": "garden.png",
        }
        with (
            patch.dict(os.environ, {"IMAGE_UPLOAD_RATE_LIMIT_PER_MINUTE": "1"}),
            patch(
                "app.api.routes_design.save_space_image",
                return_value={"id": 1},
            ) as save_space,
            patch("app.api.routes_design.add_material_reference") as save_material,
        ):
            first = self.client.put("/api/design/space-image", json=payload)
            blocked = self.client.post(
                "/api/design/material-images",
                json={**payload, "name": None, "usages": []},
            )

        self.assertEqual(200, first.status_code)
        self.assertEqual(429, blocked.status_code)
        save_space.assert_called_once()
        save_material.assert_not_called()


if __name__ == "__main__":
    unittest.main()
