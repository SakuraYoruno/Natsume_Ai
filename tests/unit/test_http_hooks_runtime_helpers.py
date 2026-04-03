from __future__ import annotations

import logging
from types import SimpleNamespace

from host_brain.app.http_hooks_runtime_helpers import run_proactive_intent_dispatch


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

