from __future__ import annotations

import asyncio

from host_brain.agent_runtime.application_ports import AgentRuntimeApplicationPorts
from host_brain.agent_runtime.context_read_ports import ConversationContextReadPorts
from host_brain.app.life_projection_handlers import handle_life_projection
from natsume_contracts.life import (
    LifeIntentKind,
    LifeProjectionMessage,
)


def _message(target: str, payload: dict) -> LifeProjectionMessage:
    return LifeProjectionMessage(
        intent_id="intent-1",
        event_id="event-1",
        command_id="command-1",
        person_id="person-1",
        intent_kind=LifeIntentKind.PROJECTION,
        target=target,
        payload=payload,
        payload_digest="a" * 64,
        attempt_count=1,
        fencing_token=1,
    )


def _agent_runtime_ports() -> AgentRuntimeApplicationPorts:
    async def _async_value(**_kwargs):  # noqa: ANN003
        return {}

    return AgentRuntimeApplicationPorts(
        record_user_turn=lambda **_kwargs: {"status": "OK"},
        record_assistant_turn=lambda **_kwargs: {"status": "OK"},
        run_retrieval=_async_value,
        filter_retrieval_evidence_items_for_prompt=lambda value, **_kwargs: value,
        build_user_preference_pack=lambda **_kwargs: {},
        resolve_host_llm_runtime_async=_async_value,
        resolve_surface_reply_llm_runtime=lambda **kwargs: dict(
            kwargs.get("base_runtime") or {}
        ),
        resolve_state_arbiter_bond_fact=_async_value,
        build_conversation_experience_read_pack=lambda **_kwargs: {},
        conversation_history=lambda: object(),
        life_memory_service=lambda: object(),
        audit_ledger=lambda: object(),
        prompt_registry=lambda: object(),
        model_attempt_service=lambda: object(),
        context_read_ports=ConversationContextReadPorts(
            conversation_history=lambda: object(),
            conversation_turn_store=lambda: object(),
            session_topic_ledger_store=lambda: object(),
            session_tactic_ledger_store=lambda: object(),
        ),
        task_status_projection=lambda: {},
        persist_post_process_audit=_async_value,
    )


def test_surface_projection_runs_durable_only_post_process_mode(monkeypatch) -> None:
    captured: dict = {}

    async def _post_process(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(
        "host_brain.agent_runtime.postprocess._async_post_process",
        _post_process,
    )
    result = asyncio.run(
        handle_life_projection(
            _message(
                "surface.post_process",
                {
                    "request_id": "request-1",
                    "person_id": "person-1",
                    "session_id": "session-1",
                    "user_message": "hello",
                    "narrative": "reply",
                    "ts_ms": 100,
                    "emotion_context": {"emotion_tag": "HAPPY"},
                    "trace_stage_meta": {"surface_main_chain_used": True},
                },
            ),
            agent_runtime_ports=_agent_runtime_ports(),
        )
    )

    assert result["status"] == "APPLIED"
    assert captured["narrative"] == "reply"
    trace = captured["trace_stage_meta"]
    assert trace["life_projection_mode"] is True
    assert trace["life_projection_intent_id"] == "intent-1"
    assert trace["skip_heavy_persona_postprocess"] is True
    assert "skip_default_mode_on_conversation_end" not in trace


def test_journal_native_projection_targets_do_not_call_legacy_writers() -> None:
    result = asyncio.run(
        handle_life_projection(_message("life.experience", {"value": 1}))
    )
    assert result == {
        "status": "PROJECTED_FROM_LIFE_JOURNAL",
        "target": "life.experience",
        "intent_id": "intent-1",
    }


def test_expression_protocol_projection_only_acknowledges_host_staging() -> None:
    result = asyncio.run(
        handle_life_projection(
            _message(
                "surface.expression_delivery_protocol",
                {
                    "plan_id": "plan-1",
                    "synchronization_id": "sync-1",
                    "self_affect_revision": 8,
                    "delivery_kind": "INTENT",
                    "delivery_trace": {
                        "channels": ["VOICE", "AVATAR"],
                        "delivery_ids": ["voice-1", "avatar-1"],
                    },
                },
            )
        )
    )

    assert result == {
        "status": "HOST_PROTOCOL_STAGED",
        "target": "surface.expression_delivery_protocol",
        "intent_id": "intent-1",
        "plan_id": "plan-1",
        "synchronization_id": "sync-1",
        "self_affect_revision": 8,
        "delivery_kind": "INTENT",
        "channels": ["VOICE", "AVATAR"],
        "delivery_ids": ["voice-1", "avatar-1"],
    }
