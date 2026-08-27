"""意图路由错误场景测试。

所有供应商行为均由内存假模型注入，测试不会读取真实 API Key，也不会发出网络请求。
"""

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage

from app.service import chat_intent_router


class _FakeRouterModel:
    """按顺序返回结果或抛出异常，用于模拟供应商故障。"""

    def __init__(self, *outcomes: str | BaseException):
        self._outcomes = iter(outcomes)
        self.call_count = 0

    def invoke(self, _messages):
        self.call_count += 1
        outcome = next(self._outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return AIMessage(content=outcome)


class _AuthenticationError(Exception):
    """模拟供应商返回的不可重试 401 认证错误。"""

    status_code = 401


class RouterErrorScenarioTests(unittest.TestCase):
    """从公开路由入口验证重试、降级和决策记录。"""

    def setUp(self):
        self._temporary_directory = TemporaryDirectory()
        self._decision_log_path = Path(self._temporary_directory.name) / "router.jsonl"
        self._path_patch = patch.object(
            chat_intent_router,
            "ROUTER_DECISION_LOG_PATH",
            self._decision_log_path,
        )
        self._environment_patch = patch.dict(
            os.environ,
            {
                "AI_INTENT_ROUTING_MAX_RETRIES": "1",
                # 错误场景测试不需要等待真实的指数退避时间。
                "AI_RETRY_BACKOFF_SECONDS": "0",
            },
        )
        self._path_patch.start()
        self._environment_patch.start()

    def tearDown(self):
        self._environment_patch.stop()
        self._path_patch.stop()
        self._temporary_directory.cleanup()

    def _route(self, message: str):
        return chat_intent_router.route_chat_intent(
            message,
            has_uploaded_image=False,
            has_reference_image=False,
            has_generated_image=False,
        )

    def _last_decision(self) -> dict:
        lines = self._decision_log_path.read_text(encoding="utf-8").splitlines()
        return json.loads(lines[-1])

    def test_timeout_once_then_model_success(self):
        model = _FakeRouterModel(
            TimeoutError("temporary timeout"),
            json.dumps(
                {
                    "intent": "general_chat",
                    "use_image": "none",
                    "confidence": 0.91,
                    "reason": "model succeeded after retry",
                }
            ),
        )

        with patch.object(chat_intent_router, "_get_router_model", return_value=model):
            result = self._route("你好，帮我介绍一下庭院设计")

        self.assertEqual(2, model.call_count)
        self.assertEqual("general_chat", result.intent)
        self.assertEqual("model succeeded after retry", result.reason)
        self.assertEqual("llm", self._last_decision()["source"])

    def test_repeated_timeout_falls_back_to_rule_result(self):
        model = _FakeRouterModel(
            TimeoutError("first timeout"),
            TimeoutError("second timeout"),
        )

        with patch.object(chat_intent_router, "_get_router_model", return_value=model):
            with self.assertLogs(chat_intent_router.logger, level="INFO") as logs:
                result = self._route("50平米院子铺莱姆石大概多少钱")

        self.assertEqual(2, model.call_count)
        self.assertEqual("estimate_price", result.intent)
        self.assertEqual("none", result.use_image)
        self.assertEqual("rule_fallback", self._last_decision()["source"])
        self.assertTrue(any("ai_call_degraded" in line for line in logs.output))

    def test_authentication_error_does_not_retry_and_falls_back(self):
        model = _FakeRouterModel(_AuthenticationError("HTTP 401 invalid API key"))

        with patch.object(chat_intent_router, "_get_router_model", return_value=model):
            result = self._route("莱姆石有哪些规格")

        self.assertEqual(1, model.call_count)
        self.assertEqual("query_material", result.intent)
        self.assertEqual("rule_fallback", self._last_decision()["source"])

    def test_invalid_model_output_uses_normalization_fallback_without_retry(self):
        model = _FakeRouterModel("this is not valid JSON")

        with patch.object(chat_intent_router, "_get_router_model", return_value=model):
            result = self._route("莱姆石有哪些规格")

        self.assertEqual(1, model.call_count)
        self.assertEqual("query_material", result.intent)
        self.assertEqual("llm_normalize_fallback", self._last_decision()["source"])


if __name__ == "__main__":
    unittest.main()
