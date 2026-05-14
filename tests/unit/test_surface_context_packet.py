from __future__ import annotations

from types import SimpleNamespace

from host_brain.app.surface_context_packet import (
    SURFACE_CONTEXT_NODE_ID,
    build_surface_context_packet,
)


def _state(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(as_dict=lambda: dict(payload))


def _plane(*, topic: str = "technical_help", thread: str = "debug_help") -> SimpleNamespace:
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
        relationship=SimpleNamespace(state=_state({"bond_stage": "FRIEND"})),
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
