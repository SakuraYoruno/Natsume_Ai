from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest

import host_brain.bond.bond_stage_engine as bond_mod


@dataclass
class _Result:
    applied: bool = True
    reason: str = "ok"


@dataclass
class _Milestone:
    milestone_type: str


class _Tracker:
    def __init__(self) -> None:
        self.promoted_calls: list[tuple[str, str, str, int]] = []
        self.milestone_calls: list[tuple[str, str, int]] = []

    def on_stage_promoted(self, *, user_id: str, from_stage: str, to_stage: str, ts_ms: int) -> _Result:
        self.promoted_calls.append((user_id, from_stage, to_stage, ts_ms))
        return _Result(applied=True, reason="promoted")

    def on_milestone(self, *, user_id: str, milestone_key: str, ts_ms: int) -> _Result:
        self.milestone_calls.append((user_id, milestone_key, ts_ms))
        return _Result(applied=True, reason="milestone")


class _LedgerWithSystemEvent:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    def log_system_event(self, *, request_id: str, event_type: str, payload: dict) -> None:
        self.calls.append((request_id, event_type, payload))


class _LedgerPath:
    def __init__(self, db_path: Path) -> None:
        self._db_path = str(db_path)


def _make_restore_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "bond_restore.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE bond_stage_transition_log (payload TEXT, ts_ms INTEGER, transition_status TEXT, user_id TEXT)"
    )
    conn.execute(
        "CREATE TABLE bond_update_log (update_kind TEXT, status TEXT, ts_ms INTEGER, user_id TEXT)"
    )
    conn.commit()
    conn.close()
    return db_path


def test_summary_count_and_texture_mid_hint_and_recent_scores_trim() -> None:
    count = bond_mod.BondSummaryCount(1)
    assert count == "1 shared memory"
    assert int(count) == 1
    assert count == 1
    assert bond_mod.BondEmotionalTexture(intimacy_temperature=0.5).style_hint() == "关系融洽，保持自然互动"

    engine = bond_mod.BondStageEngine()
    for idx in range(bond_mod.TREND_WINDOW * 2 + 2):
        engine.apply_event(user_id="trim-user", update_kind="INTERACTION", evidence_refs=[f"evd://{idx}"], ts_ms=idx + 1)
    state = engine.get_state("trim-user")
    assert len(state.recent_scores) == bond_mod.TREND_WINDOW * 2
    assert bond_mod.BondScoreState(user_id="u", recent_scores=[1, 1, 1]).trend_intensity() == "中"


def test_apply_event_with_milestone_tracker_triggers_growth(monkeypatch) -> None:
    tracker = _Tracker()

    class _MilestoneTracker:
        def on_event(self, *, user_id: str, event_type: str, ts_ms: int, metadata: dict) -> list[_Milestone]:
            assert event_type == "INTERACTION"
            assert metadata["transition_status"] == bond_mod.TRANSITION_STATUS_STABLE
            return [_Milestone("STAGE_PROMOTED:FRIEND→CLOSE_FRIEND")]

    engine = bond_mod.BondStageEngine(milestone_tracker=_MilestoneTracker())
    monkeypatch.setattr(engine, "_get_persona_growth_tracker", lambda: tracker)

    engine.apply_event(
        user_id="milestone-user",
        update_kind="INTERACTION",
        evidence_refs=["evd://m1"],
        ts_ms=1,
    )
    engine._trigger_persona_growth(user_id="milestone-user", from_stage="FRIEND", to_stage="CLOSE_FRIEND", ts_ms=2)

    assert tracker.milestone_calls == [("milestone-user", "stage_promoted", 1)]
    assert tracker.promoted_calls == [("milestone-user", "FRIEND", "CLOSE_FRIEND", 2)]


