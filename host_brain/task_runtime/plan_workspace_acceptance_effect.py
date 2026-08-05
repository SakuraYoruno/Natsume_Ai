"""Compile a selected Plan node's deferred Workspace proposal.

Capability Plans use atomic graph success.  Intermediate node effects therefore
remain proposals until every node is accepted; only then may this compiler turn
the one policy-authorized Agent Workspace proposal into a graph-scoped Effect.
It never creates conversation, Life, delivery, or other result-node effects.
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
    """Compile zero or one effect from an accepted pre-result Plan node."""

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
        # The result Chat node is the sole owner of visible conversation/Life
        # effects.  A pre-result Skill's foreground proposal is therefore safe
        # to discard (and keeps already-accepted projections recoverable), but
        # every other effect remains an authority violation.
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
    raise AcceptanceEffectCompileError(detail)


__all__ = ["compile_plan_node_acceptance_effects"]
