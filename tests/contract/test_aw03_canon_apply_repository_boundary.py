from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.canon_apply_repository import (
    CANON_APPLY_MIGRATION_SHA256,
    CanonApplyRepository,
    CanonApplySqliteAuthority,
)
from host_brain.persistence.canon_approval_repository import (
    CanonApprovalTokenRepository,
)


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "host_brain" / "audit" / "migrations" / "0053_canon_apply.sql"
NOW = 1_900_000_000_000


def _migrated_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "canon-apply.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw03-canon-apply",
        ts_ms=NOW,
    )
    assert result.success is True, result.as_dict()
    return db_path.resolve()


def test_0053_readiness_and_checksum_are_repository_owned(tmp_path: Path) -> None:
    assert hashlib.sha256(MIGRATION.read_bytes()).hexdigest() == CANON_APPLY_MIGRATION_SHA256
    db_path = _migrated_db(tmp_path)
    CanonApplyRepository(db_path).assert_ready()

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE schema_migrations SET checksum=? WHERE migration_id=53",
            ("0" * 64,),
        )
    with pytest.raises(RuntimeError, match="central_migration_mismatch"):
        CanonApplyRepository(db_path).assert_ready()


def test_subject_cas_rolls_back_with_repository_transaction(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "INSERT INTO canon_proposal_log(proposal_id, canon_kind, target_id, "
            "base_version, proposed_version, proposed_hash, source_intent_id, "
            "proposer_node_id, summary, status, ts_ms, proposed_title, "
            "proposed_tags, proposed_body) VALUES(?, 'world', ?, 0, 1, ?, '', "
            "'owner', '', 'pending', ?, 'title', '[]', 'body')",
            ("proposal-rollback", "doc-rollback", "a" * 64, NOW),
        )
    repository = CanonApplyRepository(db_path)
    with pytest.raises(RuntimeError, match="injected rollback"):
        with repository.transaction() as uow:
            assert uow.cas_subject_status(
                table="canon_proposal_log",
                subject_id="proposal-rollback",
                expected_status="pending",
                new_status="APPLY_PREPARED",
            )
            raise RuntimeError("injected rollback")
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT status FROM canon_proposal_log WHERE proposal_id=?",
            ("proposal-rollback",),
        ).fetchone() == ("pending",)


def test_apply_and_approval_repositories_share_one_sqlite_authority(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    authority = CanonApplySqliteAuthority(db_path)
    apply_repository = CanonApplyRepository(db_path, authority=authority)
    approval_repository = CanonApprovalTokenRepository(
        db_path, authority=authority
    )
    assert apply_repository.authority is authority
    assert approval_repository._authority is authority  # noqa: SLF001
    apply_repository.assert_ready()
    approval_repository.assert_ready()


def test_canon_apply_service_has_no_sql_and_composition_injects_repository() -> None:
    service_source = (
        ROOT / "host_brain" / "memory" / "canon_apply_service.py"
    ).read_text(encoding="utf-8")
    forbidden = (
        "import sqlite3",
        "def _connect",
        ".execute(",
        "SELECT ",
        "INSERT INTO",
        "UPDATE ",
        "DELETE FROM",
        "PRAGMA ",
        "schema_introspection",
    )
    for token in forbidden:
        assert token not in service_source, f"canon_apply_service.py still contains {token}"

    composition_source = (
        ROOT / "host_brain" / "composition" / "canon.py"
    ).read_text(encoding="utf-8")
    application_source = (
        ROOT / "host_brain" / "canon" / "application.py"
    ).read_text(encoding="utf-8")
    assert "apply_repository = CanonApplyRepository(canonical)" in composition_source
    assert "repository=apply_repository" in composition_source
    assert "class CanonApplyPort(Protocol)" in application_source
