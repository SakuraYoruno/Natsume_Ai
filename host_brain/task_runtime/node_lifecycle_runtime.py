"""Durable node lifecycle and terminal-failure orchestration."""

from __future__ import annotations

import hashlib
import inspect
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from host_brain.contracts.agent_runtime.budget_policy import BudgetReservationStatus

from .admission import AdmittedExecution
from .budget_runtime import BudgetCharge, BudgetRuntimeContext, DurableBudgetRuntime
from .coordinator_receipts import GovernedExecutionReceipt, build_execution_receipt
from .coordinator_records import (
    _mapping,
    _required_record_text,
    _status,
    _version,
)
from .errors import (
    GOVERNED_BUDGET_EXCEEDED,
    GOVERNED_DURABLE_STATE_CONFLICT,
    GovernedRuntimeError,
)
from .ports import TaskStorePort
from .runtime_outbox import RuntimeOutbox


_logger = logging.getLogger("Natsume.task_runtime.coordinator")


class DurableNodeLifecycleRuntime:
    """Own durable node transitions, budget rejection, and failure closure."""

    __slots__ = ("_budgets", "_owner_id", "_runtime_outbox", "_store")

    def __init__(
        self,
        *,
        task_store: TaskStorePort,
        budget_runtime: DurableBudgetRuntime,
        runtime_outbox: RuntimeOutbox,
        owner_id: str,
    ) -> None:
        self._store = task_store
        self._budgets = budget_runtime
        self._runtime_outbox = runtime_outbox
        self._owner_id = owner_id

    async def transition_graph(
        self,
        admitted: AdmittedExecution,
        graph: Mapping[str, Any],
        target_status: str,
    ) -> dict[str, Any]:
        version = _version(graph)
        return dict(
            await _maybe_await(
                self._store.transition_graph(
                    admitted.graph_id,
                    admitted.graph_revision,
                    expected_version=version,
                    target_status=target_status,
                    outbox=self._runtime_outbox.build(
                        command_id=admitted.command_id,
                        aggregate_type="TASK_GRAPH",
                        aggregate_id=admitted.graph_id,
                        graph_revision=admitted.graph_revision,
                        aggregate_version=version + 1,
                        event_type=f"TASK_GRAPH_{target_status}",
                    ),
                )
            )
        )

    async def transition_node(
        self,
        admitted: AdmittedExecution,
        node: Mapping[str, Any],
        target_status: str,
        *,
        evidence_refs: Sequence[str] = (),
        verification_result: Mapping[str, Any] | None = None,
        acceptance_judgement: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        version = _version(node)
        return dict(
            await _maybe_await(
                self._store.transition_node(
                    admitted.graph_id,
                    admitted.graph_revision,
                    admitted.node_id,
                    expected_version=version,
                    target_status=target_status,
                    outbox=self._runtime_outbox.build(
                        command_id=admitted.command_id,
                        aggregate_type="TASK_NODE",
                        aggregate_id=(
                            f"{admitted.graph_id}@{admitted.graph_revision}/{admitted.node_id}"
                        ),
                        aggregate_version=version + 1,
                        event_type=f"TASK_NODE_{target_status}",
                        evidence_refs=evidence_refs,
                    ),
                    verification_result=verification_result,
                    acceptance_judgement=acceptance_judgement,
                )
            )
        )

    async def transition_graph_with_acceptance_effects(
        self,
        admitted: AdmittedExecution,
        graph: Mapping[str, Any],
        *,
        effect_intents: Sequence[Any],
    ) -> dict[str, Any]:
        version = _version(graph)
        result = await _maybe_await(
            self._store.transition_graph_and_stage_acceptance_effects(
                admitted.graph_id,
                admitted.graph_revision,
                expected_version=version,
                outbox=self._runtime_outbox.build(
                    command_id=admitted.command_id,
                    aggregate_type="TASK_GRAPH",
                    aggregate_id=admitted.graph_id,
                    graph_revision=admitted.graph_revision,
                    aggregate_version=version + 1,
                    event_type="TASK_GRAPH_COMPLETED",
                ),
                effect_intents=effect_intents,
            )
        )
        projected = _mapping(
            result,
            field_name="atomic graph acceptance effect result",
        )
        completed = projected.get("graph")
        if not isinstance(completed, Mapping):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "atomic graph acceptance did not return its completed graph",
            )
        return dict(completed)

    async def cancel_waiting_review_node(
        self,
        admitted: AdmittedExecution,
        node: Mapping[str, Any],
        *,
        evidence_refs: Sequence[str] = (),
    ) -> dict[str, Any]:
        version = _version(node)
        return dict(
            await _maybe_await(
                self._store.request_cancel_node(
                    admitted.graph_id,
                    admitted.graph_revision,
                    admitted.node_id,
                    expected_version=version,
                    outbox=self._runtime_outbox.build(
                        command_id=admitted.command_id,
                        aggregate_type="TASK_NODE",
                        aggregate_id=(
                            f"{admitted.graph_id}@{admitted.graph_revision}/{admitted.node_id}"
                        ),
                        aggregate_version=version + 1,
                        event_type="TASK_NODE_CANCELLED",
                        evidence_refs=evidence_refs,
                    ),
                )
            )
        )

    async def transition_attempt(
        self,
        admitted: AdmittedExecution,
        attempt: Mapping[str, Any],
        *,
        lease_id: str,
        fencing_token: int,
        target_status: str,
        evidence_refs: Sequence[str] = (),
        error_ref: str | None = None,
    ) -> dict[str, Any]:
        del lease_id  # binding is checked by the durable store through the active lease
        attempt_id = _required_record_text(attempt, "attempt_id")
        version = _version(attempt)
        return dict(
            await _maybe_await(
                self._store.transition_attempt(
                    attempt_id,
                    owner_id=self._owner_id,
                    fencing_token=fencing_token,
                    expected_version=version,
                    target_status=target_status,
                    error_ref=error_ref,
                    outbox=self._runtime_outbox.build(
                        command_id=admitted.command_id,
                        aggregate_type="TASK_ATTEMPT",
                        aggregate_id=attempt_id,
                        aggregate_version=version + 1,
                        event_type=f"TASK_ATTEMPT_{target_status}",
                        attempt_id=attempt_id,
                        evidence_refs=evidence_refs,
                    ),
                )
            )
        )

    async def finalize_node_budget(
        self,
        budget: BudgetRuntimeContext,
        *,
        target_status: BudgetReservationStatus,
        defer_shared_terminal: bool,
    ) -> BudgetRuntimeContext:
        if defer_shared_terminal:
            return await self._budgets.refresh(budget)
        return await self._budgets.finalize(
            budget,
            target_status=target_status,
        )

    async def reject_budget_charge(
        self,
        admitted: AdmittedExecution,
        charge: BudgetCharge,
        *,
        attempt_id: str,
        fencing_token: int,
        pre_reservation_graph: Mapping[str, Any] | None = None,
        pre_reservation_node: Mapping[str, Any] | None = None,
        pre_reservation_attempt: Mapping[str, Any] | None = None,
        defer_shared_terminal: bool = False,
    ) -> GovernedExecutionReceipt:
        pre_reservation_state = (
            pre_reservation_graph,
            pre_reservation_node,
            pre_reservation_attempt,
        )
        if all(isinstance(item, Mapping) for item in pre_reservation_state):
            failure_ref = await self.mark_pre_reservation_failure(
                admitted,
                graph=pre_reservation_graph,
                node=pre_reservation_node,
                attempt=pre_reservation_attempt,
                attempt_id=attempt_id,
                fencing_token=fencing_token,
                reason_code=GOVERNED_BUDGET_EXCEEDED,
                graph_target=(None if defer_shared_terminal else "BLOCKED"),
                evidence_refs=(charge.evidence_ref, charge.usage_id),
                defer_graph_terminal=defer_shared_terminal,
            )
        else:
            await self.mark_worker_failure(
                admitted,
                attempt_id=attempt_id,
                fencing_token=fencing_token,
                reason_code=GOVERNED_BUDGET_EXCEEDED,
                graph_target=(None if defer_shared_terminal else "BLOCKED"),
                evidence_refs=(charge.evidence_ref, charge.usage_id),
            )
            failure_ref = None
        budget = await self.finalize_node_budget(
            charge.context,
            target_status=BudgetReservationStatus.RELEASED,
            defer_shared_terminal=defer_shared_terminal,
        )
        await self._runtime_outbox.publish_pending()
        receipt_evidence = tuple(
            item for item in (charge.evidence_ref, charge.usage_id, failure_ref) if item
        )
        return build_execution_receipt(
            admitted,
            attempt_id=attempt_id,
            status="BUDGET_BLOCKED",
            verification_status="NOT_RUN",
            accepted=False,
            reason_code=GOVERNED_BUDGET_EXCEEDED,
            evidence_refs=receipt_evidence,
            projection={
                "budget": budget.execution_payload(),
                "exceeded_dimensions": list(charge.exceeded_dimensions),
                "requested_action": budget.policy.on_exceeded.value,
                "enforced_action": "BLOCK",
            },
        )

    async def mark_worker_failure(
        self,
        admitted: AdmittedExecution,
        *,
        attempt_id: str,
        fencing_token: int,
        reason_code: str,
        graph_target: str | None = "FAILED",
        evidence_refs: Sequence[str] = (),
    ) -> None:
        error_ref = "taskerror://" + hashlib.sha256(
            f"{attempt_id}\n{reason_code}".encode("utf-8")
        ).hexdigest()
        try:
            attempt = await _maybe_await(self._store.get_attempt(attempt_id))
            if _status(attempt) == "RUNNING":
                await self.transition_attempt(
                    admitted,
                    attempt,
                    lease_id="",
                    fencing_token=fencing_token,
                    target_status="FAILED",
                    error_ref=error_ref,
                    evidence_refs=(error_ref, *evidence_refs),
                )
            node = await _maybe_await(
                self._store.get_node(admitted.graph_id, admitted.graph_revision, admitted.node_id)
            )
            if _status(node) == "RUNNING":
                await self.transition_node(
                    admitted, node, "FAILED", evidence_refs=(error_ref, *evidence_refs)
                )
            if graph_target is not None:
                graph = await _maybe_await(
                    self._store.get_graph(admitted.graph_id, admitted.graph_revision)
                )
                if _status(graph) == "ACTIVE":
                    await self.transition_graph(admitted, graph, graph_target)
        finally:
            await self._runtime_outbox.publish_pending(best_effort=True)

    async def mark_pre_reservation_failure(
        self,
        admitted: AdmittedExecution,
        *,
        graph: Mapping[str, Any],
        node: Mapping[str, Any],
        attempt: Mapping[str, Any],
        attempt_id: str,
        fencing_token: int,
        reason_code: str,
        graph_target: str | None = "FAILED",
        evidence_refs: Sequence[str] = (),
        defer_graph_terminal: bool = False,
    ) -> str:
        """Close the execution authority atomically before reservation.

        The exact same mutation is replayed once because the first transaction
        may have committed before its response was lost.  There is deliberately
        no sequential fallback: a partial terminal state would be less safe
        than surfacing a durable conflict.
        """

        error_ref = "taskerror://" + hashlib.sha256(
            f"{attempt_id}\n{reason_code}".encode("utf-8")
        ).hexdigest()
        arguments = {
            "owner_id": self._owner_id,
            "fencing_token": fencing_token,
            "expected_attempt_version": _version(attempt),
            "expected_node_version": _version(node),
            "expected_graph_version": _version(graph),
            "error_ref": error_ref,
            "attempt_outbox": self._runtime_outbox.build(
                command_id=admitted.command_id,
                aggregate_type="TASK_ATTEMPT",
                aggregate_id=attempt_id,
                aggregate_version=_version(attempt) + 1,
                event_type="TASK_ATTEMPT_FAILED",
                attempt_id=attempt_id,
                evidence_refs=(error_ref, *evidence_refs),
            ),
        }
        if defer_graph_terminal:
            graph_target = None
            fail_attempt = self._store.fail_running_attempt_node_bundle
        else:
            if graph_target is None:
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    "inline failure requires a Graph terminal target",
                )
            arguments["graph_target_status"] = graph_target
            fail_attempt = self._store.fail_running_attempt_bundle
        try:
            result_raw = await _maybe_await(fail_attempt(attempt_id, **arguments))
        except Exception as first_exc:
            try:
                result_raw = await _maybe_await(fail_attempt(attempt_id, **arguments))
            except Exception as replay_exc:
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    "pre-reservation terminal failure could not be committed or exactly replayed",
                ) from replay_exc
            _logger.warning(
                "Replayed committed pre-reservation terminal failure",
                extra={
                    "attempt_id": attempt_id,
                    "first_exception_type": type(first_exc).__name__,
                },
            )
        result = _mapping(result_raw, field_name="atomic terminal failure")
        failed_attempt = _mapping(
            result.get("attempt"),
            field_name="atomic terminal failure attempt",
        )
        failed_node = _mapping(
            result.get("node"),
            field_name="atomic terminal failure node",
        )
        failed_graph = _mapping(
            result.get("graph"),
            field_name="atomic terminal failure graph",
        )
        released_lease = _mapping(
            result.get("lease"),
            field_name="atomic terminal failure lease",
        )
        if (
            _status(failed_attempt) != "FAILED"
            or _status(failed_node) != "FAILED"
            or _status(failed_graph)
            != ("ACTIVE" if defer_graph_terminal else graph_target)
            or _status(released_lease) != "RELEASED"
        ):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "atomic terminal failure returned an incomplete durable state",
            )
        await self._runtime_outbox.publish_pending()
        return error_ref


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


__all__ = ["DurableNodeLifecycleRuntime"]
