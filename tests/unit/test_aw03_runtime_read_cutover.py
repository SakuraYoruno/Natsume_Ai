from __future__ import annotations

import json
import logging
from pathlib import Path
import re
import shutil
import sqlite3
from types import SimpleNamespace

import pytest

from host_brain.lifecycle_runtime.scheduler_handlers import _fetch_ledger_rows_by_time
from host_brain.lifecycle_runtime.life_jobs import (
    _load_last_interaction_ts,
    _load_recent_dream_snippet,
    _load_recent_memory_write_topics,
    _load_recent_topics,
)
from host_brain.app.settings import PersistenceSettings
from host_brain.surface_runtime.quality_guards import (
    _load_recent_surface_dream_text,
    _surface_recent_conversation_window_facts,
)
from host_brain.surface_runtime.life_context import _surface_recent_user_fact_texts
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.runtime_read_repository import (
    _json_mapping,
    _surface_artifact_session_id,
)


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
        "host_brain/surface_runtime/life_context.py",
        "host_brain/surface_runtime/quality_guards.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        lowered = source.lower()
        assert "sqlite3" not in lowered
        assert "_db_path" not in source
        assert "._conn" not in source
        assert ".execute(" not in source
        assert "create table" not in lowered
        assert not re.search(r"\bselect\s+.+\s+from\b", lowered)
    surface_read_sources = "\n".join(
        (ROOT / relative).read_text(encoding="utf-8")
        for relative in (
            "host_brain/surface_runtime/life_context.py",
            "host_brain/surface_runtime/quality_guards.py",
        )
    )
    assert "RuntimeReadRepository" in surface_read_sources


def test_surface_fact_and_request_window_reads_use_typed_persistence_settings(
    runtime_db: Path,
    surface_runtime_ports_factory,
) -> None:
    settings = PersistenceSettings(canonical_db_path=str(runtime_db))
    ports = surface_runtime_ports_factory(
        get_persistence_settings=lambda: settings,
    )
    agent_input = SimpleNamespace(
        person_id="person",
        session_id="session",
        request_id="current",
    )

    assert _surface_recent_user_fact_texts(agent_input, ports=ports) == [
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
    surface_runtime_ports_factory,
) -> None:
    ledger = SimpleNamespace(canonical_db_path=str(runtime_db))
    ports = surface_runtime_ports_factory(get_ledger=lambda: ledger)

    facts = _surface_recent_conversation_window_facts(
        SimpleNamespace(session_id="session", request_id="current"),
        ports=ports,
    )
    assert "用户: 项目事实：alpha" in facts[1]
    assert "Natsume(主动消息): 主动承接" in facts[1]
    assert "记住 beta" not in facts[1]
    assert _load_recent_surface_dream_text(ports=ports) == "夜车和海风"

    missing_ports = surface_runtime_ports_factory(
        get_ledger=lambda: SimpleNamespace(
            canonical_db_path=str(runtime_db.parent / "missing.sqlite3")
        ),
    )
    assert _surface_recent_conversation_window_facts(
        SimpleNamespace(session_id="session", request_id="current"),
        ports=missing_ports,
    ) == ()
    assert _load_recent_surface_dream_text(ports=missing_ports) == ""


def test_surface_window_includes_committed_reply_before_conversation_projection(
    surface_runtime_ports_factory,
) -> None:
    prior_reply = "好。你说想安静时，我就不追问，也不主动找你。"
    artifact_payload = {
        "decision": "APPROVE",
        "request_id": "prior-assistant",
        "narrative": prior_reply,
        "acceptance_effect_intents": [
            {
                "payload": {
                    "payload": {
                        "affective_turn_plan": {"session_id": "fast-session"}
                    }
                }
            }
        ],
    }
    ledger = SimpleNamespace(
        conversation_turn=[
            {
                "request_id": "prior-user",
                "session_id": "fast-session",
                "role": "user",
                "content": "我说想安静时，请先别追问，也别主动找我。",
                "ts_ms": 100,
                "turn_index": 1,
                "metadata": "{}",
            }
        ],
        task_runtime_node_artifact=[
            {
                "graph_id": "graph-fast",
                "graph_revision": 1,
                "node_id": "node-fast",
                "payload_json": json.dumps(artifact_payload, ensure_ascii=False),
                "created_ts_ms": 150,
            }
        ],
        task_runtime_acceptance_effect_intent=[
            {
                "graph_id": "graph-fast",
                "graph_revision": 1,
                "node_id": "node-fast",
                "effect_type": "surface.reply_delivery",
                "status": "COMMITTED",
                "committed_ts_ms": 180,
                "updated_ts_ms": 180,
            }
        ],
    )
    ports = surface_runtime_ports_factory(get_ledger=lambda: ledger)

    facts = _surface_recent_conversation_window_facts(
        SimpleNamespace(session_id="fast-session", request_id="current"),
        ports=ports,
    )

    assert prior_reply in facts[1]


