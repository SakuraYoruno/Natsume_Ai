from __future__ import annotations

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.memory.episodic import MEMORY_DUPLICATE_EVENT, EpisodicStore
from tests.fakes.chroma_client import FakeChromaClient


def _make_store(tmp_path):
    ledger = SQLiteLedger(db_path=str(tmp_path / "audit.sqlite3"), check_same_thread=False)
    chroma = FakeChromaClient()
    store = EpisodicStore(ledger=ledger, chroma_client=chroma)
    return store, ledger, chroma


def test_same_chat_request_maps_to_one_episode_memory(tmp_path) -> None:
    store, ledger, chroma = _make_store(tmp_path)

    first = store.write_memory(
        text="当前回合摘要：用户问这几本书怎么样。",
        evidence_refs=[
            "chat://request/req-unique-001",
            "chat://session/qq_dm_1",
            "chat://person/default",
        ],
        request_id="req-unique-001",
        ts_ms=1000,
    )
    second = store.write_memory(
        text="另一处重复写入：用户问这几本书怎么样。",
        evidence_refs=[
            "chat://request/req-unique-001",
            "surface://reply/req-unique-001",
            "chat://session/qq_dm_1",
            "chat://person/default",
        ],
        request_id="req-unique-001",
        ts_ms=1001,
    )

    assert first.success is True
    assert second.success is True
    assert first.memory_id == second.memory_id
    assert second.reason_code == MEMORY_DUPLICATE_EVENT
    assert len(store._records) == 1
    assert chroma.get(str(first.memory_id)) is not None

    rows = ledger.memory_ops_log
    assert [row["status"] for row in rows] == ["SUCCESS", "SKIPPED"]
    assert rows[1]["reason_code"] == MEMORY_DUPLICATE_EVENT


def test_non_episode_evidence_still_allows_distinct_memories(tmp_path) -> None:
    store, _ledger, _chroma = _make_store(tmp_path)

    first = store.write_memory(text="片段 A", evidence_refs=["test-evidence-001"], ts_ms=1000)
    second = store.write_memory(text="片段 B", evidence_refs=["test-evidence-001"], ts_ms=1001)

    assert first.success is True
    assert second.success is True
    assert first.memory_id != second.memory_id
    assert len(store._records) == 2


def test_episode_memory_id_is_stable_across_store_instances(tmp_path) -> None:
    ledger = SQLiteLedger(db_path=str(tmp_path / "audit.sqlite3"), check_same_thread=False)
    chroma = FakeChromaClient()
    first_store = EpisodicStore(ledger=ledger, chroma_client=chroma)

    first = first_store.write_memory(
        text="后台任务失败：模型池不可用。",
        evidence_refs=["scheduler://event/sched_evt_001", "scheduler://task/daily_recommendation"],
        request_id="sched_evt_001",
        ts_ms=1000,
    )
    second_store = EpisodicStore(ledger=ledger, chroma_client=chroma)
    second = second_store.write_memory(
        text="重复的后台任务失败记录。",
        evidence_refs=["scheduler://event/sched_evt_001", "scheduler://task/daily_recommendation"],
        request_id="sched_evt_001",
        ts_ms=1001,
    )

    assert first.success is True
    assert second.success is True
    assert first.memory_id == second.memory_id
    assert second.reason_code == MEMORY_DUPLICATE_EVENT
    assert chroma.get(str(first.memory_id)) is not None
