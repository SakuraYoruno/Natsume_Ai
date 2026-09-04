"""Project one accepted Scheduler operation after durable Effect delivery.

The Task Runtime accepts only an authority-free operation plan.  This focused
runtime owns the next boundary: dispatch the already-staged Acceptance Effect,
then reopen the immutable Scheduler result and occurrence before exposing a
domain result to the caller.  It never invokes a Scheduler worker directly.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from host_brain.core.runtime_helpers import maybe_await as _resolve
from natsume_contracts.task import HostCommand


class SchedulerAcceptedEffectRuntime:
    """Dispatch and attribute a post-Acceptance Scheduler operation."""

    def __init__(
        self,
        *,
        dispatcher: Any,
        result_repository: Any,
        occurrence_repository: Any,
    ) -> None:
        if not callable(getattr(dispatcher, "dispatch_graph", None)):
            raise TypeError("Scheduler Acceptance Effect dispatcher is required")
        if not callable(getattr(result_repository, "get_by_command", None)):
            raise TypeError("Scheduler result repository is required")
        if not callable(getattr(occurrence_repository, "get_occurrence", None)):
            raise TypeError("Scheduler occurrence repository is required")
        self._dispatcher = dispatcher
        self._results = result_repository
        self._occurrences = occurrence_repository

    async def dispatch(
        self,
        event: Mapping[str, Any],
        *,
        command: HostCommand,
    ) -> dict[str, Any]:
        projected = dict(event or {})
        receipt_raw = projected.get("execution_receipt")
        receipt = dict(receipt_raw) if isinstance(receipt_raw, Mapping) else {}
        if (
            str(receipt.get("status") or "").strip().upper() != "ACCEPTED"
            or receipt.get("accepted") is not True
        ):
            return projected

        results = await self._dispatcher.dispatch_graph(
            graph_id=command.graph_id,
            graph_revision=command.graph_revision,
        )
        statuses = [str(row.get("status") or "").strip().upper() for row in results]
        recovery_pending = sum(
            bool(row.get("recovery_pending"))
            or str(row.get("dispatch_state") or "").strip().upper()
            == "RECOVERY_PENDING"
            for row in results
        )
        summary = {
            "count": len(results),
            "committed": statuses.count("COMMITTED"),
            "failed": statuses.count("FAILED"),
            "pending": sum(
                status not in {"COMMITTED", "FAILED"} for status in statuses
            ),
            "recovery_pending": recovery_pending,
            "dispatch_status": (
                "FAILED"
                if "FAILED" in statuses
                else "RECOVERY_PENDING"
                if recovery_pending
                else "COMMITTED"
                if results and all(status == "COMMITTED" for status in statuses)
                else "NO_EFFECTS"
            ),
            "effect_ids": [
                str(row.get("effect_id") or "").strip()
                for row in results
                if str(row.get("effect_id") or "").strip()
            ],
        }
        audit_refs = dict(projected.get("audit_refs") or {})
        audit_refs["acceptance_effects"] = summary
        projected["audit_refs"] = audit_refs
        if summary["dispatch_status"] != "COMMITTED":
            projected.update(
                {
                    "scheduler_effect_status": (
                        "FAILED"
                        if summary["dispatch_status"] == "FAILED"
                        else "RECOVERY_REQUIRED"
                    ),
                    "reason_code": (
                        "SCHEDULER_ACCEPTANCE_EFFECT_FAILED"
                        if summary["dispatch_status"] == "FAILED"
                        else "SCHEDULER_ACCEPTANCE_EFFECT_RECOVERY_REQUIRED"
                    ),
                }
            )
            return projected

        try:
            result_record = await _resolve(
                self._results.get_by_command(command.command_id)
            )
        except Exception:
            return _recovery_required(
                projected,
                "SCHEDULER_EFFECT_RESULT_ATTRIBUTION_UNAVAILABLE",
            )
        if not isinstance(result_record, Mapping) or not isinstance(
            result_record.get("result"),
            Mapping,
        ):
            return _recovery_required(
                projected,
                "SCHEDULER_EFFECT_RESULT_ATTRIBUTION_MISSING",
            )
        try:
            occurrence_record = await _resolve(
                self._occurrences.get_occurrence(
                    str(result_record.get("occurrence_id") or "")
                )
            )
        except Exception:
            return _recovery_required(
                projected,
                "SCHEDULER_EFFECT_OCCURRENCE_ATTRIBUTION_UNAVAILABLE",
            )
        if not isinstance(occurrence_record, Mapping) or (
            str(occurrence_record.get("occurrence_id") or "")
            != str(result_record.get("occurrence_id") or "")
            or not str(occurrence_record.get("task_id") or "").strip()
        ):
            return _recovery_required(
                projected,
                "SCHEDULER_EFFECT_OCCURRENCE_ATTRIBUTION_MISSING",
            )

        result = dict(result_record["result"])
        result_receipt_ref = str(result_record.get("receipt_ref") or "").strip()
        if not result_receipt_ref:
            return _recovery_required(
                projected,
                "SCHEDULER_EFFECT_RESULT_ATTRIBUTION_MISSING",
            )
        projected.update(
            {
                "occurrence_id": str(result_record.get("occurrence_id") or ""),
                "task_id": str(occurrence_record.get("task_id") or ""),
                "result": result,
                "receipt_ref": result_receipt_ref,
                "scheduler_result_receipt_ref": result_receipt_ref,
                "scheduler_effect_status": "COMMITTED",
                "narrative": str(
                    result.get("summary")
                    or result.get("detail")
                    or result.get("status")
                    or ""
                )[:500],
            }
        )
        projection = dict(receipt.get("projection") or {})
        projection["scheduler_operation_result"] = {
            "receipt_ref": result_receipt_ref,
            "status": str(result.get("status") or ""),
        }
        receipt["projection"] = projection
        projected["execution_receipt"] = receipt
        return projected


def _recovery_required(
    projected: Mapping[str, Any],
    reason_code: str,
) -> dict[str, Any]:
    return {
        **dict(projected),
        "scheduler_effect_status": "RECOVERY_REQUIRED",
        "reason_code": str(reason_code),
    }


__all__ = ["SchedulerAcceptedEffectRuntime"]
