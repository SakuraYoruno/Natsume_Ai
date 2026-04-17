from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query

import host_brain.app.health_routes as health_routes
from host_brain.app.dependencies import get_ledger, get_skill_registry
from host_brain.app.codex_self_heal import (
    build_codex_self_heal_proposal_from_health_report,
    build_codex_self_heal_proposal_from_readiness_report,
)
from host_brain.core.ids import new_request_id
from host_brain.skills.executor_registry import dispatch_executor_payload

router = APIRouter(prefix="/internal/codex", tags=["internal-codex"])
_logger = logging.getLogger("Natsume.internal_codex")

_SELF_HEAL_JOB_STATUS_QUEUED = "QUEUED"
_SELF_HEAL_JOB_STATUS_RUNNING = "RUNNING"
_SELF_HEAL_JOB_STATUS_COMPLETED = "COMPLETED"
_SELF_HEAL_JOB_STATUS_FAILED = "FAILED"
_SELF_HEAL_JOB_STATUS_SKIPPED = "SKIPPED"
_AUTO_DISPATCH_DECISION_HEALTHY = "HEALTHY"
_AUTO_DISPATCH_DECISION_WAITING_THRESHOLD = "WAITING_THRESHOLD"
_AUTO_DISPATCH_DECISION_COOLDOWN_ACTIVE = "COOLDOWN_ACTIVE"
_AUTO_DISPATCH_DECISION_DISPATCHED = "DISPATCHED"
_AUTO_DISPATCH_DECISION_EXTERNAL_DEPENDENCY_BLOCKED = "EXTERNAL_DEPENDENCY_BLOCKED"
_AUTO_DISPATCH_POLICY_INSPECT_ONLY = "inspect_only"
_AUTO_DISPATCH_POLICY_INSPECT_THEN_SELF_HEAL = "inspect_then_self_heal"
_AUTO_DISPATCH_POLICIES = {
    _AUTO_DISPATCH_POLICY_INSPECT_ONLY,
    _AUTO_DISPATCH_POLICY_INSPECT_THEN_SELF_HEAL,
}

_self_heal_jobs: dict[str, dict[str, Any]] = {}
_self_heal_jobs_lock = threading.Lock()
_self_heal_auto_state: dict[str, dict[str, Any]] = {}
_self_heal_auto_state_lock = threading.Lock()
_self_heal_monitor_lock = threading.Lock()
_self_heal_monitor_thread: threading.Thread | None = None
_self_heal_monitor_stop_event = threading.Event()
_self_heal_monitor_state: dict[str, Any] = {
    "enabled": False,
    "running": False,
    "source": "health",
    "interval_seconds": None,
    "min_consecutive_failures": None,
    "cooldown_seconds": None,
    "skip_dependency_probes": False,
    "run_mode_override": None,
    "escalation_policy": None,
    "timeout_seconds": None,
    "last_cycle_at_ms": None,
    "last_result": None,
    "last_error": None,
    "thread_name": None,
    "scheduler_managed": False,
}


def _validate_internal_token(x_internal_token: str | None) -> None:
    expected = str(
        os.environ.get("HOST_INTERNAL_CODEX_TOKEN")
        or os.environ.get("HOST_INTERNAL_TRIBE_TOKEN")
        or ""
    ).strip()
    if not expected:
        return
    if str(x_internal_token or "").strip() != expected:
        raise HTTPException(status_code=401, detail="UNAUTHORIZED_INTERNAL_CODEX")


def _job_ts_ms() -> int:
    return int(time.time() * 1000)


def _parse_bool_env(name: str, *, default: bool) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _coerce_int_env(name: str, *, default: int, minimum: int, maximum: int) -> int:
    raw = str(os.environ.get(name) or "").strip()
    try:
        value = int(raw) if raw else int(default)
    except (TypeError, ValueError):
        value = int(default)
    return max(minimum, min(value, maximum))


def _normalize_run_mode(value: str | None, *, default: str = "inspect") -> str:
    candidate = str(value or "").strip().lower() or default
    if candidate not in {"inspect", "worker", "self_heal", "self_evolve"}:
        return default
    return candidate


def _normalize_escalation_policy(value: str | None, *, default: str = _AUTO_DISPATCH_POLICY_INSPECT_THEN_SELF_HEAL) -> str:
    candidate = str(value or "").strip().lower() or default
    if candidate not in _AUTO_DISPATCH_POLICIES:
        return default
    return candidate


