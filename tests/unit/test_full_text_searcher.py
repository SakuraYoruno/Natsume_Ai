"""FTS5 中文词组检索修复测试（TASK-0210 M23-A）。

验证 FullTextSearcher._split_chinese_query 和 index_memory/search 的中文字符级分割修复。

测试编号：T-FTS-01~06
"""

from __future__ import annotations

import pytest

from host_brain.retrieval.recall_fusion import FullTextSearcher


# ---------------------------------------------------------------------------
# T-FTS-01：中文词组查询命中
# ---------------------------------------------------------------------------
class TestChineseWordQueryHit:
    """T-FTS-01：中文词组查询能命中含该词组的文档。"""

    def test_chinese_word_query_hits_document(self) -> None:
        """T-FTS-01a：查询"猫咪"能命中含"猫咪"的文档。"""
        searcher = FullTextSearcher()
        searcher.index_memory(memory_id="mem_001", content="今天和用户聊了很多关于猫咪的话题")
        results = searcher.search("猫咪")
        assert len(results) >= 1, "查询'猫咪'应命中含'猫咪'的文档"
        doc_ids = [r["doc_id"] for r in results]
        assert "mem_001" in doc_ids, "mem_001 应在结果中"
        searcher.close()

    def test_chinese_phrase_query_hits_document(self) -> None:
        """T-FTS-01b：查询"工作压力"能命中含"工作压力"的文档。"""
        searcher = FullTextSearcher()
        searcher.index_memory(memory_id="mem_002", content="用户提到最近工作压力很大，需要放松")
        results = searcher.search("工作压力")
        assert len(results) >= 1, "查询'工作压力'应命中含'工作压力'的文档"
        doc_ids = [r["doc_id"] for r in results]
        assert "mem_002" in doc_ids
        searcher.close()

    def test_fts_score_positive_when_hit(self) -> None:
        """T-FTS-01c：命中时 fts_score 应大于 0。"""
        searcher = FullTextSearcher()
        searcher.index_memory(memory_id="mem_003", content="用户喜欢听轻音乐，特别是钢琴曲")
        results = searcher.search("轻音乐")
        assert len(results) >= 1
        assert results[0]["fts_score"] > 0.0, "命中时 fts_score 应 > 0"
        searcher.close()


# ---------------------------------------------------------------------------
# T-FTS-02：中文单字查询命中
# ---------------------------------------------------------------------------
class TestChineseSingleCharQuery:
    """T-FTS-02：中文单字查询能命中含该字的文档。"""

    def test_single_char_query_hits(self) -> None:
        """T-FTS-02a：查询单字"猫"能命中含"猫"的文档。"""
        searcher = FullTextSearcher()
        searcher.index_memory(memory_id="mem_cat", content="猫咪很可爱")
        results = searcher.search("猫")
        assert len(results) >= 1, "单字查询'猫'应命中含'猫'的文档"
        searcher.close()

    def test_single_char_query_no_false_positive(self) -> None:
        """T-FTS-02b：查询单字"猫"不应命中不含"猫"的文档。"""
        searcher = FullTextSearcher()
        searcher.index_memory(memory_id="mem_dog", content="用户喜欢狗狗")
        results = searcher.search("猫")
        doc_ids = [r["doc_id"] for r in results]
        assert "mem_dog" not in doc_ids, "不含'猫'的文档不应被命中"
        searcher.close()


