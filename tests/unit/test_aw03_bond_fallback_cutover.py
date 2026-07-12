from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from host_brain.app.dependencies import get_bond_neo4j_client
from host_brain.bond.neo4j_client import BOND_WRITER_ID, SqliteBondClient
from host_brain.persistence.bond_fallback_repository import (
    BOND_FALLBACK_MIGRATION_ASSET_KEY,
    BOND_FALLBACK_MIGRATION_SHA256,
)


ROOT = Path(__file__).resolve().parents[2]


def test_bond_business_boundary_has_no_fallback_connection_schema_or_sql() -> None:
    source = (ROOT / "host_brain" / "bond" / "neo4j_client.py").read_text(
        encoding="utf-8"
    )
    lowered = source.lower()
    assert "sqlite3" not in lowered
    assert "self._conn" not in source
    assert "def _connect" not in source
    assert "create table" not in lowered
    assert "insert or replace" not in lowered
    assert "select * from bond_events" not in lowered
    assert ".execute(" not in source
    assert "BondFallbackRepository" in source


def test_sqlite_bond_facade_preserves_writer_and_upsert_contract_in_memory() -> None:
    client = SqliteBondClient(db_path=":memory:")
    try:
        with pytest.raises(PermissionError):
            client.write_bond_event(
                writer_id="external",
                event_id="event",
                user_id="user",
                session_id="session",
                topic_id="topic",
                capability_hint="support",
                evidence_refs=["evidence://1"],
                payload={},
                ts_ms=1,
            )

        first = client.write_bond_event(
            writer_id=BOND_WRITER_ID,
            event_id="event",
            user_id="user",
            session_id="session",
            topic_id="topic",
            capability_hint="support",
            evidence_refs=["evidence://1"],
            payload={"revision": 1},
            ts_ms=1,
        )
        assert client.write_bond_event(
            writer_id=BOND_WRITER_ID,
            event_id="event",
            user_id="user",
            session_id="session",
            topic_id="topic",
            capability_hint="support",
            evidence_refs=["evidence://1"],
            payload={"revision": 1},
            ts_ms=1,
        ) == first
        updated = client.write_bond_event(
            writer_id=BOND_WRITER_ID,
            event_id="event",
            user_id="user",
            session_id="session",
            topic_id="topic",
            capability_hint="support",
            evidence_refs=["evidence://2"],
            payload={"revision": 2},
            ts_ms=2,
        )
        assert client.get_event("event") == updated
        assert client.events == [updated]
    finally:
        client.close()


def test_app_composition_migrates_durable_bond_db_before_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "composed-bond.sqlite3"
    monkeypatch.setenv("HOST_BOND_BACKEND", "sqlite")
    monkeypatch.setenv("HOST_BOND_SQLITE_PATH", str(db_path))

    client = get_bond_neo4j_client()
    try:
        assert isinstance(client, SqliteBondClient)
        assert db_path.is_file()
    finally:
        client.close()

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT checksum, disposition FROM schema_migration_assets
            WHERE asset_key = ?
            """,
            (BOND_FALLBACK_MIGRATION_ASSET_KEY,),
        ).fetchone()
    assert row == (BOND_FALLBACK_MIGRATION_SHA256, "APPLIED")
