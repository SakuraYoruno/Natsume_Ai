"""Utility helpers shared across surface/ submodules.

Extracted from isolated_surface_harness.py — pure text / collection
utilities with no dependency on surface package internals.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel


def _clean_text(raw: Any) -> str:
    return str(raw or "").replace("\u3000", " ").strip()


def _clean_list(values: Sequence[Any] | None) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in list(values or []):
        text = _clean_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
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


def _rebuild_control(
    control: BaseModel, updates: Mapping[str, Any]
) -> BaseModel:
    payload = _model_dump(control)
    payload.update(dict(updates))
    return _validate_model(type(control), payload)  # type: ignore[return-value]
