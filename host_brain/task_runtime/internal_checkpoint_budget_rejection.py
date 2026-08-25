"""内部 Agent 检查点因预算超限而拒绝时的原子终态收口。

这条主链只处理已提交的单节点内部委派：同一存储事务必须同时关闭
Attempt、Node、Graph、Lease 与预算预留，避免调用方看到“已经扣费却仍可执行”
的中间状态。事务响应丢失时只允许按同一参数重开终态，不会再次发起扣费变更。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
from typing import Any

from .admission import AdmittedExecution
from .coordinator_receipts import build_execution_receipt
from .coordinator_records import (
    _mapping,
    _required_record_text,
    _status,
)
from .errors import GOVERNED_OPERATION_RECOVERY_REQUIRED, GovernedRuntimeError
from .ports import InternalAgentCheckpointUnitOfWorkPort
from .runtime_outbox import RuntimeOutbox
from host_brain.core.runtime_helpers import maybe_await as _maybe_await


def is_internal_agent_checkpoint_authority(
    command: Any,
) -> bool:
    """识别由内部 ``agent_runtime`` 委派拥有的检查点权限边界。"""
    return (
        str(getattr(command.surface, "value", command.surface)).lower()
        == "internal"
        and str(command.route_type).lower() == "delegate"
        and str(command.target_id) == "agent_runtime"
    )


def requires_internal_terminal_bundle(
    admitted: AdmittedExecution,
    command: Any,
) -> bool:
    """判断路由是否必须使用绑定到该 Attempt 的内部终态工作单元。

    多节点图不能借用这个快捷收口：只有单节点才可由一次预算拒绝直接确定整个图的
    终态，防止把尚在运行的兄弟节点误判为失败。
    """

    return len(admitted.nodes) == 1 and is_internal_agent_checkpoint_authority(command)


def is_internal_budget_terminal(
    admitted: AdmittedExecution,
    command: Any,
    receipt: Any,
) -> bool:
    """判断既有回执是否已由这条内部终态链拥有图收口权。"""

    return (
        requires_internal_terminal_bundle(admitted, command)
        and isinstance(receipt, Mapping)
        and _status(receipt) == "BUDGET_BLOCKED"
    )


class InternalCheckpointBudgetRejectionRuntime:
    """将检查点预算拒绝委托给 Store 所有的原子事务。

    Runtime 仅组装围栏版本、证据与 outbox；状态转换由端口在持久层一次提交。
    这样可将授权校验、预算释放及事件版本绑定在同一恢复边界内。
    """

    def __init__(
        self,
        *,
        checkpoint_uow: InternalAgentCheckpointUnitOfWorkPort,
        runtime_outbox: RuntimeOutbox,
        owner_id: str,
    ) -> None:
        """保存事务端口与发布器；``owner_id`` 参与 Lease 围栏校验。"""
        self._checkpoint_uow = checkpoint_uow
        self._runtime_outbox = runtime_outbox
        self._owner_id = str(owner_id)

    async def reject(
        self,
        *,
        admitted: AdmittedExecution,
        charge: Any,
        attempt: Mapping[str, Any],
        node: Mapping[str, Any],
        graph: Mapping[str, Any],
        reservation: Mapping[str, Any],
        lease: Mapping[str, Any],
        fencing_token: int,
        evidence_refs: Sequence[str],
        require_internal_agent: bool = True,
    ) -> Any:
        """拒绝当前检查点，并返回可幂等重开的 ``BUDGET_BLOCKED`` 回执。

        输入携带每个聚合的期望版本和 fencing token，持久层据此拒绝过期 worker。
        一旦主事务的响应不确定，改走只读式的终态重开接口；若两者都失败则保留原始
        变更异常，避免把恢复失败误报成未发生变更。
        """
        attempt_id = _required_record_text(attempt, "attempt_id")
        error_ref = "taskerror://" + hashlib.sha256(
            f"{attempt_id}\nGOVERNED_BUDGET_EXCEEDED".encode("utf-8")
        ).hexdigest()
        evidence = tuple(
            dict.fromkeys(
                (
                    error_ref,
                    str(charge.evidence_ref),
                    str(charge.usage_id),
                    *tuple(evidence_refs),
                )
            )
        )
        active_lease = _mapping(
            lease,
            field_name="Internal Agent budget rejection Lease",
        )
        arguments = {
            "reservation_id": _required_record_text(
                reservation,
                "reservation_id",
            ),
            "budget_id": charge.context.budget_id,
            "lease_id": _required_record_text(active_lease, "lease_id"),
            "owner_id": self._owner_id,
            "fencing_token": fencing_token,
            "expected_attempt_version": int(attempt.get("status_version") or 0),
            "expected_node_version": int(node.get("status_version") or 0),
            "expected_graph_version": int(graph.get("status_version") or 0),
            "expected_budget_version": charge.context.version,
            "expected_lease_version": int(
                active_lease.get("status_version") or 0
            ),
            "error_ref": error_ref,
            "usage_id": str(charge.usage_id),
            "attempt_outbox": self._runtime_outbox.build(
                command_id=admitted.command_id,
                aggregate_type="TASK_ATTEMPT",
                aggregate_id=attempt_id,
                aggregate_version=int(attempt.get("status_version") or 0) + 1,
                event_type="TASK_ATTEMPT_FAILED",
                attempt_id=attempt_id,
                evidence_refs=evidence,
            ),
        }
        mutation = (
            self._checkpoint_uow.reject_internal_agent_checkpoint_budget_bundle
            if require_internal_agent
            else self._checkpoint_uow.reject_committed_checkpoint_budget_bundle
        )
        reopen = (
            self._checkpoint_uow.reopen_internal_agent_budget_rejection_terminal
            if require_internal_agent
            else self._checkpoint_uow.reopen_committed_checkpoint_budget_rejection_terminal
        )
        try:
            result_raw = await _maybe_await(
                mutation(
                    attempt_id,
                    **arguments,
                )
            )
        except Exception as mutation_error:
            # 写入可能已成功、只是响应在途中丢失。重开接口按精确版本确认既有终态，
            # 因而绝不能重放会再次改变预算或图状态的终态 mutation。
            try:
                result_raw = await _maybe_await(
                    reopen(
                        attempt_id,
                        **arguments,
                    )
                )
            except Exception as reopen_error:
                if (
                    str(getattr(reopen_error, "reason_code", ""))
                    == "TASK_STORE_DURABLE_DATA_INVALID"
                ):
                    raise
                raise mutation_error
        result = _mapping(
            result_raw,
            field_name="atomic committed checkpoint budget rejection",
        )
        terminal_attempt = _mapping(result.get("attempt"), field_name="terminal attempt")
        terminal_node = _mapping(result.get("node"), field_name="terminal node")
        terminal_graph = _mapping(result.get("graph"), field_name="terminal graph")
        terminal_lease = _mapping(result.get("lease"), field_name="terminal lease")
        terminal_budget = _mapping(result.get("budget"), field_name="terminal budget")
        if (
            _status(terminal_attempt) != "FAILED"
            or _status(terminal_node) != "FAILED"
            or _status(terminal_graph) != "BLOCKED"
            or _status(terminal_lease) != "RELEASED"
            or _status(terminal_budget) != "RELEASED"
            or str(result.get("usage_id") or "") != str(charge.usage_id)
        ):
            raise GovernedRuntimeError(
                GOVERNED_OPERATION_RECOVERY_REQUIRED,
                "atomic committed checkpoint budget rejection is incomplete",
            )
        await self._runtime_outbox.publish_pending()
        terminal_context = charge.context.refreshed(terminal_budget)
        return build_execution_receipt(
            admitted,
            attempt_id=attempt_id,
            status="BUDGET_BLOCKED",
            verification_status="NOT_RUN",
            accepted=False,
            reason_code="GOVERNED_BUDGET_EXCEEDED",
            evidence_refs=evidence,
            projection={
                "budget": terminal_context.execution_payload(),
                "exceeded_dimensions": list(charge.exceeded_dimensions),
                "requested_action": terminal_context.policy.on_exceeded.value,
                "enforced_action": "BLOCK",
            },
        )




__all__ = [
    "InternalCheckpointBudgetRejectionRuntime",
    "is_internal_budget_terminal",
    "is_internal_agent_checkpoint_authority",
    "requires_internal_terminal_bundle",
]
