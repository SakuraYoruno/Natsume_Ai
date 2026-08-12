"""Immutable DAG bindings produced by one atomic governed admission."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from natsume_contracts.task.v1 import ExecutorAssignment, HostCommand, TaskExecutor

from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraphCompletionPolicy,
    validate_task_graph_admission_state,
)

from .errors import (
    GOVERNED_ADMISSION_INVALID,
    GOVERNED_ASSIGNMENT_CONFLICT,
    GovernedRuntimeError,
)


# 这是准入后冻结的节点执行绑定；运行阶段不得再替换 worker 路由或业务指纹。
@dataclass(frozen=True, slots=True)
class AdmittedTaskNode:
    """Immutable per-node execution binding inside one admitted TaskGraph."""

    node_id: str
    worker_route: str
    dependencies: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    operation_fingerprint: str
    reservation_id: str
    risk_level: int = 1

    def validate(self) -> None:
        if not all(
            _text(value)
            for value in (
                self.node_id,
                self.worker_route,
                self.operation_fingerprint,
                self.reservation_id,
            )
        ):
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "admitted TaskNode execution binding is incomplete",
            )
        if self.node_id in self.dependencies or len(set(self.dependencies)) != len(
            self.dependencies
        ):
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                f"admitted TaskNode dependencies are invalid: {self.node_id}",
            )
        if (
            isinstance(self.risk_level, bool)
            or not isinstance(self.risk_level, int)
            or not 1 <= self.risk_level <= 5
        ):
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                f"admitted TaskNode risk level is invalid: {self.node_id}",
            )


# 一次原子准入产生的 DAG 快照。它把命令、图版本、执行者和预约标识绑定为可重放事实。
@dataclass(frozen=True, slots=True)
class AdmittedExecution:
    """Normalized, immutable view of one atomically admitted DAG command."""

    command_id: str
    work_order_id: str
    assignment_id: str
    executor: str
    graph_id: str
    graph_revision: int
    nodes: tuple[AdmittedTaskNode, ...]
    business_scope: str
    replayed: bool = False
    active_node_id: str | None = None
    result_node_id: str | None = None
    completion_policy: str | None = None

    @property
    def active_node(self) -> AdmittedTaskNode:
        node_id = self.active_node_id or self.nodes[0].node_id
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        raise GovernedRuntimeError(
            GOVERNED_ADMISSION_INVALID,
            f"active TaskNode is not part of the admitted graph: {node_id}",
        )

    @property
    def node_id(self) -> str:
        return self.active_node.node_id

    @property
    def worker_route(self) -> str:
        return self.active_node.worker_route

    @property
    def dependencies(self) -> tuple[str, ...]:
        return self.active_node.dependencies

    @property
    def operation_fingerprint(self) -> str:
        return self.active_node.operation_fingerprint

    @property
    def reservation_id(self) -> str:
        return self.active_node.reservation_id

    def for_node(self, node_id: str) -> "AdmittedExecution":
        # 派生视图只切换当前节点，保留同一图版本和全量绑定，避免子节点逃离原准入范围。
        normalized = _text(node_id)
        if normalized not in {node.node_id for node in self.nodes}:
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                f"TaskNode is not part of the admitted graph: {normalized}",
            )
        return AdmittedExecution(
            command_id=self.command_id,
            work_order_id=self.work_order_id,
            assignment_id=self.assignment_id,
            executor=self.executor,
            graph_id=self.graph_id,
            graph_revision=self.graph_revision,
            nodes=self.nodes,
            business_scope=self.business_scope,
            result_node_id=self.result_node_id,
            completion_policy=self.completion_policy,
            replayed=self.replayed,
            active_node_id=normalized,
        )

    @classmethod
    def from_contracts(
        cls,
        *,
        command: HostCommand,
        assignment: ExecutorAssignment,
        graph: Any,
        replayed: bool = False,
    ) -> "AdmittedExecution":
        # 先验证契约，再把外部可变图投影为不可变运行时绑定；准入失败不得留下半份执行计划。
        if not isinstance(command, HostCommand):
            raise GovernedRuntimeError(GOVERNED_ADMISSION_INVALID, "command must be HostCommand")
        if not isinstance(assignment, ExecutorAssignment):
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "assignment must be ExecutorAssignment",
            )
        graph_id = _text(getattr(graph, "graph_id", None))
        graph_revision = _exact_positive_int(getattr(graph, "revision", None), "graph.revision")
        graph_root = _text(getattr(graph, "root_work_order_id", None))
        raw_result_node_id = getattr(graph, "result_node_id", None)
        result_node_id = (
            _text(raw_result_node_id) if raw_result_node_id is not None else None
        )
        raw_completion_policy = getattr(graph, "completion_policy", None)
        completion_policy = (
            _text(getattr(raw_completion_policy, "value", raw_completion_policy))
            if raw_completion_policy is not None
            else None
        )
        try:
            validate_task_graph_admission_state(graph)
        except Exception as exc:
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                f"TaskGraph admission contract is invalid: {exc}",
            ) from exc
        raw_nodes = tuple(getattr(graph, "nodes", ()) or ())
        ordered_nodes = _topological_nodes(raw_nodes)
        if not graph_id:
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "graph identity is required",
            )
        # 命令、图和根工单必须是同一修订身份，否则可能把旧计划用于新授权。
        if (
            graph_id != command.graph_id
            or graph_revision != command.graph_revision
            or graph_root != command.root_work_order_id
        ):
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "HostCommand and TaskGraph revision identity differ",
            )
        if assignment.work_order_id != command.root_work_order_id:
            raise GovernedRuntimeError(
                GOVERNED_ASSIGNMENT_CONFLICT,
                "ExecutorAssignment belongs to a different WorkOrder",
            )
        executor = _enum_text(assignment.executor)
        if executor != TaskExecutor.CANONICAL.value:
            raise GovernedRuntimeError(GOVERNED_ASSIGNMENT_CONFLICT, "unsupported executor")
        bindings: list[AdmittedTaskNode] = []
        multiple_nodes = len(ordered_nodes) > 1
        # 稳定拓扑顺序使依赖先执行；多节点图另派生指纹和预约，避免节点间错误共享副作用资格。
        for node in ordered_nodes:
            node_id = _text(getattr(node, "node_id", None))
            worker_route = _text(getattr(node, "route", None))
            dependencies = tuple(
                _text(item)
                for item in (getattr(node, "dependencies", ()) or ())
                if _text(item)
            )
            acceptance_criteria = tuple(
                _text(item)
                for item in (getattr(node, "acceptance_criteria", ()) or ())
                if _text(item)
            )
            if not node_id or not worker_route:
                raise GovernedRuntimeError(
                    GOVERNED_ADMISSION_INVALID,
                    "node identity and frozen worker route are required",
                )
            operation_fingerprint = derive_node_operation_fingerprint(
                command.operation_fingerprint,
                node_id=node_id,
                multiple_nodes=multiple_nodes,
            )
            reservation_seed = [
                command.command_id,
                assignment.assignment_id,
                command.business_scope,
                operation_fingerprint,
            ]
            if multiple_nodes:
                reservation_seed.append(node_id)
            reservation_id = "reservation::governed::" + hashlib.sha256(
                "\n".join(reservation_seed).encode("utf-8")
            ).hexdigest()
            bindings.append(
                AdmittedTaskNode(
                    node_id=node_id,
                    worker_route=worker_route,
                    dependencies=dependencies,
                    acceptance_criteria=acceptance_criteria,
                    operation_fingerprint=operation_fingerprint,
                    reservation_id=reservation_id,
                    risk_level=_exact_positive_int(
                        getattr(node, "risk_level", None),
                        f"TaskNode[{node_id}].risk_level",
                    ),
                )
            )
        admitted = cls(
            command_id=command.command_id,
            work_order_id=command.root_work_order_id,
            assignment_id=assignment.assignment_id,
            executor=executor,
            graph_id=graph_id,
            graph_revision=graph_revision,
            nodes=tuple(bindings),
            business_scope=command.business_scope,
            result_node_id=result_node_id,
            completion_policy=completion_policy,
            replayed=bool(replayed),
        )
        admitted.validate()
        return admitted

    def validate(self) -> None:
        # 每次进入运行时重验冻结边界，及早将损坏或跨图的恢复数据隔离为准入错误。
        required = (
            self.command_id,
            self.work_order_id,
            self.assignment_id,
            self.executor,
            self.graph_id,
            self.business_scope,
        )
        if not all(_text(item) for item in required) or self.graph_revision < 1 or not self.nodes:
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "admitted execution identity is incomplete",
            )
        if self.executor != TaskExecutor.CANONICAL.value:
            raise GovernedRuntimeError(GOVERNED_ASSIGNMENT_CONFLICT, "executor is not frozen")
        node_ids = {node.node_id for node in self.nodes}
        if len(node_ids) != len(self.nodes):
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "admitted TaskNode identities must be unique",
            )
        for node in self.nodes:
            node.validate()
            missing = sorted(set(node.dependencies) - node_ids)
            if missing:
                raise GovernedRuntimeError(
                    GOVERNED_ADMISSION_INVALID,
                    f"admitted TaskNode {node.node_id} has unknown dependencies: {','.join(missing)}",
                )
        _topological_nodes(self.nodes)
        completion_policy: TaskGraphCompletionPolicy | None = None
        if self.completion_policy is not None:
            try:
                completion_policy = TaskGraphCompletionPolicy(self.completion_policy)
            except (TypeError, ValueError) as exc:
                raise GovernedRuntimeError(
                    GOVERNED_ADMISSION_INVALID,
                    "admitted completion_policy is unsupported",
                ) from exc
        if self.result_node_id is not None:
            result_node_id = _text(self.result_node_id)
            if not result_node_id or result_node_id not in node_ids:
                raise GovernedRuntimeError(
                    GOVERNED_ADMISSION_INVALID,
                    "admitted result_node_id is outside the TaskGraph",
                )
            dependents = sorted(
                node.node_id
                for node in self.nodes
                if result_node_id in node.dependencies
            )
            if dependents:
                raise GovernedRuntimeError(
                    GOVERNED_ADMISSION_INVALID,
                    "admitted result_node_id is not a sink; dependents: "
                    + ",".join(dependents),
                )
            depended_on = {
                dependency
                for node in self.nodes
                for dependency in node.dependencies
            }
            sink_node_ids = {
                node.node_id for node in self.nodes if node.node_id not in depended_on
            }
            if (
                completion_policy
                is not TaskGraphCompletionPolicy.ATOMIC_GRAPH_SUCCESS_V1
                and len(sink_node_ids) > 1
            ):
                raise GovernedRuntimeError(
                    GOVERNED_ADMISSION_INVALID,
                    "legacy admitted Graph cannot bind one result across multiple sinks",
                )
        if self.completion_policy is not None:
            if (
                completion_policy
                is TaskGraphCompletionPolicy.ATOMIC_GRAPH_SUCCESS_V1
                and self.result_node_id is None
            ):
                raise GovernedRuntimeError(
                    GOVERNED_ADMISSION_INVALID,
                    "atomic Graph success requires an admitted result authority",
                )
        if self.active_node_id is not None and self.active_node_id not in node_ids:
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "active TaskNode is outside the admitted TaskGraph",
            )


def _topological_nodes(nodes: Sequence[Any]) -> tuple[Any, ...]:
    """Return a stable dependency-first ordering for an already validated DAG."""

    ordered_input = tuple(nodes)
    dependencies = {
        _text(getattr(node, "node_id", None)): {
            _text(item)
            for item in (getattr(node, "dependencies", ()) or ())
            if _text(item)
        }
        for node in ordered_input
    }
    emitted: set[str] = set()
    result: list[Any] = []
    # 没有可发射节点代表环或缺失依赖；不能猜测顺序，否则会破坏 DAG 的因果约束。
    while len(result) < len(ordered_input):
        ready = [
            node
            for node in ordered_input
            if _text(getattr(node, "node_id", None)) not in emitted
            and dependencies[_text(getattr(node, "node_id", None))] <= emitted
        ]
        if not ready:
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "TaskGraph dependency topology is cyclic or unresolved",
            )
        for node in ready:
            node_id = _text(getattr(node, "node_id", None))
            emitted.add(node_id)
            result.append(node)
    return tuple(result)


def derive_node_operation_fingerprint(
    command_fingerprint: str,
    *,
    node_id: str,
    multiple_nodes: bool,
) -> str:
    # 单节点保持命令指纹以兼容既有幂等语义，多节点必须纳入 node_id 防止不同副作用相互重放。
    base = _text(command_fingerprint)
    if not multiple_nodes:
        return base
    digest = hashlib.sha256(f"{base}\n{node_id}".encode("utf-8")).hexdigest()
    return f"sha256:{digest}" if base.startswith("sha256:") else digest


_node_operation_fingerprint = derive_node_operation_fingerprint


def _text(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip()


def _enum_text(value: Any) -> str:
    return _text(value).lower()


def _exact_positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise GovernedRuntimeError(
            GOVERNED_ADMISSION_INVALID,
            f"{field_name} must be an integer >= 1",
        )
    return value


__all__ = [
    "AdmittedExecution",
    "AdmittedTaskNode",
    "derive_node_operation_fingerprint",
]
