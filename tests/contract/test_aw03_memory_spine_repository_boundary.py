from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.conversation.memory_spine import (
    backfill_conversation_turn_memory_spine,
    count_conversation_turn_memory_spine_gaps,
    record_conversation_turn_memory_spine,
)
from host_brain.persistence.context_repository import ContextRepository
from host_brain.persistence.experience_repository import ExperienceRepository


ROOT = Path(__file__).resolve().parents[2]
NOW = 1_900_000_000_000


class DurableLedgerReference:
    def __init__(self, db_path: Path) -> None:
        self.canonical_db_path = str(db_path)

    def __getattr__(self, name: str) -> Any:
        if name in {"_conn", "_write_lock"}:
            raise AssertionError("memory spine reached through durable ledger internals")
        raise AttributeError(name)


def _migrated_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "memory-spine.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw03-memory-spine",
        ts_ms=NOW,
    )
    assert result.success is True, result.as_dict()
    return db_path


def _append_turn(
    repository: ContextRepository,
    *,
    request_id: str,
    session_id: str,
    person_id: str = "person-1",
    role: str = "user",
    ts_ms: int,
    metadata: dict[str, Any] | None = None,
) -> None:
    repository.append_turn(
        {
            "turn_id": f"turn::{request_id}::{role}",
            "session_id": session_id,
            "person_id": person_id,
            "request_id": request_id,
            "role": role,
            "content": f"content::{request_id}",
            "ts_ms": ts_ms,
            "turn_index": ts_ms - NOW,
            "lang": "zh",
            "metadata": metadata or {},
        }
    )


def _append_life_projection(
    db_path: Path,
    *,
    request_id: str,
    role: str,
) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO life_experience_event("
            "event_id, person_id, session_id, source_kind, event_kind, "
            "content_summary, evidence_json, salience, valence, uncertainty, "
            "created_at_ms, consumed_by_organs) "
            "VALUES(?, 'person-1', 'session-main', ?, 'turn', '', ?, 0, 0, 0, ?, 1)",
            (
                f"life::{request_id}::{role}",
                f"conversation_turn.{role}",
                json.dumps({"request_id": request_id, "role": role}),
                NOW,
            ),
        )


def test_repositories_preserve_turn_window_filters_order_and_life_dedup(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    turns = ContextRepository(db_path)
    _append_turn(
        turns,
        request_id="request-old",
        session_id="session-main",
        ts_ms=NOW + 10,
    )
    _append_turn(
        turns,
        request_id="request-new",
        session_id="session-main",
        role="assistant",
        ts_ms=NOW + 20,
    )
    _append_turn(
        turns,
        request_id="request-asr",
        session_id="asr_req_1",
        ts_ms=NOW + 30,
    )
    _append_turn(
        turns,
        request_id="request-codex",
        session_id="codex_1",
        ts_ms=NOW + 40,
    )
    _append_turn(
        turns,
        request_id="request-other-person",
        session_id="session-other",
        person_id="person-2",
        ts_ms=NOW + 50,
    )
    _append_life_projection(db_path, request_id="request-old", role="user")

    default_window = turns.list_memory_spine_turns(
        person_id="person-1",
        session_id="",
        limit=500,
        include_asr_sessions=False,
    )
    assert [row["request_id"] for row in default_window] == [
        "request-old",
        "request-new",
    ]
    inclusive_window = turns.list_memory_spine_turns(
        person_id="person-1",
        session_id="",
        limit=500,
        include_asr_sessions=True,
    )
    assert [row["request_id"] for row in inclusive_window] == [
        "request-old",
        "request-new",
        "request-asr",
        "request-codex",
    ]
    assert [row["request_id"] for row in turns.list_memory_spine_turns(
        person_id="person-1",
        session_id="asr_req_1",
        limit=500,
        include_asr_sessions=False,
    )] == ["request-asr"]

    experience = ExperienceRepository(db_path)
    assert experience.existing_conversation_turn_life_keys(
        (("request-old", "user"), ("request-new", "assistant"))
    ) == frozenset({("request-old", "user")})
    assert experience.count_conversation_turn_memory_spine_gaps(
        person_id="person-1",
        session_id="",
        limit=2_000,
        include_asr_sessions=False,
    ) == 1
    assert experience.count_conversation_turn_memory_spine_gaps(
        person_id="person-1",
        session_id="",
        limit=2_000,
        include_asr_sessions=True,
    ) == 3


def test_memory_spine_orchestrates_durable_repositories_without_private_access(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = _migrated_db(tmp_path)
    turns = ContextRepository(db_path)
    _append_turn(
        turns,
        request_id="request-old",
        session_id="session-main",
        ts_ms=NOW + 10,
    )
    _append_turn(
        turns,
        request_id="request-new",
        session_id="session-main",
        role="assistant",
        ts_ms=NOW + 20,
    )
    _append_life_projection(db_path, request_id="request-old", role="user")
    ledger = DurableLedgerReference(db_path)
    monkeypatch.setattr("host_brain.app.dependencies.get_ledger", lambda: ledger)
    monkeypatch.setattr(
        "host_brain.runtime.life_continuity.ensure_life_schema",
        lambda _ledger: None,
    )

    result = backfill_conversation_turn_memory_spine(
        person_id="person-1",
        dry_run=True,
    )
    assert result["status"] == "OK"
    assert result["scanned"] == 2
    assert result["eligible"] == 1
    assert result["skipped_existing"] == 1
    assert [sample["request_id"] for sample in result["samples"]] == ["request-new"]
    assert count_conversation_turn_memory_spine_gaps(person_id="person-1") == 1


def test_record_turn_keeps_asr_filtering_and_sentiment_weights(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = _migrated_db(tmp_path)
    ledger = DurableLedgerReference(db_path)
    captured: dict[str, Any] = {}

    def fake_record_life_experience(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"event_id": "life::new"}

    monkeypatch.setattr("host_brain.app.dependencies.get_ledger", lambda: ledger)
    monkeypatch.setattr(
        "host_brain.runtime.life_continuity.ensure_life_schema",
        lambda _ledger: None,
    )
    monkeypatch.setattr(
        "host_brain.runtime.life_continuity.record_life_experience",
        fake_record_life_experience,
    )
    result = record_conversation_turn_memory_spine(
        request_id="request-voice",
        session_id="session-main",
        person_id="person-1",
        role="user",
        content="不对，重新来",
        ts_ms=NOW,
        lang="zh",
        metadata={"input_source": "asr"},
        turn_id="turn::voice",
        turn_index=7,
    )

    assert result == {
        "status": "OK",
        "source_kind": "conversation_turn.user",
        "event_kind": "user_message",
        "event_id": "life::new",
    }
    assert captured["salience"] == 0.28
    assert captured["valence"] == -0.35
    assert captured["uncertainty"] == 0.35
    assert captured["evidence"]["request_id"] == "request-voice"
    assert captured["evidence"]["input_source"] == "asr"


def test_memory_spine_module_has_no_sql_or_ledger_private_access() -> None:
    source = (
        ROOT / "host_brain" / "conversation" / "memory_spine.py"
    ).read_text(encoding="utf-8")
    forbidden = (
        "._conn",
        "._write_lock",
        ".execute(",
        "SELECT ",
        "INSERT INTO",
        "UPDATE ",
        "DELETE FROM",
        "PRAGMA",
        "import sqlite3",
    )
    for token in forbidden:
        assert token not in source, f"memory_spine.py still contains {token}"
