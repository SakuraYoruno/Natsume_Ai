from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import inspect
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from host_brain.app.memory_event_store import MemoryEventStore
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.conversation.memory_spine import record_conversation_turn_memory_spine
import host_brain.digital_life.experience_ingress as experience_ingress
from host_brain.digital_life.experience_ingress import record_life_experience


NOW = 1_900_000_000_000


class DurableLedgerReference:
    def __init__(self, db_path: Path) -> None:
        self.canonical_db_path = str(db_path)

    def __getattr__(self, name: str):
        if name in {"_conn", "_write_lock"}:
            raise AssertionError("Life experience cutover reached SQLiteLedger internals")
        raise AttributeError(name)


def _ledger(tmp_path: Path) -> tuple[Path, DurableLedgerReference]:
    db_path = tmp_path / "aw04-life-experience.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw04-life-experience-cutover",
        ts_ms=NOW,
    )
    assert result.success is True, result.as_dict()
    return db_path, DurableLedgerReference(db_path)


def _count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def test_record_life_experience_is_one_idempotent_command_without_legacy_writers(
    tmp_path: Path,
) -> None:
    db_path, ledger = _ledger(tmp_path)
    arguments = {
        "ledger": ledger,
        "person_id": "person-aw04",
        "session_id": "session-aw04",
        "source_kind": "inner_stream",
        "event_kind": "inner_stream_tick",
        "content_summary": "她刚才有一点走神。",
        "evidence": {
            "request_id": "request-aw04-life-experience",
            "turn_id": "turn-aw04-life-experience",
            "memory_event_visibility": "private",
            "ts_ms": NOW,
        },
        "salience": 0.42,
        "valence": -0.05,
        "uncertainty": 0.08,
        "consume": True,
    }

    first = record_life_experience(**arguments)
    second = record_life_experience(**arguments)

    assert first["event_id"] == second["event_id"]
    assert first["state_event_id"] == first["event_id"]
    assert first["state"]["version"] == 1
    assert second["state"]["version"] == 1
    assert first["experience"]["replayed"] is False
    assert second["experience"]["replayed"] is True
    assert second["life_action"]["command_id"] == first["experience"]["command_id"]

    with sqlite3.connect(str(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        assert _count(connection, "life_command") == 1
        assert _count(connection, "life_event_journal") == 1
        assert _count(connection, "life_command_receipt") == 1
        assert _count(connection, "life_projection_intent") == 2
        intents = connection.execute(
            """
            SELECT intent_kind, target, payload_json
            FROM life_projection_intent
            ORDER BY intent_kind, target
            """
        ).fetchall()
        assert {(row["intent_kind"], row["target"]) for row in intents} == {
            ("ORGAN", "experience"),
            ("PROJECTION", "life.experience"),
        }
        assert all(
            json.loads(row["payload_json"])["event_id"] == first["event_id"]
            for row in intents
        )
        command_payload = json.loads(
            connection.execute("SELECT payload_json FROM life_command").fetchone()[0]
        )
        assert command_payload["salience"] == 0.42
        assert command_payload["valence"] == -0.05
        assert command_payload["evidence"]["request_id"] == (
            "request-aw04-life-experience"
        )
        assert set(command_payload["state_patch"]) == {"continuity"}
        assert command_payload["state_patch"]["continuity"][
            "last_experience_event_id"
        ] == first["event_id"]
        assert _count(connection, "life_experience_event") == 0
        assert _count(connection, "digital_life_state_event") == 0
        assert _count(connection, "digital_life_state_occ_event") == 0
        assert _count(connection, "life_runtime_uow") == 0


def test_memory_event_projection_uses_memory_event_identity_and_receipt_replay(
    tmp_path: Path,
) -> None:
    db_path, ledger = _ledger(tmp_path)
    store = MemoryEventStore(ledger)
    event = store.append_event(
        person_id="person-aw04",
        session_id="session-aw04",
        conversation_id="session-aw04",
        source_kind="conversation_turn",
        event_kind="user_message",
        visibility="visible",
        role="user",
        content="你好",
        refs={"request_id": "request-aw04"},
        salience=0.7,
        created_at_ms=NOW,
    )
    memory_event_id = str(event["event_id"])

    first = store.project_life_experience_from_event(memory_event_id)
    second = store.project_life_experience_from_event(memory_event_id)

    assert first["status"] == "OK"
    assert second["status"] == "EXISTS"
    assert first["event_id"] == second["event_id"]
    assert first["command_id"] == second["command_id"]
    assert first["replayed"] is False
    assert second["replayed"] is True
    with sqlite3.connect(str(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        assert _count(connection, "life_command") == 1
        assert _count(connection, "life_event_journal") == 1
        command = connection.execute(
            "SELECT command_id, metadata_json FROM life_command"
        ).fetchone()
        assert command["command_id"] == first["command_id"]
        assert command["command_id"] == f"life::memory_event::{memory_event_id}"
        assert json.loads(command["metadata_json"])["identity_kind"] == (
            "canonical_life_command_id"
        )
        intent = connection.execute(
            """
            SELECT payload_json FROM life_projection_intent
            WHERE intent_kind = 'PROJECTION' AND target = 'life.experience'
            """
        ).fetchone()
        assert json.loads(intent["payload_json"])["memory_event_id"] == memory_event_id
        assert _count(connection, "life_experience_event") == 0
        assert _count(connection, "life_runtime_uow") == 0


def test_legacy_life_experience_ref_cannot_bypass_missing_canonical_receipt(
    tmp_path: Path,
) -> None:
    db_path, ledger = _ledger(tmp_path)
    store = MemoryEventStore(ledger)
    event = store.append_event(
        person_id="person-legacy-ref",
        session_id="session-legacy-ref",
        source_kind="legacy_import",
        event_kind="inner_stream_tick",
        visibility="private",
        content="legacy projected event",
        refs={"life_experience_event_id": "lifeexp_legacy_only"},
        created_at_ms=NOW,
    )
    memory_event_id = str(event["event_id"])

    first = store.project_life_experience_from_event(memory_event_id)
    second = store.project_life_experience_from_event(memory_event_id)

    assert first["status"] == "OK"
    assert first["event_id"] != "lifeexp_legacy_only"
    assert first["command_id"] == f"life::memory_event::{memory_event_id}"
    assert second["status"] == "EXISTS"
    assert second["event_id"] == first["event_id"]
    with sqlite3.connect(str(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        receipt = connection.execute(
            "SELECT command_id, event_id FROM life_command_receipt"
        ).fetchone()
        assert receipt["command_id"] == first["command_id"]
        assert receipt["event_id"] == first["event_id"]
        projection = connection.execute(
            """
            SELECT payload_json FROM life_projection_intent
            WHERE intent_kind = 'PROJECTION' AND target = 'life.experience'
            """
        ).fetchone()
        assert json.loads(projection["payload_json"])["evidence"][
            "life_experience_event_id"
        ] == "lifeexp_legacy_only"


def test_concurrent_duplicate_life_experience_converges_on_one_receipt(
    tmp_path: Path,
) -> None:
    db_path, ledger = _ledger(tmp_path)

    def submit(_index: int) -> dict:
        return record_life_experience(
            ledger=ledger,
            person_id="person-concurrent",
            source_kind="conversation_turn",
            event_kind="assistant_reply",
            content_summary="同一条回复",
            evidence={"request_id": "request-concurrent", "ts_ms": NOW},
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(submit, range(16)))

    assert len({result["event_id"] for result in results}) == 1
    assert sum(not result["experience"]["replayed"] for result in results) == 1
    with sqlite3.connect(str(db_path)) as connection:
        assert _count(connection, "life_command") == 1
        assert _count(connection, "life_event_journal") == 1
        assert _count(connection, "life_command_receipt") == 1


def test_signature_identity_replays_one_occurrence_but_not_a_later_occurrence(
    tmp_path: Path,
) -> None:
    db_path, ledger = _ledger(tmp_path)

    def submit(ts_ms: int) -> dict:
        return record_life_experience(
            ledger=ledger,
            person_id="person-inner-world",
            session_id="inner_world",
            source_kind="inner_world",
            event_kind="inner_numeric_tick",
            content_summary="same inner numeric signature",
            evidence={"signature": "steady|anchor|quiet", "ts_ms": ts_ms},
        )

    first = submit(NOW)
    replay = submit(NOW)
    later = submit(NOW + 120_000)

    assert replay["event_id"] == first["event_id"]
    assert replay["experience"]["replayed"] is True
    assert later["event_id"] != first["event_id"]
    assert later["experience"]["replayed"] is False
    assert later["state"]["version"] == 2
    with sqlite3.connect(str(db_path)) as connection:
        assert _count(connection, "life_command") == 2
        assert _count(connection, "life_event_journal") == 2


def test_replay_returns_receipt_exact_state_after_later_commands(
    tmp_path: Path,
) -> None:
    _db_path, ledger = _ledger(tmp_path)
    first_arguments = {
        "ledger": ledger,
        "person_id": "person-exact-state",
        "source_kind": "conversation_turn",
        "event_kind": "user_message",
        "content_summary": "first",
        "evidence": {"request_id": "request-first", "ts_ms": NOW},
    }

    first = record_life_experience(**first_arguments)
    second = record_life_experience(
        ledger=ledger,
        person_id="person-exact-state",
        source_kind="conversation_turn",
        event_kind="assistant_reply",
        content_summary="second",
        evidence={"request_id": "request-second", "ts_ms": NOW + 1},
    )
    replay = record_life_experience(**first_arguments)

    assert first["state"]["version"] == 1
    assert second["state"]["version"] == 2
    assert replay["experience"]["replayed"] is True
    assert replay["state"] == first["state"]
    assert replay["state"]["version"] == replay["experience"]["state_version"] == 1


def test_historical_occurrence_cannot_move_snapshot_apply_time_backward(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _db_path, ledger = _ledger(tmp_path)
    monkeypatch.setattr(experience_ingress, "_now_ms", lambda: 5_000)
    current = record_life_experience(
        ledger=ledger,
        person_id="person-monotonic-time",
        source_kind="conversation_turn",
        event_kind="user_message",
        content_summary="current event",
        evidence={"request_id": "request-current", "ts_ms": 5_000},
    )
    monkeypatch.setattr(experience_ingress, "_now_ms", lambda: 4_000)
    historical = record_life_experience(
        ledger=ledger,
        person_id="person-monotonic-time",
        source_kind="memory_backfill",
        event_kind="user_message",
        content_summary="historical event",
        evidence={"request_id": "request-historical", "ts_ms": 1_000},
    )

    assert current["experience"]["created_at_ms"] == 5_000
    assert historical["experience"]["occurred_at_ms"] == 1_000
    assert historical["experience"]["created_at_ms"] == 5_000
    assert historical["state"]["last_command_at_ms"] == 5_000
    assert historical["state"]["version"] == 2


def test_memory_spine_command_id_and_type_are_the_canonical_receipt_identity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path, ledger = _ledger(tmp_path)
    monkeypatch.setattr("host_brain.app.dependencies.get_ledger", lambda: ledger)

    first = record_conversation_turn_memory_spine(
        request_id="request-memory-spine",
        session_id="session-memory-spine",
        person_id="person-memory-spine",
        role="user",
        content="不要重新生成 command id",
        ts_ms=NOW,
        lang="zh",
        turn_id="turn-memory-spine",
        turn_index=3,
    )
    replay = record_conversation_turn_memory_spine(
        request_id="request-memory-spine",
        session_id="session-memory-spine",
        person_id="person-memory-spine",
        role="user",
        content="不要重新生成 command id",
        ts_ms=NOW,
        lang="zh",
        turn_id="turn-memory-spine",
        turn_index=3,
    )
    expected_command_id = "life::conversation_turn::request-memory-spine::user"

    assert first["status"] == "OK"
    assert replay == {
        "status": "SKIPPED",
        "reason_code": "DUPLICATE_CONVERSATION_TURN",
    }
    with sqlite3.connect(str(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        receipt = connection.execute(
            "SELECT command_id FROM life_command_receipt"
        ).fetchone()
        command = connection.execute(
            "SELECT command_id, command_type, payload_json FROM life_command"
        ).fetchone()
        assert receipt["command_id"] == expected_command_id
        assert command["command_id"] == expected_command_id
        assert command["command_type"] == "conversation_turn_recorded"
        payload = json.loads(command["payload_json"])
        assert payload["event_kind"] == "user_message"
        assert payload["salience"] == 0.62
        assert payload["evidence"]["canonical_life_command_id"] == (
            expected_command_id
        )
        assert payload["state_patch"]["continuity"]["last_visible_event_id"] == (
            first["event_id"]
        )


def test_life_command_authority_cache_is_bounded_lru(monkeypatch) -> None:
    class FakeRepository:
        def __init__(self, db_path: str) -> None:
            self.db_path = Path(db_path)

    monkeypatch.setattr(
        experience_ingress.LifeCommandRepository,
        "from_ledger",
        classmethod(
            lambda _cls, ledger: FakeRepository(ledger.canonical_db_path)
        ),
    )
    monkeypatch.setattr(
        experience_ingress,
        "LifeCommandService",
        lambda repository: ("service", str(repository.db_path)),
    )
    monkeypatch.setattr(
        experience_ingress,
        "LifeSnapshotReader",
        lambda repository: ("reader", str(repository.db_path)),
    )
    with experience_ingress._LIFE_COMMAND_AUTHORITY_LOCK:
        saved = experience_ingress._LIFE_COMMAND_AUTHORITIES.copy()
        experience_ingress._LIFE_COMMAND_AUTHORITIES.clear()
    try:
        authorities = [
            experience_ingress._life_command_authority(
                SimpleNamespace(canonical_db_path=f"tenant-{index}.sqlite3")
            )
            for index in range(65)
        ]
        last_replay = experience_ingress._life_command_authority(
            SimpleNamespace(canonical_db_path="tenant-64.sqlite3")
        )

        assert len(experience_ingress._LIFE_COMMAND_AUTHORITIES) == 64
        assert "tenant-0.sqlite3" not in experience_ingress._LIFE_COMMAND_AUTHORITIES
        assert last_replay is authorities[-1]
        assert next(reversed(experience_ingress._LIFE_COMMAND_AUTHORITIES)) == (
            "tenant-64.sqlite3"
        )
    finally:
        with experience_ingress._LIFE_COMMAND_AUTHORITY_LOCK:
            experience_ingress._LIFE_COMMAND_AUTHORITIES.clear()
            experience_ingress._LIFE_COMMAND_AUTHORITIES.update(saved)


def test_record_life_experience_source_has_no_retired_state_or_experience_writer() -> None:
    source = inspect.getsource(record_life_experience)

    assert "append_life_experience(" not in source
    assert "synthesize_life_state_from_organs(" not in source
    assert "_save_state(" not in source
    assert "_append_state_event(" not in source
    assert ".submit_next(" in source


def test_production_experience_callsites_do_not_import_legacy_life_continuity() -> None:
    root = Path(__file__).resolve().parents[2]
    for relative in (
        "host_brain/app/memory_event_store.py",
        "host_brain/conversation/memory_spine.py",
        "host_brain/persona/inner_world.py",
    ):
        source = (root / relative).read_text(encoding="utf-8")
        assert "runtime.life_continuity import record_life_experience" not in source
        assert "digital_life.experience_ingress import" in source

    compatibility_source = inspect.getsource(
        __import__(
            "host_brain.runtime.life_continuity",
            fromlist=["record_life_experience"],
        ).record_life_experience
    )
    assert "canonical_record_life_experience" in compatibility_source
    assert "LifeCommandRepository" not in compatibility_source
    assert ".submit_next(" not in compatibility_source
