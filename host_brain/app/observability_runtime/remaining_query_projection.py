"""Background projections for the remaining AW07 operator GET surfaces.

Only the registered jobs touch ledger, canon stores or files.  Route handlers
consume the generic ``QueryProjectionStore`` documents through ``read``.
Private goal/canon/checkpoint bodies are deliberately never materialized.
"""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from natsume_contracts.operator.v1 import ProjectionDegradedState

from .projection_runtime import projection_meta
from host_brain.canon.application import CanonApplicationService
from host_brain.observability.projection_store import QueryProjectionStore


AGENT_LOOP_KIND = "agent_loop_summary"


def _json_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        decoded = json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return dict(decoded) if isinstance(decoded, Mapping) else {}
AGENT_LOOP_INDEX_KEY = "index"
RESIDENT_CONTEXT_KIND = "resident_context"
RESIDENT_CONTEXT_INDEX_KEY = "index"
CHECKPOINT_KIND = "checkpoint"
CHECKPOINT_INDEX_KIND = "checkpoint_index"
CHECKPOINT_INDEX_KEY = "index"
WORLD_SNAPSHOT_KIND = "world_snapshot"
MEMORY_CONTEXT_KIND = "memory_context_debug"
MEMORY_HEALTH_KIND = "memory_health"
MEMORY_EVENTS_KIND = "memory_events"
CANON_SYNC_KIND = "canon_sync_health"
CANON_SYNC_KEY = "current"
STARTUP_SNAPSHOT_KIND = "startup_snapshot"
STARTUP_SNAPSHOT_KEY = "current"
REMAINING_QUERY_MAX_AGE_MS = 15_000
# Canon drift is a low-frequency health fact refreshed by one job in the
# 22-job serial OBS wave.  A 15-second read window can expire while that same
# healthy wave is still running, turning an unchanged ``OK`` result into a
# false ``STALE``.  Keep the generic interactive views at 15 seconds and give
# only canon health enough time to span a complete production wave.
CANON_SYNC_MAX_AGE_MS = 180_000


