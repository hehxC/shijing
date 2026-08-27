"""统一 AI 稳定性策略的错误场景测试。"""

import os
import unittest
from unittest.mock import patch
from urllib.error import URLError

from app.models.ai_call_record import AiOperation
from app.service.ai_resilience import (
    iterate_with_resilience,
    policy_for,
    run_with_resilience,
)


class _ProviderError(Exception):
    """模拟携带 HTTP 状态码的供应商 SDK 异常。"""

    def __init__(self, status_code: int):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class AiResilienceTests(unittest.TestCase):
    """通过共享执行接口验证所有 AI operation 的稳定性契约。"""

    def setUp(self):
        self._environment_patch = patch.dict(
            os.environ,
            {"AI_RETRY_BACKOFF_SECONDS": "0"},
        )
        self._environment_patch.start()

    def tearDown(self):
        self._environment_patch.stop()

    def test_every_operation_has_the_expected_production_policy(self):
        expected = {
            AiOperation.INTENT_ROUTING: (10, 1),
            AiOperation.TEXT_CHAT: (60, 1),
            AiOperation.SQL_QUERY: (15, 1),
            AiOperation.RAG_RETRIEVAL: (5, 0),
            AiOperation.RAG_EMBEDDING: (5, 0),
            AiOperation.VISION_ANALYSIS: (90, 1),
            AiOperation.IMAGE_GENERATION: (240, 1),
        }

        for operation, (timeout_seconds, max_retries) in expected.items():
            with self.subTest(operation=operation):
                policy = policy_for(operation)
                self.assertEqual(timeout_seconds, policy.timeout_seconds)
                self.assertEqual(max_retries, policy.max_retries)

    def test_temporary_provider_failures_retry_once_then_succeed(self):
        retryable_errors = (
            TimeoutError("timed out"),
            ConnectionError("connection reset"),
            URLError("network unavailable"),
            _ProviderError(429),
            _ProviderError(502),
            _ProviderError(503),
            _ProviderError(504),
        )

        for first_error in retryable_errors:
            with self.subTest(error=first_error):
                outcomes = iter((first_error, "success"))
                attempts = []

                def provider_call():
                    attempts.append("called")
                    outcome = next(outcomes)
                    if isinstance(outcome, BaseException):
                        raise outcome
                    return outcome

                result = run_with_resilience(AiOperation.TEXT_CHAT, provider_call)

                self.assertEqual("success", result)
                self.assertEqual(2, len(attempts))

    def test_permanent_provider_failures_are_not_retried(self):
        for error in (
            _ProviderError(400),
            _ProviderError(401),
            _ProviderError(403),
            ValueError("invalid request"),
        ):
            with self.subTest(error=error):
                attempts = []

                def provider_call():
                    attempts.append("called")
                    raise error

                with self.assertRaises(type(error)):
                    run_with_resilience(AiOperation.TEXT_CHAT, provider_call)

                self.assertEqual(1, len(attempts))

    def test_retry_limit_and_callback_report_the_actual_retry(self):
        attempts = []
        recorded_retries = []

        def provider_call():
            attempts.append("called")
            raise TimeoutError("still unavailable")

        with self.assertRaises(TimeoutError):
            run_with_resilience(
                AiOperation.IMAGE_GENERATION,
                provider_call,
                on_retry=recorded_retries.append,
            )

        self.assertEqual(2, len(attempts))
        self.assertEqual([1], recorded_retries)

    def test_wrapped_temporary_failure_is_retryable(self):
        attempts = []

        def provider_call():
            attempts.append("called")
            if len(attempts) == 1:
                try:
                    raise TimeoutError("socket timed out")
                except TimeoutError as cause:
                    raise RuntimeError("provider request failed") from cause
            return "success"

        result = run_with_resilience(AiOperation.VISION_ANALYSIS, provider_call)

        self.assertEqual("success", result)
        self.assertEqual(2, len(attempts))

    def test_rag_retrieval_does_not_retry(self):
        attempts = []

        def retrieval_call():
            attempts.append("called")
            raise TimeoutError("vector store timed out")

        with self.assertRaises(TimeoutError):
            run_with_resilience(AiOperation.RAG_RETRIEVAL, retrieval_call)

        self.assertEqual(1, len(attempts))

    def test_stream_retries_when_failure_happens_before_first_chunk(self):
        attempts = []

        def stream_factory():
            attempts.append("called")
            if len(attempts) == 1:
                raise TimeoutError("stream connection timed out")
            return iter(("first", "second"))

        chunks = list(
            iterate_with_resilience(AiOperation.TEXT_CHAT, stream_factory)
        )

        self.assertEqual(["first", "second"], chunks)
        self.assertEqual(2, len(attempts))

    def test_stream_does_not_restart_after_first_chunk_was_emitted(self):
        attempts = []

        def stream_factory():
            attempts.append("called")

            def stream():
                yield "already-visible"
                raise TimeoutError("stream disconnected")

            return stream()

        stream = iterate_with_resilience(AiOperation.TEXT_CHAT, stream_factory)
        self.assertEqual("already-visible", next(stream))
        with self.assertRaises(TimeoutError):
            next(stream)
        self.assertEqual(1, len(attempts))

    def test_invalid_environment_values_fall_back_to_safe_defaults(self):
        with patch.dict(
            os.environ,
            {
                "AI_TEXT_CHAT_TIMEOUT_SECONDS": "not-a-number",
                "AI_TEXT_CHAT_MAX_RETRIES": "invalid",
                "AI_RETRY_BACKOFF_SECONDS": "invalid",
            },
        ):
            policy = policy_for(AiOperation.TEXT_CHAT)

        self.assertEqual(60, policy.timeout_seconds)
        self.assertEqual(1, policy.max_retries)
        self.assertEqual(0.5, policy.backoff_seconds)


if __name__ == "__main__":
    unittest.main()
