"""Pure compiler and validation helpers for durable node grants."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote

from host_brain.contracts.agent_runtime.task_graph import TaskGraph
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.orchestration.node_grant_codec import (
    digest as _digest,
    exact_integer as _exact_integer,
    mapping as _mapping,
    required_text as _codec_required_text,
    stable_ref as _stable_ref,
)
from host_brain.orchestration.node_grant_errors import (
    NODE_GRANT_BINDING_MISSING,
    NODE_GRANT_INPUT_INVALID,
    NODE_GRANT_ROUTE_DRIFT,
    NODE_GRANT_ROUTE_UNSUPPORTED,
    NodeGrantDerivationError,
    NodeGrantVerificationError,
)


_SUPPORTED_EXECUTABLE_ROUTES = frozenset({"skill", "delegate", "chat", "tool"})
_MAX_EXECUTION_GRANT_TTL_MS = 24 * 60 * 60 * 1_000


@dataclass(frozen=True, slots=True)
class _NodeSpec:
    node_id: str
    route: str
    target_id: str
    risk_level: int
    ordinal: int
    decision_id: str
    intent_id: str
    nonce: str
    seq: int
    action_kind: str
    token_subject: str


def _execution_grant_ttl_ms(
    *,
    work_order: WorkOrderV3,
    minimum_ttl_ms: int,
) -> int:
    """Bind an admitted node credential to the Task's frozen time budget.

    The parent grant limits when a TaskGraph may be admitted and delegated. Once
    that delegation is durably recorded, its nodes need an execution window long
    enough to consume the already-approved Task budget. The credential remains
    bounded and still expires independently of the durable Task state.
    """

    budget_time_ms = (
        int(work_order.budget_policy.limits.time_ms)
        if work_order.budget_policy is not None
        else 0
    )
    if budget_time_ms > _MAX_EXECUTION_GRANT_TTL_MS:
        raise NodeGrantDerivationError(
            NODE_GRANT_INPUT_INVALID,
            "Task execution budget exceeds the 24-hour node-grant ceiling",
        )
    # A worker Lease may legitimately consume the full frozen time budget.
    # The node credential must therefore outlive that Lease long enough for
    # the periodic survivor to fence and resume a pre-Operation crash.  Using
    # only ``budget_time_ms`` made the grant expire a few milliseconds before
    # the Lease that it was meant to recover.
    recovery_margin_ms = int(minimum_ttl_ms)
    return min(
        _MAX_EXECUTION_GRANT_TTL_MS,
        max(recovery_margin_ms, budget_time_ms + recovery_margin_ms),
    )


def _compile_specs(
    *,
    task_graph: TaskGraph,
    work_order: WorkOrderV3,
    parent_ref: str,
    request_id: str,
    session_id: str,
    principal_id: str,
    parent_seq: int,
    review_continuation_authority: Mapping[str, Any] | None = None,
) -> tuple[tuple[_NodeSpec, ...], str]:
    # 编译器把冻结图与规划绑定转换为可验证的节点规格；任一节点缺失或路由漂移都会拒绝整批授权。
    if not task_graph.nodes:
        raise NodeGrantDerivationError(NODE_GRANT_INPUT_INVALID, "TaskGraph has no executable nodes")
    if (
        work_order.task_graph_id != task_graph.graph_id
        or work_order.graph_revision != task_graph.revision
        or work_order.root_work_order_id != task_graph.root_work_order_id
        or work_order.work_order_id != task_graph.root_work_order_id
        or work_order.approval_ref != parent_ref
        or work_order.task_node_id not in {node.node_id for node in task_graph.nodes}
    ):
        raise NodeGrantDerivationError(
            NODE_GRANT_INPUT_INVALID,
            "WorkOrderV3 graph/root/revision/node/parent authority lineage mismatch",
        )
    lineage = _mapping(work_order.metadata.get("planning_lineage"))
    if (
        str(lineage.get("graph_id") or "") != task_graph.graph_id
        or str(lineage.get("root_work_order_id") or "") != task_graph.root_work_order_id
        or _integer(lineage.get("revision"), NODE_GRANT_INPUT_INVALID, "lineage revision") != task_graph.revision
        or str(lineage.get("approval_ref") or "") != parent_ref
    ):
        raise NodeGrantDerivationError(NODE_GRANT_INPUT_INVALID, "planning lineage drifted from canonical graph")
    raw_bindings = lineage.get("node_bindings")
    if not isinstance(raw_bindings, list):
        raise NodeGrantDerivationError(NODE_GRANT_BINDING_MISSING, "planning node_bindings are required")
    bindings: dict[str, dict[str, Any]] = {}
    for raw in raw_bindings:
        if not isinstance(raw, Mapping):
            raise NodeGrantDerivationError(NODE_GRANT_BINDING_MISSING, "node binding must be an object")
        binding = dict(raw)
        node_id = str(binding.get("node_id") or "").strip()
        if not node_id or node_id in bindings:
            raise NodeGrantDerivationError(NODE_GRANT_BINDING_MISSING, "node bindings must have unique node_id")
        bindings[node_id] = binding
    graph_node_ids = {node.node_id for node in task_graph.nodes}
    if set(bindings) != graph_node_ids:
        raise NodeGrantDerivationError(
            NODE_GRANT_BINDING_MISSING,
            "node bindings must cover the executable graph exactly",
        )

    # 批次身份纳入请求、主体、父授权与图修订，确保不同执行世代绝不会共享子 Grant。
    batch_identity: dict[str, Any] = {
        "request_id": request_id,
        "session_id": session_id,
        "principal_id": principal_id,
        "parent_grant_ref": parent_ref,
        "graph_id": task_graph.graph_id,
        "graph_revision": task_graph.revision,
        "root_work_order_id": task_graph.root_work_order_id,
    }
    if review_continuation_authority:
        batch_identity["review_continuation_authority"] = dict(
            review_continuation_authority
        )
    batch_id = _stable_ref("node-grant-batch", batch_identity)
    specs: list[_NodeSpec] = []
    for ordinal, node in enumerate(task_graph.nodes, start=1):
        # route/target 必须同时与图和 WorkOrder 绑定吻合；不能让计划的显示路径与实际执行目标分离。
        route, separator, encoded_target = str(node.route or "").partition(":")
        route = route.strip().lower()
        target_id = unquote(encoded_target).strip() if separator else ""
        binding = bindings[node.node_id]
        bound_route = str(binding.get("worker_route") or "").strip().lower()
        bound_target = str(binding.get("target_id") or "").strip()
        if route not in _SUPPORTED_EXECUTABLE_ROUTES:
            raise NodeGrantDerivationError(NODE_GRANT_ROUTE_UNSUPPORTED, f"unsupported executable route: {route}")
        if not target_id or bound_route != route or bound_target != target_id:
            raise NodeGrantDerivationError(
                NODE_GRANT_ROUTE_DRIFT,
                f"TaskGraph route and WorkOrder binding differ for node: {node.node_id}",
            )
        identity = {
            "batch_id": batch_id,
            "node_id": node.node_id,
            "route": route,
            "target_id": target_id,
            "ordinal": ordinal,
        }
        digest = _digest(identity)
        # Skill and Tool low-level executors validate target-only token
        # subjects.  Their durable action binding still distinguishes Tool
        # from Skill; only the bearer-token subject preserves the existing
        # broker contract. Chat/delegate bind route+target for both fields.
        action_kind = target_id if route == "skill" else f"{route}:{target_id}"
        token_subject = target_id if route in {"skill", "tool"} else action_kind
        specs.append(_NodeSpec(
            node_id=node.node_id,
            route=route,
            target_id=target_id,
            risk_level=int(node.risk_level),
            ordinal=ordinal,
            decision_id=f"node-grant-decision://sha256/{digest}",
            intent_id=f"node-grant-intent::{digest}",
            nonce=f"nonce::node-grant::{digest}",
            seq=parent_seq * 1_000_000 + ordinal,
            action_kind=action_kind,
            token_subject=token_subject,
        ))
    return tuple(specs), batch_id


def _node_authority(
    *,
    spec: _NodeSpec,
    task_graph: TaskGraph,
    parent_ref: str,
    batch_id: str,
    request_id: str,
    expires_at_ms: int,
    review_continuation_authority: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    # 此公开投影绑定节点、顺序、动作和到期时间，bearer token 本身仍保留在 Governor 账本之外。
    authority = {
        "schema_version": "Natsume.task_graph.node_grant.binding.v1",
        "batch_id": batch_id,
        "decision_id": spec.decision_id,
        "request_id": request_id,
        "graph_id": task_graph.graph_id,
        "graph_revision": task_graph.revision,
        "root_work_order_id": task_graph.root_work_order_id,
        "node_id": spec.node_id,
        "route": spec.route,
        "target_id": spec.target_id,
        "ordinal": spec.ordinal,
        "seq": spec.seq,
        "action_kind": spec.action_kind,
        "token_subject": spec.token_subject,
        "parent_governor_grant_ref": parent_ref,
        "expires_at_ms": int(expires_at_ms),
        "nonce": spec.nonce,
    }
    if review_continuation_authority:
        authority["review_continuation_authority"] = dict(
            review_continuation_authority
        )
    return authority


def _child_intent(
    *,
    spec: _NodeSpec,
    task_graph: TaskGraph,
    parent_ref: str,
    request_id: str,
    session_id: str,
    principal_id: str,
    parent_scope: str,
    now_ms: int,
    expires_at_ms: int,
    review_continuation_authority: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    # 子 Intent 复用父 scope 但固定到一个节点，供治理账本重建和审计，而不是供 worker 自行改写。
    args: dict[str, Any] = {
        "request_id": request_id,
        "graph_id": task_graph.graph_id,
        "graph_revision": task_graph.revision,
        "root_work_order_id": task_graph.root_work_order_id,
        "node_id": spec.node_id,
        "route": spec.route,
        "target_id": spec.target_id,
        "action_kind": spec.action_kind,
        "token_subject": spec.token_subject,
        "parent_governor_grant_ref": parent_ref,
        "expires_at_ms": int(expires_at_ms),
        "nonce": spec.nonce,
    }
    if review_continuation_authority:
        args["review_continuation_authority"] = dict(
            review_continuation_authority
        )
    return {
        "schema_version": "Natsume.intent.v1",
        "id": spec.intent_id,
        "ts_ms": int(now_ms),
        "session_id": session_id,
        "seq": spec.seq,
        "person_id": principal_id,
        "principal_id": principal_id,
        "type": "query",
        "action": spec.action_kind,
        "route": spec.route,
        "target_id": spec.target_id,
        "target": parent_scope,
        "args": args,
        "nonce": spec.nonce,
        "source": (
            "node_bound_grant_service.review_continuation"
            if review_continuation_authority
            else "node_bound_grant_service"
        ),
    }


def _required_text(value: Any, field_name: str) -> str:
    try:
        return _codec_required_text(value)
    except ValueError as exc:
        raise NodeGrantDerivationError(
            NODE_GRANT_INPUT_INVALID,
            f"{field_name} is required",
        ) from exc


def _verify_text(value: Any, field_name: str) -> str:
    try:
        return _codec_required_text(value)
    except ValueError as exc:
        raise NodeGrantVerificationError(f"{field_name} is required") from exc


def _integer(value: Any, reason_code: str, field_name: str) -> int:
    try:
        return _exact_integer(value)
    except ValueError as exc:
        raise NodeGrantDerivationError(reason_code, f"{field_name} must be an integer") from exc


__all__ = [
    "NODE_GRANT_BINDING_MISSING",
    "NODE_GRANT_INPUT_INVALID",
    "NODE_GRANT_ROUTE_DRIFT",
    "NODE_GRANT_ROUTE_UNSUPPORTED",
    "NodeGrantDerivationError", "NodeGrantVerificationError",
]
