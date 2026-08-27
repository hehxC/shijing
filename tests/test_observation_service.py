from contextvars import Context
from decimal import Decimal
import os
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.ai_call_record import (
    AiCallRecord,
    AiCallStatus,
    AiErrorType,
    AiOperation,
)
from app.models.design_run import DesignRun, DesignRunOperation, DesignRunStatus
from app.models.user import User
from app.service import observation_service
from app.service.ai_resilience import run_with_resilience
from app.service.observation_service import observe_ai_call, observe_design_run


class _UsageMessage:
    """模拟 LangChain 消息，只暴露统一观测模块依赖的 Token 元数据。"""

    usage_metadata = {"input_tokens": 1_000, "output_tokens": 500}
    response_metadata = {}


class ObservationServiceTests(unittest.TestCase):
    """在隔离数据库中验证观测写入，不调用真实模型或生产数据库。"""

    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        User.__table__.create(self.engine)
        DesignRun.__table__.create(self.engine)
        AiCallRecord.__table__.create(self.engine)
        self.Session = sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
        )
        with self.Session.begin() as db:
            db.add(User(id=1, username="observer", password_hash="hash"))

        # 让被测模块写入隔离数据库，并避免不同测试共享价格配置缓存。
        self.session_patch = patch.object(
            observation_service,
            "SessionLocal",
            self.Session,
        )
        self.session_patch.start()
        self.pricing_patch = patch.dict(
            os.environ,
            {
                "MODEL_PRICING_JSON": (
                    '{"image-model":{"input_per_million":"2",'
                    '"output_per_million":"4"}}'
                )
            },
        )
        self.pricing_patch.start()
        observation_service._model_pricing.cache_clear()

    def tearDown(self):
        observation_service._model_pricing.cache_clear()
        self.pricing_patch.stop()
        self.session_patch.stop()
        self.engine.dispose()

    def test_successful_run_aggregates_ai_cost_and_generated_images(self):
        with observe_design_run(
            request_id="request-success",
            user_id=1,
            session_id="user:1:session",
            operation=DesignRunOperation.EFFECT_GENERATION,
        ):
            with observe_ai_call(
                AiOperation.IMAGE_GENERATION,
                provider="google",
                model="image-model",
            ) as call:
                call.capture_usage(_UsageMessage())

        with self.Session() as db:
            saved_run = db.scalar(select(DesignRun))
            saved_call = db.scalar(select(AiCallRecord))

        self.assertEqual(DesignRunStatus.SUCCEEDED, saved_run.status)
        self.assertEqual(1, saved_run.generated_image_count)
        self.assertEqual(Decimal("0.004000"), saved_run.total_estimated_cost)
        self.assertEqual(AiCallStatus.SUCCEEDED, saved_call.status)
        self.assertEqual(1_000, saved_call.input_tokens)
        self.assertEqual(500, saved_call.output_tokens)
        self.assertEqual(Decimal("0.004000"), saved_call.estimated_cost)

    def test_caught_critical_ai_failure_marks_whole_run_failed(self):
        with observe_design_run(
            request_id="request-critical-failure",
            user_id=1,
            session_id="user:1:session",
            operation=DesignRunOperation.VISION_ANALYSIS,
        ):
            try:
                with observe_ai_call(
                    AiOperation.VISION_ANALYSIS,
                    provider="dashscope",
                    model="vision-model",
                ):
                    raise TimeoutError(
                        "Bearer top-secret data:image/png;base64,QUJD"
                    )
            except TimeoutError:
                # 业务层可能将模型错误转换为 SSE 错误事件；任务仍应保留失败事实。
                pass

        with self.Session() as db:
            saved_run = db.scalar(select(DesignRun))
            saved_call = db.scalar(select(AiCallRecord))

        self.assertEqual(DesignRunStatus.FAILED, saved_run.status)
        self.assertEqual(AiOperation.VISION_ANALYSIS.value, saved_run.failure_stage)
        self.assertEqual(AiCallStatus.FAILED, saved_call.status)
        self.assertEqual(AiErrorType.TIMEOUT, saved_call.error_type)
        self.assertNotIn("top-secret", saved_call.error_message)
        self.assertNotIn("QUJD", saved_call.error_message)
        self.assertIn("[redacted]", saved_call.error_message)
        self.assertIn("[image-data-redacted]", saved_call.error_message)

    def test_noncritical_fallback_failure_does_not_fail_design_run(self):
        with observe_design_run(
            request_id="request-fallback",
            user_id=1,
            session_id="user:1:session",
            operation=DesignRunOperation.CHAT,
        ):
            try:
                with observe_ai_call(
                    AiOperation.INTENT_ROUTING,
                    provider="deepseek",
                    model="router-model",
                    critical=False,
                ):
                    raise RuntimeError("503 provider unavailable")
            except RuntimeError:
                pass

        with self.Session() as db:
            saved_run = db.scalar(select(DesignRun))
            saved_call = db.scalar(select(AiCallRecord))

        self.assertEqual(DesignRunStatus.SUCCEEDED, saved_run.status)
        self.assertEqual(AiCallStatus.FAILED, saved_call.status)
        self.assertEqual(AiErrorType.PROVIDER_UNAVAILABLE, saved_call.error_type)

    def test_actual_retry_count_is_persisted_with_the_ai_call(self):
        """统一重试回调应把真实次数写入调用明细，而不是保留默认值。"""
        attempts = []

        def provider_call():
            attempts.append("called")
            if len(attempts) == 1:
                raise TimeoutError("temporary provider timeout")
            return _UsageMessage()

        with patch.dict(os.environ, {"AI_RETRY_BACKOFF_SECONDS": "0"}):
            with observe_design_run(
                request_id="request-retried",
                user_id=1,
                session_id="user:1:session",
                operation=DesignRunOperation.CHAT,
            ):
                with observe_ai_call(
                    AiOperation.TEXT_CHAT,
                    provider="deepseek",
                    model="chat-model",
                ) as call:
                    response = run_with_resilience(
                        AiOperation.TEXT_CHAT,
                        provider_call,
                        on_retry=call.record_retry,
                    )
                    call.capture_usage(response)

        with self.Session() as db:
            saved_call = db.scalar(select(AiCallRecord))

        self.assertEqual(2, len(attempts))
        self.assertEqual(AiCallStatus.SUCCEEDED, saved_call.status)
        self.assertEqual(1, saved_call.retry_count)

    def test_ai_call_without_design_context_is_not_persisted(self):
        with observe_ai_call(
            AiOperation.TEXT_CHAT,
            provider="deepseek",
            model="chat-model",
        ) as call:
            self.assertFalse(call.enabled)
            call.capture_usage(_UsageMessage())

        with self.Session() as db:
            self.assertEqual([], list(db.scalars(select(AiCallRecord))))

    def test_design_run_can_finish_when_stream_resumes_in_another_context(self):
        """Starlette 会在复制的线程上下文中逐次推进同步流式生成器。"""

        def observed_stream():
            with observe_design_run(
                request_id="request-cross-context-stream",
                user_id=1,
                session_id="user:1:session",
                operation=DesignRunOperation.CHAT,
            ):
                yield "first-chunk"

        stream = observed_stream()
        self.assertEqual("first-chunk", Context().run(next, stream))
        with self.assertRaises(StopIteration):
            Context().run(next, stream)

        with self.Session() as db:
            saved_run = db.scalar(select(DesignRun))
        self.assertEqual(DesignRunStatus.SUCCEEDED, saved_run.status)

    def test_observation_database_failure_does_not_change_business_result(self):
        class _UnavailableSessionFactory:
            @staticmethod
            def begin():
                raise RuntimeError("observation database unavailable")

        with patch.object(
            observation_service,
            "SessionLocal",
            _UnavailableSessionFactory,
        ):
            with self.assertLogs(observation_service.logger, level="ERROR") as logs:
                result = None
                with observe_design_run(
                    request_id="request-db-failure",
                    user_id=1,
                    session_id="user:1:session",
                    operation=DesignRunOperation.CHAT,
                ):
                    result = "business-result"

        self.assertEqual("business-result", result)
        self.assertIn("observation_database_write_failed", logs.output[0])


if __name__ == "__main__":
    unittest.main()
