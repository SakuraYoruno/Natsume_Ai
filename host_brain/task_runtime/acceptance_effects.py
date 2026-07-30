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


class AcceptanceEffectCompileError(RuntimeError):
    def __init__(self, detail: str) -> None:
        self.reason_code = ACCEPTANCE_EFFECT_DESCRIPTOR_INVALID
        self.detail = str(detail or "acceptance effect descriptor is invalid")
        super().__init__(f"{self.reason_code}: {self.detail}")


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
    foreground = _trusted_foreground_command(
        descriptors=foreground_descriptors,
        command=command,
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
    has_affective_plan = isinstance(
        _json_mapping(foreground.get("payload"), field_name="foreground.payload").get(
            "affective_turn_plan"
        ),
        Mapping,
    )
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
        "person_id": command.principal_id,
        "content": surface_message,
        "ts_ms": command.created_at_ms,
        "lang": render_lang,
        "metadata": {
            "input_source": str(request_body.get("input_source") or ""),
            "reply_channel": str(delivery_context.get("reply_channel") or ""),
            "reply_target": str(delivery_context.get("reply_target") or ""),
            "attachments": list(request_body.get("attachments") or []),
            "multimodal_observations": dict(
                request_body.get("multimodal_observations") or {}
            ),
            "attachment_summary": str(request_body.get("attachment_summary") or ""),
            "source": "governed_task_acceptance",
        },
    }
    payloads: list[tuple[str, Mapping[str, Any]]] = []
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
                    "person_id": command.principal_id,
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


def _compile_operator_effects(
    *,
    command: HostCommand,
    work_order: Any,
    node_id: str,
    attempt_id: str,
    commit: Mapping[str, Any],
    pack: Mapping[str, Any],
) -> tuple[AcceptanceEffectIntentSpec, ...]:
    if command.route_type != "delegate":
        raise AcceptanceEffectCompileError("unsupported governed Operator route")
    if command.target_id == _OPERATOR_CONTROL_TARGET:
        return compile_operator_control_acceptance_effect(
            command=command,
            work_order=work_order,
            node_id=node_id,
            attempt_id=attempt_id,
            commit=commit,
            pack=pack,
        )
    if is_operator_state_control_command(command):
        return compile_operator_state_control_acceptance_effect(
            command=command,
            work_order=work_order,
            node_id=node_id,
            attempt_id=attempt_id,
            commit=commit,
            pack=pack,
        )
    if command.target_id == _OPERATOR_TERMINAL_TARGET:
        return _compile_operator_terminal_effect(
            command=command,
            work_order=work_order,
            node_id=node_id,
            attempt_id=attempt_id,
            commit=commit,
            pack=pack,
        )
    if command.target_id == _OPERATOR_OPERATION_RECONCILIATION_TARGET:
        return _compile_operator_operation_reconciliation_effect(
            command=command,
            work_order=work_order,
            node_id=node_id,
            attempt_id=attempt_id,
            commit=commit,
            pack=pack,
        )
    if command.target_id == _OPERATOR_EFFECT_RECONCILIATION_TARGET:
        return _compile_operator_effect_reconciliation_effect(
            command=command,
            work_order=work_order,
            node_id=node_id,
            attempt_id=attempt_id,
            commit=commit,
            pack=pack,
        )
    if command.target_id == _OPERATOR_EDGE_TARGET:
        return _compile_operator_edge_effect(
            command=command,
            work_order=work_order,
            node_id=node_id,
            attempt_id=attempt_id,
            commit=commit,
            pack=pack,
        )
    if command.target_id != _OPERATOR_SYS_MODE_TARGET:
        raise AcceptanceEffectCompileError("unsupported governed Operator route")
    raw = commit.get("acceptance_effect_intents")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise AcceptanceEffectCompileError("Operator worker must propose one effect")
    if len(raw) != 1:
        raise AcceptanceEffectCompileError("Operator worker must propose exactly one effect")
    descriptor = _json_mapping(raw[0], field_name="Operator worker effect descriptor")
    if set(descriptor) != {"effect_type", "commit_scope", "payload"}:
        raise AcceptanceEffectCompileError(
            "Operator descriptor may contain only effect_type, commit_scope and payload"
        )
    if (
        descriptor.get("effect_type") != _OPERATOR_SYS_MODE_EFFECT
        or str(descriptor.get("commit_scope") or "").strip().upper() != "NODE"
    ):
        raise AcceptanceEffectCompileError("Operator worker proposed an unsupported effect")
    payload = _json_mapping(
        descriptor.get("payload"),
        field_name="Operator worker effect payload",
    )
    _reject_worker_authority(payload)
    required_keys = {
        "operator_command_id",
        "request_id",
        "target_mode",
        "source",
        "reason",
        "event_id",
        "runtime_scope_id",
    }
    if set(payload) != required_keys:
        raise AcceptanceEffectCompileError("Operator effect payload fields drifted")
    target_mode = str(payload.get("target_mode") or "").strip().lower()
    source = str(payload.get("source") or "").strip().lower()
    reason = str(payload.get("reason") or "").strip()
    runtime_scope_id = str(payload.get("runtime_scope_id") or "").strip()
    expected_event_id = (
        "obs-sys-mode:"
        + hashlib.sha256(command.request_id.encode("utf-8")).hexdigest()
    )
    if (
        payload.get("operator_command_id") != "sys_mode.switch"
        or payload.get("request_id") != command.request_id
        or target_mode not in _SYS_MODES
        or source not in _SYS_MODE_SOURCES
        or not reason
        or payload.get("event_id") != expected_event_id
        or not runtime_scope_id
        or len(runtime_scope_id.encode("utf-8")) > 512
        or any(
            ord(character) < 32 or ord(character) == 127
            for character in runtime_scope_id
        )
    ):
        raise AcceptanceEffectCompileError("Operator effect identity or value is invalid")
    intent = _json_mapping(pack.get("intent_draft"), field_name="intent_draft")
    args = _json_mapping(intent.get("args"), field_name="intent_draft.args")
    if (
        str(intent.get("action") or "").strip()
        != "operator_sys_mode_transition"
        or str(intent.get("target") or "").strip().lower() != "system"
        or str(intent.get("route") or "").strip().lower() != "delegate"
        or str(intent.get("target_id") or "").strip() != _OPERATOR_SYS_MODE_TARGET
        or str(args.get("operator_command_id") or "").strip() != "sys_mode.switch"
        or str(args.get("target_mode") or "").strip().lower() != target_mode
        or str(args.get("source") or "").strip().lower() != source
        or str(args.get("reason") or "").strip() != reason
    ):
        raise AcceptanceEffectCompileError("Operator effect differs from frozen intent")
    preview_context = _action_preview_context(
        command=command,
        work_order=work_order,
        node_id=node_id,
        worker_commit=commit,
    )
    if preview_context is None:
        raise AcceptanceEffectCompileError(
            "governed Operator effect has no Runtime Context preview binding"
        )
    return (
        _spec(
            effect_type=_OPERATOR_SYS_MODE_EFFECT,
            payload={**payload, "_action_preview_context": preview_context},
            command=command,
            node_id=node_id,
            attempt_id=attempt_id,
        ),
    )


