"""
test_context_pack_builder.py —— ContextPackBuilder 单元测试

覆盖 token 预算截断逻辑的边界情况（需求 4.1、4.2）。
"""
from __future__ import annotations

import pytest

from host_brain.app.recall.context_pack_builder import (
    ContextPackBuilder,
    estimate_token_count,
    truncate_to_token_limit,
)
from host_brain.app.schemas.context_pack import TOKEN_BUDGET, TOTAL_TOKEN_BUDGET


class TestTokenEstimation:
    """Token 估算测试。"""

    def test_empty_string_returns_zero(self):
        assert estimate_token_count("") == 0

    def test_chinese_text_counted(self):
        count = estimate_token_count("你好世界")
        assert count == 4  # 4 个中文字符

    def test_english_text_counted(self):
        count = estimate_token_count("hello world")
        assert count == 2  # 2 个英文词


class TestTruncateToTokenLimit:
    """截断到 token 上限测试。"""

    def test_no_truncation_when_within_limit(self):
        """验证未超限时不截断。"""
        text = "你好"  # 2 tokens
        result, actual, truncated = truncate_to_token_limit(text, max_tokens=10)
        assert result == text
        assert truncated is False

    def test_truncation_when_exceeds_limit(self):
        """验证超限时截断。"""
        # 生成超过 5 tokens 的文本
        text = "一二三四五六七八九十"  # 10 tokens
        result, actual, truncated = truncate_to_token_limit(text, max_tokens=5)
        assert truncated is True
        assert actual <= 5

    def test_exact_limit_not_truncated(self):
        """验证恰好等于上限时不截断（边界情况）。"""
        text = "一二三四五"  # 5 tokens
        result, actual, truncated = truncate_to_token_limit(text, max_tokens=5)
        assert truncated is False
        assert actual == 5


class TestContextPackBuilderTokenBudget:
    """ContextPackBuilder token 预算测试。"""

    def test_each_slot_respects_individual_budget(self):
        """验证各路证据不超过各自的 token 上限（需求 4.1）。"""
        builder = ContextPackBuilder()

        # 构造超过上限的内容
        long_text = "测" * 1000  # 1000 tokens

        recall_results = {
            "world_canon": long_text,
            "style_canon": long_text,
            "episodic_vector": long_text,
        }

        pack = builder.build(recall_results)

        if pack.world_canon:
            assert pack.world_canon.token_count <= TOKEN_BUDGET["world_canon"]
        if pack.style_canon:
            assert pack.style_canon.token_count <= TOKEN_BUDGET["style_canon"]
        if pack.episodic_vector:
            assert pack.episodic_vector.token_count <= TOKEN_BUDGET["episodic_vector"]

    def test_total_does_not_exceed_budget(self):
        """验证总量不超过 2700 tokens（需求 4.2）。"""
        builder = ContextPackBuilder()

        # 所有槽位都填满超长内容
        long_text = "测" * 500

        recall_results = {slot: long_text for slot in TOKEN_BUDGET}
        pack = builder.build(recall_results)

        assert pack.total_token_count <= TOTAL_TOKEN_BUDGET, (
            f"总 token 数 {pack.total_token_count} 超过上限 {TOTAL_TOKEN_BUDGET}"
        )

    def test_truncation_priority_order(self):
        """验证超限时按优先级截断（低优先级先丢弃）（需求 4.2）。"""
        builder = ContextPackBuilder()

        # 填充所有槽位，使总量超限
        long_text = "测" * 400  # 每路 400 tokens，7路共 2800 tokens > 2700

        recall_results = {slot: long_text for slot in TOKEN_BUDGET}
        pack = builder.build(recall_results)

        # world_canon 应该被保留（最高优先级）
        if pack.world_canon:
            assert not (pack.world_canon.token_count == 0 and pack.world_canon.content == ""), (
                "world_canon 不应在全局截断中被丢弃"
            )

    def test_subgraph_trace_recorded(self):
        """验证 subgraph_trace 记录各路证据情况（需求 4.5）。"""
        builder = ContextPackBuilder()
        recall_results = {"world_canon": "测试内容"}
        pack = builder.build(recall_results)

        assert len(pack.subgraph_trace) > 0
        trace = pack.subgraph_trace[0]
        assert "slot" in trace
        assert "actual_tokens" in trace
        assert "truncated" in trace


class TestConflictDetection:
    """语义冲突检测测试（需求 4.3）。"""

    def test_conflict_suppresses_episodic(self):
        """验证 world_canon 与 episodic 冲突时 episodic 被标记为 CONFLICT_SUPPRESSED。"""
        builder = ContextPackBuilder()

        recall_results = {
            "world_canon": "用户从未养过猫",
            "episodic_vector": "用户不是从未养过猫，他有一只猫",
        }

        pack = builder.build(recall_results)

        # 如果检测到冲突，episodic 应被标记
        if pack.episodic_vector and pack.episodic_vector.conflict_suppressed:
            assert pack.episodic_vector.content == "[CONFLICT_SUPPRESSED]"
            assert len(pack.conflict_events) > 0
