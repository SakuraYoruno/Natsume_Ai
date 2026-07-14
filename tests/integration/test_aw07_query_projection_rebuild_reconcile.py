from __future__ import annotations

import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.observability.projection_store import (
    PROJECTION_CURSOR_CONFLICT,
    QueryProjectionError,
    QueryProjectionStore,
    digest_json,
)


def test_projection_rebuilds_from_immutable_journal_and_reconciles_source(tmp_path):
    db_path = tmp_path / "projection.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(request_id="aw07-integration")
    assert migrated.success, migrated
    store = QueryProjectionStore(db_path)

    source_a = {"request_id": "req-a", "status": "OK"}
    source_b = {"request_id": "req-b", "status": "RUNNING"}
    store.upsert("trace", "req-a", source_a, source_cursor=10, source_digest=digest_json(source_a))
    store.upsert("trace", "req-b", source_b, source_cursor=11, source_digest=digest_json(source_b))

    rebuilt = store.rebuild_from_journal("trace")
    assert rebuilt["rebuilt_count"] == 2
    assert store.get("trace", "req-a").payload == source_a
    assert store.state("trace")["rebuilt_ts_ms"] is not None

    changed_b = {"request_id": "req-b", "status": "OK"}
    reconciled = store.reconcile(
        "trace",
        [
            ("req-a", source_a, 10, digest_json(source_a)),
            ("req-b", changed_b, 12, digest_json(changed_b)),
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
            [("replacement", {"status": "NEW"}, 11, digest_json({"status": "NEW"}))],
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