def _load_auto_dispatch_runtime_options() -> dict[str, Any]:
    source = str(os.environ.get("HOST_CODEX_SELF_HEAL_AUTODISPATCH_SOURCE") or "health").strip().lower() or "health"
    if source not in {"health", "ready", "readiness"}:
        source = "health"
    return {
        "source": source,
        "skip_dependency_probes": _parse_bool_env(
            "HOST_CODEX_SELF_HEAL_AUTODISPATCH_SKIP_DEPENDENCY_PROBES",
            default=False,
        ),
        "min_consecutive_failures": _coerce_int_env(
            "HOST_CODEX_SELF_HEAL_AUTODISPATCH_MIN_CONSECUTIVE",
            default=3,
            minimum=1,
            maximum=20,
        ),
        "cooldown_seconds": _coerce_int_env(
            "HOST_CODEX_SELF_HEAL_AUTODISPATCH_COOLDOWN_SECONDS",
            default=900,
            minimum=0,
            maximum=86_400,
        ),
        "timeout_seconds": _coerce_int_env(
            "HOST_CODEX_SELF_HEAL_AUTODISPATCH_TIMEOUT_SECONDS",
            default=120,
            minimum=15,
            maximum=1_800,
        ),
        "run_mode_override": _normalize_run_mode(
            os.environ.get("HOST_CODEX_SELF_HEAL_AUTODISPATCH_RUN_MODE"),
            default="inspect",
        ),
        "escalation_policy": _normalize_escalation_policy(
            os.environ.get("HOST_CODEX_SELF_HEAL_AUTODISPATCH_ESCALATION_POLICY"),
        ),
    }


def _snapshot_monitor_state() -> dict[str, Any]:
    with _self_heal_monitor_lock:
        return dict(_self_heal_monitor_state)


def _update_monitor_state(**updates: Any) -> dict[str, Any]:
    with _self_heal_monitor_lock:
        _self_heal_monitor_state.update(updates)
        return dict(_self_heal_monitor_state)


def _snapshot_auto_dispatch_state(source: str) -> dict[str, Any] | None:
    source_key = str(source or "health").strip().lower() or "health"
    with _self_heal_auto_state_lock:
        state = _self_heal_auto_state.get(source_key)
        return dict(state) if isinstance(state, dict) else None


def _upsert_auto_dispatch_state(source: str, **updates: Any) -> dict[str, Any]:
    source_key = str(source or "health").strip().lower() or "health"
    with _self_heal_auto_state_lock:
        current = dict(_self_heal_auto_state.get(source_key) or {})
        current.update(updates)
        current["source"] = source_key
        current["updated_at_ms"] = _job_ts_ms()
        _self_heal_auto_state[source_key] = current
        return dict(current)


def _proposal_fingerprint(proposal: dict[str, Any]) -> str:
    source = str(proposal.get("source") or "health").strip().lower()
    reason_code = str(proposal.get("reason_code") or "NO_REASON").strip().upper() or "NO_REASON"
    components = [str(value).strip() for value in (proposal.get("affected_components") or []) if str(value).strip()]
    return "|".join([source, reason_code, ",".join(components[:8])])


def _build_auto_dispatch_snapshot(
    *,
    source: str,
    proposal: dict[str, Any],
    min_consecutive_failures: int,
    cooldown_seconds: int,
) -> dict[str, Any]:
    now_ts = _job_ts_ms()
    source_key = str(source or "health").strip().lower() or "health"
    existing = _snapshot_auto_dispatch_state(source_key) or {}
    triggered = bool(proposal.get("triggered"))
    fingerprint = _proposal_fingerprint(proposal) if triggered else None
    last_fingerprint = str(existing.get("fingerprint") or "").strip() or None
    if not triggered:
        return _upsert_auto_dispatch_state(
            source_key,
            decision=_AUTO_DISPATCH_DECISION_HEALTHY,
            eligible=False,
            reason_code=proposal.get("reason_code"),
            fingerprint=None,
            consecutive_failures=0,
            min_consecutive_failures=int(min_consecutive_failures),
            cooldown_seconds=int(cooldown_seconds),
            cooldown_remaining_seconds=0,
            last_seen_at_ms=now_ts,
        )

    dispatch_allowed = proposal.get("dispatch_allowed")
    if dispatch_allowed is False:
        previous_count = int(existing.get("consecutive_failures") or 0)
        consecutive_failures = previous_count + 1 if fingerprint and fingerprint == last_fingerprint else 1
        return _upsert_auto_dispatch_state(
            source_key,
            decision=_AUTO_DISPATCH_DECISION_EXTERNAL_DEPENDENCY_BLOCKED,
            eligible=False,
            reason_code=proposal.get("reason_code"),
            fingerprint=fingerprint,
            affected_components=list(proposal.get("affected_components") or []),
            consecutive_failures=consecutive_failures,
            min_consecutive_failures=int(min_consecutive_failures),
            cooldown_seconds=int(cooldown_seconds),
            cooldown_remaining_seconds=0,
            last_seen_at_ms=now_ts,
            last_dispatch_reason_code=proposal.get("dispatch_blocked_reason"),
        )

    previous_count = int(existing.get("consecutive_failures") or 0)
    consecutive_failures = previous_count + 1 if fingerprint and fingerprint == last_fingerprint else 1
    last_dispatched_at_ms = existing.get("last_dispatched_at_ms")
    cooldown_remaining_seconds = 0
    if isinstance(last_dispatched_at_ms, int) and int(cooldown_seconds) > 0:
        elapsed_ms = max(0, now_ts - last_dispatched_at_ms)
        remaining_ms = max(0, (int(cooldown_seconds) * 1000) - elapsed_ms)
        cooldown_remaining_seconds = remaining_ms // 1000

    eligible = consecutive_failures >= int(min_consecutive_failures) and cooldown_remaining_seconds <= 0
    if eligible:
        decision = _AUTO_DISPATCH_DECISION_DISPATCHED
    elif cooldown_remaining_seconds > 0:
        decision = _AUTO_DISPATCH_DECISION_COOLDOWN_ACTIVE
    else:
        decision = _AUTO_DISPATCH_DECISION_WAITING_THRESHOLD

    return _upsert_auto_dispatch_state(
        source_key,
        decision=decision,
        eligible=bool(eligible),
        reason_code=proposal.get("reason_code"),
        fingerprint=fingerprint,
        affected_components=list(proposal.get("affected_components") or []),
        consecutive_failures=consecutive_failures,
        min_consecutive_failures=int(min_consecutive_failures),
        cooldown_seconds=int(cooldown_seconds),
        cooldown_remaining_seconds=int(cooldown_remaining_seconds),
        last_seen_at_ms=now_ts,
    )


