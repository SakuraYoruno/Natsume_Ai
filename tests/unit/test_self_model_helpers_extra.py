from __future__ import annotations

from dataclasses import dataclass

import host_brain.persona.self_model as sm


@dataclass
class _Record:
    delta: dict[str, float]


@dataclass
class _BondState:
    current_stage: str = "FRIEND"
    event_count: int = 0


def test_compute_emotional_stability_accounts_for_flip_count_and_negative_duration() -> None:
    model = sm.SelfModel()

    stability = model._compute_emotional_stability(
        {"dominant_emotion": "SAD"},
        {
            "emotion_arc": {"start": "HAPPY", "end": "SAD"},
            "emotion_sequence": ["HAPPY", "SAD", "HAPPY", "ANXIOUS"],
            "dominant_emotion": "SAD",
            "dominant_duration_ratio": 0.9,
        },
    )

    assert 0.0 <= stability < 0.5


def test_persistent_self_state_updates_from_inner_event(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(sm, "_DEFAULT_PERSISTENT_SELF_DIR", str(tmp_path))
    sm._PERSISTENT_SELF_CACHE.clear()

    state = sm.load_persistent_self_state(user_id="u-self")
    assert "Soul.md 定义的数字生命" in state["self_identity"]
    assert state["self_update_count"] == 0

    updated = sm.observe_persistent_self_event(
        user_id="u-self",
        event_kind="unit",
        focus="repair",
        emotion_tag="guarded",
        salience=0.82,
        surface_hint="repair_first",
        evidence={"thought_id": "thought_x", "raw": "should_drop"},
        now_ms=123456,
    )
    packet = sm.build_self_directive_packet(updated)

    assert updated["self_update_count"] == 1
    assert updated["agency_score"] > state["agency_score"]
    assert any("生硬" in item for item in updated["unresolved_tensions"])
    assert "自我核心" in "".join(packet["style_anchor"])
    assert "raw" not in updated["recent_self_events"][-1].get("evidence", {})


def test_self_model_snapshot_contains_persistent_self_fields(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(sm, "_DEFAULT_PERSISTENT_SELF_DIR", str(tmp_path))
    sm._PERSISTENT_SELF_CACHE.clear()

    snapshot = sm.SelfModel().build_snapshot(
        user_id="u-snapshot",
        timeline_summary={"dominant_emotion": "CALM"},
        timeline_diary={"dominant_emotion": "CALM"},
        bond_summary={"trend": "稳定"},
    )

    assert snapshot["self_identity"]
    assert snapshot["self_values"]
    assert snapshot["self_boundaries"]
    assert snapshot["active_intentions"]
    assert snapshot["agency_score"] > 0


def test_contradiction_text_and_pool_helpers_cover_empty_paths() -> None:
    assert sm.build_contradiction_awareness_text([{}]) == ""
    assert sm.build_contradiction_awareness_text(
        [{"text": "a"}, {"text": "b"}]
    ) == "a。b"
    assert sm._pick_from_pool([]) == ""
