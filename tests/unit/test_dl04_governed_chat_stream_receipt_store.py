from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sqlite3

import pytest

from host_brain.persistence.governed_chat_stream_receipt_repository import (
    GOVERNED_CHAT_STREAM_RECEIPT_REPLAY_DRIFT,
    GovernedChatStreamReceiptStore,
    GovernedChatStreamReceiptStoreError,
)


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (
    ROOT
    / "host_brain"
    / "audit"
    / "migrations"
    / "0149_governed_chat_stream_receipts.sql"
)


def _store(tmp_path: Path, *, now: int = 100) -> GovernedChatStreamReceiptStore:
    path = tmp_path / "runtime.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(MIGRATION.read_text(encoding="utf-8"))
    return GovernedChatStreamReceiptStore(path, clock=lambda: now)


def _receipt() -> dict[str, object]:
    return {
        "schema_version": "Natsume.task_runtime.governed_chat_stream_receipt.v1",
        "graph_id": "graph-stream",
        "graph_revision": 1,
        "node_id": "node-chat",
        "attempt_id": "attempt-chat-1",
        "sentence_index": 0,
        "request_id": "request-stream",
        "principal_id": "person-stream",
        "workspace_id": "workspace-stream",
        "session_id": "session-stream",
        "affective_turn_plan_id": "affective-turn://stream",
        "synchronization_id": "sync-stream",
        "self_affect_revision": 9,
        "sentence": "先缓一下。",
        "node_grant_ref": "governor-grant://child/chat",
        "node_grant_digest": "sha256:" + "a" * 64,
    }


def test_stream_sentence_receipt_replays_exactly_across_restart(tmp_path: Path) -> None:
    store = _store(tmp_path, now=100)
    first = store.stage(receipt=_receipt())
    replay = GovernedChatStreamReceiptStore(store.db_path, clock=lambda: 999).stage(
        receipt=_receipt()
    )

    assert replay == first
    assert replay["staged_at_ms"] == 100
    assert replay["sentence"] == "先缓一下。"
    assert replay["payload_digest"].startswith("sha256:")
    assert store.list_for_attempt(
        graph_id="graph-stream",
        graph_revision=1,
        node_id="node-chat",
        attempt_id="attempt-chat-1",
    ) == (first,)


def test_stream_sentence_receipt_rejects_same_attempt_index_with_changed_text(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.stage(receipt=_receipt())
    drift = deepcopy(_receipt())
    drift["sentence"] = "我先安静陪你一会儿。"

    with pytest.raises(GovernedChatStreamReceiptStoreError) as raised:
        store.stage(receipt=drift)

    assert raised.value.reason_code == GOVERNED_CHAT_STREAM_RECEIPT_REPLAY_DRIFT


def test_stream_sentence_receipt_is_append_only(tmp_path: Path) -> None:
    store = _store(tmp_path)
    stored = store.stage(receipt=_receipt())

    with sqlite3.connect(store.db_path) as connection:
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute(
                "UPDATE task_runtime_governed_chat_stream_receipt SET sentence=? WHERE receipt_id=?",
                ("changed", stored["receipt_id"]),
            )
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute(
                "DELETE FROM task_runtime_governed_chat_stream_receipt WHERE receipt_id=?",
                (stored["receipt_id"],),
            )
