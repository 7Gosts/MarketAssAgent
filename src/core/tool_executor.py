from __future__ import annotations

import asyncio
import inspect
from typing import Any

from utils.logging_utils import get_logger
from .message_protocol import Message, ToolCall, tool_message
from .tool_protocol import ToolContext, ToolSpec


logger = get_logger(__name__)


class ToolArgumentError(ValueError):
    pass


class ToolExecutor:
    def __init__(self, registry: Any) -> None:
        self.registry = registry

    async def execute(
        self,
        call: ToolCall,
        *,
        context: ToolContext,
        allowed_names: set[str],
    ) -> Message:
        spec = self.registry.get(call.name)
        if spec is None:
            return tool_message(
                tool_call_id=call.id,
                name=call.name,
                result={"status": "error", "error": "unknown_tool"},
            )
        if call.name not in allowed_names:
            return tool_message(
                tool_call_id=call.id,
                name=call.name,
                result={"status": "error", "error": "tool_not_allowed"},
            )

        try:
            arguments = validate_arguments(spec.parameters, call.arguments)
            result = await invoke_tool(spec, arguments, context)
            return tool_message(
                tool_call_id=call.id,
                name=call.name,
                result=normalize_tool_result(result),
            )
        except ToolArgumentError as exc:
            return tool_message(
                tool_call_id=call.id,
                name=call.name,
                result={"status": "error", "error": "invalid_arguments", "message": str(exc)},
            )
        except Exception as exc:
            logger.exception("tool execution failed name=%s", call.name)
            return tool_message(
                tool_call_id=call.id,
                name=call.name,
                result={"status": "error", "error": "tool_execution_failed", "message": str(exc)},
            )

    async def execute_many(
        self,
        calls: list[ToolCall] | tuple[ToolCall, ...],
        *,
        context: ToolContext,
        allowed_names: set[str],
    ) -> list[Message]:
        results: list[Message] = []
        for call in calls:
            results.append(await self.execute(call, context=context, allowed_names=allowed_names))
        return results


async def invoke_tool(spec: ToolSpec, arguments: dict[str, Any], context: ToolContext) -> Any:
    kwargs = dict(arguments)
    if spec.requires_context:
        kwargs["context"] = context
    if inspect.iscoroutinefunction(spec.execute):
        return await spec.execute(**kwargs)
    result = await asyncio.to_thread(spec.execute, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def normalize_tool_result(result: Any) -> Any:
    if result is None:
        return {"status": "success"}
    return result


def validate_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise ToolArgumentError("arguments must be an object")
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = schema.get("required") if isinstance(schema.get("required"), list) else []
    missing = [str(name) for name in required if name not in arguments]
    if missing:
        raise ToolArgumentError(f"missing required fields: {', '.join(missing)}")
    if schema.get("additionalProperties") is False:
        unexpected = sorted(set(arguments) - set(properties))
        if unexpected:
            raise ToolArgumentError(f"unexpected fields: {', '.join(unexpected)}")

    for name, value in arguments.items():
        field_schema = properties.get(name)
        if not isinstance(field_schema, dict) or value is None:
            continue
        _validate_value(name, value, field_schema)
    return dict(arguments)


def _validate_value(name: str, value: Any, schema: dict[str, Any]) -> None:
    expected = schema.get("type")
    valid = True
    if expected == "string":
        valid = isinstance(value, str)
    elif expected == "number":
        valid = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif expected == "integer":
        valid = isinstance(value, int) and not isinstance(value, bool)
    elif expected == "boolean":
        valid = isinstance(value, bool)
    elif expected == "array":
        valid = isinstance(value, list)
    elif expected == "object":
        valid = isinstance(value, dict)
    if not valid:
        raise ToolArgumentError(f"field {name} must be {expected}")
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        raise ToolArgumentError(f"field {name} must be one of {enum}")
    min_length = schema.get("minLength")
    if isinstance(value, str) and isinstance(min_length, int) and len(value) < min_length:
        raise ToolArgumentError(f"field {name} is too short")
