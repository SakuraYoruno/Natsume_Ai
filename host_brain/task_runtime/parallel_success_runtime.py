"""0091 原子整图成功的编排边界。"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Mapping
from typing import Any

from natsume_contracts.task.v1 import HostCommand
from host_brain.contracts.agent_runtime.task_graph import TaskGraphCompletionPolicy
from host_brain.contracts.agent_runtime.parallel_success import (
    ParallelSuccessContractError,
    normalize_parallel_success_decision,
)
from host_brain.persistence.worker_projection_repository import (
    WorkerProjectionStoreError,
    validate_worker_projection_payload,
)

from .acceptance_effects import compile_graph_acceptance_effects
from .admission import AdmittedExecution
from .budget_runtime import BudgetRuntimeContext, DurableBudgetRuntime
from .coordinator_receipts import approved_commit_event, build_execution_receipt
from .coordinator_records import (
    _exception_reason,
    _mapping,
    _record_evidence_refs,
    _required_record_text,
    _status,
    _text,
    _version,
)
from .errors import (
    GOVERNED_DURABLE_STATE_CONFLICT,
    GOVERNED_EXECUTION_IN_PROGRESS,
    GovernedRuntimeError,
)
from .node_execution_projection import NodeExecutionProjection
from .plan_workspace_acceptance_effect import (
    compile_plan_node_acceptance_effects,
)
from .parallel_success_records import _projection_route_authority
from .ports import ExecutionProjectionReaderPort, TaskStorePort
from .runtime_outbox import RuntimeOutbox


_logger = logging.getLogger("Natsume.task_runtime.parallel_success")
_TASK_STORE_OWNERSHIP_CONFLICT = "TASK_STORE_OWNERSHIP_CONFLICT"


class ParallelSuccessRuntime:
    """提交或精确重放规范的 0091 并行成功决策。

    它把所有节点验收、结果节点投影、效果意图、图完成和预算消耗收束为同一耐久
    决策；网络响应丢失时只回读并重放该决策，绝不重新编译或重复执行效果。
    """

    __slots__ = (
        "_store",
        "_runtime_outbox",
        "_budgets",
        "_projection_reader",
        "_node_execution_projection",
    )

    def __init__(
        self,
        *,
        task_store: TaskStorePort,
        runtime_outbox: RuntimeOutbox,
        budget_runtime: DurableBudgetRuntime,
        projection_reader: ExecutionProjectionReaderPort | None,
        node_execution_projection: NodeExecutionProjection,
    ) -> None:
        self._store = task_store
        self._runtime_outbox = runtime_outbox
        self._budgets = budget_runtime
        self._projection_reader = projection_reader
        self._node_execution_projection = node_execution_projection

    async def complete(
        self,
        *,
        admitted: AdmittedExecution,
        command: HostCommand,
        work_order: Any,
        budget: BudgetRuntimeContext,
        node_grant_refs: Mapping[str, str],
    ) -> tuple[BudgetRuntimeContext, dict[str, Any]]:
        """提交所有节点已验收后的 B5 外层成功边界。

        返回的公开投影只源自结果节点，但其可见性以全部节点的精确状态、回执和
        预算版本为前提。这样 Plan 的中间写入效果与最终回复可在同一图终态后被
        耐久队列领取，而不会出现部分成功的对外承诺。
        """

        result_node_id = _text(admitted.result_node_id)
        if not result_node_id or self._projection_reader is None:
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "parallel success requires an explicit result node and projection reader",
            )
        result_admission = admitted.for_node(result_node_id)
        graph = _mapping(
            await _maybe_await(
                self._store.get_graph(admitted.graph_id, admitted.graph_revision)
            ),
            field_name="parallel success graph",
        )
        durable_nodes = {
            binding.node_id: _mapping(
                await _maybe_await(
                    self._store.get_node(
                        admitted.graph_id,
                        admitted.graph_revision,
                        binding.node_id,
                    )
                ),
                field_name=f"parallel success node {binding.node_id}",
            )
            for binding in admitted.nodes
        }
        if any(_status(node) != "ACCEPTED" for node in durable_nodes.values()):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "parallel success requires every durable TaskNode to be ACCEPTED",
            )
        expected_node_states = {
            node_id: {
                "status": _status(node),
                "status_version": _version(node),
            }
            for node_id, node in sorted(durable_nodes.items())
        }
        snapshot = _mapping(
            await _maybe_await(
                self._store.read_scheduler_snapshot(
                    admitted.graph_id,
                    admitted.graph_revision,
                )
            ),
            field_name="parallel success scheduler snapshot",
        )
        raw_attempts = snapshot.get("attempts")
        if not isinstance(raw_attempts, list):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "parallel success snapshot has no durable Attempt journal",
            )
        # 只使用每个节点 attempt_no 最大的记录，防止较早成功尝试掩盖后来重试
        # 或恢复后的终态；同号时以稳定 attempt_id 解除歧义。
        latest_attempt_by_node: dict[str, dict[str, Any]] = {}
        for raw_attempt in raw_attempts:
            if not isinstance(raw_attempt, Mapping):
                continue
            attempt = dict(raw_attempt)
            node_id = _text(attempt.get("node_id"))
            if not node_id:
                continue
            current = latest_attempt_by_node.get(node_id)
            key = (
                int(attempt.get("attempt_no") or 0),
                _text(attempt.get("attempt_id")),
            )
            current_key = (
                int((current or {}).get("attempt_no") or 0),
                _text((current or {}).get("attempt_id")),
            )
            if current is None or key > current_key:
                latest_attempt_by_node[node_id] = attempt

        accepted_outcomes: list[dict[str, Any]] = []
        projections: dict[str, dict[str, Any]] = {}
        receipt_refs: dict[str, str] = {}
        graph_status = _status(graph)
        # 每个已接受节点都必须回读不可变 worker projection，并重新绑定路由、
        # target 与 grant；外部传入的 node_grant_refs 不是已完成结果的权威来源。
        for binding in admitted.nodes:
            attempt = latest_attempt_by_node.get(binding.node_id)
            if attempt is None or _status(attempt) != "SUCCEEDED":
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    f"parallel success node lacks latest SUCCEEDED Attempt: {binding.node_id}",
                )
            attempt_id = _required_record_text(attempt, "attempt_id")
            reservation = await self._node_execution_projection.try_operation(
                admitted.for_node(binding.node_id).reservation_id
            )
            receipt_ref = _text((reservation or {}).get("receipt_ref"))
            if _status(reservation or {}) != "COMMITTED" or not receipt_ref:
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    f"parallel success node lacks committed operation receipt: {binding.node_id}",
                )
            loaded = await _maybe_await(self._projection_reader.load(receipt_ref))
            if not isinstance(loaded, Mapping):
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    f"parallel success node lacks durable worker projection: {binding.node_id}",
                )
            try:
                projection = validate_worker_projection_payload(
                    loaded,
                    expected_projection_id=receipt_ref,
                )
            except WorkerProjectionStoreError as exc:
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    f"parallel success projection is not canonical: {binding.node_id}",
                ) from exc
            expected_route, expected_target = _projection_route_authority(
                binding=binding,
                work_order=work_order,
                command=command,
            )
            # Review 续跑会使用一次性更新后的子 grant。图已 COMPLETED 时，精确
            # 重放必须重开投影中冻结的授权，而不能再与准入阶段的旧 grant 批次
            # 比较，否则一次合法审查恢复会被误判为授权漂移。
            expected_node_grant_ref = (
                _text(projection.get("node_grant_ref"))
                if graph_status == "COMPLETED"
                else _text(node_grant_refs.get(binding.node_id))
            )
            payload_digest = _text(projection.get("payload_digest"))
            expected_projection_authority = {
                "command_id": admitted.command_id,
                "request_id": command.request_id,
                "graph_id": admitted.graph_id,
                "graph_revision": admitted.graph_revision,
                "node_id": binding.node_id,
                "attempt_id": attempt_id,
                "route_type": expected_route,
                "target_id": expected_target,
                "node_grant_ref": expected_node_grant_ref,
                "decision": "APPROVE",
            }
            actual_projection_authority = {
                "command_id": _text(projection.get("command_id")),
                "request_id": _text(projection.get("request_id")),
                "graph_id": _text(projection.get("graph_id")),
                "graph_revision": int(projection.get("graph_revision") or 0),
                "node_id": _text(projection.get("node_id")),
                "attempt_id": _text(projection.get("attempt_id")),
                "route_type": _text(projection.get("route_type")).lower(),
                "target_id": _text(projection.get("target_id")),
                "node_grant_ref": _text(projection.get("node_grant_ref")),
                "decision": _text(projection.get("decision")).upper(),
            }
            drifted_fields = sorted(
                key
                for key, expected in expected_projection_authority.items()
                if actual_projection_authority.get(key) != expected
            )
            if not expected_node_grant_ref or drifted_fields:
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    "parallel success projection authority drifted: "
                    f"{binding.node_id}; fields={','.join(drifted_fields)}",
                )
            projections[binding.node_id] = projection
            receipt_refs[binding.node_id] = receipt_ref
            accepted_outcomes.append(
                {
                    "node_id": binding.node_id,
                    "attempt_id": attempt_id,
                    "receipt_ref": receipt_ref,
                    "payload_sha256": payload_digest,
                }
            )

        result_attempt_id = _required_record_text(
            latest_attempt_by_node[result_node_id],
            "attempt_id",
        )
        result_receipt_ref = receipt_refs[result_node_id]
        result_projection = projections[result_node_id]
        budget = await self._budgets.refresh(budget)
        if graph_status == "COMPLETED":
            # 已完成图绝不再次生成 effect 或改变 Budget；只能验证冻结决策与当前
            # 耐久事实完全一致后，重建同一份对外回执。
            raw_decision = await _maybe_await(
                self._store.get_latest_parallel_success_decision(
                    admitted.graph_id,
                    admitted.graph_revision,
                )
            )
            if not isinstance(raw_decision, Mapping):
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    "completed parallel success has no canonical decision",
                )
            try:
                decision = normalize_parallel_success_decision(
                    _mapping(
                        raw_decision.get("payload"),
                        field_name="parallel success replay payload",
                    )
                )
            except (ParallelSuccessContractError, TypeError) as exc:
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    f"parallel success replay decision is invalid: {exc}",
                ) from exc
            frozen_outcomes = sorted(
                accepted_outcomes,
                key=lambda item: item["node_id"],
            )
            if (
                decision.payload["completion_policy"]
                != TaskGraphCompletionPolicy.ATOMIC_GRAPH_SUCCESS_V1.value
                or decision.payload["result_node_id"] != result_node_id
                or decision.payload["result_attempt_id"] != result_attempt_id
                or decision.payload["result_receipt_ref"] != result_receipt_ref
                or decision.payload["node_states"] != expected_node_states
                or decision.payload["accepted_outcomes"] != frozen_outcomes
                or decision.payload["budget_id"] != budget.budget_id
                or budget.status != "CONSUMED"
                or budget.version
                != int(decision.payload["budget_status_version_before"]) + 1
            ):
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    "completed parallel success differs from its frozen authority",
                )
            evidence_refs = tuple(
                dict.fromkeys(
                    (
                        decision.decision_ref,
                        result_receipt_ref,
                        *(
                            str(effect["effect_id"])
                            for effect in decision.payload["effect_intents"]
                        ),
                    )
                )
            )
            await self._runtime_outbox.publish_pending()
            public_projection = dict(result_projection)
            public_projection["budget"] = budget.execution_payload()
            public_projection["parallel_success"] = {
                "graph_status": "COMPLETED",
                "decision_ref": decision.decision_ref,
                "result_node_id": result_node_id,
            }
            receipt = build_execution_receipt(
                result_admission,
                attempt_id=result_attempt_id,
                receipt_ref=result_receipt_ref,
                payload_digest=_text(result_projection.get("payload_digest")) or None,
                status="ACCEPTED",
                verification_status="PASS",
                accepted=True,
                replayed=True,
                evidence_refs=tuple(
                    dict.fromkeys(
                        (
                            *_record_evidence_refs(durable_nodes[result_node_id]),
                            *evidence_refs,
                        )
                    )
                ),
                projection=public_projection,
            )
            return budget, approved_commit_event(public_projection, receipt)

        if graph_status != "ACTIVE" or budget.status != "ACTIVE":
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "all-accepted Graph/Budget are not in a committable state",
            )
        try:
            # 计划中间节点仅在整图成功后才可晋升工作区产物；结果节点负责通用
            # 图效果，二者均从已提交 projection 而非内存 worker 输出编译。
            effect_intents = []
            if command.route_type == "plan":
                for binding in admitted.nodes:
                    if binding.node_id == result_node_id:
                        continue
                    node_attempt_id = _required_record_text(
                        latest_attempt_by_node[binding.node_id],
                        "attempt_id",
                    )
                    effect_intents.extend(
                        compile_plan_node_acceptance_effects(
                            command=command,
                            work_order=work_order,
                            node_id=binding.node_id,
                            attempt_id=node_attempt_id,
                            worker_commit={
                                **projections[binding.node_id],
                                "receipt_ref": receipt_refs[binding.node_id],
                            },
                        )
                    )
            effect_intents.extend(compile_graph_acceptance_effects(
                command=command,
                work_order=work_order,
                node_id=result_node_id,
                attempt_id=result_attempt_id,
                worker_commit={
                    **result_projection,
                    "receipt_ref": result_receipt_ref,
                },
            ))
            effect_intents = tuple(effect_intents)
        except Exception as exc:
            raise GovernedRuntimeError(
                _exception_reason(exc, "ACCEPTANCE_EFFECT_DESCRIPTOR_INVALID"),
                "parallel success effects could not be compiled from the durable result projection",
            ) from exc
        effect_summaries = [
            {
                "effect_id": effect.effect_id,
                "node_id": effect.node_id,
                "attempt_id": effect.attempt_id,
                "commit_scope": str(getattr(effect.commit_scope, "value", effect.commit_scope)),
                "effect_type": effect.effect_type,
                "payload_ref": effect.payload_ref,
                "payload_sha256": effect.payload_sha256,
                "idempotency_key": effect.idempotency_key,
            }
            for effect in effect_intents
        ]
        if graph_status == "ACTIVE" and budget.status == "ACTIVE":
            graph_version = _version(graph)
            budget_version = budget.version
            graph_outbox_seed = self._runtime_outbox.build(
                command_id=admitted.command_id,
                aggregate_type="TASK_GRAPH",
                aggregate_id=admitted.graph_id,
                graph_revision=admitted.graph_revision,
                aggregate_version=graph_version + 1,
                event_type="TASK_GRAPH_COMPLETED",
            )
            budget_outbox_seed = self._runtime_outbox.build(
                command_id=admitted.command_id,
                aggregate_type="BUDGET_RESERVATION",
                aggregate_id=budget.budget_id,
                aggregate_version=budget_version + 1,
                event_type="BUDGET_RESERVATION_CONSUMED",
            )
            try:
                decision = normalize_parallel_success_decision(
                    {
                        "schema_version": "Natsume.task_runtime.parallel_success.v1",
                        "graph_id": admitted.graph_id,
                        "graph_revision": admitted.graph_revision,
                        "graph_status_before": "ACTIVE",
                        "graph_status_version_before": graph_version,
                        "graph_target_status": "COMPLETED",
                        "completion_policy": (
                            TaskGraphCompletionPolicy.ATOMIC_GRAPH_SUCCESS_V1.value
                        ),
                        "budget_id": budget.budget_id,
                        "budget_status_before": "ACTIVE",
                        "budget_status_version_before": budget_version,
                        "budget_target_status": "CONSUMED",
                        "result_node_id": result_node_id,
                        "result_attempt_id": result_attempt_id,
                        "result_receipt_ref": result_receipt_ref,
                        "node_states": expected_node_states,
                        "accepted_outcomes": accepted_outcomes,
                        "effect_intents": effect_summaries,
                        "graph_outbox_id": graph_outbox_seed.outbox_id,
                        "budget_outbox_id": budget_outbox_seed.outbox_id,
                    }
                )
            except ParallelSuccessContractError as exc:
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    f"parallel success decision is invalid: {exc}",
                ) from exc
        evidence_refs = tuple(
            dict.fromkeys(
                (
                    decision.decision_ref,
                    result_receipt_ref,
                    *(effect.effect_id for effect in effect_intents),
                )
            )
        )
        graph_outbox = self._runtime_outbox.build(
            command_id=admitted.command_id,
            aggregate_type="TASK_GRAPH",
            aggregate_id=admitted.graph_id,
            graph_revision=admitted.graph_revision,
            aggregate_version=graph_version + 1,
            event_type="TASK_GRAPH_COMPLETED",
            evidence_refs=evidence_refs,
        )
        budget_outbox = self._runtime_outbox.build(
            command_id=admitted.command_id,
            aggregate_type="BUDGET_RESERVATION",
            aggregate_id=budget.budget_id,
            aggregate_version=budget_version + 1,
            event_type="BUDGET_RESERVATION_CONSUMED",
            evidence_refs=evidence_refs,
        )
        arguments = {
            "budget_id": budget.budget_id,
            "expected_graph_version": graph_version,
            "expected_budget_version": budget_version,
            "expected_node_states": expected_node_states,
            "decision_ref": decision.decision_ref,
            "decision_payload": decision.payload,
            "graph_outbox": graph_outbox,
            "budget_outbox": budget_outbox,
            "effect_intents": effect_intents,
        }
        try:
            settled_raw = await _maybe_await(
                self._store.complete_parallel_graph_success(
                    admitted.graph_id,
                    admitted.graph_revision,
                    **arguments,
                )
            )
        except Exception as first_exc:
            try:
                settled_raw = await _maybe_await(
                    self._store.complete_parallel_graph_success(
                        admitted.graph_id,
                        admitted.graph_revision,
                        **arguments,
                    )
                )
            except Exception as replay_exc:
                reason = _exception_reason(replay_exc, "")
                try:
                    latest_graph = await _maybe_await(
                        self._store.get_graph(
                            admitted.graph_id,
                            admitted.graph_revision,
                        )
                    )
                except Exception:
                    latest_graph = None
                if isinstance(latest_graph, Mapping) and _status(latest_graph) == "COMPLETED":
                    # 原子事务之后两次响应都可能丢失。通过已完成分支回读冻结的
                    # 0091 授权，不重新编译 effects，避免把已领取的效果重复入队。
                    return await self.complete(
                        admitted=admitted,
                        command=command,
                        work_order=work_order,
                        budget=budget,
                        node_grant_refs=node_grant_refs,
                    )
                if reason == _TASK_STORE_OWNERSHIP_CONFLICT:
                    raise GovernedRuntimeError(
                        GOVERNED_EXECUTION_IN_PROGRESS,
                        "parallel success still has an active durable owner",
                    ) from replay_exc
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    "parallel success could not be committed or exactly replayed",
                ) from replay_exc
            _logger.warning(
                "Replayed committed parallel success",
                extra={
                    "graph_id": admitted.graph_id,
                    "graph_revision": admitted.graph_revision,
                    "decision_ref": decision.decision_ref,
                    "first_exception_type": type(first_exc).__name__,
                },
            )
        settled = _mapping(settled_raw, field_name="parallel success settlement")
        settled_graph = _mapping(settled.get("graph"), field_name="parallel success graph")
        settled_budget = _mapping(settled.get("budget"), field_name="parallel success budget")
        settled_decision = _mapping(
            settled.get("decision"),
            field_name="parallel success decision",
        )
        if (
            _status(settled_graph) != "COMPLETED"
            or _status(settled_budget) != "CONSUMED"
            or _text(settled_decision.get("decision_ref")) != decision.decision_ref
            or settled_decision.get("payload") != decision.payload
        ):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "parallel success returned an invalid terminal authority",
            )
        budget = budget.refreshed(settled_budget)
        await self._runtime_outbox.publish_pending()
        public_projection = dict(result_projection)
        public_projection["budget"] = budget.execution_payload()
        public_projection["parallel_success"] = {
            "graph_status": "COMPLETED",
            "decision_ref": decision.decision_ref,
            "result_node_id": result_node_id,
        }
        receipt = build_execution_receipt(
            result_admission,
            attempt_id=result_attempt_id,
            receipt_ref=result_receipt_ref,
            payload_digest=_text(result_projection.get("payload_digest")) or None,
            status="ACCEPTED",
            verification_status="PASS",
            accepted=True,
            replayed=bool(settled.get("replayed")),
            evidence_refs=tuple(
                dict.fromkeys(
                    (
                        *_record_evidence_refs(durable_nodes[result_node_id]),
                        *evidence_refs,
                    )
                )
            ),
            projection=public_projection,
        )
        return budget, approved_commit_event(public_projection, receipt)


async def _maybe_await(value: Any) -> Any:
    """兼容同步/异步持久化端口；原子结算语义始终由 store 保持。"""
    if inspect.isawaitable(value):
        return await value
    return value


__all__ = ["ParallelSuccessRuntime"]
# End of module.
