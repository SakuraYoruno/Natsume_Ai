from __future__ import annotations

import hashlib
from copy import deepcopy

import pytest

from host_brain.runtime.context_contracts import WorldFactV2, WorldSnapshotV2
from host_brain.orchestration.host_route_contracts import (
    build_direct_host_route_contracts,
)
from host_brain.persistence.task_repository import AcceptanceEffectCommitScope
from host_brain.task_runtime.acceptance_effects import (
    ACCEPTANCE_EFFECT_DESCRIPTOR_INVALID,
    AcceptanceEffectCompileError,
    compile_graph_acceptance_effects,
)
from natsume_contracts.task import HostCommandSurface


_OPERATOR_REQUEST_ID = "request-gr06-operator-sys-mode"
_OPERATOR_TARGET = "operator_sys_mode_switch"
_OPERATOR_NODE_GRANT_REF = "node-grant://operator-sys-mode"
_OPERATOR_NODE_GRANT_DIGEST = "sha256:" + "a" * 64


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
