"""Structured diagnostics for automated AKBridge processes."""

from __future__ import annotations

import json
import logging
import os
import traceback
from typing import Any

from .reliability import redact_secrets


class JsonFormatter(logging.Formatter):
    """Serialize log records as one redacted JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("api", "attempt", "duration_seconds", "status", "error"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exception"] = "".join(traceback.format_exception(*record.exc_info))[-4000:]
        return json.dumps(redact_secrets(payload), ensure_ascii=False, default=str)


def configure_logging(*, level: str | None = None, json_logs: bool | None = None) -> logging.Logger:
    """Configure the AKBridge logger once, writing diagnostics to stderr."""
    logger = logging.getLogger("akbridge")
    logger.setLevel(
        getattr(
            logging, (level or os.getenv("AKBRIDGE_LOG_LEVEL", "WARNING")).upper(), logging.WARNING
        )
    )
    if not logger.handlers:
        handler = logging.StreamHandler()
        if json_logs if json_logs is not None else os.getenv("AKBRIDGE_JSON_LOGS", "0") == "1":
            handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    return logger
