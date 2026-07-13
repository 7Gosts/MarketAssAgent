from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig


def get_tool_configurable(config: RunnableConfig | None) -> dict[str, Any]:
    if not isinstance(config, dict):
        return {}
    configurable = config.get("configurable")
    return configurable if isinstance(configurable, dict) else {}


def get_tool_session_id(config: RunnableConfig | None) -> str:
    configurable = get_tool_configurable(config)
    return str(configurable.get("thread_id") or "").strip()


def get_tool_request_id(config: RunnableConfig | None) -> str:
    configurable = get_tool_configurable(config)
    return str(configurable.get("request_id") or "").strip()
