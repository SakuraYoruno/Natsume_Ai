"""T-RCL-HYB-01~06: Episodic 混合检索增强单元测试（TASK-0202）。"""

from __future__ import annotations

import pytest

from host_brain.retrieval.full_text_searcher import FullTextSearcher
from host_brain.retrieval.hybrid_fusion import (
    HybridFusionStrategy,
    DEFAULT_VECTOR_WEIGHT,
    DEFAULT_FTS_WEIGHT,
)
from host_brain.retrieval.recall_fusion import RecallFusionServiceContract
from tests.fakes.chroma_client import FakeChromaClient


# ── 测试夹具 ──────────────────────────────────────────────────────────────────

def _make_vector_candidates(n: int = 3) -> list[dict]:
    """生成 n 条向量检索候选。"""
    return [
        {
            "doc_id": f"mem_{i:03d}",
            "source_ref": f"memory://episodic/mem_{i:03d}",
            "snippet": f"向量命中内容 {i}",
            "score": round(0.9 - i * 0.1, 4),
            "confidence": round(0.9 - i * 0.1, 4),
            "metadata": {"matched_routes": ["default"], "collection": "episodic"},
            "evidence_ref": f"evd://episodic/mem_{i:03d}",
        }
        for i in range(n)
    ]


def _make_fts_candidates(doc_ids: list[str], scores: list[float]) -> list[dict]:
    """生成全文检索候选。"""
    return [
        {
            "doc_id": doc_id,
            "snippet": f"全文命中内容 {doc_id}",
            "fts_score": score,
        }
        for doc_id, score in zip(doc_ids, scores)
    ]


# ── T-RCL-HYB-01: 混合检索对专有名词的全文权重贡献 ───────────────────────────

class TestHybridFusionWeightContribution:
    """T-RCL-HYB-01: 全文命中条目的最终分数 ≥ 纯向量检索同条目分数。"""

    def test_fts_hit_increases_score(self):
        strategy = HybridFusionStrategy(vector_weight=0.7, fts_weight=0.3)
        vector_cands = _make_vector_candidates(3)
        # mem_001 同时被全文命中
        fts_cands = _make_fts_candidates(["mem_001"], [0.9])

        fused = strategy.fuse(vector_candidates=vector_cands, fts_candidates=fts_cands)

        # 找到 mem_001 的融合分数
        fused_map = {c["doc_id"]: c for c in fused}
        assert "mem_001" in fused_map

        # 融合分数 = 0.8 * 0.7 + 0.9 * 0.3 = 0.56 + 0.27 = 0.83
        # 纯向量分数 = 0.8 * 0.7 = 0.56
        fused_score = fused_map["mem_001"]["score"]
        pure_vector_score = round(0.8 * 0.7, 6)
        assert fused_score > pure_vector_score

    def test_fts_only_hit_has_positive_score(self):
        """仅全文命中（无向量结果）的条目也有正分数。"""
        strategy = HybridFusionStrategy(vector_weight=0.7, fts_weight=0.3)
        vector_cands = _make_vector_candidates(2)
        fts_cands = _make_fts_candidates(["mem_999"], [0.8])  # 仅全文命中

        fused = strategy.fuse(vector_candidates=vector_cands, fts_candidates=fts_cands)
        fused_map = {c["doc_id"]: c for c in fused}

        assert "mem_999" in fused_map
        assert fused_map["mem_999"]["score"] > 0.0


# ── T-RCL-HYB-02: use_hybrid=False 时退化为纯向量检索 ────────────────────────

class TestHybridDisabled:
    """T-RCL-HYB-02: use_hybrid=False 时，FullTextSearcher 不被调用，结果与纯向量一致。"""

    def test_use_hybrid_false_no_fts(self):
        service = RecallFusionServiceContract(
            chroma_client=FakeChromaClient(),
            use_hybrid=False,
            vector_weight=0.7,
            fts_weight=0.3,
        )
        # use_hybrid=False 时，_fts_searcher 不应被初始化
        assert service._fts_searcher is None

    def test_use_hybrid_false_result_unchanged(self):
        """use_hybrid=False 时，recall 结果与纯向量检索一致（向后兼容）。"""
        chroma = FakeChromaClient()
        # 默认维度为 16
        chroma.upsert(
            memory_id="mem_compat_001",
            text="向后兼容测试内容",
            embedding=[0.1] * 16,
            metadata={"source_ref": "memory://episodic/mem_compat_001"},
        )
        service = RecallFusionServiceContract(
            chroma_client=chroma,
            use_hybrid=False,
        )
        result = service.recall(
            request_id="req_compat",
            query="测试",
            query_embedding=[0.1] * 16,
        )
        # 不应抛出异常，结果正常返回
        assert result.status in ("OK", "DEGRADED")


