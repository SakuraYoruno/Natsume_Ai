from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import sqlite3
from pathlib import Path

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.memory.canon_world_store import compute_world_canon_hash
from host_brain.persistence.canon_repository import (
    CANON_COMMAND_CONFLICT,
    CANON_MIGRATION_ASSET_KEY,
    CANON_MIGRATION_SHA256,
    CANON_SCHEMA_NOT_READY,
    CanonRepository,
    CanonRepositoryError,
)


NOW = 1_800_000_000_000


def _migrated_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "canon.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw03-canon-migration",
        ts_ms=NOW,
    )
    assert result.success is True, result.as_dict()
    return db_path


def _proposal(*, title: str = "Canon proposal") -> dict[str, object]:
    payload = {
        "doc_id": "wc-test",
        "version": 1,
        "status": "active",
        "title": title,
        "tags": ["aw03"],
        "body": "A durable Canon proposal.",
        "updated_by": "proposal-1",
        "ts_ms": NOW,
    }
    return {
        "proposal_id": "proposal-1",
        "target_id": "wc-test",
        "base_version": 0,
        "proposed_version": 1,
        "proposed_hash": compute_world_canon_hash(payload),
        "proposed_title": title,
        "proposed_tags": ["aw03"],
        "proposed_body": "A durable Canon proposal.",
        "proposer_node_id": "canon.contract",
        "summary": "contract proposal",
        "status": "pending",
    }


def test_0067_is_checksum_bound_and_source_tables_are_immutable(tmp_path: Path) -> None:
    migration = Path("host_brain/audit/migrations/0067_canon_command_spine.sql")
    assert hashlib.sha256(migration.read_bytes()).hexdigest() == CANON_MIGRATION_SHA256
    db_path = _migrated_db(tmp_path)
    repository = CanonRepository(db_path, clock=lambda: NOW)
    repository.submit_proposal(
        _proposal(), actor_id="canon.contract", trace_id="trace-1", created_ts_ms=NOW
    )

    with sqlite3.connect(db_path) as conn:
        asset = conn.execute(
            "SELECT checksum, disposition FROM schema_migration_assets WHERE asset_key = ?",
            (CANON_MIGRATION_ASSET_KEY,),
        ).fetchone()
        assert asset == (CANON_MIGRATION_SHA256, "APPLIED")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                "UPDATE canon_command SET actor_id = 'tampered' WHERE command_id = ?",
                ("canon-proposal-submit::proposal-1",),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("DELETE FROM canon_journal")

        conn.execute(
            "UPDATE schema_migration_assets SET checksum = ? WHERE asset_key = ?",
            ("0" * 64, CANON_MIGRATION_ASSET_KEY),
        )
    with pytest.raises(CanonRepositoryError) as exc_info:
        CanonRepository(db_path)
    assert exc_info.value.reason_code == CANON_SCHEMA_NOT_READY


def test_command_uow_is_idempotent_cas_bound_and_rebuildable(tmp_path: Path) -> None:
    repository = CanonRepository(_migrated_db(tmp_path), clock=lambda: NOW)
    first = repository.submit_proposal(
        _proposal(), actor_id="canon.contract", trace_id="trace-1", created_ts_ms=NOW
    )
    replay = repository.submit_proposal(
        _proposal(), actor_id="canon.contract", trace_id="trace-1", created_ts_ms=NOW + 1
    )
    assert replay == first
    assert repository.journal_count() == 1

    changed = _proposal(title="Changed replay")
    with pytest.raises(CanonRepositoryError) as exc_info:
        repository.submit_proposal(
            changed,
            actor_id="canon.contract",
            trace_id="trace-1",
            created_ts_ms=NOW,
        )
    assert exc_info.value.reason_code == CANON_COMMAND_CONFLICT

    rejected = repository.reject_proposal(
        "proposal-1",
        reason="insufficient evidence",
        actor_id="canon.reviewer",
        trace_id="trace-2",
        created_ts_ms=NOW + 2,
    )
    assert rejected["status"] == "rejected"
    assert rejected["projection_version"] == 2
    before = rejected.copy()
    assert repository.rebuild_projections() == {
        "journal_events": 2,
        "proposals": 1,
        "documents": 0,
    }
    assert repository.get_proposal("proposal-1") == before


def test_concurrent_status_commands_have_one_cas_winner(tmp_path: Path) -> None:
    repository = CanonRepository(_migrated_db(tmp_path), clock=lambda: NOW)
    repository.submit_proposal(
        _proposal(), actor_id="canon.contract", trace_id="trace-1", created_ts_ms=NOW
    )

    def reject(index: int) -> str:
        try:
            repository.reject_proposal(
                "proposal-1",
                reason=f"reason-{index}",
                actor_id=f"reviewer-{index}",
                trace_id=f"trace-{index}",
                command_id=f"reject-{index}",
                created_ts_ms=NOW + index + 1,
            )
            return "APPLIED"
        except (CanonRepositoryError, PermissionError):
            return "CONFLICT"

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(reject, range(8)))
    assert outcomes.count("APPLIED") == 1
    assert repository.journal_count() == 2
    assert repository.get_proposal("proposal-1")["status"] == "rejected"
