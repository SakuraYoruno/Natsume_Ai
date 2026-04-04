from __future__ import annotations

import logging
from types import SimpleNamespace

from host_brain.app.http_hooks_runtime_helpers import (
    build_digital_life_runtime_snapshot,
    run_proactive_intent_dispatch,
)


class _AsDictWrapper:
    def __init__(self, payload: dict):
        self._payload = dict(payload)

    def as_dict(self) -> dict:
        return dict(self._payload)


class _FakeProfile:
    def to_summary(self) -> dict:
        return {"profile_hint": "平稳"}


class _FakeTimeline:
    def rebuild_summary(self, *, window_ms: int) -> dict:
        return {"window_ms": window_ms}

    def build_daily_diary(self, *, window_ms: int) -> dict:
        _ = window_ms
        return {}


class _FakeHardState:
    def as_dict(self) -> dict:
        return {
            "energy_level": 72,
            "mood_state": "engaged",
            "sys_mode": "focus",
            "safety_flag": False,
            "last_event_id": "mode-evt-1",
            "ts_ms": 1234567890,
            "energy_bucket": "HIGH",
        }


def test_run_proactive_intent_dispatch_passes_response_policy_to_skill(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_execute_proactive_talk(*, args, context):  # noqa: ANN001
        captured["args"] = dict(args)
        captured["context"] = dict(context)
        return {"status": "EXECUTED", "message": {"content": "ok"}}

    monkeypatch.setattr(
        "host_brain.app.http_hooks_runtime_helpers._load_recent_topics",
        lambda *, get_ledger: ["上次没说完的话题"],
    )
    monkeypatch.setattr(
        "host_brain.app.http_hooks_runtime_helpers._load_recent_diary_snippet",
        lambda: "",
    )
    monkeypatch.setattr(
        "host_brain.app.http_hooks_runtime_helpers._load_recent_dream_snippet",
        lambda **_kwargs: "",
    )
    monkeypatch.setattr(
        "host_brain.app.http_hooks_runtime_helpers._build_llm_generator",
        lambda **_kwargs: (lambda _inp: {"greeting": "hi"}),
    )
    monkeypatch.setattr(
        "host_brain.skills.impl.proactive_talk.execute_proactive_talk",
        _fake_execute_proactive_talk,
    )
    monkeypatch.setattr(
        "host_brain.app.emotion_context.get_user_emotion_profile",
        lambda: _FakeProfile(),
    )
    monkeypatch.setattr(
        "host_brain.app.emotion_context._global_emotion_timeline",
        _FakeTimeline(),
    )
    monkeypatch.setattr(
        "host_brain.bond.user_identity.get_canonical_bond_user_id",
        lambda: "persona-user-001",
    )
    monkeypatch.setattr(
        "host_brain.persona.motive_runtime.compute_runtime_motive_state",
        lambda **_kwargs: {"reach_out_drive": 0.62, "dominant_motive": "reach_out"},
    )
    monkeypatch.setattr(
        "host_brain.persona.inner_world.get_inner_world",
        lambda: SimpleNamespace(
            get_state=lambda _user_id: {
                "last_interaction_ts_ms": 0,
                "total_offline_hours": 2.5,
                "loneliness": 0.35,
                "anticipation": 0.2,
                "anticipation_topics": ["上次没说完的话题"],
            }
        ),
    )
    monkeypatch.setattr(
        "host_brain.persona.emotional_silence.get_silence_engine",
        lambda: SimpleNamespace(
            get_silence_state=lambda current_topic="": _AsDictWrapper(
                {
                    "response_willingness": 0.42,
                    "willingness_level": "RELUCTANT",
                    "resistant_topic": current_topic,
                }
            )
        ),
    )
    monkeypatch.setattr(
        "host_brain.persona.body_sense.get_body_sense_model",
        lambda: SimpleNamespace(
            get_state=lambda: _AsDictWrapper(
                {
                    "fatigue": 0.25,
                    "drowsiness": 0.1,
                    "loneliness": 0.35,
                }
            )
        ),
    )
    monkeypatch.setattr(
        "host_brain.persona.response_policy.decide_response_policy",
        lambda _inp: _AsDictWrapper(
            {
                "allow_proactive_after_reply": False,
                "should_be_withdrawn": True,
                "policy_reason_tags": ["willingness:LOW"],
            }
        ),
    )

    result = run_proactive_intent_dispatch(
        {
            "skill_name": "proactive_talk",
            "request_id": "req-proactive-runtime",
            "intent_id": "intent-proactive-runtime",
            "mood_state": "normal",
            "args": {"idle_threshold_ms": 1000},
        },
        logger=logging.getLogger("tests.http_hooks_runtime_helpers"),
        get_ledger=lambda: SimpleNamespace(_db_path=None, db_path=None),
        retention_policy=None,
        ui_pusher=lambda _msg: {"status": "OK"},
    )

    assert result["status"] == "EXECUTED"
    assert captured["args"] == {"idle_threshold_ms": 1000}
    assert captured["context"]["motive_state"]["dominant_motive"] == "reach_out"
    assert captured["context"]["response_policy"]["allow_proactive_after_reply"] is False
    assert captured["context"]["response_policy"]["should_be_withdrawn"] is True


def test_run_proactive_intent_dispatch_routes_personality_tick(monkeypatch) -> None:
    monkeypatch.setattr(
        "host_brain.persona.inner_world.get_inner_world",
        lambda: SimpleNamespace(
            tick=lambda user_id: {
                "status": "OK",
                "user_id": user_id,
                "tick_ran": True,
            }
        ),
    )

    result = run_proactive_intent_dispatch(
        {
            "skill_name": "inner_world_tick",
            "request_id": "req-inner-world-tick",
            "intent_id": "intent-inner-world-tick",
            "emotion_tag": "CALM",
            "mood_state": "steady",
            "args": {"user_id": "persona-user-001"},
        },
        logger=logging.getLogger("tests.http_hooks_runtime_helpers"),
        get_ledger=lambda: SimpleNamespace(_db_path=None, db_path=None),
        retention_policy=None,
        ui_pusher=lambda _msg: {"status": "OK"},
    )

    assert result["status"] == "OK"
    assert result["user_id"] == "persona-user-001"
    assert result["tick_ran"] is True


def test_build_digital_life_runtime_snapshot_collects_core_slots(monkeypatch) -> None:
    class _FakeBondState:
        current_stage = "FRIEND"
        event_count = 8
        last_event_ts_ms = 1700000000000

        def trend(self) -> str:
            return "上升"

        def trend_intensity(self) -> str:
            return "中"

        def capability_hint(self) -> str:
            return "朋友，可深入交流"

        def to_summary(self) -> dict:
            return {
                "stage": "朋友",
                "trend": "上升",
                "capability_hint": "朋友，可深入交流",
                "shared_memory_count": "2",
            }

    class _FakeBondEngine:
        def get_state(self, _user_id: str) -> _FakeBondState:
            return _FakeBondState()

    class _FakeSelfModelSnapshot:
        def as_dict(self) -> dict:
            return {
                "recent_mood_trend": "平稳",
                "emotional_stability": 0.68,
                "dominant_recent_emotion": "NEUTRAL",
                "bond_growth_perception": "稳定",
                "shared_memory_count": 2,
                "last_deep_talk_days_ago": 3,
                "personality_changes": ["温柔度有所提升"],
                "growth_milestones": ["更愿意延续话题"],
            }

    class _FakeNarrativeResult:
        def to_prompt_slot(self) -> dict:
            return {
                "narratives": ["我还记得你说过那个项目。"],
                "shared_memory_count": 2,
                "relationship_duration": "认识一段时间了",
            }

    class _FakeNarrativeLayer:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            pass

        def build_narrative(self, **kwargs) -> _FakeNarrativeResult:  # noqa: ANN003
            return _FakeNarrativeResult()

    monkeypatch.setattr(
        "host_brain.bond.user_identity.get_canonical_bond_user_id",
        lambda: "persona-user-001",
    )
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_hard_state",
        lambda: _FakeHardState(),
    )
    monkeypatch.setattr(
        "host_brain.app.dependencies._activity_monitor_ref",
        SimpleNamespace(get_status=lambda: {"running": True, "idle_minutes": 4.5}),
    )
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_bond_stage_engine",
        lambda: _FakeBondEngine(),
    )
    monkeypatch.setattr(
        "host_brain.persona.inner_world.get_inner_world",
        lambda: SimpleNamespace(
            get_state=lambda _user_id: {
                "last_interaction_ts_ms": 1700000000000,
                "total_offline_hours": 12.0,
                "loneliness": 0.42,
                "anticipation": 0.35,
                "anticipation_topics": ["那个项目"],
            }
        ),
    )
    monkeypatch.setattr(
        "host_brain.persona.motive_runtime.build_offline_state_from_inner_state",
        lambda _inner_state: {
            "offline_hours": 12.0,
            "loneliness_level": 0.42,
            "anticipation_level": 0.35,
            "anticipation_topics": ["那个项目"],
            "should_express_waiting": True,
        },
    )
    monkeypatch.setattr(
        "host_brain.persona.emotional_silence.get_silence_engine",
        lambda: SimpleNamespace(
            get_silence_state=lambda current_topic="": _AsDictWrapper(
                {
                    "response_willingness": 0.66,
                    "willingness_level": "NORMAL",
                    "resistant_topic": current_topic,
                }
            )
        ),
    )
    monkeypatch.setattr(
        "host_brain.persona.body_sense.get_body_sense_model",
        lambda: SimpleNamespace(
            get_state=lambda: _AsDictWrapper(
                {
                    "fatigue": 0.18,
                    "fatigue_level": "FRESH",
                    "drowsiness": 0.12,
                    "drowsiness_level": "AWAKE",
                }
            )
        ),
    )
    monkeypatch.setattr(
        "host_brain.app.emotion_context.get_user_emotion_profile",
        lambda: _FakeProfile(),
    )
    monkeypatch.setattr(
        "host_brain.app.emotion_context._global_emotion_timeline",
        _FakeTimeline(),
    )
    monkeypatch.setattr(
        "host_brain.persona.persona_engine.PersonaEngine.get_instance",
        classmethod(lambda cls, **_kwargs: SimpleNamespace(get_self_model_snapshot=lambda *args, **kwargs: _FakeSelfModelSnapshot())),
    )
    monkeypatch.setattr(
        "host_brain.persona.self_reflection.build_self_reflection_pulse",
        lambda snapshot, **_kwargs: {
            "self_awareness": f"我现在大概处在{snapshot.get('recent_mood_trend', '平稳')}状态。",
            "growth_note": "最近更会接住对话了。",
        },
    )
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_episodic_store",
        lambda: SimpleNamespace(_records={}),
    )
    monkeypatch.setattr(
        "host_brain.bond.bond_narrative.BondNarrativeLayer",
        _FakeNarrativeLayer,
    )
    monkeypatch.setattr(
        "host_brain.persona.motive_engine.compute_motive_state",
        lambda _inp: _AsDictWrapper(
            {
                "reach_out_drive": 0.48,
                "care_drive": 0.31,
                "withdraw_drive": 0.12,
                "continuity_drive": 0.57,
                "self_protect_drive": 0.09,
                "dominant_motive": "continuity",
            }
        ),
    )
    monkeypatch.setattr(
        "host_brain.persona.response_policy.decide_response_policy",
        lambda _inp: _AsDictWrapper(
            {
                "max_chars": 180,
                "allow_followup": True,
                "allow_topic_shift": False,
                "allow_proactive_after_reply": True,
                "should_be_withdrawn": False,
            }
        ),
    )

    snapshot = build_digital_life_runtime_snapshot(current_topic="那个项目")

    assert snapshot["persona_user_id"] == "persona-user-001"
    assert snapshot["sys_mode"] == "focus"
    assert snapshot["hard_state"]["mood_state"] == "engaged"
    assert snapshot["activity_monitor"]["running"] is True
    assert snapshot["bond_state"]["current_stage"] == "FRIEND"
    assert snapshot["offline_state"]["offline_hours"] == 12.0
    assert snapshot["silence_state"]["resistant_topic"] == "那个项目"
    assert snapshot["body_sense"]["fatigue_level"] == "FRESH"
    assert snapshot["self_reflection_pulse"]["growth_note"] == "最近更会接住对话了。"
    assert snapshot["bond_narrative"]["shared_memory_count"] == 2
    assert snapshot["motive_state"]["dominant_motive"] == "continuity"
    assert snapshot["response_policy"]["allow_topic_shift"] is False
    assert snapshot["runtime_summary"]["current_state"]["sys_mode"] == "focus"
    assert snapshot["runtime_summary"]["current_state"]["absence_band"] == "6h_plus"
    assert snapshot["runtime_summary"]["reply_posture"]["allow_topic_shift"] is False
    assert "mode:focus" in snapshot["runtime_summary"]["why_reply"]
    assert "motive:continuity" in snapshot["runtime_summary"]["why_reply"]
    assert snapshot["slot_status"]["response_policy"] == "live"
    assert snapshot["degraded"] is False