# ── T-RCL-HYB-03: 同 doc_id 去重，保留融合最高分 ─────────────────────────────

class TestHybridDeduplication:
    """T-RCL-HYB-03: 同一 doc_id 在向量和全文结果中均命中时，融合后只保留一条。"""

    def test_dedup_same_doc_id(self):
        strategy = HybridFusionStrategy(vector_weight=0.7, fts_weight=0.3)
        vector_cands = _make_vector_candidates(3)
        # mem_000 同时被全文命中
        fts_cands = _make_fts_candidates(["mem_000", "mem_001"], [0.95, 0.85])

        fused = strategy.fuse(vector_candidates=vector_cands, fts_candidates=fts_cands)

        # 每个 doc_id 只出现一次
        doc_ids = [c["doc_id"] for c in fused]
        assert len(doc_ids) == len(set(doc_ids))

    def test_fused_score_is_weighted_combination(self):
        """融合分数 = vector_score × 0.7 + fts_score × 0.3。"""
        strategy = HybridFusionStrategy(vector_weight=0.7, fts_weight=0.3)
        vector_cands = [
            {
                "doc_id": "mem_x",
                "source_ref": "memory://episodic/mem_x",
                "snippet": "内容",
                "score": 0.8,
                "confidence": 0.8,
                "metadata": {"matched_routes": ["default"]},
                "evidence_ref": "evd://episodic/mem_x",
            }
        ]
        fts_cands = _make_fts_candidates(["mem_x"], [0.6])

        fused = strategy.fuse(vector_candidates=vector_cands, fts_candidates=fts_cands)
        fused_map = {c["doc_id"]: c for c in fused}

        expected_score = round(0.8 * 0.7 + 0.6 * 0.3, 6)
        assert abs(fused_map["mem_x"]["score"] - expected_score) < 1e-5


# ── T-RCL-HYB-04: FTS5 不可用时自动降级为纯向量 ─────────────────────────────

class TestFTS5Unavailable:
    """T-RCL-HYB-04: FTS5 虚拟表不可用时，自动降级为纯向量检索，不抛出异常。"""

    def test_fts_searcher_init_failure_degrades(self):
        """FullTextSearcher 初始化失败时，RecallFusionServiceContract 降级为纯向量。"""
        service = RecallFusionServiceContract(
            chroma_client=FakeChromaClient(),
            use_hybrid=True,
            fts_db_path=":memory:",  # 内存数据库，FTS5 应可用
        )
        # 手动将 _fts_searcher 设为 None 模拟不可用
        service._fts_searcher = None

        chroma = FakeChromaClient()
        chroma.upsert(
            memory_id="mem_fts_fallback",
            text="fallback test content",
            embedding=[0.2] * 16,
            metadata={},
        )
        service.chroma = chroma

        # 不应抛出异常
        result = service.recall(
            request_id="req_fts_fallback",
            query="fallback test",
            query_embedding=[0.2] * 16,
        )
        assert result.status in ("OK", "DEGRADED")

    def test_fts_search_exception_degrades(self):
        """FTS5 检索抛出异常时，降级为纯向量结果。"""
        strategy = HybridFusionStrategy(vector_weight=0.7, fts_weight=0.3)
        vector_cands = _make_vector_candidates(2)

        # 模拟 FTS5 检索失败：直接调用 fuse 时传入空 fts_candidates
        fused = strategy.fuse(vector_candidates=vector_cands, fts_candidates=[])

        # 结果应与纯向量一致
        assert len(fused) == len(vector_cands)


# ── T-RCL-HYB-05: 权重校验 ───────────────────────────────────────────────────

