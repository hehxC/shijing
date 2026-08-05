import base64
import json
import os
import unittest
from unittest.mock import MagicMock, patch

from app.service.design_session_service import (
    DesignSessionError,
    MAX_MATERIAL_IMAGES,
    MaterialReference,
    material_scheme_summary,
    normalize_material_metadata,
    validate_image_data_url,
)
from app.service.image_generation_service import (
    _call_gemini_image,
    build_design_generation_prompt,
)


def image_data_url(mime_type="image/jpeg", content=b"image"):
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


class DesignGenerationTests(unittest.TestCase):
    def setUp(self):
        self.materials = (
            MaterialReference(1, image_data_url(), "granite.jpg", "芝麻灰", ("地面铺装",), 1),
            MaterialReference(2, image_data_url("image/png"), "edge.png", None, ("围边", "水景"), 2),
        )

    def test_image_validation_accepts_only_supported_data_urls(self):
        self.assertEqual(image_data_url(), validate_image_data_url(image_data_url()))
        with self.assertRaises(DesignSessionError):
            validate_image_data_url(image_data_url("image/gif"))
        with self.assertRaises(DesignSessionError):
            validate_image_data_url("data:image/jpeg;base64,not-base64!")

    def test_material_image_limit_is_twenty(self):
        self.assertEqual(20, MAX_MATERIAL_IMAGES)

    def test_material_metadata_deduplicates_and_validates_usages(self):
        name, usages = normalize_material_metadata("  青石  ", ["台阶", "台阶", "景墙"])
        self.assertEqual("青石", name)
        self.assertEqual(["台阶", "景墙"], usages)
        _, new_usages = normalize_material_metadata(None, ["汀步", "水景", "水景观", "驳岸"])
        self.assertEqual(["汀步", "水景", "水景观", "驳岸"], new_usages)
        with self.assertRaises(DesignSessionError):
            normalize_material_metadata(None, ["屋顶"])
        with self.assertRaisesRegex(DesignSessionError, "最多选择 7 个用途"):
            normalize_material_metadata(
                None,
                ["地面铺装", "汀步", "台阶", "墙面", "景墙", "围边", "水景", "驳岸"],
            )

    def test_prompt_uses_space_and_every_material_without_substitution(self):
        prompt = build_design_generation_prompt(
            "新中式庭院，需要品茶区",
            has_space_image=True,
            materials=self.materials,
        )
        self.assertIn("第 1 张输入图是庭院空间图", prompt)
        self.assertIn("芝麻灰", prompt)
        self.assertIn("edge.png", prompt)
        self.assertIn("所有石材都必须", prompt)
        self.assertIn("不得增加方案之外的其他石材", prompt)
        self.assertIn("16:9、2K", prompt)

    def test_prompt_designs_layout_when_space_image_is_missing(self):
        prompt = build_design_generation_prompt(
            "侘寂风",
            has_space_image=False,
            materials=self.materials,
        )
        self.assertIn("自行设计完整、合理的庭院布局", prompt)

    def test_gemini_request_contains_all_images_and_2k_widescreen_config(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps(
            {
                "status": "completed",
                "steps": [
                    {
                        "type": "model_output",
                        "content": [{"type": "image", "mime_type": "image/png", "data": "aW1hZ2U="}],
                    }
                ],
            }
        ).encode("utf-8")
        with (
            patch.dict(os.environ, {"GEMINI_KEY": "test-key"}),
            patch("app.service.image_generation_service.urlopen", return_value=response) as mock_urlopen,
            patch("app.service.image_generation_service._save_generated_image_data", return_value="/static/generated/test.png"),
        ):
            result = _call_gemini_image(
                "prompt",
                self.materials[0].image,
                [self.materials[1].image],
            )

        self.assertEqual("/static/generated/test.png", result)
        request = mock_urlopen.call_args.args[0]
        self.assertIn(
            "/v1beta/interactions",
            request.full_url,
        )
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual("gemini-3.1-flash-image", payload["model"])
        self.assertEqual(["image", "image", "text"], [item["type"] for item in payload["input"]])
        self.assertEqual("16:9", payload["response_format"]["aspect_ratio"])
        self.assertEqual("2K", payload["response_format"]["image_size"])

    def test_material_summary_lists_each_reference(self):
        summary = material_scheme_summary(self.materials)
        self.assertIn("芝麻灰：地面铺装", summary)
        self.assertIn("edge.png：围边、水景", summary)


if __name__ == "__main__":
    unittest.main()