def _select_auto_dispatch_run_mode(
    *,
    source: str,
    proposal: dict[str, Any],
    requested_run_mode: str | None,
    escalation_policy: str,
) -> str:
    normalized_requested_run_mode = _normalize_run_mode(requested_run_mode, default="inspect")
    normalized_policy = _normalize_escalation_policy(escalation_policy)
    if normalized_policy != _AUTO_DISPATCH_POLICY_INSPECT_THEN_SELF_HEAL:
        return normalized_requested_run_mode
    if normalized_requested_run_mode != "inspect":
        return normalized_requested_run_mode

    existing = _snapshot_auto_dispatch_state(source) or {}
    fingerprint = _proposal_fingerprint(proposal)
    last_fingerprint = str(existing.get("fingerprint") or "").strip()
    last_dispatch_run_mode = _normalize_run_mode(str(existing.get("last_dispatch_run_mode") or "").strip(), default="inspect")
    last_job_id = str(existing.get("last_job_id") or "").strip()
    if fingerprint and fingerprint == last_fingerprint and last_job_id and last_dispatch_run_mode == "inspect":
        return "self_heal"
    return normalized_requested_run_mode


def _snapshot_self_heal_job(job_id: str) -> dict[str, Any] | None:
    with _self_heal_jobs_lock:
        job = _self_heal_jobs.get(job_id)
        return dict(job) if isinstance(job, dict) else None


def _upsert_self_heal_job(job_id: str, **updates: Any) -> dict[str, Any]:
    with _self_heal_jobs_lock:
        current = dict(_self_heal_jobs.get(job_id) or {})
        current.update(updates)
        current["job_id"] = job_id
        current["updated_at_ms"] = _job_ts_ms()
        _self_heal_jobs[job_id] = current
        return dict(current)


def _create_self_heal_job(
    *,
    source: str,
    proposal: dict[str, Any],
    skip_dependency_probes: bool,
    timeout_seconds: int | None,
    run_mode_override: str | None,
) -> dict[str, Any]:
    job_id = f"codex-self-heal::{new_request_id()}"
    created_at_ms = _job_ts_ms()
    return _upsert_self_heal_job(
        job_id,
        kind="codex_self_heal",
        source=source,
        proposal=proposal,
        skip_dependency_probes=bool(skip_dependency_probes),
        timeout_seconds=timeout_seconds,
        run_mode_override=run_mode_override,
        status=_SELF_HEAL_JOB_STATUS_QUEUED,
        background=True,
        executed=False,
        dispatch=None,
        error=None,
        created_at_ms=created_at_ms,
        started_at_ms=None,
        finished_at_ms=None,
    )


def _create_background_self_heal_job(
    *,
    source: str,
    proposal: dict[str, Any],
    skip_dependency_probes: bool,
    timeout_seconds: int | None,
    run_mode_override: str | None,
) -> dict[str, Any]:
    job = _create_self_heal_job(
        source=source,
        proposal=proposal,
        skip_dependency_probes=skip_dependency_probes,
        timeout_seconds=timeout_seconds,
        run_mode_override=run_mode_override,
    )
    _start_background_self_heal_job(str(job["job_id"]))
    return job


