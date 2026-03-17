"""P12 单元测试：retrieval_agent_handler（ReAct 模式 + trace 契约 + 异常安全）。

验收标准覆盖：
  - 3.1：retrieval_agent 走 ReAct 路径，artifact_ref 非空，react_scratchpad 非 None
  - P12 §4：retrieval_agent_trace 字段契约固化（8 个字段）
  - 异常安全：任何情况下不抛异常，agent_artifacts 始终写入
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from host_brain.app.agents.agent_registry import AGENT_ID_RETRIEVAL
from host_brain.app.handlers.retrieval_agent_handler import retrieval_agent_handler


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _make_payload(request_id: str = "p12-unit-001", **kwargs: Any) -> dict[str, Any]:
    base = {
        "request_id": request_id,
        "execution_profile": "WORKFLOW_SUPERVISED",
        "user_input": "查找关于人工智能的相关资料",
        "active_agent_id": AGENT_ID_RETRIEVAL,
    }
    base.update(kwargs)
    return base


def _make_gateway(final_answer: str = "检索完成，找到相关文档") -> MagicMock:
    """构造一个返回 final_answer 的 mock gateway。"""
    import json
    gateway = MagicMock()
    gateway.complete.return_value = {
        "content": json.dumps({
            "thought": "已完成检索",
            "action_type": "final_answer",
            "final_answer": final_answer,
        }, ensure_ascii=False)
    }
    return gateway


def _make_retrieval_payload(
    results: list | None = None,
    source: str = "knowledge_base",
    request_id: str = "p12-unit-001",
    gateway: Any = None,
) -> dict[str, Any]:
    actual_results = [{"id": "doc-1", "text": "人工智能简介"}] if results is None else results
    payload = _make_payload(
        request_id=request_id,
        workbench_retrieval={
            "gateway_status": "OK",
            "payload": {
                "results": actual_results,
                "summary": "检索到相关文档",
                "source": source,
            },
        },
    )
    if gateway is not None:
        payload["_gateway"] = gateway
    return payload


# ---------------------------------------------------------------------------
# P12-1a：基本功能 — ReAct 路径可运行
# ---------------------------------------------------------------------------

class TestRetrievalAgentHandlerBasic:
    """retrieval_agent_handler 基本功能测试（ReAct 模式）。"""

    def test_returns_dict(self):
        """handler 返回 dict。"""
        result = retrieval_agent_handler(_make_payload())
        assert isinstance(result, dict)

    def test_artifact_ref_non_empty(self):
        """agent_artifacts[retrieval_agent].artifact_ref 非空。"""
        payload = _make_retrieval_payload(gateway=_make_gateway())
        result = retrieval_agent_handler(payload)
        artifacts = result.get("agent_artifacts") or {}
        art = artifacts.get(AGENT_ID_RETRIEVAL)
        assert art is not None, "agent_artifacts[retrieval_agent] 须存在"
        assert art.get("artifact_ref"), "artifact_ref 不得为空"

    def test_artifact_ref_contains_request_id(self):
        """artifact_ref 包含 request_id。"""
        payload = _make_retrieval_payload(request_id="p12-req-abc", gateway=_make_gateway())
        result = retrieval_agent_handler(payload, context={"request_id": "p12-req-abc"})
        trace = result.get("retrieval_agent_trace") or {}
        assert "p12-req-abc" in trace.get("artifact_ref", ""), (
            "artifact_ref 应包含 request_id"
        )

    def test_retrieval_agent_trace_exists(self):
        """retrieval_agent_trace 写入 state。"""
        payload = _make_retrieval_payload(gateway=_make_gateway())
        result = retrieval_agent_handler(payload)
        assert "retrieval_agent_trace" in result, "retrieval_agent_trace 须写入 state"

    def test_react_scratchpad_written(self):
        """react_scratchpad 写入 state，非 None。"""
        payload = _make_retrieval_payload(gateway=_make_gateway())
        result = retrieval_agent_handler(payload)
        assert result.get("react_scratchpad") is not None, (
            "react_scratchpad 须写入 state"
        )

    def test_react_scratchpad_has_steps(self):
        """react_scratchpad 包含至少一个 step 记录。"""
        payload = _make_retrieval_payload(gateway=_make_gateway())
        result = retrieval_agent_handler(payload)
        scratchpad = result.get("react_scratchpad") or {}
        steps = scratchpad.get("steps") or []
        assert len(steps) >= 1, "react_scratchpad.steps 须包含至少一个 step"

    def test_mode_is_react_loop(self):
        """trace.mode 固定为 react_loop。"""
        payload = _make_retrieval_payload(gateway=_make_gateway())
        result = retrieval_agent_handler(payload)
        trace = result.get("retrieval_agent_trace") or {}
        assert trace.get("mode") == "react_loop", (
            f"mode 应为 react_loop，实际为 {trace.get('mode')!r}"
        )

    def test_result_count_correct(self):
        """result_count 与检索结果条数一致。"""
        items = [{"id": f"doc-{i}"} for i in range(3)]
        payload = _make_retrieval_payload(results=items, gateway=_make_gateway())
        result = retrieval_agent_handler(payload)
        trace = result["retrieval_agent_trace"]
        assert trace["result_count"] == 3

    def test_result_count_zero_when_no_results(self):
        """无检索结果时 result_count 为 0。"""
        payload = _make_retrieval_payload(results=[], gateway=_make_gateway())
        result = retrieval_agent_handler(payload)
        trace = result["retrieval_agent_trace"]
        assert trace["result_count"] == 0

    def test_retrieval_source_from_payload(self):
        """retrieval_source 从 workbench_retrieval 读取。"""
        payload = _make_retrieval_payload(source="vector_store", gateway=_make_gateway())
        result = retrieval_agent_handler(payload)
        trace = result["retrieval_agent_trace"]
        assert trace["retrieval_source"] == "vector_store"

    def test_retrieval_source_default_knowledge_base(self):
        """workbench_retrieval 无 source 时默认为 knowledge_base。"""
        payload = _make_payload(
            workbench_retrieval={"gateway_status": "OK", "payload": {"results": []}},
            _gateway=_make_gateway(),
        )
        result = retrieval_agent_handler(payload)
        trace = result["retrieval_agent_trace"]
        assert trace["retrieval_source"] == "knowledge_base"


# ---------------------------------------------------------------------------
# P12-1b：trace 契约固化（验收 P12 §4）
# ---------------------------------------------------------------------------

class TestRetrievalAgentTraceContract:
    """retrieval_agent_trace 字段契约固化（P12 §4）。"""

    REQUIRED_FIELDS = {
        "agent_id", "mode", "artifact_ref",
        "retrieval_source", "result_count",
        "total_steps", "stopped_reason", "ts_ms",
    }

    def _get_trace(self, **kwargs: Any) -> dict[str, Any]:
        payload = _make_retrieval_payload(gateway=_make_gateway(), **kwargs)
        result = retrieval_agent_handler(payload)
        return result.get("retrieval_agent_trace") or {}

    def test_all_required_fields_present(self):
        """trace 包含全部 8 个必填字段。"""
        trace = self._get_trace()
        for field in self.REQUIRED_FIELDS:
            assert field in trace, f"trace 缺少必填字段: {field!r}"

    def test_agent_id_is_retrieval_agent(self):
        """agent_id 固定为 retrieval_agent。"""
        trace = self._get_trace()
        assert trace["agent_id"] == AGENT_ID_RETRIEVAL

    def test_mode_is_react_loop(self):
        """mode 固定为 react_loop（P12 唯一模式）。"""
        trace = self._get_trace()
        assert trace["mode"] == "react_loop"

    def test_no_legacy_chain_mode(self):
        """trace.mode 不为 legacy_chain（P11 模式已删除）。"""
        trace = self._get_trace()
        assert trace["mode"] != "legacy_chain"

    def test_no_react_enabled_field(self):
        """trace 不含 react_enabled 字段（预留字段已删除）。"""
        trace = self._get_trace()
        assert "react_enabled" not in trace

    def test_total_steps_is_int_ge_1(self):
        """total_steps 为 int 类型，≥ 1。"""
        trace = self._get_trace()
        assert isinstance(trace["total_steps"], int)
        assert trace["total_steps"] >= 1

    def test_stopped_reason_valid(self):
        """stopped_reason 为合法值之一。"""
        valid_reasons = {"FINAL_ANSWER", "MAX_STEPS", "ERROR", "NO_LLM"}
        trace = self._get_trace()
        assert trace["stopped_reason"] in valid_reasons, (
            f"stopped_reason 非法: {trace['stopped_reason']!r}"
        )

    def test_result_count_is_int(self):
        """result_count 为 int 类型，≥ 0。"""
        trace = self._get_trace()
        assert isinstance(trace["result_count"], int)
        assert trace["result_count"] >= 0

    def test_ts_ms_is_int(self):
        """ts_ms 为 int 类型（Unix 毫秒时间戳）。"""
        trace = self._get_trace()
        assert isinstance(trace["ts_ms"], int)
        assert trace["ts_ms"] > 0

    def test_artifact_ref_format(self):
        """artifact_ref 格式为 artifact://...。"""
        trace = self._get_trace()
        assert trace["artifact_ref"].startswith("artifact://"), (
            f"artifact_ref 格式错误: {trace['artifact_ref']!r}"
        )

    def test_stopped_reason_final_answer_with_gateway(self):
        """有 gateway 且返回 final_answer 时，stopped_reason 为 FINAL_ANSWER。"""
        trace = self._get_trace()
        assert trace["stopped_reason"] == "FINAL_ANSWER"

    def test_stopped_reason_no_llm_without_gateway(self):
        """无 gateway 时，stopped_reason 为 NO_LLM。"""
        payload = _make_payload(
            workbench_retrieval={"gateway_status": "OK", "payload": {"results": []}}
        )
        result = retrieval_agent_handler(payload)
        trace = result.get("retrieval_agent_trace") or {}
        assert trace.get("stopped_reason") == "NO_LLM"


