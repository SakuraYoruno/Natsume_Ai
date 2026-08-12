"""Durable graph-revision budget lifecycle for the governed coordinator."""

from __future__ import annotations

import hashlib
import inspect
import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetAmount,
    BudgetExceededAction,
    BudgetPolicy,
    BudgetReservation,
    BudgetReservationStatus,
)
from host_brain.contracts.agent_runtime.contract_errors import ContractViolation
from host_brain.contracts.agent_runtime.node_budget_reservation import (
    NodeBudgetReservationV1,
)
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3

from .admission import AdmittedExecution
from .errors import (
    GOVERNED_BUDGET_ACTION_UNSUPPORTED,
    GOVERNED_BUDGET_POLICY_REQUIRED,
    GOVERNED_BUDGET_STATE_CONFLICT,
    GOVERNED_TIME_BUDGET_EXCEEDED,
    GOVERNED_BUDGET_USAGE_INVALID,
    GovernedRuntimeError,
)
from .ports import TaskOutboxFactoryPort, TaskStorePort
from .worker_budget_usage import WorkerBudgetUsage, WorkerBudgetUsageError


_USAGE_REF = re.compile(
    r"^[a-z][a-z0-9+.-]{1,31}://"
    r"[A-Za-z0-9][A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{0,511}$"
)
_WORKER_USAGE_FIELDS = frozenset(
    {"usage_id", "token_count", "time_ms", "retry_count", "model_cost_micros"}
)
SUPPORTED_RUNTIME_BUDGET_ACTIONS = frozenset({BudgetExceededAction.BLOCK})


# 这是图修订绑定的预算快照；所有剩余额度都应来自持久投影，不能由当前进程自行推算。
@dataclass(frozen=True, slots=True)
class BudgetRuntimeContext:
    budget_id: str
    command_id: str
    graph_id: str
    graph_revision: int
    policy: BudgetPolicy
    record: dict[str, Any]
    node_record: dict[str, Any] | None = None

    @property
    def status(self) -> str:
        return str(self.record.get("status") or "").strip().upper()

    @property
    def version(self) -> int:
        return int(self.record.get("status_version") or 0)

    def refreshed(self, record: Mapping[str, Any]) -> "BudgetRuntimeContext":
        return replace(self, record=dict(record))

    def bound_to_node(
        self,
        record: Mapping[str, Any],
    ) -> "BudgetRuntimeContext":
        return replace(self, node_record=dict(record))

    @property
    def node_budget_id(self) -> str | None:
        if self.node_record is None:
            return None
        return str(self.node_record.get("node_budget_id") or "").strip() or None

    def execution_payload(self) -> dict[str, Any]:
        payload = {
            "budget_id": self.budget_id,
            "scope": self.policy.scope,
            "status": self.status,
            "status_version": self.version,
            "on_exceeded": self.policy.on_exceeded.value,
            "limits": dict(self.record.get("limits") or self.policy.limits.to_dict()),
            "consumed": dict(self.record.get("consumed") or {}),
            "remaining": dict(self.record.get("remaining") or {}),
        }
        if self.node_record is not None:
            payload["node_reservation"] = {
                "schema_version": self.node_record.get("schema_version"),
                "node_budget_id": self.node_record.get("node_budget_id"),
                "parent_budget_id": self.node_record.get("parent_budget_id"),
                "node_id": self.node_record.get("node_id"),
                "attempt_id": self.node_record.get("attempt_id"),
                "status": self.node_record.get("status"),
                "usage_version": self.node_record.get("usage_version"),
                "reserved": dict(self.node_record.get("reserved") or {}),
                "consumed": dict(self.node_record.get("consumed") or {}),
                "remaining": dict(self.node_record.get("remaining") or {}),
            }
        return payload

    def remaining_execution_time_ms(self) -> int | None:
        """Return the durable worker deadline, if this graph budgets wall time.

        A zero configured limit is legacy-compatible "not bounded" rather
        than an implicit zero-millisecond deadline.  A positive configured
        limit must always be accompanied by the canonical reconciliation
        projection; silently falling back to a policy value would let a worker
        exceed time already consumed by a sibling or a previous attempt.
        """

        # 图预算与节点预算同时存在时取更小值，避免并行兄弟或同一节点重试透支总配额。
        configured_limit = int(self.policy.limits.time_ms)
        if configured_limit <= 0:
            return None
        remaining = self.record.get("remaining")
        if not isinstance(remaining, Mapping):
            raise GovernedRuntimeError(
                GOVERNED_BUDGET_STATE_CONFLICT,
                "positive time budget has no durable remaining projection",
            )
        value = remaining.get("time_ms")
        if isinstance(value, bool) or not isinstance(value, int):
            raise GovernedRuntimeError(
                GOVERNED_BUDGET_STATE_CONFLICT,
                "durable remaining time budget is not an integer",
            )
        if value < 0 or value > configured_limit:
            raise GovernedRuntimeError(
                GOVERNED_BUDGET_STATE_CONFLICT,
                "durable remaining time budget is outside its configured limit",
            )
        if self.node_record is None:
            return value
        node_remaining = self.node_record.get("remaining")
        if not isinstance(node_remaining, Mapping):
            raise GovernedRuntimeError(
                GOVERNED_BUDGET_STATE_CONFLICT,
                "node BudgetReservation has no durable remaining projection",
            )
        node_value = node_remaining.get("time_ms")
        if isinstance(node_value, bool) or not isinstance(node_value, int):
            raise GovernedRuntimeError(
                GOVERNED_BUDGET_STATE_CONFLICT,
                "node remaining time budget is not an integer",
            )
        if node_value < 0 or node_value > configured_limit:
            raise GovernedRuntimeError(
                GOVERNED_BUDGET_STATE_CONFLICT,
                "node remaining time budget is outside its configured limit",
            )
        return min(value, node_value)


