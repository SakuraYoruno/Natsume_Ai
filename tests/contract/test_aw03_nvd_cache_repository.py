from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.pentest.data.nvd_client import NvdClient
from host_brain.pentest.data.nvd_settings import NvdSettings, load_nvd_settings
from host_brain.persistence.nvd_cache_repository import (
    NVD_CACHE_INVALID,
    NVD_CACHE_MIGRATION_ASSET_KEY,
    NVD_CACHE_MIGRATION_SHA256,
    NVD_CACHE_SCHEMA_NOT_READY,
    NvdCacheRepository,
    NvdCacheRepositoryError,
)


ROOT = Path(__file__).resolve().parents[2]
ASSET = ROOT / "host_brain" / "audit" / "migrations" / "0077_nvd_cache_persistence.sql"


def _migrated_db(tmp_path: Path) -> Path:
    path = tmp_path / "nvd-cache.sqlite3"
    result = SQLiteMigrationRunner(db_path=path).apply_migrations(
        request_id="aw03-nvd-cache-contract",
        ts_ms=1_900_000_000_000,
    )
    assert result.success is True, result.as_dict()
    return path


def _cve(cve_id: str, *, score: float = 9.8, description: str = "RCE") -> dict[str, object]:
    return {
        "id": cve_id,
        "vendor": "vendor",
        "product": "product",
        "cvss_score": score,
        "description": description,
        "attack_chain": ["rce"],
        "last_modified": "2026-01-01T00:00:00.000",
    }


def test_0077_asset_is_transactional_checksum_bound_and_catalogued(tmp_path: Path) -> None:
    sql = ASSET.read_text(encoding="utf-8-sig")
    assert hashlib.sha256(sql.encode("utf-8")).hexdigest() == NVD_CACHE_MIGRATION_SHA256
    assert NVD_CACHE_MIGRATION_ASSET_KEY == "0077#nvd_cache_persistence"
    assert "BEGIN IMMEDIATE;" in sql
    assert sql.rstrip().endswith("COMMIT;")

    db_path = _migrated_db(tmp_path)
    with sqlite3.connect(db_path) as conn:
        asset = conn.execute(
            "SELECT checksum, disposition FROM schema_migration_assets WHERE asset_key = ?",
            (NVD_CACHE_MIGRATION_ASSET_KEY,),
        ).fetchone()
    assert asset == (NVD_CACHE_MIGRATION_SHA256, "APPLIED")


def test_upsert_read_sync_state_and_prune_are_atomic() -> None:
    repository = NvdCacheRepository(":memory:")
    try:
        repository.upsert_cve(_cve("CVE-2026-0001"), synced_at="2026-01-01")
        repository.upsert_cve(
            _cve("CVE-2026-0001", score=10.0, description="updated"),
            synced_at="2026-02-01",
        )
        assert repository.stats()["total_cached"] == 1
        assert repository.get_cve("cve-2026-0001")["description"] == "updated"

        committed = repository.commit_sync(
            [_cve("CVE-2026-0002")],
            last_sync_ts="2026-03-01",
            last_mod_end_date="2026-02-28",
        )
        assert committed == {"upserted": 1, "total_cached": 2}
        assert repository.get_sync_state("last_mod_end_date") == "2026-02-28"

        assert repository.prune_synced_before("2026-04-01") == 2
        assert repository.stats()["total_cached"] == 0
        assert repository.stats()["total_synced"] == 0
    finally:
        repository.close()


def test_invalid_sync_batch_rolls_back_without_advancing_watermark() -> None:
    repository = NvdCacheRepository(":memory:")
    try:
        with pytest.raises(NvdCacheRepositoryError) as raised:
            repository.commit_sync(
                [_cve("CVE-2026-GOOD"), _cve("CVE-2026-BAD", score=11.0)],
                last_sync_ts="2026-03-01",
                last_mod_end_date="2026-02-28",
            )
        assert raised.value.reason_code == NVD_CACHE_INVALID
        assert repository.is_empty() is True
        assert repository.get_sync_state("last_mod_end_date") is None
    finally:
        repository.close()


def test_durable_restart_requires_catalog_and_exact_checksum(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    first = NvdCacheRepository(db_path)
    first.upsert_cve(_cve("CVE-2026-DURABLE"), synced_at="2026-01-01")
    first.close()

    restarted = NvdCacheRepository(db_path)
    try:
        assert restarted.get_cve("CVE-2026-DURABLE") is not None
    finally:
        restarted.close()

    missing = tmp_path / "must-not-be-created.sqlite3"
    with pytest.raises(NvdCacheRepositoryError) as unavailable:
        NvdCacheRepository(missing)
    assert unavailable.value.reason_code == NVD_CACHE_SCHEMA_NOT_READY
    assert missing.exists() is False

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE schema_migration_assets SET checksum = ? WHERE asset_key = ?",
            ("0" * 64, NVD_CACHE_MIGRATION_ASSET_KEY),
        )
        conn.commit()
    with pytest.raises(NvdCacheRepositoryError) as drifted:
        NvdCacheRepository(db_path)
    assert drifted.value.reason_code == NVD_CACHE_SCHEMA_NOT_READY


def test_typed_settings_own_environment_resolution() -> None:
    settings = load_nvd_settings(
        environ={
            "NVD_CACHE_DB_PATH": ":memory:",
            "NVD_API_KEY": "test-key",
            "NVD_INITIAL_DAYS": "30",
            "NVD_AUTO_SYNC_EMPTY_CACHE": "false",
        }
    )
    assert settings.cache_db_path == ":memory:"
    assert settings.api_key == "test-key"
    assert settings.initial_days == 30
    assert settings.auto_sync_empty_cache is False

    for relative_path in (
        "host_brain/pentest/data/nvd_client.py",
        "host_brain/pentest/agent/threat_intel.py",
        "host_brain/pentest/agent/online_research.py",
    ):
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert 'get("NVD_API_KEY"' not in source, relative_path


def test_nvd_client_uses_only_semantic_cache_operations() -> None:
    client = NvdClient(
        settings=NvdSettings(
            cache_db_path=":memory:",
            auto_sync_empty_cache=False,
        )
    )
    try:
        assert client.search_cpe(vendor="none") == []
        assert client.stats()["total_cached"] == 0
    finally:
        client.close()

    source = (ROOT / "host_brain" / "pentest" / "data" / "nvd_client.py").read_text(
        encoding="utf-8"
    )
    forbidden = (
        "sqlite3",
        "os.environ",
        "os.getenv",
        "_get_connection",
        "_connect",
        ".execute(",
        "executescript",
        "CREATE TABLE",
        "CREATE INDEX",
        "SELECT ",
        "INSERT ",
        "DELETE ",
    )
    for token in forbidden:
        assert token not in source, f"NVD client still contains {token}"

    repository_source = (
        ROOT / "host_brain" / "persistence" / "nvd_cache_repository.py"
    ).read_text(encoding="utf-8")
    assert "def _transaction(" in repository_source
    assert 'conn.execute("BEGIN IMMEDIATE")' in repository_source
    assert "schema_migration_assets" in repository_source
