"""T-CTX-01~05: ContextCompressor 单元测试。"""

from __future__ import annotations

import pytest

from host_brain.memory.context_compressor import (
    ContextCompressor,
    STRATEGY_SLIDING,
    STRATEGY_SUMMARY,
    STRATEGY_HYBRID,
    STATUS_OK,
    STATUS_DEGRADED,
)
from host_brain.core.errors import CTX_COMPRESS_LLM_UNAVAILABLE, CTX_COMPRESS_INPUT_INVALID


# ── 测试夹具 ──────────────────────────────────────────────────────────────────

def _make_turns(n: int, chars_per_turn: int = 100) -> list[dict]:
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"{'x' * chars_per_turn} turn {i}"}
        for i in range(n)
    ]


def _mock_summary_provider(turns: list[dict]) -> str:
    return f"[摘要] 共 {len(turns)} 轮对话内容的摘要。"


def _failing_summary_provider(turns: list[dict]) -> str:
    raise RuntimeError("LLM unavailable")


# ── T-CTX-01: sliding_window 截断超出 max_tokens 的轮次 ──────────────────────

class TestSlidingWindow:
    """T-CTX-01: sliding_window 截断超出 max_tokens 的轮次。"""

    def test_truncates_to_budget(self):
        # 每轮约 25 tokens（100 chars * 0.25），10 轮 = 250 tokens
        turns = _make_turns(10, chars_per_turn=100)
        compressor = ContextCompressor(max_tokens=100)
        result = compressor.compress(turns, strategy=STRATEGY_SLIDING, max_tokens=100)
        assert result.strategy == STRATEGY_SLIDING
        assert result.total_tokens <= 100
        assert len(result.turns) < 10

    def test_keeps_recent_turns(self):
        turns = _make_turns(10, chars_per_turn=100)
        compressor = ContextCompressor(max_tokens=100)
        result = compressor.compress(turns, strategy=STRATEGY_SLIDING, max_tokens=100)
        # 保留的应该是最近的轮次
        if result.turns:
            last_kept = result.turns[-1]["content"]
            assert "turn 9" in last_kept

    def test_empty_turns_returns_degraded(self):
        compressor = ContextCompressor()
        result = compressor.compress([], strategy=STRATEGY_SLIDING)
        assert result.degraded is True
        assert result.reason_code == CTX_COMPRESS_INPUT_INVALID

    def test_all_turns_fit_within_budget(self):
        turns = _make_turns(3, chars_per_turn=10)
        compressor = ContextCompressor(max_tokens=2048)
        result = compressor.compress(turns, strategy=STRATEGY_SLIDING)
        assert len(result.turns) == 3
        assert result.degraded is False


# ── T-CTX-02: summary 策略生成摘要且不伪造事实 ───────────────────────────────

class TestSummaryStrategy:
    """T-CTX-02: summary 策略生成摘要且不伪造事实。"""

    def test_summary_generated(self):
        turns = _make_turns(5)
        compressor = ContextCompressor(summary_provider=_mock_summary_provider)
        result = compressor.compress(turns, strategy=STRATEGY_SUMMARY)
        assert result.strategy == STRATEGY_SUMMARY
        assert result.summary is not None
        assert "摘要" in result.summary
        assert result.degraded is False

    def test_summary_contains_turn_count(self):
        turns = _make_turns(5)
        compressor = ContextCompressor(summary_provider=_mock_summary_provider)
        result = compressor.compress(turns, strategy=STRATEGY_SUMMARY)
        assert "5" in result.summary

    def test_summary_no_provider_degrades(self):
        turns = _make_turns(5)
        compressor = ContextCompressor(summary_provider=None)
        result = compressor.compress(turns, strategy=STRATEGY_SUMMARY)
        assert result.degraded is True
        assert result.reason_code == CTX_COMPRESS_LLM_UNAVAILABLE

    def test_summary_provider_failure_degrades(self):
        turns = _make_turns(5)
        compressor = ContextCompressor(summary_provider=_failing_summary_provider)
        result = compressor.compress(turns, strategy=STRATEGY_SUMMARY)
        assert result.degraded is True
        assert result.reason_code == CTX_COMPRESS_LLM_UNAVAILABLE


# ── T-CTX-03: hybrid 策略优先保留最近 N 轮 + 摘要历史 ────────────────────────

