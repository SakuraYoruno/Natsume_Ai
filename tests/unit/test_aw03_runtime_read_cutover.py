from __future__ import annotations

import logging
from pathlib import Path
import re
import shutil
import sqlite3
from types import SimpleNamespace

import pytest

from host_brain.app.http_hooks_runtime_helpers import (
    _fetch_ledger_rows_by_time,
    _load_last_interaction_ts,
    _load_recent_dream_snippet,
    _load_recent_memory_write_topics,
    _load_recent_topics,
)
from host_brain.app.settings import PersistenceSettings
from host_brain.app.surface_main_chain_adapter import (
    _load_recent_surface_dream_text,
    _surface_recent_conversation_window_facts,
    _surface_recent_user_fact_texts,
)
from host_brain.audit.migration_runner import SQLiteMigrationRunner


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def runtime_schema_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    db_path = tmp_path_factory.mktemp("runtime-read-cutover") / "template.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="aw03-runtime-read-unit",
        ts_ms=1_800_000_000_000,
    )
    assert result.success is True, result.as_dict()
    return db_path


@pytest.fixture
def runtime_db(runtime_schema_template: Path, tmp_path: Path) -> Path:
    db_path = tmp_path / "runtime.sqlite3"
    shutil.copyfile(runtime_schema_template, db_path)
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO conversation_turn(
                turn_id, session_id, person_id, request_id, role, content,
                ts_ms, turn_index, lang, corrupted, metadata, created_at_ms
            ) VALUES (?, 'session', 'person', ?, ?, ?, ?, ?, 'zh', 0, ?, ?)
            """,
            [
                ("turn-1", "old", "user", "项目事实：alpha", 100, 1, "{}", 100),
                ("turn-2", "old", "assistant", "主动承接", 110, 2, '{"source_kind":"life_proactive"}', 110),
                ("turn-3", "current", "user", "记住 beta", 200, 3, "{}", 200),
                ("turn-4", "later", "user", "测试事实：future", 300, 4, "{}", 300),
            ],
        )
        conn.executemany(
            """
            INSERT INTO memory_ops_log(
                request_id, op_kind, memory_id, evidence_refs, status, detail, ts_ms
            ) VALUES (?, 'WRITE', ?, ?, 'SUCCESS', ?, ?)
            """,
            [
                ("memory", "memory-1", '["tag:fact"]', "memory written: recent", 500),
                ("dream", None, '["tag:dream","source:dream_generator"]', "memory written: 夜车和海风", 600),
            ],
        )
        conn.executemany(
            """
            INSERT INTO intent_log(request_id, ts_ms, raw_text, normalized_json)
            VALUES (?, ?, ?, ?)
            """,
            [
                ("intent-1", 100, "alpha raw", '{"topic":"alpha"}'),
                ("intent-2", 200, "beta raw", '{"topic":"beta"}'),
            ],
        )
        conn.execute(
            """
            INSERT INTO channel_io_log(request_id, direction, channel, detail, ts_ms)
            VALUES ('channel-1', 'IN', 'wechat', 'hello', 150)
            """
        )
        conn.commit()
    return db_path


def test_app_giants_have_no_sqlite_connection_private_path_or_raw_sql() -> None:
    for relative in (
        "host_brain/app/surface_main_chain_adapter.py",
        "host_brain/app/http_hooks_runtime_helpers.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        lowered = source.lower()
        assert "sqlite3" not in lowered
        assert "_db_path" not in source
        assert "._conn" not in source
        assert ".execute(" not in source
        assert "create table" not in lowered
        assert not re.search(r"\bselect\s+.+\s+from\b", lowered)
        assert "RuntimeReadRepository" in source


def test_surface_fact_and_request_window_reads_use_typed_persistence_settings(
    runtime_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = PersistenceSettings(canonical_db_path=str(runtime_db))
    monkeypatch.setattr(
        "host_brain.app.surface_main_chain_adapter.get_persistence_settings",
        lambda: settings,
    )
    agent_input = SimpleNamespace(
        person_id="person",
        session_id="session",
        request_id="current",
    )

    assert _surface_recent_user_fact_texts(agent_input) == [
        "用户事实：项目事实：alpha",
        "用户事实：记住 beta",
    ]


def test_legacy_audit_path_env_is_normalized_by_typed_settings(
    runtime_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "NATSUME_AUDIT_DB",
        "HOST_AUDIT_SQLITE_PATH",
        "HOST_AUDIT_DB_PATH",
        "AUDIT_DB_PATH",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOST_AUDIT_SQLITE_PATH", str(runtime_db))
    assert PersistenceSettings(_env_file=None).canonical_db_path == str(runtime_db)


def test_surface_conversation_and_dream_reads_remain_fail_soft(
    runtime_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = SimpleNamespace(canonical_db_path=str(runtime_db))
    monkeypatch.setattr("host_brain.app.dependencies.get_ledger", lambda: ledger)

    facts = _surface_recent_conversation_window_facts(
        SimpleNamespace(session_id="session", request_id="current")
    )
    assert "用户: 项目事实：alpha" in facts[1]
    assert "Natsume(主动消息): 主动承接" in facts[1]
    assert "记住 beta" not in facts[1]
    assert _load_recent_surface_dream_text() == "夜车和海风"

    monkeypatch.setattr(
        "host_brain.app.dependencies.get_ledger",
        lambda: SimpleNamespace(canonical_db_path=str(runtime_db.parent / "missing.sqlite3")),
    )
    assert _surface_recent_conversation_window_facts(
        SimpleNamespace(session_id="session", request_id="current")
    ) == ()
    assert _load_recent_surface_dream_text() == ""


def test_http_hook_semantic_wrappers_keep_existing_api(runtime_db: Path) -> None:
    get_ledger = lambda: SimpleNamespace(canonical_db_path=str(runtime_db))
    logger = logging.getLogger("tests.runtime-read")

    assert _load_recent_topics(get_ledger=get_ledger, limit=2) == ["beta", "alpha"]
    assert _load_last_interaction_ts(
        get_ledger=get_ledger,
        now_ts_ms=150,
        max_future_skew_ms=0,
    ) == 100
    assert _load_recent_memory_write_topics(
        get_ledger=get_ledger,
        now_ts_ms=1_000,
        limit=5,
    ) == ["memory written: 夜车和海风", "memory written: recent"]
    assert "夜车和海风" in _load_recent_dream_snippet(get_ledger=get_ledger)
    assert _fetch_ledger_rows_by_time(
        get_ledger=get_ledger,
        table="channel_io_log",
        start_ts=0,
        end_ts=200,
        limit=5,
        logger=logger,
    )[0]["detail"] == "hello"

    missing = lambda: SimpleNamespace(
        canonical_db_path=str(runtime_db.parent / "missing.sqlite3")
    )
    assert _load_recent_topics(get_ledger=missing) == []
    assert _load_last_interaction_ts(get_ledger=missing) is None
    assert _load_recent_dream_snippet(get_ledger=missing) == ""