# ---------------------------------------------------------------------------
# P12-1c：异常安全
# ---------------------------------------------------------------------------

class TestRetrievalAgentHandlerErrorSafety:
    """retrieval_agent_handler 异常安全：任何情况下不抛异常，agent_artifacts 始终写入。"""

    def test_empty_payload_no_exception(self):
        """空 payload 时不抛异常。"""
        result = retrieval_agent_handler({})
        assert isinstance(result, dict)

    def test_empty_payload_artifacts_written(self):
        """空 payload 时 agent_artifacts[retrieval_agent] 仍写入。"""
        result = retrieval_agent_handler({})
        artifacts = result.get("agent_artifacts") or {}
        assert AGENT_ID_RETRIEVAL in artifacts, (
            "空 payload 时 agent_artifacts[retrieval_agent] 须存在"
        )

    def test_missing_workbench_retrieval_no_exception(self):
        """缺少 workbench_retrieval 时不抛异常。"""
        result = retrieval_agent_handler({"request_id": "p12-err-001"})
        assert isinstance(result, dict)

    def test_missing_workbench_retrieval_trace_written(self):
        """缺少 workbench_retrieval 时 retrieval_agent_trace 仍写入。"""
        result = retrieval_agent_handler({"request_id": "p12-err-001"})
        assert "retrieval_agent_trace" in result

    def test_gateway_raises_no_exception(self):
        """gateway.complete 抛异常时 handler 降级处理，不向上抛。"""
        gateway = MagicMock()
        gateway.complete.side_effect = RuntimeError("LLM 不可用")
        payload = _make_payload(_gateway=gateway)
        result = retrieval_agent_handler(payload)
        assert isinstance(result, dict)
        artifacts = result.get("agent_artifacts") or {}
        assert AGENT_ID_RETRIEVAL in artifacts

    def test_gateway_raises_stopped_reason_error(self):
        """gateway.complete 抛异常时 stopped_reason 为 ERROR。"""
        gateway = MagicMock()
        gateway.complete.side_effect = RuntimeError("LLM 不可用")
        payload = _make_payload(_gateway=gateway)
        result = retrieval_agent_handler(payload)
        trace = result.get("retrieval_agent_trace") or {}
        assert trace.get("stopped_reason") == "ERROR"

    def test_does_not_modify_supervisor_decision(self):
        """handler 不修改 supervisor_decision 字段。"""
        payload = _make_retrieval_payload(gateway=_make_gateway())
        payload["supervisor_decision"] = {
            "selected_agent": "retrieval_agent",
            "decision_source": "rule",
        }
        original = dict(payload["supervisor_decision"])
        result = retrieval_agent_handler(payload)
        assert result.get("supervisor_decision") == original

    def test_does_not_modify_execution_profile(self):
        """handler 不修改 execution_profile 字段。"""
        payload = _make_retrieval_payload(gateway=_make_gateway())
        result = retrieval_agent_handler(payload)
        assert result.get("execution_profile") == "WORKFLOW_SUPERVISED"

    def test_react_scratchpad_written_even_no_gateway(self):
        """无 gateway 时 react_scratchpad 仍写入（NO_LLM 降级路径）。"""
        payload = _make_payload(user_input="查找资料")
        result = retrieval_agent_handler(payload)
        assert result.get("react_scratchpad") is not None, (
            "无 gateway 时 react_scratchpad 须写入（NO_LLM 降级）"
        )


# ---------------------------------------------------------------------------
# P12-1d：agent_registry 常量验证
# ---------------------------------------------------------------------------

def test_agent_id_retrieval_constant():
    """AGENT_ID_RETRIEVAL 常量值为 retrieval_agent。"""
    assert AGENT_ID_RETRIEVAL == "retrieval_agent"


def test_agent_id_retrieval_in_allowlist():
    """AGENT_ID_RETRIEVAL 已加入 AGENT_ALLOWLIST。"""
    from host_brain.app.agents.agent_registry import AGENT_ALLOWLIST
    assert AGENT_ID_RETRIEVAL in AGENT_ALLOWLIST


def test_agent_id_retrieval_in_dispatch_allowlist():
    """AGENT_ID_RETRIEVAL 已加入 _AGENT_HANDLER_MAP（P16：改为测试 executor 映射表）。"""
    from host_brain.integrations.graph_executor import _AGENT_HANDLER_MAP
    assert AGENT_ID_RETRIEVAL in _AGENT_HANDLER_MAP


def test_retrieval_agent_handler_importable():
    """retrieval_agent_handler 可导入。"""
    from host_brain.app.handlers.retrieval_agent_handler import retrieval_agent_handler  # noqa: F401
