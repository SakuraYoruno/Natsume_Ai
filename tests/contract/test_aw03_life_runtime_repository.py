from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import sqlite3
from pathlib import Path

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.life_runtime_repository import (
    LIFE_RUNTIME_MIGRATION_ASSET_KEY,
    LIFE_RUNTIME_MIGRATION_SHA256,
    LIFE_RUNTIME_SCHEMA_NOT_READY,
    LifeRuntimeRepository,
    LifeRuntimeRepositoryError,
)


NOW = 1_800_000_000_000


def _migrated_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "life-runtime.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw03-life-runtime",
        ts_ms=NOW,
    )
    assert result.success is True, result.as_dict()
    return db_path


def _state(version: int, *, mood: str = "calm") -> dict:
    return {"person_id": "person-1", "version": version, "affect": {"mood": mood}}


def _save(repository: LifeRuntimeRepository, state: dict, expected: int) -> dict:
    return repository.save_state(
        state,
        expected_version=expected,
        command_ref=f"command-{state['affect']['mood']}",
        source_kind="contract",
        allow_single_writer_fallback=False,
        now_ms=NOW,
        conflict_reason="LIFE_STATE_OCC_CONFLICT",
        fallback_reason="LIFE_STATE_SINGLE_WRITER_FALLBACK",
    )


def test_0071_is_checksum_bound_and_uow_is_immutable(tmp_path: Path) -> None:
    migration = Path("host_brain/audit/migrations/0071_life_runtime_uow.sql")
    assert hashlib.sha256(migration.read_bytes()).hexdigest() == LIFE_RUNTIME_MIGRATION_SHA256
    db_path = _migrated_db(tmp_path)
    repository = LifeRuntimeRepository(db_path)
    assert _save(repository, _state(1), 0)["status"] == "APPLIED"

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT checksum, disposition FROM schema_migration_assets WHERE asset_key=?",
            (LIFE_RUNTIME_MIGRATION_ASSET_KEY,),
        ).fetchone() == (LIFE_RUNTIME_MIGRATION_SHA256, "APPLIED")
        assert conn.execute(
            "SELECT writer_id FROM life_runtime_uow"
        ).fetchone() == ("life_runtime_repository",)
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("UPDATE life_runtime_uow SET status='CONFLICT'")
        conn.execute(
            "UPDATE schema_migration_assets SET checksum=? WHERE asset_key=?",
            ("0" * 64, LIFE_RUNTIME_MIGRATION_ASSET_KEY),
        )
    with pytest.raises(LifeRuntimeRepositoryError) as exc_info:
        LifeRuntimeRepository(db_path)
    assert exc_info.value.reason_code == LIFE_RUNTIME_SCHEMA_NOT_READY


def test_state_cas_and_occ_provenance_are_one_uow(tmp_path: Path) -> None:
    repository = LifeRuntimeRepository(_migrated_db(tmp_path))
    assert _save(repository, _state(1), 0)["status"] == "APPLIED"

    def update(index: int) -> str:
        result = _save(repository, _state(2, mood=f"mood-{index}"), 1)
        return str(result["status"])

    with ThreadPoolExecutor(max_workers=12) as pool:
        outcomes = list(pool.map(update, range(12)))
    assert outcomes.count("APPLIED") == 1
    assert outcomes.count("CONFLICT") == 11
    assert repository.get_state_version("person-1") == 2
    with sqlite3.connect(repository.db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM digital_life_state_occ_event"
        ).fetchone()[0] == 13
        assert conn.execute(
            "SELECT COUNT(*) FROM life_runtime_uow WHERE operation IN ('STATE_WRITE','STATE_CONFLICT')"
        ).fetchone()[0] == 13


def test_experience_organ_state_and_events_commit_exactly_once(tmp_path: Path) -> None:
    repository = LifeRuntimeRepository(_migrated_db(tmp_path))
    repository.append_experience(
        {
            "event_id": "experience-1",
            "person_id": "person-1",
            "session_id": "session-1",
            "source_kind": "contract",
            "event_kind": "user_message",
            "content_summary": "hello",
            "evidence": {},
            "salience": 0.5,
            "valence": 0.2,
            "uncertainty": 0.1,
            "created_at_ms": NOW,
        }
    )
    applied = repository.commit_organ_processing(
        experience_event_id="experience-1",
        person_id="person-1",
        organ_events=(
            {
                "event_id": "organ-event-1",
                "organ_id": "limbic",
                "delta": {"activation": 0.2},
                "state_before": {"organ_id": "limbic", "activation": 0.0},
                "state_after": {"organ_id": "limbic", "activation": 0.2},
            },
        ),
        coupling_events=(),
        organ_states={"limbic": {"organ_id": "limbic", "activation": 0.2}},
        created_at_ms=NOW,
    )
    assert applied is True
    assert repository.commit_organ_processing(
        experience_event_id="experience-1",
        person_id="person-1",
        organ_events=(),
        coupling_events=(),
        organ_states={},
        created_at_ms=NOW,
    ) is False
    assert repository.get_experience("experience-1")["consumed_by_organs"] is True
    assert repository.get_organ_states("person-1")["limbic"]["activation"] == 0.2
    with sqlite3.connect(repository.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM organ_runtime_event").fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM life_runtime_uow WHERE operation='ORGAN_PROCESS'"
        ).fetchone()[0] == 1
