"""Numeric utility functions for emotion and affect calculations.

Provides canonical clamp/coerce helpers used across core, app, persona, and tribe layers.
Import from here instead of defining local copies.
"""

from __future__ import annotations

from typing import Any


def clamp_float(value: Any, low: float = -1.0, high: float = 1.0) -> float:
    """Clamp a numeric value to [low, high]. Returns 0.0 on conversion failure."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = 0.0
    return max(low, min(high, v))


def clamp01(value: Any) -> float:
    """Clamp a numeric value to [0.0, 1.0]."""
    return clamp_float(value, 0.0, 1.0)


def coerce_float(value: Any, *, default: float = 0.0) -> float:
    """Coerce a value to float, returning *default* on failure."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def coerce_optional_float(value: Any) -> float | None:
    """Coerce a value to float, returning None on failure."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
