from __future__ import annotations

import json
import time

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.runtime.environment_state import build_environment_snapshot, day_phase_for_hour, meal_phase_for_time
from host_brain.runtime.life_continuity import advance_life_to_now, get_life_state
from host_brain.runtime.life_organs import append_life_experience, get_organ_runtime_state


def test_environment_snapshot_classifies_afternoon_without_breakfast() -> None:
    assert day_phase_for_hour(14) == "afternoon"
    assert meal_phase_for_time(14, 5) == "none"

    snapshot = build_environment_snapshot(now_ms=1_780_825_500_000, timezone_name="Asia/Shanghai")

    assert snapshot["timezone"] == "Asia/Shanghai"
    assert "surface_facts" in snapshot
    assert snapshot["meal_phase"] in {"none", "breakfast_window", "lunch_window", "dinner_window", "late_snack_window"}


def test_advance_life_to_now_compensates_elapsed_time() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    state = get_life_state(ledger, person_id="default")
    old_ms = int(time.time() * 1000) - 40 * 60 * 1000
    state["runtime"] = {"last_integrated_ms": old_ms, "last_heartbeat_ms": old_ms, "catchup_count": 0}
    state["clock"]["last_tick_ms"] = old_ms
    state["clock"]["last_user_seen_ms"] = old_ms
    ledger._conn.execute(
        "INSERT OR REPLACE INTO digital_life_state(person_id, version, state_json, updated_at_ms) VALUES(?, ?, ?, ?)",
        ("default", int(state.get("version") or 1), json.dumps(state, ensure_ascii=False), old_ms),
    )
    ledger._conn.commit()

    result = advance_life_to_now(ledger=ledger, person_id="default", reason="foreground_user_message", force=True)
    after = result["state"]

    assert result["status"] == "OK"
    assert after["runtime"]["last_integrated_ms"] >= old_ms
    assert after["runtime"]["catchup_count"] >= 1
    assert after["clock"]["idle_minutes"] >= 39
    assert after["life_pressure"]["absence"] > 0.3


def test_inner_stream_tick_updates_organs_and_inner_world() -> None:
    ledger = SQLiteLedger(db_path=":memory:")

    event = append_life_experience(
        ledger=ledger,
        person_id="default",
        source_kind="inner_world",
        event_kind="inner_stream_tick",
        content_summary="刚才那根线还没断，注意力还在往回勾。",
        evidence={"focus": "relationship", "felt_tendency": "care_with_restraint", "inhibition": 0.12},
        salience=0.4,
        valence=0.0,
        uncertainty=0.05,
        consume=True,
    )
    organs = get_organ_runtime_state(ledger=ledger, person_id="default")

    assert event["event_id"]
    assert organs["organ_count"] == 23
    assert organs["organs"]["default_mode"]["memory_trace"]["current_preoccupation"]
    assert organs["organs"]["hippocampus"]["dominant_drive"] == "private_continuity_trace"
