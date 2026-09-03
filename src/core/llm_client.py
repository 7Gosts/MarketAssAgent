from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import httpx

from config.runtime_config import get_llm_runtime_settings, require_llm_model, resolve_llm_temperature
from .message_protocol import Message, ToolCall


_RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int = 0
    cached_prompt_tokens: int = 0


@dataclass(frozen=True)
class LLMResponse:
    message: Message
    finish_reason: str = ""
    usage: TokenUsage = field(default_factory=TokenUsage)
    raw: dict[str, Any] | None = None


class LLMClientError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


def completion_url(base_url: str) -> str:
    base = str(base_url or "https://api.openai.com/v1").rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


class OpenAICompatibleLLMClient:
    def __init__(
        self,
        *,
        model: str,
        base_url: str = "",
        api_key: str = "",
        temperature: float = 0.2,
        connect_timeout: float = 10.0,
        read_timeout: float = 90.0,
        max_retries: int = 2,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model = str(model or "").strip()
        if not self.model:
            raise ValueError("model is required")
        self.url = completion_url(base_url)
        self.api_key = str(api_key or "").strip()
        self.temperature = float(temperature)
        self.max_retries = max(0, int(max_retries))
        self.timeout = httpx.Timeout(read_timeout, connect=connect_timeout)
        self._http_client = http_client

    def _request_payload(
        self,
        *,
        messages: list[Message],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [message.to_openai_dict() for message in messages],
            "temperature": self.temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return payload

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def complete(
        self,
        *,
        messages: list[Message],
        tools: list[dict[str, Any]],
    ) -> LLMResponse:
        payload = self._request_payload(messages=messages, tools=tools)
        owns_client = self._http_client is None
        client = self._http_client or httpx.AsyncClient(timeout=self.timeout)
        try:
            for attempt in range(self.max_retries + 1):
                try:
                    response = await client.post(
                        self.url,
                        headers=self._headers(),
                        json=payload,
                        timeout=self.timeout,
                    )
                    self._raise_for_status(response)
                    return self._parse_response(self._decode_json(response))
                except LLMClientError as exc:
                    if not exc.retryable or attempt >= self.max_retries:
                        raise
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    if attempt >= self.max_retries:
                        raise LLMClientError(
                            f"LLM request failed: {exc}",
                            retryable=True,
                        ) from exc
                await asyncio.sleep(0.5 * (2 ** attempt))
        finally:
            if owns_client:
                await client.aclose()
        raise LLMClientError("LLM request failed", retryable=True)

    def complete_sync(
        self,
        *,
        messages: list[Message],
        tools: list[dict[str, Any]],
    ) -> LLMResponse:
        payload = self._request_payload(messages=messages, tools=tools)
        try:
            with httpx.Client(timeout=self.timeout) as client:
                for attempt in range(self.max_retries + 1):
                    try:
                        response = client.post(
                            self.url,
                            headers=self._headers(),
                            json=payload,
                            timeout=self.timeout,
                        )
                        self._raise_for_status(response)
                        return self._parse_response(self._decode_json(response))
                    except LLMClientError as exc:
                        if not exc.retryable or attempt >= self.max_retries:
                            raise
                    except (httpx.TimeoutException, httpx.TransportError) as exc:
                        if attempt >= self.max_retries:
                            raise LLMClientError(
                                f"LLM request failed: {exc}",
                                retryable=True,
                            ) from exc
                    import time
                    time.sleep(0.5 * (2 ** attempt))
        except LLMClientError:
            raise
        raise LLMClientError("LLM request failed", retryable=True)

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        retryable = response.status_code in _RETRYABLE_STATUS_CODES
        try:
            payload = response.json()
            error = payload.get("error") if isinstance(payload, dict) else None
            detail = error.get("message") if isinstance(error, dict) else str(error or "")
        except Exception:
            detail = response.text
        raise LLMClientError(
            f"LLM HTTP {response.status_code}: {str(detail or '').strip() or 'request failed'}",
            status_code=response.status_code,
            retryable=retryable,
        )

    @staticmethod
    def _decode_json(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except Exception as exc:
            raise LLMClientError("LLM response is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise LLMClientError("LLM response must be a JSON object")
        return payload

    @staticmethod
    def _parse_response(payload: dict[str, Any]) -> LLMResponse:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise LLMClientError("LLM response missing choices")
        choice = choices[0]
        raw_message = choice.get("message")
        if not isinstance(raw_message, dict):
            raise LLMClientError("LLM response missing message")

        calls: list[ToolCall] = []
        raw_calls = raw_message.get("tool_calls") or []
        if not isinstance(raw_calls, list):
            raise LLMClientError("LLM tool_calls must be a list")
        for index, raw_call in enumerate(raw_calls):
            if not isinstance(raw_call, dict):
                raise LLMClientError("LLM tool call must be an object")
            function = raw_call.get("function")
            if not isinstance(function, dict):
                raise LLMClientError("LLM tool call missing function")
            arguments: Any = function.get("arguments") or {}
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError as exc:
                    raise LLMClientError("LLM tool call arguments are not valid JSON") from exc
            if not isinstance(arguments, dict):
                raise LLMClientError("LLM tool call arguments must be a JSON object")
            calls.append(ToolCall(
                id=str(raw_call.get("id") or f"call_{index}"),
                name=str(function.get("name") or "").strip(),
                arguments=arguments,
            ))

        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        completion_details = usage.get("completion_tokens_details") if isinstance(usage.get("completion_tokens_details"), dict) else {}
        prompt_details = usage.get("prompt_tokens_details") if isinstance(usage.get("prompt_tokens_details"), dict) else {}
        return LLMResponse(
            message=Message(
                role="assistant",
                content=str(raw_message.get("content") or ""),
                tool_calls=tuple(calls),
            ),
            finish_reason=str(choice.get("finish_reason") or ""),
            usage=TokenUsage(
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                completion_tokens=int(usage.get("completion_tokens") or 0),
                total_tokens=int(usage.get("total_tokens") or 0),
                reasoning_tokens=int(completion_details.get("reasoning_tokens") or 0),
                cached_prompt_tokens=int(prompt_details.get("cached_tokens") or 0),
            ),
            raw=payload,
        )


def create_llm_client_from_config(*, temperature_fallback: float = 0.2) -> OpenAICompatibleLLMClient:
    settings = get_llm_runtime_settings()
    return OpenAICompatibleLLMClient(
        model=require_llm_model(settings, context="Agent"),
        base_url=str(settings.get("base_url") or ""),
        api_key=str(settings.get("api_key") or ""),
        temperature=resolve_llm_temperature(settings, fallback=temperature_fallback),
    )
