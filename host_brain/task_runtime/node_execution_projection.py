"""Read-only durable projection boundary for one TaskGraph node execution."""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any

from host_brain.contracts.agent_runtime.parallel_settlement import (
    ParallelSettlementContractError,
    normalize_parallel_settlement_decision,
)
from host_brain.contracts.agent_runtime.task_graph import TaskGraphCompletionPolicy

from .admission import AdmittedExecution
from .coordinator_receipts import (
    GovernedExecutionReceipt,
    _digest_from_receipt_ref,
    build_execution_receipt,
)
from .coordinator_records import (
    _attempt_id,
    _exception_reason,
    _mapping,
    _node_output_contract,
    _record_evidence_refs,
    _required_record_text,
    _status,
    _text,
    _version,
)
from .errors import (
    GOVERNED_BUDGET_EXCEEDED,
    GOVERNED_DURABLE_STATE_CONFLICT,
    GovernedRuntimeError,
)
from .ports import ExecutionProjectionReaderPort, TaskStorePort


class NodeExecutionProjection:
    """Rebuild terminal node receipts from canonical durable records."""

    __slots__ = ("_store", "_projection_reader")

    def __init__(
        self,
        *,
        task_store: TaskStorePort,
        projection_reader: ExecutionProjectionReaderPort | None,
    ) -> None:
        self._store = task_store
        self._projection_reader = projection_reader

    async def matching_parallel_decision(
        self,
        admitted: AdmittedExecution,
        *,
        graph: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        graph_status = _status(graph)
        if graph_status not in {"BLOCKED", "FAILED", "CANCELLED"}:
            return None
        if (
            len(admitted.nodes) <= 1
            and admitted.completion_policy
            != TaskGraphCompletionPolicy.ATOMIC_GRAPH_SUCCESS_V1.value
        ):
            return None
        raw = await _maybe_await(
            self._store.get_latest_parallel_settlement_decision(
                admitted.graph_id,
                admitted.graph_revision,
            )
        )
        if raw is None:
            return None
        if not isinstance(raw, Mapping):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "parallel settlement decision record is invalid",
            )
        payload = raw.get("payload")
        try:
            decision = normalize_parallel_settlement_decision(payload)
        except (ParallelSettlementContractError, TypeError) as exc:
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                f"parallel settlement replay decision is invalid: {exc}",
            ) from exc
        if (
            _text(raw.get("decision_ref")) != decision.decision_ref
            or decision.payload["graph_id"] != admitted.graph_id
            or decision.payload["graph_revision"] != admitted.graph_revision
        ):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "parallel settlement replay decision identity drifted",
            )
        if (
            decision.payload["graph_target_status"] != graph_status
            or decision.payload["graph_status_version_before"] + 1
            != _version(graph)
        ):
            return None
        selected = self.selected_parallel_decision_outcome(
            {"payload": decision.payload}
        )
        selected_admission = admitted.for_node(
            _required_record_text(selected, "node_id")
        )
        attempts = await self.attempts_for_node(selected_admission)
        if (
            not attempts
            or _required_record_text(attempts[-1], "attempt_id")
            != _required_record_text(selected, "attempt_id")
        ):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "parallel settlement replay selected Attempt is not latest",
            )
        record = dict(raw)
        record["decision_ref"] = decision.decision_ref
        record["payload"] = decision.payload
        return record

    @staticmethod
    def selected_parallel_decision_outcome(
        decision: Mapping[str, Any],
    ) -> dict[str, Any]:
        payload = _mapping(
            decision.get("payload"),
            field_name="parallel settlement replay payload",
        )
        selected_node_id = _required_record_text(payload, "selected_node_id")
        raw_outcomes = payload.get("outcomes")
        if not isinstance(raw_outcomes, list):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "parallel settlement replay outcomes are invalid",
            )
        selected = [
            dict(outcome)
            for outcome in raw_outcomes
            if isinstance(outcome, Mapping)
            and _text(outcome.get("node_id")) == selected_node_id
        ]
        if len(selected) != 1:
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "parallel settlement replay selected outcome is missing",
            )
        return selected[0]

    async def terminal_replay(
        self,
        admitted: AdmittedExecution,
        *,
        graph: Mapping[str, Any],
        node: Mapping[str, Any],
        allow_incomplete_atomic_graph_outcome: bool = False,
    ) -> GovernedExecutionReceipt | None:
        graph_status = _status(graph)
        node_status = _status(node)
        waiting_acceptance_review = (
            graph_status in {"ACTIVE", "BLOCKED"}
            and node_status == "WAITING_ACCEPTANCE_REVIEW"
        )
        terminal = (
            graph_status in {"COMPLETED", "FAILED", "CANCELLED"}
            or node_status in {
                "ACCEPTED",
                "FAILED",
                "RETRY_PENDING",
                "VERIFICATION_FAILED",
                "CANCELLED",
            }
            or waiting_acceptance_review
        )
        if not terminal:
            return None
        reservation = await self.try_operation(admitted.reservation_id)
        attempts = await self.attempts_for_node(admitted)
        latest = attempts[-1] if attempts else {}
        attempt_id = _text(latest.get("attempt_id")) or _attempt_id(admitted, attempt_no=1)
        reservation_status = _status(reservation or {})
        receipt_ref = _text((reservation or {}).get("receipt_ref")) or None
        checkpoint_budget_terminal: dict[str, Any] | None = None
        if graph_status == "BLOCKED" and node_status == "FAILED":
            raw_terminal = await _maybe_await(
                self._store.try_resolve_checkpoint_budget_rejection_terminal(
                    attempt_id,
                )
            )
            if raw_terminal is not None:
                checkpoint_budget_terminal = _mapping(
                    raw_terminal,
                    field_name="checkpoint budget rejection terminal",
                )
                terminal_graph = _mapping(
                    checkpoint_budget_terminal.get("graph"),
                    field_name="checkpoint budget rejection Graph",
                )
                terminal_node = _mapping(
                    checkpoint_budget_terminal.get("node"),
                    field_name="checkpoint budget rejection Node",
                )
                terminal_attempt = _mapping(
                    checkpoint_budget_terminal.get("attempt"),
                    field_name="checkpoint budget rejection Attempt",
                )
                terminal_budget = _mapping(
                    checkpoint_budget_terminal.get("budget"),
                    field_name="checkpoint budget rejection Budget",
                )
                terminal_reservation = _mapping(
                    checkpoint_budget_terminal.get("reservation"),
                    field_name="checkpoint budget rejection Operation",
                )
                terminal_usage = _mapping(
                    checkpoint_budget_terminal.get("usage"),
                    field_name="checkpoint budget rejection Usage",
                )
                terminal_error_ref = _required_record_text(
                    terminal_attempt,
                    "error_ref",
                )
                terminal_usage_id = _required_record_text(
                    terminal_usage,
                    "usage_id",
                )
                authority_kind = _required_record_text(
                    checkpoint_budget_terminal,
                    "authority_kind",
                )
                if (
                    authority_kind
                    not in {
                        "INTERNAL_AGENT_BUDGET_REJECTED",
                        "CANONICAL_CHECKPOINT_BUDGET_REJECTED",
                        "PARALLEL_CHECKPOINT_BUDGET_REJECTED",
                    }
                    or _required_record_text(latest, "error_ref")
                    != terminal_error_ref
                    or _required_record_text(terminal_graph, "graph_id")
                    != admitted.graph_id
                    or int(terminal_graph.get("revision") or 0)
                    != admitted.graph_revision
                    or _status(terminal_graph) != "BLOCKED"
                    or _version(terminal_graph) != _version(graph)
                    or _required_record_text(terminal_node, "graph_id")
                    != admitted.graph_id
                    or int(terminal_node.get("graph_revision") or 0)
                    != admitted.graph_revision
                    or _required_record_text(terminal_node, "node_id")
                    != admitted.node_id
                    or _status(terminal_node) != "FAILED"
                    or _version(terminal_node) != _version(node)
                    or _required_record_text(terminal_attempt, "graph_id")
                    != admitted.graph_id
                    or int(terminal_attempt.get("graph_revision") or 0)
                    != admitted.graph_revision
                    or _required_record_text(terminal_attempt, "node_id")
                    != admitted.node_id
                    or _required_record_text(terminal_attempt, "attempt_id")
                    != attempt_id
                    or _status(terminal_attempt) != "FAILED"
                    or _version(terminal_attempt) != _version(latest)
                    or _required_record_text(terminal_budget, "graph_id")
                    != admitted.graph_id
                    or int(terminal_budget.get("graph_revision") or 0)
                    != admitted.graph_revision
                    or terminal_budget.get("node_id") is not None
                    or terminal_budget.get("attempt_id") is not None
                    or _status(terminal_budget) != "RELEASED"
                    or _required_record_text(
                        terminal_reservation,
                        "reservation_id",
                    )
                    != admitted.reservation_id
                    or _status(terminal_reservation) != "COMMITTED"
                    or _text(terminal_reservation.get("receipt_ref"))
                    != (receipt_ref or "")
                    or _required_record_text(terminal_usage, "budget_id")
                    != _required_record_text(terminal_budget, "budget_id")
                    or _required_record_text(terminal_usage, "graph_id")
                    != admitted.graph_id
                    or int(terminal_usage.get("graph_revision") or 0)
                    != admitted.graph_revision
                    or _required_record_text(terminal_usage, "node_id")
                    != admitted.node_id
                    or _required_record_text(terminal_usage, "attempt_id")
                    != attempt_id
                    or terminal_usage.get("accepted") is not False
                    or _text(terminal_usage.get("reason_code"))
                    != "TASK_STORE_BUDGET_EXCEEDED"
                ):
                    raise GovernedRuntimeError(
                        GOVERNED_DURABLE_STATE_CONFLICT,
                        "checkpoint budget rejection replay identity drifted",
                    )
                return build_execution_receipt(
                    admitted,
                    attempt_id=attempt_id,
                    receipt_ref=receipt_ref,
                    payload_digest=_digest_from_receipt_ref(receipt_ref),
                    status="BUDGET_BLOCKED",
                    verification_status="NOT_RUN",
                    accepted=False,
                    replayed=True,
                    reason_code=GOVERNED_BUDGET_EXCEEDED,
                    evidence_refs=tuple(
                        dict.fromkeys(
                            reference
                            for reference in (
                                *_record_evidence_refs(node),
                                *_record_evidence_refs(terminal_node),
                                *_record_evidence_refs(latest),
                                *_record_evidence_refs(terminal_attempt),
                                terminal_error_ref,
                                terminal_usage_id,
                                _text(terminal_usage.get("outbox_id")),
                                receipt_ref,
                            )
                            if reference
                        )
                    ),
                    projection={
                        "budget": terminal_budget,
                        "budget_usage": terminal_usage,
                        "error_ref": terminal_error_ref,
                        "checkpoint_budget_rejection": {
                            "authority_kind": authority_kind,
                            "reservation_id": admitted.reservation_id,
                            "usage_id": terminal_usage_id,
                        },
                    },
                )
        atomic_graph_success = (
            admitted.completion_policy
            == TaskGraphCompletionPolicy.ATOMIC_GRAPH_SUCCESS_V1.value
        )
        if (
            atomic_graph_success
            and node_status == "ACCEPTED"
            and graph_status == "ACTIVE"
            and not allow_incomplete_atomic_graph_outcome
        ):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "atomic-success TaskNode is only an internal outcome until Graph and Budget commit",
            )
        accepted = node_status == "ACCEPTED" and graph_status in {"ACTIVE", "COMPLETED"}
        terminal_budget: dict[str, Any] | None = None
        if accepted and atomic_graph_success and graph_status == "COMPLETED":
            snapshot = await _maybe_await(
                self._store.read_scheduler_snapshot(
                    admitted.graph_id,
                    admitted.graph_revision,
                )
            )
            raw_budgets = snapshot.get("budgets") if isinstance(snapshot, Mapping) else None
            graph_budgets = [
                dict(item)
                for item in raw_budgets or ()
                if isinstance(item, Mapping)
                and _text(item.get("graph_id")) == admitted.graph_id
                and int(item.get("graph_revision") or 0) == admitted.graph_revision
                and item.get("node_id") is None
                and item.get("attempt_id") is None
            ]
            if len(graph_budgets) != 1 or _status(graph_budgets[0]) != "CONSUMED":
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    "completed atomic-success TaskGraph lacks its consumed graph Budget",
                )
            terminal_budget = graph_budgets[0]
        if accepted and (reservation_status != "COMMITTED" or not receipt_ref):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "accepted graph lacks its committed operation receipt",
            )
        if waiting_acceptance_review and (
            reservation_status != "COMMITTED" or not receipt_ref
        ):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "acceptance-review graph lacks its committed operation receipt",
            )
        projection: Mapping[str, Any] | None = None
        artifact_ref: str | None = None
        if accepted and _node_output_contract(node):
            try:
                raw_artifact = await _maybe_await(
                    self._store.get_node_artifact(
                        admitted.graph_id,
                        admitted.graph_revision,
                        admitted.node_id,
                    )
                )
            except Exception as exc:
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    "accepted TaskNode has no canonical durable output artifact",
                ) from exc
            if not isinstance(raw_artifact, Mapping):
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    "accepted TaskNode output artifact is invalid",
                )
            artifact = dict(raw_artifact)
            artifact_ref = _required_record_text(artifact, "artifact_ref")
            if (
                _required_record_text(artifact, "graph_id") != admitted.graph_id
                or int(artifact.get("graph_revision") or 0)
                != admitted.graph_revision
                or _required_record_text(artifact, "node_id") != admitted.node_id
                or _required_record_text(artifact, "receipt_ref") != receipt_ref
            ):
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    "accepted TaskNode output artifact lineage drifted",
                )
        if accepted and receipt_ref and self._projection_reader is not None:
            loaded = await _maybe_await(self._projection_reader.load(receipt_ref))
            if not isinstance(loaded, Mapping):
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    "accepted receipt has no durable public projection",
                )
            projection = dict(loaded)
        decision = await self.matching_parallel_decision(admitted, graph=graph)
        if decision is not None:
            selected = self.selected_parallel_decision_outcome(decision)
            selected_node_id = _required_record_text(selected, "node_id")
            if selected_node_id != admitted.node_id:
                decision = None
            elif _required_record_text(selected, "attempt_id") != attempt_id:
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    "parallel settlement replay selected Attempt drifted",
                )
        decision_ref = (
            _required_record_text(decision, "decision_ref")
            if decision is not None
            else None
        )
        if decision_ref is not None:
            projected = dict(projection or {})
            projected["parallel_settlement"] = {
                "graph_status": graph_status,
                "decision_ref": decision_ref,
            }
            projection = projected
        if terminal_budget is not None:
            projected = dict(projection or {})
            projected["budget"] = terminal_budget
            projection = projected
        return build_execution_receipt(
            admitted,
            attempt_id=attempt_id,
            receipt_ref=receipt_ref,
            payload_digest=_digest_from_receipt_ref(receipt_ref),
            status=node_status or graph_status,
            verification_status=(
                "PASS"
                if accepted or waiting_acceptance_review
                else "FAIL"
                if node_status == "VERIFICATION_FAILED"
                else "UNKNOWN"
            ),
            accepted=accepted,
            replayed=True,
            reason_code=None if accepted else f"GOVERNED_REPLAY_{node_status or graph_status}",
            evidence_refs=tuple(
                dict.fromkeys(
                    reference
                    for reference in (
                        *_record_evidence_refs(node),
                        _text(latest.get("error_ref")),
                        receipt_ref,
                        artifact_ref,
                        decision_ref,
                    )
                    if reference
                )
            ),
            projection=projection,
        )

    async def attempts_for_node(self, admitted: AdmittedExecution) -> list[dict[str, Any]]:
        snapshot = await _maybe_await(
            self._store.read_scheduler_snapshot(admitted.graph_id, admitted.graph_revision)
        )
        attempts = snapshot.get("attempts") if isinstance(snapshot, Mapping) else None
        if not isinstance(attempts, list):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "durable scheduler snapshot does not expose attempts",
            )
        rows = [
            dict(item)
            for item in attempts
            if isinstance(item, Mapping) and _text(item.get("node_id")) == admitted.node_id
        ]
        rows.sort(key=lambda item: (int(item.get("attempt_no") or 0), _text(item.get("attempt_id"))))
        return rows

    async def try_operation(self, reservation_id: str) -> dict[str, Any] | None:
        try:
            value = await _maybe_await(self._store.get_operation_reservation(reservation_id))
        except Exception as exc:
            reason = _exception_reason(exc, "")
            if reason in {"TASK_STORE_NOT_FOUND", "NOT_FOUND", "OPERATION_NOT_FOUND"}:
                return None
            raise
        return dict(value) if isinstance(value, Mapping) else None


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


__all__ = ["NodeExecutionProjection"]
