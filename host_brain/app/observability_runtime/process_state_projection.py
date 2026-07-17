"""Background-only projections for process, file and aggregate OBS state.

The route handlers consuming these documents are intentionally boring: they
peek the already-composed :class:`QueryProjectionStore` and shape an empty
response when a document is missing or stale.  Every live manager lookup,
process snapshot, ledger aggregation and filesystem scan stays in the jobs in
this module.

BioOps owner artifacts receive a stricter projection than the generic public
serializer.  Only identifiers, state metadata, logical references and a
digest of that metadata are durable; packet/candidate bodies and deployment
paths never enter the projection database.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

from natsume_contracts.operator.v1 import ProjectionDegradedState

from .projection_daemon import register_projection_job
from .projection_runtime import projection_meta
from host_brain.observability.projection_store import QueryProjectionStore, digest_json


INSIGHTS_SUMMARY_KIND = "insights_summary_snapshot"
DIGITAL_LIFE_OVERVIEW_KIND = "digital_life_overview_snapshot"
RUNTIME_SNAPSHOT_KIND = "digital_life_runtime_snapshot"
RUNTIME_SNAPSHOT_KEY = "default"
LIFE_BODY_KIND = "life_body_snapshot"
RESOURCE_MANAGER_KIND = "resource_manager_status"
RESOURCE_MANAGER_KEY = "current"
BIO_OPS_KIND = "bio_ops_metadata"
BIO_OPS_LATEST_KEY = "latest"

SUMMARY_MAX_AGE_MS = 90_000
DIGITAL_LIFE_MAX_AGE_MS = 180_000
RUNTIME_MAX_AGE_MS = 45_000
LIFE_BODY_MAX_AGE_MS = 90_000
RESOURCE_MANAGER_MAX_AGE_MS = 90_000
BIO_OPS_MAX_AGE_MS = 5 * 60_000

_cursor_lock = threading.Lock()
_last_cursor: dict[str, int] = {}
_job_lock = threading.Lock()
_last_job_run: dict[str, float] = {}


def window_key(window_minutes: int) -> str:
    return f"window:{max(1, int(window_minutes))}"


def complete_projection_meta(meta: Mapping[str, Any]) -> dict[str, Any]:
    """Return the stable version/cursor/freshness/degradation envelope."""

    return {
        "schema_version": str(
            meta.get("schema_version") or "Natsume.operator.query_projection.v1"
        ),
        "kind": str(meta.get("kind") or ""),
        "key": str(meta.get("key") or ""),
        "available": bool(meta.get("available", False)),
        "projection_version": int(meta.get("projection_version") or 0),
        "projection_cursor": int(meta.get("projection_cursor") or 0),
        "source_cursor": int(meta.get("source_cursor") or 0),
        "cursor": int(meta.get("source_cursor") or 0),
        "freshness_ts_ms": int(meta.get("freshness_ts_ms") or 0),
        "age_ms": int(meta.get("age_ms") or 0),
        "degraded_state": str(meta.get("degraded_state") or "REBUILDING"),
        "degraded_detail": meta.get("degraded_detail"),
        "reason_code": meta.get("reason_code") or "OPERATOR_PROJECTION_NOT_READY",
    }


def read_process_projection(
    kind: str,
    key: str,
    *,
    max_age_ms: int,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Peek one durable document without constructing a store or source."""

    from host_brain.composition.process_services import peek_query_projection_store

    store = peek_query_projection_store()
    document = None
    error_name: str | None = None
    if store is not None:
        try:
            document = store.get(kind, key)
        except Exception as exc:  # read boundary must remain available
            error_name = type(exc).__name__
    meta = complete_projection_meta(
        projection_meta(kind, key, document, max_age_ms=max_age_ms)
    )
    if error_name:
        meta.update(
            {
                "available": False,
                "degraded_state": "SOURCE_UNAVAILABLE",
                "degraded_detail": error_name,
                "reason_code": "OPERATOR_PROJECTION_SOURCE_UNAVAILABLE",
            }
        )
    if document is None or not meta["available"]:
        return None, meta
    return dict(document.payload), meta


def _next_cursor(name: str, observed: int | None = None) -> int:
    candidate = max(0, int(observed or int(time.time() * 1000)))
    with _cursor_lock:
        candidate = max(candidate, _last_cursor.get(name, 0) + 1)
        _last_cursor[name] = candidate
    return candidate


