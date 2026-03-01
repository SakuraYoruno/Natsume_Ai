"""Failure drill reporting helpers for production degrade exercises."""

from __future__ import annotations

from typing import Any, Iterable

from host_brain.core.errors import (
    RELEASE_ROLLBACK_TARGET_MISSING,
    SERVICE_UNAVAILABLE_COLD_START,
    SERVICE_UNAVAILABLE_DEPENDENCY,
)


S_DRILL_SCENARIO = "S_DRILL_SCENARIO"
S_DRILL_SUITE = "S_DRILL_SUITE"

STATUS_OK = "OK"
STATUS_DEGRADED = "DEGRADED"
STATUS_FAILED = "FAILED"


_DISPOSITION_BY_REASON: dict[str, tuple[str, str, str]] = {
    SERVICE_UNAVAILABLE_COLD_START: (
        "COLD_START",
        "high",
        "Keep draft_only output and complete key/config alignment before next release window.",
    ),
    SERVICE_UNAVAILABLE_DEPENDENCY: (
        "DEPENDENCY_DOWN",
        "high",
        "Keep degraded path active, keep replay_ref for incident review, and wait for dependency recovery.",
    ),
    RELEASE_ROLLBACK_TARGET_MISSING: (
        "ROLLBACK_CONFLICT",
        "critical",
        "Freeze rollback execution and repair rollback mapping assets before next drill round.",
    ),
}


def normalize_reason_code_chain(*chains: Iterable[Any]) -> list[str]:
    """Merge reason_code lists with deterministic order and de-dup."""

    merged: list[str] = []
    for chain in chains:
        for raw in chain:
            code = str(raw or "").strip()
            if not code or code in merged:
                continue
            merged.append(code)
    return merged


def classify_drill_failure(*, reason_code: str | None, stage: str | None) -> dict[str, str]:
    code = str(reason_code or "").strip()
    stage_value = str(stage or "").strip()
    if code in _DISPOSITION_BY_REASON:
        failure_class, severity, disposition = _DISPOSITION_BY_REASON[code]
        return {
            "failure_class": failure_class,
            "severity": severity,
            "disposition": disposition,
            "stage": stage_value,
        }
    return {
        "failure_class": "UNCLASSIFIED",
        "severity": "medium",
        "disposition": "Keep audit linkage and classify the reason_code before broad rollout.",
        "stage": stage_value,
    }


def build_drill_result(
    *,
    scenario_id: str,
    status: str,
    stage: str,
    reason_code: str | None,
    reason_code_chain: list[str],
    replay_ref: str,
    detail: str,
    payload: dict[str, Any],
    replay_report: dict[str, Any],
    ts_ms: int,
) -> dict[str, Any]:
    classification = classify_drill_failure(reason_code=reason_code, stage=stage)
    return {
        "scenario_id": str(scenario_id),
        "stage": str(stage),
        "status": str(status),
        "reason_code": (str(reason_code) if reason_code is not None else None),
        "reason_code_chain": list(reason_code_chain),
        "replay_ref": str(replay_ref),
        "detail": str(detail),
        "classification": classification,
        "payload": dict(payload),
        "replay_report": dict(replay_report),
        "ts_ms": int(ts_ms),
    }