class TestHybridStrategy:
    """T-CTX-03: hybrid 策略优先保留最近 N 轮 + 摘要历史。"""

    def test_recent_turns_preserved(self):
        turns = _make_turns(10)
        compressor = ContextCompressor(
            summary_provider=_mock_summary_provider,
            recent_turns=4,
        )
        result = compressor.compress(turns, strategy=STRATEGY_HYBRID)
        assert result.strategy == STRATEGY_HYBRID
        # 最近 4 轮应该被保留
        assert len(result.turns) <= 4
        if result.turns:
            assert "turn 9" in result.turns[-1]["content"]

    def test_history_summarized(self):
        turns = _make_turns(10)
        compressor = ContextCompressor(
            summary_provider=_mock_summary_provider,
            recent_turns=4,
        )
        result = compressor.compress(turns, strategy=STRATEGY_HYBRID)
        # 历史轮次应该被摘要
        assert result.summary is not None

    def test_hybrid_no_provider_degrades_gracefully(self):
        turns = _make_turns(10)
        compressor = ContextCompressor(summary_provider=None, recent_turns=4)
        result = compressor.compress(turns, strategy=STRATEGY_HYBRID)
        # 降级但仍保留最近轮次
        assert len(result.turns) > 0
        assert result.degraded is True

    def test_hybrid_few_turns_no_history(self):
        turns = _make_turns(3)
        compressor = ContextCompressor(
            summary_provider=_mock_summary_provider,
            recent_turns=6,
        )
        result = compressor.compress(turns, strategy=STRATEGY_HYBRID)
        # 轮次少于 recent_turns，无历史需要摘要
        assert len(result.turns) == 3
        assert result.summary is None


# ── T-CTX-04: evidence_pack 字段结构稳定 ─────────────────────────────────────

class TestEvidencePackStructure:
    """T-CTX-04: EvidencePack 字段结构稳定（同版本同输入）。"""

    def test_pack_fields_present(self):
        turns = _make_turns(4)
        compressor = ContextCompressor()
        compressed = compressor.compress(turns, strategy=STRATEGY_SLIDING)
        pack = compressor.build_evidence_pack(
            compressed,
            request_id="req-001",
            intent_id="intent-001",
        )
        assert pack.pack_id.startswith("epk_")
        assert pack.request_id == "req-001"
        assert pack.intent_id == "intent-001"
        assert isinstance(pack.candidates, list)
        assert pack.hash.startswith("") and len(pack.hash) == 64
        assert pack.ts_ms > 0

    def test_pack_deterministic_same_input(self):
        turns = _make_turns(3)
        compressor = ContextCompressor()
        compressed = compressor.compress(turns, strategy=STRATEGY_SLIDING)
        pack1 = compressor.build_evidence_pack(compressed, request_id="req-det", ts_ms=1000000)
        pack2 = compressor.build_evidence_pack(compressed, request_id="req-det", ts_ms=1000000)
        assert pack1.hash == pack2.hash
        assert pack1.pack_id == pack2.pack_id

    def test_pack_includes_episodic_hits(self):
        turns = _make_turns(2)
        compressor = ContextCompressor()
        compressed = compressor.compress(turns, strategy=STRATEGY_SLIDING)
        episodic = [{"doc_id": "ep_001", "snippet": "episodic memory", "score": 0.9}]
        pack = compressor.build_evidence_pack(
            compressed,
            request_id="req-ep",
            episodic_hits=episodic,
        )
        doc_ids = [c.doc_id for c in pack.candidates]
        assert "ep_001" in doc_ids


# ── T-CTX-05: LLM 不可用时降级为 sliding_window ──────────────────────────────

class TestLLMUnavailableFallback:
    """T-CTX-05: LLM 不可用时降级为 sliding_window。"""

    def test_summary_fallback_to_sliding(self):
        turns = _make_turns(8)
        compressor = ContextCompressor(summary_provider=_failing_summary_provider)
        result = compressor.compress(turns, strategy=STRATEGY_SUMMARY)
        assert result.degraded is True
        assert result.reason_code == CTX_COMPRESS_LLM_UNAVAILABLE
        # 降级后仍有轮次
        assert len(result.turns) > 0

    def test_hybrid_fallback_keeps_recent(self):
        turns = _make_turns(8)
        compressor = ContextCompressor(
            summary_provider=_failing_summary_provider,
            recent_turns=3,
        )
        result = compressor.compress(turns, strategy=STRATEGY_HYBRID)
        assert result.degraded is True
        assert len(result.turns) > 0
        # 最近轮次仍被保留
        if result.turns:
            assert "turn 7" in result.turns[-1]["content"]


