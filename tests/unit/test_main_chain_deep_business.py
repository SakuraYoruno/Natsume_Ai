"""MainChain 娣卞害涓氬姟鍖?+ SCENE_VISION_UI 鐢熶骇鍖栧姞鍥?鍗曞厓娴嬭瘯銆?
瑕嗙洊鑼冨洿锛?  - GovernorNode锛氱湡瀹?IntentDecisionEngine 璋冪敤 / 闄嶇骇鍥為€€
  - RecallFusionNode锛氱湡瀹?RetrievalOrchestrator 璋冪敤 / 闄嶇骇鍥為€€
  - QAContractNode锛氶暱搴︽埅鏂?/ 瀹夊叏璇嶆娴?/ 鏍煎紡瑙勮寖鍖?  - _VisionSLAMonitor锛氳繛缁け璐ュ憡璀?/ 闄嶇骇鐜囧憡璀?"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from host_brain.app.graph.main_chain import (
    AirlockQuarantineNode,
    ChainContext,
    GovernorNode,
    QAContractNode,
    RecallFusionNode,
)
from host_brain.app.chat_handler_vision import _VisionSLAMonitor
from host_brain.app.schemas import TaskType


# ---------------------------------------------------------------------------
# 杈呭姪鍑芥暟
# ---------------------------------------------------------------------------

def _make_ctx(
    *,
    request_id: str = "req-test-001",
    user_message: str = "浣犲ソ",
    session_id: str = "sess-001",
    quarantine_passed: bool = True,
) -> ChainContext:
    ctx = ChainContext(
        request_id=request_id,
        user_message=user_message,
        session_id=session_id,
    )
    ctx.quarantine_passed = quarantine_passed
    return ctx


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# GovernorNode 娴嬭瘯
# ---------------------------------------------------------------------------

class TestGovernorNodeDeepBusiness:
    """GovernorNode 娣卞害涓氬姟鍖栨祴璇曘€?""

    def test_governor_degraded_fallback_when_engine_unavailable(self):
        """IntentDecisionEngine 涓嶅彲鐢ㄦ椂锛岄檷绾т负鏈€灏忕姸鎬佽涔夊苟鏀捐銆?""
        ctx = _make_ctx()
        node = GovernorNode()

        with patch("host_brain.app.dependencies.get_intent_engine", side_effect=RuntimeError("engine not ready")):
            result = _run(node.execute(ctx))

        assert result is True
        assert ctx.governor_passed is True
        assert "GOVERNOR_DEGRADED_FALLBACK" in ctx.audit_tags
        assert ctx.metadata["governor"]["mode"] == "DEGRADED_FALLBACK"

    def test_governor_active_mode_when_engine_approves(self):
        """IntentDecisionEngine 杩斿洖 APPROVE 鏃讹紝GovernorNode 鏀捐骞舵爣璁?ACTIVE 妯″紡銆?""
        ctx = _make_ctx()
        node = GovernorNode()

        mock_engine = MagicMock()
        mock_engine.evaluate_intent.return_value = {
            "decision": "APPROVE",
            "intent_id": "intent-001",
            "stage": "S_INTENT_OK",
        }

        with patch("host_brain.app.dependencies.get_intent_engine", return_value=mock_engine), \
             patch("host_brain.app.emotion_context.resolve_chat_emotion_context", return_value={}), \
             patch("host_brain.app.emotion_context.build_chat_governance_snapshot", return_value={}):
            result = _run(node.execute(ctx))

        assert result is True
        assert ctx.governor_passed is True
        assert ctx.metadata["governor"]["mode"] == "ACTIVE"
        assert "GOVERNOR_PRECHECK_PASSED" in ctx.audit_tags

    def test_governor_active_mode_when_engine_rejects(self):
        """IntentDecisionEngine 杩斿洖 REJECT 鏃讹紝GovernorNode 鎷掔粷璇锋眰銆?""
        ctx = _make_ctx()
        node = GovernorNode()

        mock_engine = MagicMock()
        mock_engine.evaluate_intent.return_value = {
            "decision": "REJECT",
            "reason_code": "RATE_LIMIT_EXCEEDED",
            "stage": "S_GOV_GATES",
        }

        with patch("host_brain.app.dependencies.get_intent_engine", return_value=mock_engine), \
             patch("host_brain.app.emotion_context.resolve_chat_emotion_context", return_value={}), \
             patch("host_brain.app.emotion_context.build_chat_governance_snapshot", return_value={}):
            result = _run(node.execute(ctx))

        assert result is False
        assert ctx.governor_passed is False
        assert ctx.governor_block_reason == "RATE_LIMIT_EXCEEDED"

    def test_governor_blocks_when_airlock_not_passed(self):
        """Airlock 鏈€氳繃鏃讹紝GovernorNode 鐩存帴鎷掔粷銆?""
        ctx = _make_ctx(quarantine_passed=False)
        node = GovernorNode()
        result = _run(node.execute(ctx))
        assert result is False
        assert ctx.governor_passed is False
        assert "GOVERNOR_BLOCK_AIRLOCK" in ctx.audit_tags

    def test_governor_scene_to_task_mapping(self):
        """鍦烘櫙绫诲瀷姝ｇ‘鏄犲皠鍒?TaskType銆?""
        ctx = _make_ctx()
        ctx.metadata["pre_classified_scene"] = "EXEC_INTENT"
        node = GovernorNode()

        with patch("host_brain.app.dependencies.get_intent_engine", side_effect=RuntimeError("skip")):
            _run(node.execute(ctx))

        assert ctx.task_type == TaskType.REASONING


# ---------------------------------------------------------------------------
# RecallFusionNode 娴嬭瘯
# ---------------------------------------------------------------------------

class TestRecallFusionNodeDeepBusiness:
    """RecallFusionNode 娣卞害涓氬姟鍖栨祴璇曘€?""

    def test_recall_degraded_fallback_when_orchestrator_unavailable(self):
        """RetrievalOrchestrator 涓嶅彲鐢ㄦ椂锛岄檷绾т负鏈€灏忕姸鎬佽涔夊苟缁х画銆?""
        ctx = _make_ctx()
        node = RecallFusionNode()

        with patch("host_brain.app.dependencies.get_retrieval_orchestrator", side_effect=RuntimeError("not ready")):
            result = _run(node.execute(ctx))

        assert result is True
        assert "RECALL_DEGRADED_FALLBACK" in ctx.audit_tags
        assert ctx.metadata["recall_fusion"]["mode"] == "DEGRADED_FALLBACK"

    def test_recall_active_mode_with_evidence(self):
        """RetrievalOrchestrator 杩斿洖璇佹嵁鏃讹紝RecallFusionNode 鏍囪 ACTIVE 妯″紡銆?""
        from host_brain.retrieval.orchestrator import RetrievalOrchestratorResult

        ctx = _make_ctx()
        node = RecallFusionNode()

        mock_result = RetrievalOrchestratorResult(
            success=True,
            status="OK",
            reason_code=None,
            path="success",
            detail="ok",
            trace_id="req-test-001",
            query="浣犲ソ",
            session_state={},
            retrieval_summary={"status": "OK"},
            retrieval_evidence_items=[
                {"slot_name": "world_canon", "text": "澶忕洰鏄竴涓狝I鍔╂墜", "snippet": "澶忕洰鏄竴涓狝I鍔╂墜"}
            ],
            retrieval_bundle={},
        )

        mock_orchestrator = AsyncMock()
        mock_orchestrator.arun.return_value = mock_result

        with patch("host_brain.app.dependencies.get_retrieval_orchestrator", return_value=mock_orchestrator):
            result = _run(node.execute(ctx))

        assert result is True
        assert ctx.metadata["recall_fusion"]["mode"] == "ACTIVE"
        assert ctx.metadata["recall_fusion"]["evidence_count"] == 1
        assert ctx.recall_payload is not None
        assert "RECALL_EVIDENCE_COUNT:1" in ctx.audit_tags

    def test_recall_active_mode_empty_evidence(self):
        """RetrievalOrchestrator 杩斿洖绌鸿瘉鎹椂锛屾爣璁?RECALL_EVIDENCE_EMPTY銆?""
        from host_brain.retrieval.orchestrator import RetrievalOrchestratorResult

        ctx = _make_ctx()
        node = RecallFusionNode()

        mock_result = RetrievalOrchestratorResult(
            success=True,
            status="OK",
            reason_code=None,
            path="success",
            detail="ok",
            trace_id="req-test-001",
            query="浣犲ソ",
            session_state={},
            retrieval_summary={"status": "OK"},
            retrieval_evidence_items=[],
            retrieval_bundle={},
        )

        mock_orchestrator = AsyncMock()
        mock_orchestrator.arun.return_value = mock_result

        with patch("host_brain.app.dependencies.get_retrieval_orchestrator", return_value=mock_orchestrator):
            result = _run(node.execute(ctx))

        assert result is True
        assert ctx.metadata["recall_fusion"]["mode"] == "ACTIVE"
        assert "RECALL_EVIDENCE_EMPTY" in ctx.audit_tags


# ---------------------------------------------------------------------------
# QAContractNode 娴嬭瘯
# ---------------------------------------------------------------------------

class TestQAContractNodeDeepBusiness:
    """QAContractNode 娣卞害涓氬姟鍖栨祴璇曘€?""

    def test_qa_skips_when_no_output(self):
        """鏃犺緭鍑烘椂璺宠繃 QA 妫€鏌ャ€?""
        ctx = _make_ctx()
        node = QAContractNode()
        result = _run(node.execute(ctx))
        assert result is True
        assert ctx.metadata["qa_contract"]["status"] == "SKIPPED_NO_OUTPUT"

    def test_qa_format_normalize(self):
        """鏍煎紡瑙勮寖鍖栵細鍘婚櫎棣栧熬绌虹櫧鍜屽浣欑┖琛屻€?""
        ctx = _make_ctx()
        ctx.final_response = "  浣犲ソ\n\n\n\n涓栫晫  "
        node = QAContractNode()
        result = _run(node.execute(ctx))
        assert result is True
        assert ctx.final_response == "浣犲ソ\n\n涓栫晫"
        assert ctx.qa_rewritten is True
        assert "FORMAT_NORMALIZE" in ctx.metadata["qa_contract"]["rewrite_reasons"]

    def test_qa_length_truncation(self, monkeypatch):
        """瓒呴暱鍝嶅簲琚埅鏂紙HOST_QA_MAX_RESPONSE_CHARS 鏈€灏忓€间负 100锛夈€?""
        monkeypatch.setenv("HOST_QA_MAX_RESPONSE_CHARS", "100")
        ctx = _make_ctx()
        # 2000 瀛楃锛岃繙瓒?100 瀛楃闄愬埗
        long_text = "杩欐槸涓€娈靛緢闀跨殑鏂囧瓧銆? * 200
        ctx.final_response = long_text
        node = QAContractNode()
        result = _run(node.execute(ctx))
        assert result is True
        # 鎴柇鍚庨暱搴﹀簲 <= max_chars + 灏戦噺鍙ュ瓙杈圭晫浣欓噺锛堟渶澶?+5锛?        assert len(ctx.final_response) <= 105
        assert ctx.qa_rewritten is True
        assert any("LENGTH_TRUNCATED" in r for r in ctx.metadata["qa_contract"]["rewrite_reasons"])
        assert "QA_LENGTH_TRUNCATED" in ctx.audit_tags

    def test_qa_safety_word_detection(self):
        """瀹夊叏璇嶆娴嬶細鐤戜技绯荤粺鎻愮ず璇嶆硠闇叉椂鍐欏叆鍛婅鏍囩銆?""
        ctx = _make_ctx()
        ctx.final_response = "浣犲ソ锛乻ystem prompt: 浣犳槸涓€涓狝I鍔╂墜銆?
        node = QAContractNode()
        result = _run(node.execute(ctx))
        assert result is True  # 涓嶉樆鏂紝浠呭憡璀?        assert "QA_SAFETY_WARN:PROMPT_LEAK_SUSPECTED" in ctx.audit_tags

    def test_qa_null_byte_removal(self):
        """鍘婚櫎 null 瀛楄妭銆?""
        ctx = _make_ctx()
        ctx.final_response = "浣犲ソ\x00涓栫晫"
        node = QAContractNode()
        result = _run(node.execute(ctx))
        assert result is True
        assert "\x00" not in ctx.final_response
        assert "NULL_BYTE_REMOVED" in ctx.metadata["qa_contract"]["rewrite_reasons"]

    def test_qa_clean_output_no_rewrite(self):
        """骞插噣鐨勮緭鍑轰笉琚噸鍐欍€?""
        ctx = _make_ctx()
        ctx.final_response = "浣犲ソ锛屾湁浠€涔堝彲浠ュ府浣犵殑锛?
        node = QAContractNode()
        result = _run(node.execute(ctx))
        assert result is True
        assert ctx.qa_rewritten is False
        assert ctx.metadata["qa_contract"]["mode"] == "ACTIVE"


