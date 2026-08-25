from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.bond.neo4j_client import BOND_WRITER_ID, SqliteBondClient
from host_brain.persistence.bond_fallback_repository import (
    BOND_FALLBACK_MIGRATION_ASSET_KEY,
    BOND_FALLBACK_MIGRATION_SHA256,
    BOND_FALLBACK_SCHEMA_NOT_READY,
    BondFallbackRepository,
    BondFallbackRepositoryError,
)


ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "host_brain" / "audit" / "migrations"
ASSET = MIGRATIONS / "0076_bond_fallback_persistence.sql"


@pytest.fixture(scope="module")
def migrated_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    db_path = tmp_path_factory.mktemp("bond-fallback") / "template.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw03-bond-fallback-contract",
        ts_ms=1_800_000_000_000,
    )
    assert result.success is True, result.as_dict()
    return db_path


@pytest.fixture
def migrated_db(migrated_template: Path, tmp_path: Path) -> Path:
    db_path = tmp_path / "bond-fallback.sqlite3"
    shutil.copyfile(migrated_template, db_path)
    return db_path


def _event(*, payload_version: int = 1, ts_ms: int = 100) -> dict[str, object]:
    return {
        "event_id": "bond-event-1",
        "user_id": "user-1",
        "session_id": "session-1",
        "topic_id": "topic-1",
        "capability_hint": "support",
        "evidence_refs": ["evidence://bond/1"],
        "payload": {"version": payload_version},
        "ts_ms": ts_ms,
    }


def test_0076_asset_is_checksum_bound_guarded_and_explicitly_transactional(
    migrated_template: Path,
) -> None:
    raw = ASSET.read_bytes()
    sql = raw.decode("utf-8")
    assert hashlib.sha256(raw).hexdigest() == BOND_FALLBACK_MIGRATION_SHA256
    assert BOND_FALLBACK_MIGRATION_ASSET_KEY == "0076#bond_fallback_persistence"
    assert "BEGIN IMMEDIATE;" in sql
    assert sql.rstrip().endswith("COMMIT;")

    with sqlite3.connect(migrated_template) as conn:
        row = conn.execute(
            """
            SELECT checksum, disposition FROM schema_migration_assets
            WHERE asset_key = ?
            """,
            (BOND_FALLBACK_MIGRATION_ASSET_KEY,),
        ).fetchone()
    assert row == (BOND_FALLBACK_MIGRATION_SHA256, "APPLIED")


def test_exact_replay_is_stable_and_does_not_revert_a_newer_head() -> None:
    repository = BondFallbackRepository(":memory:", clock=lambda: 900)
    try:
        first = repository.write_event(_event())
        assert repository.write_event(_event()) == first
        assert len(repository.journal_entries()) == 1

        updated = repository.write_event(_event(payload_version=2, ts_ms=101))
        assert updated["payload"] == {"version": 2}
        assert len(repository.journal_entries()) == 2

        assert repository.write_event(_event()) == first
        assert repository.get_event("bond-event-1") == updated
        assert len(repository.journal_entries()) == 2
    finally:
        repository.close()


@pytest.mark.parametrize(
    "stage",
    ["write_event:after_projection", "write_event:after_journal", "write_event:before_commit"],
)
def test_projection_and_journal_rollback_as_one_unit(stage: str) -> None:
    def fail(current_stage: str) -> None:
        if current_stage == stage:
            raise RuntimeError(f"injected failure at {stage}")

    repository = BondFallbackRepository(":memory:", fault_injector=fail)
    try:
        with pytest.raises(RuntimeError, match="injected failure"):
            repository.write_event(_event())
        assert repository.get_event("bond-event-1") is None
        assert repository.journal_entries() == []
    finally:
        repository.close()


def test_durable_client_restart_update_and_immutable_journal(migrated_db: Path) -> None:
    client = SqliteBondClient(db_path=migrated_db)
    try:
        first = client.write_bond_event(
            writer_id=BOND_WRITER_ID,
            **_event(),
        )
        replayed = client.write_bond_event(
            writer_id=BOND_WRITER_ID,
            **_event(),
        )
        assert replayed == first
        updated = client.write_bond_event(
            writer_id=BOND_WRITER_ID,
            **_event(payload_version=2, ts_ms=101),
        )
        assert updated["payload"] == {"version": 2}
    finally:
        client.close()

    restarted = SqliteBondClient(db_path=migrated_db)
    try:
        assert restarted.get_event("bond-event-1") == updated
        assert restarted.trace_user_events("user-1") == [updated]
        assert restarted.events == [updated]
    finally:
        restarted.close()

    with sqlite3.connect(migrated_db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM bond_fallback_event_journal"
        ).fetchone()[0] == 2
        with pytest.raises(sqlite3.IntegrityError, match="IMMUTABLE"):
            conn.execute(
                "UPDATE bond_fallback_event_journal SET change_kind = 'INSERT'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="IMMUTABLE"):
            conn.execute("DELETE FROM bond_fallback_event_journal")


def test_0076_adopts_legacy_inline_bond_table_without_losing_events(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "legacy-bond.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE bond_events (
                event_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                topic_id TEXT NOT NULL,
                capability_hint TEXT NOT NULL,
                evidence_refs TEXT NOT NULL,
                payload TEXT NOT NULL,
                ts_ms INTEGER NOT NULL
            );
            CREATE INDEX idx_bond_user ON bond_events(user_id);
            CREATE INDEX idx_bond_ts ON bond_events(ts_ms);
            """
        )
        conn.execute(
            """
            INSERT INTO bond_events(
                event_id, user_id, session_id, topic_id, capability_hint,
                evidence_refs, payload, ts_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "bond-event-1",
                "user-1",
                "session-1",
                "topic-1",
                "support",
                '["evidence://bond/1"]',
                '{"version":1}',
                100,
            ),
        )
        conn.commit()

    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw03-bond-legacy-adoption",
        ts_ms=1_800_000_000_000,
    )
    assert migrated.success is True, migrated.as_dict()

    repository = BondFallbackRepository(db_path, clock=lambda: 901)
    try:
        assert repository.get_event("bond-event-1") == _event()
        assert repository.write_event(_event()) == _event()
        entries = repository.journal_entries(event_id="bond-event-1")
        assert len(entries) == 1
        assert entries[0]["change_kind"] == "UPDATE"
    finally:
        repository.close()


def test_durable_repository_fails_closed_without_catalog_or_on_checksum_drift(
    migrated_db: Path,
    tmp_path: Path,
) -> None:
    uncatalogued = tmp_path / "uncatalogued.sqlite3"
    with sqlite3.connect(uncatalogued) as conn:
        conn.executescript(ASSET.read_text(encoding="utf-8"))

    with pytest.raises(BondFallbackRepositoryError) as missing:
        BondFallbackRepository(uncatalogued)
    assert missing.value.reason_code == BOND_FALLBACK_SCHEMA_NOT_READY

    with sqlite3.connect(migrated_db) as conn:
        conn.execute(
            "UPDATE schema_migration_assets SET checksum = ? WHERE asset_key = ?",
            ("0" * 64, BOND_FALLBACK_MIGRATION_ASSET_KEY),
        )
        conn.commit()
    with pytest.raises(BondFallbackRepositoryError) as drifted:
        BondFallbackRepository(migrated_db)
    assert drifted.value.reason_code == BOND_FALLBACK_SCHEMA_NOT_READY
