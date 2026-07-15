"""Durable graph-revision budget lifecycle for the governed coordinator."""

from __future__ import annotations

import hashlib
import inspect
import json
import re
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
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3

from .admission import AdmittedExecution
from .errors import (
    GOVERNED_BUDGET_POLICY_REQUIRED,
    GOVERNED_BUDGET_STATE_CONFLICT,
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


@dataclass(frozen=True, slots=True)
class BudgetRuntimeContext:
    budget_id: str
    command_id: str
    graph_id: str
    graph_revision: int
    policy: BudgetPolicy
    record: dict[str, Any]

    @property
    def status(self) -> str:
        return str(self.record.get("status") or "").strip().upper()

    @property
    def version(self) -> int:
        return int(self.record.get("status_version") or 0)

    def refreshed(self, record: Mapping[str, Any]) -> "BudgetRuntimeContext":
        return replace(self, record=dict(record))

    def execution_payload(self) -> dict[str, Any]:
        return {
            "budget_id": self.budget_id,
            "scope": self.policy.scope,
            "status": self.status,
            "status_version": self.version,
            "on_exceeded": self.policy.on_exceeded.value,
            "limits": dict(self.record.get("limits") or self.policy.limits.to_dict()),
            "consumed": dict(self.record.get("consumed") or {}),
            "remaining": dict(self.record.get("remaining") or {}),
        }


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
        policy = _required_policy(admitted, work_order)
        budget_id = _budget_id(admitted, policy)
        await _maybe_await(self._store.assert_budget_usage_ready())
        snapshot = await _maybe_await(
            self._store.read_scheduler_snapshot(admitted.graph_id, admitted.graph_revision)
        )
        budgets = snapshot.get("budgets") if isinstance(snapshot, Mapping) else None
        if not isinstance(budgets, list):
            raise GovernedRuntimeError(
                GOVERNED_BUDGET_STATE_CONFLICT,
                "scheduler snapshot does not expose durable budgets",
            )
        graph_budgets = [dict(item) for item in budgets if isinstance(item, Mapping)]
        if graph_budgets and any(str(item.get("budget_id")) != budget_id for item in graph_budgets):
            raise GovernedRuntimeError(
                GOVERNED_BUDGET_STATE_CONFLICT,
                "graph revision is already bound to a different BudgetPolicy",
            )

        record = await self._try_reconcile(budget_id)
        if record is None:
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

    async def precharge(
        self,
        context: BudgetRuntimeContext,
        *,
        node_id: str,
        attempt_id: str,
        tool_call_count: int = 1,
    ) -> BudgetCharge:
        if (
            isinstance(tool_call_count, bool)
            or not isinstance(tool_call_count, int)
            or tool_call_count < 0
        ):
            raise GovernedRuntimeError(
                GOVERNED_BUDGET_USAGE_INVALID,
                "precharge tool_call_count must be a non-negative integer",
            )
        usage_id = _internal_usage_id(context, node_id, attempt_id, "worker-precharge")
        return await self._charge(
            context,
            usage_id=usage_id,
            charge_kind="PRECHARGE",
            node_id=node_id,
            attempt_id=attempt_id,
            delta=BudgetAmount(tool_call_count=tool_call_count),
        )

    async def settle_worker(
        self,
        context: BudgetRuntimeContext,
        *,
        node_id: str,
        attempt_id: str,
        worker_commit: Mapping[str, Any] | None,
        elapsed_ms: int,
    ) -> BudgetCharge:
        elapsed = max(0, int(elapsed_ms))
        payload = worker_commit.get("budget_usage") if isinstance(worker_commit, Mapping) else None
        if payload is None:
            usage_id = _internal_usage_id(context, node_id, attempt_id, "worker-elapsed")
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

    async def finalize(
        self,
        context: BudgetRuntimeContext,
        *,
        target_status: BudgetReservationStatus,
    ) -> BudgetRuntimeContext:
        if target_status not in {
            BudgetReservationStatus.CONSUMED,
            BudgetReservationStatus.RELEASED,
        }:
            raise ValueError("budget final status must be CONSUMED or RELEASED")
        record = await self._required_reconcile(context.budget_id)
        status = _status(record)
        if status == target_status.value:
            return context.refreshed(record)
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
                    outbox=self._outbox(
                        budget_id=context.budget_id,
                        aggregate_version=_version(record) + 1,
                        event_type=f"BUDGET_RESERVATION_{target_status.value}",
                        discriminator=f"finalize:{target_status.value}",
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
        return context.refreshed(await self._required_reconcile(str(updated["budget_id"])))

    async def refresh(self, context: BudgetRuntimeContext) -> BudgetRuntimeContext:
        return context.refreshed(await self._required_reconcile(context.budget_id))

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
        last_exc: BaseException | None = None
        for _ in range(self._cas_retries):
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
                dimensions = tuple(str(item) for item in result.exceeded_dimensions)
                return BudgetCharge(
                    context=context.refreshed(updated),
                    accepted=bool(result.accepted),
                    usage_id=usage_id,
                    replayed=bool(getattr(result, "replayed", False)),
                    exceeded_dimensions=dimensions,
                    evidence_ref=_usage_evidence_ref(context.budget_id, usage_id),
                )
            except BaseException as exc:
                last_exc = exc
                if _reason_code(exc) != "TASK_STORE_CAS_CONFLICT":
                    raise GovernedRuntimeError(
                        GOVERNED_BUDGET_STATE_CONFLICT,
                        f"durable budget charge failed: {_reason(exc)}",
                    ) from exc
        raise GovernedRuntimeError(
            GOVERNED_BUDGET_STATE_CONFLICT,
            f"durable budget CAS did not converge: {_reason(last_exc)}",
        ) from last_exc

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
    policy.validate()
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
]
