import unittest
from unittest.mock import patch

from app.garden_styles import GARDEN_STYLES, build_style_generation_request, get_garden_style, list_garden_styles
from app.api.routes_chat import garden_styles
from app.service.chat_intent_router import route_chat_intent
from app.service.chat_service import stream_chat
from app.service.image_generation_service import is_effect_image_edit_request, needs_effect_image
from app.service.session_context_service import SessionContext


class GardenStyleTests(unittest.TestCase):
    def test_styles_have_unique_ids_and_complete_public_data(self):
        styles = list_garden_styles()
        self.assertEqual(18, len(styles))
        self.assertEqual(18, len({style["id"] for style in styles}))
        self.assertTrue(all(style["name"] and style["category"] and style["description"] for style in styles))
        self.assertEqual(styles, garden_styles())
        self.assertIsNone(get_garden_style("not-a-real-style"))

    def test_generation_request_contains_selected_style_and_user_requirement(self):
        style = get_garden_style("new-chinese")
        request = build_style_generation_request(style, "院子里需要一处品茶区")
        self.assertIn("新中式", request)
        self.assertIn(style.description, request)
        self.assertIn("品茶区", request)

    def test_short_followup_edit_routes_to_generated_image(self):
        self.assertTrue(is_effect_image_edit_request("增加瓦片围边"))
        self.assertTrue(needs_effect_image("增加瓦片围边"))
        self.assertFalse(needs_effect_image("瓦片围边多少钱"))

        intent = route_chat_intent(
            "增加瓦片围边",
            has_uploaded_image=False,
            has_reference_image=True,
            has_generated_image=True,
        )

        self.assertEqual("generate_effect_image", intent.intent)
        self.assertEqual("generated", intent.use_image)

    def test_recognize_image_routes_to_generated_image_analysis(self):
        intent = route_chat_intent(
            "请识别这张图，分析庭院空间、硬景材料、植物配置、光照条件和可优化建议。",
            has_uploaded_image=False,
            has_reference_image=True,
            has_generated_image=True,
        )

        self.assertEqual("analyze_image", intent.intent)
        self.assertEqual("generated", intent.use_image)

    def test_recognize_image_ignores_supplemental_edit_words(self):
        intent = route_chat_intent(
            "请识别这张图，分析庭院空间、硬景材料、植物配置、光照条件和可优化建议。用户补充要求：增加瓦片围边",
            has_uploaded_image=False,
            has_reference_image=True,
            has_generated_image=True,
        )

        self.assertEqual("analyze_image", intent.intent)
        self.assertEqual("generated", intent.use_image)

    @patch("app.service.chat_service._stream_model_agent", return_value=iter(["识图结果"]))
    @patch("app.service.chat_service.generate_effect_image")
    @patch("app.service.chat_service.generated_image_as_data_url", return_value="data:image/png;base64,old")
    @patch("app.service.chat_service.get_session_context")
    def test_recognize_image_uses_previous_generated_image_without_generating(
        self, mock_context, mock_data_url, mock_generate, mock_stream
    ):
        mock_context.return_value = SessionContext(
            session_id="recognize-test",
            reference_image_data_url="data:image/jpeg;base64,original",
            reference_image_request="原始庭院照片",
            generated_image_url="/static/generated/old.png",
            generation_request="原始效果图需求",
        )

        response = "".join(
            stream_chat(
                "请识别这张图，分析庭院空间、硬景材料、植物配置、光照条件和可优化建议。",
                session_id="recognize-test",
            )
        )

        mock_generate.assert_not_called()
        mock_data_url.assert_called_once_with("/static/generated/old.png")
        self.assertEqual("data:image/png;base64,old", mock_stream.call_args.args[2])
        self.assertIn("识图结果", response)

    @patch("app.service.chat_service.remember_generated_image")
    @patch("app.service.chat_service._clear_session_checkpoints")
    @patch("app.service.chat_service.generate_effect_image", return_value="/static/generated/edited.jpg")
    @patch("app.service.chat_service.generated_image_as_data_url", return_value="data:image/png;base64,old")
    @patch("app.service.chat_service.get_session_context")
    def test_followup_edit_uses_previous_generated_effect_image(
        self, mock_context, mock_data_url, mock_generate, mock_clear, mock_remember
    ):
        mock_context.return_value = SessionContext(
            session_id="edit-test",
            reference_image_data_url="data:image/jpeg;base64,original",
            reference_image_request="原始庭院照片",
            generated_image_url="/static/generated/old.png",
            generation_request="原始效果图需求",
        )

        response = "".join(stream_chat("增加瓦片围边", session_id="edit-test"))

        mock_data_url.assert_called_once_with("/static/generated/old.png")
        mock_generate.assert_called_once()
        prompt, image = mock_generate.call_args.args
        self.assertEqual("data:image/png;base64,old", image)
        self.assertIn("上一轮已生成的效果图", prompt)
        self.assertIn("增加瓦片围边", prompt)
        mock_remember.assert_called_once()
        self.assertIn("效果图已生成", response)

    @patch("app.service.chat_service.remember_generated_image")
    @patch("app.service.chat_service._clear_session_checkpoints")
    @patch("app.service.chat_service.generate_effect_image", return_value="/static/generated/test.jpg")
    @patch("app.service.chat_service.get_session_context", return_value=None)
    def test_explicit_style_generation_bypasses_intent_router(
        self, mock_context, mock_generate, mock_clear, mock_remember
    ):
        style = GARDEN_STYLES[0]
        with patch("app.service.chat_service.route_chat_intent") as mock_router:
            response = "".join(
                stream_chat(
                    "增加一处休息区",
                    session_id="style-test",
                    selected_style=style,
                    force_generate_effect_image=True,
                )
            )

        mock_router.assert_not_called()
        prompt = mock_generate.call_args.args[0]
        self.assertIn(style.name, prompt)
        self.assertIn("休息区", prompt)
        mock_remember.assert_called_once()
        self.assertIn("已生成", response)


if __name__ == "__main__":
    unittest.main()
