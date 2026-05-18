from __future__ import annotations

from types import SimpleNamespace

from host_brain.app.surface_context_packet import (
    SURFACE_CONTEXT_NODE_ID,
    build_surface_context_packet,
)


def _state(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(as_dict=lambda: dict(payload))


def _plane(
    *,
    topic: str = "technical_help",
    thread: str = "debug_help",
    relationship_payload: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        continuity=SimpleNamespace(
            recent_turns=[{"role": "user", "content": "raw old turn"}],
            summary="raw older summary",
            turns_injected=2,
            fetched_turns=3,
            older_turn_count=5,
            oldest_ts_ms=10,
            newest_ts_ms=20,
            skipped_corrupted_count=1,
            skipped_empty_count=0,
            deduped_same_role_count=1,
        ),
        conversation_interpretation=SimpleNamespace(state=_state({"literal_topic": topic})),
        interaction_thread=SimpleNamespace(state=_state({"thread_kind": thread})),
        interaction_state=SimpleNamespace(
            state=_state(
                {
                    "current_topic": topic,
                    "interaction_move": "technical_next_step",
                    "ongoing_thread": thread,
                }
            )
        ),
        topic_ledger=SimpleNamespace(state=_state({"active_topics": [topic]})),
        tactic_ledger=SimpleNamespace(state=_state({"recent_topics": [topic]})),
        affect=SimpleNamespace(state=_state({"emotion_tag": "NEUTRAL"})),
        relationship=SimpleNamespace(state=_state(relationship_payload or {"bond_stage": "FRIEND"})),
        drive=SimpleNamespace(state=_state({"continuity_drive": 0.4})),
        multimodal=SimpleNamespace(state=_state({})),
        reply_plan=SimpleNamespace(
            state={
                "primary_topic": topic,
                "interaction_move": "technical_next_step",
                "response_form": "text",
            }
        ),
    )


def test_build_surface_context_packet_extracts_compressed_context_only(monkeypatch) -> None:
    calls: list[dict] = []

    def _fake_detect_relational_chat_topic(_message):  # noqa: ANN001
        return ""

    def _fake_assemble_prompt_context_plane(**kwargs):  # noqa: ANN003
        calls.append(kwargs)
        assert kwargs["node_id"] == SURFACE_CONTEXT_NODE_ID
        assert kwargs["runtime_payload"]["intent_draft"] == {"route": "chat"}
        return _plane()

    monkeypatch.setattr(
        "host_brain.app.surface_context_packet.detect_relational_chat_topic",
        _fake_detect_relational_chat_topic,
    )
    monkeypatch.setattr(
        "host_brain.app.surface_context_packet.assemble_prompt_context_plane",
        _fake_assemble_prompt_context_plane,
    )

    packet = build_surface_context_packet(
        user_message="continue",
        render_target_lang="zh",
        emotion_context={"emotion_tag": "ANXIOUS", "emotion_intensity": 0.6},
        retrieval_summary={
            "status": "OK",
            "surface_memory_recall_packet": {
                "facts": ["用户上次卡在输入输出边界。"],
                "relationship_clues": ["熟人私聊，不升级亲密。"],
                "source_refs": ["memory://raw/should-not-pass"],
                "evidence_refs": ["evd://memory/1"],
            },
        },
        request_id="req-surface-context",
        session_id="sess-surface-context",
        person_id="user-1",
        intent_draft={"route": "chat"},
    )

    assert len(calls) == 1
    assert packet["reply_plan"]["primary_topic"] == "technical_help"
    assert packet["conversation_history"] == {
        "turns_injected": 2,
        "fetched_turns": 3,
        "older_turn_count": 5,
        "summary_present": True,
        "oldest_ts_ms": 10,
        "newest_ts_ms": 20,
        "skipped_corrupted_count": 1,
        "skipped_empty_count": 0,
        "deduped_same_role_count": 1,
    }
    assert "recent_turns" not in packet
    assert "summary" not in packet
    assert packet["surface_memory_recall_packet"]["facts"] == ["用户上次卡在输入输出边界。"]
    assert packet["surface_memory_recall_packet"]["relationship_clues"] == ["熟人私聊，不升级亲密。"]
    assert "source_refs" not in packet["surface_memory_recall_packet"]
    assert packet["surface_affect_packet"]["emotional_tone"] == "steady_helpful"
    assert packet["surface_affect_packet"]["relationship_distance"] == "familiar"
    assert packet["organ_influence_packet"]["source"] == "organ_influence_contract"
    assert packet["surface_influence_packet"]["task"]["task_mode"] == "technical_help"
    assert packet["surface_influence_packet"]["affect"]["emotional_tone"] == "steady_helpful"
    assert packet["surface_influence_packet"]["relationship"]["relationship_distance"] == "familiar"
    rendered = str(packet)
    assert "raw old turn" not in rendered
    assert "raw older summary" not in rendered


def test_build_surface_context_packet_reassembles_compact_lane(monkeypatch) -> None:
    calls: list[dict] = []

    def _fake_detect_relational_chat_topic(_message):  # noqa: ANN001
        return ""

    def _fake_assemble_prompt_context_plane(**kwargs):  # noqa: ANN003
        calls.append(kwargs)
        if len(calls) == 1:
            return _plane(topic="presence_ping", thread="phatic")
        return _plane(topic="presence_ping", thread="phatic")

    monkeypatch.setattr(
        "host_brain.app.surface_context_packet.detect_relational_chat_topic",
        _fake_detect_relational_chat_topic,
    )
    monkeypatch.setattr(
        "host_brain.app.surface_context_packet.assemble_prompt_context_plane",
        _fake_assemble_prompt_context_plane,
    )

    packet = build_surface_context_packet(user_message="在吗", request_id="req-compact", session_id="sess")

    assert len(calls) == 2
    assert calls[0]["history_max_turns"] is None
    assert calls[0]["allow_older_summary"] is True
    assert calls[1]["history_max_turns"] == 6
    assert calls[1]["allow_older_summary"] is False
    assert packet["reply_plan"]["primary_topic"] == "presence_ping"


def test_surface_context_packet_keeps_chinese_lover_stage_close(monkeypatch) -> None:
    def _fake_detect_relational_chat_topic(_message):  # noqa: ANN001
        return ""

    def _fake_assemble_prompt_context_plane(**_kwargs):  # noqa: ANN003
        return _plane(
            relationship_payload={
                "bond_stage": "CONFIDANT",
                "relationship_phase": "lover",
                "relationship_substage": "stable_lovers",
            }
        )

    monkeypatch.setattr(
        "host_brain.app.surface_context_packet.detect_relational_chat_topic",
        _fake_detect_relational_chat_topic,
    )
    monkeypatch.setattr(
        "host_brain.app.surface_context_packet.assemble_prompt_context_plane",
        _fake_assemble_prompt_context_plane,
    )

    packet = build_surface_context_packet(
        user_message="今天晚点陪我聊会儿？",
        request_id="req-close-stage",
        session_id="sess-close-stage",
        bond_summary={"stage": "亲密恋人", "trend": "稳定"},
    )

    assert packet["surface_affect_packet"]["relationship_distance"] == "close"
    assert packet["surface_influence_packet"]["relationship"]["relationship_distance"] == "close"


def test_build_surface_context_packet_collects_live_tribe_context(monkeypatch) -> None:
    calls: list[dict] = []

    class _FakeCortex:
        def collect_tribe_context(self, **kwargs):  # noqa: ANN003
            calls.append(dict(kwargs))
            return {
                "vital_signs": {
                    "stress_index": 0.74,
                    "cognitive_load_index": 0.82,
                    "immune_risk_level": "high",
                    "loneliness_level": 0.62,
                },
                "emotion_context": {"emotion_tag": "ANGRY", "emotion_intensity": 0.6},
                "bond_context": {
                    "stage": "CONFIDANT",
                    "bond_vector": {"intimacy": 0.86, "trust": 0.78},
                },
                "raw_organ_outputs": {"limbic": {"messages": ["RAW SHOULD NOT PASS"]}},
            }

    monkeypatch.setattr("host_brain.app.surface_context_packet.detect_relational_chat_topic", lambda _message: "")
    monkeypatch.setattr("host_brain.app.surface_context_packet.assemble_prompt_context_plane", lambda **_kwargs: _plane())
    monkeypatch.setattr("host_brain.tribe.cortex_agent.get_cortex_agent", lambda: _FakeCortex())

    packet = build_surface_context_packet(
        user_message="我有点烦",
        request_id="req-live-organ",
        session_id="sess-live-organ",
        person_id="user-live",
    )
    rendered = str(packet)

    assert calls
    assert calls[0]["session_id"] == "sess-live-organ"
    assert calls[0]["user_id"] == "user-live"
    assert calls[0]["request_body"]["user_message"] == "我有点烦"
    assert packet["surface_organ_context_meta"]["source"] == "cortex_collect_tribe_context"
    assert packet["surface_organ_context_meta"]["status"] == "OK"
    assert packet["surface_organ_context_meta"]["compressed_route_attached"] is True
    assert packet["organ_influence_packet"]["organ_trace"]["contract_version"] == 1
    assert packet["surface_influence_packet"]["organ_trace"]["risk_level"] == "high"
    mapped_organs = packet["surface_influence_packet"]["organ_trace"]["mapped_organs"]
    assert "limbic" in mapped_organs
    assert "cortex" in mapped_organs
    assert "thalamus" in mapped_organs
    assert "RAW SHOULD NOT PASS" not in rendered


def test_build_surface_context_packet_degrades_when_live_tribe_context_fails(monkeypatch) -> None:
    class _FailingCortex:
        def collect_tribe_context(self, **_kwargs):  # noqa: ANN003
            raise RuntimeError("no tribe today")

    monkeypatch.setattr("host_brain.app.surface_context_packet.detect_relational_chat_topic", lambda _message: "")
    monkeypatch.setattr("host_brain.app.surface_context_packet.assemble_prompt_context_plane", lambda **_kwargs: _plane())
    monkeypatch.setattr("host_brain.tribe.cortex_agent.get_cortex_agent", lambda: _FailingCortex())

    packet = build_surface_context_packet(
        user_message="继续",
        request_id="req-live-organ-fail",
        session_id="sess-live-organ-fail",
    )

    assert packet["surface_organ_context_meta"]["status"] == "DEGRADED"
    assert packet["surface_organ_context_meta"]["reason_code"] == "RuntimeError"
    assert packet["surface_influence_packet"]["organ_trace"]["mapped_organs"] == []


def test_build_surface_context_packet_uses_surface_task_control_for_influence(monkeypatch) -> None:
    monkeypatch.setattr("host_brain.app.surface_context_packet.detect_relational_chat_topic", lambda _message: "")
    monkeypatch.setattr("host_brain.app.surface_context_packet.assemble_prompt_context_plane", lambda **_kwargs: _plane(topic="default_text", thread="phatic"))

    packet = build_surface_context_packet(
        user_message="端口18080被占用了",
        request_id="req-task-control",
        session_id="sess-task-control",
        organ_context={"vital_signs": {"cognitive_load_index": 0.2}},
        surface_task_control={
            "task_mode": "technical_help",
            "response_move": "technical_next_step",
            "response_form": "text",
            "question_budget": 0,
        },
    )

    assert packet["reply_plan"]["primary_topic"] == "default_text"
    assert packet["reply_plan"]["task_mode"] == "technical_help"
    assert packet["surface_influence_packet"]["task"]["task_mode"] == "technical_help"
    assert packet["surface_influence_packet"]["task"]["response_move"] == "technical_next_step"
