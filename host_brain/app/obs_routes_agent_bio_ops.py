"""Codex Runtime v2 and BioOps observability endpoint."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Query, Request

from host_brain.app.obs_route_ports import require_obs_query_projection_store
from host_brain.app.obs_routes_core import _as_dict, _as_list, router
from host_brain.agent_runtime.artifact_store import sanitize_segment
from host_brain.bio_ops.adoption import ADOPTION_STATUS_PENDING_REVIEW
from host_brain.core.ids import now_ms as _now_ms
from host_brain.observability.projection_store import QueryProjectionStore
from host_brain.app.observability_runtime.process_state_projection import (
    BIO_OPS_KIND,
    BIO_OPS_LATEST_KEY,
    BIO_OPS_MAX_AGE_MS,
    read_process_projection,
)


MAX_JSON_BYTES = 5 * 1024 * 1024

ADOPTION_LIST_STATUSES = {"pending", "decided", "all"}


def _read_bio_ops_metadata(
    store: QueryProjectionStore,
    run_id: str | None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    key = str(run_id or "").strip() or BIO_OPS_LATEST_KEY
    if any(token in key for token in ("..", "/", "\\")):
        key = "invalid"
    return read_process_projection(
        store,
        BIO_OPS_KIND,
        key,
        max_age_ms=BIO_OPS_MAX_AGE_MS,
    )


def _filter_owner_metadata(
    items: Any,
    *,
    owner_organ: str | None = None,
) -> list[dict[str, Any]]:
    owner = str(owner_organ or "").strip()
    return [
        {
            **dict(item),
            **(
                {"output_kind": item.get("artifact_kind")}
                if item.get("artifact_kind") is not None
                else {}
            ),
        }
        for item in _as_list(items)
        if isinstance(item, dict)
        and (not owner or str(item.get("owner_organ") or "") == owner)
    ]


def _text_list(value: Any, *, limit: int | None = None) -> list[str]:
    items = [str(item).strip() for item in _as_list(value) if str(item).strip()]
    return items[:limit] if limit is not None else items


def _read_json_file(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    try:
        if path.stat().st_size > MAX_JSON_BYTES:
            return {"_error": "json_file_too_large", "path": str(path)}
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_error": "json_read_failed", "path": str(path), "detail": str(exc)}
    return _as_dict(payload)


def _adoption_from_result(result: dict[str, Any]) -> dict[str, Any]:
    adoption = _as_dict(result.get("adoption"))
    if not adoption:
        adoption = _as_dict(_as_dict(result.get("metadata")).get("bio_ops_adoption"))
    return adoption


def _candidate_id(candidate: dict[str, Any]) -> str:
    return str(candidate.get("candidate_id") or "").strip()


def _decision_path_for(
    run_dir: Path,
    candidate_id: str,
    source_path: Path | None = None,
) -> Path:
    if source_path is not None:
        return source_path.parent / "adoption_decision.json"
    return (
        run_dir
        / "adoption_decisions"
        / sanitize_segment(candidate_id, "candidate")
        / "adoption_decision.json"
    )


def _read_decision(path: Path) -> dict[str, Any]:
    payload = _read_json_file(path)
    if payload and not payload.get("_error"):
        return payload
    return {}


def _compact_adoption_record(
    *,
    run_dir: Path,
    candidate: dict[str, Any],
    source_path: Path | None = None,
    result: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    candidate = _as_dict(candidate)
    candidate_id = _candidate_id(candidate)
    if not candidate_id:
        return None

    result = _as_dict(result)
    decision_path = _decision_path_for(run_dir, candidate_id, source_path)
    decision = _read_decision(decision_path)
    decided = bool(decision)
    status = str(
        decision.get("decision")
        or candidate.get("status")
        or ADOPTION_STATUS_PENDING_REVIEW
    )
    source = {
        "run_id": run_dir.name,
        "candidate_path": str(source_path) if source_path else None,
        "result_work_order_id": result.get("work_order_id"),
        "result_capability": result.get("capability"),
    }
    return {
        "candidate_id": candidate_id,
        "task_id": candidate.get("task_id"),
        "capability": candidate.get("capability") or result.get("capability"),
        "owner_organ": candidate.get("owner_organ"),
        "output_kind": candidate.get("output_kind"),
        "summary": candidate.get("summary") or result.get("summary"),
        "payload": _as_dict(candidate.get("payload")),
        "evidence_refs": _text_list(candidate.get("evidence_refs"), limit=20),
        "artifacts": _as_list(candidate.get("artifacts")),
        "risk_flags": _text_list(candidate.get("risk_flags"), limit=20),
        "review_required": bool(candidate.get("review_required", True)),
        "next_actions": _text_list(candidate.get("next_actions"), limit=10),
        "created_at_ms": candidate.get("created_at_ms"),
        "status": status,
        "pending": (not decided) and status == ADOPTION_STATUS_PENDING_REVIEW,
        "decision": decision or None,
        "decision_artifact_path": str(decision_path),
        "source": source,
        "direct_long_term_write": False,
        "write_policy": "owner_decision_artifact_only",
    }


def _collect_adoption_records(
    run_dir: Path,
    *payloads: dict[str, Any],
) -> list[dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}

    for path in sorted(run_dir.rglob("adoption_candidate.json")):
        candidate = _read_json_file(path)
        record = _compact_adoption_record(
            run_dir=run_dir,
            candidate=candidate,
            source_path=path,
        )
        if record:
            records[record["candidate_id"]] = record

    for payload in payloads:
        if not payload:
            continue
        for collection_name in ("results", "sample_capabilities"):
            for result in _as_list(payload.get(collection_name)):
                if not isinstance(result, dict):
                    continue
                candidate = _as_dict(
                    _adoption_from_result(result).get("candidate")
                )
                record = _compact_adoption_record(
                    run_dir=run_dir,
                    candidate=candidate,
                    result=result,
                )
                if not record:
                    continue
                existing = records.get(record["candidate_id"])
                if not existing:
                    records[record["candidate_id"]] = record
                elif not existing["source"].get("result_capability"):
                    existing["source"].update(record["source"])

    return sorted(
        records.values(),
        key=lambda item: (
            int(item.get("created_at_ms") or 0),
            str(item.get("candidate_id") or ""),
        ),
        reverse=True,
    )


def _filter_adoption_records(records: list[dict[str, Any]], status: str) -> list[dict[str, Any]]:
    normalized = str(status or "pending").strip().lower()
    if normalized not in ADOPTION_LIST_STATUSES:
        raise HTTPException(status_code=400, detail="status must be pending, decided, or all")
    if normalized == "all":
        return records
    if normalized == "decided":
        return [record for record in records if bool(record.get("decision"))]
    return [record for record in records if bool(record.get("pending"))]


def _find_adoption_record(records: list[dict[str, Any]], candidate_id: str) -> dict[str, Any]:
    normalized = str(candidate_id or "").strip()
    for record in records:
        if str(record.get("candidate_id") or "") == normalized:
            return record
    raise HTTPException(status_code=404, detail="BioOps adoption candidate not found")


@router.get("/codex-bio-ops")
def get_codex_bio_ops_observability(
    request: Request,
    run_id: str | None = Query(default=None),
) -> dict[str, Any]:
    payload, projection = _read_bio_ops_metadata(
        require_obs_query_projection_store(request),
        run_id,
    )
    if payload is None:
        return {
            "ok": False,
            "artifact_available": False,
            "generated_at_ms": _now_ms(),
            "run_id": None,
            "artifact_dir": None,
            "artifact_ref": None,
            "benefits": [],
            "codex_runtime": {},
            "bio_ops": {
                "capability_count": 0,
                "status_counts": {},
                "pending_adoptions": [],
            },
            "runtime_traces": [],
            "artifacts": [],
            "message": "BioOps metadata projection is not ready.",
            "projection": projection,
        }
    adoptions = _filter_owner_metadata(payload.get("adoptions"))
    return {
        "ok": bool(payload.get("ok", True)),
        "artifact_available": bool(payload.get("artifact_available", True)),
        "generated_at_ms": int(payload.get("generated_at_ms") or _now_ms()),
        "run_id": payload.get("run_id"),
        "artifact_dir": None,
        "artifact_ref": payload.get("artifact_ref"),
        "benefits": [],
        "codex_runtime": {},
        "bio_ops": {
            "capability_count": int(payload.get("capability_count") or 0),
            "status_counts": _as_dict(payload.get("status_counts")),
            "pending_adoptions": [item for item in adoptions if bool(item.get("pending"))],
        },
        "runtime_traces": [],
        "artifacts": [],
        "projection": projection,
    }


@router.get("/codex-bio-ops/adoptions")
def list_codex_bio_ops_adoptions(
    request: Request,
    run_id: str | None = Query(default=None),
    status: str = Query(default="pending"),
) -> dict[str, Any]:
    payload, projection = _read_bio_ops_metadata(
        require_obs_query_projection_store(request),
        run_id,
    )
    records = _filter_owner_metadata((payload or {}).get("adoptions"))
    filtered = _filter_adoption_records(records, status)
    return {
        "ok": payload is not None,
        "run_id": (payload or {}).get("run_id"),
        "artifact_dir": None,
        "artifact_ref": (payload or {}).get("artifact_ref"),
        "status": status,
        "count": len(filtered),
        "adoptions": filtered,
        "projection": projection,
    }


@router.get("/codex-bio-ops/adoptions/{candidate_id}")
def get_codex_bio_ops_adoption(
    request: Request,
    candidate_id: str,
    run_id: str | None = Query(default=None),
) -> dict[str, Any]:
    payload, projection = _read_bio_ops_metadata(
        require_obs_query_projection_store(request),
        run_id,
    )
    records = _filter_owner_metadata((payload or {}).get("adoptions"))
    record = _find_adoption_record(records, candidate_id)
    return {
        "ok": True,
        "run_id": (payload or {}).get("run_id"),
        "artifact_dir": None,
        "artifact_ref": (payload or {}).get("artifact_ref"),
        "adoption": record,
        "projection": projection,
    }


@router.get("/codex-bio-ops/owner-inbox")
def get_codex_bio_ops_owner_inbox(
    request: Request,
    run_id: str | None = Query(default=None),
    owner_organ: str | None = Query(default=None),
    status: str = Query(default="all"),
) -> dict[str, Any]:
    normalized_status = str(status or "all").strip().upper()
    valid_statuses = {"ALL", "PENDING_REVIEW", "READY_FOR_OWNER_IMPORT", "REJECTED_BY_OWNER", "BLOCKED"}
    if normalized_status not in valid_statuses:
        raise HTTPException(status_code=400, detail="invalid owner inbox status")
    payload, projection = _read_bio_ops_metadata(
        require_obs_query_projection_store(request),
        run_id,
    )
    items = _filter_owner_metadata((payload or {}).get("owner_inbox"), owner_organ=owner_organ)
    if normalized_status != "ALL":
        items = [item for item in items if str(item.get("status") or "").upper() == normalized_status]
    status_counts: dict[str, int] = {}
    for item in items:
        item_status = str(item.get("status") or "UNKNOWN")
        status_counts[item_status] = status_counts.get(item_status, 0) + 1
    return {
        "ok": payload is not None,
        "run_id": (payload or {}).get("run_id"),
        "artifact_ref": (payload or {}).get("artifact_ref"),
        "owner_organ": str(owner_organ or "").strip() or None,
        "status": normalized_status,
        "count": len(items),
        "status_counts": status_counts,
        "items": items,
        "projection": projection,
    }


@router.get("/codex-bio-ops/owner-inbox/import-candidates")
def get_codex_bio_ops_owner_import_candidates(
    request: Request,
    run_id: str | None = Query(default=None),
    owner_organ: str | None = Query(default=None),
) -> dict[str, Any]:
    payload, projection = _read_bio_ops_metadata(
        require_obs_query_projection_store(request),
        run_id,
    )
    items = _filter_owner_metadata((payload or {}).get("owner_import_candidates"), owner_organ=owner_organ)
    return {
        "ok": payload is not None,
        "run_id": (payload or {}).get("run_id"),
        "artifact_ref": (payload or {}).get("artifact_ref"),
        "owner_organ": str(owner_organ or "").strip() or None,
        "count": len(items),
        "status_counts": _as_dict((payload or {}).get("owner_import_status_counts")),
        "items": items,
        "projection": projection,
    }


@router.get("/codex-bio-ops/owner-queues")
def get_codex_bio_ops_owner_queues(
    request: Request,
    run_id: str | None = Query(default=None),
    owner_organ: str | None = Query(default=None),
) -> dict[str, Any]:
    payload, projection = _read_bio_ops_metadata(
        require_obs_query_projection_store(request),
        run_id,
    )
    items = _filter_owner_metadata((payload or {}).get("owner_queues"), owner_organ=owner_organ)
    return {
        "ok": payload is not None,
        "run_id": (payload or {}).get("run_id"),
        "artifact_ref": (payload or {}).get("artifact_ref"),
        "owner_organ": str(owner_organ or "").strip() or None,
        "count": len(items),
        "status_counts": _as_dict((payload or {}).get("owner_queue_status_counts")),
        "items": items,
        "projection": projection,
    }


@router.get("/codex-bio-ops/owner-state")
def get_codex_bio_ops_owner_state(
    request: Request,
    run_id: str | None = Query(default=None),
    owner_organ: str | None = Query(default=None),
) -> dict[str, Any]:
    payload, projection = _read_bio_ops_metadata(
        require_obs_query_projection_store(request),
        run_id,
    )
    items = _filter_owner_metadata((payload or {}).get("owner_state"), owner_organ=owner_organ)
    return {
        "ok": payload is not None,
        "run_id": (payload or {}).get("run_id"),
        "artifact_ref": (payload or {}).get("artifact_ref"),
        "owner_organ": str(owner_organ or "").strip() or None,
        "count": len(items),
        "status_counts": _as_dict((payload or {}).get("owner_state_status_counts")),
        "review_status_counts": _as_dict((payload or {}).get("owner_state_review_status_counts")),
        "items": items,
        "direct_long_term_write": False,
        "durable_write_performed": False,
        "durable_memory_write_performed": False,
        "projection": projection,
    }


@router.get("/codex-bio-ops/owner-state/runtime-hints")
def get_codex_bio_ops_owner_runtime_hints(
    request: Request,
    run_id: str | None = Query(default=None),
    owner_organ: str | None = Query(default=None),
    active_only: bool = Query(default=True),
) -> dict[str, Any]:
    payload, projection = _read_bio_ops_metadata(
        require_obs_query_projection_store(request),
        run_id,
    )
    items = _filter_owner_metadata((payload or {}).get("owner_runtime_hints"), owner_organ=owner_organ)
    if active_only:
        items = [item for item in items if str(item.get("status") or "").upper() == "ACTIVE"]
    return {
        "ok": payload is not None,
        "run_id": (payload or {}).get("run_id"),
        "artifact_ref": (payload or {}).get("artifact_ref"),
        "owner_organ": str(owner_organ or "").strip() or None,
        "active_only": bool(active_only),
        "count": len(items),
        "status_counts": _as_dict((payload or {}).get("owner_runtime_hint_status_counts")),
        "items": items,
        "direct_long_term_write": False,
        "durable_write_performed": False,
        "durable_memory_write_performed": False,
        "projection": projection,
    }