def test_growth_and_milestone_paths_degrade_gracefully(monkeypatch) -> None:
    engine = bond_mod.BondStageEngine(
        milestone_tracker=type(
            "_BrokenMilestoneTracker",
            (),
            {"on_event": lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom"))},
        )()
    )

    monkeypatch.setattr(engine, "_get_persona_growth_tracker", lambda: None)
    engine._trigger_persona_growth(user_id="u", from_stage="A", to_stage="B", ts_ms=1)

    class _BrokenTracker:
        def on_stage_promoted(self, **kwargs):  # noqa: ANN003
            raise RuntimeError("broken")

        def on_milestone(self, **kwargs):  # noqa: ANN003
            raise RuntimeError("broken")

    monkeypatch.setattr(engine, "_get_persona_growth_tracker", lambda: _BrokenTracker())
    engine._trigger_persona_growth(user_id="u", from_stage="A", to_stage="B", ts_ms=1)
    engine._trigger_milestone_growth("u", "STAGE_PROMOTED:FRIEND→CLOSE_FRIEND", 2)
    engine.apply_event(user_id="u", update_kind="INTERACTION", evidence_refs=["evd://broken"], ts_ms=3)


def test_get_persona_growth_tracker_handles_import_failure(monkeypatch) -> None:
    engine = bond_mod.BondStageEngine()
    real_import = __import__

    def _fake_import(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: ANN001, A002
        if name == "host_brain.tribe.prefrontal_agent":
            raise ImportError("missing")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", _fake_import)

    assert engine._get_persona_growth_tracker() is None


def test_get_persona_growth_tracker_uses_prefrontal_proxy(monkeypatch) -> None:
    class _FakePrefrontal:
        def apply_stage_promotion_growth(self, **kwargs):  # noqa: ANN003
            return {"applied": True, "reason": f"stage:{kwargs.get('to_stage')}"}

        def apply_milestone_growth(self, **kwargs):  # noqa: ANN003
            return {"applied": True, "reason": f"milestone:{kwargs.get('milestone_key')}"}

    monkeypatch.setattr(
        "host_brain.tribe.prefrontal_agent.get_prefrontal_agent",
        lambda: _FakePrefrontal(),
    )

    engine = bond_mod.BondStageEngine()
    tracker = engine._get_persona_growth_tracker()
    promoted = tracker.on_stage_promoted(
        user_id="user-x",
        from_stage="FRIEND",
        to_stage="CLOSE_FRIEND",
        ts_ms=10,
    )
    milestone = tracker.on_milestone(
        user_id="user-x",
        milestone_key="stage_promoted",
        ts_ms=11,
    )

    assert promoted.applied is True
    assert promoted.reason == "stage:CLOSE_FRIEND"
    assert milestone.applied is True
    assert milestone.reason == "milestone:stage_promoted"


def test_get_bond_state_uses_session_id_identity() -> None:
    engine = bond_mod.BondStageEngine()

    snapshot = engine.get_bond_state(session_id="session-bond-user")

    assert snapshot.user_id == "session-bond-user"
    assert snapshot.stage == bond_mod.STAGE_STRANGER


def test_restore_state_prefers_stage_transition_payload_and_adds_delta(tmp_path: Path) -> None:
    db_path = _make_restore_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO bond_stage_transition_log VALUES (?, ?, ?, ?)",
        (json.dumps({"total_score": 30}), 1000, "PROMOTED", "restore-user"),
    )
    conn.executemany(
        "INSERT INTO bond_update_log VALUES (?, ?, ?, ?)",
        [
            ("INTERACTION_ENGAGED", "SUCCESS", 1100, "restore-user"),
            ("DEEP_TALK", "OK", 1200, "restore-user"),
            ("STAGE_TRANSITION", "SUCCESS", 1000, "restore-user"),
        ],
    )
    conn.commit()
    conn.close()

    engine = bond_mod.BondStageEngine(ledger=_LedgerPath(db_path))
    state = engine.get_state("restore-user")

    assert state.total_score == 37
    assert state.current_stage == bond_mod.STAGE_FRIEND
    assert state.event_count == 2
    assert state.last_event_ts_ms == 1200


def test_restore_state_falls_back_to_accumulated_events_when_transition_payload_is_invalid(tmp_path: Path) -> None:
    db_path = _make_restore_db(tmp_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO bond_stage_transition_log VALUES (?, ?, ?, ?)",
        ("{bad json", 1000, "PROMOTED", "fallback-user"),
    )
    conn.executemany(
        "INSERT INTO bond_update_log VALUES (?, ?, ?, ?)",
        [
            ("INTERACTION_ENGAGED", "SUCCESS", 1100, "fallback-user"),
            ("DEEP_TALK", "OK", 1200, "fallback-user"),
            ("IDLE_DECAY", "SUCCESS", 1300, "fallback-user"),
        ],
    )
    conn.commit()
    conn.close()

    engine = bond_mod.BondStageEngine(ledger=_LedgerPath(db_path))
    state = engine.get_state("fallback-user")

    assert state.total_score == 7
    assert state.current_stage == bond_mod.STAGE_STRANGER
    assert state.event_count == 2
    assert state.last_event_ts_ms == 1200


def test_log_transition_falls_back_to_system_event_and_initial_state_can_noop() -> None:
    ledger = _LedgerWithSystemEvent()
    engine = bond_mod.BondStageEngine(ledger=ledger)
    result = bond_mod.StageTransitionResult(
        user_id="sys-user",
        from_stage=bond_mod.STAGE_STRANGER,
        to_stage=bond_mod.STAGE_ACQUAINTANCE,
        transition_status=bond_mod.TRANSITION_STATUS_PROMOTED,
        score_delta=10,
        total_score=10,
        evidence_refs=["evd://sys"],
        transition_id="transition-1",
        ts_ms=100,
        detail="promoted",
    )

    engine._log_transition(result, "req-1")
    engine._log_initial_state(bond_mod.BondScoreState(user_id="noop-user"))

    assert ledger.calls[0][1] == "BOND_STAGE_TRANSITION"
    assert ledger.calls[0][2]["user_id"] == "sys-user"
