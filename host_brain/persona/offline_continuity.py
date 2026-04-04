"""Shared offline continuity semantics for absence-band driven behavior."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


ABSENCE_BANDS: tuple[str, ...] = ("recent", "6h_plus", "24h_plus", "72h_plus", "7d_plus")

_RECONNECT_STYLE_BY_ABSENCE_BAND: dict[str, str] = {
    "recent": "",
    "6h_plus": "soft_resume",
    "24h_plus": "light_reconnect",
    "72h_plus": "reunion",
    "7d_plus": "deep_reunion",
}

_ABSENCE_RECOVERY_FACTOR: dict[str, float] = {
    "recent": 1.0,
    "6h_plus": 1.03,
    "24h_plus": 1.08,
    "72h_plus": 1.14,
    "7d_plus": 1.20,
}

_ABSENCE_WARMTH_FLOOR: dict[str, float] = {
    "recent": 0.0,
    "6h_plus": 0.08,
    "24h_plus": 0.15,
    "72h_plus": 0.18,
    "7d_plus": 0.22,
}


def _coerce_optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def is_absence_band(value: str) -> bool:
    return value in ABSENCE_BANDS


def classify_absence_band(offline_hours: float) -> str:
    if offline_hours >= 7 * 24:
        return "7d_plus"
    if offline_hours >= 72:
        return "72h_plus"
    if offline_hours >= 24:
        return "24h_plus"
    if offline_hours >= 6:
        return "6h_plus"
    return "recent"


def classify_absence_band_from_duration_ms(duration_ms: int) -> str:
    resolved_duration_ms = max(0, int(duration_ms or 0))
    return classify_absence_band(resolved_duration_ms / 3_600_000.0)


def resolve_absence_band(
    offline_state: Mapping[str, Any] | None = None,
    *,
    offline_hours: float | None = None,
    offline_duration_ms: int | None = None,
) -> str:
    raw = dict(offline_state or {}) if isinstance(offline_state, Mapping) else {}
    raw_band = str(raw.get("absence_band") or "").strip()
    if is_absence_band(raw_band):
        return raw_band

    resolved_hours = _coerce_optional_float(offline_hours)
    if resolved_hours is None:
        resolved_hours = _coerce_optional_float(raw.get("offline_hours"))

    resolved_duration_ms = int(offline_duration_ms or 0)
    if resolved_duration_ms <= 0:
        duration_value = _coerce_optional_float(raw.get("offline_duration_ms"))
        if duration_value and duration_value > 0:
            resolved_duration_ms = int(duration_value)

    if resolved_hours is None and resolved_duration_ms > 0:
        return classify_absence_band_from_duration_ms(resolved_duration_ms)

    return classify_absence_band(max(0.0, float(resolved_hours or 0.0)))


def reconnect_style_for_absence_band(absence_band: str) -> str:
    return _RECONNECT_STYLE_BY_ABSENCE_BAND.get(absence_band, "")


def absence_recovery_factor_for_absence_band(absence_band: str) -> float:
    return _ABSENCE_RECOVERY_FACTOR.get(absence_band, 1.0)


def warmth_floor_for_absence_band(absence_band: str) -> float:
    return _ABSENCE_WARMTH_FLOOR.get(absence_band, 0.0)