@dataclass(frozen=True, slots=True)
class BudgetCharge:
    context: BudgetRuntimeContext
    accepted: bool
    usage_id: str
    replayed: bool
    exceeded_dimensions: tuple[str, ...]
    evidence_ref: str


class DurableBudgetRuntime:
    """Own one shared BudgetReservation for an admitted graph revision."""

    def __init__(
        self,
        *,
        task_store: TaskStorePort,
        outbox_factory: TaskOutboxFactoryPort,
        cas_retries: int = 4,
    ) -> None:
        self._store = task_store
        self._outbox_factory = outbox_factory
        self._cas_retries = max(1, int(cas_retries))

    async def ensure_active(
        self,
        *,
        admitted: AdmittedExecution,
        work_order: Any,
    ) -> BudgetRuntimeContext:
        # 先从同一调度快照确认图仍能接收预算权；终态图不得因恢复调用重新创建预约。
        policy = _required_policy(admitted, work_order)
        budget_id = _budget_id(admitted, policy)
        await _maybe_await(self._store.assert_budget_usage_ready())
        await _maybe_await(self._store.assert_node_budget_ready())
        snapshot = await _maybe_await(
            self._store.read_scheduler_snapshot(admitted.graph_id, admitted.graph_revision)
        )
        budgets = snapshot.get("budgets") if isinstance(snapshot, Mapping) else None
        if not isinstance(budgets, list):
            raise GovernedRuntimeError(
                GOVERNED_BUDGET_STATE_CONFLICT,
                "scheduler snapshot does not expose durable budgets",
            )
        graph_record = (
            snapshot.get("graph_record") if isinstance(snapshot, Mapping) else None
        )
        if not isinstance(graph_record, Mapping):
            raise GovernedRuntimeError(
                GOVERNED_BUDGET_STATE_CONFLICT,
                "scheduler snapshot does not expose the durable TaskGraph",
            )
        graph_status = str(graph_record.get("status") or "").strip().upper()
        graph_accepts_new_budget_authority = graph_status in {"CREATED", "ACTIVE"}
        graph_budgets = [dict(item) for item in budgets if isinstance(item, Mapping)]
        if graph_budgets and any(str(item.get("budget_id")) != budget_id for item in graph_budgets):
            raise GovernedRuntimeError(
                GOVERNED_BUDGET_STATE_CONFLICT,
                "graph revision is already bound to a different BudgetPolicy",
            )

        record = await self._try_reconcile(budget_id)
        if record is None:
            # 创建竞争或响应丢失后立刻对账，只有确实不存在时才报告失败，避免重复创建预算。
            if not graph_accepts_new_budget_authority:
                raise GovernedRuntimeError(
                    GOVERNED_BUDGET_STATE_CONFLICT,
                    "new graph-level BudgetReservation is forbidden for "
                    f"TaskGraph status {graph_status or 'UNKNOWN'}",
                )
            reservation = BudgetReservation(
                budget_id=budget_id,
                scope=policy.scope,
                limits=policy.limits,
                reserved=policy.limits,
            )
            try:
                record = dict(
                    await _maybe_await(
                        self._store.create_budget_reservation(
                            reservation,
                            graph_id=admitted.graph_id,
                            graph_revision=admitted.graph_revision,
                            node_id=None,
                            attempt_id=None,
                            outbox=self._outbox(
                                budget_id=budget_id,
                                aggregate_version=0,
                                event_type="BUDGET_RESERVATION_CREATED",
                                discriminator="create",
                            ),
                        )
                    )
                )
            except BaseException as create_exc:
                record = await self._try_reconcile(budget_id)
                if record is None:
                    raise GovernedRuntimeError(
                        GOVERNED_BUDGET_STATE_CONFLICT,
                        f"BudgetReservation creation failed: {_reason(create_exc)}",
                    ) from create_exc

        self._assert_binding(admitted, policy, budget_id, record)
        if _status(record) == BudgetReservationStatus.PENDING.value:
            # 激活也采用“写后对账”：进程不知道首个事务是否提交时，绝不凭异常重置状态。
            if not graph_accepts_new_budget_authority:
                raise GovernedRuntimeError(
                    GOVERNED_BUDGET_STATE_CONFLICT,
                    "BudgetReservation activation is forbidden for "
                    f"TaskGraph status {graph_status or 'UNKNOWN'}",
                )
            try:
                record = dict(
                    await _maybe_await(
                        self._store.transition_budget(
                            budget_id,
                            expected_version=_version(record),
                            target_status=BudgetReservationStatus.ACTIVE.value,
                            outbox=self._outbox(
                                budget_id=budget_id,
                                aggregate_version=_version(record) + 1,
                                event_type="BUDGET_RESERVATION_ACTIVE",
                                discriminator="activate",
                            ),
                        )
                    )
                )
            except BaseException as exc:
                record = await self._required_reconcile(budget_id)
                if _status(record) != BudgetReservationStatus.ACTIVE.value:
                    raise GovernedRuntimeError(
                        GOVERNED_BUDGET_STATE_CONFLICT,
                        f"BudgetReservation activation failed: {_reason(exc)}",
                    ) from exc
        record = await self._required_reconcile(budget_id)
        self._assert_binding(admitted, policy, budget_id, record)
        return BudgetRuntimeContext(
            budget_id=budget_id,
            command_id=admitted.command_id,
            graph_id=admitted.graph_id,
            graph_revision=admitted.graph_revision,
            policy=policy,
            record=record,
        )

    async def bind_node_attempt(
        self,
        context: BudgetRuntimeContext,
        *,
        node_id: str,
        attempt_id: str,
    ) -> BudgetRuntimeContext:
        # 每笔扣费必须绑定精确的 Attempt 子预约，防止已淘汰节点借用共享图预算继续消费。
        value = await _maybe_await(
            self._store.get_node_budget_reservation(
                parent_budget_id=context.budget_id,
                attempt_id=attempt_id,
            )
        )
        if not isinstance(value, Mapping):
            raise GovernedRuntimeError(
                GOVERNED_BUDGET_STATE_CONFLICT,
                "node BudgetReservation reader returned an invalid record",
            )
        record = dict(value)
        try:
            NodeBudgetReservationV1.from_mapping(record)
        except ContractViolation as exc:
            raise GovernedRuntimeError(
                GOVERNED_BUDGET_STATE_CONFLICT,
                f"node BudgetReservation contract is invalid: {exc.detail}",
            ) from exc
        if (
            str(record.get("parent_budget_id") or "") != context.budget_id
            or str(record.get("graph_id") or "") != context.graph_id
            or int(record.get("graph_revision") or 0)
            != context.graph_revision
            or str(record.get("node_id") or "") != str(node_id)
            or str(record.get("attempt_id") or "") != str(attempt_id)
            or not str(record.get("node_budget_id") or "").strip()
            or str(record.get("status") or "")
            not in {
                "ACTIVE",
                "CONSUMED",
                "RELEASED",
                "EXCEEDED",
                "CANCEL_REQUESTED",
            }
        ):
            raise GovernedRuntimeError(
                GOVERNED_BUDGET_STATE_CONFLICT,
                "node BudgetReservation differs from the claimed Attempt",
            )
        return context.bound_to_node(record)

    async def precharge(
        self,
        context: BudgetRuntimeContext,
        *,
        node_id: str,
        attempt_id: str,
        tool_call_count: int = 1,
        execution_segment: str | None = None,
    ) -> BudgetCharge:
        # 在创建操作预约之前预扣确定性工具成本；预算不足时外部 worker 尚未被触发。
        if (
            isinstance(tool_call_count, bool)
            or not isinstance(tool_call_count, int)
            or tool_call_count < 0
        ):
            raise GovernedRuntimeError(
                GOVERNED_BUDGET_USAGE_INVALID,
                "precharge tool_call_count must be a non-negative integer",
            )
        usage_id = _internal_usage_id(
            context,
            node_id,
            attempt_id,
            _segmented_kind("worker-precharge", execution_segment),
        )
        return await self._charge(
            context,
            usage_id=usage_id,
            charge_kind="PRECHARGE",
            node_id=node_id,
            attempt_id=attempt_id,
            delta=BudgetAmount(tool_call_count=tool_call_count),
        )

    async def guard_execution_time(
        self,
        context: BudgetRuntimeContext,
        *,
        node_id: str,
        attempt_id: str,
        execution_segment: str | None = None,
    ) -> BudgetCharge | None:
        """Fail before dispatch when a bounded graph has no time left.

        The denied one-millisecond usage is intentional: it is the durable
        proof that this exact Attempt was blocked by ``time_ms`` before an
        Operation reservation or worker could cross an external-effect
        boundary.  Positive remaining time is not pre-reserved here; a worker
        is charged for its measured elapsed time at its normal terminal edge.
        """

        # 时间已耗尽时写入一条被拒绝的最小使用记录，作为“未派发 worker”的持久证据。
        remaining = context.remaining_execution_time_ms()
        if remaining is None or remaining > 0:
            return None
        usage_id = _internal_usage_id(
            context,
            node_id,
            attempt_id,
            _segmented_kind("time-guard", execution_segment),
        )
        charge = await self._charge(
            context,
            usage_id=usage_id,
            charge_kind="TIME_GUARD",
            node_id=node_id,
            attempt_id=attempt_id,
            delta=BudgetAmount(time_ms=1),
        )
        if charge.accepted or "time_ms" not in charge.exceeded_dimensions:
            raise GovernedRuntimeError(
                GOVERNED_BUDGET_STATE_CONFLICT,
                "zero remaining time budget did not produce a durable time rejection",
            )
        return charge

    async def settle_worker(
        self,
        context: BudgetRuntimeContext,
        *,
        node_id: str,
        attempt_id: str,
        worker_commit: Mapping[str, Any] | None,
        elapsed_ms: int,
        execution_segment: str | None = None,
    ) -> BudgetCharge:
        elapsed = max(0, int(elapsed_ms))
        payload = worker_commit.get("budget_usage") if isinstance(worker_commit, Mapping) else None
        if payload is None:
            usage_id = _internal_usage_id(
                context,
                node_id,
                attempt_id,
                _segmented_kind("worker-elapsed", execution_segment),
            )
            delta = BudgetAmount(time_ms=elapsed)
            kind = "WORKER_ELAPSED"
        else:
            usage_id, delta, kind = _worker_usage(payload, elapsed_ms=elapsed)
        return await self._charge(
            context,
            usage_id=usage_id,
            charge_kind=kind,
            node_id=node_id,
            attempt_id=attempt_id,
            delta=delta,
        )

    async def settle_interrupted_worker(
        self,
        context: BudgetRuntimeContext,
        *,
        node_id: str,
        attempt_id: str,
        worker_started_ns: int,
        worker_failure_reason: str,
        time_budget_ms: int | None,
        execution_segment: str | None = None,
    ) -> BudgetCharge:
        """Settle an interrupted worker without weakening timeout evidence.

        A local time guard may fire exactly at its configured boundary.  Its
        elapsed measurement is therefore lifted one millisecond above the
        remaining allowance, ensuring the durable ledger records a rejected
        ``time_ms`` charge rather than an ambiguous active reservation.
        """

        elapsed_ms = max(
            0,
            (time.monotonic_ns() - int(worker_started_ns) + 999_999) // 1_000_000,
        )
        if (
            worker_failure_reason == GOVERNED_TIME_BUDGET_EXCEEDED
            and time_budget_ms is not None
        ):
            elapsed_ms = max(elapsed_ms, int(time_budget_ms) + 1)
        return await self.settle_worker(
            context,
            node_id=node_id,
            attempt_id=attempt_id,
            worker_commit=None,
            elapsed_ms=elapsed_ms,
            execution_segment=execution_segment,
        )

    async def settle_worker_checkpoint(
        self,
        context: BudgetRuntimeContext,
        *,
        node_id: str,
        attempt_id: str,
        worker_commit: Mapping[str, Any],
        durable_elapsed_ms: int,
    ) -> BudgetCharge:
        """Resume one COMMITTED worker checkpoint without duplicating usage.

        A prior owner may already have charged the budget before it crashed.
        In that case the immutable usage journal is the checkpoint and is
        returned directly.  Otherwise the trusted durable Attempt-to-COMMIT
        elapsed time supplies the missing wall-clock charge.
        """

        if (
            type(durable_elapsed_ms) is not int
            or durable_elapsed_ms <= 0
        ):
            raise GovernedRuntimeError(
                GOVERNED_BUDGET_USAGE_INVALID,
                "worker checkpoint durable elapsed time must be a positive integer",
            )

        payload = worker_commit.get("budget_usage")
        if payload is None:
            usage_id = _internal_usage_id(
                context,
                node_id,
                attempt_id,
                "worker-elapsed",
            )
            base_delta = BudgetAmount()
            charge_kind = "WORKER_ELAPSED"
        else:
            usage_id, base_delta, charge_kind = _worker_usage(payload, elapsed_ms=0)
        existing = await self._try_budget_usage(context.budget_id, usage_id)
        if existing is not None:
            _assert_checkpoint_usage(
                existing,
                context=context,
                usage_id=usage_id,
                charge_kind=charge_kind,
                node_id=node_id,
                attempt_id=attempt_id,
                base_delta=base_delta,
            )
            updated = await self._required_reconcile(context.budget_id)
            refreshed_context = await self._refresh_node_binding(
                context.refreshed(updated)
            )
            return BudgetCharge(
                context=refreshed_context,
                accepted=bool(existing.get("accepted")),
                usage_id=usage_id,
                replayed=True,
                exceeded_dimensions=tuple(
                    str(item) for item in existing.get("exceeded_dimensions") or ()
                ),
                evidence_ref=_usage_evidence_ref(context.budget_id, usage_id),
            )
        return await self.settle_worker(
            context,
            node_id=node_id,
            attempt_id=attempt_id,
            worker_commit=worker_commit,
            elapsed_ms=durable_elapsed_ms,
        )

    async def reconcile_worker_checkpoint(
        self,
        context: BudgetRuntimeContext,
        *,
        node_id: str,
        attempt_id: str,
        worker_commit: Mapping[str, Any],
        durable_usage: Mapping[str, Any],
    ) -> BudgetCharge:
        """Read one exact worker usage outcome without inventing a charge.

        Online takeover is intentionally narrower than same-process COMMIT
        reconciliation: it may only continue from a complete post-budget
        checkpoint.  Missing or ambiguous evidence is rejected before this
        method.  An exact denied row is returned so the lifecycle layer can
        finish the canonical budget-blocked terminal bundle after a crash.
        """

        usage_id = str(durable_usage.get("usage_id") or "").strip()
        existing = await self._try_budget_usage(context.budget_id, usage_id)
        if existing is None or dict(existing) != dict(durable_usage):
            raise GovernedRuntimeError(
                GOVERNED_BUDGET_USAGE_INVALID,
                "worker checkpoint BudgetUsage is not one immutable durable row",
            )
        charge_kind = str(existing.get("charge_kind") or "").strip()
        if not (
            charge_kind.startswith("WORKER_USAGE")
            or charge_kind == "WORKER_ELAPSED"
        ):
            raise GovernedRuntimeError(
                GOVERNED_BUDGET_USAGE_INVALID,
                "checkpoint points to a precharge/retry BudgetUsage",
            )
        _assert_usage_identity(
            existing,
            context=context,
            usage_id=usage_id,
            charge_kind=charge_kind,
            node_id=node_id,
            attempt_id=attempt_id,
        )
        payload = worker_commit.get("budget_usage")
        if isinstance(payload, Mapping):
            expected_usage_id, base_delta, expected_kind = _worker_usage(
                payload,
                elapsed_ms=0,
            )
            if usage_id != expected_usage_id or charge_kind != expected_kind:
                raise GovernedRuntimeError(
                    GOVERNED_BUDGET_USAGE_INVALID,
                    "checkpoint BudgetUsage identity differs from worker projection",
                )
            _assert_checkpoint_usage(
                existing,
                context=context,
                usage_id=usage_id,
                charge_kind=charge_kind,
                node_id=node_id,
                attempt_id=attempt_id,
                base_delta=base_delta,
            )
        updated = await self._required_reconcile(context.budget_id)
        refreshed_context = await self._refresh_node_binding(
            context.refreshed(updated)
        )
        return BudgetCharge(
            context=refreshed_context,
            accepted=bool(existing.get("accepted")),
            usage_id=usage_id,
            replayed=True,
            exceeded_dimensions=tuple(
                str(item) for item in existing.get("exceeded_dimensions") or ()
            ),
            evidence_ref=_usage_evidence_ref(context.budget_id, usage_id),
        )

    async def finalize(
        self,
        context: BudgetRuntimeContext,
        *,
        target_status: BudgetReservationStatus,
    ) -> BudgetRuntimeContext:
        # 终结只能消耗或释放；EXCEEDED 统一释放，避免把超限状态错误包装成正常完成。
        _require_supported_runtime_action(context.policy)
        if target_status not in {
            BudgetReservationStatus.CONSUMED,
            BudgetReservationStatus.RELEASED,
        }:
            raise ValueError("budget final status must be CONSUMED or RELEASED")
        record = await self._required_reconcile(context.budget_id)
        status = _status(record)
        if status == target_status.value:
            return await self._refresh_node_binding(
                context.refreshed(record)
            )
        if status == BudgetReservationStatus.EXCEEDED.value:
            target_status = BudgetReservationStatus.RELEASED
        elif status != BudgetReservationStatus.ACTIVE.value:
            raise GovernedRuntimeError(
                GOVERNED_BUDGET_STATE_CONFLICT,
                f"cannot finalize budget {context.budget_id} from {status}",
            )
        try:
            updated = await _maybe_await(
                self._store.transition_budget(
                    context.budget_id,
                    expected_version=_version(record),
                    target_status=target_status.value,
                    outbox=self.build_finalization_outbox(
                        context,
                        target_status=target_status,
                        expected_version=_version(record),
                    ),
                )
            )
        except BaseException as exc:
            current = await self._required_reconcile(context.budget_id)
            if _status(current) != target_status.value:
                raise GovernedRuntimeError(
                    GOVERNED_BUDGET_STATE_CONFLICT,
                    f"BudgetReservation finalization failed: {_reason(exc)}",
                ) from exc
            updated = current
        refreshed = context.refreshed(
            await self._required_reconcile(str(updated["budget_id"]))
        )
        return await self._refresh_node_binding(refreshed)

    async def refresh(self, context: BudgetRuntimeContext) -> BudgetRuntimeContext:
        refreshed = context.refreshed(
            await self._required_reconcile(context.budget_id)
        )
        return await self._refresh_node_binding(refreshed)

    def build_finalization_outbox(
        self,
        context: BudgetRuntimeContext,
        *,
        target_status: BudgetReservationStatus,
        expected_version: int | None = None,
    ) -> Any:
        """Build the exact graph-budget finalization event without mutating it.

        A direct pre-reservation terminal UoW uses this immutable event while
        it closes Attempt/Node/Graph/Lease.  Ordinary callers still pass the
        same event through :meth:`finalize`, so response replay keeps one
        idempotency identity across both persistence boundaries.
        """

        _require_supported_runtime_action(context.policy)
        if target_status not in {
            BudgetReservationStatus.CONSUMED,
            BudgetReservationStatus.RELEASED,
        }:
            raise ValueError("budget final status must be CONSUMED or RELEASED")
        version = context.version if expected_version is None else int(expected_version)
        if version < 0:
            raise ValueError("budget finalization version must be non-negative")
        return self._outbox(
            budget_id=context.budget_id,
            aggregate_version=version + 1,
            event_type=f"BUDGET_RESERVATION_{target_status.value}",
            discriminator=f"finalize:{target_status.value}",
        )

    async def _charge(
        self,
        context: BudgetRuntimeContext,
        *,
        usage_id: str,
        charge_kind: str,
        node_id: str,
        attempt_id: str,
        delta: BudgetAmount,
    ) -> BudgetCharge:
        # 使用记录以 usage_id 幂等化，CAS 只解决并发版本竞争；响应丢失必须从已写使用记录精确重放。
        _require_supported_runtime_action(context.policy)
        self._assert_node_charge_binding(
            context,
            node_id=node_id,
            attempt_id=attempt_id,
        )
        last_exc: BaseException | None = None
        # READY claims cap simultaneous writers at the policy concurrency.
        # Size the CAS budget to that admitted fan-out instead of the old
        # fixed four attempts, while preserving the configured retry floor.
        cas_attempts = max(
            self._cas_retries,
            int(context.policy.limits.concurrency) + 1,
        )
        for _ in range(cas_attempts):
            record = await self._required_reconcile(context.budget_id)
            try:
                result = await _maybe_await(
                    self._store.consume_budget_once(
                        context.budget_id,
                        usage_id=usage_id,
                        charge_kind=charge_kind,
                        graph_id=context.graph_id,
                        graph_revision=context.graph_revision,
                        node_id=node_id,
                        attempt_id=attempt_id,
                        delta=delta,
                        expected_version=_version(record),
                        outbox=self._outbox(
                            budget_id=context.budget_id,
                            aggregate_version=_version(record) + 1,
                            event_type=(
                                "BUDGET_EXCEEDED"
                                if _would_exceed(record, delta)
                                else "BUDGET_CONSUMED"
                            ),
                            discriminator=f"usage:{usage_id}",
                            attempt_id=attempt_id,
                            usage_id=usage_id,
                        ),
                    )
                )
                updated = await self._required_reconcile(context.budget_id)
                refreshed_context = await self._refresh_node_binding(
                    context.refreshed(updated)
                )
                dimensions = tuple(str(item) for item in result.exceeded_dimensions)
                return BudgetCharge(
                    context=refreshed_context,
                    accepted=bool(result.accepted),
                    usage_id=usage_id,
                    replayed=bool(getattr(result, "replayed", False)),
                    exceeded_dimensions=dimensions,
                    evidence_ref=_usage_evidence_ref(context.budget_id, usage_id),
                )
            except BaseException as exc:
                # 进程控制信号必须原样传播；不能把取消误当作一次可安全重放的存储响应丢失。
                # Cancellation and process-control signals are not transport
                # response-loss evidence.  Even if the durable usage row is
                # already readable, the current task must propagate the
                # signal and let a later fenced owner resume the checkpoint.
                if not isinstance(exc, Exception):
                    raise
                last_exc = exc
                replay = await self._try_budget_usage(context.budget_id, usage_id)
                if replay is not None:
                    _assert_exact_usage(
                        replay,
                        context=context,
                        usage_id=usage_id,
                        charge_kind=charge_kind,
                        node_id=node_id,
                        attempt_id=attempt_id,
                        delta=delta,
                    )
                    updated = await self._required_reconcile(context.budget_id)
                    refreshed_context = await self._refresh_node_binding(
                        context.refreshed(updated)
                    )
                    return BudgetCharge(
                        context=refreshed_context,
                        accepted=bool(replay.get("accepted")),
                        usage_id=usage_id,
                        replayed=True,
                        exceeded_dimensions=tuple(
                            str(item)
                            for item in replay.get("exceeded_dimensions") or ()
                        ),
                        evidence_ref=_usage_evidence_ref(
                            context.budget_id,
                            usage_id,
                        ),
                    )
                if _reason_code(exc) != "TASK_STORE_CAS_CONFLICT":
                    raise GovernedRuntimeError(
                        GOVERNED_BUDGET_STATE_CONFLICT,
                        f"durable budget charge failed: {_reason(exc)}",
                    ) from exc
        raise GovernedRuntimeError(
            GOVERNED_BUDGET_STATE_CONFLICT,
            f"durable budget CAS did not converge: {_reason(last_exc)}",
        ) from last_exc

    async def _refresh_node_binding(
        self,
        context: BudgetRuntimeContext,
    ) -> BudgetRuntimeContext:
        if context.node_record is None:
            return context
        return await self.bind_node_attempt(
            context,
            node_id=str(context.node_record.get("node_id") or ""),
            attempt_id=str(context.node_record.get("attempt_id") or ""),
        )

    @staticmethod
    def _assert_node_charge_binding(
        context: BudgetRuntimeContext,
        *,
        node_id: str,
        attempt_id: str,
    ) -> None:
        # 将父预算、节点和 Attempt 三元组闭合，拒绝跨节点或跨尝试的费用归属。
        record = context.node_record
        if (
            not isinstance(record, Mapping)
            or not context.node_budget_id
            or str(record.get("parent_budget_id") or "") != context.budget_id
            or str(record.get("node_id") or "") != str(node_id)
            or str(record.get("attempt_id") or "") != str(attempt_id)
            or str(record.get("status") or "") not in {"ACTIVE", "EXCEEDED"}
        ):
            raise GovernedRuntimeError(
                GOVERNED_BUDGET_STATE_CONFLICT,
                "budget charge requires the exact active node BudgetReservation",
            )

    async def _try_reconcile(self, budget_id: str) -> dict[str, Any] | None:
        try:
            value = await _maybe_await(self._store.reconcile_budget(budget_id))
        except BaseException as exc:
            if _reason_code(exc) in {"TASK_STORE_NOT_FOUND", "NOT_FOUND"}:
                return None
            raise
        return dict(value) if isinstance(value, Mapping) else None

    async def _required_reconcile(self, budget_id: str) -> dict[str, Any]:
        record = await self._try_reconcile(budget_id)
        if record is None:
            raise GovernedRuntimeError(
                GOVERNED_BUDGET_STATE_CONFLICT,
                f"BudgetReservation disappeared: {budget_id}",
            )
        return record

    async def _try_budget_usage(
        self,
        budget_id: str,
        usage_id: str,
    ) -> dict[str, Any] | None:
        reader = getattr(self._store, "get_budget_usage", None)
        if not callable(reader):
            return None
        try:
            value = await _maybe_await(reader(budget_id, usage_id))
        except BaseException as exc:
            if _reason_code(exc) in {"TASK_STORE_NOT_FOUND", "NOT_FOUND"}:
                return None
            raise
        return dict(value) if isinstance(value, Mapping) else None

    @staticmethod
    def _assert_binding(
        admitted: AdmittedExecution,
        policy: BudgetPolicy,
        budget_id: str,
        record: Mapping[str, Any],
    ) -> None:
        if (
            str(record.get("budget_id") or "") != budget_id
            or str(record.get("scope") or "") != policy.scope
            or str(record.get("graph_id") or "") != admitted.graph_id
            or int(record.get("graph_revision") or 0) != admitted.graph_revision
            or record.get("node_id") is not None
            or record.get("attempt_id") is not None
            or dict(record.get("limits") or {}) != policy.limits.to_dict()
            or dict(record.get("reserved") or {}) != policy.limits.to_dict()
        ):
            raise GovernedRuntimeError(
                GOVERNED_BUDGET_STATE_CONFLICT,
                "durable BudgetReservation differs from the admitted graph BudgetPolicy",
            )

    def _outbox(
        self,
        *,
        budget_id: str,
        aggregate_version: int,
        event_type: str,
        discriminator: str,
        attempt_id: str | None = None,
        usage_id: str | None = None,
    ) -> Any:
        # 事件身份只由预算与业务判别符导出，重试同一消费决策会得到同一 outbox 幂等键。
        digest = hashlib.sha256(
            f"{budget_id}\n{discriminator}".encode("utf-8")
        ).hexdigest()
        return self._outbox_factory.create(
            aggregate_type="BUDGET_RESERVATION",
            aggregate_id=budget_id,
            aggregate_version=int(aggregate_version),
            event_type=event_type,
            payload_ref=f"taskstore://event/BUDGET_RESERVATION/{budget_id}/{int(aggregate_version)}",
            idempotency_key=f"governed-budget:{digest}",
            attempt_id=attempt_id,
            evidence_refs=((usage_id,) if usage_id else ()),
            outbox_id=f"outbox::governed-budget::{digest}",
        )


