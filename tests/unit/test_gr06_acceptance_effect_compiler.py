from __future__ import annotations

import hashlib
from copy import deepcopy

import pytest

from host_brain.runtime.context_contracts import WorldFactV2, WorldSnapshotV2
from host_brain.contracts.agent_runtime.control_route_execution_policy import (
    CONTROL_ROUTE_EXECUTION_POLICY_KEY,
    freeze_control_route_execution_policy,
)
from host_brain.orchestration.host_route_contracts import (
    build_direct_host_route_contracts,
)
from host_brain.digital_life.affective_expression_delivery import (
    build_buffered_expression_delivery,
)
from host_brain.digital_life.affective_turn_planner import (
    build_affective_turn_guidance,
)
from host_brain.persistence.task_repository import AcceptanceEffectCommitScope
from host_brain.task_runtime.acceptance_effects import (
    ACCEPTANCE_EFFECT_DESCRIPTOR_INVALID,
    AcceptanceEffectCompileError,
    compile_graph_acceptance_effects,
)
from natsume_contracts.task import HostCommandSurface
from natsume_contracts.life import digest_json


_OPERATOR_REQUEST_ID = "request-gr06-operator-sys-mode"
_OPERATOR_TARGET = "operator_sys_mode_switch"
_OPERATOR_NODE_GRANT_REF = "node-grant://operator-sys-mode"
_OPERATOR_NODE_GRANT_DIGEST = "sha256:" + "a" * 64
_EDGE_REQUEST_ID = "request-gr06-operator-edge"
_EDGE_TARGET = "operator_edge_command_push"
_EDGE_NODE_GRANT_REF = "node-grant://operator-edge"
_EDGE_NODE_GRANT_DIGEST = "sha256:" + "c" * 64
_OPERATION_REQUEST_ID = "request-gr06-operation-reconcile"
_OPERATION_TARGET = "operator_task_operation_reconcile"
_OPERATION_NODE_GRANT_REF = "node-grant://operation-reconcile"
_OPERATION_NODE_GRANT_DIGEST = "sha256:" + "e" * 64
_OPERATION_EVIDENCE_DIGEST = "sha256:" + "d" * 64
_OPERATION_EVIDENCE_REF = (
    "operation-reconcile-evidence://sha256/" + "d" * 64
)
_OPERATION_RECOVERY_CASE_REF = (
    "operation-recovery-hold://sha256/" + "9" * 64
)
_OPERATION_RECOVERY_CASE_DIGEST = "sha256:" + "9" * 64


def _control_policy(command_id: str, target_id: str) -> dict:
    return freeze_control_route_execution_policy(
        surface=HostCommandSurface.OPERATOR,
        control_command_id=command_id,
        target_id=target_id,
        review_required=True,
    )


def _contracts():
    governor = {
        "request_id": "request-gr06-compiler",
        "intent_id": "intent-gr06-compiler",
        "decision": "APPROVE",
        "risk_level": 1,
        "ts_ms": 100,
    }
    return build_direct_host_route_contracts(
        surface=HostCommandSurface.CHAT,
        request_id="request-gr06-compiler",
        session_id="session-gr06-compiler",
        principal_id="person-gr06-compiler",
        ts_ms=100,
        intent_draft={
            "id": "intent-gr06-compiler",
            "route": "chat",
            "target_id": "surface_reply",
            "action": "exec_intent",
            "target": "host",
            "task": "reply safely",
        },
        governor_result=governor,
        sanitized_message="original user turn",
        person_id="person-gr06-compiler",
        frozen_context={
            "request_body": {
                "input_source": "GATEWAY",
                "response_form": "text",
                "attachments": [],
                "multimodal_observations": {},
            },
            "delivery_context": {
                "reply_channel": "qq",
                "reply_target": "conversation-gr06",
            },
            "retrieval_summary": {"status": "OK"},
        },
    )


def _gateway_companion_contracts(*, context_schema: str = "natsume.runtime-context.request.v1"):
    """Build a gateway turn whose transport principal differs from its user."""

    relationship_subject_id = "person-gr06-companion"
    workspace_id = "tenant:gr06-companion"
    fact = WorldFactV2(
        workspace_id=workspace_id,
        subject_id=relationship_subject_id,
        key="host.runtime",
        value="ready",
        source_ref="world-observation://gr06/companion/1",
        confidence=1.0,
        observed_at_ms=111,
        expires_at_ms=10_000,
    )
    snapshot = WorldSnapshotV2(
        workspace_id=workspace_id,
        subject_id=relationship_subject_id,
        revision=1,
        facts=(fact,),
    )
    return build_direct_host_route_contracts(
        surface=HostCommandSurface.GATEWAY,
        request_id="request-gr06-companion",
        session_id="session-gr06-companion",
        principal_id="service.edge.gateway",
        ts_ms=111,
        intent_draft={
            "id": "intent-gr06-companion",
            "route": "chat",
            "target_id": "surface_reply",
            "action": "exec_intent",
            "target": "host",
            "task": "reply with the frozen companion scope",
        },
        governor_result={
            "request_id": "request-gr06-companion",
            "intent_id": "intent-gr06-companion",
            "decision": "APPROVE",
            "risk_level": 1,
            "ts_ms": 111,
        },
        sanitized_message="please keep my companion scope",
        person_id=relationship_subject_id,
        frozen_context={
            "runtime_context_schema": context_schema,
            "workspace_id": workspace_id,
            "subject_id": relationship_subject_id,
            "world_snapshot_v2": snapshot.to_dict(),
            "scene_identity_v2": {
                "schema": "natsume.scene-identity.v2",
                "workspace_id": workspace_id,
                "subject_id": relationship_subject_id,
                "scene_id": "scene.casual-chat",
                "revision": 1,
                "decision_digest": "f" * 64,
            },
            "request_body": {
                "input_source": "GATEWAY",
                "response_form": "text",
                "attachments": [],
                "multimodal_observations": {},
            },
            "delivery_context": {
                "reply_channel": "qq",
                "reply_target": "conversation-gr06-companion",
            },
        },
    )


