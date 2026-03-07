from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.memory.canon_style_store import StyleCanonStore
from host_brain.memory.canon_vector_auto_sync import trigger_canon_vector_auto_sync
from host_brain.memory.canon_world_store import WorldCanonStore


def test_auto_sync_disabled_is_noop(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("CANON_VECTOR_AUTO_SYNC_ENABLED", raising=False)
    with patch("host_brain.memory.canon_vector_projection.sync_canon_vector_index") as sync_mock:
        trigger_canon_vector_auto_sync(
            include_style=True,
            include_world=False,
            style_root=tmp_path / "style",
            world_root=tmp_path / "world",
            reason="test_disabled",
        )
    sync_mock.assert_not_called()


def test_auto_sync_runs_in_sync_mode(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CANON_VECTOR_AUTO_SYNC_ENABLED", "true")
    monkeypatch.setenv("CANON_VECTOR_AUTO_SYNC_MODE", "sync")
    with patch(
        "host_brain.memory.canon_vector_projection.sync_canon_vector_index",
        return_value={"docs_synced": 1, "issues_count": 0},
    ) as sync_mock:
        trigger_canon_vector_auto_sync(
            include_style=True,
            include_world=False,
            style_root=tmp_path / "style",
            world_root=tmp_path / "world",
            reason="test_sync_mode",
        )
    sync_mock.assert_called_once()
    kwargs = sync_mock.call_args.kwargs
    assert kwargs["include_style"] is True
    assert kwargs["include_world"] is False


def test_style_store_upsert_triggers_auto_sync(tmp_path: Path) -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = StyleCanonStore(style_root=tmp_path / "style", ledger=ledger)
        with patch("host_brain.memory.canon_style_store.trigger_canon_vector_auto_sync") as trigger_mock:
            store.upsert(
                canon_id="natsume_tone",
                content="gentle supportive concise tone",
                tags=["style"],
                proposal_id="proposal_style_auto_sync",
            )
        trigger_mock.assert_called_once()
        kwargs = trigger_mock.call_args.kwargs
        assert kwargs["include_style"] is True
        assert kwargs["include_world"] is False
    finally:
        ledger.close()


def test_world_store_upsert_triggers_auto_sync(tmp_path: Path) -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = WorldCanonStore(world_root=tmp_path / "world", ledger=ledger)
        with patch("host_brain.memory.canon_world_store.trigger_canon_vector_auto_sync") as trigger_mock:
            store.upsert_document(
                doc_id="academy_rules",
                title="Academy Rules",
                tags=["world"],
                body="Practice sessions use registered lab stations only.",
                proposal_id="proposal_world_auto_sync",
            )
        trigger_mock.assert_called_once()
        kwargs = trigger_mock.call_args.kwargs
        assert kwargs["include_style"] is False
        assert kwargs["include_world"] is True
    finally:
        ledger.close()
