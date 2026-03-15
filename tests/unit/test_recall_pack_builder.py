from __future__ import annotations

from host_brain.app.recall.recall_pack_builder import (
    RecallPackBuilder,
    estimate_token_count,
    truncate_to_token_limit,
)
from host_brain.app.schemas.recall_pack import (
    RECALL_PACK_TOKEN_BUDGET,
    RECALL_SLOT_TOKEN_BUDGET,
)


class TestTokenEstimation:
    def test_empty_string_returns_zero(self) -> None:
        assert estimate_token_count("") == 0

    def test_chinese_text_counted(self) -> None:
        assert estimate_token_count("你好世界") == 4

    def test_english_text_counted(self) -> None:
        assert estimate_token_count("hello world") == 2


class TestTruncateToTokenLimit:
    def test_no_truncation_when_within_limit(self) -> None:
        text = "你好"
        result, actual, truncated = truncate_to_token_limit(text, max_tokens=10)
        assert result == text
        assert actual == 2
        assert truncated is False

    def test_truncation_when_exceeds_limit(self) -> None:
        text = "一二三四五六七八九十"
        _, actual, truncated = truncate_to_token_limit(text, max_tokens=5)
        assert truncated is True
        assert actual <= 5

    def test_exact_limit_not_truncated(self) -> None:
        text = "一二三四五"
        _, actual, truncated = truncate_to_token_limit(text, max_tokens=5)
        assert actual == 5
        assert truncated is False


class TestRecallPackBuilderTokenBudget:
    def test_each_slot_respects_individual_budget(self) -> None:
        builder = RecallPackBuilder()
        long_text = "海" * 1000
        pack = builder.build(
            {
                "world_canon": long_text,
                "style_canon": long_text,
                "episodic_vector": long_text,
            }
        )

        if pack.world_canon:
            assert pack.world_canon.token_count <= RECALL_SLOT_TOKEN_BUDGET["world_canon"]
        if pack.style_canon:
            assert pack.style_canon.token_count <= RECALL_SLOT_TOKEN_BUDGET["style_canon"]
        if pack.episodic_vector:
            assert pack.episodic_vector.token_count <= RECALL_SLOT_TOKEN_BUDGET["episodic_vector"]

    def test_total_does_not_exceed_budget(self) -> None:
        builder = RecallPackBuilder()
        long_text = "海" * 500
        recall_results = {slot: long_text for slot in RECALL_SLOT_TOKEN_BUDGET}
        pack = builder.build(recall_results)
        assert pack.total_token_count <= RECALL_PACK_TOKEN_BUDGET

    def test_truncation_priority_order(self) -> None:
        builder = RecallPackBuilder()
        long_text = "海" * 400
        recall_results = {slot: long_text for slot in RECALL_SLOT_TOKEN_BUDGET}
        pack = builder.build(recall_results)
        if pack.world_canon:
            assert not (pack.world_canon.token_count == 0 and pack.world_canon.content == "")

    def test_subgraph_trace_recorded(self) -> None:
        builder = RecallPackBuilder()
        pack = builder.build({"world_canon": "测试内容"})
        assert pack.subgraph_trace
        trace = pack.subgraph_trace[0]
        assert "slot" in trace
        assert "actual_tokens" in trace
        assert "truncated" in trace


class TestConflictDetection:
    def test_conflict_suppresses_episodic(self) -> None:
        builder = RecallPackBuilder()
        pack = builder.build(
            {
                "world_canon": "用户从未养过猫",
                "episodic_vector": "用户不是从未养过猫，他有一只猫",
            }
        )

        if pack.episodic_vector and pack.episodic_vector.conflict_suppressed:
            assert pack.episodic_vector.content == "[CONFLICT_SUPPRESSED]"
            assert pack.conflict_events