def _affective_chat_contracts():
    fact = WorldFactV2(
        workspace_id="workspace-gr06-affective",
        subject_id="person-gr06-compiler",
        key="relationship.state",
        value="connected",
        source_ref="world-observation://gr06/affective/1",
        confidence=1.0,
        observed_at_ms=100,
        expires_at_ms=10_000,
    )
    snapshot = WorldSnapshotV2(
        workspace_id=fact.workspace_id,
        subject_id=fact.subject_id,
        revision=1,
        facts=(fact,),
    )
    return build_direct_host_route_contracts(
        surface=HostCommandSurface.CHAT,
        request_id="request-gr06-affective",
        session_id="session-gr06-affective",
        principal_id="person-gr06-compiler",
        ts_ms=100,
        intent_draft={
            "id": "intent-gr06-affective",
            "route": "chat",
            "target_id": "surface_reply",
            "action": "exec_intent",
            "target": "host",
            "task": "reply with companionship",
        },
        governor_result={
            "request_id": "request-gr06-affective",
            "intent_id": "intent-gr06-affective",
            "decision": "APPROVE",
            "risk_level": 1,
            "ts_ms": 100,
        },
        sanitized_message="我今天有点累",
        person_id="person-gr06-compiler",
        frozen_context={
            "runtime_context_schema": "natsume.runtime-context.request.v1",
            "workspace_id": fact.workspace_id,
            "subject_id": fact.subject_id,
            "world_snapshot_v2": snapshot.to_dict(),
            "scene_identity_v2": {
                "schema": "natsume.scene-identity.v2",
                "workspace_id": fact.workspace_id,
                "subject_id": fact.subject_id,
                "scene_id": "scene.companion",
                "revision": 1,
                "decision_digest": "f" * 64,
            },
        },
    )


def _affective_foreground_descriptor() -> dict:
    guidance = build_affective_turn_guidance(
        emotion_context={
            "_affective_life_internal": True,
            "affective_life": {
                "schema_version": "affective_turn_context.v1",
                "self_id": "natsume_ai",
                "workspace_id": "workspace-gr06-affective",
                "relationship_subject_id": "person-gr06-compiler",
                "session_id": "session-gr06-affective",
                "self_affect": {
                    "self_id": "natsume_ai",
                    "relationship_subject_id": "person-gr06-compiler",
                    "revision": 6,
                    "components": [{"emotion": "CARE", "intensity": 0.6}],
                    "last_regulation": {
                        "regulation_id": "reg-gr06-affective",
                        "mode": "EXPRESS",
                    },
                },
                "user_observations": [],
                "relationship_life": {
                    "state": {
                        "relationship_subject_id": "person-gr06-compiler",
                        "revision": 3,
                        "repair_required": False,
                        "proactive_permission": "UNKNOWN",
                    },
                    "aftereffect": {"outcome": "UNKNOWN"},
                },
            },
        },
        request_id="request-gr06-affective",
        workspace_id="workspace-gr06-affective",
        relationship_subject_id="person-gr06-compiler",
        session_id="session-gr06-affective",
        created_at_ms=100,
        previous_avatar_expression="NEUTRAL",
        previous_expression_source="FALLBACK_NEUTRAL",
    )
    assert guidance is not None
    plan = guidance.plan.as_dict()
    narrative = "我在这儿，先歇一会儿也好。"
    intents, controls, receipts = build_buffered_expression_delivery(
        affective_turn_plan=plan,
        narrative=narrative,
        created_at_ms=100,
    )
    return {
        "effect_type": "life.foreground_completed",
        "commit_scope": "NODE",
        "payload": {
            "command_type": "foreground_completed",
            "person_id": "person-gr06-compiler",
            "command_id": "life::foreground_completed::request-gr06-affective",
            "correlation_id": "request-gr06-affective",
            "created_at_ms": 100,
            "metadata": {"source": "surface_main_chain"},
            "payload": {
                "assistant_reply": narrative,
                "affective_turn_plan": plan,
                "affective_turn_plan_digest": digest_json(plan),
                "expression_delivery_intents": [item.as_dict() for item in intents],
                "expression_delivery_controls": [item.as_dict() for item in controls],
                "expression_delivery_receipts": [item.as_dict() for item in receipts],
            },
        },
    }


