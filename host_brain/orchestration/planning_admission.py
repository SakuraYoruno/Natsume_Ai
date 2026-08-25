"""Durable admission boundary for one compiled PlanningSearch graph revision.

Planning chooses a path, ``PlanningGraphCompiler`` freezes it, and this service
creates the matching HostCommand/ExecutorAssignment before handing all four
contracts to the atomic command repository. It never plans, executes, mutates
the compiled revision, or accepts legacy step-list payloads.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Mapping

from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.contracts.agent_runtime.task_graph import TaskGraph
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.orchestration.planning_graph_compiler import CompiledPlanningGraph
from natsume_contracts.task import (
    ExecutorAssignment,
    HostCommand,
    HostCommandSurface,
    build_host_command,
)


PLANNING_ADMISSION_INVALID = "PLANNING_ADMISSION_INVALID"
PLANNING_ADMISSION_AUTHORITY_MISMATCH = "PLANNING_ADMISSION_AUTHORITY_MISMATCH"
PLANNING_ADMISSION_DURABLE_DRIFT = "PLANNING_ADMISSION_DURABLE_DRIFT"


class PlanningAdmissionError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or PLANNING_ADMISSION_INVALID)
        self.detail = str(detail or "planning admission failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


# 此回执把编译计划、命令、工单、执行者与图修订固定为同一持久世代。
@dataclass(frozen=True, slots=True)
class PlanningAdmissionReceipt:
    command: HostCommand
    work_order: WorkOrderV3
    assignment: ExecutorAssignment
    graph: TaskGraph
    created: bool
    replayed: bool

    def validate(self) -> None:
        # 重验全部 lineage，防止持久读取将命令或工单悄然指向另一张图修订。
        self.work_order.validate()
        self.graph.validate()
        if (
            self.command.root_work_order_id != self.work_order.root_work_order_id
            or self.command.root_work_order_id != self.graph.root_work_order_id
            or self.command.graph_id != self.graph.graph_id
            or self.command.graph_revision != self.graph.revision
            or self.work_order.task_graph_id != self.graph.graph_id
            or self.work_order.graph_revision != self.graph.revision
            or self.assignment.work_order_id != self.graph.root_work_order_id
        ):
            raise PlanningAdmissionError(
                PLANNING_ADMISSION_DURABLE_DRIFT,
                "admitted command/work-order/assignment/graph lineage differs",
            )


class GovernedPlanningAdmissionService:
    """Admit only an immutable compiled graph through the canonical repository."""

    def __init__(
        self,
        *,
        command_repository: Any,
        runtime_router: CanonicalRuntimeRouter,
    ) -> None:
        if command_repository is None or not callable(getattr(command_repository, "admit", None)):
            raise ValueError("command_repository.admit is required")
        if not isinstance(runtime_router, CanonicalRuntimeRouter):
            raise ValueError("runtime_router must be CanonicalRuntimeRouter")
        self._commands = command_repository
        self._router = runtime_router

    async def admit(
        self,
        compiled: CompiledPlanningGraph,
        *,
        surface: HostCommandSurface,
        command_request_id: str,
        session_id: str,
        principal_id: str,
        input_ref: str,
        input_sha256: str,
        governor_grant_ref: str,
        target_id: str,
        ingress_claim_ref: str | None = None,
        ingress_audit_ref: str | None = None,
    ) -> PlanningAdmissionReceipt:
        # 只接纳编译后的不可变图；旧式步骤列表既缺少完整依赖拓扑，也缺少本次准入的冻结身份。
        if not isinstance(compiled, CompiledPlanningGraph):
            raise PlanningAdmissionError(
                PLANNING_ADMISSION_INVALID,
                "legacy plan payloads are forbidden; CompiledPlanningGraph is required",
            )
        compiled.validate()
        if not isinstance(surface, HostCommandSurface):
            raise PlanningAdmissionError(
                PLANNING_ADMISSION_INVALID,
                "surface must be HostCommandSurface",
            )
        work_order = compiled.work_order
        graph = compiled.task_graph
        grant_ref = str(governor_grant_ref or "").strip()
        if not grant_ref or grant_ref != str(work_order.approval_ref or "").strip():
            raise PlanningAdmissionError(
                PLANNING_ADMISSION_AUTHORITY_MISMATCH,
                "HostCommand grant must equal the compiled WorkOrder approval_ref",
            )
        if work_order.budget_policy is None:
            raise PlanningAdmissionError(
                PLANNING_ADMISSION_INVALID,
                "compiled WorkOrder requires an explicit BudgetPolicy",
            )

        # HostCommand 将入口输入摘要、父治理授权和图修订绑定，作为后续任务运行时的唯一执行请求。
        command = build_host_command(
            surface=surface,
            request_id=str(command_request_id or "").strip(),
            session_id=str(session_id or "").strip(),
            principal_id=str(principal_id or "").strip(),
            ingress_claim_ref=ingress_claim_ref,
            ingress_audit_ref=ingress_audit_ref,
            governor_grant_ref=grant_ref,
            input_ref=str(input_ref or "").strip(),
            input_sha256=str(input_sha256 or "").strip(),
            root_work_order_id=graph.root_work_order_id,
            graph_id=graph.graph_id,
            graph_revision=graph.revision,
            route_type="plan",
            target_id=str(target_id or "").strip(),
            business_scope=str(work_order.business_idempotency_scope or "").strip(),
            operation_fingerprint=str(work_order.operation_fingerprint or "").strip(),
            created_at_ms=graph.created_at,
        )
        assignment = self._router.assign_new_task(
            graph.root_work_order_id,
            assigned_at_ms=graph.created_at,
        )
        # 首次图与失败后的 replan 使用不同仓储原子操作；后者必须显式携带旧修订及失败证据。
        if compiled.previous_revision is None:
            raw = self._commands.admit(command, work_order, assignment, graph)
        else:
            admit_replan = getattr(self._commands, "admit_replan", None)
            if not callable(admit_replan):
                raise PlanningAdmissionError(
                    PLANNING_ADMISSION_DURABLE_DRIFT,
                    "command repository has no durable graph supersession authority",
                )
            raw = admit_replan(
                command,
                work_order,
                assignment,
                graph,
                previous_revision=compiled.previous_revision,
                failure_evidence_refs=compiled.failure_evidence_refs,
            )
        record = await raw if inspect.isawaitable(raw) else raw
        if not isinstance(record, Mapping):
            raise PlanningAdmissionError(
                PLANNING_ADMISSION_DURABLE_DRIFT,
                "command repository returned no typed durable admission",
            )
        # 仓储返回的类型化回执必须与提交的四份契约逐字一致，避免“成功”隐藏持久投影漂移。
        receipt = PlanningAdmissionReceipt(
            command=_typed(record, "command", HostCommand),
            work_order=_typed(record, "work_order", WorkOrderV3),
            assignment=_typed(record, "assignment", ExecutorAssignment),
            graph=_typed(record, "graph", TaskGraph),
            created=bool(record.get("created")),
            replayed=bool(record.get("replayed")),
        )
        receipt.validate()
        _require_exact_projection(receipt, command, work_order, assignment, graph)
        return receipt


def _typed(record: Mapping[str, Any], key: str, expected: type[Any]) -> Any:
    value = record.get(key)
    if not isinstance(value, expected):
        raise PlanningAdmissionError(
            PLANNING_ADMISSION_DURABLE_DRIFT,
            f"durable admission {key} is not {expected.__name__}",
        )
    return value


def _require_exact_projection(
    receipt: PlanningAdmissionReceipt,
    command: HostCommand,
    work_order: WorkOrderV3,
    assignment: ExecutorAssignment,
    graph: TaskGraph,
) -> None:
    # 对比序列化契约而非对象身份，使重启后的 durable replay 仍能发现任意字段级别的漂移。
    expected = (
        command.to_dict(),
        work_order.to_dict(),
        assignment.to_dict(),
        graph.to_dict(),
    )
    durable = (
        receipt.command.to_dict(),
        receipt.work_order.to_dict(),
        receipt.assignment.to_dict(),
        receipt.graph.to_dict(),
    )
    if durable != expected:
        raise PlanningAdmissionError(
            PLANNING_ADMISSION_DURABLE_DRIFT,
            "durable replay differs from the immutable compiled admission",
        )


__all__ = [
    "GovernedPlanningAdmissionService",
    "PLANNING_ADMISSION_AUTHORITY_MISMATCH",
    "PLANNING_ADMISSION_DURABLE_DRIFT",
    "PLANNING_ADMISSION_INVALID",
    "PlanningAdmissionError",
    "PlanningAdmissionReceipt",
]
