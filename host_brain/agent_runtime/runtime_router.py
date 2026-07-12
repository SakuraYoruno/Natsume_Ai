"""Canonical runtime cutover router and guards.

The router is a control-plane primitive: it assigns new WorkOrders to legacy or
canonical execution, reports legacy drain state, and rejects cross-executor
resume. It does not run workers or replay side effects.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping, Sequence

from natsume_contracts.task import (
    EXECUTOR_ASSIGNMENT_SCHEMA_VERSION,
    ExecutorAssignment,
    TaskContractError,
    TaskExecutor,
    TaskRuntimeMode,
)

from host_brain.core.ids import now_ms


RUNTIME_ASSIGNMENT_SCHEMA_VERSION = EXECUTOR_ASSIGNMENT_SCHEMA_VERSION
RUNTIME_ASSIGNMENT_INVALID = "RUNTIME_ASSIGNMENT_INVALID"
RUNTIME_CANARY_OWNER_REQUIRED = "RUNTIME_CANARY_OWNER_REQUIRED"
RUNTIME_CROSS_EXECUTOR_RESUME_REJECTED = "RUNTIME_CROSS_EXECUTOR_RESUME_REJECTED"
RUNTIME_DUPLICATE_SIDE_EFFECT = "RUNTIME_DUPLICATE_SIDE_EFFECT"
RUNTIME_KILL_SWITCH_ACTIVE = "RUNTIME_KILL_SWITCH_ACTIVE"
RUNTIME_LEGACY_DRAIN_INCOMPLETE = "RUNTIME_LEGACY_DRAIN_INCOMPLETE"
RUNTIME_MODE_INVALID = "RUNTIME_MODE_INVALID"
RUNTIME_NEW_TASK_CANONICAL = "RUNTIME_NEW_TASK_CANONICAL"
RUNTIME_NEW_TASK_LEGACY = "RUNTIME_NEW_TASK_LEGACY"
RUNTIME_SHADOW_LEGACY_EXECUTOR = "RUNTIME_SHADOW_LEGACY_EXECUTOR"


# Backward-compatible import surface.  There is one assignment model: the
# runtime-independent contract in natsume_contracts.task.
RuntimeRouterError = TaskContractError
RuntimeMode = TaskRuntimeMode
RuntimeExecutor = TaskExecutor
RuntimeAssignment = ExecutorAssignment


class CanonicalRuntimeRouter:
    def __init__(
        self,
        *,
        mode: str = "legacy",
        canary_percent: int = 0,
        kill_switch: bool = False,
        canonical_owner: str = "",
        confirmed_owner: str = "",
        clock: Any = now_ms,
    ) -> None:
        try:
            self.mode = RuntimeMode(_text(mode).lower() or RuntimeMode.LEGACY.value)
        except ValueError as exc:
            raise RuntimeRouterError(RUNTIME_MODE_INVALID, "runtime mode must be legacy, shadow, or canonical") from exc
        self.canary_percent = _exact_int(canary_percent, "canary_percent", minimum=0, maximum=100)
        self.kill_switch = bool(kill_switch)
        self.canonical_owner = _text(canonical_owner)
        self.confirmed_owner = _text(confirmed_owner)
        if not callable(clock):
            raise RuntimeRouterError(RUNTIME_ASSIGNMENT_INVALID, "clock must be callable")
        self._clock = clock

    def assign_new_task(
        self,
        work_order_id: str,
        *,
        assigned_at_ms: int | None = None,
    ) -> RuntimeAssignment:
        work_ref = _required_text(work_order_id, "work_order_id")
        ts_ms = int(self._clock() if assigned_at_ms is None else assigned_at_ms)
        if ts_ms < 0:
            raise RuntimeRouterError(
                RUNTIME_ASSIGNMENT_INVALID,
                "assigned_at_ms must be non-negative",
            )
        if self.kill_switch:
            return self._assignment(
                work_ref,
                executor=RuntimeExecutor.LEGACY,
                canary_selected=False,
                reason_code=RUNTIME_KILL_SWITCH_ACTIVE,
                assigned_at_ms=ts_ms,
                is_new_task=True,
            )
        if self.mode is RuntimeMode.LEGACY:
            return self._assignment(
                work_ref,
                executor=RuntimeExecutor.LEGACY,
                canary_selected=False,
                reason_code=RUNTIME_NEW_TASK_LEGACY,
                assigned_at_ms=ts_ms,
                is_new_task=True,
            )
        if self.mode is RuntimeMode.SHADOW:
            return self._assignment(
                work_ref,
                executor=RuntimeExecutor.LEGACY,
                canary_selected=False,
                reason_code=RUNTIME_SHADOW_LEGACY_EXECUTOR,
                assigned_at_ms=ts_ms,
                is_new_task=True,
            )
        self._require_canonical_owner()
        selected = _stable_percent(work_ref) < self.canary_percent
        return self._assignment(
            work_ref,
            executor=RuntimeExecutor.CANONICAL if selected else RuntimeExecutor.LEGACY,
            canary_selected=selected,
            reason_code=RUNTIME_NEW_TASK_CANONICAL if selected else RUNTIME_NEW_TASK_LEGACY,
            assigned_at_ms=ts_ms,
            is_new_task=True,
        )

    def preserve_existing_assignment(
        self,
        assignment: RuntimeAssignment | Mapping[str, Any] | None,
        *,
        work_order_id: str,
    ) -> RuntimeAssignment:
        if assignment is not None:
            existing = (
                assignment
                if isinstance(assignment, RuntimeAssignment)
                else RuntimeAssignment.from_mapping(assignment)
            )
            if existing.work_order_id != _text(work_order_id):
                raise RuntimeRouterError(
                    RUNTIME_ASSIGNMENT_INVALID,
                    "existing runtime assignment belongs to a different WorkOrder",
                )
            return existing
        return self._assignment(
            _required_text(work_order_id, "work_order_id"),
            executor=RuntimeExecutor.LEGACY,
            canary_selected=False,
            reason_code=RUNTIME_LEGACY_DRAIN_INCOMPLETE,
            assigned_at_ms=int(self._clock()),
            is_new_task=False,
        )

    def validate_resume_executor(
        self,
        assignment: RuntimeAssignment | Mapping[str, Any],
        *,
        requested_executor: RuntimeExecutor | str,
    ) -> None:
        existing = (
            assignment
            if isinstance(assignment, RuntimeAssignment)
            else RuntimeAssignment.from_mapping(assignment)
        )
        try:
            requested = RuntimeExecutor(_text(requested_executor).lower())
        except ValueError as exc:
            raise RuntimeRouterError(RUNTIME_ASSIGNMENT_INVALID, "requested_executor is invalid") from exc
        if requested is not existing.executor:
            raise RuntimeRouterError(
                RUNTIME_CROSS_EXECUTOR_RESUME_REJECTED,
                f"resume requested {requested.value} but WorkOrder is assigned to {existing.executor.value}",
            )

    def _assignment(
        self,
        work_order_id: str,
        *,
        executor: RuntimeExecutor,
        canary_selected: bool,
        reason_code: str,
        assigned_at_ms: int,
        is_new_task: bool,
    ) -> RuntimeAssignment:
        digest = hashlib.sha256(
            json.dumps(
                {
                    "work_order_id": work_order_id,
                    "executor": executor.value,
                    "mode": self.mode.value,
                    "canary_percent": self.canary_percent,
                    "is_new_task": bool(is_new_task),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return RuntimeAssignment(
            work_order_id=work_order_id,
            executor=executor,
            mode=self.mode,
            assignment_id=f"runtime-assignment://{work_order_id}/{digest}",
            assigned_at_ms=assigned_at_ms,
            canary_percent=self.canary_percent,
            canary_selected=canary_selected,
            kill_switch_active=self.kill_switch,
            is_new_task=bool(is_new_task),
            reason_code=reason_code,
        )

    def _require_canonical_owner(self) -> None:
        if self.canary_percent <= 0:
            return
        if not self.canonical_owner or self.confirmed_owner != self.canonical_owner:
            raise RuntimeRouterError(
                RUNTIME_CANARY_OWNER_REQUIRED,
                "canonical canary requires matching owner confirmation",
            )


def build_legacy_drain_report(jobs: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    terminal = {"COMPLETED", "FAILED", "CANCELLED"}
    rows = [dict(item) for item in jobs if isinstance(item, Mapping)]
    open_jobs = [
        str(row.get("job_id") or "")
        for row in rows
        if _text(row.get("status")).upper() not in terminal
    ]
    status_counts: dict[str, int] = {}
    for row in rows:
        status = _text(row.get("status")).upper() or "UNKNOWN"
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "legacy_job_count": len(rows),
        "legacy_open_job_count": len([item for item in open_jobs if item]),
        "legacy_open_job_ids": tuple(item for item in open_jobs if item),
        "status_counts": status_counts,
        "drained": len([item for item in open_jobs if item]) == 0,
        "reason_code": None if not open_jobs else RUNTIME_LEGACY_DRAIN_INCOMPLETE,
    }


def build_canary_metrics(assignments: Sequence[RuntimeAssignment | Mapping[str, Any]]) -> dict[str, Any]:
    normalized = [
        item if isinstance(item, RuntimeAssignment) else RuntimeAssignment.from_mapping(item)
        for item in assignments
    ]
    canonical = [item for item in normalized if item.executor is RuntimeExecutor.CANONICAL]
    legacy = [item for item in normalized if item.executor is RuntimeExecutor.LEGACY]
    return {
        "assignment_count": len(normalized),
        "canonical_count": len(canonical),
        "legacy_count": len(legacy),
        "canary_selected_count": sum(1 for item in normalized if item.canary_selected),
        "kill_switch_count": sum(1 for item in normalized if item.kill_switch_active),
    }


def detect_duplicate_side_effects(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    seen: dict[str, dict[str, Any]] = {}
    duplicates: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, Mapping):
            continue
        fingerprint = _text(
            record.get("operation_fingerprint")
            or record.get("business_idempotency_key")
            or record.get("idempotency_key")
        )
        if not fingerprint:
            continue
        current = {
            "operation_fingerprint": fingerprint,
            "executor": _text(record.get("executor")),
            "attempt_id": _text(record.get("attempt_id")),
            "receipt_ref": _text(record.get("receipt_ref")),
        }
        if fingerprint in seen:
            duplicates.append({"first": seen[fingerprint], "duplicate": current})
        else:
            seen[fingerprint] = current
    return {
        "side_effect_record_count": len(seen) + len(duplicates),
        "duplicate_side_effect_count": len(duplicates),
        "duplicates": tuple(duplicates),
        "reason_code": None if not duplicates else RUNTIME_DUPLICATE_SIDE_EFFECT,
    }


def assert_no_duplicate_side_effects(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    report = detect_duplicate_side_effects(records)
    if int(report["duplicate_side_effect_count"]) > 0:
        raise RuntimeRouterError(
            RUNTIME_DUPLICATE_SIDE_EFFECT,
            f"duplicate side effects: {report['duplicate_side_effect_count']}",
        )
    return report


def _stable_percent(value: str) -> int:
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 100


def _text(value: Any) -> str:
    return str(value or "").strip()


def _required_text(value: Any, field_name: str) -> str:
    text = _text(value)
    if not text:
        raise RuntimeRouterError(RUNTIME_ASSIGNMENT_INVALID, f"{field_name} is required")
    return text


def _exact_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise RuntimeRouterError(RUNTIME_ASSIGNMENT_INVALID, f"{field_name} must be a boolean")
    return value


def _exact_int(value: Any, field_name: str, *, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise RuntimeRouterError(
            RUNTIME_ASSIGNMENT_INVALID,
            f"{field_name} must be an exact integer >= {minimum}",
        )
    if maximum is not None and value > maximum:
        raise RuntimeRouterError(
            RUNTIME_ASSIGNMENT_INVALID,
            f"{field_name} must be <= {maximum}",
        )
    return value


__all__ = [
    "RUNTIME_ASSIGNMENT_INVALID",
    "RUNTIME_ASSIGNMENT_SCHEMA_VERSION",
    "RUNTIME_CANARY_OWNER_REQUIRED",
    "RUNTIME_CROSS_EXECUTOR_RESUME_REJECTED",
    "RUNTIME_DUPLICATE_SIDE_EFFECT",
    "RUNTIME_KILL_SWITCH_ACTIVE",
    "RUNTIME_LEGACY_DRAIN_INCOMPLETE",
    "RUNTIME_MODE_INVALID",
    "RUNTIME_NEW_TASK_CANONICAL",
    "RUNTIME_NEW_TASK_LEGACY",
    "RUNTIME_SHADOW_LEGACY_EXECUTOR",
    "CanonicalRuntimeRouter",
    "RuntimeAssignment",
    "RuntimeExecutor",
    "RuntimeMode",
    "RuntimeRouterError",
    "assert_no_duplicate_side_effects",
    "build_canary_metrics",
    "build_legacy_drain_report",
    "detect_duplicate_side_effects",
]
