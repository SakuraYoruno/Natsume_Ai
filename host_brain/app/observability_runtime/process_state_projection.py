"""Background-only projections for process, file and aggregate OBS state.

The route handlers consuming these documents are intentionally boring: each
receives its application's :class:`QueryProjectionStore` explicitly and
shapes an empty response when a document is missing or stale.  Every live
manager lookup, process snapshot, ledger aggregation and filesystem scan stays
in the jobs in this module.

BioOps owner artifacts receive a stricter projection than the generic public
serializer.  Only identifiers, state metadata, logical references and a
digest of that metadata are durable; packet/candidate bodies and deployment
paths never enter the projection database.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

from natsume_contracts.operator.v1 import ProjectionDegradedState

from host_brain.contracts.resource_manager_status import (
    RESOURCE_MANAGER_STATUS_AGGREGATE_TYPE,
    RESOURCE_MANAGER_STATUS_EVENT_TYPE,
)
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

# Summary and Life Body are 60/360-minute aggregates produced by one serial
# 22-job wave.  A full real-data wave can take around 90 seconds, so their GET
# freshness window needs one complete-wave margin.  Canonical Runtime remains
# on its separate 45-second live contract below.
SUMMARY_MAX_AGE_MS = 180_000
DIGITAL_LIFE_MAX_AGE_MS = 180_000
RUNTIME_MAX_AGE_MS = 45_000
LIFE_BODY_MAX_AGE_MS = 180_000
RESOURCE_MANAGER_MAX_AGE_MS = 90_000
BIO_OPS_MAX_AGE_MS = 5 * 60_000

_cursor_lock = threading.Lock()
_last_cursor: dict[str, int] = {}


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
        # A healthy projection has no degradation reason.  Do not attach the
        # fallback NOT_READY code to an explicitly available document.
        "reason_code": (
            meta.get("reason_code")
            if bool(meta.get("available", False))
            else meta.get("reason_code") or "OPERATOR_PROJECTION_NOT_READY"
        ),
    }


def read_process_projection(
    store: QueryProjectionStore,
    kind: str,
    key: str,
    *,
    max_age_ms: int,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Read one durable document through an explicitly composed store."""

    document = None
    error_name: str | None = None
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


def _upsert(
    kind: str,
    key: str,
    payload: Mapping[str, Any],
    *,
    cursor: int,
    source_ref: str,
    degraded_state: ProjectionDegradedState = ProjectionDegradedState.HEALTHY,
    degraded_detail: str | None = None,
    store: QueryProjectionStore,
) -> None:
    store.upsert(
        kind,
        key,
        payload,
        source_cursor=cursor,
        source_ref=source_ref,
        degraded_state=degraded_state,
        degraded_detail=degraded_detail,
    )


def refresh_runtime_snapshot_projection(
    *,
    lifecycle_ports: Any,
    mode_transition_service: Any,
    store: QueryProjectionStore,
) -> dict[str, int]:
    """Read process/runtime managers outside ``GET /runtime-snapshot``."""

    from host_brain.app.obs_routes_sysmode import _build_runtime_snapshot_response

    value = _build_runtime_snapshot_response(
        "",
        lifecycle_ports=lifecycle_ports,
        mode_transition_service=mode_transition_service,
    )
    payload = value.model_dump() if hasattr(value, "model_dump") else value.dict()
    cursor = _next_cursor(RUNTIME_SNAPSHOT_KIND, int(payload.get("timestamp") or 0))
    _upsert(
        RUNTIME_SNAPSHOT_KIND,
        RUNTIME_SNAPSHOT_KEY,
        payload,
        cursor=cursor,
        source_ref="runtime-snapshot-background-worker",
        store=store,
    )
    return {"document_count": 1}


def load_resource_manager_projection_source(
    *,
    event_store: Any,
) -> tuple[dict[str, Any], int, str]:
    """Read the latest Host-fenced status head from the canonical journal."""

    from host_brain.app.obs_routes_core import _compact_resource_manager_status

    try:
        heads = event_store.list_latest_event_heads(
            aggregate_type=RESOURCE_MANAGER_STATUS_AGGREGATE_TYPE,
            event_type=RESOURCE_MANAGER_STATUS_EVENT_TYPE,
            limit=100,
        )
    except Exception as exc:
        return (
            {
                "status": "UNAVAILABLE",
                "source": "runtime_event_journal",
                "timestamp": 0,
                "running": False,
                "registered": False,
                "reason_code": "RESOURCE_MANAGER_STATUS_JOURNAL_UNAVAILABLE",
                "detail": type(exc).__name__,
                "status_ref": "resource-manager://status/current",
            },
            0,
            "runtime_event_journal",
        )
    if not heads:
        return (
            {
                "status": "UNAVAILABLE",
                "source": "runtime_event_journal",
                "timestamp": 0,
                "running": False,
                "registered": False,
                "reason_code": "RESOURCE_MANAGER_STATUS_NOT_RECORDED",
                "status_ref": "resource-manager://status/current",
            },
            0,
            "runtime_event_journal",
        )
    record = max(
        heads,
        key=lambda item: (
            int(item.get("cursor") or 0),
            int(item.get("aggregate_version") or 0),
        ),
    )
    durable = dict(record.get("payload") or {})
    raw = durable.get("status")
    if (
        not isinstance(raw, Mapping)
        or str(durable.get("runtime_scope_id") or "")
        != str(record.get("aggregate_id") or "")
        or int(durable.get("status_revision") or 0)
        != int(record.get("aggregate_version") or 0)
        or int(durable.get("observed_at_ms") or 0)
        != int(record.get("ts_ms") or 0)
    ):
        return (
            {
                "status": "UNAVAILABLE",
                "source": "runtime_event_journal",
                "timestamp": 0,
                "running": False,
                "registered": False,
                "reason_code": "RESOURCE_MANAGER_STATUS_DURABLE_DATA_INVALID",
                "status_ref": "resource-manager://status/current",
            },
            int(record.get("cursor") or 0),
            "runtime_event_journal",
        )
    observed_at_ms = int(durable["observed_at_ms"])
    cursor = int(record.get("cursor") or 0)
    payload = {
        "status": "OK",
        "source": "runtime_event_journal",
        "timestamp": observed_at_ms,
        **_compact_resource_manager_status(dict(raw)),
        "registered": True,
        "runtime_scope_id": str(durable["runtime_scope_id"]),
        "status_revision": int(durable["status_revision"]),
        "status_event_id": str(record.get("event_id") or ""),
        "status_payload_digest": str(record.get("payload_digest") or ""),
        "status_reason": str(durable.get("reason") or ""),
    }
    payload["status_ref"] = "resource-manager://status/current"
    return payload, cursor, "runtime_event_journal"


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

    import host_brain.app.obs_routes_agent_bio_ops as routes
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


def refresh_bio_ops_projection(
    *,
    store: QueryProjectionStore,
    root: Path,
) -> dict[str, int]:
    """Materialize BioOps metadata through explicit app-local ports only."""

    documents = load_bio_ops_projection_sources(Path(root))
    if not documents:
        return {"document_count": 0}
    for run_id, payload, observed in documents:
        current = store.get(BIO_OPS_KIND, run_id)
        cursor = _monotonic_source_cursor(current, payload, observed)
        store.upsert(
            BIO_OPS_KIND,
            run_id,
            payload,
            source_cursor=cursor,
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
        source_ref="bio-ops-metadata-background-worker:latest",
    )
    return {"document_count": len(documents) + 1}


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
    "refresh_runtime_snapshot_projection",
    "window_key",
]