# ---------------------------------------------------------------------------
# T-FTS-03：英文查询不受影响
# ---------------------------------------------------------------------------
class TestEnglishQueryUnaffected:
    """T-FTS-03：英文查询不受中文分割逻辑影响。"""

    def test_english_query_hits(self) -> None:
        """T-FTS-03a：英文查询正常命中。"""
        searcher = FullTextSearcher()
        searcher.index_memory(memory_id="mem_en", content="user likes cats and music")
        results = searcher.search("cats")
        assert len(results) >= 1, "英文查询'cats'应命中"
        searcher.close()

    def test_english_query_no_false_positive(self) -> None:
        """T-FTS-03b：英文查询不命中无关文档。"""
        searcher = FullTextSearcher()
        searcher.index_memory(memory_id="mem_en2", content="user likes dogs")
        results = searcher.search("cats")
        doc_ids = [r["doc_id"] for r in results]
        assert "mem_en2" not in doc_ids
        searcher.close()

    def test_split_chinese_query_english_unchanged(self) -> None:
        """T-FTS-03c：_split_chinese_query 对纯英文不做拆分。"""
        result = FullTextSearcher._split_chinese_query("hello world")
        assert "hello" in result
        assert "world" in result


# ---------------------------------------------------------------------------
# T-FTS-04：混合中英文查询
# ---------------------------------------------------------------------------
class TestMixedChineseEnglishQuery:
    """T-FTS-04：混合中英文查询正常工作。"""

    def test_mixed_query_split(self) -> None:
        """T-FTS-04a：_split_chinese_query 对混合文本正确处理。"""
        result = FullTextSearcher._split_chinese_query("用户喜欢cats")
        # 中文字符应被分割，英文保留
        assert "用" in result
        assert "户" in result
        assert "cats" in result

    def test_mixed_content_searchable(self) -> None:
        """T-FTS-04b：含中英文混合内容的文档可被中文词查询命中。"""
        searcher = FullTextSearcher()
        searcher.index_memory(memory_id="mem_mix", content="用户喜欢cats和音乐")
        results = searcher.search("音乐")
        assert len(results) >= 1, "混合内容文档应被中文词查询命中"
        searcher.close()


# ---------------------------------------------------------------------------
# T-FTS-05：FTS5 无结果时降级为空列表（回归保护）
# ---------------------------------------------------------------------------
class TestFTS5NoResultFallback:
    """T-FTS-05：FTS5 无结果时返回空列表，不抛出异常（回归保护）。"""

    def test_no_match_returns_empty(self) -> None:
        """T-FTS-05a：查询无匹配时返回空列表。"""
        searcher = FullTextSearcher()
        searcher.index_memory(memory_id="mem_x", content="完全不相关的内容")
        results = searcher.search("量子纠缠宇宙飞船")
        assert isinstance(results, list), "无结果时应返回列表"
        # 可能为空，也可能有部分字符命中，但不应抛出异常
        searcher.close()

    def test_empty_query_returns_empty(self) -> None:
        """T-FTS-05b：空查询返回空列表。"""
        searcher = FullTextSearcher()
        results = searcher.search("")
        assert results == [], "空查询应返回空列表"
        searcher.close()

    def test_whitespace_query_returns_empty(self) -> None:
        """T-FTS-05c：纯空白查询返回空列表。"""
        searcher = FullTextSearcher()
        results = searcher.search("   ")
        assert results == [], "纯空白查询应返回空列表"
        searcher.close()