def _run_if_due(
    name: str,
    interval_seconds: float,
    callback: Callable[[], dict[str, int]],
) -> dict[str, int | bool]:
    now = time.monotonic()
    with _job_lock:
        previous = _last_job_run.get(name, 0.0)
        if now - previous < max(1.0, float(interval_seconds)):
            return {"skipped": True}
        _last_job_run[name] = now
    return callback()


def _store() -> QueryProjectionStore:
    from host_brain.composition.process_services import get_query_projection_store

    return get_query_projection_store()


def _upsert(
    kind: str,
    key: str,
    payload: Mapping[str, Any],
    *,
    cursor: int,
    source_ref: str,
    degraded_state: ProjectionDegradedState = ProjectionDegradedState.HEALTHY,
    degraded_detail: str | None = None,
) -> None:
    _store().upsert(
        kind,
        key,
        payload,
        source_cursor=cursor,
        source_digest=digest_json(payload),
        source_ref=source_ref,
        degraded_state=degraded_state,
        degraded_detail=degraded_detail,
    )


def refresh_insights_summary_projection() -> dict[str, int]:
    """Aggregate the configured summary window outside the request path."""

    from host_brain.app.obs_routes_summary import (
        INSIGHTS_SUMMARY_PROJECTION_WINDOW_MINUTES,
        _build_insights_summary_snapshot_sync,
    )

    window = int(INSIGHTS_SUMMARY_PROJECTION_WINDOW_MINUTES)
    payload = _build_insights_summary_snapshot_sync(window)
    cursor = _next_cursor(INSIGHTS_SUMMARY_KIND, int(payload.get("timestamp") or 0))
    _upsert(
        INSIGHTS_SUMMARY_KIND,
        window_key(window),
        payload,
        cursor=cursor,
        source_ref="insights-summary-background-worker",
    )
    return {"document_count": 1}


def refresh_digital_life_overview_projection() -> dict[str, int]:
    """Build the digital-life aggregate only in the background worker."""

    import asyncio

    from host_brain.app.obs_routes_summary import (
        DIGITAL_LIFE_OVERVIEW_PROJECTION_WINDOW_MINUTES,
        _build_digital_life_overview_snapshot_async,
    )

    window = int(DIGITAL_LIFE_OVERVIEW_PROJECTION_WINDOW_MINUTES)
    payload = asyncio.run(
        _build_digital_life_overview_snapshot_async(
            window,
            projection_store=_store(),
        )
    )
    cursor = _next_cursor(DIGITAL_LIFE_OVERVIEW_KIND, int(payload.get("timestamp") or 0))
    _upsert(
        DIGITAL_LIFE_OVERVIEW_KIND,
        window_key(window),
        payload,
        cursor=cursor,
        source_ref="digital-life-overview-background-worker",
    )
    return {"document_count": 1}


def refresh_runtime_snapshot_projection() -> dict[str, int]:
    """Read process/runtime managers outside ``GET /runtime-snapshot``."""

    from host_brain.app.obs_routes_sysmode import _build_runtime_snapshot_response
    from host_brain.app.lifecycle_runtime_composition import (
        build_lifecycle_runtime_application_ports,
    )

    value = _build_runtime_snapshot_response(
        "",
        lifecycle_ports=build_lifecycle_runtime_application_ports(),
    )
    payload = value.model_dump() if hasattr(value, "model_dump") else value.dict()
    cursor = _next_cursor(RUNTIME_SNAPSHOT_KIND, int(payload.get("timestamp") or 0))
    _upsert(
        RUNTIME_SNAPSHOT_KIND,
        RUNTIME_SNAPSHOT_KEY,
        payload,
        cursor=cursor,
        source_ref="runtime-snapshot-background-worker",
    )
    return {"document_count": 1}


def refresh_life_body_projection() -> dict[str, int]:
    """Build the heavy life aggregate outside all three life GET handlers."""

    from host_brain.app.obs_routes_life_body import (
        LIFE_BODY_PROJECTION_WINDOW_MINUTES,
        _build_life_body_payload,
    )

    window = int(LIFE_BODY_PROJECTION_WINDOW_MINUTES)
    payload = _build_life_body_payload(
        window,
        projection_store=_store(),
    )
    cursor = _next_cursor(LIFE_BODY_KIND, int(payload.get("timestamp") or 0))
    _upsert(
        LIFE_BODY_KIND,
        window_key(window),
        payload,
        cursor=cursor,
        source_ref="life-body-background-worker",
    )
    return {"document_count": 1}


def _resource_manager_status_path() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "runtime_resource_manager.json"