class TestWeightValidation:
    """T-RCL-HYB-05: vector_weight + fts_weight != 1.0 时抛出 ValueError。"""

    def test_invalid_weights_raise_value_error(self):
        with pytest.raises(ValueError, match="1.0"):
            HybridFusionStrategy(vector_weight=0.6, fts_weight=0.3)

    def test_valid_weights_no_error(self):
        strategy = HybridFusionStrategy(vector_weight=0.7, fts_weight=0.3)
        assert strategy.vector_weight == 0.7
        assert strategy.fts_weight == 0.3

    def test_equal_weights_valid(self):
        strategy = HybridFusionStrategy(vector_weight=0.5, fts_weight=0.5)
        assert strategy.vector_weight == 0.5

    def test_default_weights_valid(self):
        strategy = HybridFusionStrategy()
        assert abs(strategy.vector_weight + strategy.fts_weight - 1.0) < 1e-6


# ── T-RCL-HYB-06: 融合结果为合法 EvidencePack 格式 ──────────────────────────

class TestFusedResultFormat:
    """T-RCL-HYB-06: 融合结果仍为合法候选格式（含 doc_id / score / source_ref / snippet）。"""

    def test_fused_candidates_have_required_fields(self):
        strategy = HybridFusionStrategy(vector_weight=0.7, fts_weight=0.3)
        vector_cands = _make_vector_candidates(3)
        fts_cands = _make_fts_candidates(["mem_000", "mem_999"], [0.8, 0.7])

        fused = strategy.fuse(vector_candidates=vector_cands, fts_candidates=fts_cands)

        for cand in fused:
            assert "doc_id" in cand and cand["doc_id"]
            assert "score" in cand and isinstance(cand["score"], float)
            assert "snippet" in cand
            assert "source_ref" in cand

    def test_fused_results_sorted_by_score_desc(self):
        """融合结果按 score 降序排列。"""
        strategy = HybridFusionStrategy(vector_weight=0.7, fts_weight=0.3)
        vector_cands = _make_vector_candidates(5)
        fts_cands = _make_fts_candidates(["mem_004"], [0.99])  # 最低向量分 + 高全文分

        fused = strategy.fuse(vector_candidates=vector_cands, fts_candidates=fts_cands)
        scores = [c["score"] for c in fused]
        assert scores == sorted(scores, reverse=True)


# ── FullTextSearcher 基础功能测试 ─────────────────────────────────────────────

class TestFullTextSearcher:
    """FullTextSearcher 基础功能测试（内存数据库）。"""

    def test_index_and_search(self):
        searcher = FullTextSearcher(db_path=":memory:")
        searcher.index_memory(memory_id="mem_001", content="natsume is a gentle AI assistant")
        searcher.index_memory(memory_id="mem_002", content="today weather is sunny and nice")

        results = searcher.search("natsume", top_k=5)
        assert len(results) >= 1
        doc_ids = [r["doc_id"] for r in results]
        assert "mem_001" in doc_ids

    def test_empty_query_returns_empty(self):
        searcher = FullTextSearcher(db_path=":memory:")
        searcher.index_memory(memory_id="mem_001", content="测试内容")

        results = searcher.search("", top_k=5)
        assert results == []

    def test_no_match_returns_empty(self):
        searcher = FullTextSearcher(db_path=":memory:")
        searcher.index_memory(memory_id="mem_001", content="完全不相关的内容")

        results = searcher.search("zzz_no_match_xyz", top_k=5)
        assert results == []

    def test_scores_normalized_to_0_1(self):
        searcher = FullTextSearcher(db_path=":memory:")
        for i in range(5):
            searcher.index_memory(memory_id=f"mem_{i:03d}", content=f"test content {i} natsume")

        results = searcher.search("natsume", top_k=5)
        for r in results:
            assert 0.0 <= r["fts_score"] <= 1.0

    def test_idempotent_index(self):
        """重复写入同一 memory_id 不产生重复记录。"""
        searcher = FullTextSearcher(db_path=":memory:")
        searcher.index_memory(memory_id="mem_dup", content="first write content")
        searcher.index_memory(memory_id="mem_dup", content="second write content override")

        results = searcher.search("content", top_k=10)
        doc_ids = [r["doc_id"] for r in results]
        assert doc_ids.count("mem_dup") == 1
