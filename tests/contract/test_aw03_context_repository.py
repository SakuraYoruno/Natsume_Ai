from __future__ import annotations

import hashlib
from pathlib import Path
import pytest

from host_brain.persona.context.tactic_ledger import SessionTacticLedgerStore
from host_brain.persona.context.topic_ledger import SessionTopicLedgerStore
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.context_repository import (
    CONTEXT_MIGRATION_ASSET_KEY,
    CONTEXT_MIGRATION_SHA256,
    CONTEXT_SCHEMA_NOT_READY,
    ContextRepository,
    ContextRepositoryError,
)


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "host_brain" / "audit" / "migrations" / "0070_context_persistence.sql"
NOW = 1_900_000_000_000


def _migrated_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "context.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw03-context-repository",
        ts_ms=NOW,
    )
    assert result.success is True, result.as_dict()
    return db_path


class DurableLedgerReference:
    def __init__(self, db_path: Path) -> None:
        self.canonical_db_path = str(db_path)

    def __getattr__(self, name: str):
        if name in {"_conn", "_write_lock"}:
            raise AssertionError("durable application store reached through SQLiteLedger internals")
        raise AttributeError(name)


def test_aw03_context_migration_is_catalogued_and_checksum_frozen(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    assert hashlib.sha256(MIGRATION.read_bytes()).hexdigest() == CONTEXT_MIGRATION_SHA256

    repository = ContextRepository(db_path)
    repository.append_generated_turn(
        {
            "turn_id": "turn::contract",
            "session_id": "session-contract",
            "person_id": "person-contract",
            "request_id": "request-contract",
            "role": "user",
            "content": "hello",
            "ts_ms": NOW,
            "lang": "en",
        }
    )
    assert repository.find_turn(
        session_id="session-contract",
        request_id="request-contract",
        role="user",
    )["content"] == "hello"

    import sqlite3

    with sqlite3.connect(str(db_path)) as conn:
        asset = conn.execute(
            "SELECT checksum, disposition FROM schema_migration_assets WHERE asset_key = ?",
            (CONTEXT_MIGRATION_ASSET_KEY,),
        ).fetchone()
        assert asset == (CONTEXT_MIGRATION_SHA256, "APPLIED")
        conn.execute(
            "UPDATE schema_migration_assets SET checksum = ? WHERE asset_key = ?",
            ("0" * 64, CONTEXT_MIGRATION_ASSET_KEY),
        )
    with pytest.raises(ContextRepositoryError) as error:
        ContextRepository(db_path)
    assert error.value.reason_code == CONTEXT_SCHEMA_NOT_READY


def test_aw03_durable_context_repository_avoids_ledger_connection(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    ledger = DurableLedgerReference(db_path)
    store = ContextRepository.from_ledger(ledger)

    turn = {
        "turn_id": "turn::request-1::user",
        "session_id": "session-1",
        "person_id": "person-1",
        "request_id": "request-1",
        "role": "user",
        "content": "first",
        "ts_ms": NOW,
        "lang": "en",
    }
    first = store.append_generated_turn(turn)
    replay = store.append_generated_turn(turn)
    assert replay["turn_id"] == first["turn_id"]
    assert replay["content"] == "first"

    with pytest.raises(ContextRepositoryError) as conflict:
        store.append_generated_turn(
            {
                **turn,
                "content": "conflicting replay body",
                "ts_ms": NOW + 1,
            }
        )

    assert conflict.value.reason_code == "CONTEXT_RECORD_CONFLICT"
    canonical = store.find_turn(
        session_id="session-1",
        request_id="request-1",
        role="user",
    )
    assert canonical is not None
    assert canonical["content"] == "first"
    assert store.next_turn_index(session_id="session-1") == 1


def test_aw03_topic_ledger_uses_one_repository_writer(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    ledger = DurableLedgerReference(db_path)
    ledger_store = SessionTopicLedgerStore(ledger)  # type: ignore[arg-type]

    ledger_store.record_reply_plan(
        session_id="session-topic",
        person_id="person-1",
        request_id="request-ledger",
        turn_index=3,
        primary_topic="identity",
        answer_style="identify",
        metadata={"source": "reply_plan"},
    )

    repository = ContextRepository(db_path)
    rows = repository.fetch_topic_states(session_id="session-topic")
    assert len(rows) == 1
    assert len({row["topic_key"] for row in rows}) == len(rows)
    assert ledger_store.fetch_block(session_id="session-topic").entries


def test_aw03_tactic_store_uses_semantic_repository_uow(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    store = SessionTacticLedgerStore(DurableLedgerReference(db_path))  # type: ignore[arg-type]
    store.record_reply_plan(
        session_id="session-tactic",
        request_id="request-1",
        topic_key="identity",
        interaction_move="reconfirm",
        tactic_family="ack_name_then_wait",
        tactic_variant="probing",
        trajectory_stage="early",
        ts_ms=NOW,
    )

    block = store.fetch_block(session_id="session-tactic")
    assert block.recent_tactic_families("identity") == ("ack_name_then_wait",)
    assert store.delete_by_session(session_id="session-tactic") == 1


def test_aw03_context_compatibility_files_have_no_private_sqlite_access() -> None:
    files = (
        ROOT / "host_brain" / "persona" / "context" / "topic_ledger.py",
        ROOT / "host_brain" / "persona" / "context" / "tactic_ledger.py",
    )
    forbidden = ("._conn", "._write_lock", "._ensure_schema", "CREATE TABLE", "ALTER TABLE")
    for path in files:
        source = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, f"{path.name} still contains {token}"
    assert not (ROOT / "host_brain" / "conversation" / "store.py").exists()
