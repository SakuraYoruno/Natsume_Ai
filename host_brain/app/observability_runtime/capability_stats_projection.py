"""Background-owned operator projection for capability gaps and Forge state."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .projection_runtime import projection_meta


CAPABILITY_FORGE_OPERATOR_KIND = "capability_forge_operator"
CAPABILITY_FORGE_OPERATOR_KEY = "current"
# The standalone Obs daemon refreshes all projections in one bounded,
# sequential wave.  Capability Forge itself may take a few seconds and the
# complete wave can legitimately take longer than its 15-second scheduling
# interval.  A 15-second read TTL therefore marked a healthy projection stale
# before the next wave reached it.  Keep the read window aligned with the
# standard operator projection window while the producer cadence remains
# 15 seconds (best effort).
CAPABILITY_FORGE_OPERATOR_MAX_AGE_MS = 120_000


def read_capability_forge_projection_from_store(
    store: Any,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Read Capability Forge state through an explicitly composed store."""

    document = store.get(CAPABILITY_FORGE_OPERATOR_KIND, CAPABILITY_FORGE_OPERATOR_KEY)
    meta = projection_meta(
        CAPABILITY_FORGE_OPERATOR_KIND,
        CAPABILITY_FORGE_OPERATOR_KEY,
        document,
        max_age_ms=CAPABILITY_FORGE_OPERATOR_MAX_AGE_MS,
    )
    if document is None or not meta["available"]:
        return None, meta
    return dict(document.payload), meta


def filter_forge_candidates(
    candidates: list[Any],
    *,
    limit: int,
    min_detected_count: int,
    min_unique_sessions: int,
    min_failure_rate: float,
    min_failed_count: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apply request thresholds to the superset captured by the worker."""

    thresholds = {
        "min_detected_count": max(1, int(min_detected_count)),
        "min_unique_sessions": max(1, int(min_unique_sessions)),
        "min_failure_rate": round(max(0.0, min(1.0, float(min_failure_rate))), 4),
        "min_failed_count": max(0, int(min_failed_count)),
    }
    selected: list[dict[str, Any]] = []
    for value in candidates:
        if not isinstance(value, Mapping):
            continue
        item = dict(value)
        detected = int(item.get("detected_count") or 0)
        sessions = int(item.get("impact_sessions") or 0)
        executed = int(item.get("executed_count") or 0)
        failed = int(item.get("failed_count") or 0)
        failure_rate = float(item.get("failure_rate") or 0.0)
        if (
            detected >= thresholds["min_detected_count"]
            and sessions >= thresholds["min_unique_sessions"]
            and (
                failed >= thresholds["min_failed_count"]
                or failure_rate >= thresholds["min_failure_rate"]
                or (
                    executed == 0
                    and detected >= max(4, thresholds["min_detected_count"] * 2)
                )
            )
        ):
            item["stage"] = "READY_FOR_FORGE"
            selected.append(item)
    return selected[: max(1, int(limit))], thresholds


__all__ = [
    "CAPABILITY_FORGE_OPERATOR_KEY",
    "CAPABILITY_FORGE_OPERATOR_KIND",
    "filter_forge_candidates",
    "read_capability_forge_projection_from_store",
]
