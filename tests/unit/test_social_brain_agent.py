from __future__ import annotations

from types import SimpleNamespace

from host_brain.tribe.social_brain_agent import SocialBrainAgent


class _FakeRift:
    severity = "low"
    repair_progress = 0.4

    def is_active(self) -> bool:
        return True


class _FakeBondState:
    current_stage = "FRIEND"
    event_count = 6
    last_event_ts_ms = 1700000000000
    intimacy_score = 0.62
    texture = SimpleNamespace(intimacy_temperature=0.71)
    rift = _FakeRift()

    def trend(self) -> str:
        return "up"

    def trend_intensity(self) -> str:
        return "mid"

    def capability_hint(self) -> str:
        return "can_deepen"

    def to_summary(self) -> dict[str, object]:
        return {
            "stage": "FRIEND",
            "trend": "up",
            "capability_hint": "can_deepen",
            "shared_memory_count": 2,
        }


def test_social_brain_resolve_bond_snapshot_with_live_engine(monkeypatch) -> None:
    class _Engine:
        def get_state(self, user_id: str):
            assert user_id == "user_1"
            return _FakeBondState()

    monkeypatch.setattr("host_brain.app.dependencies.get_bond_stage_engine", lambda: _Engine())

    snapshot = SocialBrainAgent().resolve_bond_snapshot(person_id="user_1")

    assert snapshot["bond_stage"] == "FRIEND"
    assert snapshot["bond_trend"] == "up"
    assert snapshot["bond_trend_intensity"] == "mid"
    assert snapshot["capability_hint"] == "can_deepen"
    assert snapshot["intimacy_score"] == 0.62
    assert snapshot["last_event_ts_ms"] == 1700000000000
    assert snapshot["bond_summary"]["stage"] == "FRIEND"
    assert snapshot["bond_state_obj"] is not None


def test_social_brain_resolve_bond_snapshot_uses_fallback_summary(monkeypatch) -> None:
    monkeypatch.setattr("host_brain.app.dependencies.get_bond_stage_engine", lambda: None)

    snapshot = SocialBrainAgent().resolve_bond_snapshot(
        person_id="user_2",
        bond_summary={"stage": "FRIEND", "trend": "steady", "capability_hint": "short_chat"},
    )

    assert snapshot["bond_stage"] == "FRIEND"
    assert snapshot["bond_trend"] == "steady"
    assert snapshot["capability_hint"] == "short_chat"
    assert snapshot["bond_state_obj"] is None


def test_social_brain_resolve_bond_stage_uppercases_default(monkeypatch) -> None:
    monkeypatch.setattr("host_brain.app.dependencies.get_bond_stage_engine", lambda: None)
    stage = SocialBrainAgent().resolve_bond_stage(person_id=None, default="friend")
    assert stage == "FRIEND"


def test_social_brain_resolve_scheduler_bond_summary(monkeypatch) -> None:
    class _Snapshot:
        stage = "CLOSE_FRIEND"
        trend = "up"
        capability_hint = "more_intimate"

    class _Engine:
        def get_bond_state(self, *, session_id: str):
            assert session_id == "default"
            return _Snapshot()

    monkeypatch.setattr("host_brain.app.dependencies.get_bond_stage_engine", lambda: _Engine())

    summary = SocialBrainAgent().resolve_scheduler_bond_summary(session_id="default")

    assert summary == {
        "stage": "CLOSE_FRIEND",
        "trend": "up",
        "capability_hint": "more_intimate",
    }


def test_social_brain_resolve_stage_engine(monkeypatch) -> None:
    sentinel = object()
    monkeypatch.setattr("host_brain.app.dependencies.get_bond_stage_engine", lambda: sentinel)
    assert SocialBrainAgent().resolve_stage_engine() is sentinel
