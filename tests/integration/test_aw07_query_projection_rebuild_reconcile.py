from __future__ import annotations

import json
import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.observability.projection_store import (
    PROJECTION_CURSOR_CONFLICT,
    PROJECTION_DATA_INVALID,
    QueryProjectionError,
    QueryProjectionStore,
    canonical_json,
)


def test_projection_rebuilds_from_immutable_journal_and_reconciles_source(tmp_path):
    db_path = tmp_path / "projection.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(request_id="aw07-integration")
    assert migrated.success, migrated
    store = QueryProjectionStore(db_path)

    source_a = {"request_id": "req-a", "status": "OK"}
    source_b = {"request_id": "req-b", "status": "RUNNING"}
    store.upsert("trace", "req-a", source_a, source_cursor=10)
    store.upsert("trace", "req-b", source_b, source_cursor=11)

    rebuilt = store.rebuild_from_journal("trace")
    assert rebuilt["rebuilt_count"] == 2
    assert store.get("trace", "req-a").payload == source_a
    assert store.state("trace")["rebuilt_ts_ms"] is not None

    changed_b = {"request_id": "req-b", "status": "OK"}
    reconciled = store.reconcile(
        "trace",
        [
            ("req-a", source_a, 10),
            ("req-b", changed_b, 12),
        ],
    )
    assert reconciled["unchanged_count"] == 1
    assert reconciled["repaired_count"] == 1
    assert store.get("trace", "req-b").payload == changed_b

    with sqlite3.connect(db_path) as conn:
        try:
            conn.execute("DELETE FROM operator_query_projection_journal")
        except sqlite3.IntegrityError as exc:
            assert "immutable" in str(exc)
        else:
            raise AssertionError("projection journal deletion was not fenced")


def test_large_projection_journal_payload_is_compact_and_rebuilds_transparently(tmp_path):
    db_path = tmp_path / "projection-compressed.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw07-compressed-journal"
    )
    assert migrated.success, migrated
    store = QueryProjectionStore(db_path)
    payload = {
        "status": "READY",
        "items": [
            {"item_id": f"item-{index}", "summary": "自然陪伴投影" * 40}
            for index in range(400)
        ],
    }

    store.upsert("session", "index", payload, source_cursor=10)

    with sqlite3.connect(db_path) as conn:
        encoded = str(
            conn.execute(
                """
                SELECT source_payload_json
                FROM operator_query_projection_journal
                WHERE projection_kind = 'session' AND document_key = 'index'
                """
            ).fetchone()[0]
        )
    envelope = json.loads(encoded)
    assert isinstance(envelope, list)
    assert envelope[0] == "Natsume.operator.query_projection.journal_payload.zlib.v1"
    assert len(encoded.encode("utf-8")) < len(canonical_json(payload).encode("utf-8")) // 5
    assert store.get("session", "index").payload == payload
    assert store.journal_text_values("session", "index", "status") == frozenset({"READY"})

    rebuilt = store.rebuild_from_journal("session")
    assert rebuilt["rebuilt_count"] == 1
    assert store.get("session", "index").payload == payload


def test_small_legacy_journal_object_remains_readable(tmp_path):
    db_path = tmp_path / "projection-legacy-object.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw07-legacy-journal"
    )
    assert migrated.success, migrated
    store = QueryProjectionStore(db_path)
    payload = {"status": "READY"}

    store.upsert("trace", "small", payload, source_cursor=1)

    with sqlite3.connect(db_path) as conn:
        encoded = str(
            conn.execute(
                "SELECT source_payload_json FROM operator_query_projection_journal"
            ).fetchone()[0]
        )
    assert json.loads(encoded) == payload
    assert store.rebuild_from_journal("trace")["rebuilt_count"] == 1
    assert store.get("trace", "small").payload == payload


