"""Canonical-only WorkOrder executor assignment.

Production composition has one executor authority and every new or resumed
WorkOrder is bound to it by an immutable assignment digest.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping

from natsume_contracts.task import (
    EXECUTOR_ASSIGNMENT_SCHEMA_VERSION,
    ExecutorAssignment,
    TaskContractError,
    TaskExecutor,
)

from host_brain.core.ids import now_ms


RUNTIME_ASSIGNMENT_SCHEMA_VERSION = EXECUTOR_ASSIGNMENT_SCHEMA_VERSION
RUNTIME_ASSIGNMENT_INVALID = "RUNTIME_ASSIGNMENT_INVALID"
RUNTIME_CROSS_EXECUTOR_RESUME_REJECTED = "RUNTIME_CROSS_EXECUTOR_RESUME_REJECTED"
RUNTIME_DUPLICATE_SIDE_EFFECT = "RUNTIME_DUPLICATE_SIDE_EFFECT"
RUNTIME_NEW_TASK_CANONICAL = "RUNTIME_NEW_TASK_CANONICAL"

RuntimeRouterError = TaskContractError
RuntimeExecutor = TaskExecutor
RuntimeAssignment = ExecutorAssignment


class CanonicalRuntimeRouter:
    """Assign every WorkOrder to the sole canonical executor."""

    def __init__(self, *, clock: Any = now_ms) -> None:
        if not callable(clock):
            raise RuntimeRouterError(RUNTIME_ASSIGNMENT_INVALID, "clock must be callable")
        self._clock = clock

    def assign_new_task(
        self,
        work_order_id: str,
        *,
        assigned_at_ms: int | None = None,
    ) -> RuntimeAssignment:
        return self._assignment(
            _required_text(work_order_id, "work_order_id"),
            assigned_at_ms=int(self._clock() if assigned_at_ms is None else assigned_at_ms),
            is_new_task=True,
        )

    def preserve_existing_assignment(
        self,
        assignment: RuntimeAssignment | Mapping[str, Any] | None,
        *,
        work_order_id: str,
    ) -> RuntimeAssignment:
        work_ref = _required_text(work_order_id, "work_order_id")
        if assignment is None:
            return self._assignment(
                work_ref,
                assigned_at_ms=int(self._clock()),
                is_new_task=False,
            )
        existing = (
            assignment
            if isinstance(assignment, RuntimeAssignment)
            else RuntimeAssignment.from_mapping(assignment)
        )
        if existing.work_order_id != work_ref:
            raise RuntimeRouterError(
                RUNTIME_ASSIGNMENT_INVALID,
                "existing runtime assignment belongs to a different WorkOrder",
            )
        if existing.executor is not RuntimeExecutor.CANONICAL:
            raise RuntimeRouterError(
                RUNTIME_CROSS_EXECUTOR_RESUME_REJECTED,
                "non-canonical assignments cannot resume after legacy drain",
            )
        return existing

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
            requested = RuntimeExecutor(str(getattr(requested_executor, "value", requested_executor)).strip().lower())
        except ValueError as exc:
            raise RuntimeRouterError(RUNTIME_ASSIGNMENT_INVALID, "requested_executor is invalid") from exc
        if requested is not RuntimeExecutor.CANONICAL or existing.executor is not requested:
            raise RuntimeRouterError(
                RUNTIME_CROSS_EXECUTOR_RESUME_REJECTED,
                "resume executor must be canonical and match the durable assignment",
            )

    def _assignment(
        self,
        work_order_id: str,
        *,
        assigned_at_ms: int,
        is_new_task: bool,
    ) -> RuntimeAssignment:
        if assigned_at_ms < 0:
            raise RuntimeRouterError(
                RUNTIME_ASSIGNMENT_INVALID,
                "assigned_at_ms must be non-negative",
            )
        digest = hashlib.sha256(
            json.dumps(
                {
                    "work_order_id": work_order_id,
                    "executor": RuntimeExecutor.CANONICAL.value,
                    "is_new_task": bool(is_new_task),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return RuntimeAssignment(
            work_order_id=work_order_id,
            executor=RuntimeExecutor.CANONICAL,
            assignment_id=f"runtime-assignment://{work_order_id}/{digest}",
            assigned_at_ms=assigned_at_ms,
            is_new_task=bool(is_new_task),
            reason_code=RUNTIME_NEW_TASK_CANONICAL,
        )


def detect_duplicate_side_effects(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    seen: dict[str, dict[str, Any]] = {}
    duplicates: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, Mapping):
            continue
        fingerprint = str(
            record.get("operation_fingerprint")
            or record.get("business_idempotency_key")
            or record.get("idempotency_key")
            or ""
        ).strip()
        if not fingerprint:
            continue
        current = {
            "operation_fingerprint": fingerprint,
            "executor": str(record.get("executor") or ""),
            "attempt_id": str(record.get("attempt_id") or ""),
            "receipt_ref": str(record.get("receipt_ref") or ""),
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


def _required_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise RuntimeRouterError(RUNTIME_ASSIGNMENT_INVALID, f"{field_name} is required")
    return text


__all__ = [
    "RUNTIME_ASSIGNMENT_INVALID",
    "RUNTIME_ASSIGNMENT_SCHEMA_VERSION",
    "RUNTIME_CROSS_EXECUTOR_RESUME_REJECTED",
    "RUNTIME_DUPLICATE_SIDE_EFFECT",
    "RUNTIME_NEW_TASK_CANONICAL",
    "CanonicalRuntimeRouter",
    "RuntimeAssignment",
    "RuntimeExecutor",
    "RuntimeRouterError",
    "assert_no_duplicate_side_effects",
    "detect_duplicate_side_effects",
]
