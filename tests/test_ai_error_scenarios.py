"""聊天各 AI 链路的端到端错误场景测试。

测试从公开 ``stream_chat`` / ``embed_query`` 入口进入，只替换模型、向量库等
外部系统边界，不会发送真实网络请求或写入真实会话数据。
"""

from contextlib import ExitStack
import json
import os
import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage, AIMessageChunk

from app.garden_styles import GARDEN_STYLES
from app.service import embedding_service
from app.service.chat_service import stream_chat
from app.service.image_generation_service import ImageGenerationError


class _AuthenticationError(Exception):
    """模拟供应商 SDK 的 401 错误。"""

    status_code = 401


class _RouterModel:
    """返回固定合法意图的路由供应商替身。"""

    def __init__(self, intent: str):
        self.intent = intent

    def invoke(self, _messages):
        return AIMessage(
            content=json.dumps(
                {
                    "intent": self.intent,
                    "use_image": "none",
                    "confidence": 0.95,
                    "reason": "test router",
                }
            )
        )


class _StreamingModel:
    """逐次模拟流式模型成功或失败。"""

    def __init__(self, *outcomes: str | BaseException):
        self._outcomes = iter(outcomes)
        self.call_count = 0

    def stream(self, _payload, *, stream_mode):
        self.call_count += 1
        outcome = next(self._outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return iter(((AIMessageChunk(content=outcome), {}),))


class _FailingEmbeddings:
    """记录 Embedding 供应商调用次数并抛出指定错误。"""

    def __init__(self, error: BaseException):
        self.error = error
        self.call_count = 0

    def embed_query(self, _text):
        self.call_count += 1
        raise self.error


def _wrapped_image_timeout(message: str) -> ImageGenerationError:
    """创建与真实图片适配器一致、保留超时根因的业务异常。"""
    try:
        raise TimeoutError(message)
    except TimeoutError as cause:
        error = ImageGenerationError(f"效果图生成服务请求失败：{message}")
        error.__cause__ = cause
        return error


class AiErrorScenarioTests(unittest.TestCase):
    """验证六条业务链路的错误处理结果。"""

    def setUp(self):
        self._stack = ExitStack()
        self._stack.enter_context(
            patch.dict(os.environ, {"AI_RETRY_BACKOFF_SECONDS": "0"})
        )
        # 会话读取和历史记录属于数据库边界，本组测试使用空设计会话。
        self._stack.enter_context(
            patch("app.service.chat_service.get_session_context", return_value=None)
        )
        self._stack.enter_context(
            patch("app.service.chat_service.load_recent_model_history", return_value=[])
        )
        self._stack.enter_context(
            patch("app.service.chat_service.remember_reference_image")
        )

    def tearDown(self):
        self._stack.close()

    def _router(self, intent: str):
        return patch(
            "app.service.chat_intent_router._get_router_model",
            return_value=_RouterModel(intent),
        )

    def test_text_chat_retries_timeout_before_output_then_succeeds(self):
        model = _StreamingModel(TimeoutError("text timeout"), "文本回答")

        with (
            self._router("general_chat"),
            patch("app.service.chat_service.get_chat_agent", return_value=model),
        ):
            response = "".join(stream_chat("你好", session_id="text-timeout"))

        self.assertEqual("文本回答", response)
        self.assertEqual(2, model.call_count)

    def test_text_chat_stops_after_retry_is_exhausted(self):
        model = _StreamingModel(
            TimeoutError("first text timeout"),
            TimeoutError("second text timeout"),
        )

        with (
            self._router("general_chat"),
            patch("app.service.chat_service.get_chat_agent", return_value=model),
        ):
            with self.assertRaises(TimeoutError):
                list(stream_chat("你好", session_id="text-failed"))

        self.assertEqual(2, model.call_count)

    def test_sql_authentication_failure_is_not_retried(self):
        model = _StreamingModel(_AuthenticationError("HTTP 401 invalid API key"))

        with (
            self._router("query_material"),
            patch("app.service.chat_service.get_chat_agent", return_value=model),
        ):
            with self.assertRaises(_AuthenticationError):
                list(stream_chat("莱姆石有哪些规格", session_id="sql-auth"))

        self.assertEqual(1, model.call_count)

    def test_vision_analysis_retries_timeout_then_succeeds(self):
        model = _StreamingModel(TimeoutError("vision timeout"), "视觉分析结果")

        with patch("app.service.chat_service.get_chat_agent", return_value=model):
            response = "".join(
                stream_chat(
                    "请分析这张图片",
                    image="data:image/png;base64,aW1hZ2U=",
                    session_id="vision-timeout",
                )
            )

        self.assertEqual("视觉分析结果", response)
        self.assertEqual(2, model.call_count)

    def test_vision_analysis_stops_after_retry_is_exhausted(self):
        model = _StreamingModel(
            TimeoutError("first vision timeout"),
            TimeoutError("second vision timeout"),
        )

        with patch("app.service.chat_service.get_chat_agent", return_value=model):
            with self.assertRaises(TimeoutError):
                list(
                    stream_chat(
                        "请分析这张图片",
                        image="data:image/png;base64,aW1hZ2U=",
                        session_id="vision-failed",
                    )
                )

        self.assertEqual(2, model.call_count)

    def test_rag_failure_degrades_to_text_answer_without_retry(self):
        model = _StreamingModel("无知识库上下文的回答")

        with (
            patch.dict(os.environ, {"ENABLE_RAG": "true"}),
            self._router("general_chat"),
            patch("app.service.chat_service.get_chat_agent", return_value=model),
            patch(
                "app.service.chat_service.retrieve",
                side_effect=TimeoutError("vector store timeout"),
            ) as retrieval,
        ):
            response = "".join(
                stream_chat("庭院排水怎么设计", session_id="rag-fallback")
            )

        self.assertEqual("无知识库上下文的回答", response)
        self.assertEqual(1, retrieval.call_count)

    def test_embedding_failure_is_returned_without_hidden_retry(self):
        embeddings = _FailingEmbeddings(TimeoutError("embedding timeout"))

        with patch(
            "app.service.embedding_service._get_embeddings",
            return_value=embeddings,
        ):
            with self.assertRaises(TimeoutError):
                embedding_service.embed_query("庭院排水")

        self.assertEqual(1, embeddings.call_count)

    def test_effect_image_retries_wrapped_timeout_then_succeeds(self):
        outcomes = iter(
            (
                _wrapped_image_timeout("first image timeout"),
                "/static/generated/success.png",
            )
        )
        calls = []

        def image_provider(*_args, **_kwargs):
            calls.append("called")
            outcome = next(outcomes)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        with (
            patch(
                "app.service.chat_service.get_design_generation_context",
                return_value=None,
            ),
            patch(
                "app.service.chat_service.generate_effect_image",
                side_effect=image_provider,
            ),
            patch("app.service.chat_service.remember_generated_image"),
        ):
            response = "".join(
                stream_chat(
                    "增加一处休息区",
                    session_id="image-timeout",
                    selected_style=GARDEN_STYLES[0],
                    force_generate_effect_image=True,
                )
            )

        self.assertIn("已生成", response)
        self.assertEqual(2, len(calls))

    def test_effect_image_returns_failure_after_retry_is_exhausted(self):
        calls = []

        def image_provider(*_args, **_kwargs):
            calls.append("called")
            raise _wrapped_image_timeout("image service unavailable")

        with (
            patch(
                "app.service.chat_service.get_design_generation_context",
                return_value=None,
            ),
            patch(
                "app.service.chat_service.generate_effect_image",
                side_effect=image_provider,
            ),
        ):
            response = "".join(
                stream_chat(
                    "增加一处休息区",
                    session_id="image-failed",
                    selected_style=GARDEN_STYLES[0],
                    force_generate_effect_image=True,
                )
            )

        self.assertIn("效果图生成失败", response)
        self.assertEqual(2, len(calls))


if __name__ == "__main__":
    unittest.main()
