"""Utility helpers shared across surface/ submodules.

Extracted from isolated_surface_harness.py — pure text / collection
utilities with no dependency on surface package internals.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel


def _clean_text(raw: Any) -> str:
    return str(raw or "").replace("\u3000", " ").strip()


def _coerce_sequence(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes, bytearray)):
        text = _clean_text(value)
        return [text] if text else []
    if isinstance(value, Sequence):
        return list(value)
    return [value]


def _looks_numeric(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def _clean_list(
    values: Sequence[Any] | None,
    *,
    limit: int | None = None,
    max_chars: int = 0,
) -> list[str]:
    return _clean_bounded_list(values, limit=limit, max_chars=max_chars)


def _clean_bounded_list(
    values: Sequence[Any] | None,
    *,
    limit: int | None = None,
    max_chars: int = 180,
) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in list(values or []):
        text = _clean_text(value)
        if max_chars > 0:
            text = text[:max_chars].strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
        if limit is not None and len(cleaned) >= limit:
            break
    return cleaned


def _optional_list(raw: Any) -> list[str]:
    if raw is None or isinstance(raw, (str, bytes, bytearray)) or not isinstance(raw, Sequence):
        return []
    return _clean_list(raw)


def _model_dump(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return dict(model.model_dump())  # type: ignore[attr-defined]
    return dict(model.dict())  # pragma: no cover - pydantic v1


def _validate_model(
    model_type: type[BaseModel], payload: Mapping[str, Any]
) -> BaseModel:
    if hasattr(model_type, "model_validate"):
        return model_type.model_validate(payload)  # type: ignore[attr-defined]
    return model_type.parse_obj(payload)  # pragma: no cover - pydantic v1