async def _auto_dispatch_self_heal_once(
    *,
    source: str,
    skip_dependency_probes: bool,
    min_consecutive_failures: int,
    cooldown_seconds: int,
    timeout_seconds: int | None,
    run_mode_override: str | None,
    escalation_policy: str,
) -> dict[str, Any]:
    normalized_source = "readiness" if str(source).strip().lower() in {"ready", "readiness"} else "health"
    proposal = await _build_live_self_heal_proposal(
        source=source,
        skip_dependency_probes=skip_dependency_probes,
    )
    auto_state = _build_auto_dispatch_snapshot(
        source=normalized_source,
        proposal=proposal,
        min_consecutive_failures=min_consecutive_failures,
        cooldown_seconds=cooldown_seconds,
    )

    if not bool(proposal.get("triggered")):
        return {
            "accepted": False,
            "dispatched": False,
            "background": False,
            "job_id": None,
            "proposal": proposal,
            "auto_state": auto_state,
            "job": None,
        }

    if not bool(auto_state.get("eligible")):
        return {
            "accepted": True,
            "dispatched": False,
            "background": False,
            "job_id": None,
            "proposal": proposal,
            "auto_state": auto_state,
            "job": None,
        }

    selected_run_mode = _select_auto_dispatch_run_mode(
        source=normalized_source,
        proposal=proposal,
        requested_run_mode=run_mode_override,
        escalation_policy=escalation_policy,
    )
    job = _create_background_self_heal_job(
        source=normalized_source,
        proposal=proposal,
        skip_dependency_probes=skip_dependency_probes,
        timeout_seconds=timeout_seconds,
        run_mode_override=selected_run_mode,
    )
    auto_state = _upsert_auto_dispatch_state(
        normalized_source,
        decision=_AUTO_DISPATCH_DECISION_DISPATCHED,
        eligible=False,
        cooldown_remaining_seconds=int(cooldown_seconds),
        last_dispatched_at_ms=_job_ts_ms(),
        last_job_id=job["job_id"],
        last_dispatch_run_mode=selected_run_mode,
        escalation_policy=_normalize_escalation_policy(escalation_policy),
    )
    return {
        "accepted": True,
        "dispatched": True,
        "background": True,
        "job_id": job["job_id"],
        "proposal": proposal,
        "auto_state": auto_state,
        "job": job,
        "selected_run_mode": selected_run_mode,
    }


async def _build_live_self_heal_proposal(
    *,
    source: str,
    skip_dependency_probes: bool,
) -> dict[str, Any]:
    source_text = str(source or "health").strip().lower()
    if source_text in {"ready", "readiness"}:
        report = await health_routes.build_live_readiness_report()
        return build_codex_self_heal_proposal_from_readiness_report(
            report,
            include_dependency_probes=True,
        )
    report = await health_routes.build_live_health_report(skip_dependency_probes=skip_dependency_probes)
    return build_codex_self_heal_proposal_from_health_report(
        report,
        include_dependency_probes=not skip_dependency_probes,
    )


def _flatten_result_text(raw_result: Any) -> str:
    if isinstance(raw_result, dict):
        for key in (
            "result_text",
            "final_summary",
            "summary",
            "detail",
            "last_message",
            "narrative",
        ):
            value = str(raw_result.get(key) or "").strip()
            if value:
                return value
    return str(raw_result or "").strip()


def _dispatch_status(raw_result: Any) -> tuple[str, str | None, bool]:
    if isinstance(raw_result, dict):
        status_text = str(raw_result.get("status") or "UNKNOWN").strip().upper() or "UNKNOWN"
        reason_code = str(raw_result.get("reason_code") or raw_result.get("result_code") or "").strip() or None
    else:
        status_text = "UNKNOWN"
        reason_code = None
    success = status_text in {"EXECUTED", "OK", "SUCCESS", "COMPLETED"} and not reason_code
    return status_text, reason_code, success


def _log_self_heal_dispatch(
    *,
    request_id: str,
    intent_id: str,
    result_status: str,
    result_code: str | None,
    result_digest: str,
    ts_ms: int,
) -> None:
    try:
        get_ledger().log_skill_exec(
            {
                "request_id": request_id,
                "thread_id": None,
                "turn_id": None,
                "intent_id": intent_id,
                "event_id": f"evt::{request_id}::codex_codegen",
                "skill_name": "codex_codegen",
                "approval_token_ref": None,
                "confirm_token_ref": None,
                "undo_token_ref": None,
                "result_status": result_status,
                "result_code": result_code,
                "result_digest": result_digest[:4000] if result_digest else None,
                "evidence_ref": f"skill_exec::{request_id}::codex_codegen",
                "manifest_ref": None,
                "ts_ms": ts_ms,
            }
        )
    except Exception:
        return


