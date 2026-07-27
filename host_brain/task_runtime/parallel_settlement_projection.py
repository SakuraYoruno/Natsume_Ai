"""Read-only durable projection for parallel settlement."""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any

from host_brain.persistence.task_store_types import TASK_STORE_BUDGET_EXCEEDED

from .admission import AdmittedExecution
from .budget_runtime import BudgetRuntimeContext
from .coordinator_receipts import build_execution_receipt, terminal_event
from .coordinator_records import (
    _ParallelNodeOutcome,
    _evidence_refs,
    _mapping,
    _record_evidence_refs,
    _required_record_text,
    _status,
    _text,
)
from .errors import (
    GOVERNED_BUDGET_EXCEEDED,
    GOVERNED_DURABLE_STATE_CONFLICT,
    GOVERNED_EXECUTION_IN_PROGRESS,
    GovernedRuntimeError,
)
from .ports import TaskStorePort


class ParallelSettlementProjection:
    """Validate the durable read model used by the 0090 writer."""

    __slots__ = ("_store",)

    def __init__(self, *, task_store: TaskStorePort) -> None:
        self._store = task_store

    async def assert_parallel_reentry_idle(
        self,
        *,
        admitted: AdmittedExecution,
        durable_nodes: Mapping[str, Mapping[str, Any]],
    ) -> None:
        active_node_statuses = frozenset(
            {
                "LEASED",
                "RUNNING",
                "EXECUTED",
                "VERIFYING",
                "VERIFIED",
                "CANCEL_REQUESTED",
            }
        )
        active_attempt_statuses = frozenset(
            {
                "CREATED",
                "LEASED",
                "RUNNING",
                "RECOVERABLE",
                "CANCEL_REQUESTED",
            }
        )
        active_nodes = tuple(
            sorted(
                node_id
                for node_id, node in durable_nodes.items()
                if _status(node) in active_node_statuses
            )
        )
        snapshot = _mapping(
            await _maybe_await(
                self._store.read_scheduler_snapshot(
                    admitted.graph_id,
                    admitted.graph_revision,
                )
            ),
            field_name="parallel re-entry scheduler snapshot",
        )
        raw_leases = snapshot.get("active_leases")
        raw_attempts = snapshot.get("attempts")
        if not isinstance(raw_leases, list) or not isinstance(raw_attempts, list):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "parallel re-entry scheduler snapshot is incomplete",
            )
        active_leases = tuple(
            sorted(
                _text(lease.get("lease_id"))
                for lease in raw_leases
                if isinstance(lease, Mapping) and _text(lease.get("lease_id"))
            )
        )
        active_attempts = tuple(
            sorted(
                _text(attempt.get("attempt_id"))
                for attempt in raw_attempts
                if isinstance(attempt, Mapping)
                and _status(attempt) in active_attempt_statuses
                and _text(attempt.get("attempt_id"))
            )
        )
        if active_nodes or active_leases or active_attempts:
            raise GovernedRuntimeError(
                GOVERNED_EXECUTION_IN_PROGRESS,
                "parallel TaskGraph still has an in-progress durable batch",
            )

    async def durable_budget_blocked_outcome(
        self,
        *,
        admitted: AdmittedExecution,
        outcomes: tuple[_ParallelNodeOutcome, ...],
        budget: BudgetRuntimeContext,
    ) -> _ParallelNodeOutcome | None:
        snapshot = _mapping(
            await _maybe_await(
                self._store.read_scheduler_snapshot(
                    admitted.graph_id,
                    admitted.graph_revision,
                )
            ),
            field_name="parallel Budget outcome snapshot",
        )
        raw_usages = snapshot.get("budget_usage")
        if not isinstance(raw_usages, list):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "parallel Budget outcome has no durable usage ledger",
            )
        outcomes_by_lineage = {
            (outcome.node_id, outcome.attempt_id): outcome for outcome in outcomes
        }
        terminal_winners: list[Mapping[str, Any]] = []
        for raw_usage in raw_usages:
            if not isinstance(raw_usage, Mapping):
                continue
            node_id = _text(raw_usage.get("node_id"))
            attempt_id = _text(raw_usage.get("attempt_id"))
            exceeded_dimensions = raw_usage.get("exceeded_dimensions")
            if (
                _text(raw_usage.get("budget_id")) != budget.budget_id
                or raw_usage.get("accepted") is not False
                or _text(raw_usage.get("reason_code")) != TASK_STORE_BUDGET_EXCEEDED
                or not isinstance(exceeded_dimensions, list)
                or not exceeded_dimensions
            ):
                continue
            terminal_winners.append(raw_usage)
        if len(terminal_winners) != 1:
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "EXCEEDED Budget must have exactly one canonical winning usage "
                "in the durable graph ledger",
            )
        usage = terminal_winners[0]
        winner_node_id = _required_record_text(usage, "node_id")
        winner_attempt_id = _required_record_text(usage, "attempt_id")
        source = outcomes_by_lineage.get(
            (winner_node_id, winner_attempt_id)
        )
        if source is None:
            source = await self._reopen_durable_budget_winner_outcome(
                admitted=admitted,
                snapshot=snapshot,
                usage=usage,
            )
        if source is None:
            return None
        usage_ref = _required_record_text(usage, "usage_id")
        event = dict(source.event)
        receipt_raw = event.get("execution_receipt")
        receipt = dict(receipt_raw) if isinstance(receipt_raw, Mapping) else {}
        evidence_refs = tuple(dict.fromkeys((*_evidence_refs(receipt), usage_ref)))
        receipt.update(
            {
                "status": "BUDGET_BLOCKED",
                "accepted": False,
                "reason_code": GOVERNED_BUDGET_EXCEEDED,
                "evidence_refs": list(evidence_refs),
            }
        )
        event.update(
            {
                "phase": "TERMINAL",
                "done": True,
                "decision": "REJECT",
                "reason_code": GOVERNED_BUDGET_EXCEEDED,
                "execution_receipt": receipt,
            }
        )
        return _ParallelNodeOutcome(
            node_id=source.node_id,
            attempt_id=source.attempt_id,
            status="BUDGET_BLOCKED",
            accepted=False,
            reason_code=GOVERNED_BUDGET_EXCEEDED,
            event=event,
        )

    async def _reopen_durable_budget_winner_outcome(
        self,
        *,
        admitted: AdmittedExecution,
        snapshot: Mapping[str, Any],
        usage: Mapping[str, Any],
    ) -> _ParallelNodeOutcome | None:
        """Rebuild the winner when another fenced owner closed it first.

        Parallel checkpoint recovery is intentionally multi-owner safe.  The
        caller's local outcome tuple can omit the canonical EXCEEDED winner
        when a competing owner has already finished that child.  In that case
        only the exact durable FAILED Attempt/Node, COMMITTED Operation and
        released Lease may stand in for the missing process-local event.
        """

        node_id = _required_record_text(usage, "node_id")
        attempt_id = _required_record_text(usage, "attempt_id")
        if node_id not in {binding.node_id for binding in admitted.nodes}:
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "durable Budget winner is outside the admitted TaskGraph",
            )
        raw_attempts = snapshot.get("attempts")
        raw_leases = snapshot.get("active_leases")
        raw_graph = snapshot.get("graph_record")
        raw_nodes = (
            raw_graph.get("nodes")
            if isinstance(raw_graph, Mapping)
            else None
        )
        if (
            not isinstance(raw_attempts, list)
            or not isinstance(raw_leases, list)
            or not isinstance(raw_nodes, list)
        ):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "durable Budget winner snapshot is incomplete",
            )
        attempts = [
            dict(item)
            for item in raw_attempts
            if isinstance(item, Mapping)
            and _text(item.get("node_id")) == node_id
        ]
        if not attempts:
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "durable Budget winner has no Attempt lineage",
            )
        latest = max(
            attempts,
            key=lambda item: (
                int(item.get("attempt_no") or 0),
                _text(item.get("attempt_id")),
            ),
        )
        matching_nodes = [
            dict(item)
            for item in raw_nodes
            if isinstance(item, Mapping)
            and _text(item.get("node_id")) == node_id
        ]
        if len(matching_nodes) != 1:
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "durable Budget winner node snapshot is ambiguous",
            )
        node = matching_nodes[0]
        attempt = latest
        winner_admission = admitted.for_node(node_id)
        operation = _mapping(
            await _maybe_await(
                self._store.get_operation_reservation(
                    winner_admission.reservation_id
                )
            ),
            field_name="durable Budget winner operation",
        )
        active_lease = any(
            isinstance(item, Mapping)
            and _text(item.get("attempt_id")) == attempt_id
            for item in raw_leases
        )
        receipt_ref = _required_record_text(operation, "receipt_ref")
        node_status = _status(node)
        attempt_status = _status(attempt)
        if (
            _text(latest.get("attempt_id")) != attempt_id
            or _text(attempt.get("graph_id")) != admitted.graph_id
            or int(attempt.get("graph_revision") or 0)
            != admitted.graph_revision
            or _text(attempt.get("node_id")) != node_id
            or _text(operation.get("attempt_id")) != attempt_id
            or _status(operation) != "COMMITTED"
        ):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "durable Budget winner lineage or committed Operation drifted",
            )
        if (
            node_status in {
                "LEASED",
                "RUNNING",
                "EXECUTED",
                "VERIFYING",
                "VERIFIED",
                "CANCEL_REQUESTED",
            }
            and attempt_status in {
                "CREATED",
                "LEASED",
                "RUNNING",
                "RECOVERABLE",
                "CANCEL_REQUESTED",
            }
        ):
            # The winner has durable usage but its response-loss checkpoint
            # has not yet been closed.  Its own graph-ordered recovery must
            # produce the FAILED terminal; a sibling cannot synthesize it.
            return None
        if (
            node_status != "FAILED"
            or attempt_status != "FAILED"
            or active_lease
        ):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "durable Budget winner child terminal state drifted",
            )
        usage_id = _required_record_text(usage, "usage_id")
        evidence_refs = tuple(
            dict.fromkeys(
                reference
                for reference in (
                    *_record_evidence_refs(node),
                    *_record_evidence_refs(attempt),
                    _text(attempt.get("error_ref")),
                    usage_id,
                    _text(usage.get("outbox_id")),
                    receipt_ref,
                )
                if reference
            )
        )
        receipt = build_execution_receipt(
            winner_admission,
            attempt_id=attempt_id,
            receipt_ref=receipt_ref,
            status="BUDGET_BLOCKED",
            verification_status="NOT_RUN",
            accepted=False,
            replayed=True,
            reason_code=GOVERNED_BUDGET_EXCEEDED,
            evidence_refs=evidence_refs,
            projection={
                "budget_usage": dict(usage),
                "winner_reopen": "DURABLE_PARALLEL_CHECKPOINT",
            },
        )
        return _ParallelNodeOutcome(
            node_id=node_id,
            attempt_id=attempt_id,
            status="BUDGET_BLOCKED",
            accepted=False,
            reason_code=GOVERNED_BUDGET_EXCEEDED,
            event=terminal_event(receipt),
        )

    async def validate_parallel_outcome_snapshot(
        self,
        *,
        admitted: AdmittedExecution,
        outcomes: tuple[_ParallelNodeOutcome, ...],
    ) -> None:
        expected_durable_statuses = {
            "ACCEPTED": frozenset({"ACCEPTED"}),
            "RETRY_PENDING": frozenset({"RETRY_PENDING"}),
            "FAILED": frozenset({"FAILED"}),
            "BUDGET_BLOCKED": frozenset({"FAILED"}),
            "VERIFICATION_FAILED": frozenset({"VERIFICATION_FAILED"}),
            "WAITING_ACCEPTANCE_REVIEW": frozenset(
                {"WAITING_ACCEPTANCE_REVIEW"}
            ),
            "WAITING_APPROVAL": frozenset({"WAITING_APPROVAL"}),
            "CANCELLED": frozenset({"CANCELLED"}),
            # Recovery may be detected before the active Attempt/Lease can be
            # closed, or after a reservation conflict has already failed it.
            "RECOVERY_REQUIRED": frozenset({"RUNNING", "FAILED"}),
        }
        expected_attempt_statuses = {
            "ACCEPTED": frozenset({"SUCCEEDED"}),
            "WAITING_ACCEPTANCE_REVIEW": frozenset({"SUCCEEDED"}),
            "VERIFICATION_FAILED": frozenset({"SUCCEEDED"}),
            "FAILED": frozenset({"FAILED"}),
            "BUDGET_BLOCKED": frozenset({"FAILED"}),
            "RETRY_PENDING": frozenset({"FAILED"}),
            "CANCELLED": frozenset({"CANCELLED"}),
            "RECOVERY_REQUIRED": frozenset({"RUNNING", "FAILED"}),
        }
        admitted_node_ids = {binding.node_id for binding in admitted.nodes}
        observed_node_ids = [outcome.node_id for outcome in outcomes]
        if (
            len(set(observed_node_ids)) != len(observed_node_ids)
            or not set(observed_node_ids).issubset(admitted_node_ids)
        ):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "parallel outcome set does not match the admitted TaskGraph",
            )
        snapshot = _mapping(
            await _maybe_await(
                self._store.read_scheduler_snapshot(
                    admitted.graph_id,
                    admitted.graph_revision,
                )
            ),
            field_name="parallel outcome scheduler snapshot",
        )
        attempt_rows = snapshot.get("attempts")
        if not isinstance(attempt_rows, list):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "parallel outcome snapshot has no durable Attempts",
            )
        latest_attempt_by_node: dict[str, dict[str, Any]] = {}
        for raw_attempt in attempt_rows:
            if not isinstance(raw_attempt, Mapping):
                continue
            attempt_row = dict(raw_attempt)
            node_id = _text(attempt_row.get("node_id"))
            current = latest_attempt_by_node.get(node_id)
            sort_key = (
                int(attempt_row.get("attempt_no") or 0),
                _text(attempt_row.get("attempt_id")),
            )
            current_key = (
                int((current or {}).get("attempt_no") or 0),
                _text((current or {}).get("attempt_id")),
            )
            if current is None or sort_key > current_key:
                latest_attempt_by_node[node_id] = attempt_row
        for outcome in outcomes:
            allowed = expected_durable_statuses.get(outcome.status)
            if allowed is None:
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    f"parallel TaskNode {outcome.node_id} returned an unknown outcome",
                )
            durable_node = _mapping(
                await _maybe_await(
                    self._store.get_node(
                        admitted.graph_id,
                        admitted.graph_revision,
                        outcome.node_id,
                    )
                ),
                field_name=f"parallel outcome node {outcome.node_id}",
            )
            durable_status = _status(durable_node)
            if durable_status not in allowed:
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    (
                        f"parallel TaskNode {outcome.node_id} receipt status "
                        f"{outcome.status} conflicts with durable status {durable_status}"
                    ),
                )
            if outcome.status == "WAITING_APPROVAL":
                if outcome.attempt_id is not None:
                    raise GovernedRuntimeError(
                        GOVERNED_DURABLE_STATE_CONFLICT,
                        f"parallel TaskNode {outcome.node_id} pre-execution review "
                        "bound a TaskAttempt",
                    )
                continue
            if not outcome.attempt_id:
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    f"parallel TaskNode {outcome.node_id} receipt has no TaskAttempt",
                )
            durable_attempt = _mapping(
                await _maybe_await(self._store.get_attempt(outcome.attempt_id)),
                field_name=f"parallel outcome attempt {outcome.attempt_id}",
            )
            allowed_attempt_statuses = expected_attempt_statuses.get(outcome.status)
            if (
                allowed_attempt_statuses is None
                or _text(durable_attempt.get("graph_id")) != admitted.graph_id
                or int(durable_attempt.get("graph_revision") or 0)
                != admitted.graph_revision
                or _text(durable_attempt.get("node_id")) != outcome.node_id
                or _status(durable_attempt) not in allowed_attempt_statuses
                or _text(
                    latest_attempt_by_node.get(outcome.node_id, {}).get(
                        "attempt_id"
                    )
                )
                != outcome.attempt_id
            ):
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    f"parallel TaskNode {outcome.node_id} receipt Attempt lineage drifted",
                )


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


__all__ = ["ParallelSettlementProjection"]
