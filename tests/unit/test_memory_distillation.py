"""M10-C 记忆蒸馏系统单元测试。

T-Distill-01：distill_top_k 正常蒸馏 — 有情节记忆时应返回 semantic_id，写入 semantic_memory 表。
T-Distill-02：幂等检查 — 相同 source_memory_ids 组合重复蒸馏应跳过（skipped=True）。
T-Distill-03：无情节记忆 — 空 EpisodicStore 时 distill_top_k 应返回 success=False。
T-Distill-04：list_semantic_memories 分页 — 写入多条后分页查询正确。
T-Distill-05：context_pack semantic_slot 验证 — 合法 semantic_slot 通过验证，缺字段时报错。
T-Distill-06：build_semantic_slot — 从 ledger 构建 semantic_slot 列表格式正确。
"""

from __future__ import annotations

import pytest

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.memory.distillation import MemoryDistillationEngine
from host_brain.memory.episodic import EpisodicStore
from host_brain.core.prompt_context import (
    validate_prompt_context,
    build_semantic_slot,
    EXPECTED_SLOTS,
)
from tests.fakes.chroma_client import FakeChromaClient


# ---------------------------------------------------------------------------
# 共享 fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def ledger(tmp_path):
    return SQLiteLedger(db_path=str(tmp_path / "test.sqlite3"))


@pytest.fixture()
def episodic(ledger):
    chroma = FakeChromaClient()
    return EpisodicStore(ledger=ledger, chroma_client=chroma)


@pytest.fixture()
def engine(episodic, ledger):
    return MemoryDistillationEngine(episodic_store=episodic, ledger=ledger)


def _write_memories(episodic: EpisodicStore, texts: list[str]) -> list[str]:
    """辅助：批量写入情节记忆，返回 memory_id 列表。"""
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
# T-Distill-01：正常蒸馏
# ---------------------------------------------------------------------------

def test_distill_top_k_success(engine, episodic, ledger):
    """有情节记忆时 distill_top_k 应返回 semantic_id，写入 semantic_memory 表。"""
    _write_memories(episodic, [
        "夏目贵志是一个能看见妖怪的少年",
        "猫咪老师是一只强大的妖怪，外形像猫",
        "夏目继承了祖母玲子的友人帐",
    ])

    result = engine.distill_top_k(
        query_text="夏目贵志",
        top_k=3,
        tags=["主角", "妖怪"],
        ts_ms=2000,
    )

    assert result.success is True
    assert result.semantic_id is not None
    assert result.source_count > 0
    assert result.skipped is False

    # 验证写入了 semantic_memory 表
    items, total = ledger.list_semantic_memories()
    assert total == 1
    assert items[0]["semantic_id"] == result.semantic_id
    assert "夏目贵志" in items[0]["summary"]
    assert items[0]["confidence"] >= 0.0


# ---------------------------------------------------------------------------
# T-Distill-02：幂等检查
# ---------------------------------------------------------------------------

def test_distill_top_k_idempotent(engine, episodic, ledger):
    """相同 source_memory_ids 组合重复蒸馏应跳过（skipped=True），不重复写入。"""
    _write_memories(episodic, ["夏目贵志能看见妖怪"])

    result1 = engine.distill_top_k(query_text="夏目", top_k=1, ts_ms=1000)
    assert result1.success is True
    assert result1.skipped is False

    # 第二次相同查询（召回相同 source_ids）
    result2 = engine.distill_top_k(query_text="夏目", top_k=1, ts_ms=2000)
    assert result2.success is True
    assert result2.skipped is True
    assert result2.semantic_id == result1.semantic_id

    # 数据库中只有一条记录
    _, total = ledger.list_semantic_memories()
    assert total == 1


# ---------------------------------------------------------------------------
# T-Distill-03：无情节记忆
# ---------------------------------------------------------------------------

def test_distill_top_k_empty_store(engine):
    """空 EpisodicStore 时 distill_top_k 应返回 success=False。"""
    result = engine.distill_top_k(query_text="夏目贵志", top_k=5)
    assert result.success is False
    assert result.semantic_id is None
    assert result.source_count == 0


# ---------------------------------------------------------------------------
# T-Distill-04：list_semantic_memories 分页
# ---------------------------------------------------------------------------

def test_list_semantic_memories_pagination(engine, episodic, ledger):
    """写入多条后分页查询正确。"""
    # 写入 3 批不同的情节记忆，蒸馏 3 次
    for i in range(3):
        _write_memories(episodic, [f"记忆片段{i:03d}：内容描述{i}"])
        engine.distill_top_k(query_text=f"记忆{i:03d}", top_k=1, ts_ms=1000 + i * 100)

    # 全量查询
    items_all, total = ledger.list_semantic_memories(limit=10, offset=0)
    assert total == 3
    assert len(items_all) == 3

    # 分页：每页 2 条
    items_p1, _ = ledger.list_semantic_memories(limit=2, offset=0)
    items_p2, _ = ledger.list_semantic_memories(limit=2, offset=2)
    assert len(items_p1) == 2
    assert len(items_p2) == 1


# ---------------------------------------------------------------------------
# T-Distill-05：context_pack semantic_slot 验证
# ---------------------------------------------------------------------------

def _make_valid_context_pack(**overrides) -> dict:
    """构造最小合法 context_pack。"""
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


def test_context_pack_semantic_slot_valid():
    """合法 semantic_slot 应通过验证。"""
    cp = _make_valid_context_pack(
        semantic_slot=[
            {
                "slot": "semantic_slot",
                "semantic_id": "sem_abc123",
                "summary": "夏目贵志的语义摘要",
                "confidence": 0.85,
                "tags": ["主角"],
                "source_count": 3,
                "ts_ms": 1000,
            }
        ]
    )
    result = validate_prompt_context(cp)
    assert result.valid, f"应通过验证，但有违规: {result.violations}"


def test_context_pack_semantic_slot_missing_required_field():
    """semantic_slot 条目缺少 semantic_id 或 summary 时应报错。"""
    cp = _make_valid_context_pack(
        semantic_slot=[
            {"summary": "缺少 semantic_id 的条目"}  # 缺少 semantic_id
        ]
    )
    result = validate_prompt_context(cp)
    assert not result.valid
    codes = [v.code for v in result.violations]
    assert "PROMPT_CONTEXT_TYPE_INVALID" in codes


def test_context_pack_without_semantic_slot_still_valid():
    """不包含 semantic_slot 的 context_pack 应仍然通过验证（可选槽位）。"""
    cp = _make_valid_context_pack()
    result = validate_prompt_context(cp)
    assert result.valid, f"不含 semantic_slot 应通过验证，但有违规: {result.violations}"


# ---------------------------------------------------------------------------
# T-Distill-06：build_semantic_slot
# ---------------------------------------------------------------------------

def test_build_semantic_slot(engine, episodic, ledger):
    """build_semantic_slot 应从 ledger 构建格式正确的 semantic_slot 列表。"""
    _write_memories(episodic, ["夏目贵志的故事"])
    engine.distill_top_k(query_text="夏目", top_k=1, ts_ms=1000)

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
