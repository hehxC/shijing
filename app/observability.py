"""为所有 HTTP 请求提供 request ID 上下文和结构化安全日志。

业务代码只需要读取 ``current_request_id`` 或调用 ``log_event``。request ID 的
校验、生成、响应头注入、流式请求计时、JSON 编码和敏感信息脱敏都隐藏在本模块中。
"""

from contextvars import ContextVar
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
import json
import logging
import os
import re
import time
from typing import Any
from uuid import uuid4

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send


REQUEST_ID_HEADER = "X-Request-ID"
_MAX_REQUEST_ID_LENGTH = 64
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_CURRENT_REQUEST_ID: ContextVar[str | None] = ContextVar(
    "current_request_id",
    default=None,
)

# 日志只保留定位所需摘要，不保存完整图片内容或常见凭证值。
_MAX_LOG_TEXT_LENGTH = 2_000
_DATA_URL_PATTERN = re.compile(r"data:image/[^\s\"']+", re.I)
_BEARER_PATTERN = re.compile(
    r"(?i)(authorization\s*[:=]\s*bearer|bearer)\s+[^\s,;]+"
)
_SECRET_PATTERN = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|token|secret|password)"
    r"\s*[:=]\s*[^\s,;]+"
)
_SENSITIVE_FIELD_NAMES = {
    "api_key",
    "apikey",
    "access_token",
    "authorization",
    "password",
    "secret",
    "token",
}

logger = logging.getLogger(__name__)


def current_request_id() -> str:
    """返回当前请求 ID；不属于 HTTP 请求的日志明确标记为 ``unscoped``。"""
    return _CURRENT_REQUEST_ID.get() or "unscoped"


def _accepted_request_id(raw_value: str | None) -> str | None:
    """只接受短且可安全写入响应头、数据库和日志的客户端 request ID。"""
    if not raw_value or len(raw_value) > _MAX_REQUEST_ID_LENGTH:
        return None
    return raw_value if _REQUEST_ID_PATTERN.fullmatch(raw_value) else None


def _sanitize_text(value: str) -> str:
    """脱敏并截断任意日志文本，防止凭证和完整图片进入日志。"""
    value = _DATA_URL_PATTERN.sub("[image-data-redacted]", value)
    value = _BEARER_PATTERN.sub(r"\1 [redacted]", value)
    value = _SECRET_PATTERN.sub(r"\1=[redacted]", value)
    return value[:_MAX_LOG_TEXT_LENGTH]


def _safe_log_value(value: Any) -> Any:
    """把结构化字段递归转换为 JSON 可编码且经过脱敏的值。"""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, (Decimal, Enum)):
        return str(value.value if isinstance(value, Enum) else value)
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            safe_key = _sanitize_text(str(key))
            normalized_key = safe_key.lower().replace("-", "_")
            sanitized[safe_key] = (
                "[redacted]"
                if normalized_key in _SENSITIVE_FIELD_NAMES
                else _safe_log_value(item)
            )
        return sanitized
    if isinstance(value, (list, tuple, set)):
        return [_safe_log_value(item) for item in value]
    return _sanitize_text(str(value))


class StructuredJsonFormatter(logging.Formatter):
    """将应用日志编码为单行 JSON，并统一注入时间、级别和 request ID。"""

    def format(self, record: logging.LogRecord) -> str:
        event = getattr(record, "event", "application_log")
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created,
                tz=timezone.utc,
            ).isoformat(),
            "level": record.levelname.lower(),
            "event": _sanitize_text(str(event)),
            "request_id": current_request_id(),
        }
        fields = getattr(record, "structured_fields", {})
        if isinstance(fields, dict):
            payload.update(_safe_log_value(fields))

        # 普通 logger 调用仍能进入统一格式；结构化事件不重复输出 event 文本。
        if event == "application_log":
            payload["message"] = _sanitize_text(record.getMessage())
        if record.exc_info and record.exc_info[1] is not None:
            payload["exception_type"] = record.exc_info[0].__name__
            payload["exception_message"] = _sanitize_text(str(record.exc_info[1]))
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_structured_logging() -> None:
    """为 ``app.*`` 日志安装一次 JSON 输出，不改写第三方库的日志策略。"""
    app_logger = logging.getLogger("app")
    if any(getattr(handler, "_shijing_structured", False) for handler in app_logger.handlers):
        return

    handler = logging.StreamHandler()
    handler.setFormatter(StructuredJsonFormatter())
    handler._shijing_structured = True  # type: ignore[attr-defined]
    app_logger.addHandler(handler)
    app_logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    app_logger.propagate = False


def log_event(
    target_logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    """记录一个带稳定事件名的结构化日志事件。"""
    target_logger.log(
        level,
        event,
        extra={"event": event, "structured_fields": fields},
    )


class RequestContextMiddleware:
    """覆盖完整 ASGI 流程，为普通响应和流式响应提供同一个 request ID。"""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        raw_request_id = headers.get(REQUEST_ID_HEADER.lower().encode("ascii"))
        incoming = raw_request_id.decode("latin-1") if raw_request_id else None
        request_id = _accepted_request_id(incoming) or uuid4().hex
        token = _CURRENT_REQUEST_ID.set(request_id)
        started = time.perf_counter()
        status_code = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_headers = MutableHeaders(scope=message)
                response_headers[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except BaseException as exc:
            log_event(
                logger,
                "http_request_failed",
                level=logging.ERROR,
                method=scope.get("method"),
                path=scope.get("path"),
                status_code=status_code,
                latency_ms=max(0, round((time.perf_counter() - started) * 1_000)),
                error_type=exc.__class__.__name__,
                error_message=str(exc),
            )
            raise
        else:
            log_event(
                logger,
                "http_request_completed",
                method=scope.get("method"),
                path=scope.get("path"),
                status_code=status_code,
                latency_ms=max(0, round((time.perf_counter() - started) * 1_000)),
            )
        finally:
            _CURRENT_REQUEST_ID.reset(token)