def _operator_contracts():
    fact = WorldFactV2(
        workspace_id="workspace-operator",
        subject_id="principal-operator",
        key="host.sys_mode",
        value="normal",
        source_ref="world-observation://operator/sys-mode/1",
        confidence=1.0,
        observed_at_ms=100,
        expires_at_ms=10_000,
    )
    snapshot = WorldSnapshotV2(
        workspace_id=fact.workspace_id,
        subject_id=fact.subject_id,
        revision=1,
        facts=(fact,),
    )
    governor = {
        "request_id": _OPERATOR_REQUEST_ID,
        "intent_id": "intent-gr06-operator-sys-mode",
        "decision": "APPROVE",
        "risk_level": 4,
        "ts_ms": 100,
    }
    return build_direct_host_route_contracts(
        surface=HostCommandSurface.OPERATOR,
        request_id=_OPERATOR_REQUEST_ID,
        session_id="session-gr06-operator",
        principal_id="principal-operator",
        ts_ms=100,
        intent_draft={
            "id": "intent-gr06-operator-sys-mode",
            "route": "delegate",
            "target_id": _OPERATOR_TARGET,
            "domain": "organ_ops",
            "action": "operator_sys_mode_transition",
            "target": "system",
            "task": "switch host mode to focus",
            "args": {
                "operator_command_id": "sys_mode.switch",
                "target_mode": "focus",
                "source": "manual",
                "reason": "focused work",
            },
        },
        governor_result=governor,
        sanitized_message="switch host mode to focus",
        person_id="principal-operator",
        operator_command_id="sys_mode.switch",
        control_route_execution_policy=_control_policy(
            "sys_mode.switch",
            _OPERATOR_TARGET,
        ),
        frozen_context={
            "runtime_context_schema": "natsume.runtime-context.request.v1",
            "workspace_id": fact.workspace_id,
            "subject_id": fact.subject_id,
            "world_snapshot_v2": snapshot.to_dict(),
            "scene_identity_v2": {
                "schema": "natsume.scene-identity.v2",
                "workspace_id": fact.workspace_id,
                "subject_id": fact.subject_id,
                "scene_id": "scene.focused-work",
                "revision": 1,
                "decision_digest": "b" * 64,
            },
        },
    )


def _operator_commit():
    event_id = "obs-sys-mode:" + hashlib.sha256(
        _OPERATOR_REQUEST_ID.encode("utf-8")
    ).hexdigest()
    return {
        "decision": "APPROVE",
        "request_id": _OPERATOR_REQUEST_ID,
        "narrative": "accepted host mode transition",
        "route_type": "delegate",
        "target_id": _OPERATOR_TARGET,
        "audit_refs": {
            "node_grant_ref": _OPERATOR_NODE_GRANT_REF,
            "node_grant_digest": _OPERATOR_NODE_GRANT_DIGEST,
        },
        "acceptance_effect_intents": [
            {
                "effect_type": "host.sys_mode_transition",
                "commit_scope": "NODE",
                "payload": {
                    "operator_command_id": "sys_mode.switch",
                    "request_id": _OPERATOR_REQUEST_ID,
                    "target_mode": "focus",
                    "source": "manual",
                    "reason": "focused work",
                    "event_id": event_id,
                    "runtime_scope_id": "runtime-scope-operator-compiler",
                },
            }
        ],
    }


def _edge_operator_contracts():
    fact = WorldFactV2(
        workspace_id="workspace-edge",
        subject_id="principal-edge",
        key="edge.connection",
        value="ready",
        source_ref="world-observation://operator/edge/1",
        confidence=1.0,
        observed_at_ms=200,
        expires_at_ms=10_000,
    )
    snapshot = WorldSnapshotV2(
        workspace_id=fact.workspace_id,
        subject_id=fact.subject_id,
        revision=1,
        facts=(fact,),
    )
    return build_direct_host_route_contracts(
        surface=HostCommandSurface.OPERATOR,
        request_id=_EDGE_REQUEST_ID,
        session_id="session-gr06-edge",
        principal_id="principal-edge",
        ts_ms=200,
        intent_draft={
            "id": "intent-gr06-operator-edge",
            "route": "delegate",
            "target_id": _EDGE_TARGET,
            "domain": "organ_ops",
            "action": "operator_edge_command_push",
            "target": "edge",
            "task": "set edge expression to happy",
            "args": {
                "operator_command_id": "edge.command.push",
                "command_type": "emotion_state_command",
                "target_agent": "edge.somatic",
                "embodiment_runtime_scope_id": "runtime-scope-operator-edge",
                "payload": {"emotion_tag": "HAPPY", "intensity": 0.75},
            },
        },
        governor_result={
            "request_id": _EDGE_REQUEST_ID,
            "intent_id": "intent-gr06-operator-edge",
            "decision": "APPROVE",
            "risk_level": 4,
            "ts_ms": 200,
        },
        sanitized_message="set edge expression to happy",
        person_id="principal-edge",
        operator_command_id="edge.command.push",
        control_route_execution_policy=_control_policy(
            "edge.command.push",
            _EDGE_TARGET,
        ),
        frozen_context={
            "runtime_context_schema": "natsume.runtime-context.request.v1",
            "workspace_id": fact.workspace_id,
            "subject_id": fact.subject_id,
            "world_snapshot_v2": snapshot.to_dict(),
            "scene_identity_v2": {
                "schema": "natsume.scene-identity.v2",
                "workspace_id": fact.workspace_id,
                "subject_id": fact.subject_id,
                "scene_id": "scene.operator-edge",
                "revision": 2,
                "decision_digest": "d" * 64,
            },
        },
    )


