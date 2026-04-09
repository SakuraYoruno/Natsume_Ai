from __future__ import annotations

import logging
from types import SimpleNamespace

from host_brain.app.http_hooks_runtime_helpers import (
    build_digital_life_runtime_snapshot,
    run_proactive_intent_dispatch,
)


def test_run_proactive_dispatch_uses_social_brain_summary(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_execute_proactive_talk(*, args, context):  # noqa: ANN001
        captured["args"] = dict(args)
        captured["context"] = dict(context)
        return {"status": "EXECUTED", "reason_code": None}

    class _FakeSocialBrain:
        def resolve_scheduler_bond_summary(self, *, session_id: str = "default", user_id=None):  # noqa: ANN001
            assert session_id == "default"
            return {"stage": "FRIEND", "trend": "up", "capability_hint": "can_deepen"}

    monkeypatch.setattr(
        "host_brain.tribe.social_brain_agent.get_social_brain_agent",
        lambda: _FakeSocialBrain(),
    )
    monkeypatch.setattr(
        "host_brain.skills.impl.proactive_talk.execute_proactive_talk",
        _fake_execute_proactive_talk,
    )
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

    result = run_proactive_intent_dispatch(
        {
            "skill_name": "proactive_talk",
            "request_id": "req-social-1",
            "intent_id": "intent-social-1",
            "args": {},
        },
        logger=logging.getLogger("tests.runtime.social"),
        get_ledger=lambda: SimpleNamespace(_db_path=None, db_path=None),
        retention_policy=None,
        ui_pusher=lambda _msg: {"status": "OK"},
    )

    assert result["status"] == "EXECUTED"
    assert captured["context"]["bond_summary"]["stage"] == "FRIEND"
    assert captured["context"]["bond_summary"]["trend"] == "up"


def test_runtime_snapshot_uses_social_brain_bond_snapshot(monkeypatch) -> None:
    class _FakeHardState:
        def as_dict(self) -> dict[str, object]:
            return {"mood_state": "steady", "sys_mode": "normal"}

    class _FakeBondState:
        event_count = 3
        last_event_ts_ms = 1700000001234

        def to_summary(self) -> dict[str, object]:
            return {"stage": "FRIEND", "shared_memory_count": 1}

    class _FakeSocialBrain:
        def resolve_bond_snapshot(self, *, person_id: str | None, bond_summary=None):  # noqa: ANN001
            return {
                "person_id": person_id,
                "bond_state_obj": _FakeBondState(),
                "bond_summary": {"stage": "FRIEND", "trend": "up", "capability_hint": "stable"},
                "bond_stage": "FRIEND",
                "bond_trend": "up",
                "bond_trend_intensity": "mid",
                "capability_hint": "stable",
                "last_event_ts_ms": 1700000001234,
                "intimacy_score": 0.55,
                "texture": None,
                "rift": None,
            }

    monkeypatch.setattr(
        "host_brain.tribe.social_brain_agent.get_social_brain_agent",
        lambda: _FakeSocialBrain(),
    )
    monkeypatch.setattr(
        "host_brain.app.dependencies.get_hard_state",
        lambda: _FakeHardState(),
    )
    monkeypatch.setattr(
        "host_brain.bond.user_identity.get_canonical_bond_user_id",
        lambda: "persona-user-001",
    )

    snapshot = build_digital_life_runtime_snapshot(current_topic="hello")

    assert snapshot["bond_state"]["current_stage"] == "FRIEND"
    assert snapshot["bond_summary"]["stage"] == "FRIEND"
    assert snapshot["bond_state"]["trend"] == "up"
