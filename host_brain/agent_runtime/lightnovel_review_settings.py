"""Typed cold settings for light-novel deep review."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from typing import Any


@dataclass(frozen=True, slots=True)
class LightNovelReviewSettings:
    enabled: bool = True
    cache_enabled: bool = True
    timeout_s: int = 12
    workers: int = 3
    cache_max_age_days: float = 30.0
    max_items: int | None = None


def _bool(value: Any, *, default: bool) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def load_lightnovel_review_settings(
    environ: Mapping[str, Any] | None = None,
) -> LightNovelReviewSettings:
    source = os.environ if environ is None else environ
    raw_max_items = str(source.get("HOST_LIGHTNOVEL_DEEP_REVIEW_MAX_ITEMS") or "").strip()
    try:
        max_items = max(1, int(raw_max_items)) if raw_max_items else None
    except ValueError:
        max_items = None
    try:
        cache_days = float(
            source.get("HOST_LIGHTNOVEL_DEEP_REVIEW_CACHE_MAX_AGE_DAYS") or 30
        )
    except (TypeError, ValueError):
        cache_days = 30.0
    return LightNovelReviewSettings(
        enabled=_bool(
            source.get("HOST_LIGHTNOVEL_DEEP_REVIEW_ENABLED"),
            default=True,
        ),
        cache_enabled=_bool(
            source.get("HOST_LIGHTNOVEL_DEEP_REVIEW_CACHE_ENABLED"),
            default=True,
        ),
        timeout_s=_int(
            source.get("HOST_LIGHTNOVEL_DEEP_REVIEW_TIMEOUT_S"),
            default=12,
            minimum=1,
            maximum=120,
        ),
        workers=_int(
            source.get("HOST_LIGHTNOVEL_DEEP_REVIEW_WORKERS"),
            default=3,
            minimum=1,
            maximum=6,
        ),
        cache_max_age_days=max(0.01, min(cache_days, 3650.0)),
        max_items=max_items,
    )


__all__ = ["LightNovelReviewSettings", "load_lightnovel_review_settings"]
