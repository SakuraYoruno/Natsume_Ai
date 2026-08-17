"""已提交 checkpoint 的纯恢复执行上下文渲染。

此上下文供验证与验收读取，不恢复 worker 执行授权；它显式区分历史 lease 证据与已
释放的执行权限，避免下游把接管信息误当作可再次执行外部操作的许可。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from natsume_contracts.task.v1 import HostCommand

from .admission import AdmittedExecution
from .coordinator_records import _node_input_refs, _text
from .operation_recovery_runtime import CommittedOperationCheckpoint
from .worker_registry import GovernedWorkerRoute


def build_checkpoint_execution_context(
    *,
    admitted: AdmittedExecution,
    command: HostCommand,
    budget: Any,
    node: Mapping[str, Any],
    checkpoint: CommittedOperationCheckpoint,
    node_grant_ref: str | None,
    owner_id: str,
    input_artifacts: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """从冻结准入、checkpoint 和当前预算构建验证/验收使用的恢复上下文。"""
    if node_grant_ref is None:
        recovery_target_id = command.target_id
    else:
        frozen_route = GovernedWorkerRoute.parse(admitted.worker_route)
        recovery_target_id = frozen_route.target_id or command.target_id
    # 有节点 Grant 时以准入冻结路由确定 target，防止命令可变 target 扩大恢复作用域。
    active_lease = checkpoint.lease
    context = {
        "execution_mode": "governed_task_runtime",
        "command_id": admitted.command_id,
        "request_id": command.request_id,
        "principal_id": command.principal_id,
        "target_id": recovery_target_id,
        "command_target_id": command.target_id,
        "work_order_id": admitted.work_order_id,
        "assignment_id": admitted.assignment_id,
        "executor": admitted.executor,
        "worker_route": admitted.worker_route,
        "graph_id": admitted.graph_id,
        "graph_revision": admitted.graph_revision,
        "node_id": admitted.node_id,
        "attempt_id": checkpoint.attempt_id,
        # 验证只在接管 Attempt 已 SUCCEEDED 且其 lease 已释放后运行；以下 lease 绑定
        # 仅是证据，不是继续执行 worker 的权限。
        "lease_id": None,
        "lease_owner_id": None,
        "fencing_token": None,
        "reservation_id": admitted.reservation_id,
        "business_scope": admitted.business_scope,
        "operation_fingerprint": admitted.operation_fingerprint,
        "parent_grant_ref": str(command.governor_grant_ref or "").strip(),
        "budget": budget.execution_payload(),
        "node_input_refs": list(_node_input_refs(node)),
        "node_input_artifacts": [dict(item) for item in input_artifacts],
        "node_acceptance_criteria": list(admitted.active_node.acceptance_criteria),
        "node_risk_level": int(admitted.active_node.risk_level),
        "recovery": {
            "mode": "COMMITTED_CHECKPOINT_ONLINE_TAKEOVER",
            "worker_replayed": False,
            "recovery_owner_id": owner_id,
            "execution_authority_active": False,
            "takeover_lease_acquired": active_lease is not None,
            "checkpoint_payload_digest": checkpoint.payload_digest,
            "checkpoint_payload_digest_source": checkpoint.payload_digest_source,
            "takeover_lease": (
                {
                    "lease_id": _text(active_lease.get("lease_id")),
                    "owner_id": _text(active_lease.get("owner_id")),
                    "fencing_token": int(active_lease.get("fencing_token") or 0),
                }
                if active_lease
                else None
            ),
            "checkpoint_lease": {
                "lease_id": _text(checkpoint.reservation.get("lease_id")) or None,
                "owner_id": _text(checkpoint.reservation.get("owner_id")) or None,
                "fencing_token": int(
                    checkpoint.reservation.get("fencing_token") or 0
                ),
            },
        },
    }
    if node_grant_ref is not None:
        # Grant 引用只为验证其历史授权谱系，不在恢复上下文中重新授予操作能力。
        context["node_grant_ref"] = node_grant_ref
    return context


__all__ = ["build_checkpoint_execution_context"]