def _dispatch_codex_self_heal_proposal(
    proposal: dict[str, Any],
    *,
    dispatch_request_id: str,
    timeout_seconds: int | None = None,
    run_mode_override: str | None = None,
) -> dict[str, Any]:
    intent_draft = dict(proposal.get("intent_draft") or {})
    args = dict(intent_draft.get("args") or {})
    if timeout_seconds is not None:
        args["timeout_seconds"] = int(timeout_seconds)
    if str(run_mode_override or "").strip():
        args["run_mode"] = str(run_mode_override).strip().lower()

    registry = get_skill_registry()
    skill_meta = registry.require("codex_codegen")
    context = {
        "request_id": dispatch_request_id,
        "session_id": None,
    }
    payload = skill_meta.handler(args, context)
    if isinstance(payload, dict):
        payload = dict(payload)
        payload.setdefault("_context", context)
    raw_result = dispatch_executor_payload(payload)
    status_text, reason_code, success = _dispatch_status(raw_result)
    result_text = _flatten_result_text(raw_result)
    ts_ms = int(time.time() * 1000)
    intent_id = str(intent_draft.get("id") or f"intent::codex-self-heal::{dispatch_request_id}").strip()
    _log_self_heal_dispatch(
        request_id=dispatch_request_id,
        intent_id=intent_id,
        result_status="SUCCESS" if success else "FAILED",
        result_code=reason_code,
        result_digest=result_text or str(raw_result or ""),
        ts_ms=ts_ms,
    )
    return {
        "request_id": dispatch_request_id,
        "intent_id": intent_id,
        "skill_name": "codex_codegen",
        "status": status_text,
        "reason_code": reason_code,
        "success": success,
        "detail": str(raw_result.get("detail") or "").strip() if isinstance(raw_result, dict) else None,
        "artifacts_dir": str(raw_result.get("artifacts_dir") or "").strip() if isinstance(raw_result, dict) else None,
        "result_text": result_text or None,
        "raw_result": raw_result,
        "normalized_payload": payload if isinstance(payload, dict) else {"payload": payload},
        "ts_ms": ts_ms,
    }


def _run_self_heal_job(job_id: str) -> None:
    job = _snapshot_self_heal_job(job_id)
    if not isinstance(job, dict):
        return
    proposal = dict(job.get("proposal") or {})
    if not bool(proposal.get("triggered")):
        _upsert_self_heal_job(
            job_id,
            status=_SELF_HEAL_JOB_STATUS_SKIPPED,
            executed=False,
            dispatch=None,
            error="SELF_HEAL_NOT_REQUIRED",
            finished_at_ms=_job_ts_ms(),
        )
        return

    _upsert_self_heal_job(
        job_id,
        status=_SELF_HEAL_JOB_STATUS_RUNNING,
        started_at_ms=_job_ts_ms(),
        error=None,
    )
    try:
        dispatch_request_id = new_request_id()
        dispatch = _dispatch_codex_self_heal_proposal(
            proposal,
            dispatch_request_id=dispatch_request_id,
            timeout_seconds=job.get("timeout_seconds"),
            run_mode_override=job.get("run_mode_override"),
        )
        job_status = _SELF_HEAL_JOB_STATUS_COMPLETED if bool(dispatch.get("success")) else _SELF_HEAL_JOB_STATUS_FAILED
        _upsert_self_heal_job(
            job_id,
            status=job_status,
            executed=True,
            dispatch=dispatch,
            error=None if bool(dispatch.get("success")) else str(dispatch.get("reason_code") or dispatch.get("status") or "SELF_HEAL_JOB_FAILED"),
            finished_at_ms=_job_ts_ms(),
        )
        _upsert_auto_dispatch_state(
            str(job.get("source") or "health"),
            last_job_status=job_status,
            last_dispatch_status=dispatch.get("status"),
            last_dispatch_reason_code=dispatch.get("reason_code"),
            last_dispatch_success=bool(dispatch.get("success")),
            last_artifacts_dir=dispatch.get("artifacts_dir"),
        )
    except Exception as exc:
        _upsert_self_heal_job(
            job_id,
            status=_SELF_HEAL_JOB_STATUS_FAILED,
            executed=False,
            dispatch=None,
            error=str(exc),
            finished_at_ms=_job_ts_ms(),
        )
        _upsert_auto_dispatch_state(
            str(job.get("source") or "health"),
            last_job_status=_SELF_HEAL_JOB_STATUS_FAILED,
            last_dispatch_status=None,
            last_dispatch_reason_code="SELF_HEAL_JOB_EXCEPTION",
            last_dispatch_success=False,
        )


def _start_background_self_heal_job(job_id: str) -> None:
    thread = threading.Thread(
        target=_run_self_heal_job,
        args=(job_id,),
        name=f"codex-self-heal-{job_id}",
        daemon=True,
    )
    thread.start()


