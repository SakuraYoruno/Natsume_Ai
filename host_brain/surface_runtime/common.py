"""Dependency-free Surface runtime normalization helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def _safe_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _set_agent_input_mapping(agent_input: Any, name: str, value: Mapping[str, Any]) -> None:
    try:
        setattr(agent_input, name, dict(value))
    except Exception:
        return


def _clean_list(values: Sequence[Any] | None) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in list(values or []):
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned


def _coerce_sequence(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes, bytearray)):
        text = str(value or "").strip()
        return [text] if text else []
    if isinstance(value, Sequence):
        return list(value)
    return [value]


def _first_nonempty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _coerce_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


__all__ = [
    "_clean_list",
    "_coerce_int",
    "_coerce_sequence",
    "_first_nonempty",
    "_safe_mapping",
    "_set_agent_input_mapping",
]