def _read_resource_manager_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def load_resource_manager_projection_source(
    *,
    manager_provider: Callable[[], Any | None] | None = None,
    status_path: Path | None = None,
) -> tuple[dict[str, Any], int, str]:
    """Background source for live manager state with file fallback."""

    from host_brain.app.obs_routes_core import _compact_resource_manager_status

    enabled = str(os.environ.get("HOST_RESOURCE_MANAGER_ENABLED") or "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        now = int(time.time() * 1000)
        return (
            {
                "status": "DISABLED",
                "source": "disabled",
                "timestamp": now,
                "running": False,
                "registered": False,
                "detail": "SystemResourceManager is disabled.",
                "status_ref": "resource-manager://status/current",
            },
            now,
            "disabled",
        )
    if manager_provider is None:
        from host_brain.composition.process_services import get_resource_manager

        manager_provider = lambda: get_resource_manager(create_if_missing=False)
    raw: dict[str, Any] = {}
    source = "unavailable"
    try:
        manager = manager_provider()
        if manager is not None:
            value = manager.get_status()
            raw = dict(value) if isinstance(value, Mapping) else {}
            source = "live_manager"
    except Exception:
        raw = {}
    path = status_path or _resource_manager_status_path()
    if not raw:
        raw = _read_resource_manager_file(path)
        if raw:
            source = "status_file"
    cursor = max(
        int(raw.get("last_tick_ts_ms") or 0),
        int(path.stat().st_mtime * 1000) if source == "status_file" and path.exists() else 0,
        int(time.time() * 1000),
    )
    payload = {
        "status": "OK" if raw else "UNAVAILABLE",
        "source": source,
        "timestamp": cursor,
        **_compact_resource_manager_status(raw),
    }
    # Never persist a machine-local status path.
    payload.pop("status_path", None)
    payload["status_ref"] = "resource-manager://status/current"
    return payload, cursor, source


def refresh_resource_manager_projection() -> dict[str, int]:
    payload, observed, source = load_resource_manager_projection_source()
    cursor = _next_cursor(RESOURCE_MANAGER_KIND, observed)
    unavailable = str(payload.get("status") or "").upper() == "UNAVAILABLE"
    _upsert(
        RESOURCE_MANAGER_KIND,
        RESOURCE_MANAGER_KEY,
        payload,
        cursor=cursor,
        source_ref=f"resource-manager-{source}-background-worker",
        degraded_state=(
            ProjectionDegradedState.SOURCE_UNAVAILABLE
            if unavailable
            else ProjectionDegradedState.HEALTHY
        ),
        degraded_detail="resource manager source unavailable" if unavailable else None,
    )
    return {"document_count": 1}


_BIO_METADATA_FIELDS = frozenset(
    {
        "active_only",
        "already_imported_count",
        "artifact_available",
        "blocked_count",
        "candidate_id",
        "capability",
        "channel_id",
        "count",
        "created_at_ms",
        "decision",
        "direct_long_term_write",
        "durable_memory_write_performed",
        "durable_write_performed",
        "generated_at_ms",
        "hint_id",
        "import_allowed",
        "imported_count",
        "item_count",
        "ok",
        "owner_candidate_state_write_performed",
        "owner_organ",
        "owner_runtime_hint_write_performed",
        "packet_count",
        "pending",
        "reason_code",
        "review_status",
        "reviewed_at_ms",
        "run_id",
        "source_queue_count",
        "state_owner",
        "status",
        "updated_at_ms",
        "write_policy",
    }
)


def _bio_metadata_item(
    value: Mapping[str, Any],
    *,
    run_id: str,
    collection: str,
) -> dict[str, Any]:
    item = {
        str(key): raw
        for key, raw in value.items()
        if str(key) in _BIO_METADATA_FIELDS
        and isinstance(raw, (str, int, float, bool, type(None)))
    }
    decision_value = value.get("decision")
    if isinstance(decision_value, Mapping):
        item["decision"] = str(decision_value.get("decision") or "") or None
    output_kind = str(value.get("output_kind") or "").strip()
    if output_kind:
        item["artifact_kind"] = output_kind
    identity = str(
        item.get("candidate_id")
        or item.get("hint_id")
        or item.get("channel_id")
        or len(item)
    )
    item["source_ref"] = f"bio-ops://{run_id}/{collection}/{identity}"
    item["metadata_digest"] = digest_json(item)
    return item


def _bio_items(
    value: Mapping[str, Any],
    *,
    run_id: str,
    collection: str,
) -> list[dict[str, Any]]:
    rows = value.get("items")
    if not isinstance(rows, list):
        return []
    return [
        _bio_metadata_item(row, run_id=run_id, collection=collection)
        for row in rows
        if isinstance(row, Mapping)
    ]


def _safe_call(callback: Callable[[], Mapping[str, Any]]) -> dict[str, Any]:
    try:
        value = callback()
    except Exception:
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _monotonic_source_cursor(
    current: Any | None,
    payload: Mapping[str, Any],
    observed: int,
) -> int:
    """Bind changed sources to a new cursor and reuse unchanged durable cursors."""

    if current is None:
        return max(0, int(observed))
    current_cursor = int(current.source_cursor)
    if current.source_digest == digest_json(payload):
        return max(0, int(observed), current_cursor)
    return max(0, int(observed), current_cursor + 1)


def load_bio_ops_projection_sources(root: Path) -> list[tuple[str, dict[str, Any], int]]:
    """Scan owner artifacts and emit metadata-only documents per run."""

    from host_brain.app import obs_routes_agent_bio_ops as routes
    from host_brain.bio_ops.owner_consumer import BioOpsOwnerIntakeConsumer
    from host_brain.bio_ops.owner_inbox import BioOpsOwnerInbox
    from host_brain.organ_ops.owner_candidate_store import OwnerCandidateStateStore

    if not root.is_dir():
        return []
    documents: list[tuple[str, dict[str, Any], int]] = []
    for run_dir in sorted(
        (path for path in root.iterdir() if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
    ):
        run_id = run_dir.name
        inbox = BioOpsOwnerInbox(
            source_root=run_dir,
            inbox_root=run_dir / "owner_inbox",
        )
        consumer = BioOpsOwnerIntakeConsumer(
            inbox_root=run_dir / "owner_inbox",
            queue_root=run_dir / "owner_queues",
        )
        owner_state = OwnerCandidateStateStore(
            queue_root=run_dir / "owner_queues",
            state_root=run_dir / "owner_state",
        )
        adoption_records = _safe_call(
            lambda: {
                "items": routes._collect_adoption_records(
                    run_dir,
                    routes._read_json_file(run_dir / "bio_ops_daily_full.json"),
                    routes._read_json_file(run_dir / "bio_ops_daily_summary.json"),
                )
            }
        )
        inbox_rows = _safe_call(lambda: inbox.scan(status="all"))
        import_rows = _safe_call(inbox.scan_import_candidates)
        queue_rows = _safe_call(consumer.scan_queue)
        state_rows = _safe_call(owner_state.scan_state)
        hint_rows = _safe_call(
            lambda: owner_state.scan_runtime_hints(active_only=False)
        )
        summary = routes._read_json_file(run_dir / "summary.json")
        generated = max(
            int(summary.get("generated_at_ms") or summary.get("timestamp") or 0),
            int(run_dir.stat().st_mtime * 1000),
        )
        adoptions = _bio_items(
            adoption_records, run_id=run_id, collection="adoptions"
        )
        payload = {
            "ok": True,
            "artifact_available": True,
            "generated_at_ms": generated,
            "run_id": run_id,
            "artifact_ref": f"bio-ops://{run_id}",
            "capability_count": int(summary.get("bio_ops_capability_count") or 0),
            "status_counts": {
                str(key): int(value or 0)
                for key, value in dict(summary.get("bio_ops_status_counts") or {}).items()
                if isinstance(value, (int, float))
            },
            "adoptions": adoptions,
            "owner_inbox": _bio_items(
                inbox_rows, run_id=run_id, collection="owner-inbox"
            ),
            "owner_inbox_status_counts": dict(inbox_rows.get("status_counts") or {}),
            "owner_import_candidates": _bio_items(
                import_rows, run_id=run_id, collection="owner-import-candidates"
            ),
            "owner_import_status_counts": dict(import_rows.get("status_counts") or {}),
            "owner_queues": _bio_items(
                queue_rows, run_id=run_id, collection="owner-queues"
            ),
            "owner_queue_status_counts": dict(queue_rows.get("status_counts") or {}),
            "owner_state": _bio_items(
                state_rows, run_id=run_id, collection="owner-state"
            ),
            "owner_state_status_counts": dict(state_rows.get("status_counts") or {}),
            "owner_state_review_status_counts": dict(
                state_rows.get("review_status_counts") or {}
            ),
            "owner_runtime_hints": _bio_items(
                hint_rows, run_id=run_id, collection="owner-runtime-hints"
            ),
            "owner_runtime_hint_status_counts": dict(
                hint_rows.get("status_counts") or {}
            ),
        }
        documents.append((run_id, payload, generated))
    return documents


def refresh_bio_ops_projection() -> dict[str, int]:
    from host_brain.app.obs_routes_agent_bio_ops import OBSERVABLE_EFFECTS_ROOT

    documents = load_bio_ops_projection_sources(Path(OBSERVABLE_EFFECTS_ROOT))
    if not documents:
        return {"document_count": 0}
    store = _store()
    for run_id, payload, observed in documents:
        current = store.get(BIO_OPS_KIND, run_id)
        cursor = _monotonic_source_cursor(current, payload, observed)
        store.upsert(
            BIO_OPS_KIND,
            run_id,
            payload,
            source_cursor=cursor,
            source_digest=digest_json(payload),
            source_ref=f"bio-ops-metadata-background-worker:{run_id}",
        )
    latest_run_id, latest_payload, latest_observed = documents[-1]
    latest = {**latest_payload, "latest_run_id": latest_run_id}
    current_latest = store.get(BIO_OPS_KIND, BIO_OPS_LATEST_KEY)
    latest_cursor = _monotonic_source_cursor(
        current_latest,
        latest,
        latest_observed,
    )
    store.upsert(
        BIO_OPS_KIND,
        BIO_OPS_LATEST_KEY,
        latest,
        source_cursor=latest_cursor,
        source_digest=digest_json(latest),
        source_ref="bio-ops-metadata-background-worker:latest",
    )
    return {"document_count": len(documents) + 1}


def run_insights_summary_projection_job() -> dict[str, int | bool]:
    return _run_if_due("insights-summary", 30.0, refresh_insights_summary_projection)


def run_digital_life_overview_projection_job() -> dict[str, int | bool]:
    return _run_if_due(
        "digital-life-overview", 60.0, refresh_digital_life_overview_projection
    )


def run_runtime_snapshot_projection_job() -> dict[str, int | bool]:
    return _run_if_due("runtime-snapshot", 15.0, refresh_runtime_snapshot_projection)


def run_life_body_projection_job() -> dict[str, int | bool]:
    return _run_if_due("life-body", 30.0, refresh_life_body_projection)


def run_resource_manager_projection_job() -> dict[str, int | bool]:
    return _run_if_due("resource-manager", 30.0, refresh_resource_manager_projection)


def run_bio_ops_projection_job() -> dict[str, int | bool]:
    return _run_if_due("bio-ops-metadata", 30.0, refresh_bio_ops_projection)


# Registration is import-driven, while source access remains callback-driven.
register_projection_job("insights-summary", run_insights_summary_projection_job)
register_projection_job(
    "digital-life-overview", run_digital_life_overview_projection_job
)
register_projection_job("runtime-snapshot", run_runtime_snapshot_projection_job)
register_projection_job("life-body", run_life_body_projection_job)
register_projection_job("resource-manager", run_resource_manager_projection_job)
register_projection_job("bio-ops-metadata", run_bio_ops_projection_job)


__all__ = [
    "BIO_OPS_KIND",
    "BIO_OPS_LATEST_KEY",
    "BIO_OPS_MAX_AGE_MS",
    "DIGITAL_LIFE_MAX_AGE_MS",
    "DIGITAL_LIFE_OVERVIEW_KIND",
    "INSIGHTS_SUMMARY_KIND",
    "LIFE_BODY_KIND",
    "LIFE_BODY_MAX_AGE_MS",
    "RESOURCE_MANAGER_KEY",
    "RESOURCE_MANAGER_KIND",
    "RESOURCE_MANAGER_MAX_AGE_MS",
    "RUNTIME_MAX_AGE_MS",
    "RUNTIME_SNAPSHOT_KEY",
    "RUNTIME_SNAPSHOT_KIND",
    "SUMMARY_MAX_AGE_MS",
    "complete_projection_meta",
    "load_bio_ops_projection_sources",
    "load_resource_manager_projection_source",
    "read_process_projection",
    "refresh_bio_ops_projection",
    "refresh_digital_life_overview_projection",
    "refresh_insights_summary_projection",
    "refresh_life_body_projection",
    "refresh_resource_manager_projection",
    "refresh_runtime_snapshot_projection",
    "run_bio_ops_projection_job",
    "run_digital_life_overview_projection_job",
    "run_insights_summary_projection_job",
    "run_life_body_projection_job",
    "run_resource_manager_projection_job",
    "run_runtime_snapshot_projection_job",
    "window_key",
]
