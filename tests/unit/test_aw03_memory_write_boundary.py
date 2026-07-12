from __future__ import annotations

import ast
from pathlib import Path

import pytest

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.memory.life_memory.store import (
    LIFE_MEMORY_DIRECT_CHAT_WRITE_FORBIDDEN,
    LifeMemoryStore,
)


ROOT = Path(__file__).resolve().parents[2]
CHAT_PRODUCTION_FILES = (
    ROOT / "host_brain/app/agent_runtime_shared.py",
    ROOT / "host_brain/app/chat_handler_vision.py",
    ROOT / "host_brain/app/chat_handler.py",
    ROOT / "host_brain/persona/surface_memory_recall.py",
)


def test_chat_production_has_no_legacy_memory_writer_or_helper() -> None:
    forbidden_symbols = {
        "persist_life_chat_episode",
        "persist_chat_episodic_memory",
        "write_surface_turn_memory",
        "_write_surface_main_chain_memory_sync",
    }
    for path in CHAT_PRODUCTION_FILES:
        source = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(source, filename=str(path))
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        attributes = {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        assert not forbidden_symbols.intersection(names | attributes), path
        assert "get_episodic_store" not in names, path
        if path.name in {"agent_runtime_shared.py", "chat_handler_vision.py"}:
            assert source.count(".admit_chat") == 1, path


def test_observability_module_no_longer_defines_chat_memory_writer() -> None:
    path = ROOT / "host_brain/app/observability_persist.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    definitions = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "persist_life_chat_episode" not in definitions
    assert "persist_chat_episodic_memory" not in definitions


def test_repository_owns_connection_without_ledger_private_connection() -> None:
    path = ROOT / "host_brain/persistence/life_memory_repository.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    private_attributes = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in {"_conn", "_write_lock"}
    }
    assert private_attributes == set()
    assert "CREATE TABLE" not in source
    assert "ALTER TABLE" not in source


def test_legacy_store_chat_writer_fails_closed_for_durable_database(tmp_path: Path) -> None:
    ledger = SQLiteLedger(db_path=tmp_path / "legacy-life.sqlite3")
    try:
        store = LifeMemoryStore(ledger=ledger)
        with pytest.raises(PermissionError, match=LIFE_MEMORY_DIRECT_CHAT_WRITE_FORBIDDEN):
            store.write_chat_episode(
                request_id="direct-write",
                session_id="session",
                person_id="owner",
                user_message="remember this",
                assistant_reply="ok",
                ts_ms=1,
            )
    finally:
        ledger.close()