def read_from_store(
    store: QueryProjectionStore,
    kind: str,
    key: str,
    *,
    max_age_ms: int = REMAINING_QUERY_MAX_AGE_MS,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Read one materialized document through an explicit app-local store."""

    document = store.get(kind, key)
    meta = projection_meta(kind, key, document, max_age_ms=max_age_ms)
    if document is None or not meta["available"]:
        return None, meta
    return dict(document.payload), meta


def response_meta(meta: Mapping[str, Any]) -> dict[str, Any]:
    """Stable flat metadata shared by legacy response DTOs."""

    return {
        "schema_version": str(
            meta.get("schema_version") or "Natsume.operator.query_projection.v1"
        ),
        "projection_version": int(meta.get("projection_version") or 0),
        "cursor": int(meta.get("source_cursor") or 0),
        "freshness_ts_ms": int(meta.get("freshness_ts_ms") or 0),
        "degraded_state": str(meta.get("degraded_state") or "REBUILDING"),
    }


def read_all_from_store(
    store: QueryProjectionStore,
    kind: str,
    *,
    max_age_ms: int = REMAINING_QUERY_MAX_AGE_MS,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read every fresh document through an explicit app-local store."""

    documents = list(store.list(kind))
    available: list[tuple[Any, dict[str, Any]]] = []
    for document in documents:
        meta = projection_meta(
            kind,
            str(document.key),
            document,
            max_age_ms=max_age_ms,
        )
        if meta["available"]:
            available.append((document, meta))
    if available:
        newest = max(available, key=lambda item: int(item[1].get("freshness_ts_ms") or 0))
        meta = dict(newest[1])
        meta["projection_version"] = max(
            int(item[1].get("projection_version") or 0) for item in available
        )
        meta["source_cursor"] = max(
            int(item[1].get("source_cursor") or 0) for item in available
        )
        return [dict(item[0].payload) for item in available], meta
    # Preserve stale version/cursor metadata when documents exist, while never
    # exposing stale payloads.
    if documents:
        document = max(documents, key=lambda item: int(item.freshness_ts_ms))
        return [], projection_meta(
            kind,
            str(document.key),
            document,
            max_age_ms=max_age_ms,
        )
    return [], projection_meta(kind, "index", None, max_age_ms=max_age_ms)


def _fetch_rows(ledger: Any, *tables: str) -> list[dict[str, Any]]:
    now = int(time.time() * 1000)
    resolver = getattr(ledger, "resolve_fetch_table", None)
    for candidate in tables:
        table = resolver(candidate) if callable(resolver) else candidate
        if not table:
            continue
        try:
            rows = ledger.fetch_by_time_range(table, start_ms=0, end_ms=now)
        except Exception:
            continue
        if rows:
            return [dict(row) for row in rows if isinstance(row, Mapping)]
    return []


def _agent_loop_payload(ledger: Any) -> tuple[dict[str, Any], int]:
    rows = _fetch_rows(ledger, "agent_loop_log")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        request_id = str(row.get("request_id") or "").strip()
        if request_id:
            grouped[request_id].append(row)

    loops: list[dict[str, Any]] = []
    for request_id, request_rows in grouped.items():
        request_rows.sort(key=lambda row: int(row.get("ts_ms") or 0))
        terminal = [
            row
            for row in request_rows
            if str(row.get("phase") or "").upper()
            in {"LOOP_END", "LOOP_SUCCESS", "LOOP_FAILED"}
        ]
        summary = terminal[-1] if terminal else request_rows[-1]
        loops.append(
            {
                "request_id": request_id,
                "session_id": summary.get("session_id"),
                # Goal text is private task content and is never projected.
                "goal": None,
                "total_rounds": max(
                    (int(row.get("round") or 0) for row in request_rows), default=0
                ),
                "goal_achieved": bool(summary.get("goal_achieved")),
                "total_actions": sum(
                    int(row.get("action_count") or 0) for row in request_rows
                ),
                "success_actions": sum(
                    int(row.get("success_count") or 0) for row in request_rows
                ),
                "failed_actions": sum(
                    int(row.get("failed_count") or 0) for row in request_rows
                ),
                "total_latency_ms": sum(
                    int(row.get("latency_ms") or 0) for row in request_rows
                )
                or None,
                "ts_ms": int(summary.get("ts_ms") or 0),
            }
        )

    history_rows = _fetch_rows(ledger, "audit_log")
    history: list[dict[str, Any]] = []
    for row in history_rows:
        if str(row.get("stage") or "").upper() != "LOOP_DONE":
            continue
        raw_detail = row.get("detail")
        try:
            detail = json.loads(raw_detail) if isinstance(raw_detail, str) else {}
        except (TypeError, ValueError):
            detail = {}
        detail = detail if isinstance(detail, Mapping) else {}
        history.append(
            {
                "request_id": str(row.get("request_id") or ""),
                "goal": None,
                "final_status": detail.get("final_status") or row.get("final_status"),
                "goal_achieved": detail.get("goal_achieved"),
                "total_rounds": detail.get("total_rounds"),
                "total_latency_ms": detail.get("total_latency_ms"),
                "ts_ms": int(row.get("ts_ms") or 0),
                "client_mode": detail.get("client_mode"),
            }
        )
    loops.sort(key=lambda item: int(item.get("ts_ms") or 0), reverse=True)
    history.sort(key=lambda item: int(item.get("ts_ms") or 0), reverse=True)
    cursor = max(
        [int(item.get("ts_ms") or 0) for item in loops + history], default=0
    )
    return {"loops": loops, "history": history, "timestamp": cursor}, cursor


def _resident_context_payload(ledger: Any) -> tuple[dict[str, Any], int]:
    world_root = str(
        os.environ.get("Natsume_WORLD_CANON_ROOT") or "host_brain/canon/world"
    ).strip()
    character_path = str(
        os.environ.get("NATSUME_CHARACTER_PROFILE_PATH")
        or os.path.join("host_brain", "canon", "character", "character_profile.json")
    ).strip()
    world_documents: list[dict[str, Any]] = []
    try:
        from host_brain.memory.canon_world_store import WorldCanonStore

        for document in WorldCanonStore(world_root=world_root, ledger=ledger).list_documents():
            doc_id = str(getattr(document, "doc_id", "") or "").strip()
            world_documents.append(
                {
                    "kind": "world",
                    "doc_id": doc_id,
                    "title": str(getattr(document, "title", "") or "").strip() or None,
                    # Filesystem paths are private deployment metadata.  The
                    # public projection exposes only a stable logical ref.
                    "source_ref": f"canon://world/{doc_id}" if doc_id else None,
                    "version": int(getattr(document, "version", 0) or 0) or None,
                    "status": str(getattr(document, "status", "") or "").strip() or None,
                    "updated_by": str(getattr(document, "updated_by", "") or "").strip()
                    or None,
                    "ts_ms": int(getattr(document, "ts_ms", 0) or 0) or None,
                    # Canon body/preview is intentionally absent.
                }
            )
    except Exception:
        world_documents = []

    persona_documents: list[dict[str, Any]] = []
    try:
        with open(character_path, "r", encoding="utf-8-sig") as stream:
            value = json.load(stream)
        value = value if isinstance(value, Mapping) else {}
        modified_ms = int(os.path.getmtime(character_path) * 1000)
        persona_documents.append(
            {
                "kind": "persona",
                "doc_id": str(value.get("doc_id") or "character-profile"),
                "title": str(value.get("title") or "角色人设常驻文件"),
                "source_ref": "canon://character/profile",
                "version": int(value.get("version") or 0) or None,
                "status": str(value.get("status") or "ACTIVE"),
                "updated_by": "character_profile",
                "ts_ms": modified_ms,
                # instruction_cn and every other body field are excluded.
            }
        )
    except Exception:
        persona_documents = []
    cursor = max(
        (
            int(document.get("ts_ms") or 0)
            for document in world_documents + persona_documents
        ),
        default=0,
    )
    return {
        "world_canon_documents": world_documents,
        "persona_resident_documents": persona_documents,
        "timestamp": cursor,
    }, cursor


def _checkpoint_documents(ledger: Any) -> tuple[list[dict[str, Any]], int]:
    rows = _fetch_rows(ledger, "checkpoints", "graph_checkpoint")
    documents: list[dict[str, Any]] = []
    for row in rows:
        checkpoint_id = str(row.get("checkpoint_id") or row.get("id") or "").strip()
        if not checkpoint_id:
            continue
        deadline = int(row.get("resume_deadline_at") or 0) or None
        created = int(row.get("created_at_ms") or row.get("ts_ms") or 0)
        documents.append(
            {
                "checkpoint_id": checkpoint_id,
                "request_id": row.get("request_id"),
                "interrupt_reason": row.get("interrupt_reason"),
                "resume_deadline_at": deadline,
                "created_at_ms": created,
                # Checkpoint payload and summaries derived from it never enter
                # the operator projection database.
            }
        )
    documents.sort(key=lambda item: int(item.get("created_at_ms") or 0), reverse=True)
    cursor = max(
        (int(document.get("created_at_ms") or 0) for document in documents), default=0
    )
    return documents, cursor


def _memory_payloads(ledger: Any) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], int]:
    from host_brain.app.context.memory_context_orchestrator import (
        build_memory_context_debug,
        memory_health,
    )
    from host_brain.persistence.context_memory_repository import MemoryEventRepository

    now = int(time.time() * 1000)
    debug = build_memory_context_debug(
        ledger=ledger,
        person_id="default",
        session_id="",
        user_message="",
        limit=30,
    )
    # The operator debug projection is structural diagnostics only.  It never
    # persists messages, semantic summaries, selected context, or suppressed
    # item bodies.
    selected_events = debug.get("selected_events")
    selected_events = selected_events if isinstance(selected_events, Mapping) else {}
    binding = debug.get("binding_decision")
    binding = binding if isinstance(binding, Mapping) else {}
    context_payload = {
        "person_id": "default",
        "session_id": "",
        "layer_order": [str(item) for item in list(debug.get("layer_order") or [])],
        "reason_codes": [str(item) for item in list(debug.get("reason_codes") or [])],
        "binding": {
            "binding_kind": binding.get("binding_kind"),
            "reason_code": binding.get("reason_code"),
            "bound_event_id": binding.get("bound_event_id"),
        },
        "counts": {
            "recent_visible": len(list(selected_events.get("recent_visible") or [])),
            "memory_evidence": len(list(debug.get("memory_evidence") or [])),
            "suppressed": len(list(debug.get("suppressed_items") or [])),
        },
        "projection_status": dict(debug.get("projection_status") or {}),
        "generated_at_ms": now,
        "content_withheld": True,
    }
    health_payload = dict(memory_health(ledger=ledger, person_id="default"))
    health_payload.update({"person_id": "default", "generated_at_ms": now})
    raw_events = MemoryEventRepository.from_ledger(ledger).list_events(
        person_id="default",
        session_id="",
        visibility="",
        limit=200,
    )
    events: list[dict[str, Any]] = []
    for item in raw_events:
        if not isinstance(item, Mapping):
            continue
        events.append(
            {
                "event_id": item.get("event_id"),
                "person_id": item.get("person_id"),
                "session_id": item.get("session_id"),
                "conversation_id": item.get("conversation_id"),
                "source_kind": item.get("source_kind"),
                "event_kind": item.get("event_kind"),
                "visibility": item.get("visibility"),
                "role": item.get("role"),
                "salience": item.get("salience"),
                "valence": item.get("valence"),
                "uncertainty": item.get("uncertainty"),
                "confidence": item.get("confidence"),
                "projection_status": _json_mapping(item.get("projection_status_json")),
                "created_at_ms": int(item.get("created_at_ms") or 0),
                "active": bool(item.get("active")),
                "content_withheld": True,
            }
        )
    events_payload = {
        "person_id": "default",
        "items": events,
        "generated_at_ms": now,
        "content_withheld": True,
    }
    cursor = max([now] + [int(item.get("created_at_ms") or 0) for item in events])
    return context_payload, health_payload, events_payload, cursor