def _edge_operator_commit():
    return {
        "decision": "APPROVE",
        "request_id": _EDGE_REQUEST_ID,
        "narrative": "accepted edge expression command",
        "route_type": "delegate",
        "target_id": _EDGE_TARGET,
        "audit_refs": {
            "node_grant_ref": _EDGE_NODE_GRANT_REF,
            "node_grant_digest": _EDGE_NODE_GRANT_DIGEST,
        },
        "acceptance_effect_intents": [
            {
                "effect_type": "edge.embodiment_enqueue",
                "commit_scope": "NODE",
                "payload": {
                    "operator_command_id": "edge.command.push",
                    "request_id": _EDGE_REQUEST_ID,
                    "command_type": "emotion_state_command",
                    "target_agent": "edge.somatic",
                    "payload": {"emotion_tag": "HAPPY", "intensity": 0.75},
                    "runtime_scope_id": "runtime-scope-operator-edge",
                },
            }
        ],
    }


def _operation_reconciliation_contracts(*, recovery_case: bool = False):
    fact = WorldFactV2(
        workspace_id="workspace-operation-reconcile",
        subject_id="principal-operation-reconcile",
        key="task.operation_reconciliation",
        value="signed",
        source_ref="world-observation://operator/operation-reconcile/1",
        confidence=1.0,
        observed_at_ms=300,
        expires_at_ms=10_000,
    )
    snapshot = WorldSnapshotV2(
        workspace_id=fact.workspace_id,
        subject_id=fact.subject_id,
        revision=1,
        facts=(fact,),
    )
    args = {
        "operator_command_id": "task_operation.reconcile",
        "evidence_ref": _OPERATION_EVIDENCE_REF,
        "evidence_digest": _OPERATION_EVIDENCE_DIGEST,
        "reservation_id": "reservation-gr06-reacquire",
        "expected_version": 2,
        "action": "REACQUIRE",
        "reconciler_actor_id": fact.subject_id,
    }
    if recovery_case:
        args.update(
            {
                "recovery_case_ref": _OPERATION_RECOVERY_CASE_REF,
                "recovery_case_subject_sha256": (
                    _OPERATION_RECOVERY_CASE_DIGEST
                ),
            }
        )
    return build_direct_host_route_contracts(
        surface=HostCommandSurface.OPERATOR,
        request_id=_OPERATION_REQUEST_ID,
        session_id="session-gr06-operation-reconcile",
        principal_id=fact.subject_id,
        ts_ms=300,
        intent_draft={
            "id": "intent-gr06-operation-reconcile",
            "route": "delegate",
            "target_id": _OPERATION_TARGET,
            "domain": "task_runtime",
            "action": "operator_task_operation_reconciliation",
            "target": "task_runtime",
            "task": "reacquire one signed operation",
            "args": args,
        },
        governor_result={
            "request_id": _OPERATION_REQUEST_ID,
            "intent_id": "intent-gr06-operation-reconcile",
            "decision": "APPROVE",
            "risk_level": 4,
            "ts_ms": 300,
        },
        sanitized_message="reacquire one signed operation",
        person_id=fact.subject_id,
        operator_command_id="task_operation.reconcile",
        control_route_execution_policy=_control_policy(
            "task_operation.reconcile",
            _OPERATION_TARGET,
        ),
        frozen_context={
            "runtime_context_schema": "natsume.runtime-context.request.v1",
            "workspace_id": fact.workspace_id,
            "subject_id": fact.subject_id,
            "world_snapshot_v2": snapshot.to_dict(),
            "scene_identity_v2": {
                "schema": "natsume.scene-identity.v2",
                "workspace_id": fact.workspace_id,
                "subject_id": fact.subject_id,
                "scene_id": "scene.operation-reconciliation",
                "revision": 3,
                "decision_digest": "f" * 64,
            },
        },
    )


def _operation_reconciliation_commit(*, recovery_case: bool = False):
    payload = {
        "operator_command_id": "task_operation.reconcile",
        "request_id": _OPERATION_REQUEST_ID,
        "evidence_ref": _OPERATION_EVIDENCE_REF,
        "evidence_digest": _OPERATION_EVIDENCE_DIGEST,
        "reservation_id": "reservation-gr06-reacquire",
        "expected_version": 2,
        "action": "REACQUIRE",
        "reconciler_actor_id": "principal-operation-reconcile",
    }
    if recovery_case:
        payload.update(
            {
                "recovery_case_ref": _OPERATION_RECOVERY_CASE_REF,
                "recovery_case_subject_sha256": (
                    _OPERATION_RECOVERY_CASE_DIGEST
                ),
            }
        )
    return {
        "decision": "APPROVE",
        "request_id": _OPERATION_REQUEST_ID,
        "narrative": "accepted signed Operation reacquisition",
        "route_type": "delegate",
        "target_id": _OPERATION_TARGET,
        "audit_refs": {
            "node_grant_ref": _OPERATION_NODE_GRANT_REF,
            "node_grant_digest": _OPERATION_NODE_GRANT_DIGEST,
        },
        "acceptance_effect_intents": [
            {
                "effect_type": "task.operation_reconciliation",
                "commit_scope": "NODE",
                "payload": payload,
            }
        ],
    }