def _compile_operator_edge_effect(
    *,
    command: HostCommand,
    work_order: Any,
    node_id: str,
    attempt_id: str,
    commit: Mapping[str, Any],
    pack: Mapping[str, Any],
) -> tuple[AcceptanceEffectIntentSpec, ...]:
    raw = commit.get("acceptance_effect_intents")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise AcceptanceEffectCompileError("Edge Operator must propose one effect")
    if len(raw) != 1:
        raise AcceptanceEffectCompileError(
            "Edge Operator must propose exactly one effect"
        )
    descriptor = _json_mapping(
        raw[0],
        field_name="Edge Operator effect descriptor",
    )
    if set(descriptor) != {"effect_type", "commit_scope", "payload"}:
        raise AcceptanceEffectCompileError(
            "Edge Operator descriptor fields drifted"
        )
    if (
        descriptor.get("effect_type") != _OPERATOR_EDGE_EFFECT
        or str(descriptor.get("commit_scope") or "").strip().upper() != "NODE"
    ):
        raise AcceptanceEffectCompileError(
            "Edge Operator proposed an unsupported effect"
        )
    proposed = _json_mapping(
        descriptor.get("payload"),
        field_name="Edge Operator effect payload",
    )
    _reject_worker_authority(proposed)
    required = {
        "operator_command_id",
        "request_id",
        "command_type",
        "target_agent",
        "payload",
        "runtime_scope_id",
    }
    if set(proposed) != required:
        raise AcceptanceEffectCompileError(
            "Edge Operator effect payload fields drifted"
        )
    device_payload = _json_mapping(
        proposed.get("payload"),
        field_name="Edge Operator device payload",
    )
    if set(device_payload) - {"emotion_tag", "intensity"}:
        raise AcceptanceEffectCompileError(
            "Edge Operator device payload contains unsupported fields"
        )
    emotion_tag = str(device_payload.get("emotion_tag") or "").strip()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", emotion_tag):
        raise AcceptanceEffectCompileError(
            "Edge Operator emotion_tag is not canonical"
        )
    canonical_device_payload: dict[str, Any] = {"emotion_tag": emotion_tag}
    if "intensity" in device_payload:
        intensity = device_payload.get("intensity")
        if (
            isinstance(intensity, bool)
            or not isinstance(intensity, (int, float))
            or not math.isfinite(float(intensity))
            or not 0.0 <= float(intensity) <= 1.0
        ):
            raise AcceptanceEffectCompileError(
                "Edge Operator intensity is invalid"
            )
        canonical_device_payload["intensity"] = float(intensity)
    if device_payload != canonical_device_payload:
        raise AcceptanceEffectCompileError(
            "Edge Operator device payload is not canonical"
        )

    intent = _json_mapping(pack.get("intent_draft"), field_name="intent_draft")
    args = _json_mapping(intent.get("args"), field_name="intent_draft.args")
    metadata = _json_mapping(
        getattr(work_order, "metadata", {}),
        field_name="work_order.metadata",
    )
    runtime_scope_id = str(proposed.get("runtime_scope_id") or "").strip()
    if (
        proposed.get("operator_command_id") != "edge.command.push"
        or proposed.get("request_id") != command.request_id
        or proposed.get("command_type") != "emotion_state_command"
        or proposed.get("target_agent") != "edge.somatic"
        or not runtime_scope_id
        or len(runtime_scope_id.encode("utf-8")) > 512
        or any(
            ord(character) < 32 or ord(character) == 127
            for character in runtime_scope_id
        )
        or str(intent.get("action") or "").strip()
        != "operator_edge_command_push"
        or str(intent.get("target") or "").strip().lower() != "edge"
        or str(intent.get("route") or "").strip().lower() != "delegate"
        or str(intent.get("target_id") or "").strip()
        != _OPERATOR_EDGE_TARGET
        or str(args.get("operator_command_id") or "").strip()
        != "edge.command.push"
        or str(args.get("command_type") or "").strip()
        != "emotion_state_command"
        or str(args.get("target_agent") or "").strip() != "edge.somatic"
        or str(args.get("embodiment_runtime_scope_id") or "").strip()
        != runtime_scope_id
        or _json_mapping(
            args.get("payload"),
            field_name="intent_draft.args.payload",
        )
        != canonical_device_payload
        or str(metadata.get("operator_command_id") or "").strip()
        != "edge.command.push"
    ):
        raise AcceptanceEffectCompileError(
            "Edge Operator effect differs from frozen intent"
        )

    preview_context = _action_preview_context(
        command=command,
        work_order=work_order,
        node_id=node_id,
        worker_commit=commit,
    )
    if preview_context is None:
        raise AcceptanceEffectCompileError(
            "governed Edge effect has no Runtime Context preview binding"
        )
    identity_digest = hashlib.sha256(
        _canonical_json(
            {
                "graph_id": command.graph_id,
                "graph_revision": command.graph_revision,
                "node_id": node_id,
                "attempt_id": attempt_id,
                "request_id": command.request_id,
                "command_type": "emotion_state_command",
                "target_agent": "edge.somatic",
                "payload": canonical_device_payload,
            }
        ).encode("utf-8")
    ).hexdigest()
    issued_at_ms = int(command.created_at_ms)
    embodiment_command = build_embodiment_command(
        kind="avatar",
        action="set_expression",
        payload=canonical_device_payload,
        authority_ref=f"authority://task-acceptance/sha256/{identity_digest}",
        grant_ref=str(preview_context["node_grant_ref"]),
        deadline_ms=issued_at_ms + 7 * 24 * 60 * 60 * 1000,
        idempotency_key=f"task-edge-effect:{identity_digest}",
        request_id=command.request_id,
        issued_at_ms=issued_at_ms,
    )
    payload = {
        "operator_command_id": "edge.command.push",
        "request_id": command.request_id,
        "command_type": "emotion_state_command",
        "target_agent": "edge.somatic",
        "runtime_scope_id": runtime_scope_id,
        "embodiment_command": embodiment_command.as_dict(),
        "_action_preview_context": preview_context,
    }
    return (
        _spec(
            effect_type=_OPERATOR_EDGE_EFFECT,
            payload=payload,
            command=command,
            node_id=node_id,
            attempt_id=attempt_id,
        ),
    )


