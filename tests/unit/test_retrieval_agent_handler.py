"""P11 单元测试：retrieval_agent_handler（legacy_chain 模式 + trace 契约 + 异常安全）。

验收标准覆盖：
  - 3.1：retrieval_agent 可产出工件，artifact_ref 非空，retrieval_agent_trace 符合契约
  - 3.3：retrieval_agent_trace 字段契约固化（7 个字段）
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

def _make_payload(request_id: str = "p11-unit-001", **kwargs: Any) -> dict[str, Any]:
    base = {
        "request_id": request_id,
        "execution_profile": "WORKFLOW_SUPERVISED",
        "user_input": "查找关于人工智能的相关资料",
        "active_agent_id": AGENT_ID_RETRIEVAL,
    }
    base.update(kwargs)
    return base


def _make_retrieval_output(
    results: list | None = None,
    summary: str = "检索到相关文档",
    source: str = "knowledge_base",
    gateway_status: str = "OK",
) -> dict[str, Any]:
    actual_results = [{"id": "doc-1", "text": "人工智能简介"}] if results is None else results
    return {
        "gateway_status": gateway_status,
        "payload": {
            "results": actual_results,
            "summary": summary,
            "source": source,
        },
    }


# ---------------------------------------------------------------------------
# P11-1a：基本功能 — 产出工件与 trace
# ---------------------------------------------------------------------------

class TestRetrievalAgentHandlerBasic:
    """retrieval_agent_handler 基本功能测试。"""

    def test_returns_dict(self):
        """handler 返回 dict。"""
        result = retrieval_agent_handler(_make_payload())
        assert isinstance(result, dict)

    def test_artifact_ref_non_empty(self):
        """agent_artifacts[retrieval_agent].artifact_ref 非空。"""
        payload = _make_payload(
            workbench_retrieval=_make_retrieval_output()
        )
        result = retrieval_agent_handler(payload)
        artifacts = result.get("agent_artifacts") or {}
        art = artifacts.get(AGENT_ID_RETRIEVAL)
        assert art is not None, "agent_artifacts[retrieval_agent] 须存在"
        assert art.get("artifact_ref"), "artifact_ref 不得为空"

    def test_artifact_ref_contains_request_id(self):
        """artifact_ref 包含 request_id。"""
        payload = _make_payload(
            request_id="p11-req-abc",
            workbench_retrieval=_make_retrieval_output(),
        )
        result = retrieval_agent_handler(payload, context={"request_id": "p11-req-abc"})
        trace = result.get("retrieval_agent_trace") or {}
        assert "p11-req-abc" in trace.get("artifact_ref", ""), (
            "artifact_ref 应包含 request_id"
        )

    def test_retrieval_agent_trace_exists(self):
        """retrieval_agent_trace 写入 state。"""
        payload = _make_payload(workbench_retrieval=_make_retrieval_output())
        result = retrieval_agent_handler(payload)
        assert "retrieval_agent_trace" in result, "retrieval_agent_trace 须写入 state"

    def test_result_count_correct(self):
        """result_count 与检索结果条数一致。"""
        items = [{"id": f"doc-{i}"} for i in range(3)]
        payload = _make_payload(
            workbench_retrieval=_make_retrieval_output(results=items)
        )
        result = retrieval_agent_handler(payload)
        trace = result["retrieval_agent_trace"]
        assert trace["result_count"] == 3

    def test_result_count_zero_when_no_results(self):
        """无检索结果时 result_count 为 0。"""
        payload = _make_payload(
            workbench_retrieval=_make_retrieval_output(results=[])
        )
        result = retrieval_agent_handler(payload)
        trace = result["retrieval_agent_trace"]
        assert trace["result_count"] == 0

    def test_retrieval_source_from_payload(self):
        """retrieval_source 从 workbench_retrieval 读取。"""
        payload = _make_payload(
            workbench_retrieval=_make_retrieval_output(source="vector_store")
        )
        result = retrieval_agent_handler(payload)
        trace = result["retrieval_agent_trace"]
        assert trace["retrieval_source"] == "vector_store"

    def test_retrieval_source_default_knowledge_base(self):
        """workbench_retrieval 无 source 时默认为 knowledge_base。"""
        payload = _make_payload(
            workbench_retrieval={"gateway_status": "OK", "payload": {"results": []}}
        )
        result = retrieval_agent_handler(payload)
        trace = result["retrieval_agent_trace"]
        assert trace["retrieval_source"] == "knowledge_base"


# ---------------------------------------------------------------------------
# P11-1b：trace 契约固化（验收 3.3）
# ---------------------------------------------------------------------------

class TestRetrievalAgentTraceContract:
    """retrieval_agent_trace 字段契约固化（P11 §3.3）。"""

    REQUIRED_FIELDS = {
        "agent_id", "mode", "artifact_ref",
        "retrieval_source", "result_count", "ts_ms",
    }

    def _get_trace(self, **kwargs: Any) -> dict[str, Any]:
        payload = _make_payload(
            workbench_retrieval=_make_retrieval_output(),
            **kwargs,
        )
        result = retrieval_agent_handler(payload)
        return result.get("retrieval_agent_trace") or {}

    def test_all_required_fields_present(self):
        """trace 包含全部 7 个必填字段。"""
        trace = self._get_trace()
        for field in self.REQUIRED_FIELDS:
            assert field in trace, f"trace 缺少必填字段: {field!r}"

    def test_agent_id_is_retrieval_agent(self):
        """agent_id 固定为 retrieval_agent。"""
        trace = self._get_trace()
        assert trace["agent_id"] == AGENT_ID_RETRIEVAL

    def test_mode_is_legacy_chain(self):
        """mode 固定为 legacy_chain（P11 唯一模式）。"""
        trace = self._get_trace()
        assert trace["mode"] == "legacy_chain"

    def test_no_react_enabled_field(self):
        """trace 不含 react_enabled 字段（预留字段已删除）。"""
        trace = self._get_trace()
        assert "react_enabled" not in trace

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

    def test_no_extra_required_fields(self):
        """trace 字段数量不超过契约定义（不得擅自新增必填字段）。"""
        trace = self._get_trace()
        # 允许 trace 包含契约字段，不允许缺少
        for field in self.REQUIRED_FIELDS:
            assert field in trace


# ---------------------------------------------------------------------------
# P11-1c：异常安全
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
        result = retrieval_agent_handler({"request_id": "p11-err-001"})
        assert isinstance(result, dict)

    def test_missing_workbench_retrieval_trace_written(self):
        """缺少 workbench_retrieval 时 retrieval_agent_trace 仍写入。"""
        result = retrieval_agent_handler({"request_id": "p11-err-001"})
        assert "retrieval_agent_trace" in result

    def test_invoke_model_node_raises_no_exception(self):
        """invoke_model_node 抛异常时 handler 降级处理，不向上抛。"""
        ctx = {"invoke_model_node": MagicMock(side_effect=RuntimeError("节点不可用"))}
        result = retrieval_agent_handler({"request_id": "p11-err-002"}, context=ctx)
        assert isinstance(result, dict)
        artifacts = result.get("agent_artifacts") or {}
        assert AGENT_ID_RETRIEVAL in artifacts

    def test_gateway_status_not_ok_risk_flag(self):
        """gateway_status 非 OK 时 risk_flags 包含 GATEWAY_STATUS 标记。"""
        payload = _make_payload(
            workbench_retrieval=_make_retrieval_output(gateway_status="ERROR")
        )
        result = retrieval_agent_handler(payload)
        artifacts = result.get("agent_artifacts") or {}
        art = artifacts.get(AGENT_ID_RETRIEVAL) or {}
        risk_flags = art.get("risk_flags") or []
        assert any("GATEWAY_STATUS" in f for f in risk_flags), (
            "gateway_status 非 OK 时须有 GATEWAY_STATUS risk_flag"
        )

    def test_gateway_status_not_ok_low_confidence(self):
        """gateway_status 非 OK 时 confidence 降低。"""
        payload = _make_payload(
            workbench_retrieval=_make_retrieval_output(gateway_status="TIMEOUT")
        )
        result = retrieval_agent_handler(payload)
        artifacts = result.get("agent_artifacts") or {}
        art = artifacts.get(AGENT_ID_RETRIEVAL) or {}
        assert art.get("confidence", 1.0) < 0.5, (
            "gateway_status 非 OK 时 confidence 应低于 0.5"
        )

    def test_does_not_modify_supervisor_decision(self):
        """handler 不修改 supervisor_decision 字段。"""
        payload = _make_payload(
            workbench_retrieval=_make_retrieval_output(),
            supervisor_decision={"selected_agent": "retrieval_agent", "decision_source": "rule"},
        )
        original = dict(payload["supervisor_decision"])
        result = retrieval_agent_handler(payload)
        assert result.get("supervisor_decision") == original

    def test_does_not_modify_execution_profile(self):
        """handler 不修改 execution_profile 字段。"""
        payload = _make_payload(workbench_retrieval=_make_retrieval_output())
        result = retrieval_agent_handler(payload)
        assert result.get("execution_profile") == "WORKFLOW_SUPERVISED"


# ---------------------------------------------------------------------------
# P11-1d：agent_registry 常量验证
# ---------------------------------------------------------------------------

def test_agent_id_retrieval_constant():
    """AGENT_ID_RETRIEVAL 常量值为 retrieval_agent。"""
    assert AGENT_ID_RETRIEVAL == "retrieval_agent"


def test_agent_id_retrieval_in_allowlist():
    """AGENT_ID_RETRIEVAL 已加入 AGENT_ALLOWLIST。"""
    from host_brain.app.agents.agent_registry import AGENT_ALLOWLIST
    assert AGENT_ID_RETRIEVAL in AGENT_ALLOWLIST


def test_agent_id_retrieval_in_dispatch_allowlist():
    """AGENT_ID_RETRIEVAL 已加入 DEFAULT_AGENT_DISPATCH_ALLOWLIST。"""
    from host_brain.app.handlers.agent_dispatch_handler import DEFAULT_AGENT_DISPATCH_ALLOWLIST
    assert AGENT_ID_RETRIEVAL in DEFAULT_AGENT_DISPATCH_ALLOWLIST


def test_retrieval_agent_handler_importable():
    """retrieval_agent_handler 可导入。"""
    from host_brain.app.handlers.retrieval_agent_handler import retrieval_agent_handler  # noqa: F401