def test_compiler_rebuilds_graph_scoped_user_life_and_delivery_effects() -> None:
    contracts = _contracts()
    kwargs = {
        "command": contracts.command,
        "work_order": contracts.work_order,
        "node_id": contracts.graph.nodes[0].node_id,
        "attempt_id": "attempt-gr06-compiler",
        "worker_commit": {
            "decision": "APPROVE",
            "request_id": contracts.command.request_id,
            "narrative": "accepted assistant reply",
            "route_type": "chat",
            "target_id": "surface_reply",
        },
    }

    first = compile_graph_acceptance_effects(**kwargs)
    replay = compile_graph_acceptance_effects(**kwargs)

    assert first == replay
    assert [item.effect_type for item in first] == [
        "conversation.user_turn",
        "conversation.preference_signal",
        "life.conversation_turn_recorded",
        "life.foreground_completed",
        "surface.reply_delivery",
    ]
    assert all(item.commit_scope is AcceptanceEffectCommitScope.GRAPH for item in first)
    assert all(item.graph_id == contracts.graph.graph_id for item in first)
    assert all(item.attempt_id == "attempt-gr06-compiler" for item in first)
    user = first[0].payload
    assert user["content"] == "original user turn"
    preference = first[1].payload
    assert preference["schema_version"] == "Natsume.conversation.preference_signal.v1"
    assert preference["operation_id"].startswith("preference-effect://sha256/")
    conversation_life = first[2].payload
    assert conversation_life["schema_version"] == "Natsume.conversation.life_projection.v1"
    assert conversation_life["command_id"].endswith("::user")
    life = first[3].payload
    assert life["payload"]["assistant_reply"] == "accepted assistant reply"
    assert life["payload"]["deliveries"] == []
    delivery = first[4].payload
    assert delivery["reply_target"] == "conversation-gr06"
    assert delivery["authority"]["governor_grant_ref"] == contracts.command.governor_grant_ref


def test_compiler_keeps_gateway_authority_separate_from_companion_subject() -> None:
    contracts = _gateway_companion_contracts()
    effects = compile_graph_acceptance_effects(
        command=contracts.command,
        work_order=contracts.work_order,
        node_id=contracts.graph.nodes[0].node_id,
        attempt_id="attempt-gr06-companion",
        worker_commit={
            "decision": "APPROVE",
            "request_id": contracts.command.request_id,
            "narrative": "I will keep the relationship scope intact.",
            "route_type": "chat",
            "target_id": "surface_reply",
            "audit_refs": {
                "node_grant_ref": "node-grant://gr06-companion",
                "node_grant_digest": "sha256:" + "b" * 64,
            },
        },
    )

    assert contracts.command.principal_id == "service.edge.gateway"
    by_type = {effect.effect_type: effect.payload for effect in effects}
    for effect_type in (
        "conversation.user_turn",
        "conversation.preference_signal",
        "life.conversation_turn_recorded",
        "life.foreground_completed",
        "surface.reply_delivery",
    ):
        assert by_type[effect_type]["person_id"] == "person-gr06-companion"
    assert (
        by_type["conversation.user_turn"]["metadata"]["authority_principal_id"]
        == "service.edge.gateway"
    )
    life = by_type["life.foreground_completed"]
    assert life["payload"]["workspace_id"] == "tenant:gr06-companion"
    assert life["payload"]["person_id"] == "person-gr06-companion"
    assert (
        life["payload"]["projection_intents"][0]["payload"]["person_id"]
        == "person-gr06-companion"
    )
    assert (
        by_type["surface.reply_delivery"]["authority"]["authority_principal_id"]
        == "service.edge.gateway"
    )


def test_compiler_places_frozen_companion_expression_after_life_acceptance() -> None:
    contracts = _affective_chat_contracts()
    effects = compile_graph_acceptance_effects(
        command=contracts.command,
        work_order=contracts.work_order,
        node_id=contracts.graph.nodes[0].node_id,
        attempt_id="attempt-gr06-affective",
        worker_commit={
            "decision": "APPROVE",
            "request_id": contracts.command.request_id,
            "narrative": "我在这儿，先歇一会儿也好。",
            "route_type": "chat",
            "target_id": "surface_reply",
            "audit_refs": {
                "node_grant_ref": "node-grant://gr06-affective",
                "node_grant_digest": "sha256:" + "4" * 64,
            },
            "acceptance_effect_intents": [_affective_foreground_descriptor()],
        },
    )

    effect_types = [item.effect_type for item in effects]
    first_expression = effect_types.index("host.companion_expression_enqueue")
    assert effect_types[first_expression - 1] == "life.foreground_completed"
    assert effect_types.count("host.companion_expression_enqueue") == 1
    life = next(
        item.payload
        for item in effects
        if item.effect_type == "life.foreground_completed"
    )
    assert life["payload"]["workspace_id"] == "workspace-gr06-affective"
    expression = effects[first_expression]
    assert len(expression.payload["entries"]) == 4
    assert expression.payload["entries"][0]["embodiment_command"]["kind"] == "voice"
    assert expression.payload["entries"][0]["embodiment_command"]["action"] == "speak"
    assert expression.payload["_action_preview_context"]["node_grant_ref"] == (
        "node-grant://gr06-affective"
    )