def test_surface_window_filters_noncommitted_or_unusable_artifacts(
    surface_runtime_ports_factory,
) -> None:
    def payload(
        *,
        decision: str = "APPROVE",
        request_id: str = "prior",
        session_id: str = "target-session",
        narrative: str = "可用回复",
    ) -> str:
        return json.dumps(
            {
                "decision": decision,
                "request_id": request_id,
                "narrative": narrative,
                "acceptance_effect_intents": [
                    {
                        "payload": {
                            "payload": {
                                "affective_turn_plan": {"session_id": session_id}
                            }
                        }
                    }
                ],
            },
            ensure_ascii=False,
        )

    ledger = SimpleNamespace(
        conversation_turn=[
            {
                "request_id": "prior-user",
                "session_id": "target-session",
                "role": "user",
                "content": "上一条用户消息",
                "ts_ms": 50,
                "turn_index": 1,
                "metadata": "{}",
            }
        ],
        task_runtime_acceptance_effect_intent=[
            {
                "graph_id": "graph",
                "graph_revision": 1,
                "node_id": node_id,
                "effect_type": "surface.reply_delivery",
                "status": "COMMITTED",
                "committed_ts_ms": 100 + index,
                "updated_ts_ms": 100 + index,
            }
            for index, node_id in enumerate(
                ("rejected", "current", "foreign", "empty", "valid")
            )
        ],
        task_runtime_node_artifact=[
            {
                "graph_id": "uncommitted",
                "graph_revision": 1,
                "node_id": "missing-effect",
                "payload_json": payload(),
                "created_ts_ms": 1,
            },
            {
                "graph_id": "graph",
                "graph_revision": 1,
                "node_id": "rejected",
                "payload_json": payload(decision="REJECT"),
                "created_ts_ms": 2,
            },
            {
                "graph_id": "graph",
                "graph_revision": 1,
                "node_id": "current",
                "payload_json": payload(request_id="current"),
                "created_ts_ms": 3,
            },
            {
                "graph_id": "graph",
                "graph_revision": 1,
                "node_id": "foreign",
                "payload_json": payload(session_id="other-session"),
                "created_ts_ms": 4,
            },
            {
                "graph_id": "graph",
                "graph_revision": 1,
                "node_id": "empty",
                "payload_json": payload(narrative=""),
                "created_ts_ms": 5,
            },
            {
                "graph_id": "graph",
                "graph_revision": 1,
                "node_id": "valid",
                "payload_json": payload(narrative="唯一可用回复"),
                "created_ts_ms": 6,
            },
        ],
    )
    ports = surface_runtime_ports_factory(get_ledger=lambda: ledger)

    facts = _surface_recent_conversation_window_facts(
        SimpleNamespace(session_id="target-session", request_id="current"),
        ports=ports,
    )

    assert facts[1].endswith("Natsume: 唯一可用回复")


def test_runtime_read_json_and_surface_session_helpers_fail_soft() -> None:
    assert _json_mapping({"value": 1}) == {"value": 1}
    assert _json_mapping("not-json") == {}
    assert _json_mapping("[]") == {}
    assert _surface_artifact_session_id({}) == ""


