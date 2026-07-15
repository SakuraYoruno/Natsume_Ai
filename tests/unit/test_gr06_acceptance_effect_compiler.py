from __future__ import annotations

import pytest

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
        "life.foreground_completed",
        "surface.reply_delivery",
    ]
    assert all(item.commit_scope is AcceptanceEffectCommitScope.GRAPH for item in first)
    assert all(item.graph_id == contracts.graph.graph_id for item in first)
    assert all(item.attempt_id == "attempt-gr06-compiler" for item in first)
    user = first[0].payload
    assert user["content"] == "original user turn"
    life = first[1].payload
    assert life["payload"]["assistant_reply"] == "accepted assistant reply"
    assert life["payload"]["deliveries"] == []
    delivery = first[2].payload
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
