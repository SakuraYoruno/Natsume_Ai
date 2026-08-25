"""Small, side-effect-free text normalizers shared by Persona modules."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from host_brain.core.runtime_helpers import _coerce_mapping as _safe_mapping


def coerce_bounded_sequence(value: Any) -> list[Any]:
    """Normalize a scalar or sequence while bounding scalar text to 180 chars."""

    if value is None:
        return []
    if isinstance(value, (str, bytes, bytearray)):
        text = str(value or "").replace("\u3000", " ").strip()[:180]
        return [text] if text else []
    if isinstance(value, Sequence):
        return list(value)
    return [value]


def _normalize_text_list(raw: Any, fallback: list[str]) -> list[str]:
    if isinstance(raw, list):
        normalized = [str(item).strip() for item in raw if str(item).strip()]
        if normalized:
            return normalized
    return list(fallback)


def _coerce_text_list(raw: Any, fallback: list[str]) -> list[str]:
    if isinstance(raw, list):
        values = [str(item) for item in raw if str(item or "").strip()]
        if values:
            return values
    return list(fallback)


def _coerce_text_pool_mapping(
    raw: Any,
    fallback: dict[str, list[str]],
) -> dict[str, list[str]]:
    if isinstance(raw, Mapping):
        result: dict[str, list[str]] = {}
        for key, value in raw.items():
            if not isinstance(value, list):
                continue
            items = [str(item) for item in value if str(item or "").strip()]
            if items:
                result[str(key)] = items
        if result:
            return result
    return {str(key): list(value) for key, value in fallback.items()}


__all__ = [
    "coerce_bounded_sequence",
    "_coerce_text_list",
    "_coerce_text_pool_mapping",
    "_normalize_text_list",
    "_safe_mapping",
]
