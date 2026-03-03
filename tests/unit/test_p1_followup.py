"""P1 风险跟进 + M10-D/E 功能测试。

覆盖：
- F-33: check_wcanon_slot_violation() 告警写入审计
- F-34: WorldCanonStore 文件锁（filelock 可选降级）
- F-38: list_proposals() cursor-based 分页（after_id）
- M10-D: distill_top_k() llm_client 参数（LLM 摘要 + 降级）
- M10-E: _HotwordsCache TTL 自动失效
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# F-33: WCANON_SLOT_VIOLATION 告警
# ---------------------------------------------------------------------------

class TestWcanonSlotViolation:
    def test_returns_violated_true(self):
        from host_brain.core.qa import check_wcanon_slot_violation
        result = check_wcanon_slot_violation("world_canon_slot", detail="slot inject failed")
        assert result.violated is True
        assert result.slot_name == "world_canon_slot"
        assert "slot inject failed" in result.detail

    def test_writes_to_ledger(self):
        from host_brain.core.qa import check_wcanon_slot_violation, VIOLATION_WCANON_SLOT
        mock_ledger = MagicMock()
        result = check_wcanon_slot_violation(
            "semantic_slot",
            detail="semantic slot missing",
            ledger=mock_ledger,
            request_id="req-001",
        )
        assert result.violated is True
        mock_ledger.log_qa_violation.assert_called_once_with(
            request_id="req-001",
            code=VIOLATION_WCANON_SLOT,
            detail="semantic slot missing",
            field="semantic_slot",
        )

    def test_no_ledger_no_error(self):
        from host_brain.core.qa import check_wcanon_slot_violation
        # 无 ledger 时不抛异常
        result = check_wcanon_slot_violation("world_canon_slot")
        assert result.violated is True

    def test_as_dict(self):
        from host_brain.core.qa import check_wcanon_slot_violation
        result = check_wcanon_slot_violation("world_canon_slot", detail="test")
        d = result.as_dict()
        assert d["violated"] is True
        assert d["slot_name"] == "world_canon_slot"


# ---------------------------------------------------------------------------
# F-34: WorldCanonStore 文件锁（filelock 可选降级）
# ---------------------------------------------------------------------------

class TestWorldCanonStoreFileLock:
    def test_upsert_creates_lock_dir(self, tmp_path):
        """upsert_document 后 .locks 目录应存在。"""
        from host_brain.memory.canon_world_store import WorldCanonStore
        from host_brain.audit.sqlite_ledger import SQLiteLedger
        ledger = SQLiteLedger(db_path=str(tmp_path / "test.db"))
        store = WorldCanonStore(world_root=tmp_path / "world", ledger=ledger)
        assert (tmp_path / "world" / ".locks").exists()

    def test_upsert_succeeds_without_filelock(self, tmp_path):
        """filelock 不可用时降级写入仍然成功。"""
        from host_brain.memory.canon_world_store import WorldCanonStore
        from host_brain.audit.sqlite_ledger import SQLiteLedger
        import host_brain.memory.canon_world_store as cws_mod

        ledger = SQLiteLedger(db_path=str(tmp_path / "test.db"))
        store = WorldCanonStore(world_root=tmp_path / "world", ledger=ledger)

        # 模拟 filelock 不可用
        original = cws_mod._FILELOCK_AVAILABLE
        cws_mod._FILELOCK_AVAILABLE = False
        try:
            doc = store.upsert_document(
                doc_id="wc-test-001",
                title="测试文档",
                tags=["tag1"],
                body="测试内容",
                proposal_id="prop-001",
            )
            assert doc.doc_id == "wc-test-001"
        finally:
            cws_mod._FILELOCK_AVAILABLE = original


# ---------------------------------------------------------------------------
# F-38: list_proposals() cursor-based 分页
# ---------------------------------------------------------------------------

class TestListProposalsCursorPaging:
    def _make_ledger(self, tmp_path):
        from host_brain.audit.sqlite_ledger import SQLiteLedger
        return SQLiteLedger(db_path=str(tmp_path / "test.db"))

    def _insert_proposals(self, ledger, n: int):
        for i in range(n):
            ledger.log_canon_proposal({
                "proposal_id": f"prop-{i:04d}",
                "canon_kind": "world",
                "target_id": f"doc-{i:04d}",
                "base_version": 0,
                "proposed_version": 1,
                "proposed_hash": f"hash{i}",
                "proposer_node_id": "test",
                "summary": f"proposal {i}",
                "status": "pending",
                "ts_ms": 1000 + i,
            })

    def test_first_page_no_cursor(self, tmp_path):
        ledger = self._make_ledger(tmp_path)
        self._insert_proposals(ledger, 10)
        items, total = ledger.list_proposals(limit=5)
        assert total == 10
        assert len(items) == 5

    def test_cursor_based_second_page(self, tmp_path):
        ledger = self._make_ledger(tmp_path)
        self._insert_proposals(ledger, 10)
        # 第一页
        page1, total = ledger.list_proposals(limit=5)
        assert len(page1) == 5
        last_row_id = page1[-1]["row_id"]  # 使用 row_id 作为 cursor
        # 第二页（cursor）
        page2, _ = ledger.list_proposals(limit=5, after_id=last_row_id)
        assert len(page2) == 5
        # 两页 row_id 不重叠
        ids1 = {r["row_id"] for r in page1}
        ids2 = {r["row_id"] for r in page2}
        assert ids1.isdisjoint(ids2)

    def test_cursor_no_overlap_with_offset(self, tmp_path):
        """cursor-based 与 offset 分页结果一致（小数据集验证）。"""
        ledger = self._make_ledger(tmp_path)
        self._insert_proposals(ledger, 6)
        page1, _ = ledger.list_proposals(limit=3)
        last_row_id = page1[-1]["row_id"]
        cursor_page2, _ = ledger.list_proposals(limit=3, after_id=last_row_id)
        offset_page2, _ = ledger.list_proposals(limit=3, offset=3)
        assert [r["proposal_id"] for r in cursor_page2] == [r["proposal_id"] for r in offset_page2]


# ---------------------------------------------------------------------------
# M10-D: distill_top_k() llm_client 参数
# ---------------------------------------------------------------------------

class TestDistillTopKLlmClient:
    def _make_engine(self, tmp_path):
        from host_brain.audit.sqlite_ledger import SQLiteLedger
        from host_brain.memory.distillation import MemoryDistillationEngine

        mock_episodic = MagicMock()
        mock_episodic.recall.return_value = [
            {"memory_id": "m1", "text": "记忆片段1", "score": 0.9},
            {"memory_id": "m2", "text": "记忆片段2", "score": 0.8},
        ]
        ledger = SQLiteLedger(db_path=str(tmp_path / "test.db"))
        engine = MemoryDistillationEngine(episodic_store=mock_episodic, ledger=ledger)
        return engine

    def test_llm_client_used_when_provided(self, tmp_path):
        from host_brain.memory.distillation import LlmSummaryClient
        engine = self._make_engine(tmp_path)

        mock_llm = MagicMock(spec=LlmSummaryClient)
        mock_llm.summarize.return_value = "LLM 生成的摘要"

        result = engine.distill_top_k(
            query_text="测试查询",
            llm_client=mock_llm,
        )
        assert result.success is True
        mock_llm.summarize.assert_called_once()
        # 验证 distill_strategy 被改为 llm_summary
        items, _ = engine.list_semantic_memories()
        assert items[0]["distill_strategy"] == "llm_summary"

    def test_llm_client_fallback_on_error(self, tmp_path):
        """LLM 摘要失败时降级为文本拼接，不抛异常。"""
        from host_brain.memory.distillation import LlmSummaryClient
        engine = self._make_engine(tmp_path)

        mock_llm = MagicMock(spec=LlmSummaryClient)
        mock_llm.summarize.side_effect = RuntimeError("LLM unavailable")

        result = engine.distill_top_k(
            query_text="测试查询",
            llm_client=mock_llm,
        )
        assert result.success is True
        # 降级后 summary 应包含文本拼接标记
        items, _ = engine.list_semantic_memories()
        assert "语义摘要" in items[0]["summary"]

    def test_no_llm_client_uses_merge_texts(self, tmp_path):
        """不传 llm_client 时使用文本拼接。"""
        engine = self._make_engine(tmp_path)
        result = engine.distill_top_k(query_text="测试查询")
        assert result.success is True
        items, _ = engine.list_semantic_memories()
        assert "语义摘要" in items[0]["summary"]


# ---------------------------------------------------------------------------
# M10-E: _HotwordsCache TTL 自动失效
# ---------------------------------------------------------------------------

class TestHotwordsCacheTTL:
    def test_cache_valid_within_ttl(self):
        from edge_client.senses.asr import _HotwordsCache
        cache = _HotwordsCache(ttl_s=60.0)
        cache.set(["word1", "word2"])
        assert cache.is_valid() is True
        assert cache.get() == ["word1", "word2"]

    def test_cache_expired_after_ttl(self):
        from edge_client.senses.asr import _HotwordsCache
        cache = _HotwordsCache(ttl_s=0.05)  # 50ms TTL
        cache.set(["word1"])
        time.sleep(0.1)
        assert cache.is_valid() is False
        assert cache.get() == []

    def test_cache_clear_resets(self):
        from edge_client.senses.asr import _HotwordsCache
        cache = _HotwordsCache(ttl_s=60.0)
        cache.set(["word1"])
        cache.clear()
        assert cache.is_valid() is False
        assert cache.get() == []

    def test_get_canon_hotwords_returns_copy(self):
        from edge_client.senses.asr import _CANON_HOTWORDS_CACHE, get_canon_hotwords
        _CANON_HOTWORDS_CACHE.set(["hotword_a", "hotword_b"])
        result = get_canon_hotwords()
        assert "hotword_a" in result
        # 修改返回值不影响缓存
        result.append("injected")
        assert "injected" not in get_canon_hotwords()

    def test_inject_updates_cache_ttl(self):
        """inject_canon_hotwords 成功后缓存应有效。"""
        from edge_client.senses.asr import _CANON_HOTWORDS_CACHE, inject_canon_hotwords
        import json as _json

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = _json.dumps({
            "hotwords": ["夏目", "友人帳"],
            "total": 2,
        }).encode("utf-8")
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            count = inject_canon_hotwords("http://localhost:18080")

        assert count == 2
        assert _CANON_HOTWORDS_CACHE.is_valid() is True
        assert "夏目" in _CANON_HOTWORDS_CACHE.get()
