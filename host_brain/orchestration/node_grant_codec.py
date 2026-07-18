"""Pure normalization and digest helpers for node-bound grants."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import Any


def mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(decoded) if isinstance(decoded, Mapping) else {}
    return {}


def required_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("value is required")
    return text


def exact_integer(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("boolean is not an integer authority")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("value must be an integer") from exc


def digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def stable_ref(kind: str, value: Mapping[str, Any]) -> str:
    return f"{kind}://sha256/{digest(value)}"


__all__ = ["digest", "exact_integer", "mapping", "required_text", "stable_ref"]