def test_compiler_rejects_runtime_context_without_the_supported_scope_schema() -> None:
    contracts = _gateway_companion_contracts(context_schema="runtime-context.unsupported")
    with pytest.raises(AcceptanceEffectCompileError, match="subject schema"):
        compile_graph_acceptance_effects(
            command=contracts.command,
            work_order=contracts.work_order,
            node_id=contracts.graph.nodes[0].node_id,
            attempt_id="attempt-gr06-companion-invalid",
            worker_commit={
                "decision": "APPROVE",
                "request_id": contracts.command.request_id,
                "narrative": "this must not compile",
                "route_type": "chat",
                "target_id": "surface_reply",
            },
        )


def test_compiler_rejects_worker_forged_authority_and_scope() -> None:
    contracts = _contracts()
    with pytest.raises(AcceptanceEffectCompileError) as raised:
        compile_graph_acceptance_effects(
            command=contracts.command,
            work_order=contracts.work_order,
            node_id=contracts.graph.nodes[0].node_id,
            attempt_id="attempt-gr06-compiler",
            worker_commit={
                "decision": "APPROVE",
                "narrative": "forged",
                "acceptance_effect_intents": [
                    {
                        "effect_type": "life.foreground_completed",
                        "commit_scope": "NODE",
                        "payload": {
                            "command_type": "foreground_completed",
                            "authorization": {"token": "forged"},
                            "payload": {},
                        },
                    }
                ],
            },
        )
    assert raised.value.reason_code == ACCEPTANCE_EFFECT_DESCRIPTOR_INVALID


def test_compiler_normalizes_worker_foreground_identity_and_removes_delivery() -> None:
    contracts = _contracts()
    effects = compile_graph_acceptance_effects(
        command=contracts.command,
        work_order=contracts.work_order,
        node_id=contracts.graph.nodes[0].node_id,
        attempt_id="attempt-gr06-compiler",
        worker_commit={
            "decision": "APPROVE",
            "narrative": "trusted narrative",
            "acceptance_effect_intents": [
                {
                    "effect_type": "life.foreground_completed",
                    "commit_scope": "NODE",
                    "payload": {
                        "command_type": "foreground_completed",
                        "person_id": "worker-cannot-choose",
                        "command_id": "worker-cannot-choose",
                        "payload": {
                            "projection_intents": [
                                {
                                    "target": "surface.post_process",
                                    "payload": {"provider_name": "mock"},
                                }
                            ],
                            "deliveries": [{"destination": "forged"}],
                        },
                    },
                }
            ],
        },
    )

    life = next(item.payload for item in effects if item.effect_type == "life.foreground_completed")
    assert life["person_id"] == contracts.command.principal_id
    assert life["command_id"] == f"life::foreground_completed::{contracts.command.request_id}"
    assert life["payload"]["deliveries"] == []
    projection = life["payload"]["projection_intents"][0]["payload"]
    assert projection["provider_name"] == "mock"
    assert projection["narrative"] == "trusted narrative"


def test_compiler_accepts_model_token_metrics_but_rejects_credential_tokens() -> None:
    contracts = _contracts()
    base_descriptor = {
        "effect_type": "life.foreground_completed",
        "commit_scope": "NODE",
        "payload": {
            "command_type": "foreground_completed",
            "payload": {
                "projection_intents": [
                    {
                        "target": "surface.post_process",
                        "payload": {
                            "first_token_ms": 17,
                            "stream_after_first_token_ms": 4,
                            "token_count": 321,
                            "prompt_tokens": 123,
                        },
                    }
                ]
            },
        },
    }

    effects = compile_graph_acceptance_effects(
        command=contracts.command,
        work_order=contracts.work_order,
        node_id=contracts.graph.nodes[0].node_id,
        attempt_id="attempt-gr06-token-metadata",
        worker_commit={
            "decision": "APPROVE",
            "narrative": "trusted metrics",
            "acceptance_effect_intents": [base_descriptor],
        },
    )
    life = next(item.payload for item in effects if item.effect_type == "life.foreground_completed")
    projection = life["payload"]["projection_intents"][0]["payload"]
    assert projection["token_count"] == 321
    assert projection["first_token_ms"] == 17

    forged = {
        **base_descriptor,
        "payload": {
            **base_descriptor["payload"],
            "payload": {
                **base_descriptor["payload"]["payload"],
                "api_token": "forged",
            },
        },
    }
    with pytest.raises(AcceptanceEffectCompileError):
        compile_graph_acceptance_effects(
            command=contracts.command,
            work_order=contracts.work_order,
            node_id=contracts.graph.nodes[0].node_id,
            attempt_id="attempt-gr06-forged-token",
            worker_commit={
                "decision": "APPROVE",
                "narrative": "forged token",
                "acceptance_effect_intents": [forged],
            },
        )


