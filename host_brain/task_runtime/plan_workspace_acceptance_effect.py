"""编译指定 Plan 节点延后的 Workspace 产物提案。

Capability Plan 以整张图的原子成功为准，因此中间节点的效果只能保留为提案；
只有全部节点通过验收后，本编译器才把策略唯一授权的 Agent Workspace 提案转为
图作用域 Effect。它绝不创建会话、Life、交付或其他结果节点专属效果。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from host_brain.agent_runtime.workspace_artifact_effect import (
    AGENT_WORKSPACE_ARTIFACT_EFFECT_TYPE,
)
from host_brain.contracts.agent_runtime.plan_execution_policy import (
    PLAN_EXECUTION_POLICY_METADATA_KEY,
    PLAN_NODE_WORKSPACE_EFFECT_MODE,
    PLAN_SPECULATIVE_EFFECT_MODE,
    PlanExecutionPolicyError,
    require_plan_workspace_effect_owner,
    validate_plan_execution_policy,
)
from host_brain.task_runtime.acceptance_effects import (
    AcceptanceEffectCompileError,
    build_acceptance_effect_intent_spec,
    build_action_preview_context,
)
from host_brain.task_runtime.agent_workspace_acceptance_effect import (
    AgentWorkspaceAcceptanceEffectError,
    compile_agent_workspace_artifact_effect,
    validated_worker_effect_descriptors,
)
from natsume_contracts.task import HostCommand


def compile_plan_node_acceptance_effects(
    *,
    command: HostCommand,
    work_order: Any,
    node_id: str,
    attempt_id: str,
    worker_commit: Mapping[str, Any],
) -> tuple[Any, ...]:
    """从已验收的 Plan 前置节点编译零或一个 Effect。

    这里将节点身份同时绑定到计划策略、worker 投影和 Runtime Context；即使某个
    节点已成功，也不能绕开整图验收或把结果节点拥有的可见副作用提前执行。
    """

    if not isinstance(command, HostCommand) or command.route_type != "plan":
        _invalid("Plan node effect compilation requires a Plan HostCommand")
    node_ref = str(node_id or "").strip()
    attempt_ref = str(attempt_id or "").strip()
    commit = dict(worker_commit or {})
    if (
        not node_ref
        or not attempt_ref
        or str(commit.get("decision") or "").strip().upper() != "APPROVE"
        or str(commit.get("request_id") or command.request_id)
        != command.request_id
    ):
        _invalid("Plan node effect compilation requires one approved projection")

    metadata = getattr(work_order, "metadata", None)
    metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
    raw_policy = metadata.get(PLAN_EXECUTION_POLICY_METADATA_KEY)
    try:
        policy = validate_plan_execution_policy(
            raw_policy if isinstance(raw_policy, Mapping) else {}
        )
    except PlanExecutionPolicyError as exc:
        _invalid(exc.detail)
    # 排除最终结果节点：其前台回复有独立的验收编译链，不能被 Workspace 分支
    # 误认领为中间节点效果。
    matches = [
        dict(node)
        for node in policy["nodes"][:-1]
        if node["node_id"] == node_ref
    ]
    if len(matches) != 1:
        _invalid("Plan projection does not belong to one pre-result node")
    node_policy = matches[0]
    try:
        descriptors = validated_worker_effect_descriptors(commit)
    except AgentWorkspaceAcceptanceEffectError as exc:
        _invalid(str(exc))

    if node_policy["effect_mode"] == PLAN_SPECULATIVE_EFFECT_MODE:
        # 结果 Chat 节点是可见会话/Life 效果的唯一所有者。前置 Skill 的前台
        # 提案可以丢弃（已验收投影仍可恢复），但任何其他效果都属于越权。
        unauthorized = tuple(
            descriptor
            for descriptor in descriptors
            if descriptor["effect_type"] != "life.foreground_completed"
        )
        if unauthorized:
            _invalid("speculative Plan node proposed an Acceptance Effect")
        return ()
    if node_policy["effect_mode"] != PLAN_NODE_WORKSPACE_EFFECT_MODE:
        _invalid("Plan node effect mode is unsupported")
    try:
        require_plan_workspace_effect_owner(
            policy=policy,
            node_id=node_ref,
            target_id=str(node_policy["target_id"]),
        )
    except PlanExecutionPolicyError as exc:
        _invalid(exc.detail)
    if (
        len(descriptors) != 1
        or descriptors[0]["effect_type"]
        != AGENT_WORKSPACE_ARTIFACT_EFFECT_TYPE
    ):
        _invalid(
            "Workspace-authorized Plan node must propose exactly one "
            "Workspace Effect"
        )
    try:
        payload = compile_agent_workspace_artifact_effect(
            descriptors=descriptors,
            command=command,
            work_order=work_order,
            node_id=node_ref,
            attempt_id=attempt_ref,
            worker_commit=commit,
        )
    except AgentWorkspaceAcceptanceEffectError as exc:
        _invalid(str(exc))
    if payload is None:
        _invalid("Workspace-authorized Plan node has no Workspace payload")
    preview = build_action_preview_context(
        command=command,
        work_order=work_order,
        node_id=node_ref,
        worker_commit=commit,
    )
    if preview is None:
        _invalid(
            "Workspace-authorized Plan node has no frozen Runtime Context "
            "for ActionPreview"
        )
    # Preview 仅作为后续领取时重开授权的耐久谱系，真实的短期执行证明由
    # dispatcher 在持有当前 lease/fence 后另行签发。
    payload = {**dict(payload), "_action_preview_context": preview}
    return (
        build_acceptance_effect_intent_spec(
            effect_type=AGENT_WORKSPACE_ARTIFACT_EFFECT_TYPE,
            payload=payload,
            command=command,
            node_id=node_ref,
            attempt_id=attempt_ref,
        ),
    )


def _invalid(detail: str) -> None:
    """统一转换为验收编译失败，阻止不完整提案进入图终态事务。"""
    raise AcceptanceEffectCompileError(detail)


__all__ = ["compile_plan_node_acceptance_effects"]
