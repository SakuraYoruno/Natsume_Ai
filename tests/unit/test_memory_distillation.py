"""M10-C 璁板繂钂搁绯荤粺鍗曞厓娴嬭瘯銆?
T-Distill-01锛歞istill_top_k 姝ｅ父钂搁 鈥?鏈夋儏鑺傝蹇嗘椂搴旇繑鍥?semantic_id锛屽啓鍏?semantic_memory 琛ㄣ€?T-Distill-02锛氬箓绛夋鏌?鈥?鐩稿悓 source_memory_ids 缁勫悎閲嶅钂搁搴旇烦杩囷紙skipped=True锛夈€?T-Distill-03锛氭棤鎯呰妭璁板繂 鈥?绌?EpisodicStore 鏃?distill_top_k 搴旇繑鍥?success=False銆?T-Distill-04锛歭ist_semantic_memories 鍒嗛〉 鈥?鍐欏叆澶氭潯鍚庡垎椤垫煡璇㈡纭€?T-Distill-05锛歝ontext_pack semantic_slot 楠岃瘉 鈥?鍚堟硶 semantic_slot 閫氳繃楠岃瘉锛岀己瀛楁鏃舵姤閿欍€?T-Distill-06锛歜uild_semantic_slot 鈥?浠?ledger 鏋勫缓 semantic_slot 鍒楄〃鏍煎紡姝ｇ‘銆?"""

from __future__ import annotations

import pytest

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.memory.chroma_client import InMemoryChromaClient
from host_brain.memory.distillation import MemoryDistillationEngine
from host_brain.memory.episodic import EpisodicStore
from host_brain.core.prompt_context import (
    validate_prompt_context,
    build_semantic_slot,
    EXPECTED_SLOTS,
)


# ---------------------------------------------------------------------------
# 鍏变韩 fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def ledger(tmp_path):
    return SQLiteLedger(db_path=str(tmp_path / "test.sqlite3"))


@pytest.fixture()
def episodic(ledger):
    chroma = InMemoryChromaClient()
    return EpisodicStore(ledger=ledger, chroma_client=chroma)


@pytest.fixture()
def engine(episodic, ledger):
    return MemoryDistillationEngine(episodic_store=episodic, ledger=ledger)


