from __future__ import annotations

import json
import math
from collections.abc import Mapping
from decimal import Decimal
from typing import Any


MAX_SAFE_INTEGER = (1 << 53) - 1


class CanonicalJsonError(ValueError):
    pass


def _utf16_key(value: str) -> bytes:
    return value.encode("utf-16-be", errors="surrogatepass")


def canonical_json(value: Any) -> bytes:
    return _encode(value).encode("utf-8")


def _encode(value: Any, path: str = "$") -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise CanonicalJsonError(f"integer outside IEEE-754 safe range at {path}")
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalJsonError(f"non-finite number at {path}")
        if value == 0:
            return "0"
        rendered = format(Decimal(str(value)), "f")
        if "." in rendered:
            rendered = rendered.rstrip("0").rstrip(".")
        return rendered
    if isinstance(value, list):
        return "[" + ",".join(_encode(item, f"{path}[{index}]") for index, item in enumerate(value)) + "]"
    if isinstance(value, Mapping):
        parts: list[str] = []
        if any(not isinstance(key, str) for key in value):
            raise CanonicalJsonError(f"non-string object key at {path}")
        for key in sorted(value, key=_utf16_key):
            parts.append(f"{_encode(key)}:{_encode(value[key], f'{path}.{key}')}")
        return "{" + ",".join(parts) + "}"
    raise CanonicalJsonError(f"unsupported JSON value {type(value).__name__} at {path}")
