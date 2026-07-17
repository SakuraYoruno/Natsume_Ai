"""Background-owned operator projection for capability gaps and Forge state."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from .projection_runtime import projection_meta
from host_brain.observability.projection_store import digest_json


CAPABILITY_FORGE_OPERATOR_KIND = "capability_forge_operator"
CAPABILITY_FORGE_OPERATOR_KEY = "current"
CAPABILITY_FORGE_OPERATOR_MAX_AGE_MS = 15_000


def _store(*, owning: bool) -> Any:
    from host_brain.composition import process_services as dependencies

    return (
        dependencies.get_query_projection_store()
        if owning
        else dependencies.peek_query_projection_store()
    )


def read_capability_forge_projection() -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Read the materialized document without initializing any source service."""

    store = _store(owning=False)
    document = (
        store.get(CAPABILITY_FORGE_OPERATOR_KIND, CAPABILITY_FORGE_OPERATOR_KEY)
        if store is not None
        else None
    )
    meta = projection_meta(
        CAPABILITY_FORGE_OPERATOR_KIND,
        CAPABILITY_FORGE_OPERATOR_KEY,
        document,
        max_age_ms=CAPABILITY_FORGE_OPERATOR_MAX_AGE_MS,
    )
    if document is None or not meta["available"]:
        return None, meta
    return dict(document.payload), meta


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


def refresh_capability_forge_projection() -> dict[str, int]:
    """The only place where operator queries touch live capability services."""

    from host_brain.composition.process_services import (
        get_capability_forge_service,
        get_capability_gap_telemetry,
    )

    telemetry = get_capability_gap_telemetry()
    forge = get_capability_forge_service()
    gap = telemetry.snapshot(
        limit_recent=200,
        limit_hotspots=50,
        limit_candidates=200,
        min_candidate_detected=1,
        min_candidate_sessions=1,
        min_candidate_failure_rate=0.0,
        min_candidate_failed=0,
    )
    payload = {
        "timestamp": int(gap.get("timestamp") or int(time.time() * 1000)),
        "gap": dict(gap),
        "proposals": list(forge.list_proposals(limit=200)),
        "runtime_stats": dict(forge.runtime_match_stats()),
        "runtime_tuning": dict(forge.runtime_tuning_stats(limit=200)),
        "runtime_quality": dict(forge.runtime_quality_stats(limit=200)),
    }
    store = _store(owning=True)
    current = store.get(CAPABILITY_FORGE_OPERATOR_KIND, CAPABILITY_FORGE_OPERATOR_KEY)
    cursor = max(
        int(payload["timestamp"]),
        int(current.source_cursor) + 1 if current is not None else 0,
    )
    store.upsert(
        CAPABILITY_FORGE_OPERATOR_KIND,
        CAPABILITY_FORGE_OPERATOR_KEY,
        payload,
        source_cursor=cursor,
        source_digest=digest_json(payload),
        source_ref="capability-forge-background-worker",
    )
    return {"document_count": 1}


__all__ = [
    "CAPABILITY_FORGE_OPERATOR_KEY",
    "CAPABILITY_FORGE_OPERATOR_KIND",
    "filter_forge_candidates",
    "read_capability_forge_projection",
    "read_capability_forge_projection_from_store",
    "refresh_capability_forge_projection",
]
