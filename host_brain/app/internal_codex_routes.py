"""Internal Codex runtime v2 routes."""

from __future__ import annotations

import threading
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query

from host_brain.codex_runtime import CodexRuntimeExecutor, CodexWorkOrder
from host_brain.codex_runtime.immune import build_immune_proposal_from_report
from host_brain.codex_runtime.job_registry import GLOBAL_CODEX_JOB_REGISTRY
from host_brain.core.ids import now_ms

router = APIRouter(prefix="/internal/codex", tags=["internal-codex"])

_immune_auto_state_lock = threading.Lock()
_immune_auto_state: dict[str, Any] = {
    "source": "health",
    "last_cycle_at_ms": None,
    "last_proposal": None,
    "last_job_id": None,
    "last_result_status": None,
}


def _execute_job(job_id: str, work_order: dict[str, Any]) -> None:
    GLOBAL_CODEX_JOB_REGISTRY.update(job_id, status="RUNNING")
    try:
        result = CodexRuntimeExecutor().execute(CodexWorkOrder.from_mapping(work_order))
        terminal_status = "COMPLETED" if result.get("status") == "EXECUTED" else "FAILED"
        GLOBAL_CODEX_JOB_REGISTRY.update(job_id, status=terminal_status, result=result)
    except Exception as exc:
        GLOBAL_CODEX_JOB_REGISTRY.update(
            job_id,
            status="FAILED",
            result={"status": "FAILED", "reason_code": "CODEX_JOB_CRASHED", "detail": str(exc)},
        )


def _start_background_job(*, kind: str, work_order: dict[str, Any]) -> dict[str, Any]:
    job = GLOBAL_CODEX_JOB_REGISTRY.create(kind=kind, work_order=work_order)
    thread = threading.Thread(
        target=_execute_job,
        args=(str(job["job_id"]), dict(work_order)),
        name=f"codex-runtime-{job['job_id']}",
        daemon=True,
    )
    thread.start()
    return job


@router.post("/work-orders/preview")
async def preview_work_order(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    order = CodexWorkOrder.from_mapping(payload.get("work_order") if isinstance(payload.get("work_order"), dict) else payload)
    order.validate()
    return {"ok": True, "work_order": order.to_dict()}


@router.post("/work-orders/execute")
async def execute_work_order(
    payload: dict[str, Any] = Body(...),
    background: bool = Query(False),
) -> dict[str, Any]:
    order = CodexWorkOrder.from_mapping(payload.get("work_order") if isinstance(payload.get("work_order"), dict) else payload)
    order.validate()
    if background:
        job = _start_background_job(kind="codex_work_order", work_order=order.to_dict())
        return {"accepted": True, "background": True, "job_id": job["job_id"], "work_order": order.to_dict()}
    result = CodexRuntimeExecutor().execute(order)
    return {"accepted": True, "background": False, "job_id": None, "result": result}


@router.get("/jobs/{job_id}")
async def get_codex_job(job_id: str) -> dict[str, Any]:
    job = GLOBAL_CODEX_JOB_REGISTRY.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="CODEX_JOB_NOT_FOUND")
    return job


async def _live_report(source: str) -> dict[str, Any]:
    source_key = "readiness" if str(source or "").strip().lower() in {"ready", "readiness"} else "health"
    if source_key == "readiness":
        from host_brain.app.health_routes import build_live_readiness_report  # noqa: PLC0415

        return await build_live_readiness_report()
    from host_brain.app.health_routes import build_live_health_report  # noqa: PLC0415

    return await build_live_health_report()


@router.post("/immune/proposal")
async def build_immune_proposal(
    payload: dict[str, Any] | None = Body(default=None),
    source: str = Query("health"),
) -> dict[str, Any]:
    report = dict((payload or {}).get("report") or {})
    if not report:
        report = await _live_report(source)
    return build_immune_proposal_from_report(report, source=source)


@router.post("/immune/dispatch")
async def dispatch_immune_work_order(
    payload: dict[str, Any] | None = Body(default=None),
    source: str = Query("health"),
    background: bool = Query(True),
    confirmed: bool = Query(True),
) -> dict[str, Any]:
    proposal = await build_immune_proposal(payload or {}, source=source)
    work_order = proposal.get("work_order")
    if not proposal.get("triggered") or not isinstance(work_order, dict):
        return {
            "accepted": False,
            "executed": False,
            "reason_code": "IMMUNE_WORKORDER_NOT_REQUIRED",
            "proposal": proposal,
            "dispatch": None,
        }
    work_order = dict(work_order)
    approval = dict(work_order.get("approval_policy") or {})
    approval["confirmed"] = bool(confirmed)
    work_order["approval_policy"] = approval
    if background:
        job = _start_background_job(kind="codex_immune", work_order=work_order)
        with _immune_auto_state_lock:
            _immune_auto_state.update(
                {
                    "source": source,
                    "last_cycle_at_ms": now_ms(),
                    "last_proposal": proposal,
                    "last_job_id": job["job_id"],
                    "last_result_status": "QUEUED",
                }
            )
        return {"accepted": True, "background": True, "job_id": job["job_id"], "proposal": proposal}
    result = CodexRuntimeExecutor().execute(CodexWorkOrder.from_mapping(work_order))
    with _immune_auto_state_lock:
        _immune_auto_state.update(
            {
                "source": source,
                "last_cycle_at_ms": now_ms(),
                "last_proposal": proposal,
                "last_job_id": None,
                "last_result_status": result.get("status"),
            }
        )
    return {"accepted": True, "background": False, "job_id": None, "proposal": proposal, "dispatch": result}


@router.get("/immune/auto-state")
async def get_immune_auto_state() -> dict[str, Any]:
    with _immune_auto_state_lock:
        return dict(_immune_auto_state)


async def run_scheduled_immune_auto_dispatch_cycle() -> dict[str, Any]:
    result = await dispatch_immune_work_order({}, source="health", background=True, confirmed=True)
    with _immune_auto_state_lock:
        _immune_auto_state["last_cycle_at_ms"] = now_ms()
        _immune_auto_state["last_result_status"] = "DISPATCHED" if result.get("accepted") else "SKIPPED"
    return result


def start_immune_auto_dispatch_monitor(*, logger: Any | None = None) -> dict[str, Any]:
    state = {"enabled": False, "running": False, "scheduler_managed": True}
    if logger is not None:
        logger.info("Codex immune monitor is scheduler-managed in runtime v2.")
    return state


def stop_immune_auto_dispatch_monitor(*, logger: Any | None = None) -> dict[str, Any]:
    state = {"enabled": False, "running": False}
    if logger is not None:
        logger.info("Codex immune monitor stop requested; no standalone monitor is active.")
    return state


__all__ = [
    "router",
    "run_scheduled_immune_auto_dispatch_cycle",
    "start_immune_auto_dispatch_monitor",
    "stop_immune_auto_dispatch_monitor",
]