def _compile_operator_terminal_effect(
    *,
    command: HostCommand,
    work_order: Any,
    node_id: str,
    attempt_id: str,
    commit: Mapping[str, Any],
    pack: Mapping[str, Any],
) -> tuple[AcceptanceEffectIntentSpec, ...]:
    raw = commit.get("acceptance_effect_intents")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise AcceptanceEffectCompileError("terminal Operator must propose one effect")
    if len(raw) != 1:
        raise AcceptanceEffectCompileError(
            "terminal Operator must propose exactly one effect"
        )
    descriptor = _json_mapping(
        raw[0], field_name="terminal Operator effect descriptor"
    )
    if set(descriptor) != {"effect_type", "commit_scope", "payload"}:
        raise AcceptanceEffectCompileError(
            "terminal Operator descriptor fields drifted"
        )
    if (
        descriptor.get("effect_type") != _OPERATOR_TERMINAL_EFFECT
        or str(descriptor.get("commit_scope") or "").strip().upper() != "NODE"
    ):
        raise AcceptanceEffectCompileError(
            "terminal Operator proposed an unsupported effect"
        )
    payload = _json_mapping(
        descriptor.get("payload"),
        field_name="terminal Operator effect payload",
    )
    _reject_worker_authority(payload)
    required_keys = {
        "operator_command_id",
        "request_id",
        "case_id",
        "signed_command_ref",
        "signed_command_sha256",
    }
    if set(payload) != required_keys:
        raise AcceptanceEffectCompileError(
            "terminal Operator effect payload fields drifted"
        )
    intent = _json_mapping(pack.get("intent_draft"), field_name="intent_draft")
    args = _json_mapping(intent.get("args"), field_name="intent_draft.args")
    expected = {
        "operator_command_id": "task_terminal.reconcile",
        "case_id": str(args.get("case_id") or "").strip(),
        "signed_command_ref": str(args.get("signed_command_ref") or "").strip(),
        "signed_command_sha256": str(
            args.get("signed_command_sha256") or ""
        ).strip(),
    }
    if (
        payload.get("request_id") != command.request_id
        or {key: payload.get(key) for key in expected} != expected
        or not expected["case_id"]
        or not re.fullmatch(
            r"terminal-command://sha256/[0-9a-f]{64}",
            expected["signed_command_ref"],
        )
        or not re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            expected["signed_command_sha256"],
        )
        or str(intent.get("action") or "").strip()
        != "operator_task_terminal_reconciliation"
        or str(intent.get("target") or "").strip().lower() != "system"
        or str(intent.get("route") or "").strip().lower() != "delegate"
        or str(intent.get("target_id") or "").strip()
        != _OPERATOR_TERMINAL_TARGET
    ):
        raise AcceptanceEffectCompileError(
            "terminal Operator effect differs from frozen intent"
        )
    preview_context = _action_preview_context(
        command=command,
        work_order=work_order,
        node_id=node_id,
        worker_commit=commit,
    )
    if preview_context is None:
        raise AcceptanceEffectCompileError(
            "terminal Operator effect has no Runtime Context preview binding"
        )
    return (
        _spec(
            effect_type=_OPERATOR_TERMINAL_EFFECT,
            payload={**payload, "_action_preview_context": preview_context},
            command=command,
            node_id=node_id,
            attempt_id=attempt_id,
        ),
    )