def _run_auto_dispatch_monitor_loop() -> None:
    interval_seconds = _coerce_int_env(
        "HOST_CODEX_SELF_HEAL_AUTODISPATCH_INTERVAL_SECONDS",
        default=300,
        minimum=15,
        maximum=86_400,
    )
    options = _load_auto_dispatch_runtime_options()
    source = str(options["source"])
    min_consecutive_failures = int(options["min_consecutive_failures"])
    cooldown_seconds = int(options["cooldown_seconds"])
    timeout_seconds = int(options["timeout_seconds"])
    run_mode_override = str(options["run_mode_override"])
    escalation_policy = str(options["escalation_policy"])
    skip_dependency_probes = bool(options["skip_dependency_probes"])

    _update_monitor_state(
        enabled=True,
        running=True,
        source="readiness" if source in {"ready", "readiness"} else "health",
        interval_seconds=interval_seconds,
        min_consecutive_failures=min_consecutive_failures,
        cooldown_seconds=cooldown_seconds,
        skip_dependency_probes=bool(skip_dependency_probes),
        run_mode_override=run_mode_override,
        escalation_policy=escalation_policy,
        timeout_seconds=timeout_seconds,
        last_error=None,
        thread_name=threading.current_thread().name,
        scheduler_managed=False,
    )

    while not _self_heal_monitor_stop_event.is_set():
        try:
            result = asyncio.run(
                _auto_dispatch_self_heal_once(
                    source=source,
                    skip_dependency_probes=skip_dependency_probes,
                    min_consecutive_failures=min_consecutive_failures,
                    cooldown_seconds=cooldown_seconds,
                    timeout_seconds=timeout_seconds,
                    run_mode_override=run_mode_override,
                    escalation_policy=escalation_policy,
                )
            )
            _update_monitor_state(
                last_cycle_at_ms=_job_ts_ms(),
                last_result={
                    "accepted": bool(result.get("accepted")),
                    "dispatched": bool(result.get("dispatched")),
                    "background": bool(result.get("background")),
                    "job_id": result.get("job_id"),
                    "reason_code": (result.get("proposal") or {}).get("reason_code"),
                    "decision": (result.get("auto_state") or {}).get("decision"),
                    "selected_run_mode": result.get("selected_run_mode") or run_mode_override,
                },
                last_error=None,
            )
        except Exception as exc:
            _logger.warning("Codex self-heal auto-dispatch monitor cycle failed: %s", exc)
            _update_monitor_state(
                last_cycle_at_ms=_job_ts_ms(),
                last_error=str(exc),
            )
        if _self_heal_monitor_stop_event.wait(interval_seconds):
            break

    _update_monitor_state(running=False)


async def run_scheduled_self_heal_auto_dispatch_cycle() -> dict[str, Any]:
    options = _load_auto_dispatch_runtime_options()
    result = await _auto_dispatch_self_heal_once(
        source=str(options["source"]),
        skip_dependency_probes=bool(options["skip_dependency_probes"]),
        min_consecutive_failures=int(options["min_consecutive_failures"]),
        cooldown_seconds=int(options["cooldown_seconds"]),
        timeout_seconds=int(options["timeout_seconds"]),
        run_mode_override=str(options["run_mode_override"]),
        escalation_policy=str(options["escalation_policy"]),
    )
    _update_monitor_state(
        enabled=True,
        running=False,
        source="readiness" if str(options["source"]) in {"ready", "readiness"} else "health",
        min_consecutive_failures=int(options["min_consecutive_failures"]),
        cooldown_seconds=int(options["cooldown_seconds"]),
        skip_dependency_probes=bool(options["skip_dependency_probes"]),
        run_mode_override=str(options["run_mode_override"]),
        escalation_policy=str(options["escalation_policy"]),
        timeout_seconds=int(options["timeout_seconds"]),
        scheduler_managed=True,
        last_cycle_at_ms=_job_ts_ms(),
        last_result={
            "accepted": bool(result.get("accepted")),
            "dispatched": bool(result.get("dispatched")),
            "background": bool(result.get("background")),
            "job_id": result.get("job_id"),
            "reason_code": (result.get("proposal") or {}).get("reason_code"),
            "decision": (result.get("auto_state") or {}).get("decision"),
            "selected_run_mode": result.get("selected_run_mode") or str(options["run_mode_override"]),
        },
        last_error=None,
    )
    return result