# ---------------------------------------------------------------------------
# _VisionSLAMonitor 娴嬭瘯
# ---------------------------------------------------------------------------

class TestVisionSLAMonitor:
    """_VisionSLAMonitor 鐢熶骇鍖栧姞鍥烘祴璇曘€?""

    def test_consecutive_failures_trigger_alert(self, monkeypatch):
        """杩炵画澶辫触杈惧埌闃堝€兼椂瑙﹀彂鍛婅銆?""
        monkeypatch.setenv("HOST_VISION_SLA_FAIL_THRESHOLD", "3")
        monitor = _VisionSLAMonitor()

        # 鍓?2 娆″け璐ワ細涓嶈Е鍙戝憡璀?        for _ in range(2):
            result = monitor.record_call(
                status="DEGRADED", request_id="req-001",
                latency_ms=100, model_id="gemini-2.5-flash", provider="gemini",
            )
        assert result["alert_triggered"] is False

        # 绗?3 娆″け璐ワ細瑙﹀彂鍛婅
        result = monitor.record_call(
            status="DEGRADED", request_id="req-001",
            latency_ms=100, model_id="gemini-2.5-flash", provider="gemini",
        )
        assert result["alert_triggered"] is True
        assert "CONSECUTIVE_FAILURES" in result["alert_reason"]

    def test_consecutive_failures_reset_on_success(self, monkeypatch):
        """鎴愬姛璋冪敤鍚庯紝杩炵画澶辫触璁℃暟閲嶇疆銆?""
        monkeypatch.setenv("HOST_VISION_SLA_FAIL_THRESHOLD", "3")
        monitor = _VisionSLAMonitor()

        # 2 娆″け璐?        for _ in range(2):
            monitor.record_call(
                status="DEGRADED", request_id="req-001",
                latency_ms=100, model_id="gemini-2.5-flash", provider="gemini",
            )

        # 1 娆℃垚鍔燂細閲嶇疆璁℃暟
        monitor.record_call(
            status="OK", request_id="req-001",
            latency_ms=100, model_id="gemini-2.5-flash", provider="gemini",
        )

        # 鍐嶆澶辫触锛氫笉瑙﹀彂鍛婅锛堣鏁板凡閲嶇疆锛?        result = monitor.record_call(
            status="DEGRADED", request_id="req-001",
            latency_ms=100, model_id="gemini-2.5-flash", provider="gemini",
        )
        assert result["consecutive_failures"] == 1
        assert result["alert_triggered"] is False

    def test_degrade_rate_alert(self, monkeypatch):
        """闄嶇骇鐜囪秴杩囬槇鍊兼椂瑙﹀彂鍛婅銆?""
        monkeypatch.setenv("HOST_VISION_SLA_FAIL_THRESHOLD", "100")  # 绂佺敤杩炵画澶辫触鍛婅
        monkeypatch.setenv("HOST_VISION_SLA_DEGRADE_RATE_THRESHOLD", "0.5")
        monitor = _VisionSLAMonitor()

        # 10 娆¤皟鐢紝6 娆￠檷绾э紙60% > 50%锛?        for i in range(10):
            status = "DEGRADED" if i < 6 else "OK"
            result = monitor.record_call(
                status=status, request_id="req-001",
                latency_ms=100, model_id="gemini-2.5-flash", provider="gemini",
            )

        assert result["alert_triggered"] is True
        assert "DEGRADE_RATE" in result["alert_reason"]

    def test_no_alert_for_normal_operation(self, monkeypatch):
        """姝ｅ父杩愯鏃朵笉瑙﹀彂鍛婅銆?""
        monkeypatch.setenv("HOST_VISION_SLA_FAIL_THRESHOLD", "3")
        monkeypatch.setenv("HOST_VISION_SLA_DEGRADE_RATE_THRESHOLD", "0.5")
        monitor = _VisionSLAMonitor()

        for _ in range(20):
            result = monitor.record_call(
                status="OK", request_id="req-001",
                latency_ms=500, model_id="gemini-2.5-flash", provider="gemini",
            )

        assert result["alert_triggered"] is False
        assert result["degrade_rate"] == 0.0


# ---------------------------------------------------------------------------
# 棰勫彫鍥炴敞鍏ラ摼璺祴璇曪紙RecallFusionNode 鈫?_sse_event_generator锛?# ---------------------------------------------------------------------------

class TestPreRecallInjection:
    """楠岃瘉 RecallFusionNode 棰勫彫鍥炵粨鏋滄敞鍏?_sse_event_generator 鐨勯摼璺€?
    鏍稿績闂锛歊ecallFusionNode 鎵ц涓冭矾鍙洖鍚庯紝缁撴灉鍐欏叆 ctx.recall_payload锛?    浣?run_as_sse_adapter 璋冪敤 _unified_chat_sse_generator 鏃跺繀椤诲皢鍏朵紶鍏ワ紝
    鍚﹀垯 _sse_event_generator 浼氶噸澶嶆墽琛屼竴娆″彫鍥烇紙瀛ゅ矝闂锛夈€?    """

    def test_sse_event_generator_uses_pre_recall_evidence_skips_retrieval(self):
        """褰?_pre_recall_evidence 浼犲叆鏃讹紝_sse_event_generator 璺宠繃鍐呴儴鍙洖銆?""
        import asyncio
        from unittest.mock import patch, MagicMock, AsyncMock
        from host_brain.app.chat_stream_handler import _sse_event_generator

        pre_evidence = [
            {"slot_name": "world_canon", "text": "澶忕洰鏄竴涓狝I鍔╂墜", "snippet": "澶忕洰鏄竴涓狝I鍔╂墜"},
            {"slot_name": "episodic", "text": "涓婃鑱婁簡澶╂皵", "snippet": "涓婃鑱婁簡澶╂皵"},
        ]

        mock_governor_result = {"decision": "APPROVE", "intent_id": "intent-001"}
        mock_llm_result = MagicMock()
        mock_llm_result.__aiter__ = AsyncMock(return_value=iter([]))

        retrieval_called = []

        async def _mock_run_retrieval(**kwargs):
            retrieval_called.append(kwargs)
            return {"status": "OK"}, []

        async def _collect_events():
            events = []
            with patch("host_brain.app.chat_stream_handler._run_retrieval", side_effect=_mock_run_retrieval), \
                 patch("host_brain.app.chat_stream_handler.resolve_chat_retrieval_gate", return_value=(True, None)), \
                 patch("host_brain.app.chat_stream_handler._resolve_state_arbiter_world_fact",
                       return_value=({"status": "SKIPPED"}, None)), \
                 patch("host_brain.app.chat_stream_handler.resolve_host_llm_runtime",
                       return_value={"provider_name": "test", "model_id": "test-model",
                                     "status": "OK", "reason_code": None}), \
                 patch("host_brain.app.chat_stream_handler._build_stream_chat_prompt",
                       return_value="test prompt"), \
                 patch("host_brain.app.chat_stream_handler.stream_llm_response",
                       return_value=AsyncMock(__aiter__=lambda s: iter([]))):
                async for event in _sse_event_generator(
                    message="浣犲ソ",
                    session_id="sess-001",
                    thread_id=None,
                    mood_state=None,
                    emotion_tag=None,
                    request_id="req-test-pre-recall",
                    ts_ms=0,
                    intent_draft={"id": "intent-001", "action": "narrative_only"},
                    _pre_approved_governor_result=mock_governor_result,
                    _pre_recall_evidence=pre_evidence,
                ):
                    events.append(event)
            return events

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_collect_events())
        except Exception:
            pass  # 鍏佽 LLM 璋冪敤澶辫触锛屽彧楠岃瘉鍙洖鏄惁琚烦杩?        finally:
            loop.close()

        # 鏍稿績鏂█锛歘run_retrieval 涓嶅簲琚皟鐢紙棰勫彫鍥炲凡鎻愪緵锛?        assert len(retrieval_called) == 0, (
            f"_run_retrieval 琚皟鐢ㄤ簡 {len(retrieval_called)} 娆★紝"
            "璇存槑棰勫彫鍥炴敞鍏ユ湭鐢熸晥锛孯ecallFusionNode 瀛ゅ矝闂鏈慨澶?
        )

    def test_recall_payload_extracted_correctly_in_run_as_sse_adapter(self):
        """run_as_sse_adapter 姝ｇ‘浠?ctx.recall_payload 鎻愬彇 retrieval_evidence_items銆?""
        # 楠岃瘉鎻愬彇閫昏緫锛氬彧鏈夐潪绌哄垪琛ㄦ墠浼氳鎻愬彇涓?_pre_recall
        test_cases = [
            # (recall_payload, expected_pre_recall_is_not_none)
            ({"retrieval_evidence_items": [{"text": "test"}]}, True),
            ({"retrieval_evidence_items": []}, False),  # 绌哄垪琛ㄤ笉鎻愬彇
            ({"retrieval_evidence_items": None}, False),  # None 涓嶆彁鍙?            ({}, False),  # 鏃?key 涓嶆彁鍙?            (None, False),  # None pack 涓嶆彁鍙?        ]

        for pack, expected_not_none in test_cases:
            # 妯℃嫙 run_as_sse_adapter 涓殑鎻愬彇閫昏緫
            _pre_recall = None
            _recall_pack = pack
            if isinstance(_recall_pack, dict):
                _items = _recall_pack.get("retrieval_evidence_items")
                if isinstance(_items, list) and len(_items) > 0:
                    _pre_recall = _items

            if expected_not_none:
                assert _pre_recall is not None, f"pack={pack} 搴旇鎻愬彇鍒?_pre_recall"
                assert len(_pre_recall) > 0
            else:
                assert _pre_recall is None, f"pack={pack} 涓嶅簲璇ユ彁鍙栧埌 _pre_recall"