def _compile_operator_operation_reconciliation_effect(
    *,
    command: HostCommand,
    work_order: Any,
    node_id: str,
    attempt_id: str,
    commit: Mapping[str, Any],
    pack: Mapping[str, Any],
) -> tuple[AcceptanceEffectIntentSpec, ...]:
    intent = _json_mapping(pack.get("intent_draft"), field_name="intent_draft")
    args = _json_mapping(intent.get("args"), field_name="intent_draft.args")
    try:
        payload = validate_operation_reconciliation_proposal(
            raw_effects=commit.get("acceptance_effect_intents"),
            frozen_intent=intent,
            frozen_args=args,
            work_order_metadata=_json_mapping(
                getattr(work_order, "metadata", {}),
                field_name="work_order.metadata",
            ),
            request_id=command.request_id,
        )
    except ValueError as exc:
        raise AcceptanceEffectCompileError(str(exc)) from exc
    _reject_worker_authority(payload)
    preview_context = _action_preview_context(
        command=command,
        work_order=work_order,
        node_id=node_id,
        worker_commit=commit,
    )
    if preview_context is None:
        raise AcceptanceEffectCompileError(
            "Operation reconciliation has no Runtime Context preview binding"
        )
    return (
        _spec(
            effect_type=_OPERATOR_OPERATION_RECONCILIATION_EFFECT,
            payload={**payload, "_action_preview_context": preview_context},
            command=command,
            node_id=node_id,
            attempt_id=attempt_id,
        ),
    )


