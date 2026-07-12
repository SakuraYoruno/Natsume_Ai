from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from host_brain.app.context.conversation_experience_store import (
    ConversationExperienceStore,
)
from host_brain.app.context.memory_context_orchestrator import run_memory_sanitize
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.experience_repository import (
    CONTEXT_MIGRATION_ASSET_KEY,
    CONTEXT_MIGRATION_SHA256,
    EXPERIENCE_REPLAY_CONFLICT,
    EXPERIENCE_SCHEMA_NOT_READY,
    ExperienceRepository,
    ExperienceRepositoryError,
)


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "host_brain" / "audit" / "migrations" / "0070_context_persistence.sql"
NOW = 1_900_000_000_000


def _migrated_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "experience.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw03-experience-repository",
        ts_ms=NOW,
    )
    assert result.success is True, result.as_dict()
    return db_path


class DurableLedgerReference:
    def __init__(self, db_path: Path) -> None:
        self.canonical_db_path = str(db_path)

    def __getattr__(self, name: str):
        if name in {"_conn", "_write_lock"}:
            raise AssertionError("durable store reached through ledger internals")
        raise AttributeError(name)


def _understanding(*, lesson: str = "repair") -> dict:
    return {
        "selected_intent": "conversation_repair",
        "domain": "conversation",
        "belief_state": {
            "selected_intent": "conversation_repair",
            "repair_requested": True,
            "confidence": 0.8,
        },
        "resolved_references": [
            {"target_label": lesson, "target_kind": "conversation", "confidence": 0.9}
        ],
    }


def test_0070_catalog_checksum_and_experience_schema_are_fail_closed(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    assert hashlib.sha256(MIGRATION.read_bytes()).hexdigest() == CONTEXT_MIGRATION_SHA256
    ExperienceRepository(db_path)

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT checksum, disposition FROM schema_migration_assets WHERE asset_key=?",
            (CONTEXT_MIGRATION_ASSET_KEY,),
        ).fetchone() == (CONTEXT_MIGRATION_SHA256, "APPLIED")
        conn.execute(
            "UPDATE schema_migration_assets SET checksum=? WHERE asset_key=?",
            ("0" * 64, CONTEXT_MIGRATION_ASSET_KEY),
        )
    with pytest.raises(ExperienceRepositoryError) as error:
        ExperienceRepository(db_path)
    assert error.value.reason_code == EXPERIENCE_SCHEMA_NOT_READY


def test_inference_correction_and_lessons_share_exactly_once_uow(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    store = ConversationExperienceStore(
        ledger=DurableLedgerReference(db_path)  # type: ignore[arg-type]
    )
    first = store.record_inference(
        request_id="request-1",
        session_id="session-1",
        person_id="person-1",
        user_text="不是这个",
        understanding=_understanding(),
    )
    replay = store.record_inference(
        request_id="request-1",
        session_id="session-1",
        person_id="person-1",
        user_text="不是这个",
        understanding=_understanding(),
    )
    assert replay["event_id"] == first["event_id"]
    assert replay["replayed"] is True
    assert replay["correction"]["replayed"] is True

    reply = store.update_assistant_reply(
        request_id="request-1", assistant_reply="canonical reply"
    )
    reply_replay = store.update_assistant_reply(
        request_id="request-1", assistant_reply="canonical reply"
    )
    assert reply["replayed"] is False
    assert reply_replay["replayed"] is True
    assert store.record_inference(
        request_id="request-1",
        session_id="session-1",
        person_id="person-1",
        user_text="不是这个",
        understanding=_understanding(),
    )["replayed"] is True
    with pytest.raises(ExperienceRepositoryError) as reply_conflict:
        store.update_assistant_reply(
            request_id="request-1", assistant_reply="conflicting reply"
        )
    assert reply_conflict.value.reason_code == EXPERIENCE_REPLAY_CONFLICT

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM conversation_experience_event"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM conversation_correction_event"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM conversation_experience_lesson"
        ).fetchone()[0] == 2
        assert conn.execute(
            "SELECT version FROM conversation_belief_state"
        ).fetchone()[0] == 1

    with pytest.raises(ExperienceRepositoryError) as conflict:
        store.record_inference(
            request_id="request-1",
            session_id="session-1",
            person_id="person-1",
            user_text="changed replay",
            understanding=_understanding(),
        )
    assert conflict.value.reason_code == EXPERIENCE_REPLAY_CONFLICT


