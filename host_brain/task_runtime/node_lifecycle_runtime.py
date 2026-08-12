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
from .node_acceptance_lifecycle import (
    commit_acceptance_review,
    commit_accepted_node_output,
)
from .ports import TaskStorePort
from .runtime_outbox import RuntimeOutbox


_logger = logging.getLogger("Natsume.task_runtime.coordinator")


# 生命周期运行时是状态转换的编排层；真正的并发裁决和原子写入仍由 TaskStore 持有。
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
        # 以期望版本写入状态与 outbox：竞争者先提交时本次必须失败，不能覆盖其图修订。
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
        # 节点转移携带证据引用，使异步消费者能追溯状态改变而不依赖进程内日志。
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

    async def commit_node_verification(
        self,
        admitted: AdmittedExecution,
        node: Mapping[str, Any],
        *,
        attempt_id: str,
        reservation_id: str,
        target_status: str,
        evidence_refs: Sequence[str],
        verification_result: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Persist a verification decision and Node state in one UoW."""

        version = _version(node)
        node_outbox = self._runtime_outbox.build(
            command_id=admitted.command_id,
            aggregate_type="TASK_NODE",
            aggregate_id=(
                f"{admitted.graph_id}@{admitted.graph_revision}/"
                f"{admitted.node_id}"
            ),
            aggregate_version=version + 1,
            event_type=f"TASK_NODE_{target_status}",
            attempt_id=attempt_id,
            evidence_refs=evidence_refs,
        )
        arguments = {
            "attempt_id": attempt_id,
            "reservation_id": reservation_id,
            "expected_node_version": version,
            "target_status": target_status,
            "verification_result": verification_result,
            "node_outbox": node_outbox,
        }
        # 验证决定、节点状态和事件必须同一事务封存；响应丢失时只重放完全相同的写入。
        try:
            raw = await _maybe_await(
                self._store.commit_node_verification(
                    admitted.graph_id,
                    admitted.graph_revision,
                    admitted.node_id,
                    **arguments,
                )
            )
        except Exception:
            # The first transaction may have committed before its response was
            # lost.  The Store owns exact replay validation.
            raw = await _maybe_await(
                self._store.commit_node_verification(
                    admitted.graph_id,
                    admitted.graph_revision,
                    admitted.node_id,
                    **arguments,
                )
            )
        result = _mapping(raw, field_name="durable node verification")
        committed_node = result.get("node")
        decision = result.get("decision")
        if (
            not isinstance(committed_node, Mapping)
            or not isinstance(decision, Mapping)
            or _status(committed_node) != target_status
            or _required_record_text(decision, "attempt_id") != attempt_id
            or _required_record_text(decision, "target_status") != target_status
        ):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "durable node verification returned different lineage",
            )
        return dict(committed_node)

    async def finalize_verification_failure(
        self,
        admitted: AdmittedExecution,
        *,
        node: Mapping[str, Any],
        graph: Mapping[str, Any],
        budget: BudgetRuntimeContext,
        attempt_id: str,
        reservation_id: str,
        evidence_refs: Sequence[str],
        verification_result: Mapping[str, Any],
        defer_shared_terminal: bool,
    ) -> tuple[dict[str, Any], dict[str, Any], BudgetRuntimeContext]:
        """Persist verification rejection and direct terminal state atomically."""

        # 并行 DAG 由外层统一裁决图终态；单节点路径则要原子关闭节点、图和预算。
        if defer_shared_terminal:
            failed_node = await self.commit_node_verification(
                admitted,
                node,
                attempt_id=attempt_id,
                reservation_id=reservation_id,
                target_status="VERIFICATION_FAILED",
                evidence_refs=evidence_refs,
                verification_result=verification_result,
            )
            return failed_node, dict(graph), await self._budgets.refresh(budget)

        node_version = _version(node)
        graph_version = _version(graph)
        node_outbox = self._runtime_outbox.build(
            command_id=admitted.command_id,
            aggregate_type="TASK_NODE",
            aggregate_id=(
                f"{admitted.graph_id}@{admitted.graph_revision}/"
                f"{admitted.node_id}"
            ),
            aggregate_version=node_version + 1,
            event_type="TASK_NODE_VERIFICATION_FAILED",
            attempt_id=attempt_id,
            evidence_refs=evidence_refs,
        )
        graph_outbox = self._runtime_outbox.build(
            command_id=admitted.command_id,
            aggregate_type="TASK_GRAPH",
            aggregate_id=admitted.graph_id,
            graph_revision=admitted.graph_revision,
            aggregate_version=graph_version + 1,
            event_type="TASK_GRAPH_FAILED",
        )
        budget_outbox = self._budgets.build_finalization_outbox(
            budget,
            target_status=BudgetReservationStatus.RELEASED,
            expected_version=budget.version,
        )
        arguments = {
            "attempt_id": attempt_id,
            "reservation_id": reservation_id,
            "budget_id": budget.budget_id,
            "expected_node_version": node_version,
            "expected_graph_version": graph_version,
            "expected_budget_version": budget.version,
            "verification_result": verification_result,
            "node_outbox": node_outbox,
            "graph_outbox": graph_outbox,
            "budget_outbox": budget_outbox,
        }
        try:
            raw = await _maybe_await(
                self._store.finalize_node_verification_failure(
                    admitted.graph_id,
                    admitted.graph_revision,
                    admitted.node_id,
                    **arguments,
                )
            )
        except Exception:
            raw = await _maybe_await(
                self._store.finalize_node_verification_failure(
                    admitted.graph_id,
                    admitted.graph_revision,
                    admitted.node_id,
                    **arguments,
                )
            )
        result = _mapping(
            raw,
            field_name="atomic verification failure terminal",
        )
        failed_node = result.get("node")
        failed_graph = result.get("graph")
        released_budget = result.get("budget")
        if (
            not isinstance(failed_node, Mapping)
            or not isinstance(failed_graph, Mapping)
            or not isinstance(released_budget, Mapping)
            or _status(failed_node) != "VERIFICATION_FAILED"
            or _status(failed_graph) != "FAILED"
            or _status(released_budget) != BudgetReservationStatus.RELEASED.value
        ):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "atomic verification failure returned different terminal state",
            )
        refreshed_budget = await self._budgets.refresh(
            budget.refreshed(released_budget)
        )
        return dict(failed_node), dict(failed_graph), refreshed_budget

    async def accept_node_output(
        self,
        admitted: AdmittedExecution,
        node: Mapping[str, Any],
        *,
        attempt_id: str,
        receipt_ref: str,
        worker_output: Mapping[str, Any],
        evidence_refs: Sequence[str] = (),
        acceptance_judgement: Mapping[str, Any],
        effect_intents: Sequence[Any] = (),
    ) -> dict[str, Any]:
        """Atomically accept one node and persist its canonical output."""

        # Canonical output 只能随 ACCEPT 决定一并落库，避免未验收的 worker 输出被后续节点消费。
        return await commit_accepted_node_output(
            store=self._store,
            runtime_outbox=self._runtime_outbox,
            admitted=admitted,
            node=node,
            attempt_id=attempt_id,
            receipt_ref=receipt_ref,
            worker_output=worker_output,
            evidence_refs=evidence_refs,
            acceptance_judgement=acceptance_judgement,
            effect_intents=effect_intents,
        )

    async def commit_node_acceptance_review(
        self,
        admitted: AdmittedExecution,
        *,
        node: Mapping[str, Any],
        graph: Mapping[str, Any],
        attempt_id: str,
        evidence_refs: Sequence[str],
        acceptance_judgement: Mapping[str, Any],
        review_item: Any,
        defer_shared_terminal: bool,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        """Freeze non-ACCEPT judgement, ReviewItem and waiting state in one UoW."""

        # 非 ACCEPT 不被当作失败猜测，而是固化为可恢复的 ReviewItem 与等待状态。
        return await commit_acceptance_review(
            store=self._store,
            runtime_outbox=self._runtime_outbox,
            admitted=admitted,
            node=node,
            graph=graph,
            attempt_id=attempt_id,
            evidence_refs=evidence_refs,
            acceptance_judgement=acceptance_judgement,
            review_item=review_item,
            defer_shared_terminal=defer_shared_terminal,
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
        # fencing token 由存储层对活跃租约核验，淘汰 owner 不能继续推进 Attempt。
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
                budget=charge.context,
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
        budget: BudgetRuntimeContext | None = None,
    ) -> str:
        """Close the execution authority atomically before reservation.

        The exact same mutation is replayed once because the first transaction
        may have committed before its response was lost.  There is deliberately
        no sequential fallback: a partial terminal state would be less safe
        than surfacing a durable conflict.
        """

        # 预约前仍可安全关闭执行权；预约后副作用已可能发生，必须转入操作恢复而不能走此分支。
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
            if budget is not None:
                arguments.update(
                    budget_id=budget.budget_id,
                    expected_budget_version=budget.version,
                    budget_outbox=self._budgets.build_finalization_outbox(
                        budget,
                        target_status=BudgetReservationStatus.RELEASED,
                    ),
                )
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
        if budget is not None and not defer_graph_terminal:
            released_budget = _mapping(
                result.get("budget"),
                field_name="atomic terminal failure BudgetReservation",
            )
            if (
                _status(released_budget) != BudgetReservationStatus.RELEASED.value
                or _version(released_budget) != budget.version + 1
            ):
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    "atomic terminal failure did not release the bound BudgetReservation",
                )
        await self._runtime_outbox.publish_pending()
        return error_ref


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


__all__ = ["DurableNodeLifecycleRuntime"]