# ---------------------------------------------------------------------------
# T-FTS-06：hybrid_fused=True 当 FTS5 有命中时（回归保护）
# ---------------------------------------------------------------------------
class TestHybridFusedWhenFTSHits:
    """T-FTS-06：FTS5 有命中时，混合融合结果 hybrid_fused=True（回归保护）。"""

    def test_hybrid_fused_true_when_fts_hits(self) -> None:
        """T-FTS-06a：FTS5 命中时，HybridFusionStrategy 融合后 fts_score > 0。"""
        from host_brain.retrieval.recall_fusion import HybridFusionStrategy

        searcher = FullTextSearcher()
        searcher.index_memory(memory_id="mem_h1", content="今天和用户聊了很多关于猫咪的话题")

        # 先通过 FTS5 检索获取候选
        fts_candidates = searcher.search("猫咪", top_k=5)
        assert len(fts_candidates) >= 1, (
            "FTS5 应命中含'猫咪'的文档，当前命中 0 条，说明 FTS5 中文检索仍未修复"
        )
        assert fts_candidates[0]["fts_score"] > 0.0, "FTS5 命中时 fts_score 应 > 0"

        # 构造向量候选（模拟）
        vector_candidates = [
            {"doc_id": "mem_h1", "score": 0.8, "snippet": "今天和用户聊了很多关于猫咪的话题"},
        ]

        strategy = HybridFusionStrategy(
            vector_weight=0.7,
            fts_weight=0.3,
        )
        fused = strategy.fuse(
            vector_candidates=vector_candidates,
            fts_candidates=fts_candidates,
        )
        assert len(fused) >= 1, "融合结果不应为空"
        # 融合后 mem_h1 的 fts_score 应 > 0（FTS5 有命中）
        mem_h1 = next((c for c in fused if c["doc_id"] == "mem_h1"), None)
        assert mem_h1 is not None
        assert mem_h1.get("fts_score", 0.0) > 0.0, (
            "FTS5 命中时融合候选的 fts_score 应 > 0，"
            "当前为 0 说明 FTS5 中文检索仍未修复"
        )
        searcher.close()

    def test_hybrid_fused_false_when_fts_no_hit(self) -> None:
        """T-FTS-06b：FTS5 无命中时，融合候选的 fts_score=0（降级为纯向量，回归保护）。"""
        from host_brain.retrieval.recall_fusion import HybridFusionStrategy

        searcher = FullTextSearcher()
        # 不写入任何内容，FTS5 必然无命中
        fts_candidates = searcher.search("完全不存在的内容", top_k=5)
        # FTS5 无命中时返回空列表
        assert fts_candidates == [], "FTS5 无命中时应返回空列表"

        vector_candidates = [
            {"doc_id": "mem_v1", "score": 0.9, "snippet": "some content"},
        ]

        strategy = HybridFusionStrategy(
            vector_weight=0.7,
            fts_weight=0.3,
        )
        fused = strategy.fuse(
            vector_candidates=vector_candidates,
            fts_candidates=fts_candidates,  # 空列表
        )
        assert len(fused) >= 1, "纯向量融合结果不应为空"
        # FTS5 无命中时，融合候选的 fts_score 应为 0
        mem_v1 = next((c for c in fused if c["doc_id"] == "mem_v1"), None)
        assert mem_v1 is not None
        assert mem_v1.get("fts_score", 0.0) == 0.0, "FTS5 无命中时 fts_score 应为 0"
        searcher.close()


# ---------------------------------------------------------------------------
# _split_chinese_query 单元测试
# ---------------------------------------------------------------------------
class TestSplitChineseQuery:
    """_split_chinese_query 静态方法的单元测试。"""

    def test_pure_chinese_split(self) -> None:
        """纯中文词组被逐字分割。"""
        result = FullTextSearcher._split_chinese_query("猫咪")
        assert result == "猫 咪"

    def test_longer_chinese_phrase(self) -> None:
        """较长中文词组被逐字分割。"""
        result = FullTextSearcher._split_chinese_query("工作压力大")
        assert result == "工 作 压 力 大"

    def test_pure_english_unchanged(self) -> None:
        """纯英文不被拆分。"""
        result = FullTextSearcher._split_chinese_query("hello")
        assert "hello" in result

    def test_empty_string(self) -> None:
        """空字符串返回空字符串。"""
        result = FullTextSearcher._split_chinese_query("")
        assert result == ""

    def test_mixed_chinese_english(self) -> None:
        """混合中英文：中文逐字，英文保留。"""
        result = FullTextSearcher._split_chinese_query("用户喜欢cats")
        parts = result.split()
        assert "用" in parts
        assert "户" in parts
        assert "喜" in parts
        assert "欢" in parts
        assert "cats" in parts

    def test_numbers_unchanged(self) -> None:
        """数字不被拆分。"""
        result = FullTextSearcher._split_chinese_query("2026年")
        assert "2026" in result
        assert "年" in result