def start_self_heal_auto_dispatch_monitor(*, logger: Any | None = None) -> dict[str, Any]:
    if _parse_bool_env("HOST_CODEX_SELF_HEAL_SCHEDULER_ENABLED", default=False):
        return _update_monitor_state(
            enabled=False,
            running=False,
            last_error=None,
            scheduler_managed=True,
        )
    enabled = _parse_bool_env("HOST_CODEX_SELF_HEAL_AUTODISPATCH_ENABLED", default=False)
    if not enabled:
        return _update_monitor_state(enabled=False, running=False, last_error=None, scheduler_managed=False)

    with _self_heal_monitor_lock:
        global _self_heal_monitor_thread
        if _self_heal_monitor_thread is not None and _self_heal_monitor_thread.is_alive():
            return dict(_self_heal_monitor_state)
        _self_heal_monitor_stop_event.clear()
        _self_heal_monitor_state.update(
            {
                "enabled": True,
                "running": True,
                "last_error": None,
                "thread_name": "codex-self-heal-auto-dispatch",
            }
        )
        _self_heal_monitor_thread = threading.Thread(
            target=_run_auto_dispatch_monitor_loop,
            name="codex-self-heal-auto-dispatch",
            daemon=True,
        )
        _self_heal_monitor_thread.start()
        state = dict(_self_heal_monitor_state)
    target_logger = logger or _logger
    target_logger.info("Codex self-heal auto-dispatch monitor started")
    return state


def stop_self_heal_auto_dispatch_monitor(*, logger: Any | None = None) -> dict[str, Any]:
    with _self_heal_monitor_lock:
        global _self_heal_monitor_thread
        thread = _self_heal_monitor_thread
        _self_heal_monitor_stop_event.set()
    if thread is not None and thread.is_alive():
        thread.join(timeout=5.0)
    with _self_heal_monitor_lock:
        _self_heal_monitor_thread = None
        _self_heal_monitor_state["running"] = False
        state = dict(_self_heal_monitor_state)
    target_logger = logger or _logger
    target_logger.info("Codex self-heal auto-dispatch monitor stopped")
    return state


@router.get("/self_heal/proposal")
async def get_self_heal_proposal(
    source: str = Query("health", pattern="^(health|ready|readiness)$"),
    skip_dependency_probes: bool = Query(
        False,
        description="Only applies to health source. Skip external dependency probes when building the live health report.",
    ),
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token", convert_underscores=False),
) -> dict[str, Any]:
    _validate_internal_token(x_internal_token)
    return await _build_live_self_heal_proposal(
        source=source,
        skip_dependency_probes=skip_dependency_probes,
    )


@router.post("/self_heal/dispatch")
async def dispatch_self_heal(
    source: str = Query("health", pattern="^(health|ready|readiness)$"),
    skip_dependency_probes: bool = Query(
        False,
        description="Only applies to health source. Skip external dependency probes when building the live health report.",
    ),
    timeout_seconds: int | None = Query(
        default=None,
        ge=15,
        le=1800,
        description="Optional Codex timeout override passed to codex_codegen.",
    ),
    run_mode_override: str | None = Query(
        default=None,
        pattern="^(inspect|worker|self_heal|self_evolve)$",
        description="Optional run mode override for debugging or safe inspection.",
    ),
    background: bool = Query(
        False,
        description="Submit the self-heal execution as a background job and poll it through /internal/codex/jobs/{job_id}.",
    ),
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token", convert_underscores=False),
) -> dict[str, Any]:
    _validate_internal_token(x_internal_token)
    proposal = await _build_live_self_heal_proposal(
        source=source,
        skip_dependency_probes=skip_dependency_probes,
    )
    if not bool(proposal.get("triggered")):
        return {
            "accepted": False,
            "executed": False,
            "background": bool(background),
            "job_id": None,
            "reason_code": "SELF_HEAL_NOT_REQUIRED",
            "proposal": proposal,
            "dispatch": None,
        }

    if background:
        job = _create_background_self_heal_job(
            source=source,
            proposal=proposal,
            skip_dependency_probes=skip_dependency_probes,
            timeout_seconds=timeout_seconds,
            run_mode_override=run_mode_override,
        )
        return {
            "accepted": True,
            "executed": False,
            "background": True,
            "job_id": job["job_id"],
            "proposal": proposal,
            "dispatch": None,
            "job": job,
        }

    dispatch_request_id = new_request_id()
    dispatch = _dispatch_codex_self_heal_proposal(
        proposal,
        dispatch_request_id=dispatch_request_id,
        timeout_seconds=timeout_seconds,
        run_mode_override=run_mode_override,
    )
    return {
        "accepted": True,
        "executed": True,
        "background": False,
        "job_id": None,
        "proposal": proposal,
        "dispatch": dispatch,
    }


@router.get("/jobs/{job_id}")
async def get_codex_job(
    job_id: str,
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token", convert_underscores=False),
) -> dict[str, Any]:
    _validate_internal_token(x_internal_token)
    job = _snapshot_self_heal_job(job_id)
    if not isinstance(job, dict):
        raise HTTPException(status_code=404, detail="CODEX_JOB_NOT_FOUND")
    return job