def _required_policy(admitted: AdmittedExecution, work_order: Any) -> BudgetPolicy:
    if not isinstance(work_order, WorkOrderV3):
        raise GovernedRuntimeError(
            GOVERNED_BUDGET_POLICY_REQUIRED,
            "governed execution requires an explicit WorkOrderV3",
        )
    work_order.validate()
    policy = work_order.budget_policy
    if policy is None:
        raise GovernedRuntimeError(
            GOVERNED_BUDGET_POLICY_REQUIRED,
            "WorkOrderV3 has no BudgetPolicy",
        )
    _require_supported_runtime_action(policy)
    if (
        work_order.root_work_order_id != admitted.work_order_id
        or work_order.task_graph_id != admitted.graph_id
        or work_order.graph_revision != admitted.graph_revision
    ):
        raise GovernedRuntimeError(
            GOVERNED_BUDGET_POLICY_REQUIRED,
            "WorkOrderV3 budget lineage differs from the admitted graph revision",
        )
    return policy


def _require_supported_runtime_action(policy: BudgetPolicy) -> None:
    """Reject policy actions that have no durable production state machine.

    ``BudgetPolicy`` remains a forward-compatible contract and can describe
    DEGRADE or REVIEW proposals.  The current canonical executor must not
    silently reinterpret either proposal as BLOCK.  A future implementation
    may add them only together with immutable replacement-graph or budget
    exception authority and exact restart/replay evidence.
    """

    try:
        policy.validate()
    except ContractViolation as exc:
        raise GovernedRuntimeError(
            GOVERNED_BUDGET_POLICY_REQUIRED,
            "BudgetPolicy contract is invalid",
        ) from exc
    if policy.on_exceeded not in SUPPORTED_RUNTIME_BUDGET_ACTIONS:
        raise GovernedRuntimeError(
            GOVERNED_BUDGET_ACTION_UNSUPPORTED,
            (
                f"Budget action {policy.on_exceeded.value} has no canonical "
                "durable production state machine"
            ),
        )


