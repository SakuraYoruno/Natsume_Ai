"""Atomic terminal closure for one committed single-node budget rejection."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import inspect
from typing import Any

from .admission import AdmittedExecution
from .coordinator_receipts import build_execution_receipt
from .coordinator_records import (
    _mapping,
    _required_record_text,
    _status,
)
from .errors import GOVERNED_OPERATION_RECOVERY_REQUIRED, GovernedRuntimeError
from .ports import InternalAgentCheckpointUnitOfWorkPort
from .runtime_outbox import RuntimeOutbox


def is_internal_agent_checkpoint_authority(
    command: Any,
) -> bool:
    return (
        str(getattr(command.surface, "value", command.surface)).lower()
        == "internal"
        and str(command.route_type).lower() == "delegate"
        and str(command.target_id) == "agent_runtime"
    )


class InternalCheckpointBudgetRejectionRuntime:
    """Use one Store-owned transaction for internal or canonical checkpoints."""

    def __init__(
        self,
        *,
        checkpoint_uow: InternalAgentCheckpointUnitOfWorkPort,
        runtime_outbox: RuntimeOutbox,
        owner_id: str,
    ) -> None:
        self._checkpoint_uow = checkpoint_uow
        self._runtime_outbox = runtime_outbox
        self._owner_id = str(owner_id)

    async def reject(
        self,
        *,
        admitted: AdmittedExecution,
        charge: Any,
        attempt: Mapping[str, Any],
        node: Mapping[str, Any],
        graph: Mapping[str, Any],
        reservation: Mapping[str, Any],
        lease: Mapping[str, Any],
        fencing_token: int,
        evidence_refs: Sequence[str],
        require_internal_agent: bool = True,
    ) -> Any:
        attempt_id = _required_record_text(attempt, "attempt_id")
        error_ref = "taskerror://" + hashlib.sha256(
            f"{attempt_id}\nGOVERNED_BUDGET_EXCEEDED".encode("utf-8")
        ).hexdigest()
        evidence = tuple(
            dict.fromkeys(
                (
                    error_ref,
                    str(charge.evidence_ref),
                    str(charge.usage_id),
                    *tuple(evidence_refs),
                )
            )
        )
        active_lease = _mapping(
            lease,
            field_name="Internal Agent budget rejection Lease",
        )
        arguments = {
            "reservation_id": _required_record_text(
                reservation,
                "reservation_id",
            ),
            "budget_id": charge.context.budget_id,
            "lease_id": _required_record_text(active_lease, "lease_id"),
            "owner_id": self._owner_id,
            "fencing_token": fencing_token,
            "expected_attempt_version": int(attempt.get("status_version") or 0),
            "expected_node_version": int(node.get("status_version") or 0),
            "expected_graph_version": int(graph.get("status_version") or 0),
            "expected_budget_version": charge.context.version,
            "expected_lease_version": int(
                active_lease.get("status_version") or 0
            ),
            "error_ref": error_ref,
            "usage_id": str(charge.usage_id),
            "attempt_outbox": self._runtime_outbox.build(
                command_id=admitted.command_id,
                aggregate_type="TASK_ATTEMPT",
                aggregate_id=attempt_id,
                aggregate_version=int(attempt.get("status_version") or 0) + 1,
                event_type="TASK_ATTEMPT_FAILED",
                attempt_id=attempt_id,
                evidence_refs=evidence,
            ),
        }
        mutation = (
            self._checkpoint_uow.reject_internal_agent_checkpoint_budget_bundle
            if require_internal_agent
            else self._checkpoint_uow.reject_committed_checkpoint_budget_bundle
        )
        reopen = (
            self._checkpoint_uow.reopen_internal_agent_budget_rejection_terminal
            if require_internal_agent
            else self._checkpoint_uow.reopen_committed_checkpoint_budget_rejection_terminal
        )
        try:
            result_raw = await _maybe_await(
                mutation(
                    attempt_id,
                    **arguments,
                )
            )
        except Exception as mutation_error:
            # The mutation may have committed before its response was lost.
            # Reopen the exact terminal through the read-only authority; never
            # invoke the terminal mutation a second time.
            try:
                result_raw = await _maybe_await(
                    reopen(
                        attempt_id,
                        **arguments,
                    )
                )
            except Exception as reopen_error:
                if (
                    str(getattr(reopen_error, "reason_code", ""))
                    == "TASK_STORE_DURABLE_DATA_INVALID"
                ):
                    raise
                raise mutation_error
        result = _mapping(
            result_raw,
            field_name="atomic committed checkpoint budget rejection",
        )
        terminal_attempt = _mapping(result.get("attempt"), field_name="terminal attempt")
        terminal_node = _mapping(result.get("node"), field_name="terminal node")
        terminal_graph = _mapping(result.get("graph"), field_name="terminal graph")
        terminal_lease = _mapping(result.get("lease"), field_name="terminal lease")
        terminal_budget = _mapping(result.get("budget"), field_name="terminal budget")
        if (
            _status(terminal_attempt) != "FAILED"
            or _status(terminal_node) != "FAILED"
            or _status(terminal_graph) != "BLOCKED"
            or _status(terminal_lease) != "RELEASED"
            or _status(terminal_budget) != "RELEASED"
            or str(result.get("usage_id") or "") != str(charge.usage_id)
        ):
            raise GovernedRuntimeError(
                GOVERNED_OPERATION_RECOVERY_REQUIRED,
                "atomic committed checkpoint budget rejection is incomplete",
            )
        await self._runtime_outbox.publish_pending()
        terminal_context = charge.context.refreshed(terminal_budget)
        return build_execution_receipt(
            admitted,
            attempt_id=attempt_id,
            status="BUDGET_BLOCKED",
            verification_status="NOT_RUN",
            accepted=False,
            reason_code="GOVERNED_BUDGET_EXCEEDED",
            evidence_refs=evidence,
            projection={
                "budget": terminal_context.execution_payload(),
                "exceeded_dimensions": list(charge.exceeded_dimensions),
                "requested_action": terminal_context.policy.on_exceeded.value,
                "enforced_action": "BLOCK",
            },
        )


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


__all__ = [
    "InternalCheckpointBudgetRejectionRuntime",
    "is_internal_agent_checkpoint_authority",
]
