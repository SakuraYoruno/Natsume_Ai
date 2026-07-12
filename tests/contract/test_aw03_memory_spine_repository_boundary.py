from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

from host_brain.audit.migration_runner import SQLiteMigrationRunner
import host_brain.conversation.memory_spine as memory_spine
from host_brain.conversation.memory_spine import (
    backfill_conversation_turn_memory_spine,
    count_conversation_turn_memory_spine_gaps,
    record_conversation_turn_memory_spine,
)
from host_brain.digital_life.contracts import LifeCommand
from host_brain.digital_life.service import LifeCommandService
from host_brain.persistence.context_repository import ContextRepository
from host_brain.persistence.life_command_repository import LifeCommandRepository


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
    content: str | None = None,
    turn_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    repository.append_turn(
        {
            "turn_id": turn_id or f"turn::{request_id}::{role}",
            "session_id": session_id,
            "person_id": person_id,
            "request_id": request_id,
            "role": role,
            "content": content or f"content::{request_id}",
            "ts_ms": ts_ms,
            "turn_index": ts_ms - NOW,
            "lang": "zh",
            "metadata": metadata or {},
        }
    )


def _append_legacy_life_projection(
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


def _append_canonical_receipt(
    db_path: Path,
    *,
    request_id: str,
    role: str,
    session_id: str = "session-main",
    turn_id: str = "",
    content: str = "",
    ts_ms: int = NOW,
    person_id: str = "person-1",
) -> str:
    command_id = memory_spine._conversation_turn_command_id(
        request_id=request_id,
        session_id=session_id,
        turn_id=turn_id or f"turn::{request_id}::{role}",
        role=role,
        content=content or f"content::{request_id}",
        ts_ms=ts_ms,
    )
    repository = LifeCommandRepository(db_path)
    snapshot = repository.get_snapshot(person_id)
    receipt = LifeCommandService(
        repository,
        clock=lambda: NOW + 100,
    ).submit(
        LifeCommand(
            command_id=command_id,
            person_id=person_id,
            command_type="conversation_turn_recorded",
            payload={"source": "memory_spine_test"},
            expected_version=snapshot.version,
            created_at_ms=NOW + 100,
        )
    )
    assert receipt.command_id == command_id
    return command_id


def test_repositories_preserve_turn_window_filters_and_canonical_receipts(
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
    command_id = _append_canonical_receipt(
        db_path,
        request_id="request-old",
        role="user",
        ts_ms=NOW + 10,
    )

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

    receipt = LifeCommandRepository(db_path).get_receipt(command_id)
    assert receipt is not None
    assert receipt.command_id == command_id


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
    _append_canonical_receipt(
        db_path,
        request_id="request-old",
        role="user",
        ts_ms=NOW + 10,
    )
    # A legacy experience row is deliberately present for request-new. It is
    # not the dedupe authority, so that turn must remain eligible.
    _append_legacy_life_projection(
        db_path,
        request_id="request-new",
        role="assistant",
    )
    ledger = DurableLedgerReference(db_path)
    monkeypatch.setattr("host_brain.app.dependencies.get_ledger", lambda: ledger)
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
        "host_brain.digital_life.experience_ingress.record_life_experience",
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
    assert captured["evidence"]["canonical_life_command_id"] == (
        "life::conversation_turn::request-voice::user"
    )
    assert captured["evidence"]["command_id"] == (
        "life::conversation_turn::request-voice::user"
    )
    assert captured["evidence"]["idempotency_key"] == (
        "life::conversation_turn::request-voice::user"
    )
    assert captured["evidence"]["canonical_idempotency_strategy"] == (
        "request_id_role"
    )
    assert captured["evidence"]["canonical_turn_identity"] == {
        "request_id": "request-voice",
        "session_id": "session-main",
        "turn_id": "turn::voice",
        "turn_index": 7,
        "role": "user",
        "content_sha256": hashlib.sha256(
            "不对，重新来".encode("utf-8")
        ).hexdigest(),
        "ts_ms": NOW,
    }


def test_record_turn_skips_on_canonical_receipt_not_legacy_event(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = _migrated_db(tmp_path)
    _append_canonical_receipt(
        db_path,
        request_id="request-duplicate",
        role="assistant",
        content="已经处理",
        turn_id="turn::duplicate",
        ts_ms=NOW + 7,
    )
    ledger = DurableLedgerReference(db_path)
    monkeypatch.setattr("host_brain.app.dependencies.get_ledger", lambda: ledger)

    result = record_conversation_turn_memory_spine(
        request_id="request-duplicate",
        session_id="session-main",
        person_id="person-1",
        role="assistant",
        content="已经处理",
        ts_ms=NOW + 7,
        lang="zh",
        turn_id="turn::duplicate",
    )

    assert result == {
        "status": "SKIPPED",
        "reason_code": "DUPLICATE_CONVERSATION_TURN",
    }


def test_requestless_turn_command_id_is_stable_and_content_sensitive() -> None:
    kwargs = {
        "request_id": "",
        "session_id": "session-derived",
        "turn_id": "turn-derived",
        "role": "user",
        "content": "同一条消息",
        "ts_ms": NOW + 9,
    }
    first = memory_spine._conversation_turn_command_id(**kwargs)
    second = memory_spine._conversation_turn_command_id(**kwargs)
    changed = memory_spine._conversation_turn_command_id(
        **{**kwargs, "content": "另一条消息"}
    )

    assert first == second
    assert first.startswith("life::conversation_turn::derived::")
    assert changed != first


def test_memory_spine_module_has_no_sql_or_ledger_private_access() -> None:
    source = (
        ROOT / "host_brain" / "conversation" / "memory_spine.py"
    ).read_text(encoding="utf-8")
    forbidden = (
        "ExperienceRepository",
        "has_conversation_turn_life_event",
        "existing_conversation_turn_life_keys",
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