def _compile_operator_effect_reconciliation_effect(
    *,
    command: HostCommand,
    work_order: Any,
    node_id: str,
    attempt_id: str,
    commit: Mapping[str, Any],
    pack: Mapping[str, Any],
) -> tuple[AcceptanceEffectIntentSpec, ...]:
    raw = commit.get("acceptance_effect_intents")
    if not isinstance(raw, Sequence) or isinstance(
        raw,
        (str, bytes, bytearray),
    ):
        raise AcceptanceEffectCompileError(
            "Acceptance Effect reconciliation Operator must propose one effect"
        )
    if len(raw) != 1:
        raise AcceptanceEffectCompileError(
            "Acceptance Effect reconciliation Operator must propose exactly "
            "one effect"
        )
    descriptor = _json_mapping(
        raw[0],
        field_name="Acceptance Effect reconciliation descriptor",
    )
    if set(descriptor) != {"effect_type", "commit_scope", "payload"}:
        raise AcceptanceEffectCompileError(
            "Acceptance Effect reconciliation descriptor fields drifted"
        )
    if (
        descriptor.get("effect_type")
        != _OPERATOR_EFFECT_RECONCILIATION_EFFECT
        or str(descriptor.get("commit_scope") or "").strip().upper()
        != "NODE"
    ):
        raise AcceptanceEffectCompileError(
            "Acceptance Effect reconciliation proposed an unsupported effect"
        )
    payload = _json_mapping(
        descriptor.get("payload"),
        field_name="Acceptance Effect reconciliation payload",
    )
    _reject_worker_authority(payload)
    intent = _json_mapping(pack.get("intent_draft"), field_name="intent_draft")
    args = _json_mapping(intent.get("args"), field_name="intent_draft.args")
    request_fields = {
        "evidence_ref",
        "evidence_digest",
        "effect_id",
        "case_id",
        "purpose",
        "outcome",
        "reconciler_actor_id",
    }
    required_keys = {
        "operator_command_id",
        "request_id",
        *request_fields,
    }
    expected = {
        "operator_command_id": "task_effect.reconcile",
        "request_id": command.request_id,
        **{field: args.get(field) for field in request_fields},
    }
    evidence_ref = str(expected.get("evidence_ref") or "")
    metadata = _json_mapping(
        getattr(work_order, "metadata", {}),
        field_name="work_order.metadata",
    )
    if (
        set(payload) != required_keys
        or payload != expected
        or not re.fullmatch(
            r"effect-reconcile-evidence://sha256/[0-9a-f]{64}",
            evidence_ref,
        )
        or str(expected.get("evidence_digest") or "")
        != "sha256:" + evidence_ref.rsplit("/", 1)[-1]
        or not re.fullmatch(
            r"[a-z][a-z0-9+.-]{1,31}://\S+",
            str(expected.get("effect_id") or ""),
        )
        or not re.fullmatch(
            r"[a-z][a-z0-9+.-]{1,31}://\S+",
            str(expected.get("case_id") or ""),
        )
        or str(expected.get("purpose") or "")
        != "GENERAL_UNKNOWN_EFFECT"
        or str(expected.get("outcome") or "")
        not in {"NO_SIDE_EFFECT", "SIDE_EFFECT_CONFIRMED"}
        or not str(expected.get("reconciler_actor_id") or "").strip()
        or str(intent.get("action") or "").strip()
        != "operator_task_effect_reconciliation"
        or str(intent.get("target") or "").strip().lower()
        != "task_runtime"
        or str(intent.get("route") or "").strip().lower() != "delegate"
        or str(intent.get("target_id") or "").strip()
        != _OPERATOR_EFFECT_RECONCILIATION_TARGET
        or str(metadata.get("operator_command_id") or "").strip()
        != "task_effect.reconcile"
    ):
        raise AcceptanceEffectCompileError(
            "Acceptance Effect reconciliation differs from frozen intent"
        )
    preview_context = _action_preview_context(
        command=command,
        work_order=work_order,
        node_id=node_id,
        worker_commit=commit,
    )
    if preview_context is None:
        raise AcceptanceEffectCompileError(
            "Acceptance Effect reconciliation has no Runtime Context preview "
            "binding"
        )
    return (
        _spec(
            effect_type=_OPERATOR_EFFECT_RECONCILIATION_EFFECT,
            payload={**payload, "_action_preview_context": preview_context},
            command=command,
            node_id=node_id,
            attempt_id=attempt_id,
        ),
    )


