from __future__ import annotations

import ast
import json
from pathlib import Path
import sqlite3
import threading

from host_brain.persistence.life_body_query_repository import (
    LIFE_BODY_EVENT_TABLE_ALLOWLIST,
    LIFE_BODY_GENERIC_TABLE_ALLOWLIST,
    LifeBodyQueryRepository,
    LifeBodyQueryRepositoryError,
    decode_life_body_row,
)


ROOT = Path(__file__).resolve().parents[2]
NOW = 1_900_000_000_000


def _create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE memory_ops_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operation TEXT,
            source_memory_ids TEXT,
            evidence_refs TEXT,
            ts_ms INTEGER
        );
        CREATE TABLE scheduler_event_log(
            event_name TEXT
        );
        CREATE TABLE voice_event_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT,
            payload TEXT,
            reason_code TEXT,
            detail TEXT,
            stage TEXT,
            direction TEXT,
            ts_ms INTEGER
        );
        """
    )


def _durable_db(tmp_path: Path) -> Path:
    path = tmp_path / "life-body.sqlite3"
    with sqlite3.connect(path) as conn:
        _create_tables(conn)
        conn.executemany(
            """
            INSERT INTO memory_ops_log(
                operation, source_memory_ids, evidence_refs, ts_ms
            ) VALUES (?, ?, ?, ?)
            """,
            [
                ("old", "[]", "[]", NOW - 3_700_000),
                ("first", '["memory-1"]', '["evidence-1"]', NOW - 3),
                ("second", '["memory-2"]', '["evidence-2"]', NOW - 2),
                ("third", '["memory-3"]', '["evidence-3"]', NOW - 1),
            ],
        )
        conn.executemany(
            "INSERT INTO scheduler_event_log(event_name) VALUES (?)",
            [("first",), ("second",), ("third",)],
        )
        conn.executemany(
            """
            INSERT INTO voice_event_log(
                event_type, payload, reason_code, detail, stage, direction, ts_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "IMMUNE_CHAT_GATE",
                    json.dumps({"decision": "BLOCK", "risk_level": "high"}),
                    "IMMUNE_BLOCKED",
                    "blocked",
                    "gate",
                    "inbound",
                    NOW - 1,
                ),
                ("INPUT_TEXT", json.dumps({}), "", "no gate", "input", "inbound", NOW - 2),
                (
                    "INPUT_VOICE",
                    json.dumps(
                        {
                            "immune_gate": {
                                "decision": "ALLOW",
                                "risk_level": "low",
                            }
                        }
                    ),
                    "",
                    "allowed",
                    "gate",
                    "inbound",
                    NOW - 3,
                ),
                ("OTHER", "{}", "", "ignored", "", "", NOW - 4),
            ],
        )
        conn.commit()
    return path


def test_generic_window_preserves_time_and_rowid_sort_limit_and_decoder(tmp_path: Path) -> None:
    repository = LifeBodyQueryRepository(_durable_db(tmp_path))

    timed = repository.read_generic_window(
        "memory_ops_log",
        start_ms=NOW - 3_600_000,
        end_ms=NOW,
        limit=2,
    )
    decoded = [decode_life_body_row("memory_ops_log", row) for row in timed]
    assert [row["operation"] for row in decoded] == ["third", "second"]
    assert decoded[0]["source_memory_ids"] == ["memory-3"]
    assert decoded[0]["evidence_refs"] == ["evidence-3"]
    assert "id" not in decoded[0]

    rowid_fallback = repository.read_generic_window(
        "scheduler_event_log",
        start_ms=0,
        end_ms=NOW,
        limit=2,
    )
    assert [row["event_name"] for row in rowid_fallback] == ["third", "second"]


def test_event_type_window_preserves_scan_limit_and_payload_decoder(tmp_path: Path) -> None:
    repository = LifeBodyQueryRepository(_durable_db(tmp_path))
    rows = repository.read_event_type_window(
        "voice_event_log",
        start_ms=NOW - 3_600_000,
        end_ms=NOW,
        exact_event_type="IMMUNE_CHAT_GATE",
        like_event_type="INPUT_%",
        scan_limit=3,
    )
    decoded = [decode_life_body_row("voice_event_log", row) for row in rows]
    assert [row["event_type"] for row in decoded] == [
        "IMMUNE_CHAT_GATE",
        "INPUT_TEXT",
        "INPUT_VOICE",
    ]
    assert decoded[0]["payload"]["decision"] == "BLOCK"
    assert decoded[2]["payload"]["immune_gate"]["decision"] == "ALLOW"


def test_in_memory_connection_is_borrowed_only_inside_adapter() -> None:
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    connection.row_factory = sqlite3.Row
    _create_tables(connection)
    connection.execute(
        """
        INSERT INTO memory_ops_log(operation, source_memory_ids, evidence_refs, ts_ms)
        VALUES ('memory', '[]', '[]', ?)
        """,
        (NOW,),
    )
    connection.commit()

    class MemoryLedger:
        canonical_db_path = ":memory:"
        _conn = connection
        _write_lock = threading.RLock()

    try:
        rows = LifeBodyQueryRepository.from_ledger(MemoryLedger()).read_generic_window(
            "memory_ops_log",
            start_ms=NOW - 1,
            end_ms=NOW,
            limit=1,
        )
        assert rows[0]["operation"] == "memory"
    finally:
        connection.close()


def test_allowlist_and_route_static_boundary_are_locked() -> None:
    assert LIFE_BODY_GENERIC_TABLE_ALLOWLIST == {
        "memory_ops_log",
        "bond_update_log",
        "scheduler_event_log",
    }
    assert LIFE_BODY_EVENT_TABLE_ALLOWLIST == {"voice_event_log"}
    repository = LifeBodyQueryRepository.__new__(LifeBodyQueryRepository)
    try:
        repository.read_generic_window(
            "sqlite_master",
            start_ms=0,
            end_ms=1,
            limit=1,
        )
    except LifeBodyQueryRepositoryError:
        pass
    else:
        raise AssertionError("non-allowlisted table was accepted")

    path = ROOT / "host_brain" / "app" / "obs_routes_life_body.py"
    source = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(source, filename=str(path))
    forbidden = (
        "._conn",
        "._decode_row",
        "._table_has_column",
        ".execute(",
        "SELECT ",
        "INSERT ",
        "UPDATE ",
        "DELETE ",
        "PRAGMA ",
    )
    for token in forbidden:
        assert token not in source, f"Life Body route still contains {token}"
    assert not any(
        isinstance(node, ast.Attribute)
        and node.attr in {"_conn", "_decode_row", "_table_has_column"}
        for node in ast.walk(tree)
    )
