from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from host_brain.app.surface_main_chain_adapter import (
    build_surface_main_chain_input_from_chat_input,
    surface_main_chain_enabled,
    surface_main_chain_sse_generator,
)


SOUL = "你是数字生命夏目蓝。中文私聊，反应快，有一点嘴硬，但帮忙时先给能执行的一步。"


def _parse_sse(raw: str) -> dict:
    assert raw.startswith("data: ")
    return json.loads(raw.removeprefix("data: ").strip())


def _agent_input(**overrides):
    payload = {
        "request_id": "req-surface-app",
        "session_id": "sess-surface-app",
        "person_id": "user-1",
        "user_message": "这段我不会改",
        "render_policy": {
            "primary_topic": "technical_help",
            "interaction_move": "technical_next_step",
            "response_goal": "先看输入输出",
            "response_form": "text",
            "max_questions": 0,
        },
        "emotion_context": {"emotion_tag": "ANXIOUS", "emotion_intensity": 0.4},
        "emotion_snapshot": {},
        "bond_summary": {"relationship_distance": "familiar"},
        "retrieval_summary": {
            "status": "OK",
            "surface_memory_recall_packet": {
                "facts": ["用户上次改这类段落时卡在输入输出边界。"],
                "style_anchor": ["记忆只补当前承接，不展开旧对话。"],
                "source_refs": ["memory://episodic/1"],
                "evidence_refs": ["evd://episodic/1"],
            },
        },
        "trace_stage_meta": {},
        "llm_runtime": {
            "available": True,
            "provider_name": "mock",
            "model_id": "mock-chat",
            "timeout_ms": 5000,
            "llm_api_key": "",
            "base_url_override": "",
        },
        "attachments": [],
        "multimodal_observations": {},
        "response_form": "text",
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def _candidate_payload(candidates: list[str]) -> str:
    return json.dumps({"candidates": candidates}, ensure_ascii=False)


def test_build_surface_main_chain_input_from_chat_input_compresses_app_context(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    payload = build_surface_main_chain_input_from_chat_input(_agent_input())

    assert payload.soul_md == SOUL
    assert payload.user_message == "这段我不会改"
    assert payload.task_control["task_mode"] == "technical_help"
    assert payload.task_control["response_move"] == "technical_next_step"
    assert tuple(payload.memory_packet.facts) == ("用户上次改这类段落时卡在输入输出边界。",)
    assert payload.affect_packet.emotional_tone == "steady_helpful"
    assert "memory://episodic/1" not in json.dumps(payload.as_dict(), ensure_ascii=False)


def test_build_surface_main_chain_input_prefers_surface_task_control(monkeypatch) -> None:
    trace: dict[str, object] = {}
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    def _fake_surface_context_packet(**kwargs):  # noqa: ANN003
        calls.append(dict(kwargs))
        return {
            "reply_plan": {
                "primary_topic": "context_fallback_should_not_win",
                "interaction_move": "context_move_should_not_win",
                "response_form": "text",
                "max_questions": 0,
            },
            "conversation_history": {"turns_injected": 1, "summary_present": False},
            "surface_affect_packet": {
                "emotional_tone": "playful_light",
                "relationship_distance": "familiar_not_intimate",
                "style_anchor": ["轻一点接，但先处理当前句子。"],
                "must_not_do": ["不要把上下文包写进回复"],
            },
            "surface_memory_recall_packet": {
                "facts": ["用户刚刚要求继续迁移主链。"],
                "style_anchor": ["记忆只补当前承接，不展开旧对话。"],
                "source_refs": ["memory://raw/should-not-pass"],
                "evidence_refs": ["evd://memory/2"],
            },
        }

    monkeypatch.setattr("host_brain.app.surface_context_packet.build_surface_context_packet", _fake_surface_context_packet)

    payload = build_surface_main_chain_input_from_chat_input(
        _agent_input(
            surface_task_control={
                "task_mode": "style_repair",
                "response_move": "ack_style_issue",
                "response_form": "text",
                "question_budget": 0,
            }
        ),
        trace_stage_meta=trace,
    )

    assert calls
    assert payload.task_control["task_mode"] == "style_repair"
    assert payload.task_control["response_move"] == "ack_style_issue"
    assert payload.affect_packet.emotional_tone == "playful_light"
    assert tuple(payload.memory_packet.facts) == ("用户刚刚要求继续迁移主链。",)
    assert trace["surface_context_packet_prepared"] is True
    assert trace["surface_selected_reply_plan"]["task_mode"] == "style_repair"
    assert trace["reply_plan"]["primary_topic"] == "context_fallback_should_not_win"
    assert "memory://raw/should-not-pass" not in json.dumps(payload.as_dict(), ensure_ascii=False)


def test_build_surface_main_chain_input_derives_reply_plan_without_passing_history(monkeypatch) -> None:
    trace: dict[str, object] = {}

    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    def _blocked_build_cortex_prompt(**_kwargs):  # noqa: ANN003
        raise AssertionError("surface main chain must not build legacy cortex prompt messages")

    def _fake_surface_context_packet(**kwargs):  # noqa: ANN003
        assert kwargs["user_message"] == "这段我不会改"
        return {
            "reply_plan": {
                "primary_topic": "presence_ping",
                "interaction_move": "answer_current_chat_presence",
                "response_form": "text",
                "max_questions": 0,
            },
            "conversation_history": {"turns_injected": 2, "summary_present": True},
            "surface_affect_packet": {
                "emotional_tone": "slightly_guilty",
                "relationship_distance": "familiar_not_intimate",
                "style_anchor": ["轻轻承认慢了一拍，马上回到当前对话。"],
                "must_not_do": ["不要把慢了一拍写成现实动作"],
            },
            "surface_memory_recall_packet": {
                "facts": ["用户刚刚指出上一句不通顺。"],
                "relationship_clues": ["熟人私聊，不升级亲密。"],
                "source_refs": ["memory://raw/should-not-pass"],
                "evidence_refs": ["evd://memory/1"],
            },
            "legacy_messages": [{"content": "RAW HISTORY PROMPT SHOULD NOT REACH SURFACE"}],
        }

    monkeypatch.setattr("host_brain.app.cortex_prompt_builder.build_cortex_prompt", _blocked_build_cortex_prompt)
    monkeypatch.setattr("host_brain.app.surface_context_packet.build_surface_context_packet", _fake_surface_context_packet)

    payload = build_surface_main_chain_input_from_chat_input(
        _agent_input(render_policy={}, surface_task_control={}),
        trace_stage_meta=trace,
    )
    rendered = json.dumps(payload.as_dict(), ensure_ascii=False)

    assert payload.task_control["task_mode"] == "presence_ping"
    assert payload.task_control["response_move"] == "answer_current_chat_presence"
    assert payload.affect_packet.emotional_tone == "slightly_guilty"
    assert tuple(payload.memory_packet.facts) == ("用户刚刚指出上一句不通顺。",)
    assert trace["conversation_history"] == {"turns_injected": 2, "summary_present": True}
    assert trace["reply_plan"]["primary_topic"] == "presence_ping"
    assert trace["surface_affect_packet"]["emotional_tone"] == "slightly_guilty"
    assert "RAW HISTORY PROMPT SHOULD NOT REACH SURFACE" not in rendered
    assert "memory://raw/should-not-pass" not in rendered


def test_build_surface_main_chain_input_reuses_prepared_context_packet(monkeypatch) -> None:
    trace: dict[str, object] = {
        "surface_context_packet_prepared": True,
        "reply_plan": {
            "primary_topic": "prepared_presence",
            "interaction_move": "reply_from_prepared_packet",
            "response_form": "text",
            "max_questions": 0,
        },
        "surface_affect_packet": {
            "emotional_tone": "slightly_guilty",
            "relationship_distance": "familiar_not_intimate",
            "style_anchor": ["承认刚才慢了一点，但别扩写。"],
            "must_not_do": ["不要重建旧上下文"],
        },
        "surface_memory_recall_packet": {
            "facts": ["用户正在让主链继续迁移。"],
            "style_anchor": ["只承接当前任务。"],
            "source_refs": ["memory://raw/should-not-pass"],
            "evidence_refs": ["evd://memory/prepared"],
        },
    }

    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    def _blocked_surface_context_packet(**_kwargs):  # noqa: ANN003
        raise AssertionError("prepared surface context packet should be reused")

    monkeypatch.setattr(
        "host_brain.app.surface_context_packet.build_surface_context_packet",
        _blocked_surface_context_packet,
    )

    payload = build_surface_main_chain_input_from_chat_input(
        _agent_input(render_policy={}, surface_task_control={}),
        trace_stage_meta=trace,
    )
    rendered = json.dumps(payload.as_dict(), ensure_ascii=False)

    assert payload.task_control["task_mode"] == "prepared_presence"
    assert payload.task_control["response_move"] == "reply_from_prepared_packet"
    assert payload.affect_packet.emotional_tone == "slightly_guilty"
    assert tuple(payload.memory_packet.facts) == ("用户正在让主链继续迁移。",)
    assert trace["surface_selected_reply_plan"]["primary_topic"] == "prepared_presence"
    assert "memory://raw/should-not-pass" not in rendered


@pytest.mark.asyncio
async def test_surface_main_chain_sse_generator_calls_chat_surface_pool(monkeypatch) -> None:
    calls: list[dict] = []
    post_process_calls: list[dict] = []

    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.get_compiled_soul",
        lambda: SimpleNamespace(source_text=SOUL, runtime_full_text=SOUL),
    )

    async def _fake_complete(**kwargs):  # noqa: ANN003
        calls.append(kwargs)
        rendered = "\n".join(message["content"] for message in kwargs["messages"])
        assert kwargs["scene_type"] == "CHAT_SURFACE"
        assert "ReplyControlPacket" in rendered
        assert "memory://episodic/1" not in rendered
        return SimpleNamespace(
            content=json.dumps(
                {
                    "reply_text": "上次卡在输入输出，这轮先盯那一块。",
                    "natsume_emotion_tag": "THINKING",
                    "user_emotion_tag": "ANXIOUS",
                    "emotion_confidence": 0.68,
                },
                ensure_ascii=False,
            ),
            final_provider="mock",
            final_model="mock-chat",
        )

    async def _fake_post_process(**kwargs):  # noqa: ANN003
        post_process_calls.append(kwargs)

    monkeypatch.setattr("host_brain.integrations.llm_helpers.acomplete_llm_with_pool", _fake_complete)
    monkeypatch.setattr("host_brain.app.agent_runtime_shared._async_post_process", _fake_post_process)

    events = [_parse_sse(chunk) async for chunk in surface_main_chain_sse_generator(_agent_input())]
    commit = next(event for event in events if event.get("phase") == "COMMIT")

    assert calls
    assert commit["decision"] == "APPROVE"
    assert commit["audit_refs"]["path"] == "chat_agent:surface_main_chain"
    assert commit["audit_refs"]["trace_stage_meta"]["surface_main_chain_used"] is True
    assert "timings_ms" in commit["audit_refs"]
    assert "输入输出" in commit["narrative"]
    assert commit["reply_envelope"]["audit"]["natsume_emotion_tag"] == "THINKING"
    assert commit["reply_envelope"]["audit"]["vts_expression_id"]
    assert commit["audit_refs"]["surface_main_chain"]["user_emotion_tag"] == "ANXIOUS"
    await asyncio.sleep(0)
    assert post_process_calls
    assert post_process_calls[0]["trace_stage_meta"]["surface_main_chain_used"] is True
    assert post_process_calls[0]["reply_plan"]["primary_topic"] == "technical_help"
    assert post_process_calls[0]["emotion_context"]["emotion_tag"] == "THINKING"
    assert post_process_calls[0]["emotion_context"]["surface_user_emotion_tag"] == "ANXIOUS"
    assert post_process_calls[0]["emotion_context"]["surface_vts_expression_id"]


def test_surface_main_chain_enabled_defaults_to_normal_text_only(monkeypatch) -> None:
    monkeypatch.delenv("HOST_SURFACE_MAIN_CHAIN_ENABLED", raising=False)
    assert surface_main_chain_enabled(_agent_input()) is True

    monkeypatch.setenv("HOST_SURFACE_MAIN_CHAIN_ENABLED", "false")
    assert surface_main_chain_enabled(_agent_input()) is False

    monkeypatch.setenv("HOST_SURFACE_MAIN_CHAIN_ENABLED", "true")
    assert surface_main_chain_enabled(_agent_input(attachments=[{"kind": "image"}])) is False
    assert surface_main_chain_enabled(_agent_input(response_form="sticker")) is False
    assert surface_main_chain_enabled(_agent_input(intent_draft={"route": "delegate"})) is False
