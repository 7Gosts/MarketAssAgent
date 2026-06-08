from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any
from copy import copy


_DEFAULT_FORMAT = "%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s"
_DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_DEFAULT_ACCESS_FORMAT = '%(asctime)s.%(msecs)03d [%(levelname)s] %(client_addr)s - "%(request_line)s" %(status_code)s'


class _VSCodeStyleFormatter(logging.Formatter):
    """Render logs like VS Code Output: timestamp + lowercase level + message."""

    def format(self, record: logging.LogRecord) -> str:
        original_levelname = record.levelname
        original_message = record.msg
        try:
            record.levelname = original_levelname.lower()
            if not isinstance(record.msg, str):
                record.msg = str(record.msg)
            return super().format(record)
        finally:
            record.levelname = original_levelname
            record.msg = original_message


class _VSCodeAccessFormatter(_VSCodeStyleFormatter):
    """Render uvicorn access logs in VS Code Output style."""

    def format(self, record: logging.LogRecord) -> str:
        record_copy = copy(record)
        if len(record_copy.args) >= 5:
            client_addr, method, full_path, http_version, status_code = record_copy.args[:5]
            record_copy.__dict__.update(
                {
                    "client_addr": client_addr,
                    "request_line": f"{method} {full_path} HTTP/{http_version}",
                    "status_code": status_code,
                }
            )
            record_copy.args = ()
        return super().format(record_copy)


def _parse_level(value: str | None) -> int:
    text = str(value or "").strip().upper()
    if not text:
        return logging.INFO
    return getattr(logging, text, logging.INFO)


@lru_cache(maxsize=1)
def configure_logging() -> None:
    """Configure application-wide stdlib logging once."""
    level = _parse_level(os.getenv("MARKET_AGENT_LOG_LEVEL"))
    fmt = os.getenv("MARKET_AGENT_LOG_FORMAT", _DEFAULT_FORMAT)
    datefmt = os.getenv("MARKET_AGENT_LOG_DATE_FORMAT", _DEFAULT_DATE_FORMAT)

    root = logging.getLogger()
    handler = logging.StreamHandler()
    handler.setFormatter(_VSCodeStyleFormatter(fmt=fmt, datefmt=datefmt))

    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)


def get_uvicorn_log_config() -> dict[str, Any]:
    """Return a uvicorn log config using the same VS Code Output style formatter."""
    level_name = logging.getLevelName(_parse_level(os.getenv("MARKET_AGENT_LOG_LEVEL")))
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "vscode": {
                "()": "utils.logging_utils._VSCodeStyleFormatter",
                "fmt": os.getenv("MARKET_AGENT_LOG_FORMAT", _DEFAULT_FORMAT),
                "datefmt": os.getenv("MARKET_AGENT_LOG_DATE_FORMAT", _DEFAULT_DATE_FORMAT),
            },
            "vscode_access": {
                "()": "utils.logging_utils._VSCodeAccessFormatter",
                "fmt": os.getenv(
                    "MARKET_AGENT_ACCESS_LOG_FORMAT",
                    _DEFAULT_ACCESS_FORMAT,
                ),
                "datefmt": os.getenv("MARKET_AGENT_LOG_DATE_FORMAT", _DEFAULT_DATE_FORMAT),
            },
        },
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": "vscode",
                "stream": "ext://sys.stderr",
            },
            "access": {
                "class": "logging.StreamHandler",
                "formatter": "vscode_access",
                "stream": "ext://sys.stdout",
            },
        },
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": level_name, "propagate": False},
            "uvicorn.error": {"handlers": ["default"], "level": level_name, "propagate": False},
            "uvicorn.access": {"handlers": ["access"], "level": level_name, "propagate": False},
        },
    }
