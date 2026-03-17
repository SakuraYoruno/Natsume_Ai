"""P10 集成测试：translate_agent ReAct 化后三图主链与 P9 基线不退化。

验收标准（集成层）：
  - Supervised Graph 主链含 translate_agent ReAct 模式不退化
  - TRANSLATE_AGENT_REACT_ENABLED=true 时 translate_agent_handler 可在 Supervised 图上下文中运行
  - TRANSLATE_AGENT_REACT_ENABLED=false 时行为与 P9 基线完全一致
  - P9 基线测试全部通过（不退化）
  - P10 新增文件存在（基线完整性）
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _make_gateway(final_answer: str = "翻译草案已生成") -> MagicMock:
    """构造返回 final_answer 的 mock LLM gateway。"""
    gateway = MagicMock()
    gateway.complete.return_value = {
        "content": json.dumps(
            {
                "thought": "直接给出翻译草案",
                "action_type": "final_answer",
                "final_answer": final_answer,
            },
            ensure_ascii=False,
        )
    }
    return gateway


# ---------------------------------------------------------------------------
# P10-1a：translate_agent_handler 在 Supervised 图上下文中可运行（ReAct 模式）
# ---------------------------------------------------------------------------

class TestTranslateAgentInSupervisedContext:
    """translate_agent_handler 在 Supervised 图上下文中的集成行为。"""

    def _make_supervised_payload(self) -> dict[str, Any]:
        """模拟 Supervised 图中 agent_dispatch 后传入 translate_agent_handler 的 payload。"""
        return {
            "request_id": "p10-int-001",
            "execution_profile": "WORKFLOW_SUPERVISED",
            "user_input": "请将以下内容翻译成英文：人工智能",
            "scene": "SCENE_WORK_TRANSLATE",
            "supervisor_decision": {
                "selected_agent": "translate_agent",
                "decision_source": "rule",
                "proposal": {"agent_id": "translate_agent", "confidence": 0.9},
            },
        }

    def test_react_mode_runs_in_supervised_context(self):
        """ReAct 模式在 Supervised 图上下文中可正常运行，不抛异常。"""
        from host_brain.app.handlers.translate_agent_handler import translate_agent_handler
        gateway = _make_gateway()
        with patch.dict(os.environ, {"TRANSLATE_AGENT_REACT_ENABLED": "true"}):
            result = translate_agent_handler(
                self._make_supervised_payload(),
                context={
                    "request_id": "p10-int-001",
                    "gateway": gateway,
                    "execution_profile": "WORKFLOW_SUPERVISED",
                },
            )
        assert "translate_agent_trace" in result
        assert result["translate_agent_trace"]["mode"] == "react_loop"

    def test_legacy_mode_runs_in_supervised_context(self):
        """Legacy 模式在 Supervised 图上下文中可正常运行，不抛异常。"""
        from host_brain.app.handlers.translate_agent_handler import translate_agent_handler
        payload = self._make_supervised_payload()
        payload["workbench_translate"] = {
            "gateway_status": "OK",
            "payload": {"translation": "Artificial Intelligence"},
        }
        with patch.dict(os.environ, {"TRANSLATE_AGENT_REACT_ENABLED": "false"}):
            result = translate_agent_handler(payload)
        assert "translate_agent_trace" in result
        assert result["translate_agent_trace"]["mode"] == "legacy_chain"

    def test_react_mode_does_not_modify_supervisor_decision(self):
        """ReAct 模式不修改 supervisor_decision 字段（不破坏 Supervised 图主链）。"""
        from host_brain.app.handlers.translate_agent_handler import translate_agent_handler
        gateway = _make_gateway()
        payload = self._make_supervised_payload()
        original_decision = dict(payload["supervisor_decision"])
        with patch.dict(os.environ, {"TRANSLATE_AGENT_REACT_ENABLED": "true"}):
            result = translate_agent_handler(
                payload,
                context={"gateway": gateway},
            )
        assert result.get("supervisor_decision") == original_decision, (
            "translate_agent_handler 不得修改 supervisor_decision"
        )

    def test_react_mode_does_not_modify_execution_profile(self):
        """ReAct 模式不修改 execution_profile 字段。"""
        from host_brain.app.handlers.translate_agent_handler import translate_agent_handler
        gateway = _make_gateway()
        payload = self._make_supervised_payload()
        with patch.dict(os.environ, {"TRANSLATE_AGENT_REACT_ENABLED": "true"}):
            result = translate_agent_handler(
                payload,
                context={"gateway": gateway},
            )
        assert result.get("execution_profile") == "WORKFLOW_SUPERVISED", (
            "translate_agent_handler 不得修改 execution_profile"
        )

    def test_react_mode_artifact_ref_contains_request_id(self):
        """ReAct 模式 artifact_ref 包含 request_id。"""
        from host_brain.app.handlers.translate_agent_handler import translate_agent_handler
        gateway = _make_gateway()
        with patch.dict(os.environ, {"TRANSLATE_AGENT_REACT_ENABLED": "true"}):
            result = translate_agent_handler(
                self._make_supervised_payload(),
                context={"request_id": "p10-int-001", "gateway": gateway},
            )
        trace = result["translate_agent_trace"]
        assert "p10-int-001" in trace["artifact_ref"], (
            "artifact_ref 应包含 request_id"
        )


# ---------------------------------------------------------------------------
# P10-1b：P9 基线不退化（P10 开工后 P9 集成测试仍通过）
# ---------------------------------------------------------------------------

class TestP9BaselineNotDegraded:
    """P9 基线不退化：P10 新增代码不破坏 P9 已封板契约。"""

    def test_code_agent_handler_importable(self):
        """P9 code_agent_handler 仍可导入。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler  # noqa: F401

    def test_code_agent_trace_legacy_mode_still_works(self):
        """P9 code_agent_handler legacy 模式在 P10 后仍正常工作。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        payload = {
            "request_id": "p10-p9-baseline-001",
            "user_input": "写代码",
            "workbench_code_assist": {
                "gateway_status": "OK",
                "payload": {"code_draft": "def foo(): pass"},
            },
        }
        with patch.dict(os.environ, {"CODE_AGENT_REACT_ENABLED": "false"}):
            result = code_agent_handler(payload)
        trace = result.get("code_agent_trace") or {}
        assert trace.get("mode") == "legacy_chain"
        assert trace.get("react_enabled") is False

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

    def test_p8_switch_off_no_p8_fields(self):
        """P8 开关关闭时 collect() 不含 P8 字段（P10 后仍成立）。"""
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
        p8_fields = [
            "subgraph_failure_metrics", "local_loop_failure_metrics",
            "intent_budget_breach_metrics", "graph_latency_metrics",
        ]
        for f in p8_fields:
            assert f not in result, f"P8 开关关闭时不得出现字段 {f!r}"

    def test_p7_fields_still_present_after_p10(self):
        """P7 核心字段在 P10 后仍存在（不退化）。"""
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
            assert field in result, f"P7 字段 {field!r} 在 P10 后不得消失"


# ---------------------------------------------------------------------------
# P10-1c：P10 新增文件存在（基线完整性）
# ---------------------------------------------------------------------------

def test_p10_new_files_exist():
    """P10 新增/修改文件存在（基线完整性）。"""
    root = Path(__file__).resolve().parents[2]
    files = [
        root / "tests" / "unit" / "test_translate_agent_handler.py",
        root / "tests" / "integration" / "test_p10_integration.py",
        root / "host_brain" / "app" / "handlers" / "translate_agent_handler.py",
        root / "docs" / "50_project" / "MULTI_AGENT_P10_SCOPE_2026-03.md",
    ]
    for f in files:
        assert f.is_file(), f"P10 文件须存在: {f}"


def test_p9_baseline_files_still_exist():
    """P9 实施文件在 P10 后仍存在（不退化）。"""
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


def test_p10_scope_doc_exists():
    """P10 SCOPE 定稿文档存在。"""
    root = Path(__file__).resolve().parents[2]
    doc = root / "docs" / "50_project" / "MULTI_AGENT_P10_SCOPE_2026-03.md"
    assert doc.is_file(), "MULTI_AGENT_P10_SCOPE_2026-03.md 须存在"


# ---------------------------------------------------------------------------
# P10-1d：translate_agent_handler 异常安全（集成层）
# ---------------------------------------------------------------------------

class TestTranslateAgentHandlerErrorSafety:
    """translate_agent_handler 异常安全：任何情况下不抛异常，agent_artifacts 始终写入。"""

    def test_react_mode_gateway_raises_exception(self):
        """gateway.complete 抛异常时，handler 降级处理，不向上抛。"""
        from host_brain.app.handlers.translate_agent_handler import translate_agent_handler
        from host_brain.app.agents.agent_registry import AGENT_ID_TRANSLATE
        gateway = MagicMock()
        gateway.complete.side_effect = RuntimeError("LLM 服务不可用")
        with patch.dict(os.environ, {"TRANSLATE_AGENT_REACT_ENABLED": "true"}):
            result = translate_agent_handler(
                {"request_id": "p10-err-001", "user_input": "翻译这段文字"},
                context={"gateway": gateway},
            )
        # 不抛异常，agent_artifacts 仍写入
        artifacts = result.get("agent_artifacts") or {}
        assert AGENT_ID_TRANSLATE in artifacts, "异常时 agent_artifacts[translate_agent] 仍须存在"

    def test_react_mode_empty_payload(self):
        """空 payload 时 handler 不抛异常。"""
        from host_brain.app.handlers.translate_agent_handler import translate_agent_handler
        gateway = _make_gateway()
        with patch.dict(os.environ, {"TRANSLATE_AGENT_REACT_ENABLED": "true"}):
            result = translate_agent_handler({}, context={"gateway": gateway})
        assert isinstance(result, dict)

    def test_legacy_mode_missing_workbench_output(self):
        """legacy 模式缺少 workbench_translate 时不抛异常。"""
        from host_brain.app.handlers.translate_agent_handler import translate_agent_handler
        with patch.dict(os.environ, {"TRANSLATE_AGENT_REACT_ENABLED": "false"}):
            result = translate_agent_handler(
                {"request_id": "p10-err-002", "user_input": "翻译这段文字"}
            )
        assert "translate_agent_trace" in result

    def test_react_and_legacy_coexist_no_interference(self):
        """ReAct 模式和 legacy 模式在同一进程中交替调用不互相干扰。"""
        from host_brain.app.handlers.translate_agent_handler import translate_agent_handler
        gateway = _make_gateway()
        payload_react = {"request_id": "p10-coexist-001", "user_input": "翻译A"}
        payload_legacy = {
            "request_id": "p10-coexist-002",
            "user_input": "翻译B",
            "workbench_translate": {
                "gateway_status": "OK",
                "payload": {"translation": "Translate B"},
            },
        }
        with patch.dict(os.environ, {"TRANSLATE_AGENT_REACT_ENABLED": "true"}):
            result_react = translate_agent_handler(payload_react, context={"gateway": gateway})
        with patch.dict(os.environ, {"TRANSLATE_AGENT_REACT_ENABLED": "false"}):
            result_legacy = translate_agent_handler(payload_legacy)
        assert result_react["translate_agent_trace"]["mode"] == "react_loop"
        assert result_legacy["translate_agent_trace"]["mode"] == "legacy_chain"