def test_operator_compiler_emits_only_bound_sys_mode_transition() -> None:
    contracts = _operator_contracts()
    kwargs = {
        "command": contracts.command,
        "work_order": contracts.work_order,
        "node_id": contracts.graph.nodes[0].node_id,
        "attempt_id": "attempt-gr06-operator",
        "worker_commit": _operator_commit(),
    }

    effects = compile_graph_acceptance_effects(**kwargs)
    replay = compile_graph_acceptance_effects(**kwargs)

    assert effects == replay
    assert len(effects) == 1
    effect = effects[0]
    assert effect.effect_type == "host.sys_mode_transition"
    assert effect.commit_scope is AcceptanceEffectCommitScope.GRAPH
    assert effect.graph_id == contracts.command.graph_id
    assert effect.node_id == contracts.graph.nodes[0].node_id
    assert effect.attempt_id == "attempt-gr06-operator"
    payload = effect.payload
    assert payload["operator_command_id"] == "sys_mode.switch"
    assert payload["request_id"] == _OPERATOR_REQUEST_ID
    assert payload["target_mode"] == "focus"
    assert payload["source"] == "manual"
    assert payload["reason"] == "focused work"
    assert payload["event_id"] == (
        "obs-sys-mode:"
        + hashlib.sha256(_OPERATOR_REQUEST_ID.encode("utf-8")).hexdigest()
    )
    assert payload["runtime_scope_id"] == "runtime-scope-operator-compiler"
    preview = payload["_action_preview_context"]
    assert preview["schema_version"] == (
        "Natsume.acceptance_effect.action_preview_context.v1"
    )
    assert preview["task_graph_id"] == contracts.command.graph_id
    assert preview["graph_revision"] == contracts.command.graph_revision
    assert preview["task_node_id"] == contracts.graph.nodes[0].node_id
    assert preview["parent_governor_grant_ref"] == (
        contracts.command.governor_grant_ref
    )
    assert preview["node_grant_ref"] == _OPERATOR_NODE_GRANT_REF
    assert preview["node_grant_digest"] == _OPERATOR_NODE_GRANT_DIGEST
    assert preview["context_pack_digest"].startswith("sha256:")
    assert preview["scene_identity"] == {
        "schema": "natsume.scene-identity.v2",
        "workspace_id": "workspace-operator",
        "subject_id": "principal-operator",
        "scene_id": "scene.focused-work",
        "revision": 1,
        "decision_digest": "sha256:" + "b" * 64,
    }


def test_operator_compiler_reopens_control_policy_before_effect_staging() -> None:
    contracts = _operator_contracts()
    tampered = deepcopy(
        contracts.work_order.metadata[CONTROL_ROUTE_EXECUTION_POLICY_KEY]
    )
    tampered["policy_ref"] = "control-route-policy://sha256/" + "0" * 64
    contracts.work_order.metadata[CONTROL_ROUTE_EXECUTION_POLICY_KEY] = tampered

    with pytest.raises(AcceptanceEffectCompileError) as raised:
        compile_graph_acceptance_effects(
            command=contracts.command,
            work_order=contracts.work_order,
            node_id=contracts.graph.nodes[0].node_id,
            attempt_id="attempt-gr06-control-policy-tamper",
            worker_commit=_operator_commit(),
        )
    assert raised.value.reason_code == ACCEPTANCE_EFFECT_DESCRIPTOR_INVALID


@pytest.mark.parametrize(
    "payload_patch",
    [
        {"target_mode": "maintenance"},
        {"governor_grant_ref": "governor-grant://forged"},
    ],
    ids=("target-drift", "forged-authority"),
)
def test_operator_compiler_rejects_forged_target_or_authority(
    payload_patch: dict[str, str],
) -> None:
    contracts = _operator_contracts()
    commit = deepcopy(_operator_commit())
    commit["acceptance_effect_intents"][0]["payload"].update(payload_patch)

    with pytest.raises(AcceptanceEffectCompileError) as raised:
        compile_graph_acceptance_effects(
            command=contracts.command,
            work_order=contracts.work_order,
            node_id=contracts.graph.nodes[0].node_id,
            attempt_id="attempt-gr06-operator-forged",
            worker_commit=commit,
        )

    assert raised.value.reason_code == ACCEPTANCE_EFFECT_DESCRIPTOR_INVALID