def _worker_usage(payload: Any, *, elapsed_ms: int) -> tuple[str, BudgetAmount, str]:
    if isinstance(payload, Mapping) and payload.get("schema_version") is not None:
        try:
            typed = WorkerBudgetUsage.from_payload(payload)
        except WorkerBudgetUsageError as exc:
            raise GovernedRuntimeError(
                GOVERNED_BUDGET_USAGE_INVALID,
                f"worker budget_usage is invalid: {exc}",
            ) from exc
        return (
            typed.usage_id,
            typed.to_budget_amount(elapsed_ms=elapsed_ms),
            _typed_charge_kind(typed.measured_dimensions()),
        )
    if not isinstance(payload, Mapping) or set(payload) != _WORKER_USAGE_FIELDS:
        raise GovernedRuntimeError(
            GOVERNED_BUDGET_USAGE_INVALID,
            "worker budget_usage must contain exactly usage_id/token_count/time_ms/retry_count/model_cost_micros",
        )
    usage_id = str(payload.get("usage_id") or "").strip()
    if not _USAGE_REF.fullmatch(usage_id):
        raise GovernedRuntimeError(
            GOVERNED_BUDGET_USAGE_INVALID,
            "worker budget_usage.usage_id must be a stable durable reference",
        )
    values: dict[str, int] = {}
    for key in _WORKER_USAGE_FIELDS - {"usage_id"}:
        value = payload.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise GovernedRuntimeError(
                GOVERNED_BUDGET_USAGE_INVALID,
                f"worker budget_usage.{key} must be a non-negative integer",
            )
        values[key] = value
    return (
        usage_id,
        BudgetAmount(
            token_count=values["token_count"],
            time_ms=max(values["time_ms"], max(0, int(elapsed_ms))),
            retry_count=values["retry_count"],
            model_cost_micros=values["model_cost_micros"],
        ),
        "WORKER_USAGE_LEGACY",
    )


