"""Pure bounded value helpers shared by Digital Life domain reducers."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _merge_patch(base: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(base))
    for key in sorted(str(item) for item in patch):
        incoming = patch.get(key)
        if incoming is None:
            result.pop(key, None)
        elif isinstance(incoming, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _merge_patch(dict(result[key]), incoming)
        elif isinstance(incoming, Mapping):
            result[key] = _merge_patch({}, incoming)
        else:
            result[key] = deepcopy(incoming)
    return result


def _clamp(value: Any, default: float = 0.0) -> float:
    return max(0.0, min(1.0, _float(value, default)))


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        return float(default)


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        return int(default)


def _required_text(value: Any, name: str, *, limit: int) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} is required")
    if len(normalized) > limit:
        raise ValueError(f"{name} exceeds {limit} characters")
    return normalized


def _approach(current: float, target: float, weight: float) -> float:
    ratio = _clamp(weight)
    return round(max(-1.0, min(1.0, current + (target - current) * ratio)), 4)


def _approach_signed(current: float, target: float, weight: float) -> float:
    ratio = _clamp(weight)
    return round(max(-1.0, min(1.0, current + (target - current) * ratio)), 6)


def _first_float(
    values: Mapping[str, Any],
    keys: tuple[str, ...],
    default: float,
) -> float:
    for key in keys:
        if values.get(key) not in (None, ""):
            return _clamp(values.get(key), default)
    return _clamp(default)


__all__ = ["reduce_life_domains"]
