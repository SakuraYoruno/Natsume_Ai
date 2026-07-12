from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3

from host_brain.app.memory_event_store import (
    MemoryEventStore,
    get_recent_memory_events,
    rebuild_memory_system,
    restore_memory_backup,
)
from host_brain.app.user_preference_store import (
    LIGHTNOVEL_DOMAIN,
    UserPreferenceStore,
)
from host_brain.audit.migration_runner import SQLiteMigrationRunner


NOW = 1_900_000_000_000
ROOT = Path(__file__).resolve().parents[2]


class DurableLedgerReference:
    def __init__(self, db_path: Path) -> None:
        self.canonical_db_path = str(db_path)

    def __getattr__(self, name: str):
        if name in {"_conn", "_write_lock"}:
            raise AssertionError("app store reached through SQLiteLedger internals")
        raise AttributeError(name)


def _ledger(tmp_path: Path) -> tuple[Path, DurableLedgerReference]:
    db_path = tmp_path / "context.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw03-memory-preference",
        ts_ms=NOW,
    )
    assert result.success is True, result.as_dict()
    return db_path, DurableLedgerReference(db_path)


def test_aw03_memory_event_uses_repository_and_visible_turn_uow(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _db_path, ledger = _ledger(tmp_path)
    store = MemoryEventStore(ledger)
    monkeypatch.setattr(
        store,
        "project_life_experience_from_event",
        lambda _event_id, consume=True: {"status": "OK", "event_id": "life-event-1"},
    )

    result = store.append_visible_turn(
        request_id="request-1",
        session_id="session-1",
        person_id="person-1",
        role="user",
        content="hello",
        ts_ms=NOW,
    )
    replay = store.append_visible_turn(
        request_id="request-1",
        session_id="session-1",
        person_id="person-1",
        role="user",
        content="hello",
        ts_ms=NOW,
    )

    assert result["status"] == "OK"
    assert replay["reason_code"] == "DUPLICATE_VISIBLE_TURN"
    recent = get_recent_memory_events(ledger=ledger, person_id="person-1")
    assert len(recent["items"]) == 1
    assert recent["items"][0]["refs"]["turn_id"] == result["turn"]["turn_id"]


def test_aw03_concurrent_visible_turn_admission_has_one_memory_authority(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path, ledger = _ledger(tmp_path)
    monkeypatch.setattr(
        MemoryEventStore,
        "project_life_experience_from_event",
        lambda self, _event_id, consume=True: {"status": "OK", "event_id": "life-1"},
    )

    def admit(_index: int) -> str:
        return MemoryEventStore(ledger).append_visible_turn(
            request_id="request-concurrent",
            session_id="session-concurrent",
            person_id="person-1",
            role="user",
            content="same message",
            ts_ms=NOW,
        )["status"]

    with ThreadPoolExecutor(max_workers=8) as executor:
        statuses = list(executor.map(admit, range(16)))

    assert statuses.count("OK") == 1
    assert statuses.count("SKIPPED") == 15
    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM memory_event WHERE session_id = ?",
            ("session-concurrent",),
        ).fetchone() == (1,)
        assert conn.execute(
            "SELECT COUNT(*) FROM conversation_turn WHERE session_id = ?",
            ("session-concurrent",),
        ).fetchone() == (1,)


def test_aw03_memory_rebuild_creates_verified_restorable_backup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path, ledger = _ledger(tmp_path)
    store = MemoryEventStore(ledger)
    store.append_event(
        person_id="person-1",
        source_kind="contract",
        event_kind="user_message",
        visibility="visible",
        role="user",
        content="preserve in backup",
        created_at_ms=NOW,
    )
    monkeypatch.chdir(tmp_path)

    result = rebuild_memory_system(ledger=ledger, person_id="person-1")

    assert result["backup_receipt"]["migration_asset_count"] > 0
    assert Path(result["backup_path"]).is_file()
    restored = tmp_path / "restored.sqlite3"
    restore = restore_memory_backup(
        backup_path=result["backup_path"],
        target_path=str(restored),
    )
    assert restore["migration_catalog_digest"] == result["backup_receipt"][
        "migration_catalog_digest"
    ]
    with sqlite3.connect(str(db_path)) as conn:
        current = conn.execute("SELECT event_kind FROM memory_event").fetchall()
        assert current == [("memory_system_rebuilt",)]
    with sqlite3.connect(str(restored)) as conn:
        restored_events = conn.execute("SELECT event_kind FROM memory_event").fetchall()
        assert restored_events == [("user_message",)]


def test_aw03_preference_and_delivery_share_repository_exactly_once(
    tmp_path: Path,
) -> None:
    db_path, ledger = _ledger(tmp_path)
    store = UserPreferenceStore(ledger=ledger)
    delivery_args = {
        "person_id": "person-1",
        "session_id": "session-1",
        "domain": LIGHTNOVEL_DOMAIN,
        "platform": "qq",
        "conversation_id": "conversation-1",
        "request_id": "request-1",
        "job_id": "job-1",
        "recommendations_ref": "artifact://recommendations/1",
        "ranked_recommendations_ref": "artifact://ranked/1",
        "items": [{"recommendation_rank": 1, "title": "Book A", "series_key": "a"}],
    }
    first = store.record_delivery(**delivery_args, created_at_ms=NOW)
    replay = store.record_delivery(**delivery_args, created_at_ms=NOW + 10)

    assert first is not None and replay is not None
    assert replay["delivery_id"] == first["delivery_id"]
    assert replay["created_at_ms"] == first["created_at_ms"]
    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM recommendation_delivery_log").fetchone() == (1,)


def test_aw03_memory_and_preference_app_files_have_no_sqlite_authority() -> None:
    forbidden = (
        "CREATE TABLE",
        "ALTER TABLE",
        "sqlite3.connect",
        "self._conn",
        "self.conn",
        ".execute(",
        ".executescript(",
    )
    for relative in (
        "host_brain/app/memory_event_store.py",
        "host_brain/app/user_preference_store.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, f"{relative} still contains {token}"

    for relative in (
        "host_brain/persistence/context_repository.py",
        "host_brain/persistence/context_memory_repository.py",
        "host_brain/persistence/context_preference_repository.py",
        "host_brain/persistence/sqlite_backup.py",
    ):
        assert len((ROOT / relative).read_text(encoding="utf-8").splitlines()) < 800
