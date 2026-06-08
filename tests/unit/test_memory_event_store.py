from __future__ import annotations

import json

import pytest

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.app.context.memory_context_orchestrator import build_memory_context_debug
from host_brain.app.memory_event_store import MemoryEventStore, memory_event_health, rebuild_memory_system
from host_brain.conversation import TurnStore
from host_brain.conversation.writer import record_assistant_turn, record_user_turn
from host_brain.runtime.life_continuity import record_life_experience, record_work_environment_event


def _ledger() -> SQLiteLedger:
    return SQLiteLedger(":memory:", check_same_thread=False)


def test_visible_user_turn_writes_memory_event_and_projections(monkeypatch) -> None:
    ledger = _ledger()
    store = TurnStore(ledger)
    monkeypatch.setattr("host_brain.app.dependencies._conversation_turn_store", store, raising=False)

    result = record_user_turn(
        request_id="req-user",
        session_id="qq_dm_test",
        person_id="default",
        content="怎么了",
        ts_ms=1000,
        lang="zh",
    )

    assert result["status"] == "OK"
    memory_event_id = result["memory_event_id"]
    event = ledger._conn.execute("SELECT * FROM memory_event WHERE event_id=?", (memory_event_id,)).fetchone()
    assert event["event_kind"] == "user_message"
    assert event["visibility"] == "visible"
    turn = ledger._conn.execute("SELECT * FROM conversation_turn WHERE request_id='req-user'").fetchone()
    assert json.loads(turn["metadata"])["memory_event_id"] == memory_event_id
    life = ledger._conn.execute("SELECT * FROM life_experience_event WHERE json_extract(evidence_json, '$.memory_event_id')=?", (memory_event_id,)).fetchone()
    assert life["event_kind"] == "user_message"


def test_life_proactive_visible_event_binds_context_question(monkeypatch) -> None:
    ledger = _ledger()
    store = TurnStore(ledger)
    monkeypatch.setattr("host_brain.app.dependencies._conversation_turn_store", store, raising=False)

    result = record_assistant_turn(
        request_id="lifepro_1",
        session_id="qq_dm_test",
        person_id="default",
        content="你安静了一会儿，我轻轻冒个泡。",
        ts_ms=1000,
        lang="zh",
        metadata={"source_kind": "life_proactive", "proactive_outbox_id": "lifepro_1", "proactive_episode_id": "lifeproep_1"},
    )

    debug = build_memory_context_debug(
        ledger=ledger,
        session_id="qq_dm_test",
        person_id="default",
        user_message="怎么了",
    )

    assert result["status"] == "OK"
    assert debug["binding_decision"]["bound_event_id"] == result["memory_event_id"]
    assert debug["binding_decision"]["reason_code"] == "BOUND_RECENT_VISIBLE_ASSISTANT_EVENT"


def test_private_life_experience_writes_memory_event() -> None:
    ledger = _ledger()

    result = record_life_experience(
        ledger=ledger,
        person_id="default",
        session_id="qq_dm_test",
        source_kind="inner_stream",
        event_kind="inner_stream_tick",
        content_summary="她刚才有一点走神。",
        evidence={"focus": "idle"},
        consume=True,
    )

    event = ledger._conn.execute("SELECT * FROM memory_event WHERE event_id=?", (result["memory_event_id"],)).fetchone()
    assert event["visibility"] == "private"
    assert event["event_kind"] == "inner_stream_tick"
    assert json.loads(event["refs_json"])["life_experience_event_id"] == result["event_id"]


@pytest.mark.asyncio
async def test_work_event_writes_memory_event_and_life_projection() -> None:
    ledger = _ledger()

    result = await record_work_environment_event(
        ledger=ledger,
        event_type="task_started",
        person_id="default",
        session_id="qq_dm_test",
        request_id="work_req",
        work_id="work_1",
        summary="开始修复记忆系统",
        urgency="high",
    )

    row = ledger._conn.execute("SELECT * FROM memory_event WHERE event_kind='work_event' ORDER BY id DESC LIMIT 1").fetchone()
    assert row["visibility"] == "system"
    assert json.loads(row["refs_json"])["work_id"] == "work_1"
    life = ledger._conn.execute("SELECT * FROM life_experience_event WHERE json_extract(evidence_json, '$.memory_event_id')=?", (row["event_id"],)).fetchone()
    assert life["event_kind"] == "work_event"
    assert result["event_id"]


def test_memory_rebuild_clears_fragments_and_keeps_epoch_event(monkeypatch) -> None:
    ledger = _ledger()
    store = TurnStore(ledger)
    monkeypatch.setattr("host_brain.app.dependencies._conversation_turn_store", store, raising=False)
    MemoryEventStore(ledger).append_visible_turn(
        request_id="req",
        session_id="qq_dm_test",
        person_id="default",
        role="user",
        content="你好",
        ts_ms=1000,
    )

    result = rebuild_memory_system(ledger=ledger, person_id="default")

    assert result["epoch_event_id"]
    assert ledger._conn.execute("SELECT COUNT(*) FROM conversation_turn").fetchone()[0] == 0
    rows = ledger._conn.execute("SELECT event_kind FROM memory_event").fetchall()
    assert [row["event_kind"] for row in rows] == ["memory_system_rebuilt"]
    assert all(value == 0 for value in memory_event_health(ledger=ledger, person_id="default").values())