def _action_preview_context(
    *,
    command: HostCommand,
    work_order: Any,
    node_id: str,
    worker_commit: Mapping[str, Any],
) -> dict[str, Any] | None:
    pack = getattr(work_order, "input_pack", None)
    if not isinstance(pack, Mapping) or pack.get("runtime_context_schema") != "natsume.runtime-context.request.v1":
        return None
    audit = worker_commit.get("audit_refs")
    audit_map = dict(audit) if isinstance(audit, Mapping) else {}
    node_grant_ref = str(audit_map.get("node_grant_ref") or "").strip()
    node_grant_digest = str(audit_map.get("node_grant_digest") or "").strip().lower()
    if not node_grant_ref or not node_grant_digest.startswith("sha256:"):
        raise AcceptanceEffectCompileError(
            "runtime-context effect has no exact node grant preview binding"
        )
    try:
        context_digest = context_pack_digest_from_work_order(work_order)
    except ActionPreviewAuthorityError as exc:
        raise AcceptanceEffectCompileError(exc.detail) from exc
    scene = pack.get("scene_identity_v2")
    if not isinstance(scene, Mapping):
        raise AcceptanceEffectCompileError("runtime-context effect has no exact Scene identity")
    return {
        "schema_version": "Natsume.acceptance_effect.action_preview_context.v1",
        "context_pack_digest": context_digest,
        "scene_identity": action_preview_scene_identity(
            _json_mapping(scene, field_name="scene_identity_v2")
        ),
        "task_graph_id": command.graph_id,
        "graph_revision": command.graph_revision,
        "task_node_id": node_id,
        "parent_governor_grant_ref": command.governor_grant_ref,
        "node_grant_ref": node_grant_ref,
        "node_grant_digest": node_grant_digest,
    }