def _typed_charge_kind(dimensions: tuple[str, ...]) -> str:
    labels = {
        "token_count": "TOKEN",
        "tool_call_count": "TOOL",
        "time_ms": "TIME",
        "retry_count": "RETRY",
        "model_cost_micros": "COST",
    }
    return "WORKER_USAGE:" + "+".join(labels[item] for item in dimensions)


def _assert_exact_usage(
    record: Mapping[str, Any],
    *,
    context: BudgetRuntimeContext,
    usage_id: str,
    charge_kind: str,
    node_id: str,
    attempt_id: str,
    delta: BudgetAmount,
) -> None:
    _assert_usage_identity(
        record,
        context=context,
        usage_id=usage_id,
        charge_kind=charge_kind,
        node_id=node_id,
        attempt_id=attempt_id,
    )
    if dict(record.get("delta") or {}) != delta.to_dict():
        raise GovernedRuntimeError(
            GOVERNED_BUDGET_STATE_CONFLICT,
            f"durable budget usage replay drifted: {usage_id}",
        )


def _assert_checkpoint_usage(
    record: Mapping[str, Any],
    *,
    context: BudgetRuntimeContext,
    usage_id: str,
    charge_kind: str,
    node_id: str,
    attempt_id: str,
    base_delta: BudgetAmount,
) -> None:
    _assert_usage_identity(
        record,
        context=context,
        usage_id=usage_id,
        charge_kind=charge_kind,
        node_id=node_id,
        attempt_id=attempt_id,
    )
    durable_delta = dict(record.get("delta") or {})
    expected = base_delta.to_dict()
    if (
        set(durable_delta) != set(expected)
        or any(
            int(durable_delta.get(field) or 0) != int(expected[field])
            for field in expected
            if field != "time_ms"
        )
        or int(durable_delta.get("time_ms") or 0) < int(expected["time_ms"])
    ):
        raise GovernedRuntimeError(
            GOVERNED_BUDGET_STATE_CONFLICT,
            f"committed checkpoint budget usage drifted: {usage_id}",
        )


