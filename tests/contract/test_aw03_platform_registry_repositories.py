from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.channels.vts_mapping_store import VTSMappingStore
from host_brain.persistence.tool_registry_repository import (
    PLATFORM_REGISTRY_MIGRATION_ASSET_KEY,
    PLATFORM_REGISTRY_MIGRATION_SHA256,
    TOOL_REGISTRY_CONFLICT,
    TOOL_REGISTRY_SCHEMA_NOT_READY,
    ToolRegistryRepository,
    ToolRegistryRepositoryError,
)
from host_brain.persistence.vts_mapping_repository import (
    VTS_MAPPING_CONFLICT,
    VTS_MAPPING_NOT_FOUND,
    VTS_MAPPING_SCHEMA_NOT_READY,
    VTSMappingRepository,
    VTSMappingRepositoryError,
)


ROOT = Path(__file__).resolve().parents[2]
ASSET = ROOT / "host_brain" / "audit" / "migrations" / "0072_platform_registry_persistence.sql"


@pytest.fixture(scope="module")
def migrated_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("platform-registry") / "template.sqlite3"
    result = SQLiteMigrationRunner(db_path=path).apply_migrations(
        request_id="aw03-platform-registry-contract",
        ts_ms=1_800_000_000_000,
    )
    assert result.success is True, result.as_dict()
    return path


@pytest.fixture
def migrated_db(migrated_template: Path, tmp_path: Path) -> Path:
    target = tmp_path / "platform-registry.sqlite3"
    shutil.copyfile(migrated_template, target)
    return target


def _tool(*, version: str = "1.0.0", name: str = "Search") -> dict[str, object]:
    return {
        "provider_id": "github",
        "tool_id": "search",
        "name": name,
        "description": "Search issues",
        "access_level": "PUBLIC",
        "risk_level": "LOW",
        "reversible": True,
        "version": version,
        "schema_hash": "sha256:schema",
        "domain_tags": ["issues"],
        "emotion_requirement": [],
    }


def test_0072_asset_is_checksum_bound_and_explicitly_transactional() -> None:
    raw = ASSET.read_bytes()
    sql = raw.decode("utf-8")
    assert hashlib.sha256(raw).hexdigest() == PLATFORM_REGISTRY_MIGRATION_SHA256
    assert PLATFORM_REGISTRY_MIGRATION_ASSET_KEY == "0072#platform_registry_persistence"
    assert "BEGIN IMMEDIATE;" in sql
    assert sql.rstrip().endswith("COMMIT;")


def test_vts_revision_exact_replay_and_conflict() -> None:
    repository = VTSMappingRepository(":memory:", clock=lambda: 101)
    try:
        created = repository.save_mapping(
            {"happy": "hotkey_happy"}, version="v1", changed_by="contract"
        )
        replayed = repository.save_mapping(
            {"HAPPY": "hotkey_happy"}, version="v1", changed_by="contract"
        )

        assert replayed == created
        assert len(repository.list_versions()) == 1

        with pytest.raises(VTSMappingRepositoryError) as raised:
            repository.save_mapping(
                {"HAPPY": "different"}, version="v1", changed_by="contract"
            )
        assert raised.value.reason_code == VTS_MAPPING_CONFLICT
        assert repository.load_mapping(version="v1")["mapping"] == {
            "HAPPY": "hotkey_happy"
        }
    finally:
        repository.close()


def test_vts_mapping_and_journal_rollback_as_one_unit() -> None:
    def fail(stage: str) -> None:
        if stage == "after_mapping_write":
            raise RuntimeError("injected journal outage")

    repository = VTSMappingRepository(":memory:", fault_injector=fail)
    try:
        with pytest.raises(RuntimeError, match="injected journal outage"):
            repository.save_mapping(
                {"HAPPY": "hotkey_happy"}, version="v1", changed_by="contract"
            )
        assert repository.list_versions() == []
        with pytest.raises(VTSMappingRepositoryError) as raised:
            repository.load_mapping(version="v1")
        assert raised.value.reason_code == VTS_MAPPING_NOT_FOUND
    finally:
        repository.close()


def test_mcp_revision_exact_replay_conflict_and_update() -> None:
    repository = ToolRegistryRepository(":memory:", clock=lambda: 202)
    try:
        inserted = repository.register(_tool())
        replayed = repository.register(_tool())
        assert replayed == inserted
        assert len(repository.sync_log()) == 1

        with pytest.raises(ToolRegistryRepositoryError) as raised:
            repository.register(_tool(name="drifted"))
        assert raised.value.reason_code == TOOL_REGISTRY_CONFLICT

        updated = repository.register(_tool(version="2.0.0", name="Search v2"))
        assert updated["change_kind"] == "UPDATE"
        assert updated["diff"]["changed"]["name"] == {
            "before": "Search",
            "after": "Search v2",
        }
        assert repository.get(provider_id="github", tool_id="search")["version"] == "2.0.0"
        assert len(repository.sync_log()) == 2
    finally:
        repository.close()