def test_compressed_projection_journal_fails_closed_on_digest_tamper(tmp_path):
    db_path = tmp_path / "projection-compressed-tamper.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw07-compressed-tamper"
    )
    assert migrated.success, migrated
    store = QueryProjectionStore(db_path)
    payload = {"status": "READY", "items": ["evidence" * 100 for _ in range(200)]}
    store.upsert("source", "valid", payload, source_cursor=1)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT schema_version, source_digest, source_payload_json
            FROM operator_query_projection_journal
            WHERE projection_kind = 'source'
            """
        ).fetchone()
        envelope = json.loads(str(row[2]))
        assert isinstance(envelope, list)
        envelope[2] = digest_json({"status": "TAMPERED"})
        conn.execute(
            """
            INSERT INTO operator_query_projection_journal(
                schema_version, projection_kind, document_key, source_cursor,
                source_digest, source_payload_json, source_ref, observed_ts_ms
            ) VALUES (?, 'tampered', 'invalid', 1, ?, ?, 'test', 1)
            """,
            (str(row[0]), str(row[1]), canonical_json(envelope)),
        )

    with pytest.raises(QueryProjectionError) as invalid:
        store.rebuild_from_journal("tampered")
    assert invalid.value.reason_code == PROJECTION_DATA_INVALID


def test_projection_history_checkpoint_bounds_refresh_writes_and_rebuilds_last_checkpoint(
    tmp_path,
):
    db_path = tmp_path / "projection-checkpoint.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw07-projection-checkpoint"
    )
    assert migrated.success, migrated
    clock = [1_000]
    store = QueryProjectionStore(
        db_path,
        clock=lambda: clock[0],
        journal_checkpoint_interval_ms=60_000,
    )

    first = store.upsert("session", "index", {"status": "A"}, source_cursor=1)
    clock[0] = 2_000
    current = store.upsert("session", "index", {"status": "B"}, source_cursor=2)

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM operator_query_projection_journal"
        ).fetchone()[0] == 1
    assert current.payload == {"status": "B"}
    assert current.source_cursor == 2
    assert current.projection_cursor == first.projection_cursor

    rebuilt = store.rebuild_from_journal("session")
    assert rebuilt["rebuilt_count"] == 1
    restored = store.get("session", "index")
    assert restored is not None
    assert restored.payload == {"status": "A"}
    assert restored.source_cursor == 1

    clock[0] = 61_001
    latest = store.upsert("session", "index", {"status": "C"}, source_cursor=3)
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM operator_query_projection_journal"
        ).fetchone()[0] == 2
    assert latest.payload == {"status": "C"}
    assert store.rebuild_from_journal("session")["rebuilt_count"] == 1
    assert store.get("session", "index").payload == {"status": "C"}


def test_forced_projection_transition_bypasses_history_checkpoint_interval(tmp_path):
    db_path = tmp_path / "projection-exact-kind.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw07-projection-exact-kind"
    )
    assert migrated.success, migrated
    clock = [1_000]
    store = QueryProjectionStore(
        db_path,
        clock=lambda: clock[0],
        journal_checkpoint_interval_ms=60_000,
    )

    store.upsert(
        "obs_internal_host_source_lineage",
        "runtime",
        {"source_instance_id": "host-a", "source_revision": 1},
        source_cursor=1,
    )
    clock[0] = 2_000
    store.upsert(
        "obs_internal_host_source_lineage",
        "runtime",
        {"source_instance_id": "host-a", "source_revision": 2},
        source_cursor=2,
    )
    clock[0] = 3_000
    store.upsert(
        "obs_internal_host_source_lineage",
        "runtime",
        {"source_instance_id": "host-b", "source_revision": 1},
        source_cursor=3,
        force_journal_checkpoint=True,
    )

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM operator_query_projection_journal"
        ).fetchone()[0] == 2
    assert store.journal_text_values(
        "obs_internal_host_source_lineage",
        "runtime",
        "source_instance_id",
    ) == frozenset({"host-a", "host-b"})


def test_checkpointed_rebuild_rejects_unjournaled_concurrent_current_change(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "projection-checkpoint-race.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw07-projection-checkpoint-race"
    )
    assert migrated.success, migrated
    clock = [1_000]
    rebuilding_store = QueryProjectionStore(
        db_path,
        clock=lambda: clock[0],
        journal_checkpoint_interval_ms=60_000,
    )
    competing_store = QueryProjectionStore(
        db_path,
        clock=lambda: clock[0],
        journal_checkpoint_interval_ms=60_000,
    )
    rebuilding_store.upsert("trace", "current", {"status": "A"}, source_cursor=1)
    original_rebuild = rebuilding_store.rebuild

    def advance_current_without_checkpoint(kind, documents, **kwargs):  # noqa: ANN001
        clock[0] = 2_000
        competing_store.upsert(
            "trace",
            "current",
            {"status": "B"},
            source_cursor=2,
        )
        return original_rebuild(kind, documents, **kwargs)

    monkeypatch.setattr(rebuilding_store, "rebuild", advance_current_without_checkpoint)
    with pytest.raises(QueryProjectionError) as conflict:
        rebuilding_store.rebuild_from_journal("trace")
    assert conflict.value.reason_code == PROJECTION_CURSOR_CONFLICT
    assert rebuilding_store.get("trace", "current").payload == {"status": "B"}


def test_all_projection_writes_remove_task_session_trace_bodies_before_sqlite(tmp_path):
    db_path = tmp_path / "public-projection.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(request_id="aw07-privacy")
    assert migrated.success, migrated
    store = QueryProjectionStore(db_path)

    secrets = {
        "task": "TASK_PRIVATE_BODY_7e3a9",
        "session": "SESSION_PRIVATE_PREVIEW_91cb2",
        "trace": "TRACE_PRIVATE_BODY_f552d",
    }
    store.upsert(
        "task_execution",
        "graph@1",
        {"task_description": secrets["task"], "acceptance_criteria": [secrets["task"]]},
        source_cursor=1,
    )
    store.upsert(
        "session",
        "index",
        {"items": [{"request_id": "req", "input_preview": secrets["session"]}]},
        source_cursor=2,
    )
    store.upsert(
        "trace",
        "req",
        {
            "summary": {"input_text": secrets["trace"], "output_text": secrets["trace"]},
            "stream_events": [{"chunk": secrets["trace"]}],
            "preview": "PRIVATE_ALIAS_PREVIEW_1fd2",
            "description": "PRIVATE_ALIAS_DESCRIPTION_7ab4",
            "resume_token_ref": "review_resume::PRIVATE_TOKEN_98ce",
            "accessToken": "PRIVATE_CAMEL_TOKEN_12ac",
            "privateReasoning": "PRIVATE_CAMEL_REASONING_38de",
            "rawText": "PRIVATE_CAMEL_RAW_TEXT_54fa",
            "messageBody": "PRIVATE_CAMEL_MESSAGE_BODY_76bc",
        },
        source_cursor=3,
    )
    store.mark_degraded(
        "trace",
        "req",
        state="SOURCE_UNAVAILABLE",
        detail=f"api_key={secrets['trace']}",
    )

    raw = db_path.read_bytes()
    for secret in secrets.values():
        assert secret.encode("utf-8") not in raw
    for secret in (
        b"PRIVATE_ALIAS_PREVIEW_1fd2",
        b"PRIVATE_ALIAS_DESCRIPTION_7ab4",
        b"PRIVATE_TOKEN_98ce",
        b"PRIVATE_CAMEL_TOKEN_12ac",
        b"PRIVATE_CAMEL_REASONING_38de",
        b"PRIVATE_CAMEL_RAW_TEXT_54fa",
        b"PRIVATE_CAMEL_MESSAGE_BODY_76bc",
    ):
        assert secret not in raw
    assert b"operator-private-" in raw


def test_rebuild_failure_rolls_back_atomic_switch_and_preserves_last_good(tmp_path, monkeypatch):
    db_path = tmp_path / "projection-rebuild-crash.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(request_id="aw07-rebuild-crash")
    assert migrated.success, migrated
    store = QueryProjectionStore(db_path)
    store.upsert("trace", "last-good", {"status": "GOOD"}, source_cursor=10)

    def crash_before_switch(_kind, _conn):  # noqa: ANN001
        raise RuntimeError("injected rebuild crash")

    monkeypatch.setattr(store, "_before_rebuild_commit", crash_before_switch)
    with pytest.raises(RuntimeError, match="injected rebuild crash"):
        store.rebuild(
            "trace",
            [("replacement", {"status": "NEW"}, 11)],
        )
    last_good = store.get("trace", "last-good")
    assert last_good is not None
    assert last_good.payload == {"status": "GOOD"}
    assert store.get("trace", "replacement") is None

    with pytest.raises(RuntimeError, match="injected rebuild crash"):
        store.rebuild_from_journal("trace")
    still_good = store.get("trace", "last-good")
    assert still_good is not None
    assert still_good.payload == {"status": "GOOD"}


def test_journal_rebuild_fails_closed_if_another_store_advances_snapshot(tmp_path, monkeypatch):
    db_path = tmp_path / "projection-rebuild-race.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(request_id="aw07-rebuild-race")
    assert migrated.success, migrated
    rebuilding_store = QueryProjectionStore(db_path)
    competing_store = QueryProjectionStore(db_path)
    rebuilding_store.upsert("trace", "old", {"status": "GOOD"}, source_cursor=10)
    original_rebuild = rebuilding_store.rebuild

    def advance_before_switch(kind, documents, **kwargs):  # noqa: ANN001
        competing_store.upsert("trace", "new", {"status": "NEW"}, source_cursor=11)
        return original_rebuild(kind, documents, **kwargs)

    monkeypatch.setattr(rebuilding_store, "rebuild", advance_before_switch)
    with pytest.raises(QueryProjectionError) as conflict:
        rebuilding_store.rebuild_from_journal("trace")
    assert conflict.value.reason_code == PROJECTION_CURSOR_CONFLICT
    assert rebuilding_store.get("trace", "old") is not None
    new_document = rebuilding_store.get("trace", "new")
    assert new_document is not None
    assert new_document.payload == {"status": "NEW"}