def _assert_usage_identity(
    record: Mapping[str, Any],
    *,
    context: BudgetRuntimeContext,
    usage_id: str,
    charge_kind: str,
    node_id: str,
    attempt_id: str,
) -> None:
    if (
        str(record.get("budget_id") or "") != context.budget_id
        or str(record.get("usage_id") or "") != usage_id
        or str(record.get("charge_kind") or "").upper() != charge_kind.upper()
        or str(record.get("graph_id") or "") != context.graph_id
        or int(record.get("graph_revision") or 0) != context.graph_revision
        or str(record.get("node_id") or "") != node_id
        or str(record.get("attempt_id") or "") != attempt_id
        or type(record.get("accepted")) is not bool
    ):
        raise GovernedRuntimeError(
            GOVERNED_BUDGET_STATE_CONFLICT,
            f"durable budget usage lineage drifted: {usage_id}",
        )


def _budget_id(admitted: AdmittedExecution, policy: BudgetPolicy) -> str:
    policy_json = json.dumps(
        policy.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    digest = hashlib.sha256(
        f"{admitted.command_id}\n{admitted.graph_id}\n{admitted.graph_revision}\n{policy_json}".encode(
            "utf-8"
        )
    ).hexdigest()
    return f"budget::governed::{digest}"


def _internal_usage_id(
    context: BudgetRuntimeContext,
    node_id: str,
    attempt_id: str,
    kind: str,
) -> str:
    digest = hashlib.sha256(
        f"{context.budget_id}\n{node_id}\n{attempt_id}\n{kind}".encode("utf-8")
    ).hexdigest()
    return f"budget-usage://{digest}"


def _segmented_kind(kind: str, execution_segment: str | None) -> str:
    segment = str(execution_segment or "").strip()
    if not segment:
        return kind
    if not re.fullmatch(r"reacquire-[0-9a-f]{32}", segment):
        raise GovernedRuntimeError(
            GOVERNED_BUDGET_USAGE_INVALID,
            "execution budget segment is invalid",
        )
    return f"{kind}:{segment}"


def _usage_evidence_ref(budget_id: str, usage_id: str) -> str:
    digest = hashlib.sha256(f"{budget_id}\n{usage_id}".encode("utf-8")).hexdigest()
    return f"budget-evidence://{digest}"


def _status(record: Mapping[str, Any]) -> str:
    return str(record.get("status") or "").strip().upper()


def _version(record: Mapping[str, Any]) -> int:
    return int(record.get("status_version") or 0)


def _would_exceed(record: Mapping[str, Any], delta: BudgetAmount) -> bool:
    consumed = BudgetAmount.from_mapping(
        record.get("consumed") if isinstance(record.get("consumed"), Mapping) else {}
    )
    reserved = BudgetAmount.from_mapping(
        record.get("reserved") if isinstance(record.get("reserved"), Mapping) else {}
    )
    attempted = {
        key: consumed.to_dict()[key] + delta.to_dict()[key]
        for key in consumed.to_dict()
    }
    return any(attempted[key] > reserved.to_dict()[key] for key in attempted)


def _reason_code(exc: BaseException) -> str:
    return str(getattr(exc, "reason_code", "") or "").strip().upper()


def _reason(exc: BaseException | None) -> str:
    if exc is None:
        return "UNKNOWN"
    return _reason_code(exc) or type(exc).__name__


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


__all__ = [
    "BudgetCharge",
    "BudgetRuntimeContext",
    "DurableBudgetRuntime",
    "SUPPORTED_RUNTIME_BUDGET_ACTIONS",
]