def test_mcp_head_and_journal_rollback_as_one_unit() -> None:
    def fail(stage: str) -> None:
        if stage == "after_registry_write":
            raise RuntimeError("injected journal outage")

    repository = ToolRegistryRepository(":memory:", fault_injector=fail)
    try:
        with pytest.raises(RuntimeError, match="injected journal outage"):
            repository.register(_tool())
        assert repository.get(provider_id="github", tool_id="search") is None
        assert repository.sync_log() == []
    finally:
        repository.close()


def test_durable_restart_and_immutable_journals(migrated_db: Path) -> None:
    vts = VTSMappingRepository(migrated_db, clock=lambda: 303)
    vts.save_mapping({"HAPPY": "hotkey_happy"}, version="v1", changed_by="test")
    vts.close()
    tools = ToolRegistryRepository(migrated_db, clock=lambda: 404)
    tools.register(_tool())
    tools.close()

    restarted_vts = VTSMappingRepository(migrated_db)
    restarted_tools = ToolRegistryRepository(migrated_db)
    try:
        assert restarted_vts.load_mapping(version="v1")["mapping"]["HAPPY"] == "hotkey_happy"
        assert restarted_tools.get(provider_id="github", tool_id="search")["name"] == "Search"
        assert len(restarted_tools.sync_log()) == 1
    finally:
        restarted_vts.close()
        restarted_tools.close()

    with sqlite3.connect(migrated_db) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="IMMUTABLE"):
            conn.execute(
                "UPDATE vts_mapping_change_journal SET changed_by = 'x' WHERE version = 'v1'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="SEALED"):
            conn.execute(
                """
                INSERT INTO vts_expression_mapping(
                    version, emotion_tag, expression_id, created_ts_ms
                ) VALUES ('v1', 'EXTRA', 'hotkey_extra', 1)
                """
            )
        with pytest.raises(sqlite3.IntegrityError, match="IMMUTABLE"):
            conn.execute("DELETE FROM mcp_tool_sync_journal")


def test_vts_store_reconciles_pre_0072_rows_without_rewriting_them(
    migrated_db: Path,
) -> None:
    with sqlite3.connect(migrated_db) as conn:
        conn.execute(
            """
            INSERT INTO vts_expression_mapping(
                version, emotion_tag, expression_id, created_ts_ms
            ) VALUES ('v1.0.0', 'HAPPY', 'legacy_happy', 100)
            """
        )
        conn.commit()

    store = VTSMappingStore(db_path=str(migrated_db))
    try:
        assert store.load_mapping(version="v1.0.0").mapping == {
            "HAPPY": "legacy_happy"
        }
        versions = store.list_versions()
        assert len(versions) == 1
        assert versions[0]["version"] == "v1.0.0"
        assert versions[0]["new_hash"] == store.load_mapping(
            version="v1.0.0"
        ).content_hash
        assert versions[0]["changed_by"] == "legacy_backfill"
    finally:
        store.close()


def test_durable_repositories_fail_closed_without_catalog_or_on_checksum_drift(
    migrated_db: Path,
    tmp_path: Path,
) -> None:
    uncatalogued = tmp_path / "uncatalogued.sqlite3"
    with sqlite3.connect(uncatalogued) as conn:
        conn.executescript(ASSET.read_text(encoding="utf-8"))

    with pytest.raises(VTSMappingRepositoryError) as vts_raised:
        VTSMappingRepository(uncatalogued)
    assert vts_raised.value.reason_code == VTS_MAPPING_SCHEMA_NOT_READY
    with pytest.raises(ToolRegistryRepositoryError) as tool_raised:
        ToolRegistryRepository(uncatalogued)
    assert tool_raised.value.reason_code == TOOL_REGISTRY_SCHEMA_NOT_READY

    with sqlite3.connect(migrated_db) as conn:
        conn.execute(
            "UPDATE schema_migration_assets SET checksum = ? WHERE asset_key = ?",
            ("0" * 64, PLATFORM_REGISTRY_MIGRATION_ASSET_KEY),
        )
        conn.commit()
    with pytest.raises(VTSMappingRepositoryError) as drifted:
        VTSMappingRepository(migrated_db)
    assert drifted.value.reason_code == VTS_MAPPING_SCHEMA_NOT_READY