def _canon_sync_payload(service: CanonApplicationService) -> tuple[dict[str, Any], int]:
    from host_brain.memory.canon_sync import detect_world_version_drift

    now = int(time.time() * 1000)
    try:
        drifts = detect_world_version_drift(service=service)
        samples = []
        for raw in list(drifts)[:5]:
            row = dict(raw) if isinstance(raw, Mapping) else {}
            samples.append(
                {
                    "doc_id": row.get("doc_id"),
                    "file_version": row.get("file_version"),
                    "indexed_version": row.get("indexed_version"),
                    "reason_code": row.get("reason_code") or row.get("code"),
                    "source_name": (
                        Path(str(row.get("source_path") or "")).name or None
                    ),
                }
            )
        payload = {
            "status": "DEGRADED" if drifts else "OK",
            "source_available": True,
            "drift_count": len(drifts),
            "drift_sample": samples,
            "reason_code": "CANON_SYNC_FAILED" if drifts else None,
            "detail": "world canon version drift detected" if drifts else "canon sync healthy",
            "generated_at_ms": now,
        }
    except Exception:
        payload = {
            "status": "DEGRADED",
            "source_available": False,
            "drift_count": 0,
            "drift_sample": [],
            "reason_code": "CANON_SYNC_FAILED",
            "detail": "canon sync source unavailable",
            "generated_at_ms": now,
        }
    return payload, now


__all__ = [
    "AGENT_LOOP_INDEX_KEY",
    "AGENT_LOOP_KIND",
    "CHECKPOINT_INDEX_KEY",
    "CHECKPOINT_INDEX_KIND",
    "CHECKPOINT_KIND",
    "CANON_SYNC_KEY",
    "CANON_SYNC_KIND",
    "CANON_SYNC_MAX_AGE_MS",
    "MEMORY_CONTEXT_KIND",
    "MEMORY_EVENTS_KIND",
    "MEMORY_HEALTH_KIND",
    "REMAINING_QUERY_MAX_AGE_MS",
    "RESIDENT_CONTEXT_INDEX_KEY",
    "RESIDENT_CONTEXT_KIND",
    "STARTUP_SNAPSHOT_KEY",
    "STARTUP_SNAPSHOT_KIND",
    "WORLD_SNAPSHOT_KIND",
    "read_from_store",
    "read_all_from_store",
    "response_meta",
]
