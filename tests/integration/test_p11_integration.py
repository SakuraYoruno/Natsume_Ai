"""P11 集成测试：retrieval_agent 最小接入 Supervised Graph + P10 基线不退化。

验收标准覆盖：
  - 3.1：retrieval_agent 可在 Supervised Graph 内被调度并产出工件
  - 3.2：P10 基线（三图主链 + code_agent/translate_agent 路径）不退化
  - 3.3：retrieval_agent_trace 字段契约固化（集成层验证）
  - 文件完整性：P11 新增文件存在，P10 基线文件不退化
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from host_brain.app.agents.agent_registry import AGENT_ID_RETRIEVAL


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _make_supervised_payload(request_id: str = "p11-int-001") -> dict[str, Any]:
    return {
        "request_id": request_id,
        "execution_profile": "WORKFLOW_SUPERVISED",
        "user_input": "查找关于深度学习的相关资料",
        "active_agent_id": AGENT_ID_RETRIEVAL,
        "supervisor_decision": {
            "selected_agent": AGENT_ID_RETRIEVAL,
            "decision_source": "rule",
            "proposal": {"agent_id": AGENT_ID_RETRIEVAL, "confidence": 0.9},
        },
        "workbench_retrieval": {
            "gateway_status": "OK",
            "payload": {
                "results": [
                    {"id": "doc-1", "text": "深度学习基础"},
                    {"id": "doc-2", "text": "神经网络原理"},
                ],
                "summary": "找到 2 篇相关文档",
                "source": "knowledge_base",
            },
        },
    }


# ---------------------------------------------------------------------------
# P11-1a：retrieval_agent 在 Supervised 图上下文中可运行（验收 3.1）
# ---------------------------------------------------------------------------

class TestRetrievalAgentInSupervisedContext:
    """retrieval_agent_handler 在 Supervised 图上下文中的集成行为。"""

    def test_handler_runs_in_supervised_context(self):
        """retrieval_agent_handler 在 Supervised 图上下文中可正常运行，不抛异常。"""
        from host_brain.app.handlers.retrieval_agent_handler import retrieval_agent_handler
        result = retrieval_agent_handler(
            _make_supervised_payload(),
            context={"request_id": "p11-int-001", "execution_profile": "WORKFLOW_SUPERVISED"},
        )
        assert "retrieval_agent_trace" in result
        assert result["retrieval_agent_trace"]["mode"] == "react_loop"

    def test_artifact_ref_non_empty_in_supervised_context(self):
        """Supervised 图上下文中 artifact_ref 非空。"""
        from host_brain.app.handlers.retrieval_agent_handler import retrieval_agent_handler
        result = retrieval_agent_handler(_make_supervised_payload())
        artifacts = result.get("agent_artifacts") or {}
        art = artifacts.get(AGENT_ID_RETRIEVAL)
        assert art is not None
        assert art.get("artifact_ref")

    def test_artifact_ref_contains_request_id(self):
        """artifact_ref 包含 request_id。"""
        from host_brain.app.handlers.retrieval_agent_handler import retrieval_agent_handler
        result = retrieval_agent_handler(
            _make_supervised_payload("p11-int-req-xyz"),
            context={"request_id": "p11-int-req-xyz"},
        )
        trace = result["retrieval_agent_trace"]
        assert "p11-int-req-xyz" in trace["artifact_ref"]

    def test_agent_artifacts_retrieval_agent_exists(self):
        """agent_artifacts[retrieval_agent] 存在且 artifact_ref 非空。"""
        from host_brain.app.handlers.retrieval_agent_handler import retrieval_agent_handler
        result = retrieval_agent_handler(_make_supervised_payload())
        artifacts = result.get("agent_artifacts") or {}
        assert AGENT_ID_RETRIEVAL in artifacts
        assert artifacts[AGENT_ID_RETRIEVAL].get("artifact_ref")

    def test_trace_contract_in_supervised_context(self):
        """Supervised 图上下文中 retrieval_agent_trace 符合 P11 §3.3 契约。"""
        from host_brain.app.handlers.retrieval_agent_handler import retrieval_agent_handler
        result = retrieval_agent_handler(_make_supervised_payload())
        trace = result["retrieval_agent_trace"]
        required = {"agent_id", "mode", "artifact_ref", "retrieval_source", "result_count",
                   "total_steps", "stopped_reason", "ts_ms"}
        for field in required:
            assert field in trace, f"trace 缺少字段: {field!r}"
        assert trace["agent_id"] == AGENT_ID_RETRIEVAL
        assert trace["mode"] == "react_loop"
        assert "react_enabled" not in trace, "react_enabled 预留字段已删除"

    def test_does_not_modify_supervisor_decision(self):
        """retrieval_agent_handler 不修改 supervisor_decision。"""
        from host_brain.app.handlers.retrieval_agent_handler import retrieval_agent_handler
        payload = _make_supervised_payload()
        original_decision = dict(payload["supervisor_decision"])
        result = retrieval_agent_handler(payload)
        assert result.get("supervisor_decision") == original_decision

    def test_does_not_modify_execution_profile(self):
        """retrieval_agent_handler 不修改 execution_profile。"""
        from host_brain.app.handlers.retrieval_agent_handler import retrieval_agent_handler
        result = retrieval_agent_handler(_make_supervised_payload())
        assert result.get("execution_profile") == "WORKFLOW_SUPERVISED"

    def test_result_count_matches_results(self):
        """result_count 与检索结果条数一致。"""
        from host_brain.app.handlers.retrieval_agent_handler import retrieval_agent_handler
        result = retrieval_agent_handler(_make_supervised_payload())
        trace = result["retrieval_agent_trace"]
        assert trace["result_count"] == 2

    def test_retrieval_source_from_workbench_output(self):
        """retrieval_source 从 workbench_retrieval 读取。"""
        from host_brain.app.handlers.retrieval_agent_handler import retrieval_agent_handler
        result = retrieval_agent_handler(_make_supervised_payload())
        trace = result["retrieval_agent_trace"]
        assert trace["retrieval_source"] == "knowledge_base"


# ---------------------------------------------------------------------------
# P11-1b：agent_dispatch allowlist 扩展（验收 3.1）
# P16：改为测试 _AGENT_HANDLER_MAP 和 graph_config_builder allowlist
# ---------------------------------------------------------------------------

class TestAgentDispatchAllowlistExtension:
    """P16：_AGENT_HANDLER_MAP 已包含 retrieval_agent，graph_config allowlist 对齐。"""

    def test_retrieval_agent_in_handler_map(self):
        """_AGENT_HANDLER_MAP 包含 retrieval_agent。"""
        from host_brain.integrations.graph_executor import _AGENT_HANDLER_MAP
        assert AGENT_ID_RETRIEVAL in _AGENT_HANDLER_MAP

    def test_code_agent_still_in_handler_map(self):
        """P9 code_agent 仍在 _AGENT_HANDLER_MAP（不退化）。"""
        from host_brain.app.agents.agent_registry import AGENT_ID_CODE
        from host_brain.integrations.graph_executor import _AGENT_HANDLER_MAP
        assert AGENT_ID_CODE in _AGENT_HANDLER_MAP

    def test_translate_agent_still_in_handler_map(self):
        """P10 translate_agent 仍在 _AGENT_HANDLER_MAP（不退化）。"""
        from host_brain.app.agents.agent_registry import AGENT_ID_TRANSLATE
        from host_brain.integrations.graph_executor import _AGENT_HANDLER_MAP
        assert AGENT_ID_TRANSLATE in _AGENT_HANDLER_MAP

    def test_graph_config_allowlist_contains_retrieval_agent(self):
        """graph_config_builder allowlist 包含 retrieval_agent，可被分派到 retrieval_agent_handler。"""
        from host_brain.app.graph_config_builder import (
            AGENT_DISPATCH_NODE_ID,
            build_supervised_graph,
        )
        config = build_supervised_graph(
            provider_name="mock",
            model_id="mock",
            timeout_ms=5000,
            available=True,
            api_key=None,
            provider_fn=lambda _: None,
        )
        bindings = {b["node_id"]: b for b in config.get("node_bindings", [])}
        agent_binding = bindings.get(AGENT_DISPATCH_NODE_ID)
        assert agent_binding is not None
        allowlist = set(agent_binding.get("agent_dispatch_allowlist") or [])
        assert AGENT_ID_RETRIEVAL in allowlist


# ---------------------------------------------------------------------------
# P11-1c：P10 基线不退化（验收 3.2）
# ---------------------------------------------------------------------------

class TestP10BaselineNotDegraded:
    """P10 基线不退化：P11 新增代码不破坏 P10 已封板契约。"""

    def test_translate_agent_handler_importable(self):
        """P10 translate_agent_handler 仍可导入。"""
        from host_brain.app.handlers.translate_agent_handler import translate_agent_handler  # noqa: F401

    def test_translate_agent_trace_react_mode_still_works(self):
        """P10 translate_agent_handler ReAct 模式在 P11 后仍正常工作。"""
        from host_brain.app.handlers.translate_agent_handler import translate_agent_handler
        gateway = MagicMock()
        gateway.complete.return_value = {
            "content": json.dumps(
                {"thought": "直接翻译", "action_type": "final_answer", "final_answer": "Artificial Intelligence"},
                ensure_ascii=False,
            )
        }
        payload = {"request_id": "p11-p10-baseline-001", "user_input": "翻译这段文字"}
        result = translate_agent_handler(payload, context={"gateway": gateway})
        trace = result.get("translate_agent_trace") or {}
        assert trace.get("mode") == "react_loop"

    def test_translate_agent_trace_contract_unchanged(self):
        """P10 translate_agent_trace 契约字段不变（react_enabled 已删除）。"""
        from host_brain.app.handlers.translate_agent_handler import translate_agent_handler
        gateway = MagicMock()
        gateway.complete.return_value = {
            "content": json.dumps(
                {"thought": "翻译", "action_type": "final_answer", "final_answer": "Hello"},
                ensure_ascii=False,
            )
        }
        payload = {"request_id": "p11-p10-contract-001", "user_input": "翻译"}
        result = translate_agent_handler(payload, context={"gateway": gateway})
        trace = result.get("translate_agent_trace") or {}
        for field in ("agent_id", "mode", "artifact_ref", "total_steps", "stopped_reason", "ts_ms"):
            assert field in trace, f"P10 translate_agent_trace 字段 {field!r} 不得消失"
        assert "react_enabled" not in trace

    def test_code_agent_handler_importable(self):
        """P9 code_agent_handler 仍可导入。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler  # noqa: F401

    def test_code_agent_trace_react_mode_still_works(self):
        """P9 code_agent_handler ReAct 模式在 P11 后仍正常工作。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        gateway = MagicMock()
        gateway.complete.return_value = {
            "content": json.dumps(
                {"thought": "直接给出答案", "action_type": "final_answer", "final_answer": "代码草案"},
                ensure_ascii=False,
            )
        }
        payload = {"request_id": "p11-p9-baseline-001", "user_input": "写代码"}
        result = code_agent_handler(payload, context={"gateway": gateway})
        trace = result.get("code_agent_trace") or {}
        assert trace.get("mode") == "react_loop"

    def test_p10_integration_test_still_importable(self):
        """P10 集成测试模块仍可导入（不退化）。"""
        import tests.integration.test_p10_integration  # noqa: F401

    def test_p8_extended_metrics_module_importable(self):
        """P8 p8_extended_metrics 模块仍可导入。"""
        from host_brain.observability import p8_extended_metrics  # noqa: F401

    def test_p8_ledger_constants_importable(self):
        """P8 ledger_constants 仍可导入。"""
        from host_brain.audit.ledger_constants import (
            P8_LOCAL_LOOP_FAILURE_CODES,
            P8_TABLE_SUBGRAPH_FAILURE_LOG,
            P8_TABLE_GRAPH_RUN_LOG,
        )
        assert "LOCAL_LOOP_TIMEOUT" in P8_LOCAL_LOOP_FAILURE_CODES
        assert P8_TABLE_SUBGRAPH_FAILURE_LOG == "p8_subgraph_failure_log"
        assert P8_TABLE_GRAPH_RUN_LOG == "p8_graph_run_log"

    def test_p7_fields_still_present_after_p11(self):
        """P7 核心字段在 P11 后仍存在（不退化）。"""
        from host_brain.observability.metrics import MetricsCollector
        ledger = MagicMock()
        ledger.supervisor_decision_log = []
        ledger.governor_log = []
        ledger.retrieval_event_log = []
        ledger.intent_log = []
        ledger.skill_exec_log = []
        ledger.config_snapshot_log = []
        ledger.stream_events = []
        ledger.governor_decision_log = []
        ledger.p8_subgraph_failure_log = []
        ledger.p8_graph_run_log = []
        with patch.dict(os.environ, {"P8_EXTENDED_METRICS_ENABLED": "false"}):
            collector = MetricsCollector(ledger=ledger)
            result = collector.collect()
        required_p7_fields = [
            "ts_ms", "obs_mode", "supervisor_metrics",
            "budget_breach_metrics", "agent_metrics",
        ]
        for field in required_p7_fields:
            assert field in result


# ---------------------------------------------------------------------------
# P11-1d：文件完整性
# ---------------------------------------------------------------------------

def test_p11_new_files_exist():
    """P11 新增/修改文件存在（基线完整性）。"""
    root = Path(__file__).resolve().parents[2]
    files = [
        root / "tests" / "unit" / "test_retrieval_agent_handler.py",
        root / "tests" / "integration" / "test_p11_integration.py",
        root / "host_brain" / "app" / "handlers" / "retrieval_agent_handler.py",
        root / "docs" / "50_project" / "MULTI_AGENT_P11_SCOPE_2026-03.md",
    ]
    for f in files:
        assert f.is_file(), f"P11 文件须存在: {f}"


def test_p10_baseline_files_still_exist():
    """P10 实施文件在 P11 后仍存在（不退化）。"""
    root = Path(__file__).resolve().parents[2]
    files = [
        root / "host_brain" / "app" / "handlers" / "translate_agent_handler.py",
        root / "tests" / "unit" / "test_translate_agent_handler.py",
        root / "tests" / "integration" / "test_p10_integration.py",
        root / "docs" / "50_project" / "MULTI_AGENT_P10_SCOPE_2026-03.md",
        root / "docs" / "50_project" / "MULTI_AGENT_P10_SEAL_RESULT_2026-03.md",
    ]
    for f in files:
        assert f.is_file(), f"P10 基线文件须存在: {f}"


def test_p9_baseline_files_still_exist():
    """P9 实施文件在 P11 后仍存在（不退化）。"""
    root = Path(__file__).resolve().parents[2]
    files = [
        root / "host_brain" / "app" / "handlers" / "code_agent_handler.py",
        root / "host_brain" / "app" / "agents" / "react_loop.py",
        root / "host_brain" / "app" / "agents" / "tool_registry.py",
        root / "tests" / "unit" / "test_code_agent_handler.py",
        root / "tests" / "integration" / "test_p9_integration.py",
        root / "docs" / "50_project" / "MULTI_AGENT_P9_SCOPE_2026-03.md",
        root / "docs" / "50_project" / "MULTI_AGENT_P9_SEAL_RESULT_2026-03.md",
    ]
    for f in files:
        assert f.is_file(), f"P9 基线文件须存在: {f}"


def test_p11_scope_doc_exists():
    """P11 SCOPE 定稿文档存在。"""
    root = Path(__file__).resolve().parents[2]
    doc = root / "docs" / "50_project" / "MULTI_AGENT_P11_SCOPE_2026-03.md"
    assert doc.is_file(), "MULTI_AGENT_P11_SCOPE_2026-03.md 须存在"


# ---------------------------------------------------------------------------
# P11-1e：retrieval_agent_handler 异常安全（集成层）
# ---------------------------------------------------------------------------

class TestRetrievalAgentHandlerErrorSafetyIntegration:
    """retrieval_agent_handler 异常安全（集成层）。"""

    def test_empty_payload_no_exception(self):
        """空 payload 时不抛异常，agent_artifacts 仍写入。"""
        from host_brain.app.handlers.retrieval_agent_handler import retrieval_agent_handler
        result = retrieval_agent_handler({})
        assert isinstance(result, dict)
        artifacts = result.get("agent_artifacts") or {}
        assert AGENT_ID_RETRIEVAL in artifacts

    def test_missing_workbench_retrieval_no_exception(self):
        """缺少 workbench_retrieval 时不抛异常，trace 仍写入。"""
        from host_brain.app.handlers.retrieval_agent_handler import retrieval_agent_handler
        result = retrieval_agent_handler({"request_id": "p11-int-err-001"})
        assert "retrieval_agent_trace" in result

    def test_retrieval_and_translate_coexist_no_interference(self):
        """retrieval_agent 和 translate_agent 在同一进程中交替调用不互相干扰。"""
        from host_brain.app.handlers.retrieval_agent_handler import retrieval_agent_handler
        from host_brain.app.handlers.translate_agent_handler import translate_agent_handler

        payload_retrieval = {
            "request_id": "p11-coexist-001",
            "workbench_retrieval": {
                "gateway_status": "OK",
                "payload": {"results": [{"id": "doc-1"}], "summary": "找到结果"},
            },
        }
        gateway = MagicMock()
        gateway.complete.return_value = {
            "content": json.dumps(
                {"thought": "翻译", "action_type": "final_answer", "final_answer": "Hello"},
                ensure_ascii=False,
            )
        }
        payload_translate = {"request_id": "p11-coexist-002", "user_input": "翻译B"}

        result_retrieval = retrieval_agent_handler(payload_retrieval)
        result_translate = translate_agent_handler(payload_translate, context={"gateway": gateway})

        assert result_retrieval["retrieval_agent_trace"]["mode"] == "react_loop"
        assert result_translate["translate_agent_trace"]["mode"] == "react_loop"
        assert "retrieval_agent_trace" not in result_translate
        assert "translate_agent_trace" not in result_retrieval