def test_surface_window_keeps_logical_turn_order_when_projection_timestamps_lag(
    surface_runtime_ports_factory,
) -> None:
    artifact_payload = {
        "decision": "APPROVE",
        "request_id": "request-2",
        "narrative": "第二条回复",
        "acceptance_effect_intents": [
            {
                "payload": {
                    "payload": {
                        "affective_turn_plan": {"session_id": "ordered-session"}
                    }
                }
            }
        ],
    }
    ledger = SimpleNamespace(
        conversation_turn=[
            {
                "request_id": "request-1",
                "session_id": "ordered-session",
                "role": "user",
                "content": "第一条用户消息",
                "ts_ms": 100,
                "turn_index": 0,
                "metadata": "{}",
            },
            {
                "request_id": "request-1",
                "session_id": "ordered-session",
                "role": "assistant",
                "content": "第一条回复",
                "ts_ms": 300,
                "turn_index": 1,
                "metadata": "{}",
            },
            {
                "request_id": "request-2",
                "session_id": "ordered-session",
                "role": "user",
                "content": "第二条用户消息",
                "ts_ms": 200,
                "turn_index": 2,
                "metadata": "{}",
            },
        ],
        task_runtime_node_artifact=[
            {
                "graph_id": "graph-2",
                "graph_revision": 1,
                "node_id": "node-2",
                "payload_json": json.dumps(artifact_payload, ensure_ascii=False),
                "created_ts_ms": 230,
            }
        ],
        task_runtime_acceptance_effect_intent=[
            {
                "graph_id": "graph-2",
                "graph_revision": 1,
                "node_id": "node-2",
                "effect_type": "surface.reply_delivery",
                "status": "COMMITTED",
                "committed_ts_ms": 250,
                "updated_ts_ms": 250,
            }
        ],
    )
    ports = surface_runtime_ports_factory(get_ledger=lambda: ledger)

    facts = _surface_recent_conversation_window_facts(
        SimpleNamespace(session_id="ordered-session", request_id="current"),
        ports=ports,
    )

    assert facts[1].endswith(
        "用户: 第一条用户消息 / Natsume: 第一条回复 / "
        "用户: 第二条用户消息 / Natsume: 第二条回复"
    )


def test_surface_window_deduplicates_committed_reply_after_projection_catches_up(
    surface_runtime_ports_factory,
) -> None:
    prior_reply = "好，我不追问，也不主动发消息。"
    artifact_payload = {
        "decision": "APPROVE",
        "request_id": "same-request",
        "narrative": prior_reply,
        "acceptance_effect_intents": [
            {
                "payload": {
                    "payload": {
                        "affective_turn_plan": {"session_id": "dedupe-session"}
                    }
                }
            }
        ],
    }
    ledger = SimpleNamespace(
        conversation_turn=[
            {
                "request_id": "prior-user",
                "session_id": "dedupe-session",
                "role": "user",
                "content": "今晚别问我。",
                "ts_ms": 100,
                "turn_index": 1,
                "metadata": "{}",
            },
            {
                "request_id": "same-request",
                "session_id": "dedupe-session",
                "role": "assistant",
                "content": prior_reply,
                "ts_ms": 200,
                "turn_index": 2,
                "metadata": "{}",
            },
        ],
        task_runtime_node_artifact=[
            {
                "graph_id": "graph-dedupe",
                "graph_revision": 1,
                "node_id": "node-dedupe",
                "payload_json": json.dumps(artifact_payload, ensure_ascii=False),
                "created_ts_ms": 150,
            }
        ],
        task_runtime_acceptance_effect_intent=[
            {
                "graph_id": "graph-dedupe",
                "graph_revision": 1,
                "node_id": "node-dedupe",
                "effect_type": "surface.reply_delivery",
                "status": "COMMITTED",
                "committed_ts_ms": 180,
                "updated_ts_ms": 180,
            }
        ],
    )
    ports = surface_runtime_ports_factory(get_ledger=lambda: ledger)

    facts = _surface_recent_conversation_window_facts(
        SimpleNamespace(session_id="dedupe-session", request_id="current"),
        ports=ports,
    )

    assert facts[1].count(prior_reply) == 1


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
