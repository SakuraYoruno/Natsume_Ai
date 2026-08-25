"""图取消的持久化读取与终态投影。

该模块只读取规范取消意图、决定、图、节点和预算，随后构造可重放的终态事件；
它不发起取消，也不把本地协程是否退出误解为取消已经在持久层完成。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from host_brain.contracts.agent_runtime.graph_cancellation import (
    GraphCancellationContractError,
    normalize_graph_cancellation_decision,
)

from .admission import AdmittedExecution
from .coordinator_receipts import build_execution_receipt, terminal_event
from .coordinator_records import (
    _attempt_id,
    _assert_durable_identity,
    _mapping,
    _required_record_text,
    _text,
)
from .errors import (
    GOVERNED_BUDGET_STATE_CONFLICT,
    GOVERNED_DURABLE_STATE_CONFLICT,
    GOVERNED_GRAPH_CANCELLATION_PENDING,
    GOVERNED_GRAPH_CANCELLED,
    GovernedRuntimeError,
)
from .graph_cancellation_records import (
    _cancellation_budget_projection,
    _validated_graph_cancellation_intent,
)
from .ports import TaskStorePort
from host_brain.core.runtime_helpers import maybe_await as _maybe_await


class GraphCancellationProjection:
    """读取规范取消状态并构建其终态回执。"""

    __slots__ = ("_store",)

    def __init__(self, *, task_store: TaskStorePort) -> None:
        self._store = task_store

    async def cancellation_for_graph(
        self,
        admitted: AdmittedExecution,
    ) -> Any:
        """按冻结图修订查询当前取消记录，保留 Store 对不存在状态的语义。"""
        return await _maybe_await(
            self._store.get_graph_cancellation_for_graph(
                admitted.graph_id,
                admitted.graph_revision,
            )
        )

    async def graph(
        self,
        admitted: AdmittedExecution,
        *,
        field_name: str,
    ) -> dict[str, Any]:
        """读取并规范化图记录，供后续身份与终态判断使用。"""
        return _mapping(
            await _maybe_await(
                self._store.get_graph(
                    admitted.graph_id,
                    admitted.graph_revision,
                )
            ),
            field_name=field_name,
        )

    async def nodes(
        self,
        admitted: AdmittedExecution,
        *,
        graph: Mapping[str, Any],
    ) -> dict[str, dict[str, Any]]:
        """读取所有已准入节点并逐个核验它们仍属于同一冻结图。"""
        nodes: dict[str, dict[str, Any]] = {}
        for binding in admitted.nodes:
            node_admission = admitted.for_node(binding.node_id)
            node = _mapping(
                await _maybe_await(
                    self._store.get_node(
                        admitted.graph_id,
                        admitted.graph_revision,
                        binding.node_id,
                    )
                ),
                field_name=f"cancellation TaskNode {binding.node_id}",
            )
            _assert_durable_identity(node_admission, graph=graph, node=node)
            nodes[binding.node_id] = node
        return nodes

    async def budget_record(
        self,
        admitted: AdmittedExecution,
    ) -> dict[str, Any] | None:
        """从调度快照取得唯一共享图预算；多个预算代表不可安全解释的冲突。"""
        snapshot = _mapping(
            await _maybe_await(
                self._store.read_scheduler_snapshot(
                    admitted.graph_id,
                    admitted.graph_revision,
                )
            ),
            field_name="cancellation scheduler snapshot",
        )
        raw_budgets = snapshot.get("budgets")
        if not isinstance(raw_budgets, list):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "cancellation scheduler snapshot has no Budget list",
            )
        budgets = [dict(item) for item in raw_budgets if isinstance(item, Mapping)]
        if len(budgets) > 1:
            raise GovernedRuntimeError(
                GOVERNED_BUDGET_STATE_CONFLICT,
                "TaskGraph cancellation found multiple shared Budgets",
            )
        if not budgets:
            return None
        budget = budgets[0]
        if (
            _text(budget.get("graph_id")) != admitted.graph_id
            or int(budget.get("graph_revision") or 0) != admitted.graph_revision
            or budget.get("node_id") is not None
            or budget.get("attempt_id") is not None
        ):
            raise GovernedRuntimeError(
                GOVERNED_BUDGET_STATE_CONFLICT,
                "cancellation Budget is not bound to the admitted Graph revision",
            )
        return budget

    async def decision_for_intent(
        self,
        admitted: AdmittedExecution,
        *,
        intent_ref: str,
    ) -> dict[str, Any]:
        """读取并校验某取消意图对应的最终决定及其图修订身份。"""
        raw = await _maybe_await(
            self._store.get_graph_cancellation_decision_for_intent(intent_ref)
        )
        if not isinstance(raw, Mapping):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "finalized cancellation intent has no canonical decision",
            )
        record = dict(raw)
        try:
            decision = normalize_graph_cancellation_decision(
                _mapping(
                    record.get("payload"),
                    field_name="cancellation decision payload",
                )
            )
        except (GraphCancellationContractError, GovernedRuntimeError) as exc:
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                f"durable graph cancellation decision is invalid: {exc}",
            ) from exc
        if (
            _text(record.get("decision_ref")) != decision.decision_ref
            or decision.payload["intent_ref"] != intent_ref
            or decision.payload["graph_id"] != admitted.graph_id
            or decision.payload["graph_revision"] != admitted.graph_revision
        ):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "durable graph cancellation decision identity drifted",
            )
        return record

    async def terminal_event(
        self,
        *,
        admitted: AdmittedExecution,
        intent_record: Mapping[str, Any],
        decision_record: Mapping[str, Any] | None,
        local_pending: Sequence[str] = (),
        replayed: bool,
    ) -> dict[str, Any]:
        """将请求中或已完成的取消状态投影为拒绝终态事件。

        ``local_pending`` 仅是本进程停止加速的诊断信息，不能影响持久化取消的
        FINALIZED 判定或回执中的权威状态。
        """
        intent = _validated_graph_cancellation_intent(admitted, intent_record)
        finalized = decision_record is not None
        decision_ref = None
        payload_digest = _text(intent_record.get("payload_sha256")) or None
        if decision_record is not None:
            decision = normalize_graph_cancellation_decision(
                _mapping(
                    decision_record.get("payload"),
                    field_name="terminal cancellation decision payload",
                )
            )
            decision_ref = decision.decision_ref
            payload_digest = _text(decision_record.get("payload_sha256")) or None
        # 回执选结果节点（或稳定末节点）承载图级取消，保持回执 schema 的节点锚点。
        selected = admitted.for_node(
            admitted.result_node_id or admitted.nodes[-1].node_id
        )
        attempts = await self._attempts_for_node(selected)
        latest = attempts[-1] if attempts else None
        attempt_id = (
            _required_record_text(latest, "attempt_id")
            if latest is not None
            else _attempt_id(selected, attempt_no=1)
        )
        budget = await self.budget_record(admitted)
        projection: dict[str, Any] = {
            "graph_cancellation": {
                "status": "FINALIZED" if finalized else "REQUESTED",
                "graph_status": "CANCELLED" if finalized else "BLOCKED",
                "intent_ref": intent.intent_ref,
                "decision_ref": decision_ref,
                "request_id": intent.payload["request_id"],
                "request_identity": intent.payload["request_identity"],
                "source": intent.payload["source"],
                "reason_code": intent.payload["reason_code"],
                "local_pending_tasks": list(local_pending),
            },
            "budget": _cancellation_budget_projection(budget),
        }
        evidence_refs = tuple(
            item
            for item in (
                intent.intent_ref,
                decision_ref,
                intent.payload["request_identity"],
            )
            if item
        )
        receipt = build_execution_receipt(
            selected,
            attempt_id=attempt_id,
            receipt_ref=decision_ref or intent.intent_ref,
            payload_digest=payload_digest,
            status="CANCELLED" if finalized else "CANCEL_REQUESTED",
            verification_status="NOT_RUN",
            accepted=False,
            replayed=bool(replayed),
            reason_code=(
                GOVERNED_GRAPH_CANCELLED
                if finalized
                else GOVERNED_GRAPH_CANCELLATION_PENDING
            ),
            evidence_refs=evidence_refs,
            projection=projection,
        )
        return terminal_event(receipt)

    async def _attempts_for_node(
        self,
        admitted: AdmittedExecution,
    ) -> list[dict[str, Any]]:
        """以稳定 Attempt 顺序读取选定节点的历史，供回执绑定最新执行身份。"""
        snapshot = await _maybe_await(
            self._store.read_scheduler_snapshot(
                admitted.graph_id,
                admitted.graph_revision,
            )
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
            if isinstance(item, Mapping)
            and _text(item.get("node_id")) == admitted.node_id
        ]
        rows.sort(
            key=lambda item: (
                int(item.get("attempt_no") or 0),
                _text(item.get("attempt_id")),
            )
        )
        return rows




__all__ = ["GraphCancellationProjection"]
