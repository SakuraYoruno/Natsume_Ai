from __future__ import annotations

import logging
import sqlite3
from types import SimpleNamespace

from host_brain.app.http_hooks_runtime_helpers import (
    _build_runtime_summary,
    _load_last_interaction_ts,
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


def test_runtime_summary_offline_continuity_matrix_surfaces_cross_day_cross_week_differences() -> None:
    cases = [
        ("24h_plus", 26.0, "light_reconnect", "balanced", 0.15),
        ("72h_plus", 80.0, "reunion", "warm_reconnect", 0.18),
        ("7d_plus", 8 * 24.0, "deep_reunion", "warm_reconnect", 0.22),
    ]

    summaries = [
        _build_runtime_summary(
            current_topic="那个项目",
            sys_mode="normal",
            activity_monitor={},
            bond_state={"current_stage": "FRIEND"},
            bond_summary={"stage": "FRIEND"},
            offline_state={
                "offline_hours": offline_hours,
                "offline_duration_ms": int(offline_hours * 3600 * 1000),
                "absence_band": absence_band,
                "loneliness_level": 0.42,
                "anticipation_level": 0.46,
                "awakening_hint": "还记得那个项目",
            },
            silence_state={"willingness_level": "NORMAL"},
            body_sense={"fatigue_level": "FRESH"},
            self_reflection_pulse={"growth_note": "最近更会把对话接住了。"},
            bond_narrative={"narratives": ["我还记得你说过那个项目。"]},
            motive_state={
                "dominant_motive": "continuity",
                "motive_reason_tags": [f"continuity:absence_band={absence_band}"],
            },
            response_policy={
                "max_chars": 220,
                "max_sentences": 4,
                "allow_followup": True,
                "allow_topic_shift": True,
                "allow_proactive_after_reply": True,
                "should_be_withdrawn": False,
                "warmth_floor": warmth_floor,
                "intimacy_ceiling": 0.5,
                "policy_reason_tags": [f"offline:{absence_band}"],
            },
            slot_status={"offline_state": "live", "response_policy": "live"},
            degraded_reasons=[],
        )
        for absence_band, offline_hours, _style, _reply_posture, warmth_floor in cases
    ]

    reconnect_styles = [summary["current_state"]["offline_reconnect_style"] for summary in summaries]
    reply_postures = [summary["reply_posture"]["style"] for summary in summaries]
    warmth_floors = [summary["reply_posture"]["warmth_floor"] for summary in summaries]

    assert reconnect_styles == ["light_reconnect", "reunion", "deep_reunion"]
    assert reply_postures == ["balanced", "warm_reconnect", "warm_reconnect"]
    assert warmth_floors == [0.15, 0.18, 0.22]

    for summary, (absence_band, _offline_hours, reconnect_style, _reply_posture, _warmth_floor) in zip(summaries, cases):
        assert summary["current_state"]["absence_band"] == absence_band
        assert summary["current_state"]["offline_reconnect_style"] == reconnect_style
        assert summary["decision_chain"][0] == "mode:normal"
        assert f"offline:{absence_band}->{reconnect_style}" in summary["decision_chain"]
        assert summary["core_slots"]["offline_state"]["absence_band"] == absence_band
        assert summary["core_slots"]["offline_state"]["offline_reconnect_style"] == reconnect_style
        assert summary["core_slots"]["response_policy"]["reply_posture"] == summary["reply_posture"]["style"]
        assert f"offline={absence_band}" in summary["headline"]
        assert f"offline:{absence_band}" in summary["why_reply"]
        assert "motive:continuity" in summary["why_reply"]


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
        "host_brain.tribe.immune_agent.get_immune_agent",
        lambda: SimpleNamespace(
            export_runtime_state=lambda: {
                "enabled": True,
                "blocking_enabled": False,
                "warn_threshold": 0.55,
                "block_threshold": 0.85,
                "ruleset_version": "immune_v1",
                "counters": {"total": 12, "allow": 10, "report": 2, "block": 0},
                "last_inspection": {"decision": "ALLOW"},
            },
            inspect_text=lambda **_kwargs: {
                "decision": "ALLOW",
                "risk_level": "none",
                "score": 0.0,
                "reason_codes": [],
            },
        ),
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
        "host_brain.tribe.limbic_agent.get_limbic_agent",
        lambda: SimpleNamespace(
            compute_runtime_motive_state=lambda **_kwargs: {
                "reach_out_drive": 0.62,
                "dominant_motive": "reach_out",
            },
            resolve_inner_world_state=lambda person_id=None: {
                "last_interaction_ts_ms": 0,
                "total_offline_hours": 2.5,
                "loneliness": 0.35,
                "anticipation": 0.2,
                "anticipation_topics": ["上次没说完的话题"],
                "user_id": person_id,
            },
            resolve_offline_state=lambda person_id=None, now_ms=None: {
                "offline_hours": 2.5,
                "offline_duration_ms": int(2.5 * 3600 * 1000),
                "absence_band": "recent",
                "loneliness_level": 0.35,
                "anticipation_level": 0.2,
                "anticipation_topics": ["上次没说完的话题"],
            },
            resolve_offline_reconnect_profile=lambda offline_state=None: {
                "absence_band": "6h_plus",
                "reconnect_style": "soft_resume",
            },
            resolve_silence_state=lambda current_topic="": {
                "response_willingness": 0.42,
                "willingness_level": "RELUCTANT",
                "resistant_topic": current_topic,
            },
            resolve_body_sense_state=lambda: {
                "fatigue": 0.25,
                "drowsiness": 0.1,
                "loneliness": 0.35,
            },
            decide_response_policy=lambda **_kwargs: {
                "allow_proactive_after_reply": False,
                "should_be_withdrawn": True,
                "policy_reason_tags": ["willingness:LOW"],
            },
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


def test_run_proactive_intent_dispatch_does_not_treat_mood_state_as_sys_mode(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_execute_proactive_talk(*, args, context):  # noqa: ANN001
        captured["args"] = dict(args)
        captured["context"] = dict(context)
        return {"status": "EXECUTED", "reason_code": None}

    monkeypatch.setattr(
        "host_brain.app.http_hooks_runtime_helpers._load_recent_topics",
        lambda **_kwargs: [],
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
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "host_brain.skills.impl.proactive_talk.execute_proactive_talk",
        _fake_execute_proactive_talk,
    )

    result = run_proactive_intent_dispatch(
        {
            "skill_name": "proactive_talk",
            "request_id": "req-proactive-scheduler-shape",
            "intent_id": "intent-proactive-scheduler-shape",
            "mood_state": "steady",
            "args": {"idle_threshold_ms": 0},
        },
        logger=logging.getLogger("tests.http_hooks_runtime_helpers"),
        get_ledger=lambda: SimpleNamespace(_db_path=None, db_path=None),
        retention_policy=None,
        ui_pusher=lambda _msg: {"status": "OK"},
    )

    assert result["status"] == "EXECUTED"
    assert captured["context"]["sys_mode"] == "normal"


def test_load_last_interaction_ts_ignores_future_intent_rows(tmp_path) -> None:
    db_path = tmp_path / "audit.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE intent_log (ts_ms INTEGER)")
    conn.execute("INSERT INTO intent_log(ts_ms) VALUES (?)", (1_800_000_000_003,))
    conn.execute("INSERT INTO intent_log(ts_ms) VALUES (?)", (1_775_314_738_620,))
    conn.commit()
    conn.close()

    result = _load_last_interaction_ts(
        get_ledger=lambda: SimpleNamespace(_db_path=str(db_path), db_path=str(db_path)),
        now_ts_ms=1_775_315_000_000,
    )

    assert result == 1_775_314_738_620


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
        "host_brain.tribe.limbic_agent.get_limbic_agent",
        lambda: SimpleNamespace(
            compute_motive_state=lambda **_kwargs: {
                "reach_out_drive": 0.48,
                "care_drive": 0.31,
                "withdraw_drive": 0.12,
                "continuity_drive": 0.57,
                "self_protect_drive": 0.09,
                "dominant_motive": "continuity",
            },
            resolve_inner_world_state=lambda person_id=None: {
                "user_id": str(person_id or "persona-user-001"),
                "last_interaction_ts_ms": 1700000000000,
                "last_tick_ts_ms": 1700000009000,
                "total_offline_hours": 12.0,
                "loneliness": 0.42,
                "anticipation": 0.35,
                "anticipation_topics": ["那个项目"],
            },
            resolve_offline_state=lambda person_id=None, now_ms=None: {
                "offline_hours": 12.0,
                "loneliness_level": 0.42,
                "anticipation_level": 0.35,
                "anticipation_topics": ["那个项目"],
                "should_express_waiting": True,
            },
            resolve_offline_reconnect_profile=lambda offline_state=None: {
                "absence_band": "6h_plus",
                "reconnect_style": "soft_resume",
            },
            resolve_silence_state=lambda current_topic="": {
                "response_willingness": 0.66,
                "willingness_level": "NORMAL",
                "resistant_topic": current_topic,
            },
            resolve_body_sense_state=lambda: {
                "fatigue": 0.18,
                "fatigue_level": "FRESH",
                "drowsiness": 0.12,
                "drowsiness_level": "AWAKE",
            },
            decide_response_policy=lambda **_kwargs: {
                "max_chars": 180,
                "allow_followup": True,
                "allow_topic_shift": False,
                "allow_proactive_after_reply": True,
                "should_be_withdrawn": False,
            },
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
    monkeypatch.setattr(
        "host_brain.tribe.somatic_agent.get_somatic_agent",
        lambda: SimpleNamespace(
            export_runtime_state=lambda: {
                "vts_connected": True,
                "last_emotion_tag": "NEUTRAL",
                "fatigue_level": 0.16,
            }
        ),
    )
    monkeypatch.setattr(
        "host_brain.tribe.peripheral_agent.get_peripheral_agent",
        lambda: SimpleNamespace(
            export_runtime_state=lambda: {
                "connected_platforms": ["wechat"],
                "suppressed": False,
            }
        ),
    )
    monkeypatch.setattr(
        "host_brain.tribe.circadian_agent.get_circadian_agent",
        lambda: SimpleNamespace(
            export_runtime_state=lambda: {
                "phase": "dusk",
                "proactive_multiplier": 0.68,
                "sleep_pressure": 0.55,
            }
        ),
    )
    monkeypatch.setattr(
        "host_brain.tribe.proprioception_agent.get_proprioception_agent",
        lambda: SimpleNamespace(
            export_runtime_state=lambda: {
                "cognitive_load_index": 0.31,
                "readiness_hint": 0.62,
                "continuity_hint": 0.71,
            }
        ),
    )
    monkeypatch.setattr(
        "host_brain.tribe.initiative_governor_agent.get_initiative_governor_agent",
        lambda: SimpleNamespace(
            get_last_decision=lambda: {
                "decision": "ALLOW",
                "reason_code": "PASS_ALL_GATES",
            }
        ),
    )
    monkeypatch.setattr(
        "host_brain.tribe.default_mode_agent.get_default_mode_agent",
        lambda: SimpleNamespace(
            export_runtime_state=lambda: {
                "last_focus": "project",
                "continuity_score": 0.66,
                "tick_count": 3,
            }
        ),
    )
    monkeypatch.setattr(
        "host_brain.tribe.mirror_neuron_agent.get_mirror_neuron_agent",
        lambda: SimpleNamespace(
            export_runtime_state=lambda: {
                "last_resonance_strength": 0.43,
                "last_co_regulation_hint": "resonate",
            }
        ),
    )
    monkeypatch.setattr(
        "host_brain.tribe.hippocampus_agent.get_hippocampus_agent",
        lambda: SimpleNamespace(
            resolve_memory_layer_snapshot=lambda: {
                "episodic": {"id": "ep-1"},
                "semantic": {"id": "sm-1"},
                "autobiographical": {"id": "ab-1"},
                "last_write_type": "episodic",
            }
        ),
    )
    monkeypatch.setattr(
        "host_brain.tribe.language_system_agent.get_language_system_agent",
        lambda: SimpleNamespace(
            state_registry=SimpleNamespace(
                get_state=lambda _ns: {
                    "state": {
                        "last_dual_system": {
                            "wernicke": {"intent_type": "question"},
                            "broca": {"response_mode": "direct_answer"},
                            "style": {"style": "warm", "tone": "balanced"},
                        }
                    }
                }
            ),
            resolve_dual_system=lambda **_kwargs: {
                "wernicke": {"intent_type": "question"},
                "broca": {"response_mode": "direct_answer"},
                "style": {"style": "warm", "tone": "balanced"},
            },
        ),
    )
    monkeypatch.setattr(
        "host_brain.tribe.interoception_agent.get_interoception_agent",
        lambda: SimpleNamespace(
            export_runtime_state=lambda: {
                "interoceptive_index": 0.31,
                "body_budget": 0.72,
                "recovery_urgency": 0.28,
            }
        ),
    )
    monkeypatch.setattr(
        "host_brain.tribe.motor_cortex_agent.get_motor_cortex_agent",
        lambda: SimpleNamespace(
            export_runtime_state=lambda: {
                "last_plan_id": "motor_plan_1",
                "last_dispatch_ok": True,
                "last_dispatch_reason": None,
            }
        ),
    )
    monkeypatch.setattr(
        "host_brain.tribe.cerebellum_agent.get_cerebellum_agent",
        lambda: SimpleNamespace(
            export_runtime_state=lambda: {
                "last_strategy": "NO_ADJUST",
                "last_urgency": 0.18,
            }
        ),
    )
    monkeypatch.setattr(
        "host_brain.tribe.glymphatic_agent.get_glymphatic_agent",
        lambda: SimpleNamespace(
            export_runtime_state=lambda: {
                "executed": True,
                "reason_code": "RECOVERY_WINDOW_OPEN",
                "phase": "night",
            }
        ),
    )
    monkeypatch.setattr(
        "host_brain.tribe.vestibular_agent.get_vestibular_agent",
        lambda: SimpleNamespace(
            export_runtime_state=lambda: {
                "stability_state": "stable",
                "jitter_index": 0.24,
                "route_bias": "normal",
            }
        ),
    )

    monkeypatch.setattr("host_brain.app.http_hooks_runtime_helpers._time.time", lambda: 1700000010.0)
    monkeypatch.setattr(
        "host_brain.scheduler.scheduler_routes.get_scheduler_health_snapshot_safe",
        lambda: {
            "scheduler_status": "HEALTHY",
            "total_tasks": 9,
            "enabled_tasks": 9,
            "suspended_tasks": 0,
            "error_tasks": 0,
            "tasks": [
                {
                    "task_id": "inner_world_tick",
                    "status": "OK",
                    "enabled": True,
                    "last_run_ts": 1700000009000,
                    "trigger_config": {"seconds": 60},
                },
                {
                    "task_id": "silence_tick",
                    "status": "OK",
                    "enabled": True,
                    "last_run_ts": 1700000009000,
                    "trigger_config": {"seconds": 60},
                },
                {
                    "task_id": "body_sense_tick",
                    "status": "OK",
                    "enabled": True,
                    "last_run_ts": 1700000009000,
                    "trigger_config": {"seconds": 60},
                },
            ],
            "ts_ms": 1700000010000,
        },
    )

    snapshot = build_digital_life_runtime_snapshot(current_topic="那个项目")

    assert snapshot["persona_user_id"] == "persona-user-001"
    assert snapshot["sys_mode"] == "focus"
    assert snapshot["hard_state"]["mood_state"] == "engaged"
    assert snapshot["activity_monitor"]["running"] is True
    assert snapshot["immune_state"]["enabled"] is True
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
    assert snapshot["runtime_summary"]["current_state"]["offline_reconnect_style"] == "soft_resume"
    assert snapshot["runtime_summary"]["reply_posture"]["allow_topic_shift"] is False
    assert snapshot["runtime_summary"]["decision_chain"][0] == "mode:focus"
    assert "offline:6h_plus->soft_resume" in snapshot["runtime_summary"]["decision_chain"]
    assert snapshot["runtime_summary"]["core_slots"]["sys_mode"]["idle_minutes"] == 4.5
    assert snapshot["runtime_summary"]["core_slots"]["offline_state"]["anticipation_topics"] == ["那个项目"]
    assert snapshot["runtime_summary"]["core_slots"]["bond_narrative"]["shared_memory_count"] == 2
    assert snapshot["runtime_summary"]["core_slots"]["motive_state"]["active_drives"]["continuity_drive"] == 0.57
    assert snapshot["runtime_summary"]["core_slots"]["response_policy"]["reply_posture"] == "focused"
    assert "mode:focus" in snapshot["runtime_summary"]["why_reply"]
    assert "motive:continuity" in snapshot["runtime_summary"]["why_reply"]
    assert snapshot["background_runtime"]["scheduler_status"] == "HEALTHY"
    assert snapshot["background_runtime"]["core_ticks_registered"] is True
    assert snapshot["background_runtime"]["core_tick_tasks"]["inner_world_tick"]["status"] == "OK"
    assert snapshot["background_runtime"]["core_tick_tasks"]["inner_world_tick"]["last_reason_code"] is None
    assert snapshot["background_runtime"]["proactive_task"]["task_id"] == "proactive_talk_periodic"
    assert snapshot["background_runtime"]["proactive_task"]["status"] == "MISSING"
    assert snapshot["background_runtime"]["gate_blocked_tasks"] == []
    assert snapshot["background_runtime"]["persona_user_alignment_ok"] is True
    assert snapshot["background_runtime"]["tick_health"] == "fresh"
    assert snapshot["background_runtime"]["is_advancing"] is True
    assert snapshot["somatic_state"]["vts_connected"] is True
    assert snapshot["peripheral_state"]["connected_platforms"] == ["wechat"]
    assert snapshot["circadian_state"]["phase"] == "dusk"
    assert snapshot["proprioception_state"]["cognitive_load_index"] == 0.31
    assert snapshot["initiative_decision"]["decision"] == "ALLOW"
    assert snapshot["default_mode_state"]["last_focus"] == "project"
    assert snapshot["mirror_neuron_state"]["last_co_regulation_hint"] == "resonate"
    assert snapshot["memory_layers"]["last_write_type"] == "episodic"
    assert snapshot["language_dual_system"]["broca"]["response_mode"] == "direct_answer"
    assert snapshot["interoception_state"]["interoceptive_index"] == 0.31
    assert snapshot["motor_cortex_state"]["last_plan_id"] == "motor_plan_1"
    assert snapshot["cerebellum_state"]["last_strategy"] == "NO_ADJUST"
    assert snapshot["glymphatic_state"]["executed"] is True
    assert snapshot["vestibular_state"]["stability_state"] == "stable"
    assert snapshot["slot_status"]["immune_state"] == "live"
    assert snapshot["slot_status"]["response_policy"] == "live"
    assert snapshot["slot_status"]["homeostasis_report"] == "live"
    assert snapshot["slot_status"]["interoception_state"] == "live"
    assert snapshot["slot_status"]["vestibular_state"] == "live"
    assert snapshot["degraded"] is False