def test_operator_compiler_builds_deterministic_edge_embodiment_command() -> None:
    contracts = _edge_operator_contracts()
    kwargs = {
        "command": contracts.command,
        "work_order": contracts.work_order,
        "node_id": contracts.graph.nodes[0].node_id,
        "attempt_id": "attempt-gr06-edge",
        "worker_commit": _edge_operator_commit(),
    }

    effects = compile_graph_acceptance_effects(**kwargs)
    replay = compile_graph_acceptance_effects(**kwargs)

    assert effects == replay
    assert len(effects) == 1
    effect = effects[0]
    assert effect.effect_type == "edge.embodiment_enqueue"
    assert effect.commit_scope is AcceptanceEffectCommitScope.GRAPH
    payload = effect.payload
    assert payload["operator_command_id"] == "edge.command.push"
    assert payload["command_type"] == "emotion_state_command"
    assert payload["target_agent"] == "edge.somatic"
    assert payload["runtime_scope_id"] == "runtime-scope-operator-edge"
    command = payload["embodiment_command"]
    assert command["kind"] == "avatar"
    assert command["action"] == "set_expression"
    assert command["payload"] == {"emotion_tag": "HAPPY", "intensity": 0.75}
    assert command["request_id"] == _EDGE_REQUEST_ID
    assert command["grant_ref"] == _EDGE_NODE_GRANT_REF
    assert command["idempotency_key"].startswith("task-edge-effect:")
    assert command["authority_ref"].startswith(
        "authority://task-acceptance/sha256/"
    )
    assert command["issued_at_ms"] == 200
    assert command["deadline_ms"] == 604_800_200
    preview = payload["_action_preview_context"]
    assert preview["task_graph_id"] == contracts.command.graph_id
    assert preview["task_node_id"] == contracts.graph.nodes[0].node_id
    assert preview["node_grant_ref"] == _EDGE_NODE_GRANT_REF
    assert preview["node_grant_digest"] == _EDGE_NODE_GRANT_DIGEST


@pytest.mark.parametrize(
    "payload_patch",
    [
        {"target_agent": "edge.channel"},
        {"payload": {"emotion_tag": "HAPPY", "intensity": 1.5}},
        {"runtime_scope_id": "runtime-scope-forged"},
    ],
    ids=("target-drift", "invalid-intensity", "runtime-scope-drift"),
)
def test_operator_compiler_rejects_forged_edge_effect(
    payload_patch: dict[str, object],
) -> None:
    contracts = _edge_operator_contracts()
    commit = deepcopy(_edge_operator_commit())
    commit["acceptance_effect_intents"][0]["payload"].update(payload_patch)

    with pytest.raises(AcceptanceEffectCompileError) as raised:
        compile_graph_acceptance_effects(
            command=contracts.command,
            work_order=contracts.work_order,
            node_id=contracts.graph.nodes[0].node_id,
            attempt_id="attempt-gr06-edge-forged",
            worker_commit=commit,
        )

    assert raised.value.reason_code == ACCEPTANCE_EFFECT_DESCRIPTOR_INVALID


def test_operator_compiler_binds_signed_operation_reconciliation() -> None:
    contracts = _operation_reconciliation_contracts()
    kwargs = {
        "command": contracts.command,
        "work_order": contracts.work_order,
        "node_id": contracts.graph.nodes[0].node_id,
        "attempt_id": "attempt-gr06-operation-reconcile",
        "worker_commit": _operation_reconciliation_commit(),
    }

    effects = compile_graph_acceptance_effects(**kwargs)
    replay = compile_graph_acceptance_effects(**kwargs)

    assert effects == replay
    assert len(effects) == 1
    effect = effects[0]
    assert effect.effect_type == "task.operation_reconciliation"
    assert effect.commit_scope is AcceptanceEffectCommitScope.GRAPH
    assert effect.payload["action"] == "REACQUIRE"
    assert effect.payload["evidence_ref"] == _OPERATION_EVIDENCE_REF
    assert effect.payload["reservation_id"] == "reservation-gr06-reacquire"
    assert effect.payload["_action_preview_context"]["node_grant_ref"] == (
        _OPERATION_NODE_GRANT_REF
    )


def test_operator_compiler_rejects_forged_operation_reconciler() -> None:
    contracts = _operation_reconciliation_contracts()
    commit = deepcopy(_operation_reconciliation_commit())
    commit["acceptance_effect_intents"][0]["payload"][
        "reconciler_actor_id"
    ] = "principal-forged"

    with pytest.raises(AcceptanceEffectCompileError) as raised:
        compile_graph_acceptance_effects(
            command=contracts.command,
            work_order=contracts.work_order,
            node_id=contracts.graph.nodes[0].node_id,
            attempt_id="attempt-gr06-operation-reconcile-forged",
            worker_commit=commit,
        )

    assert raised.value.reason_code == ACCEPTANCE_EFFECT_DESCRIPTOR_INVALID


def test_operator_compiler_binds_complete_operation_recovery_case() -> None:
    contracts = _operation_reconciliation_contracts(recovery_case=True)
    commit = _operation_reconciliation_commit(recovery_case=True)
    effects = compile_graph_acceptance_effects(
        command=contracts.command,
        work_order=contracts.work_order,
        node_id=contracts.graph.nodes[0].node_id,
        attempt_id="attempt-gr06-operation-recovery-case",
        worker_commit=commit,
    )
    assert effects[0].payload["recovery_case_ref"] == (
        _OPERATION_RECOVERY_CASE_REF
    )
    assert effects[0].payload["recovery_case_subject_sha256"] == (
        _OPERATION_RECOVERY_CASE_DIGEST
    )

    incomplete = deepcopy(commit)
    incomplete["acceptance_effect_intents"][0]["payload"].pop(
        "recovery_case_subject_sha256"
    )
    with pytest.raises(AcceptanceEffectCompileError):
        compile_graph_acceptance_effects(
            command=contracts.command,
            work_order=contracts.work_order,
            node_id=contracts.graph.nodes[0].node_id,
            attempt_id="attempt-gr06-operation-recovery-case-incomplete",
            worker_commit=incomplete,
        )
