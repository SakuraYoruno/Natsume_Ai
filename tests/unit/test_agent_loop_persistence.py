"""Historical Agent Loop ledger compatibility without a live loop executor."""

from __future__ import annotations

from pathlib import Path

from host_brain.audit.sqlite_ledger import SQLiteLedger


def _make_ledger() -> SQLiteLedger:
    return SQLiteLedger(db_path=":memory:", check_same_thread=False)


def test_historical_agent_loop_table_and_indexes_remain_readable() -> None:
    ledger = _make_ledger()
    columns = {
        row[1]
        for row in ledger._conn.execute("PRAGMA table_info(agent_loop_log)").fetchall()
    }
    indexes = {
        row[1]
        for row in ledger._conn.execute(
            "SELECT * FROM sqlite_master WHERE type='index' AND tbl_name='agent_loop_log'"
        ).fetchall()
    }

    assert {
        "id",
        "request_id",
        "session_id",
        "goal",
        "round",
        "phase",
        "status",
        "goal_achieved",
        "action_plan_json",
        "report_json",
        "extra_json",
        "ts_ms",
    }.issubset(columns)
    assert {
        "idx_agent_loop_request_id",
        "idx_agent_loop_session_id",
        "idx_agent_loop_ts_ms",
    }.issubset(indexes)


def test_historical_agent_loop_table_is_read_only_through_public_ledger() -> None:
    ledger = _make_ledger()
    assert not hasattr(ledger, "log_agent_loop")
    assert ledger.agent_loop_log == []


def test_historical_agent_loop_column_constant_is_removed() -> None:
    constants_path = (
        Path(__file__).resolve().parents[2]
        / "host_brain/audit/ledger_constants.py"
    )
    assert "AGENT_LOOP_COLUMNS" not in constants_path.read_text(encoding="utf-8-sig")
