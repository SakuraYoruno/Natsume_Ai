from __future__ import annotations

from pathlib import Path
import shutil
import sqlite3
from types import SimpleNamespace

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.persistence.runtime_read_repository import (
    APPROVED_LEDGER_TIME_TABLES,
    RUNTIME_READ_TABLE_NOT_ALLOWED,
    RuntimeReadRepository,
    RuntimeReadRepositoryError,
)


@pytest.fixture(scope="module")
def runtime_read_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    db_path = tmp_path_factory.mktemp("runtime-read") / "template.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw03-runtime-read-contract",
        ts_ms=1_800_000_000_000,
    )
    assert result.success is True, result.as_dict()
    return db_path


@pytest.fixture
def runtime_read_db(runtime_read_template: Path, tmp_path: Path) -> Path:
    db_path = tmp_path / "runtime-read.sqlite3"
    shutil.copyfile(runtime_read_template, db_path)
    with sqlite3.connect(db_path) as conn:
        turns = [
            ("turn-1", "session-1", "person-1", "request-old", "user", "项目事实：alpha", 100, 1, "{}"),
            ("turn-2", "session-1", "person-1", "request-old", "assistant", "收到", 110, 2, '{"source_kind":"life_proactive"}'),
            ("turn-3", "session-1", "person-1", "request-current", "user", "记住 beta", 200, 3, "{}"),
            ("turn-4", "session-1", "person-1", "request-future", "assistant", "未来回复", 210, 4, "{}"),
            ("turn-5", "session-1", "person-1", "request-later", "user", "测试事实：future", 300, 5, "{}"),
        ]
        conn.executemany(
            """
            INSERT INTO conversation_turn(
                turn_id, session_id, person_id, request_id, role, content,
                ts_ms, turn_index, lang, corrupted, metadata, created_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'zh', 0, ?, ?)
            """,
            [(*row, row[6]) for row in turns],
        )
        conn.executemany(
            """
            INSERT INTO memory_ops_log(
                request_id, op_kind, memory_id, evidence_refs, status, detail, ts_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("memory-old", "WRITE", "memory-old", '["tag:fact"]', "SUCCESS", "memory written: old", 100),
                ("dream", "WRITE", "dream-1", '["tag:dream","source:dream_generator"]', "SUCCESS", "memory written: 夜车和海", 150),
                ("dream-hint", "WRITE", "dream-hint", '["tag:dream_usage_hint","source:dream_generator"]', "SUCCESS", "usage hint", 160),
                ("memory-new", "WRITE", "memory-new", '["tag:fact"]', "SUCCESS", "memory written: recent topic", 500),
                ("memory-failed", "WRITE", "memory-failed", '["tag:fact"]', "FAILED", "failed topic", 600),
            ],
        )
        conn.executemany(
            """
            INSERT INTO intent_log(request_id, ts_ms, raw_text, normalized_json)
            VALUES (?, ?, ?, ?)
            """,
            [
                ("intent-1", 100, "raw alpha", '{"topic":"alpha"}'),
                ("intent-2", 200, "raw beta", '{"primary_topic":"beta"}'),
                ("intent-3", 300, "raw gamma", "{}"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO channel_io_log(request_id, direction, channel, detail, ts_ms)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                ("channel-1", "IN", "wechat", "old channel", 100),
                ("channel-2", "OUT", "wechat", "new channel", 200),
            ],
        )
        conn.commit()
    return db_path


def test_conversation_queries_honor_current_request_boundaries(
    runtime_read_db: Path,
) -> None:
    repository = RuntimeReadRepository(runtime_read_db)
    contents = repository.recent_user_turn_contents(
        person_id="person-1",
        session_id="session-1",
        request_id="request-current",
        limit=20,
    )
    assert contents == ["记住 beta", "项目事实：alpha"]

    turns = repository.recent_conversation_turns_before_request(
        session_id="session-1",
        request_id="request-current",
        limit=5,
    )
    assert [row["content"] for row in turns] == ["收到", "项目事实：alpha"]


def test_dream_memory_topics_intents_and_last_interaction_are_semantic_reads(
    runtime_read_db: Path,
) -> None:
    repository = RuntimeReadRepository(runtime_read_db)
    dream = repository.recent_dream_memory()
    assert dream is not None
    assert dream["memory_id"] == "dream-1"
    assert dream["detail"] == "memory written: 夜车和海"

    assert repository.recent_memory_write_topics(since_ts=400, limit=5) == [
        "memory written: recent topic"
    ]
    assert repository.recent_intent_topics(limit=3) == ["raw gamma", "beta", "alpha"]
    assert repository.last_interaction_ts(upper_bound_ts=250) == 200


def test_time_window_reader_has_an_explicit_allowlist_and_decodes_central_json(
    runtime_read_db: Path,
) -> None:
    repository = RuntimeReadRepository(runtime_read_db)
    assert APPROVED_LEDGER_TIME_TABLES == {
        "channel_io_log",
        "intent_log",
        "memory_ops_log",
    }
    rows = repository.read_approved_ledger_rows_by_time(
        table="memory_ops_log",
        start_ts=100,
        end_ts=160,
        limit=10,
    )
    assert [row["memory_id"] for row in rows] == ["dream-hint", "dream-1", "memory-old"]
    assert rows[1]["evidence_refs"] == ["tag:dream", "source:dream_generator"]

    with pytest.raises(RuntimeReadRepositoryError) as raised:
        repository.read_approved_ledger_rows_by_time(
            table="intent_log; DROP TABLE conversation_turn",
            start_ts=0,
            end_ts=1_000,
            limit=10,
        )
    assert raised.value.reason_code == RUNTIME_READ_TABLE_NOT_ALLOWED


def test_file_connections_are_query_only(runtime_read_db: Path) -> None:
    repository = RuntimeReadRepository(runtime_read_db)
    with repository._read_operation() as conn:  # noqa: SLF001 - contract proof
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM intent_log")
    assert repository.last_interaction_ts(upper_bound_ts=1_000) == 300


def test_in_memory_ledger_is_read_through_the_repository_boundary() -> None:
    ledger = SQLiteLedger(db_path=":memory:", check_same_thread=False)
    try:
        ledger.log_intent(
            {
                "request_id": "memory-intent",
                "ts_ms": 123,
                "raw_text": "in-memory topic",
                "normalized_json": {"topic": "memory-topic"},
            }
        )
        repository = RuntimeReadRepository.from_ledger(ledger)
        assert repository.recent_intent_topics(limit=2) == ["memory-topic"]
        assert repository.last_interaction_ts(upper_bound_ts=123) == 123
    finally:
        ledger.close()


def test_public_ledger_reader_fallback_preserves_injected_test_api() -> None:
    rows_by_table = {
        "intent_log": [
            {"ts_ms": 10, "raw_text": "alpha", "normalized_json": {"topic": "a"}},
            {"ts_ms": 20, "raw_text": "beta", "normalized_json": {"topic": "b"}},
        ],
        "channel_io_log": [
            {"ts_ms": 15, "detail": "channel"},
        ],
    }

    def fetch(table, *, start_ms, end_ms, limit):  # noqa: ANN001
        return [
            row
            for row in rows_by_table.get(table, [])
            if int(start_ms) <= int(row["ts_ms"]) <= int(end_ms)
        ][:limit]

    ledger = SimpleNamespace(
        fetch_by_time_range=fetch,
        conversation_turn=[
            {
                "session_id": "s",
                "person_id": "p",
                "request_id": "old",
                "role": "user",
                "content": "old fact",
                "turn_index": 1,
                "ts_ms": 10,
            },
            {
                "session_id": "s",
                "person_id": "p",
                "request_id": "current",
                "role": "user",
                "content": "current fact",
                "turn_index": 2,
                "ts_ms": 20,
            },
        ],
    )
    repository = RuntimeReadRepository.from_ledger(ledger)
    assert repository.recent_intent_topics(limit=2) == ["b", "a"]
    assert repository.read_approved_ledger_rows_by_time(
        table="channel_io_log", start_ts=0, end_ts=20, limit=5
    ) == [{"ts_ms": 15, "detail": "channel"}]
    assert repository.recent_user_turn_contents(
        person_id="p", session_id="s", request_id="current", limit=5
    ) == ["current fact", "old fact"]
