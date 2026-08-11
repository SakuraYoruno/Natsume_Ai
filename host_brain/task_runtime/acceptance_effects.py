"""Compile trusted graph-scoped effects after governed execution Acceptance.

Workers may return a rich, authority-free foreground payload, but they never
choose durable identity, graph scope, attempt binding, delivery destination or
authorization.  This compiler rebuilds those fields from the admitted command
and immutable WorkOrder and returns canonical TaskStore intent specs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import hashlib
import json
import math
import re
from typing import Any

from host_brain.persistence.task_repository import (
    AcceptanceEffectCommitScope,
    AcceptanceEffectIntentSpec,
)
from host_brain.contracts.conversation_acceptance_effects import (
    LIFE_CONVERSATION_PROJECTION_SCHEMA_V1,
    PREFERENCE_SIGNAL_SCHEMA_V1,
    conversation_life_command_id,
    preference_operation_id,
)
from host_brain.contracts.memory_acceptance_effects import (
    EXPLICIT_MEMORY_STORE_EFFECT_TYPE,
    EXPLICIT_MEMORY_STORE_SCHEMA_V1,
)
from host_brain.contracts.agent_runtime.plan_execution_policy import (
    PLAN_EXECUTION_POLICY_METADATA_KEY,
    PlanExecutionPolicyError,
    require_plan_result_effect_owner,
)
from host_brain.contracts.agent_runtime.control_route_execution_policy import (
    ControlRouteExecutionPolicyError,
    require_control_route_execution_policy,
)
from natsume_contracts.edge.embodiment_v1 import build_embodiment_command
from natsume_contracts.task import HostCommand, HostCommandSurface
from host_brain.runtime.action_preview_authority import (
    ActionPreviewAuthorityError,
    context_pack_digest_from_work_order,
)
from host_brain.runtime.action_preview_resolver import action_preview_scene_identity
from host_brain.task_runtime.operator_control_acceptance_effect import (
    compile_operator_control_acceptance_effect,
)
from host_brain.task_runtime.operator_state_control_acceptance_effect import (
    compile_operator_state_control_acceptance_effect,
    is_operator_state_control_command,
)
from host_brain.task_runtime.operation_reconciliation_acceptance_effect import (
    validate_operation_reconciliation_proposal,
)
from host_brain.task_runtime.worker_authority_guard import forbidden_worker_authority_path
from host_brain.task_runtime import agent_workspace_acceptance_effect as agent_workspace_effects
from host_brain.agent_runtime.workspace_artifact_effect import AGENT_WORKSPACE_ARTIFACT_EFFECT_TYPE
from host_brain.digital_life.companion_expression_acceptance import (
    COMPANION_EXPRESSION_ENQUEUE_EFFECT,
    CompanionExpressionEffectError,
    compile_companion_expression_effect_payloads,
)

ACCEPTANCE_EFFECT_DESCRIPTOR_INVALID = "ACCEPTANCE_EFFECT_DESCRIPTOR_INVALID"
_EFFECT_TYPE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$")
_OPERATOR_SYS_MODE_TARGET = "operator_sys_mode_switch"
_OPERATOR_SYS_MODE_EFFECT = "host.sys_mode_transition"
_OPERATOR_EDGE_TARGET = "operator_edge_command_push"
_OPERATOR_EDGE_EFFECT = "edge.embodiment_enqueue"
_OPERATOR_TERMINAL_TARGET = "operator_task_terminal_reconcile"
_OPERATOR_TERMINAL_EFFECT = "task.terminal_reconciliation"
_OPERATOR_OPERATION_RECONCILIATION_TARGET = (
    "operator_task_operation_reconcile"
)
_OPERATOR_OPERATION_RECONCILIATION_EFFECT = (
    "task.operation_reconciliation"
)
_OPERATOR_EFFECT_RECONCILIATION_TARGET = (
    "operator_task_effect_reconcile"
)
_OPERATOR_EFFECT_RECONCILIATION_EFFECT = (
    "task.acceptance_effect_reconciliation"
)
_OPERATOR_CONTROL_TARGET = "operator_task_control"
_SCHEDULER_OPERATION_EFFECT = "scheduler.operation.execute"
_SCHEDULER_PLAN_SCHEMA = "Natsume.scheduler.operation_plan.v1"
_SCHEDULER_EFFECT_SCHEMA = "Natsume.scheduler.accepted_operation.v1"
_INTERNAL_RESULT_COMMIT_EFFECT = "internal.agent.result_commit"
_INTERNAL_PROPOSAL_SCHEMA = "Natsume.internal.agent_result_proposal.v1"
_INTERNAL_EFFECT_SCHEMA = "Natsume.internal.accepted_agent_result.v1"
_SYS_MODES = frozenset(
    {"normal", "sleep", "gaming", "focus", "maintenance", "locked"}
)
_SYS_MODE_SOURCES = frozenset(
    {"manual", "auto_gaming", "auto_idle", "auto_wakeup", "auto_schedule", "skill"}
)


from host_brain.task_runtime.acceptance_effect_support import (
    ACCEPTANCE_EFFECT_DESCRIPTOR_INVALID,
    AcceptanceEffectCompileError,
    _action_preview_context,
    _canonical_json,
    _frozen_companion_scope,
    _json_mapping,
    _reject_worker_authority,
    _required,
    _spec,
    _trusted_foreground_command,
    build_acceptance_effect_intent_spec,
    build_action_preview_context,
)
from host_brain.task_runtime.operator_acceptance_effects import (
    _compile_operator_effects,
)


def compile_graph_acceptance_effects(
    *,
    command: HostCommand,
    work_order: Any,
    node_id: str,
    attempt_id: str,
    worker_commit: Mapping[str, Any],
) -> tuple[AcceptanceEffectIntentSpec, ...]:
    if not isinstance(command, HostCommand):
        raise AcceptanceEffectCompileError("HostCommand is required")
    node_ref = _required(node_id, "node_id")
    attempt_ref = _required(attempt_id, "attempt_id")
    commit = _json_mapping(worker_commit, field_name="worker_commit")
    if str(commit.get("decision") or "").strip().upper() != "APPROVE":
        raise AcceptanceEffectCompileError("only an approved worker projection may emit effects")
    narrative = _required(commit.get("narrative"), "worker_commit.narrative")
    if str(commit.get("request_id") or command.request_id) != command.request_id:
        raise AcceptanceEffectCompileError("worker projection request_id drifted")

    pack = _json_mapping(getattr(work_order, "input_pack", {}), field_name="work_order.input_pack")
    if command.surface in {
        HostCommandSurface.INTERNAL,
        HostCommandSurface.OPERATOR,
        HostCommandSurface.SCHEDULER,
    }:
        try:
            require_control_route_execution_policy(
                command=command,
                work_order=work_order,
            )
        except ControlRouteExecutionPolicyError as exc:
            raise AcceptanceEffectCompileError(exc.detail) from exc
    if command.route_type == "plan":
        metadata = _json_mapping(
            getattr(work_order, "metadata", {}),
            field_name="work_order.metadata",
        )
        raw_plan_policy = metadata.get(PLAN_EXECUTION_POLICY_METADATA_KEY)
        try:
            require_plan_result_effect_owner(
                policy=(
                    raw_plan_policy
                    if isinstance(raw_plan_policy, Mapping)
                    else {}
                ),
                node_id=node_ref,
            )
        except PlanExecutionPolicyError as exc:
            raise AcceptanceEffectCompileError(exc.detail) from exc
    if is_operator_state_control_command(command):
        return _compile_operator_effects(
            command=command, work_order=work_order, node_id=node_ref,
            attempt_id=attempt_ref, commit=commit, pack=pack,
        )
    if command.surface is HostCommandSurface.SCHEDULER or _proposes_scheduler_operation(
        commit
    ):
        return _compile_scheduler_operation(
            command=command,
            work_order=work_order,
            node_id=node_ref,
            attempt_id=attempt_ref,
            commit=commit,
            pack=pack,
        )
    if command.surface is HostCommandSurface.INTERNAL or _proposes_internal_result_commit(
        commit
    ):
        return _compile_internal_result_commit(
            command=command,
            work_order=work_order,
            node_id=node_ref,
            attempt_id=attempt_ref,
            commit=commit,
        )
    if command.surface is HostCommandSurface.OPERATOR:
        return _compile_operator_effects(
            command=command,
            work_order=work_order,
            node_id=node_ref,
            attempt_id=attempt_ref,
            commit=commit,
            pack=pack,
        )
    surface_message = str(
        pack.get("surface_user_message")
        or pack.get("user_message")
        or getattr(work_order, "objective", "")
        or ""
    ).strip()
    if not surface_message:
        raise AcceptanceEffectCompileError("accepted graph has no frozen surface user message")
    render_lang = str(
        pack.get("render_target_lang")
        or pack.get("prepared_render_target_lang")
        or "zh"
    ).strip() or "zh"
    request_body = _json_mapping(pack.get("request_body"), field_name="request_body")
    delivery_context = _json_mapping(pack.get("delivery_context"), field_name="delivery_context")
    emotion_context = _json_mapping(
        pack.get("emotion_context") or pack.get("prepared_emotion_context"),
        field_name="emotion_context",
    )
    bond_summary = _json_mapping(pack.get("bond_summary"), field_name="bond_summary")

    try:
        descriptors = agent_workspace_effects.validated_worker_effect_descriptors(
            commit
        )
    except agent_workspace_effects.AgentWorkspaceAcceptanceEffectError as exc:
        raise AcceptanceEffectCompileError(str(exc)) from exc
    foreground_descriptors = tuple(
        descriptor
        for descriptor in descriptors
        if descriptor["effect_type"] == "life.foreground_completed"
    )
    companion_subject_id, companion_workspace_id = _frozen_companion_scope(
        command=command,
        pack=pack,
    )
    foreground = _trusted_foreground_command(
        descriptors=foreground_descriptors,
        command=command,
        companion_subject_id=companion_subject_id,
        companion_workspace_id=companion_workspace_id,
        surface_message=surface_message,
        narrative=narrative,
        render_lang=render_lang,
        request_body=request_body,
        emotion_context=emotion_context,
        bond_summary=bond_summary,
        pack=pack,
    )
    preview_context = _action_preview_context(
        command=command,
        work_order=work_order,
        node_id=node_ref,
        worker_commit=commit,
    )
    raw_affective_plan = _json_mapping(
        foreground.get("payload"),
        field_name="foreground.payload",
    ).get("affective_turn_plan")
    if raw_affective_plan is None or raw_affective_plan == {}:
        # Compatibility/task routes that did not freeze a companion plan may
        # still accept their text result.  They cannot manufacture Voice or
        # Avatar effects from an empty placeholder.
        has_affective_plan = False
    elif not isinstance(raw_affective_plan, Mapping) or (
        raw_affective_plan.get("schema_version") != "affective_turn_plan.v1"
    ):
        raise AcceptanceEffectCompileError("affective turn plan is invalid")
    else:
        has_affective_plan = True
    companion_expression_effects: tuple[dict[str, Any], ...] = ()
    if has_affective_plan:
        if preview_context is None:
            raise AcceptanceEffectCompileError(
                "affective companion delivery requires a Runtime Context Preview binding"
            )
        try:
            companion_expression_effects = (
                compile_companion_expression_effect_payloads(
                    graph_id=command.graph_id,
                    graph_revision=command.graph_revision,
                    node_id=node_ref,
                    attempt_id=attempt_ref,
                    request_id=command.request_id,
                    principal_id=command.principal_id,
                    relationship_subject_id=companion_subject_id,
                    workspace_id=companion_workspace_id,
                    session_id=command.session_id,
                    node_grant_ref=str(preview_context["node_grant_ref"]),
                    foreground=foreground,
                )
            )
        except CompanionExpressionEffectError as exc:
            raise AcceptanceEffectCompileError(str(exc)) from exc
    user_turn = {
        "request_id": command.request_id,
        "session_id": command.session_id,
        # A HostCommand principal authorizes execution.  It is not necessarily
        # the relationship subject: Gateway and Edge requests commonly carry
        # a service principal.  Conversation/Life projections must remain
        # scoped to the immutable Runtime Context subject instead.
        "person_id": companion_subject_id,
        "content": surface_message,
        "ts_ms": command.created_at_ms,
        "lang": render_lang,
        "metadata": {
            # The dispatcher uses this immutable compiler-owned marker to
            # distinguish ordinary realtime chat from control/skill routes.
            # Worker output cannot move a graph onto the low-latency path.
            "route_type": command.route_type,
            "input_source": str(request_body.get("input_source") or ""),
            "reply_channel": str(delivery_context.get("reply_channel") or ""),
            "reply_target": str(delivery_context.get("reply_target") or ""),
            "attachments": list(request_body.get("attachments") or []),
            "multimodal_observations": dict(
                request_body.get("multimodal_observations") or {}
            ),
            "attachment_summary": str(request_body.get("attachment_summary") or ""),
            "source": "governed_task_acceptance",
            "authority_principal_id": command.principal_id,
        },
    }
    payloads: list[tuple[str, Mapping[str, Any]]] = []
    accepted_memory = _compile_explicit_memory_store_payload(
        command=command,
        work_order=work_order,
        node_id=node_ref,
        attempt_id=attempt_ref,
        pack=pack,
        person_id=companion_subject_id,
    )
    if accepted_memory is not None:
        payloads.append((EXPLICIT_MEMORY_STORE_EFFECT_TYPE, accepted_memory))
    try:
        agent_workspace_effect = (
            agent_workspace_effects.compile_agent_workspace_artifact_effect(
            descriptors=descriptors,
            command=command,
            work_order=work_order,
            node_id=node_ref,
            attempt_id=attempt_ref,
            worker_commit=commit,
            )
        )
    except agent_workspace_effects.AgentWorkspaceAcceptanceEffectError as exc:
        raise AcceptanceEffectCompileError(str(exc)) from exc
    if agent_workspace_effect is not None:
        payloads.append(
            (AGENT_WORKSPACE_ARTIFACT_EFFECT_TYPE, agent_workspace_effect)
        )
    payloads.extend([
        ("conversation.user_turn", user_turn),
        (
            "conversation.preference_signal",
            {
                "schema_version": PREFERENCE_SIGNAL_SCHEMA_V1,
                "operation_id": preference_operation_id(
                    graph_id=command.graph_id,
                    graph_revision=command.graph_revision,
                    node_id=node_ref,
                    attempt_id=attempt_ref,
                    request_id=command.request_id,
                ),
                **user_turn,
            },
        ),
        (
            "life.conversation_turn_recorded",
            {
                "schema_version": LIFE_CONVERSATION_PROJECTION_SCHEMA_V1,
                "command_id": conversation_life_command_id(command.request_id),
                **user_turn,
            },
        ),
        ("life.foreground_completed", foreground),
    ])
    payloads.extend(
        (COMPANION_EXPRESSION_ENQUEUE_EFFECT, payload)
        for payload in companion_expression_effects
    )
    reply_channel = str(delivery_context.get("reply_channel") or "").strip()
    reply_target = str(delivery_context.get("reply_target") or "").strip()
    if reply_channel and reply_target:
        payloads.append(
            (
                "surface.reply_delivery",
                {
                    "request_id": command.request_id,
                    "session_id": command.session_id,
                    "person_id": companion_subject_id,
                    "reply_channel": reply_channel,
                    "reply_target": reply_target,
                    "decision": "APPROVE",
                    "narrative": narrative,
                    "authority": {
                        "request_id": command.request_id,
                        "governor_grant_ref": command.governor_grant_ref,
                        "authority_kind": "governed_task_acceptance",
                        "authority_principal_id": command.principal_id,
                    },
                },
            )
        )

    if preview_context is not None:
        payloads = [
            (
                effect_type,
                {**dict(payload), "_action_preview_context": preview_context},
            )
            for effect_type, payload in payloads
        ]

    return tuple(
        _spec(
            effect_type=effect_type,
            payload=payload,
            command=command,
            node_id=node_ref,
            attempt_id=attempt_ref,
        )
        for effect_type, payload in payloads
    )


def _compile_explicit_memory_store_payload(
    *,
    command: HostCommand,
    work_order: Any,
    node_id: str,
    attempt_id: str,
    pack: Mapping[str, Any],
    person_id: str,
) -> dict[str, Any] | None:
    target_id, intent_id, args = _accepted_skill_node_input(
        command=command,
        work_order=work_order,
        node_id=node_id,
        pack=pack,
    )
    if target_id != "memory_store":
        return None
    content = _required(args.get("content"), "memory_store.content")
    if len(content) > 4_000:
        raise AcceptanceEffectCompileError("memory_store.content exceeds 4000 characters")
    memory_type = str(args.get("memory_type") or "episodic").strip().lower()
    if memory_type not in {"episodic", "semantic"}:
        raise AcceptanceEffectCompileError("memory_store.memory_type is invalid")
    raw_tags = args.get("tags")
    tags = (
        [str(tag).strip().lower() for tag in raw_tags if str(tag).strip()]
        if isinstance(raw_tags, list)
        else []
    )
    if len(tags) > 16 or any(len(tag) > 64 for tag in tags):
        raise AcceptanceEffectCompileError("memory_store.tags are invalid")
    try:
        importance = float(args.get("importance", 0.5))
    except (TypeError, ValueError) as exc:
        raise AcceptanceEffectCompileError("memory_store.importance is invalid") from exc
    if not 0.0 <= importance <= 1.0:
        raise AcceptanceEffectCompileError("memory_store.importance is outside 0..1")
    return {
        "schema_version": EXPLICIT_MEMORY_STORE_SCHEMA_V1,
        "operation_id": (
            f"memory-store://graph/{command.graph_id}/revision/{command.graph_revision}"
            f"/node/{node_id}/attempt/{attempt_id}"
        ),
        "request_id": command.request_id,
        "intent_id": intent_id,
        "session_id": command.session_id,
        "person_id": person_id,
        "content": content,
        "memory_type": memory_type,
        "tags": tags,
        "importance": importance,
        "source": str(args.get("source") or "explicit_user_request").strip(),
        "emotion_tag": str(args.get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL",
        "created_at_ms": int(command.created_at_ms),
    }


def _accepted_skill_node_input(
    *,
    command: HostCommand,
    work_order: Any,
    node_id: str,
    pack: Mapping[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    if command.route_type not in {"skill", "plan"}:
        return "", "", {}
    if command.route_type == "skill":
        intent = _json_mapping(pack.get("intent_draft"), field_name="intent_draft")
        return (
            str(command.target_id or "").strip(),
            _required(intent.get("id") or intent.get("intent_id"), "intent_draft.id"),
            _json_mapping(intent.get("args"), field_name="intent_draft.args"),
        )
    metadata = _json_mapping(getattr(work_order, "metadata", {}), field_name="work_order.metadata")
    lineage = _json_mapping(metadata.get("planning_lineage"), field_name="planning_lineage")
    bindings = lineage.get("node_bindings")
    if not isinstance(bindings, Sequence) or isinstance(bindings, (str, bytes, bytearray)):
        return "", "", {}
    binding = next(
        (
            dict(item)
            for item in bindings
            if isinstance(item, Mapping) and str(item.get("node_id") or "") == node_id
        ),
        None,
    )
    if binding is None:
        return "", "", {}
    candidate_id = str(binding.get("candidate_id") or "").strip()
    approved = _json_mapping(
        pack.get("approved_candidate_inputs"),
        field_name="approved_candidate_inputs",
    )
    candidate = _json_mapping(
        approved.get(candidate_id),
        field_name="approved_candidate_input",
    )
    return (
        str(binding.get("target_id") or "").strip(),
        _required(
            candidate.get("intent_id")
            or candidate.get("id")
            or f"plan-node:{command.graph_id}:{command.graph_revision}:{node_id}",
            "planned memory intent_id",
        ),
        _json_mapping(candidate.get("args"), field_name="approved_candidate_input.args"),
    )


def _proposes_scheduler_operation(commit: Mapping[str, Any]) -> bool:
    raw = commit.get("acceptance_effect_intents")
    return bool(
        isinstance(raw, Sequence)
        and not isinstance(raw, (str, bytes, bytearray))
        and any(
            isinstance(item, Mapping)
            and str(item.get("effect_type") or "").strip()
            == _SCHEDULER_OPERATION_EFFECT
            for item in raw
        )
    )


def _proposes_internal_result_commit(commit: Mapping[str, Any]) -> bool:
    raw = commit.get("acceptance_effect_intents")
    return bool(
        isinstance(raw, Sequence)
        and not isinstance(raw, (str, bytes, bytearray))
        and any(
            isinstance(item, Mapping)
            and str(item.get("effect_type") or "").strip()
            == _INTERNAL_RESULT_COMMIT_EFFECT
            for item in raw
        )
    )


def _compile_internal_result_commit(
    *,
    command: HostCommand,
    work_order: Any,
    node_id: str,
    attempt_id: str,
    commit: Mapping[str, Any],
) -> tuple[AcceptanceEffectIntentSpec, ...]:
    if command.surface is not HostCommandSurface.INTERNAL:
        raise AcceptanceEffectCompileError(
            "Internal Agent result commit requires Internal ingress"
        )
    if command.route_type != "delegate" or command.target_id != "agent_runtime":
        raise AcceptanceEffectCompileError(
            "Internal Agent result commit requires delegate:agent_runtime"
        )
    raw = commit.get("acceptance_effect_intents")
    if (
        not isinstance(raw, Sequence)
        or isinstance(raw, (str, bytes, bytearray))
        or len(raw) != 1
    ):
        raise AcceptanceEffectCompileError(
            "Internal Agent worker must propose exactly one result commit"
        )
    descriptor = _json_mapping(
        raw[0],
        field_name="Internal Agent result descriptor",
    )
    if set(descriptor) != {"effect_type", "commit_scope", "payload"} or (
        descriptor.get("effect_type") != _INTERNAL_RESULT_COMMIT_EFFECT
        or str(descriptor.get("commit_scope") or "").strip().upper() != "NODE"
    ):
        raise AcceptanceEffectCompileError(
            "Internal Agent worker proposed an unsupported result effect"
        )
    proposed = _json_mapping(
        descriptor.get("payload"),
        field_name="Internal Agent result proposal",
    )
    _reject_worker_authority(proposed)
    required = {
        "schema_version",
        "work_order_id",
        "target_id",
        "operation_fingerprint",
        "result_digest",
        "result",
    }
    if set(proposed) != required or proposed.get("schema_version") != (
        _INTERNAL_PROPOSAL_SCHEMA
    ):
        raise AcceptanceEffectCompileError(
            "Internal Agent result proposal fields drifted"
        )
    result = _json_mapping(
        proposed.get("result"),
        field_name="Internal Agent proposed result",
    )
    result_digest = "sha256:" + hashlib.sha256(
        json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    work_order_id = _required(
        getattr(work_order, "work_order_id", ""),
        "work_order.work_order_id",
    )
    operation_fingerprint = _required(
        getattr(work_order, "operation_fingerprint", ""),
        "work_order.operation_fingerprint",
    )
    worker_projection_ref = _required(
        commit.get("receipt_ref"),
        "worker_commit.receipt_ref",
    )
    if (
        not worker_projection_ref.startswith("worker-projection://")
        or proposed.get("work_order_id") != work_order_id
        or proposed.get("target_id") != "agent_runtime"
        or proposed.get("operation_fingerprint") != operation_fingerprint
        or operation_fingerprint != command.operation_fingerprint
        or proposed.get("result_digest") != result_digest
        or str(result.get("status") or "").strip().upper() != "EXECUTED"
        or str(getattr(work_order, "task_graph_id", "") or "")
        != command.graph_id
        or int(getattr(work_order, "graph_revision", 0) or 0)
        != command.graph_revision
        or str(getattr(work_order, "task_node_id", "") or "") != node_id
    ):
        raise AcceptanceEffectCompileError(
            "Internal Agent result proposal differs from canonical admission"
        )
    preview_context = _action_preview_context(
        command=command,
        work_order=work_order,
        node_id=node_id,
        worker_commit=commit,
    )
    if preview_context is None:
        raise AcceptanceEffectCompileError(
            "governed Internal Agent result has no Runtime Context preview binding"
        )
    payload = {
        "schema_version": _INTERNAL_EFFECT_SCHEMA,
        "command_id": command.command_id,
        "request_id": command.request_id,
        "work_order_id": work_order_id,
        "graph_id": command.graph_id,
        "graph_revision": command.graph_revision,
        "node_id": node_id,
        "attempt_id": attempt_id,
        "target_id": "agent_runtime",
        "operation_fingerprint": operation_fingerprint,
        "worker_projection_ref": worker_projection_ref,
        "result_digest": result_digest,
        "_action_preview_context": preview_context,
    }
    return (
        _spec(
            effect_type=_INTERNAL_RESULT_COMMIT_EFFECT,
            payload=payload,
            command=command,
            node_id=node_id,
            attempt_id=attempt_id,
        ),
    )


def _compile_scheduler_operation(
    *,
    command: HostCommand,
    work_order: Any,
    node_id: str,
    attempt_id: str,
    commit: Mapping[str, Any],
    pack: Mapping[str, Any],
) -> tuple[AcceptanceEffectIntentSpec, ...]:
    if command.surface not in {
        HostCommandSurface.SCHEDULER,
        HostCommandSurface.OPERATOR,
    }:
        raise AcceptanceEffectCompileError(
            "Scheduler operation may only originate from Scheduler or Operator ingress"
        )
    if command.route_type != "delegate" or not str(command.target_id or "").strip():
        raise AcceptanceEffectCompileError(
            "Scheduler operation requires one frozen delegate target"
        )
    raw = commit.get("acceptance_effect_intents")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise AcceptanceEffectCompileError(
            "Scheduler worker must propose exactly one operation effect"
        )
    if len(raw) != 1:
        raise AcceptanceEffectCompileError(
            "Scheduler worker must propose exactly one operation effect"
        )
    descriptor = _json_mapping(
        raw[0],
        field_name="Scheduler worker effect descriptor",
    )
    if set(descriptor) != {"effect_type", "commit_scope", "payload"}:
        raise AcceptanceEffectCompileError(
            "Scheduler descriptor may contain only effect_type, commit_scope and payload"
        )
    if (
        descriptor.get("effect_type") != _SCHEDULER_OPERATION_EFFECT
        or str(descriptor.get("commit_scope") or "").strip().upper() != "NODE"
    ):
        raise AcceptanceEffectCompileError(
            "Scheduler worker proposed an unsupported operation effect"
        )
    proposed = _json_mapping(
        descriptor.get("payload"),
        field_name="Scheduler operation plan",
    )
    _reject_worker_authority(proposed)
    required_plan_fields = {
        "schema_version",
        "occurrence_id",
        "definition_ref",
        "definition_revision",
        "worker_key",
        "operation_fingerprint",
    }
    if set(proposed) != required_plan_fields or proposed.get("schema_version") != (
        _SCHEDULER_PLAN_SCHEMA
    ):
        raise AcceptanceEffectCompileError("Scheduler operation plan fields drifted")
    occurrence_id = _required(pack.get("occurrence_id"), "input_pack.occurrence_id")
    definition_ref = _required(pack.get("definition_ref"), "input_pack.definition_ref")
    worker_key = _required(command.target_id, "command.target_id").lower()
    metadata = _json_mapping(
        getattr(work_order, "metadata", {}),
        field_name="work_order.metadata",
    )
    try:
        definition_revision = int(proposed.get("definition_revision"))
    except (TypeError, ValueError) as exc:
        raise AcceptanceEffectCompileError(
            "Scheduler definition_revision must be a positive integer"
        ) from exc
    operation_fingerprint = _required(
        proposed.get("operation_fingerprint"),
        "Scheduler operation_fingerprint",
    )
    if (
        proposed.get("occurrence_id") != occurrence_id
        or proposed.get("definition_ref") != definition_ref
        or definition_revision < 1
        or str(proposed.get("worker_key") or "").strip().lower() != worker_key
        or command.input_ref != occurrence_id
        or str(metadata.get("worker_key") or "").strip().lower() != worker_key
        or operation_fingerprint
        != str(getattr(work_order, "operation_fingerprint", "") or "").strip()
        or str(getattr(work_order, "task_graph_id", "") or "").strip()
        != command.graph_id
        or int(getattr(work_order, "graph_revision", 0) or 0)
        != command.graph_revision
    ):
        raise AcceptanceEffectCompileError(
            "Scheduler operation plan differs from canonical admission"
        )
    if command.surface is HostCommandSurface.OPERATOR and (
        str(metadata.get("operator_command_id") or "").strip()
        != "scheduler.task.trigger"
        or str(metadata.get("command_surface") or "").strip()
        != HostCommandSurface.OPERATOR.value
    ):
        raise AcceptanceEffectCompileError(
            "Operator Scheduler effect lacks scheduler.task.trigger authority"
        )
    preview_context = _action_preview_context(
        command=command,
        work_order=work_order,
        node_id=node_id,
        worker_commit=commit,
    )
    if preview_context is None:
        raise AcceptanceEffectCompileError(
            "governed Scheduler effect has no Runtime Context preview binding"
        )
    payload = {
        "schema_version": _SCHEDULER_EFFECT_SCHEMA,
        "occurrence_id": occurrence_id,
        "definition_ref": definition_ref,
        "definition_revision": definition_revision,
        "worker_key": worker_key,
        "operation_fingerprint": operation_fingerprint,
        "command_id": command.command_id,
        "request_id": command.request_id,
        "work_order_id": str(getattr(work_order, "work_order_id", "") or ""),
        "graph_id": command.graph_id,
        "graph_revision": command.graph_revision,
        "node_id": node_id,
        "attempt_id": attempt_id,
        "_action_preview_context": preview_context,
    }
    return (
        _spec(
            effect_type=_SCHEDULER_OPERATION_EFFECT,
            payload=payload,
            command=command,
            node_id=node_id,
            attempt_id=attempt_id,
        ),
    )


__all__ = [
    "ACCEPTANCE_EFFECT_DESCRIPTOR_INVALID",
    "AcceptanceEffectCompileError",
    "build_acceptance_effect_intent_spec",
    "build_action_preview_context",
    "compile_graph_acceptance_effects",
]