def test_outcome_exact_replay_does_not_increment_lesson_twice(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    store = ConversationExperienceStore(
        ledger=DurableLedgerReference(db_path)  # type: ignore[arg-type]
    )
    store.record_inference(
        request_id="request-feedback",
        session_id="session-feedback",
        person_id="person-1",
        user_text="不是这个",
        understanding=_understanding(),
    )
    first = store.record_outcome_feedback(
        session_id="session-feedback", person_id="person-1", raw_text="对了"
    )
    replay = store.record_outcome_feedback(
        session_id="session-feedback", person_id="person-1", raw_text="对了"
    )
    assert first["replayed"] is False
    assert replay["replayed"] is True

    with sqlite3.connect(db_path) as conn:
        counts = conn.execute(
            "SELECT success_count, failure_count FROM conversation_experience_lesson"
        ).fetchall()
    assert counts
    assert set(counts) == {(1, 1)}

    with pytest.raises(ExperienceRepositoryError) as conflict:
        store.record_outcome_feedback(
            session_id="session-feedback",
            person_id="person-1",
            raw_text="还是不对",
        )
    assert conflict.value.reason_code == EXPERIENCE_REPLAY_CONFLICT


def test_sanitize_changes_and_returned_summary_commit_atomically(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO conversation_belief_state(session_id, person_id, version, "
            "belief_json, updated_at_ms) VALUES(?, ?, 1, ?, ?)",
            (
                "codex_atomic",
                "person-1",
                json.dumps({"selected_intent": "open_chat", "confidence": 0.1}),
                NOW,
            ),
        )
        conn.execute(
            "CREATE TRIGGER reject_sanitize_audit BEFORE INSERT ON memory_sanitize_audit "
            "BEGIN SELECT RAISE(ABORT, 'audit rejected'); END"
        )

    ledger = DurableLedgerReference(db_path)
    with pytest.raises(sqlite3.IntegrityError, match="audit rejected"):
        run_memory_sanitize(ledger=ledger, person_id="person-1")  # type: ignore[arg-type]
    with sqlite3.connect(db_path) as conn:
        belief = json.loads(
            conn.execute(
                "SELECT belief_json FROM conversation_belief_state"
            ).fetchone()[0]
        )
        assert "suppressed_at_ms" not in belief
        assert conn.execute(
            "SELECT COUNT(*) FROM memory_sanitize_audit"
        ).fetchone()[0] == 0
        conn.execute("DROP TRIGGER reject_sanitize_audit")

    summary = run_memory_sanitize(  # type: ignore[arg-type]
        ledger=ledger, person_id="person-1"
    )
    with sqlite3.connect(db_path) as conn:
        audit_summary = json.loads(
            conn.execute(
                "SELECT summary_json FROM memory_sanitize_audit ORDER BY id DESC LIMIT 1"
            ).fetchone()[0]
        )
        belief = json.loads(
            conn.execute(
                "SELECT belief_json FROM conversation_belief_state"
            ).fetchone()[0]
        )
    assert audit_summary == summary
    assert summary["belief_deleted"] == 1
    assert belief["can_drive_reply"] is False


def test_experience_application_files_have_no_sql_or_private_connection_access() -> None:
    files = (
        ROOT / "host_brain" / "app" / "context" / "conversation_experience_store.py",
        ROOT / "host_brain" / "app" / "context" / "memory_context_orchestrator.py",
    )
    forbidden = (
        "._conn",
        "._write_lock",
        "CREATE TABLE",
        ".execute(",
        ".commit(",
        "SELECT ",
        "INSERT INTO",
        "UPDATE ",
        "DELETE FROM",
        "import sqlite3",
    )
    for path in files:
        source = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, f"{path.name} still contains {token}"