@router.get("/self_heal/auto_state")
async def get_self_heal_auto_state(
    source: str = Query("health", pattern="^(health|ready|readiness)$"),
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token", convert_underscores=False),
) -> dict[str, Any]:
    _validate_internal_token(x_internal_token)
    source_key = "readiness" if str(source).strip().lower() in {"ready", "readiness"} else "health"
    state = _snapshot_auto_dispatch_state(source_key)
    if isinstance(state, dict):
        return state
    defaults = _load_auto_dispatch_runtime_options()
    return {
        "source": source_key,
        "decision": _AUTO_DISPATCH_DECISION_HEALTHY,
        "eligible": False,
        "consecutive_failures": 0,
        "min_consecutive_failures": int(defaults.get("min_consecutive_failures", 3)),
        "cooldown_seconds": int(defaults.get("cooldown_seconds", 900)),
        "cooldown_remaining_seconds": 0,
        "last_dispatch_run_mode": None,
        "last_dispatch_status": None,
        "last_dispatch_reason_code": None,
        "last_dispatch_success": None,
        "last_job_status": None,
        "last_artifacts_dir": None,
        "escalation_policy": _normalize_escalation_policy(
            str(defaults.get("escalation_policy") or _AUTO_DISPATCH_POLICY_INSPECT_THEN_SELF_HEAL)
        ),
        "last_seen_at_ms": None,
        "last_dispatched_at_ms": None,
        "last_job_id": None,
        "updated_at_ms": None,
    }


@router.get("/self_heal/monitor_status")
async def get_self_heal_monitor_status(
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token", convert_underscores=False),
) -> dict[str, Any]:
    _validate_internal_token(x_internal_token)
    state = _snapshot_monitor_state()
    if bool(state.get("scheduler_managed")):
        defaults = _load_auto_dispatch_runtime_options()
        state.setdefault("enabled", False)
        state.setdefault("running", False)
        state["source"] = str(defaults.get("source") or state.get("source") or "health")
        state["interval_seconds"] = int(
            _coerce_int_env(
                "HOST_CODEX_SELF_HEAL_AUTODISPATCH_INTERVAL_SECONDS",
                default=300,
                minimum=5,
                maximum=86_400,
            )
        )
        state["min_consecutive_failures"] = int(defaults.get("min_consecutive_failures", 3))
        state["cooldown_seconds"] = int(defaults.get("cooldown_seconds", 900))
        state["skip_dependency_probes"] = bool(defaults.get("skip_dependency_probes"))
        state["run_mode_override"] = _normalize_run_mode(
            str(defaults.get("run_mode_override") or state.get("run_mode_override") or "inspect"),
            default="inspect",
        )
        state["escalation_policy"] = _normalize_escalation_policy(
            str(defaults.get("escalation_policy") or state.get("escalation_policy") or _AUTO_DISPATCH_POLICY_INSPECT_THEN_SELF_HEAL)
        )
        state["timeout_seconds"] = int(defaults.get("timeout_seconds", 120))
    return state


@router.post("/self_heal/auto_dispatch")
async def auto_dispatch_self_heal(
    source: str = Query("health", pattern="^(health|ready|readiness)$"),
    skip_dependency_probes: bool = Query(
        False,
        description="Only applies to health source. Skip external dependency probes when building the live health report.",
    ),
    min_consecutive_failures: int = Query(
        3,
        ge=1,
        le=20,
        description="Dispatch only after the same degraded proposal has been observed this many consecutive times.",
    ),
    cooldown_seconds: int = Query(
        900,
        ge=0,
        le=86400,
        description="Minimum cooldown before the same source can auto-dispatch another Codex self-heal job.",
    ),
    timeout_seconds: int | None = Query(
        default=None,
        ge=15,
        le=1800,
        description="Optional Codex timeout override passed to the background codex_codegen job.",
    ),
    run_mode_override: str | None = Query(
        default="inspect",
        pattern="^(inspect|worker|self_heal|self_evolve)$",
        description="Run mode used for the automatically submitted Codex job.",
    ),
    escalation_policy: str = Query(
        default=_AUTO_DISPATCH_POLICY_INSPECT_THEN_SELF_HEAL,
        pattern="^(inspect_only|inspect_then_self_heal)$",
        description="Whether repeated failures stay in inspect mode or escalate to self_heal after an initial inspect dispatch.",
    ),
    x_internal_token: str | None = Header(default=None, alias="X-Internal-Token", convert_underscores=False),
) -> dict[str, Any]:
    _validate_internal_token(x_internal_token)
    return await _auto_dispatch_self_heal_once(
        source=source,
        skip_dependency_probes=skip_dependency_probes,
        min_consecutive_failures=min_consecutive_failures,
        cooldown_seconds=cooldown_seconds,
        timeout_seconds=timeout_seconds,
        run_mode_override=run_mode_override,
        escalation_policy=escalation_policy,
    )
