from __future__ import annotations

from copy import deepcopy

from host_brain.digital_life.domain_reducer import reduce_life_domains


NOW = 1_900_000_000_000


def _reduce(
    before: dict,
    *,
    command_type: str,
    payload: dict,
    command_id: str = "life::domain::unit",
    now_ms: int = NOW,
) -> dict:
    return reduce_life_domains(
        before,
        person_id="person-domain",
        command_id=command_id,
        command_type=command_type,
        payload=payload,
        created_at_ms=now_ms,
    )


def test_foreground_derives_all_kernel_domains_without_mutating_input() -> None:
    before = {"person_id": "person-domain", "version": 0}
    frozen = deepcopy(before)
    payload = {
        "user_message": "今天见到你很开心",
        "assistant_reply": "我也是。",
        "emotion_context": {
            "emotion_tag": "HAPPY",
            "emotion_valence": 0.8,
            "emotion_arousal": 0.55,
        },
        "bond_summary": {"warmth": 0.82, "trust": 0.74},
        "state_patch": {
            "proactive": {
                "last_target": {
                    "platform": "qq",
                    "conversation_id": "conversation-1",
                }
            }
        },
    }

    first = _reduce(before, command_type="foreground_completed", payload=payload)
    second = _reduce(before, command_type="foreground_completed", payload=payload)

    assert first == second
    assert before == frozen
    assert {
        "world",
        "appraisal",
        "affect",
        "homeostasis",
        "relationship",
        "scene",
        "intent",
    }.issubset(first)
    assert first["scene"]["kind"] == "dialogue"
    assert first["intent"]["kind"] == "respond"
    assert first["clock"]["last_user_seen_ms"] == NOW
    assert first["relationship"]["warmth"] > 0.42
    assert first["embodiment"]["emotion_tag"] == "HAPPY"
    assert first["life_action"]["source_command_id"] == "life::domain::unit"


def test_tick_evolves_homeostasis_from_command_time_only() -> None:
    before = _reduce(
        {"person_id": "person-domain", "version": 0},
        command_type="foreground_completed",
        payload={"user_message": "在吗"},
        command_id="life::foreground::seed",
        now_ms=NOW,
    )
    later = NOW + 8 * 3_600_000
    payload = {
        "scheduled_at_ms": later,
        "source": "scheduler_clock",
        "occurrence_payload": {"timezone": "Asia/Shanghai"},
    }

    after = _reduce(
        before,
        command_type="tick",
        payload=payload,
        command_id="life::tick::one",
        now_ms=later,
    )

    assert after["runtime"]["last_integrated_ms"] == later
    assert after["clock"]["idle_minutes"] == 8 * 60
    assert after["affect"]["fatigue"] > before["affect"]["fatigue"]
    assert after["needs"]["contact"] > before["needs"]["contact"]
    assert after["homeostasis"]["balance"] < before["homeostasis"]["balance"]
    assert after["world"]["source_command_id"] == "life::tick::one"


def test_experience_appraises_signal_and_updates_visible_continuity() -> None:
    after = _reduce(
        {"person_id": "person-domain", "version": 0},
        command_type="experience_ingest",
        payload={
            "event_kind": "user_message",
            "content_summary": "这次效果不对",
            "salience": 0.8,
            "valence": -0.7,
            "uncertainty": 0.2,
            "evidence": {"role": "user", "request_id": "request-negative"},
        },
    )

    assert after["appraisal"]["event_type"] == "user_message"
    assert after["appraisal"]["valence"] == -0.7
    assert after["affect"]["tension"] > 0
    assert after["continuity"]["last_visible_event_id"] == "request-negative"
    assert after["intent"]["kind"] == "integrate_experience"


def test_work_event_drives_world_scene_and_intent_then_closes() -> None:
    started = _reduce(
        {"person_id": "person-domain", "version": 0},
        command_type="work_event",
        command_id="life::work::start",
        payload={
            "event_type": "task_started",
            "work_id": "work-1",
            "summary": "架构重构",
            "urgency": "high",
        },
    )
    done = _reduce(
        started,
        command_type="work_event",
        command_id="life::work::done",
        payload={
            "event_type": "task_done",
            "work_id": "work-1",
            "summary": "架构重构完成",
        },
        now_ms=NOW + 1_000,
    )

    assert started["work"]["active"] is True
    assert started["scene"]["kind"] == "work_waiting"
    assert started["intent"]["kind"] == "work_presence"
    assert done["work"]["active"] is False
    assert done["affect"]["pride"] > started["affect"]["pride"]
    assert done["world"]["work_active"] is False