def _trusted_foreground_command(
    *,
    descriptors: Sequence[Mapping[str, Any]],
    command: HostCommand,
    surface_message: str,
    narrative: str,
    render_lang: str,
    request_body: Mapping[str, Any],
    emotion_context: Mapping[str, Any],
    bond_summary: Mapping[str, Any],
    pack: Mapping[str, Any],
) -> dict[str, Any]:
    if descriptors:
        foreground = deepcopy(dict(descriptors[0]["payload"]))
        if str(foreground.get("command_type") or "") != "foreground_completed":
            raise AcceptanceEffectCompileError("worker foreground command_type drifted")
        payload = _json_mapping(foreground.get("payload"), field_name="foreground.payload")
    else:
        payload = {}
        foreground = {"payload": payload}

    completed_at_ms = int(foreground.get("created_at_ms") or command.created_at_ms)
    projection_payload = {
        "request_id": command.request_id,
        "session_id": command.session_id,
        "person_id": command.principal_id,
        "user_message": surface_message,
        "assistant_reply": narrative,
        "narrative": narrative,
        "request_ts_ms": command.created_at_ms,
        "ts_ms": command.created_at_ms,
        "render_target_lang": render_lang,
        "emotion_context": dict(emotion_context),
        "bond_summary": dict(bond_summary),
        "reply_channel": None,
        "reply_target": None,
        "retrieval_summary": dict(pack.get("retrieval_summary") or {}),
        "retrieval_evidence_items": list(pack.get("retrieval_evidence_items") or []),
        "pii_ingress_stats": dict(pack.get("pii_ingress_stats") or {}),
        "motive_state": dict(pack.get("motive_state") or {}),
        "attachments": list(request_body.get("attachments") or []),
        "multimodal_observations": dict(
            request_body.get("multimodal_observations") or {}
        ),
        "attachment_summary": str(request_body.get("attachment_summary") or ""),
        "trace_stage_meta": {
            "governed_task_acceptance": True,
            "surface_main_chain_used": bool(descriptors),
            "reply_source": "governed_task_runtime",
            "life_projection_mode": True,
        },
    }
    existing_projection = next(
        (
            dict(item)
            for item in list(payload.get("projection_intents") or [])
            if isinstance(item, Mapping) and item.get("target") == "surface.post_process"
        ),
        None,
    )
    if existing_projection is not None:
        rich = _json_mapping(existing_projection.get("payload"), field_name="surface.post_process")
        rich.update(projection_payload)
        projection_payload = rich
    canonical_payload = {
        **payload,
        "request_id": command.request_id,
        "session_id": command.session_id,
        "person_id": command.principal_id,
        "user_message": surface_message,
        "assistant_reply": narrative,
        "completed_at_ms": completed_at_ms,
        "state_patch": {
            "clock": {"last_user_seen_ms": completed_at_ms, "idle_minutes": 0},
            "continuity": {"last_interaction_digest": surface_message[:160]},
        },
        "emotion_context": dict(emotion_context),
        "bond_summary": dict(bond_summary),
        "projection_intents": [
            {"target": "surface.post_process", "payload": projection_payload}
        ],
        # Transport delivery is a separate acceptance effect with independently
        # rebuilt authority and idempotency.
        "deliveries": [],
    }
    return {
        "command_type": "foreground_completed",
        "person_id": command.principal_id,
        "command_id": f"life::foreground_completed::{command.request_id}",
        "correlation_id": command.request_id,
        "payload": canonical_payload,
        "created_at_ms": completed_at_ms,
        "metadata": {
            "source": "governed_task_acceptance",
            "reply_sendable": True,
            "reply_persistable": True,
            "delivery_authorized": False,
        },
    }


def _spec(
    *,
    effect_type: str,
    payload: Mapping[str, Any],
    command: HostCommand,
    node_id: str,
    attempt_id: str,
) -> AcceptanceEffectIntentSpec:
    if not _EFFECT_TYPE.fullmatch(effect_type):
        raise AcceptanceEffectCompileError(f"invalid effect_type: {effect_type}")
    canonical = _canonical_json(payload)
    payload_hex = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    binding_hex = hashlib.sha256(
        _canonical_json(
            {
                "effect_type": effect_type,
                "graph_id": command.graph_id,
                "graph_revision": command.graph_revision,
                "node_id": node_id,
                "attempt_id": attempt_id,
                "payload_sha256": f"sha256:{payload_hex}",
            }
        ).encode("utf-8")
    ).hexdigest()
    return AcceptanceEffectIntentSpec(
        effect_id=f"acceptance-effect://sha256/{binding_hex}",
        graph_id=command.graph_id,
        graph_revision=command.graph_revision,
        node_id=node_id,
        attempt_id=attempt_id,
        commit_scope=AcceptanceEffectCommitScope.GRAPH,
        effect_type=effect_type,
        payload_ref=f"acceptance-payload://sha256/{payload_hex}",
        payload=json.loads(canonical),
        payload_sha256=f"sha256:{payload_hex}",
        idempotency_key=f"acceptance-idempotency://sha256/{binding_hex}",
    )


def _reject_worker_authority(value: Any, *, path: str = "payload") -> None:
    forbidden = forbidden_worker_authority_path(value, path=path)
    if forbidden is not None:
        raise AcceptanceEffectCompileError(f"worker effect contains forbidden authority field: {forbidden}")


def _json_mapping(value: Any, *, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise AcceptanceEffectCompileError(f"{field_name} must be a mapping")
    try:
        return json.loads(_canonical_json(value))
    except (TypeError, ValueError) as exc:
        raise AcceptanceEffectCompileError(f"{field_name} must be canonical JSON") from exc


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _required(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise AcceptanceEffectCompileError(f"{field_name} is required")
    return text


# Shared compiler primitives for route-specific graph-Acceptance modules.
build_action_preview_context = _action_preview_context
build_acceptance_effect_intent_spec = _spec


__all__ = [
    "ACCEPTANCE_EFFECT_DESCRIPTOR_INVALID",
    "AcceptanceEffectCompileError",
    "build_acceptance_effect_intent_spec",
    "build_action_preview_context",
    "compile_graph_acceptance_effects",
]