def _write_memories(episodic: EpisodicStore, texts: list[str]) -> list[str]:
    """杈呭姪锛氭壒閲忓啓鍏ユ儏鑺傝蹇嗭紝杩斿洖 memory_id 鍒楄〃銆?""
    ids = []
    for text in texts:
        result = episodic.write_memory(
            text=text,
            evidence_refs=["test-evidence-001"],
            ts_ms=1000,
        )
        assert result.success
        ids.append(result.memory_id)
    return ids


# ---------------------------------------------------------------------------
# T-Distill-01锛氭甯歌捀棣?# ---------------------------------------------------------------------------

def test_distill_top_k_success(engine, episodic, ledger):
    """鏈夋儏鑺傝蹇嗘椂 distill_top_k 搴旇繑鍥?semantic_id锛屽啓鍏?semantic_memory 琛ㄣ€?""
    _write_memories(episodic, [
        "澶忕洰璐靛織鏄竴涓兘鐪嬭濡栨€殑灏戝勾",
        "鐚挭鑰佸笀鏄竴鍙己澶х殑濡栨€紝澶栧舰鍍忕尗",
        "澶忕洰缁ф壙浜嗙姣嶇幉瀛愮殑鍙嬩汉甯?,
    ])

    result = engine.distill_top_k(
        query_text="澶忕洰璐靛織",
        top_k=3,
        tags=["涓昏", "濡栨€?],
        ts_ms=2000,
    )

    assert result.success is True
    assert result.semantic_id is not None
    assert result.source_count > 0
    assert result.skipped is False

    # 楠岃瘉鍐欏叆浜?semantic_memory 琛?    items, total = ledger.list_semantic_memories()
    assert total == 1
    assert items[0]["semantic_id"] == result.semantic_id
    assert "澶忕洰璐靛織" in items[0]["summary"]
    assert items[0]["confidence"] >= 0.0


# ---------------------------------------------------------------------------
# T-Distill-02锛氬箓绛夋鏌?# ---------------------------------------------------------------------------

def test_distill_top_k_idempotent(engine, episodic, ledger):
    """鐩稿悓 source_memory_ids 缁勫悎閲嶅钂搁搴旇烦杩囷紙skipped=True锛夛紝涓嶉噸澶嶅啓鍏ャ€?""
    _write_memories(episodic, ["澶忕洰璐靛織鑳界湅瑙佸鎬?])

    result1 = engine.distill_top_k(query_text="澶忕洰", top_k=1, ts_ms=1000)
    assert result1.success is True
    assert result1.skipped is False

    # 绗簩娆＄浉鍚屾煡璇紙鍙洖鐩稿悓 source_ids锛?    result2 = engine.distill_top_k(query_text="澶忕洰", top_k=1, ts_ms=2000)
    assert result2.success is True
    assert result2.skipped is True
    assert result2.semantic_id == result1.semantic_id

    # 鏁版嵁搴撲腑鍙湁涓€鏉¤褰?    _, total = ledger.list_semantic_memories()
    assert total == 1


# ---------------------------------------------------------------------------
# T-Distill-03锛氭棤鎯呰妭璁板繂
# ---------------------------------------------------------------------------

def test_distill_top_k_empty_store(engine):
    """绌?EpisodicStore 鏃?distill_top_k 搴旇繑鍥?success=False銆?""
    result = engine.distill_top_k(query_text="澶忕洰璐靛織", top_k=5)
    assert result.success is False
    assert result.semantic_id is None
    assert result.source_count == 0


# ---------------------------------------------------------------------------
# T-Distill-04锛歭ist_semantic_memories 鍒嗛〉
# ---------------------------------------------------------------------------

def test_list_semantic_memories_pagination(engine, episodic, ledger):
    """鍐欏叆澶氭潯鍚庡垎椤垫煡璇㈡纭€?""
    # 鍐欏叆 3 鎵逛笉鍚岀殑鎯呰妭璁板繂锛岃捀棣?3 娆?    for i in range(3):
        _write_memories(episodic, [f"璁板繂鐗囨{i:03d}锛氬唴瀹规弿杩皗i}"])
        engine.distill_top_k(query_text=f"璁板繂{i:03d}", top_k=1, ts_ms=1000 + i * 100)

    # 鍏ㄩ噺鏌ヨ
    items_all, total = ledger.list_semantic_memories(limit=10, offset=0)
    assert total == 3
    assert len(items_all) == 3

    # 鍒嗛〉锛氭瘡椤?2 鏉?    items_p1, _ = ledger.list_semantic_memories(limit=2, offset=0)
    items_p2, _ = ledger.list_semantic_memories(limit=2, offset=2)
    assert len(items_p1) == 2
    assert len(items_p2) == 1


# ---------------------------------------------------------------------------
# T-Distill-05锛歝ontext_pack semantic_slot 楠岃瘉
# ---------------------------------------------------------------------------

def _make_valid_prompt_context(**overrides) -> dict:
    """鏋勯€犳渶灏忓悎娉?prompt_context銆?""
    base = {
        "hard_snapshot": {},
        "bond_summary": {},
        "authoritative": [],
        "retrieval": [],
        "style_pack": {},
        "constraints": {},
        "tool_meta_pack": [],
    }
    base.update(overrides)
    return base


def test_prompt_context_semantic_slot_valid():
    """鍚堟硶 semantic_slot 搴旈€氳繃楠岃瘉銆?""
    cp = _make_valid_prompt_context(
        semantic_slot=[
            {
                "slot": "semantic_slot",
                "semantic_id": "sem_abc123",
                "summary": "澶忕洰璐靛織鐨勮涔夋憳瑕?,
                "confidence": 0.85,
                "tags": ["涓昏"],
                "source_count": 3,
                "ts_ms": 1000,
            }
        ]
    )
    result = validate_prompt_context(cp)
    assert result.valid, f"搴旈€氳繃楠岃瘉锛屼絾鏈夎繚瑙? {result.violations}"


def test_prompt_context_semantic_slot_missing_required_field():
    """semantic_slot 鏉＄洰缂哄皯 semantic_id 鎴?summary 鏃跺簲鎶ラ敊銆?""
    cp = _make_valid_prompt_context(
        semantic_slot=[
            {"summary": "缂哄皯 semantic_id 鐨勬潯鐩?}  # 缂哄皯 semantic_id
        ]
    )
    result = validate_prompt_context(cp)
    assert not result.valid
    codes = [v.code for v in result.violations]
    assert "PROMPT_CONTEXT_TYPE_INVALID" in codes


def test_prompt_context_without_semantic_slot_still_valid():
    """涓嶅寘鍚?semantic_slot 鐨?prompt_context 搴斾粛鐒堕€氳繃楠岃瘉锛堝彲閫夋Ы浣嶏級銆?""
    cp = _make_valid_prompt_context()
    result = validate_prompt_context(cp)
    assert result.valid, f"涓嶅惈 semantic_slot 搴旈€氳繃楠岃瘉锛屼絾鏈夎繚瑙? {result.violations}"


# ---------------------------------------------------------------------------
# T-Distill-06锛歜uild_semantic_slot
# ---------------------------------------------------------------------------

def test_build_semantic_slot(engine, episodic, ledger):
    """build_semantic_slot 搴斾粠 ledger 鏋勫缓鏍煎紡姝ｇ‘鐨?semantic_slot 鍒楄〃銆?""
    _write_memories(episodic, ["澶忕洰璐靛織鐨勬晠浜?])
    engine.distill_top_k(query_text="澶忕洰", top_k=1, ts_ms=1000)

    slot = build_semantic_slot(ledger=ledger, top_k=5)
    assert isinstance(slot, list)
    assert len(slot) == 1

    entry = slot[0]
    assert entry["slot"] == "semantic_slot"
    assert "semantic_id" in entry
    assert "summary" in entry
    assert "confidence" in entry
    assert isinstance(entry["tags"], list)
    assert isinstance(entry["source_count"], int)


def test_distill_top_k_concurrent_idempotent(tmp_path):
    """F-41: concurrent distillation with same source_hash should persist exactly once."""
    from concurrent.futures import ThreadPoolExecutor
    from unittest.mock import MagicMock

    from host_brain.audit.sqlite_ledger import SQLiteLedger
    from host_brain.memory.distillation import MemoryDistillationEngine

    ledger = SQLiteLedger(db_path=str(tmp_path / "concurrent.sqlite3"), check_same_thread=False)
    episodic = MagicMock()
    episodic.recall.return_value = [
        {"memory_id": "mem_a", "text": "A", "score": 0.9},
        {"memory_id": "mem_b", "text": "B", "score": 0.8},
    ]
    engine = MemoryDistillationEngine(episodic_store=episodic, ledger=ledger)

    def _run_once() -> str | None:
        result = engine.distill_top_k(query_text="same query", top_k=2)
        assert result.success is True
        return result.semantic_id

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _idx: _run_once(), range(20)))

        items, total = ledger.list_semantic_memories(limit=10, offset=0)
        assert total == 1
        persisted_id = items[0]["semantic_id"]
        assert all(sid == persisted_id for sid in results)
    finally:
        ledger.close()

