from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
import sqlite3
import threading

from host_brain.persistence.world_model_query_repository import WorldModelQueryRepository
from host_brain.runtime.world_model import build_world_snapshot


ROOT = Path(__file__).resolve().parents[2]
LIFE_ASSET = ROOT / "host_brain" / "audit" / "migrations" / "0057_life_occ.sql"
NOW = 1_900_000_000_000


def _seed(conn: sqlite3.Connection) -> None:
    state = {
        "person_id": "person-1",
        "version": 5,
        "runtime": {"alive_status": "alive", "last_integrated_ms": NOW - 1},
        "clock": {"day_phase": "evening"},
        "affect": {"mood": "curious"},
        "needs": {"contact": 0.4},
        "work": {"active": True, "updated_at_ms": NOW - 2},
        "life_action": {"action_kind": "focus", "action_id": "action-1"},
    }
    conn.execute(
        "INSERT INTO digital_life_state(person_id, version, state_json, updated_at_ms) VALUES (?, ?, ?, ?)",
        ("person-1", 5, json.dumps(state), NOW - 1),
    )
    conn.executemany(
        """
        INSERT INTO work_environment_event(
            event_id, person_id, event_type, work_id, summary, urgency,
            emotional_effect_json, created_at_ms, active
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                f"work-{index}",
                "person-1",
                "task_progress",
                f"task-{index}",
                f"summary-{index}",
                "high" if index == 4 else "medium",
                json.dumps({"valence": index / 10, "arousal": 0.5}),
                NOW + index,
                1 if index == 4 else 0,
            )
            for index in range(5)
        ],
    )
    conn.commit()


def _durable_db(tmp_path: Path) -> Path:
    path = tmp_path / "world-model.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.executescript(LIFE_ASSET.read_text(encoding="utf-8-sig"))
        _seed(conn)
    return path


def test_repository_reads_one_sorted_limited_snapshot_with_vad_json(tmp_path: Path) -> None:
    repository = WorldModelQueryRepository(_durable_db(tmp_path))

    snapshot = repository.read_snapshot(person_id="person-1", work_event_limit=3)

    assert snapshot.life_state is not None
    assert snapshot.life_state["version"] == 5
    assert snapshot.life_state["affect"]["mood"] == "curious"
    assert [event.event_id for event in snapshot.work_events] == [
        "work-4",
        "work-3",
        "work-2",
    ]
    assert snapshot.work_events[0].emotional_effect == {
        "valence": 0.4,
        "arousal": 0.5,
    }
    assert snapshot.life_state_source_available is True
    assert snapshot.work_event_source_available is True


def test_in_memory_compatibility_connection_is_borrowed_only_by_adapter() -> None:
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.executescript(LIFE_ASSET.read_text(encoding="utf-8-sig"))
    _seed(connection)

    class MemoryLedger:
        canonical_db_path = ":memory:"
        _conn = connection
        _write_lock = threading.RLock()

    try:
        snapshot = WorldModelQueryRepository.from_ledger(MemoryLedger()).read_snapshot(
            person_id="person-1",
            work_event_limit=2,
        )
        assert snapshot.life_state is not None
        assert [event.event_id for event in snapshot.work_events] == [
            "work-4",
            "work-3",
        ]
    finally:
        connection.close()


def test_world_model_fail_soft_preserves_public_snapshot_api(tmp_path: Path) -> None:
    class MissingLedger:
        canonical_db_path = str(tmp_path / "missing.sqlite3")

        def __getattr__(self, name: str):
            if name in {"_conn", "_db_path"}:
                raise AssertionError("runtime reached through ledger internals")
            raise AttributeError(name)

    snapshot = asyncio.run(
        build_world_snapshot(
            ledger=MissingLedger(),
            person_id="person-1",
            now_ms=NOW,
        )
    )
    assert snapshot["status"] == "OK"
    assert snapshot["read_only"] is True
    assert snapshot["write_api_available"] is False
    degraded = {fact["key"]: fact for fact in snapshot["facts"]}
    assert degraded["life.state_available"]["conflict_state"] == "SOURCE_UNAVAILABLE"
    assert degraded["runtime.health.alive_status"]["value"] == "unknown"


def test_runtime_world_model_has_no_sql_or_private_ledger_access() -> None:
    path = ROOT / "host_brain" / "runtime" / "world_model.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    forbidden = (
        "sqlite3",
        ".execute(",
        "SELECT ",
        "INSERT ",
        "UPDATE ",
        "DELETE ",
        "CREATE TABLE",
    )
    for token in forbidden:
        assert token not in source, f"runtime World Model still contains {token}"
    assert not any(
        isinstance(node, ast.Attribute) and node.attr in {"_conn", "_db_path"}
        for node in ast.walk(tree)
    )
    assert not any(
        isinstance(node, ast.Constant) and node.value in {"_conn", "_db_path"}
        for node in ast.walk(tree)
    )
    assert "WorldModelQueryRepository.from_ledger(ledger)" in source

    adapter_source = (
        ROOT / "host_brain" / "persistence" / "world_model_query_repository.py"
    ).read_text(encoding="utf-8")
    assert "?mode=ro" in adapter_source
    assert "def _snapshot(" in adapter_source