# ── T-CTX-06~09: 压缩前静默保存（COMPRESS_BEFORE_SAVE，TASK-0201）────────────

class TestPreCompressSave:
    """T-CTX-06~09: ContextCompressor 压缩前静默保存验收测试。"""

    def _make_episodic_writer(self):
        """返回 (writer_callable, saved_records_list)。"""
        saved: list[tuple[str, list[str]]] = []

        def _writer(text: str, evidence_refs: list[str]) -> None:
            saved.append((text, evidence_refs))

        return _writer, saved

    def _make_mock_ledger(self):
        """返回 (ledger, events_list)。"""
        events: list[dict] = []

        class _MockLedger:
            def log_scheduler_event(self, row: dict) -> None:
                events.append(dict(row))

        return _MockLedger(), events

    # T-CTX-06: summary 策略触发压缩时，_pre_compress_save 被调用
    def test_summary_strategy_triggers_pre_save(self):
        writer, saved = self._make_episodic_writer()
        ledger, events = self._make_mock_ledger()
        compressor = ContextCompressor(
            summary_provider=_mock_summary_provider,
            episodic_writer=writer,
            ledger=ledger,
            pre_compress_save_enabled=True,
        )
        # 构造含 assistant 轮次的对话
        turns = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！我是夏目，有什么可以帮你的？"},
            {"role": "user", "content": "今天天气怎么样？"},
            {"role": "assistant", "content": "今天天气晴朗，适合出行。"},
        ]
        compressor.compress(turns, strategy=STRATEGY_SUMMARY, session_id="sess_001")

        # 至少写入 1 条 Episodic 记录（assistant 轮次）
        assert len(saved) >= 1

    # T-CTX-06 补充：保存内容含有效 evidence_refs
    def test_pre_save_records_contain_evidence_refs(self):
        writer, saved = self._make_episodic_writer()
        compressor = ContextCompressor(
            summary_provider=_mock_summary_provider,
            episodic_writer=writer,
            pre_compress_save_enabled=True,
        )
        turns = [
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": "这是一个重要的回答，包含关键信息。"},
        ]
        compressor.compress(turns, strategy=STRATEGY_SUMMARY, session_id="sess_002")

        assert len(saved) >= 1
        text, evidence_refs = saved[0]
        assert len(evidence_refs) >= 1
        assert all(isinstance(ref, str) and ref for ref in evidence_refs)

    # T-CTX-07: 审计 Ledger 中存在 COMPRESS_BEFORE_SAVE 记录
    def test_audit_ledger_compress_before_save_event(self):
        writer, saved = self._make_episodic_writer()
        ledger, events = self._make_mock_ledger()
        compressor = ContextCompressor(
            summary_provider=_mock_summary_provider,
            episodic_writer=writer,
            ledger=ledger,
            pre_compress_save_enabled=True,
        )
        turns = [
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": "重要回答内容。"},
        ]
        compressor.compress(turns, strategy=STRATEGY_SUMMARY, session_id="sess_003")

        # 审计 Ledger 中应有 COMPRESS_BEFORE_SAVE 事件
        assert len(events) >= 1
        event = events[0]
        assert event["status"] == "COMPRESS_BEFORE_SAVE"
        assert "sess_003" in str(event.get("mode_at_trigger") or "")

    # T-CTX-08: pre_compress_save_enabled=False 时跳过保存
    def test_pre_compress_save_disabled_skips_write(self):
        writer, saved = self._make_episodic_writer()
        ledger, events = self._make_mock_ledger()
        compressor = ContextCompressor(
            summary_provider=_mock_summary_provider,
            episodic_writer=writer,
            ledger=ledger,
            pre_compress_save_enabled=False,
        )
        turns = [
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": "回答内容。"},
        ]
        compressor.compress(turns, strategy=STRATEGY_SUMMARY, session_id="sess_004")

        # 禁用时不写入任何记录
        assert len(saved) == 0
        assert len(events) == 0

    # T-CTX-09: sliding_window 策略不触发 _pre_compress_save
    def test_sliding_window_does_not_trigger_pre_save(self):
        writer, saved = self._make_episodic_writer()
        ledger, events = self._make_mock_ledger()
        compressor = ContextCompressor(
            episodic_writer=writer,
            ledger=ledger,
            pre_compress_save_enabled=True,
        )
        turns = _make_turns(5)
        compressor.compress(turns, strategy=STRATEGY_SLIDING, session_id="sess_005")

        # sliding_window 不触发保存
        assert len(saved) == 0
        assert len(events) == 0
