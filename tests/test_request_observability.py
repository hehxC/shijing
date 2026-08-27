import io
import json
import logging
import re
import unittest

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from app.observability import (
    RequestContextMiddleware,
    StructuredJsonFormatter,
    current_request_id,
    log_event,
)


class RequestObservabilityTests(unittest.TestCase):
    """从公共中间件和日志接口验证 request ID 全链路行为。"""

    def setUp(self):
        self.log_stream = io.StringIO()
        self.log_handler = logging.StreamHandler(self.log_stream)
        self.log_handler.setFormatter(StructuredJsonFormatter())
        self.test_logger = logging.getLogger("request-observability-test")
        self.test_logger.handlers = [self.log_handler]
        self.test_logger.setLevel(logging.INFO)
        self.test_logger.propagate = False

        app = FastAPI()
        app.add_middleware(RequestContextMiddleware)

        @app.get("/request-id")
        def request_id_endpoint():
            log_event(self.test_logger, "request_id_seen")
            return {"request_id": current_request_id()}

        @app.get("/stream")
        def stream_endpoint():
            def body():
                yield current_request_id()
                log_event(self.test_logger, "stream_finished")

            return StreamingResponse(body(), media_type="text/plain")

        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.test_logger.handlers = []

    def _log_lines(self) -> list[dict]:
        return [
            json.loads(line)
            for line in self.log_stream.getvalue().splitlines()
            if line.strip()
        ]

    def test_server_generates_and_returns_request_id(self):
        response = self.client.get("/request-id")

        request_id = response.headers["X-Request-ID"]
        self.assertRegex(request_id, re.compile(r"^[a-f0-9]{32}$"))
        self.assertEqual(request_id, response.json()["request_id"])
        self.assertEqual(request_id, self._log_lines()[0]["request_id"])

    def test_valid_client_request_id_is_preserved(self):
        response = self.client.get(
            "/request-id",
            headers={"X-Request-ID": "client-request_2026-08-24"},
        )

        self.assertEqual(
            "client-request_2026-08-24",
            response.headers["X-Request-ID"],
        )
        self.assertEqual(
            "client-request_2026-08-24",
            response.json()["request_id"],
        )

    def test_invalid_client_request_id_is_replaced(self):
        response = self.client.get(
            "/request-id",
            headers={"X-Request-ID": "invalid request id with spaces"},
        )

        self.assertNotEqual(
            "invalid request id with spaces",
            response.headers["X-Request-ID"],
        )
        self.assertRegex(response.headers["X-Request-ID"], r"^[a-f0-9]{32}$")

    def test_request_context_survives_until_stream_finishes(self):
        response = self.client.get(
            "/stream",
            headers={"X-Request-ID": "stream-request-1"},
        )

        self.assertEqual("stream-request-1", response.text)
        self.assertEqual("stream-request-1", response.headers["X-Request-ID"])
        self.assertEqual("stream-request-1", self._log_lines()[0]["request_id"])

    def test_structured_log_redacts_credentials_and_image_data(self):
        log_event(
            self.test_logger,
            "provider_failed",
            authorization="Bearer top-secret",
            password="plain-password",
            error_message=(
                "api_key=provider-key data:image/png;base64,QUJDREVG"
            ),
        )

        saved = self._log_lines()[0]
        rendered = json.dumps(saved, ensure_ascii=False)
        self.assertEqual("provider_failed", saved["event"])
        self.assertEqual("[redacted]", saved["authorization"])
        self.assertEqual("[redacted]", saved["password"])
        self.assertNotIn("top-secret", rendered)
        self.assertNotIn("plain-password", rendered)
        self.assertNotIn("provider-key", rendered)
        self.assertNotIn("QUJDREVG", rendered)
        self.assertIn("[image-data-redacted]", rendered)


if __name__ == "__main__":
    unittest.main()
