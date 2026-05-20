"""Async enqueue executor for Codex production skills."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import threading
from typing import Any, Mapping

from host_brain.codex_runtime.artifact_store import CodexArtifactStore
from host_brain.codex_runtime.executor import CodexRuntimeExecutor
from host_brain.codex_runtime.production_studio import build_persona_boundary_contract
from host_brain.codex_runtime.result_contract import CodexRuntimeResult
from host_brain.codex_runtime.work_order import CodexWorkOrder
from host_brain.core.ids import new_event_id, now_ms


STATUS_QUEUED = "QUEUED"
STATUS_RUNNING = "RUNNING"
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"


@dataclass(slots=True)
class CodexProductionJob:
    job_id: str
    work_order_id: str
    skill_id: str
    status: str
    artifacts_dir: str
    result_contract_ref: str
    request_id: str | None = None
    detail: str = ""
    result: dict[str, Any] | None = None
    error: str | None = None
    created_ts_ms: int = field(default_factory=now_ms)
    updated_ts_ms: int = field(default_factory=now_ms)
    finished_ts_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_JOBS_LOCK = threading.RLock()
_JOBS: dict[str, CodexProductionJob] = {}


def _result_contract(
    *,
    status: str,
    order: CodexWorkOrder,
    run_dir: Path,
    detail: str = "",
    error: str | None = None,
    result: Mapping[str, Any] | None = None,
) -> CodexRuntimeResult:
    skill_id = str(order.metadata.get("skill_id") or "").strip()
    metadata = {
        "skill_id": skill_id,
        "codex_production": True,
        "codex_background": True,
        "surface_main_chain_required": True,
        "persona_boundary": dict(order.metadata.get("persona_boundary") or build_persona_boundary_contract()),
        "result_contract": dict(order.metadata.get("result_contract") or {}),
        "trigger_policy": dict(order.metadata.get("trigger_policy") or {}),
    }
    if result:
        metadata["worker_result_status"] = str(result.get("status") or "")
        metadata["worker_reason_code"] = result.get("reason_code")
    return CodexRuntimeResult(
        status=status,
        work_order_id=order.work_order_id,
        summary=f"Codex production WorkOrder {status.lower()}.",
        result_text="",
        artifacts_dir=str(run_dir),
        verification={"status": "NOT_RUN", "reason": "background production queue"},
        risk_flags=[] if not error else ["codex_production_error"],
        review_required=False,
        detail=detail or None,
        reason_code=None if not error else "CODEX_PRODUCTION_ERROR",
        metadata=metadata,
    )


def _persist_job(store: CodexArtifactStore, run_dir: Path, job: CodexProductionJob) -> None:
    store.write_json(run_dir / "production_job.json", job.to_dict())


def _set_job(job_id: str, **fields: Any) -> CodexProductionJob | None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return None
        for name, value in fields.items():
            if hasattr(job, name):
                setattr(job, name, value)
        job.updated_ts_ms = now_ms()
        if job.status in {STATUS_COMPLETED, STATUS_FAILED} and job.finished_ts_ms is None:
            job.finished_ts_ms = job.updated_ts_ms
        return CodexProductionJob(**job.to_dict())


def get_codex_production_job(job_id: str) -> dict[str, Any] | None:
    with _JOBS_LOCK:
        job = _JOBS.get(str(job_id or "").strip())
        return None if job is None else job.to_dict()


def list_codex_production_jobs(limit: int = 20) -> list[dict[str, Any]]:
    with _JOBS_LOCK:
        jobs = [job.to_dict() for job in _JOBS.values()]
    jobs.sort(key=lambda item: int(item.get("updated_ts_ms") or 0), reverse=True)
    return jobs[: max(1, int(limit))]


def _validate_production_boundary(order: CodexWorkOrder) -> None:
    metadata = dict(order.metadata or {})
    if not bool(metadata.get("codex_production")):
        raise ValueError("codex production executor requires metadata.codex_production=true")
    boundary = metadata.get("persona_boundary")
    if not isinstance(boundary, Mapping):
        raise ValueError("codex production WorkOrder requires metadata.persona_boundary")
    if str(boundary.get("surface_owner") or "") != "host_main_persona":
        raise ValueError("persona_boundary.surface_owner must be host_main_persona")
    if not bool(metadata.get("surface_main_chain_required")):
        raise ValueError("codex production WorkOrder must require surface main chain rendering")
    forbidden = set(order.forbidden_actions or [])
    for action in {
        "write_long_term_memory",
        "write_bond_state",
        "write_persona_core",
        "send_external_message",
    }:
        if action not in forbidden:
            raise ValueError(f"codex production WorkOrder must forbid {action}")


def _run_background_job(
    *,
    job_id: str,
    order: CodexWorkOrder,
    store: CodexArtifactStore,
    run_dir: Path,
) -> None:
    running = _set_job(job_id, status=STATUS_RUNNING, detail="Codex production worker running.")
    if running is not None:
        _persist_job(store, run_dir, running)
        store.write_json(
            run_dir / "result_contract.json",
            _result_contract(status="RUNNING", order=order, run_dir=run_dir, detail=running.detail).to_dict(),
        )
    try:
        result = CodexRuntimeExecutor(artifact_store=store).execute(order)
        final_status = STATUS_COMPLETED if str(result.get("status") or "").upper() == "EXECUTED" else STATUS_FAILED
        detail = str(result.get("summary") or "Codex production worker finished.")
        completed = _set_job(
            job_id,
            status=final_status,
            detail=detail,
            result=dict(result),
            error=None if final_status == STATUS_COMPLETED else str(result.get("detail") or result.get("reason_code") or ""),
        )
        if completed is not None:
            _persist_job(store, run_dir, completed)
            store.write_json(
                run_dir / "result_contract.json",
                _result_contract(
                    status="EXECUTED" if final_status == STATUS_COMPLETED else "DEGRADED",
                    order=order,
                    run_dir=run_dir,
                    detail=detail,
                    error=completed.error,
                    result=result,
                ).to_dict(),
            )
    except Exception as exc:  # pragma: no cover - defensive background guard
        failed = _set_job(
            job_id,
            status=STATUS_FAILED,
            detail="Codex production worker crashed.",
            error=str(exc),
        )
        if failed is not None:
            _persist_job(store, run_dir, failed)
            store.write_json(
                run_dir / "result_contract.json",
                _result_contract(status="FAILED", order=order, run_dir=run_dir, detail=failed.detail, error=str(exc)).to_dict(),
            )


def execute_codex_production_payload(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload or {})
    raw_order = data.get("work_order")
    if not isinstance(raw_order, dict):
        return {
            "status": "FAILED",
            "reason_code": "CODEX_PRODUCTION_WORKORDER_REQUIRED",
            "detail": "payload.work_order is required",
        }

    artifacts_root = str(
        data.get("artifacts_root")
        or data.get("runs_root")
        or dict(raw_order.get("metadata") or {}).get("artifacts_root")
        or ""
    ).strip()
    store = CodexArtifactStore(artifacts_root or None)
    order = CodexWorkOrder.from_mapping(raw_order)
    run_dir = store.prepare_run_dir(order.work_order_id)
    try:
        order.validate()
        _validate_production_boundary(order)
    except Exception as exc:
        contract = _result_contract(status="FAILED", order=order, run_dir=run_dir, detail=str(exc), error=str(exc))
        store.write_json(run_dir / "work_order.json", order.to_dict())
        store.write_json(run_dir / "result_contract.json", contract.to_dict())
        return {
            "status": "FAILED",
            "reason_code": "CODEX_PRODUCTION_CONTRACT_INVALID",
            "detail": str(exc),
            "manifest_ref": f"codex_production::{order.work_order_id}",
            "work_order_id": order.work_order_id,
            "artifacts_dir": str(run_dir),
        }

    job_id = str(data.get("job_id") or f"codex_job_{new_event_id()}").strip()
    skill_id = str(order.metadata.get("skill_id") or "").strip()
    job = CodexProductionJob(
        job_id=job_id,
        work_order_id=order.work_order_id,
        skill_id=skill_id,
        status=STATUS_QUEUED,
        artifacts_dir=str(run_dir),
        result_contract_ref=str(run_dir / "result_contract.json"),
        request_id=str(order.metadata.get("request_id") or "").strip() or None,
        detail="Codex production worker queued.",
    )
    with _JOBS_LOCK:
        _JOBS[job_id] = job

    store.write_json(run_dir / "work_order.json", order.to_dict())
    store.write_json(run_dir / "result_contract.json", _result_contract(status="QUEUED", order=order, run_dir=run_dir, detail=job.detail).to_dict())
    _persist_job(store, run_dir, job)

    start_background = bool(data.get("start_background", True))
    if start_background:
        thread = threading.Thread(
            target=_run_background_job,
            kwargs={"job_id": job_id, "order": order, "store": store, "run_dir": run_dir},
            name=f"codex-production-{job_id[:16]}",
            daemon=True,
        )
        thread.start()

    return {
        "status": "OK",
        "result_code": "CODEX_PRODUCTION_QUEUED",
        "manifest_ref": f"codex_production::{order.work_order_id}",
        "action": "enqueue",
        "job_id": job_id,
        "work_order_id": order.work_order_id,
        "skill_id": skill_id,
        "queued": True,
        "codex_background": True,
        "surface_main_chain_required": True,
        "artifacts_dir": str(run_dir),
        "result_contract_ref": str(run_dir / "result_contract.json"),
        "persona_boundary": dict(order.metadata.get("persona_boundary") or {}),
        "trigger_policy": dict(order.metadata.get("trigger_policy") or {}),
        "detail": job.to_dict(),
    }


__all__ = [
    "CodexProductionJob",
    "execute_codex_production_payload",
    "get_codex_production_job",
    "list_codex_production_jobs",
]
