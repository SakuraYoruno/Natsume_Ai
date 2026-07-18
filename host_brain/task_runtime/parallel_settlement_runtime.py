"""0090 parallel Graph/Budget settlement orchestration."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Mapping
from typing import Any

from host_brain.contracts.agent_runtime.budget_policy import BudgetReservationStatus
from host_brain.contracts.agent_runtime.parallel_settlement import (
    ParallelSettlementContractError,
    normalize_parallel_settlement_decision,
)
from host_brain.persistence.task_store_types import TASK_STORE_BUDGET_EXCEEDED

from .admission import AdmittedExecution
from .budget_runtime import BudgetRuntimeContext, DurableBudgetRuntime
from .coordinator_records import (
    _ParallelNodeOutcome,
    _evidence_refs,
    _exception_reason,
    _mapping,
    _record_evidence_refs,
    _required_record_text,
    _status,
    _text,
    _version,
)
from .errors import (
    GOVERNED_BUDGET_EXCEEDED,
    GOVERNED_DURABLE_STATE_CONFLICT,
    GOVERNED_EXECUTION_IN_PROGRESS,
    GOVERNED_OPERATION_RECOVERY_REQUIRED,
    GOVERNED_REVIEW_REQUIRED,
    GovernedRuntimeError,
)
from .parallel_settlement_projection import ParallelSettlementProjection
from .ports import TaskStorePort
from .runtime_outbox import RuntimeOutbox


_logger = logging.getLogger("Natsume.task_runtime.coordinator")
_TASK_STORE_CAS_CONFLICT = "TASK_STORE_CAS_CONFLICT"
_TASK_STORE_OWNERSHIP_CONFLICT = "TASK_STORE_OWNERSHIP_CONFLICT"


class ParallelSettlementRuntime:
    """Resolve a parallel batch and own its atomic 0090 writer."""

    __slots__ = ("_budgets", "_projection", "_runtime_outbox", "_store")

    def __init__(
        self,
        *,
        task_store: TaskStorePort,
        budget_runtime: DurableBudgetRuntime,
        runtime_outbox: RuntimeOutbox,
    ) -> None:
        self._store = task_store
        self._budgets = budget_runtime
        self._runtime_outbox = runtime_outbox
        self._projection = ParallelSettlementProjection(task_store=task_store)

    async def assert_parallel_reentry_idle(
        self,
        *,
        admitted: AdmittedExecution,
        durable_nodes: Mapping[str, Mapping[str, Any]],
    ) -> None:
        await self._projection.assert_parallel_reentry_idle(
            admitted=admitted,
            durable_nodes=durable_nodes,
        )

    async def _parallel_settlement_conflict_is_in_progress(
        self,
        *,
        admitted: AdmittedExecution,
    ) -> bool:
        """Distinguish a settlement-window claim from durable state drift."""

        try:
            durable_nodes = {
                binding.node_id: _mapping(
                    await _maybe_await(
                        self._store.get_node(
                            admitted.graph_id,
                            admitted.graph_revision,
                            binding.node_id,
                        )
                    ),
                    field_name=f"parallel settlement node {binding.node_id}",
                )
                for binding in admitted.nodes
            }
            await self._projection.assert_parallel_reentry_idle(
                admitted=admitted,
                durable_nodes=durable_nodes,
            )
        except GovernedRuntimeError as exc:
            return exc.reason_code == GOVERNED_EXECUTION_IN_PROGRESS
        except Exception:
            return False
        return False

    async def resolve_parallel_outcomes(
        self,
        *,
        admitted: AdmittedExecution,
        outcomes: tuple[_ParallelNodeOutcome, ...],
        budget: BudgetRuntimeContext,
    ) -> tuple[BudgetRuntimeContext, dict[str, Any] | None]:
        if not outcomes:
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "parallel batch produced no node outcomes",
            )
        await self._projection.validate_parallel_outcome_snapshot(
            admitted=admitted,
            outcomes=outcomes,
        )
        budget = await self._budgets.refresh(budget)

        def first_status(*statuses: str) -> _ParallelNodeOutcome | None:
            expected = frozenset(statuses)
            return next((item for item in outcomes if item.status in expected), None)

        recovery = first_status("RECOVERY_REQUIRED")
        if recovery is not None:
            return budget, self._parallel_event_with_budget(
                recovery.event,
                budget=budget,
                graph_status="ACTIVE",
                decision_ref=None,
            )

        budget_blocked = first_status("BUDGET_BLOCKED")
        if budget.status == "EXCEEDED":
            budget_blocked = await self._projection.durable_budget_blocked_outcome(
                admitted=admitted,
                outcomes=outcomes,
                budget=budget,
            )
        if budget.status == "EXCEEDED" or budget_blocked is not None:
            if budget_blocked is None:
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    "parallel Budget settlement lacks its rejected durable usage",
                )
            selected = budget_blocked
            settlement_outcomes = tuple(
                selected
                if (
                    outcome.node_id == selected.node_id
                    and outcome.attempt_id == selected.attempt_id
                )
                else outcome
                for outcome in outcomes
            )
            budget, graph_status, decision_ref = await self._settle_parallel_terminal(
                admitted=admitted.for_node(selected.node_id),
                outcomes=settlement_outcomes,
                budget=budget,
                graph_target_status="BLOCKED",
                release_budget=True,
                decision_kind="BUDGET",
                selected_node_id=selected.node_id,
            )
            return budget, self._parallel_event_with_budget(
                selected.event,
                budget=budget,
                graph_status=graph_status,
                decision_ref=decision_ref,
            )

        failed = first_status("FAILED", "VERIFICATION_FAILED", "CANCELLED")
        if failed is not None:
            graph_target = (
                "BLOCKED"
                if failed.reason_code == GOVERNED_OPERATION_RECOVERY_REQUIRED
                else "FAILED"
            )
            budget, graph_status, decision_ref = await self._settle_parallel_terminal(
                admitted=admitted.for_node(failed.node_id),
                outcomes=outcomes,
                budget=budget,
                graph_target_status=graph_target,
                release_budget=True,
                decision_kind="FAILURE",
                selected_node_id=failed.node_id,
            )
            return budget, self._parallel_event_with_budget(
                failed.event,
                budget=budget,
                graph_status=graph_status,
                decision_ref=decision_ref,
            )

        review = first_status("WAITING_ACCEPTANCE_REVIEW", "WAITING_APPROVAL")
        if review is not None:
            budget, graph_status, decision_ref = await self._settle_parallel_terminal(
                admitted=admitted.for_node(review.node_id),
                outcomes=outcomes,
                budget=budget,
                graph_target_status="BLOCKED",
                release_budget=False,
                decision_kind="REVIEW",
                selected_node_id=review.node_id,
            )
            return budget, self._parallel_event_with_budget(
                review.event,
                budget=budget,
                graph_status=graph_status,
                decision_ref=decision_ref,
            )

        retry = first_status("RETRY_PENDING")
        if retry is not None:
            return budget, self._parallel_event_with_budget(
                retry.event,
                budget=budget,
                graph_status="ACTIVE",
                decision_ref=None,
            )

        if all(item.accepted and item.status == "ACCEPTED" for item in outcomes):
            return budget, None
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "parallel batch returned an unsupported node-outcome combination",
        )

    async def _settle_parallel_terminal(
        self,
        *,
        admitted: AdmittedExecution,
        outcomes: tuple[_ParallelNodeOutcome, ...],
        budget: BudgetRuntimeContext,
        graph_target_status: str,
        release_budget: bool,
        decision_kind: str,
        selected_node_id: str,
    ) -> tuple[BudgetRuntimeContext, str, str]:
        graph = _mapping(
            await _maybe_await(
                self._store.get_graph(admitted.graph_id, admitted.graph_revision)
            ),
            field_name="parallel settlement graph",
        )
        durable_nodes: dict[str, dict[str, Any]] = {}
        for binding in admitted.nodes:
            durable_nodes[binding.node_id] = _mapping(
                await _maybe_await(
                    self._store.get_node(
                        admitted.graph_id,
                        admitted.graph_revision,
                        binding.node_id,
                    )
                ),
                field_name=f"parallel settlement node {binding.node_id}",
            )
        budget = await self._budgets.refresh(budget)
        settlement_snapshot = _mapping(
            await _maybe_await(
                self._store.read_scheduler_snapshot(
                    admitted.graph_id,
                    admitted.graph_revision,
                )
            ),
            field_name="parallel settlement scheduler snapshot",
        )
        raw_attempts = settlement_snapshot.get("attempts")
        if not isinstance(raw_attempts, list):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "parallel settlement snapshot has no durable Attempt journal",
            )
        latest_attempt_by_node: dict[str, dict[str, Any]] = {}
        for raw_attempt in raw_attempts:
            if not isinstance(raw_attempt, Mapping):
                continue
            attempt = dict(raw_attempt)
            node_id = _text(attempt.get("node_id"))
            if not node_id:
                continue
            current = latest_attempt_by_node.get(node_id)
            attempt_key = (
                int(attempt.get("attempt_no") or 0),
                _text(attempt.get("attempt_id")),
            )
            current_key = (
                int((current or {}).get("attempt_no") or 0),
                _text((current or {}).get("attempt_id")),
            )
            if current is None or attempt_key > current_key:
                latest_attempt_by_node[node_id] = attempt
        expected_node_states = {
            node_id: {
                "status": _status(node),
                "status_version": _version(node),
            }
            for node_id, node in sorted(durable_nodes.items())
        }
        decision_outcomes: list[dict[str, Any]] = []
        rejected_budget_usage_refs: dict[tuple[str, str], str] = {}
        all_budget_blocked_outcomes = tuple(
            outcome for outcome in outcomes if outcome.status == "BUDGET_BLOCKED"
        )
        budget_blocked_outcomes = tuple(
            outcome
            for outcome in all_budget_blocked_outcomes
            if outcome.node_id == selected_node_id
        )
        if all_budget_blocked_outcomes:
            if decision_kind != "BUDGET" or len(budget_blocked_outcomes) != 1:
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    "parallel Budget decision must select one blocked outcome",
                )
            raw_usages = settlement_snapshot.get("budget_usage")
            if not isinstance(raw_usages, list):
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    "parallel Budget decision has no durable usage ledger",
                )
            for outcome in budget_blocked_outcomes:
                candidates: list[Mapping[str, Any]] = []
                for raw_usage in raw_usages:
                    if not isinstance(raw_usage, Mapping):
                        continue
                    exceeded_dimensions = raw_usage.get("exceeded_dimensions")
                    if (
                        _text(raw_usage.get("budget_id")) != budget.budget_id
                        or _text(raw_usage.get("node_id")) != outcome.node_id
                        or _text(raw_usage.get("attempt_id")) != outcome.attempt_id
                        or raw_usage.get("accepted") is not False
                        or _text(raw_usage.get("reason_code"))
                        != TASK_STORE_BUDGET_EXCEEDED
                        or not isinstance(exceeded_dimensions, list)
                        or not exceeded_dimensions
                    ):
                        continue
                    candidates.append(raw_usage)
                if len(candidates) != 1:
                    raise GovernedRuntimeError(
                        GOVERNED_DURABLE_STATE_CONFLICT,
                        "parallel Budget decision requires exactly one canonical "
                        "winning usage lineage",
                    )
                selected_usage = candidates[0]
                rejected_budget_usage_refs[(outcome.node_id, outcome.attempt_id)] = (
                    _required_record_text(selected_usage, "usage_id")
                )
        decision_terminal_statuses = frozenset(
            {
                "ACCEPTED",
                "FAILED",
                "RETRY_PENDING",
                "VERIFICATION_FAILED",
                "WAITING_APPROVAL",
                "WAITING_ACCEPTANCE_REVIEW",
                "CANCELLED",
            }
        )
        decision_outcome_node_ids: set[str] = set()
        for binding in admitted.nodes:
            durable_status = _status(durable_nodes[binding.node_id])
            if durable_status not in decision_terminal_statuses:
                continue
            durable_attempt = latest_attempt_by_node.get(binding.node_id)
            if durable_status != "WAITING_APPROVAL" and durable_attempt is None:
                continue
            attempt_id = (
                None
                if durable_status == "WAITING_APPROVAL"
                else _required_record_text(durable_attempt or {}, "attempt_id")
            )
            node_admission = admitted.for_node(binding.node_id)
            reservation = await self._try_operation(node_admission.reservation_id)
            durable_evidence = tuple(
                dict.fromkeys(
                    reference
                    for reference in (
                        *_record_evidence_refs(durable_nodes[binding.node_id]),
                        _text((durable_attempt or {}).get("error_ref")),
                        _text((reservation or {}).get("receipt_ref")),
                        rejected_budget_usage_refs.get(
                            (binding.node_id, attempt_id or "")
                        ),
                    )
                    if reference
                )
            )
            decision_status = (
                "BUDGET_BLOCKED"
                if (
                    decision_kind == "BUDGET"
                    and binding.node_id == selected_node_id
                )
                else durable_status
            )
            decision_outcome_node_ids.add(binding.node_id)
            decision_outcomes.append(
                {
                    "node_id": binding.node_id,
                    "attempt_id": attempt_id,
                    "status": decision_status,
                    "accepted": decision_status == "ACCEPTED",
                    "reason_code": _canonical_parallel_outcome_reason_code(
                        decision_status
                    ),
                    "evidence_refs": list(durable_evidence),
                }
            )
        required_outcome_node_ids = {outcome.node_id for outcome in outcomes}
        if not required_outcome_node_ids.issubset(decision_outcome_node_ids):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "parallel decision cannot reconstruct every durable batch outcome",
            )
        graph_version = _version(graph)
        budget_version = budget.version
        graph_outbox = self._runtime_outbox.build(
            command_id=admitted.command_id,
            aggregate_type="TASK_GRAPH",
            aggregate_id=admitted.graph_id,
            graph_revision=admitted.graph_revision,
            aggregate_version=graph_version + 1,
            event_type=f"TASK_GRAPH_{graph_target_status}",
        )
        budget_outbox = (
            self._runtime_outbox.build(
                command_id=admitted.command_id,
                aggregate_type="BUDGET_RESERVATION",
                aggregate_id=budget.budget_id,
                aggregate_version=budget_version + 1,
                event_type="BUDGET_RESERVATION_RELEASED",
            )
            if release_budget
            else None
        )
        decision_payload = {
            "schema_version": "Natsume.task_runtime.parallel_settlement.v2",
            "decision_kind": decision_kind,
            "selected_node_id": selected_node_id,
            "graph_id": admitted.graph_id,
            "graph_revision": admitted.graph_revision,
            "graph_status_before": _status(graph),
            "graph_status_version_before": graph_version,
            "graph_target_status": graph_target_status,
            "budget_id": budget.budget_id,
            "budget_status_before": budget.status,
            "budget_status_version_before": budget_version,
            "budget_target_status": (
                BudgetReservationStatus.RELEASED.value if release_budget else None
            ),
            "node_states": expected_node_states,
            "outcomes": decision_outcomes,
            "graph_outbox_id": graph_outbox.outbox_id,
            "budget_outbox_id": (
                budget_outbox.outbox_id if budget_outbox is not None else None
            ),
        }
        try:
            decision = normalize_parallel_settlement_decision(decision_payload)
        except ParallelSettlementContractError as exc:
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                f"parallel settlement decision is invalid: {exc}",
            ) from exc
        decision_ref = decision.decision_ref
        graph_outbox = self._runtime_outbox.build(
            command_id=admitted.command_id,
            aggregate_type="TASK_GRAPH",
            aggregate_id=admitted.graph_id,
            graph_revision=admitted.graph_revision,
            aggregate_version=graph_version + 1,
            event_type=f"TASK_GRAPH_{graph_target_status}",
            evidence_refs=(decision_ref,),
        )
        budget_outbox = (
            self._runtime_outbox.build(
                command_id=admitted.command_id,
                aggregate_type="BUDGET_RESERVATION",
                aggregate_id=budget.budget_id,
                aggregate_version=budget_version + 1,
                event_type="BUDGET_RESERVATION_RELEASED",
                evidence_refs=(decision_ref,),
            )
            if release_budget
            else None
        )
        arguments = {
            "budget_id": budget.budget_id,
            "expected_graph_version": graph_version,
            "expected_budget_version": budget_version,
            "expected_node_states": expected_node_states,
            "graph_target_status": graph_target_status,
            "budget_target_status": (
                BudgetReservationStatus.RELEASED.value if release_budget else None
            ),
            "decision_ref": decision_ref,
            "decision_payload": decision.payload,
            "graph_outbox": graph_outbox,
            "budget_outbox": budget_outbox,
        }
        try:
            settled_raw = await _maybe_await(
                self._store.settle_parallel_graph_budget(
                    admitted.graph_id,
                    admitted.graph_revision,
                    **arguments,
                )
            )
        except Exception as first_exc:
            first_reason = _exception_reason(first_exc, "")
            if first_reason == _TASK_STORE_OWNERSHIP_CONFLICT or (
                first_reason == _TASK_STORE_CAS_CONFLICT
                and await self._parallel_settlement_conflict_is_in_progress(
                    admitted=admitted
                )
            ):
                raise GovernedRuntimeError(
                    GOVERNED_EXECUTION_IN_PROGRESS,
                    "parallel TaskGraph gained an in-progress child before settlement",
                ) from first_exc
            try:
                settled_raw = await _maybe_await(
                    self._store.settle_parallel_graph_budget(
                        admitted.graph_id,
                        admitted.graph_revision,
                        **arguments,
                    )
                )
            except Exception as replay_exc:
                replay_reason = _exception_reason(replay_exc, "")
                if replay_reason == _TASK_STORE_OWNERSHIP_CONFLICT or (
                    replay_reason == _TASK_STORE_CAS_CONFLICT
                    and await self._parallel_settlement_conflict_is_in_progress(
                        admitted=admitted
                    )
                ):
                    raise GovernedRuntimeError(
                        GOVERNED_EXECUTION_IN_PROGRESS,
                        "parallel TaskGraph gained an in-progress child before settlement",
                    ) from replay_exc
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    "parallel Graph/Budget settlement could not be committed or exactly replayed",
                ) from replay_exc
            _logger.warning(
                "Replayed committed parallel Graph/Budget settlement",
                extra={
                    "graph_id": admitted.graph_id,
                    "graph_revision": admitted.graph_revision,
                    "decision_ref": decision_ref,
                    "first_exception_type": type(first_exc).__name__,
                },
            )
        settled = _mapping(settled_raw, field_name="parallel Graph/Budget settlement")
        settled_graph = _mapping(
            settled.get("graph"),
            field_name="parallel Graph/Budget settlement graph",
        )
        settled_budget = _mapping(
            settled.get("budget"),
            field_name="parallel Graph/Budget settlement budget",
        )
        settled_decision = _mapping(
            settled.get("decision"),
            field_name="parallel Graph/Budget settlement decision",
        )
        expected_budget_status = (
            BudgetReservationStatus.RELEASED.value if release_budget else budget.status
        )
        if (
            _status(settled_graph) != graph_target_status
            or _status(settled_budget) != expected_budget_status
            or _text(settled_decision.get("decision_ref")) != decision_ref
            or settled_decision.get("payload") != decision.payload
        ):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "parallel settlement returned an invalid Graph/Budget terminal state",
            )
        await self._runtime_outbox.publish_pending()
        return budget.refreshed(settled_budget), _status(settled_graph), decision_ref

    @staticmethod
    def _parallel_event_with_budget(
        event: Mapping[str, Any],
        *,
        budget: BudgetRuntimeContext,
        graph_status: str,
        decision_ref: str | None,
    ) -> dict[str, Any]:
        projected = dict(event)
        receipt_raw = projected.get("execution_receipt")
        receipt = dict(receipt_raw) if isinstance(receipt_raw, Mapping) else {}
        projection_raw = receipt.get("projection")
        projection = dict(projection_raw) if isinstance(projection_raw, Mapping) else {}
        projection["budget"] = budget.execution_payload()
        projection["parallel_settlement"] = {
            "graph_status": graph_status,
            "decision_ref": decision_ref,
        }
        if decision_ref:
            receipt["evidence_refs"] = list(
                dict.fromkeys((*_evidence_refs(receipt), decision_ref))
            )
        receipt["projection"] = projection
        projected["execution_receipt"] = receipt
        return projected

    async def _try_operation(self, reservation_id: str) -> dict[str, Any] | None:
        try:
            value = await _maybe_await(self._store.get_operation_reservation(reservation_id))
        except Exception as exc:
            reason = _exception_reason(exc, "")
            if reason in {"TASK_STORE_NOT_FOUND", "NOT_FOUND", "OPERATION_NOT_FOUND"}:
                return None
            raise
        return dict(value) if isinstance(value, Mapping) else None


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _canonical_parallel_outcome_reason_code(status: str) -> str | None:
    normalized = _text(status).upper()
    reasons = {
        "ACCEPTED": None,
        "BUDGET_BLOCKED": GOVERNED_BUDGET_EXCEEDED,
        "FAILED": "GOVERNED_PARALLEL_NODE_FAILED",
        "VERIFICATION_FAILED": "GOVERNED_PARALLEL_VERIFICATION_FAILED",
        "WAITING_APPROVAL": GOVERNED_REVIEW_REQUIRED,
        "WAITING_ACCEPTANCE_REVIEW": GOVERNED_REVIEW_REQUIRED,
        "RETRY_PENDING": GOVERNED_EXECUTION_IN_PROGRESS,
        "CANCELLED": "GOVERNED_PARALLEL_NODE_CANCELLED",
    }
    if normalized not in reasons:
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            f"parallel decision outcome status is unsupported: {normalized}",
        )
    return reasons[normalized]


# Keep the composition import surface limited to the stateful runtime.
__all__ = ["ParallelSettlementRuntime"]
