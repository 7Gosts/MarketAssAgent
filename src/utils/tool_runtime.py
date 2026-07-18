from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_config as get_current_runnable_config


def get_tool_configurable(config: RunnableConfig | None) -> dict[str, Any]:
    effective_config = config
    if not isinstance(effective_config, dict):
        try:
            effective_config = get_current_runnable_config()
        except Exception:
            return {}
    if not isinstance(effective_config, dict):
        return {}
    configurable = effective_config.get("configurable")
    return configurable if isinstance(configurable, dict) else {}


def get_tool_session_id(config: RunnableConfig | None) -> str:
    configurable = get_tool_configurable(config)
    return str(configurable.get("thread_id") or "").strip()


def get_tool_request_id(config: RunnableConfig | None) -> str:
    configurable = get_tool_configurable(config)
    return str(configurable.get("request_id") or "").strip()
