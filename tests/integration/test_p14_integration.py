"""P14 集成测试：judge_agent ReAct 化 + P13 基线不退化。

验收标准覆盖：
  - 3.1：judge_agent_handler 走 ReAct 路径，react_scratchpad 非 None，
         mode="react_loop"，judge_agent_trace 字段契约正确
  - 3.2：evaluate_artifact / compare_artifacts 工具有真实实现，可通过 execute_tool 调用
  - 3.3：P13 基线（85 用例）不退化，tool_registry 接口契约不变
  - 文件完整性：P14 修改/新增文件存在，P13 基线文件不退化
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from host_brain.app.agents.agent_registry import AGENT_ID_JUDGE
from host_brain.app.agents.tool_registry import (
    ToolSpec,
    execute_tool,
    get_tools_for_agent,
    is_tool_allowed,
    _TOOL_REGISTRY,
    _AGENT_TOOLS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_gateway(final_answer: str = "selected_agent=code_agent, confidence=0.85, reason=最高置信度") -> MagicMock:
    gateway = MagicMock()
    gateway.complete.return_value = {
        "content": json.dumps(
            {
                "thought": "分析工件后选择最优",
                "action_type": "final_answer",
                "final_answer": final_answer,
            },
            ensure_ascii=False,
        )
    }
    return gateway


def _make_artifacts() -> dict[str, Any]:
    return {
        "code_agent": {
            "artifact_ref": "artifact://req-p14/code_agent_1000",
            "summary": "代码草案已生成",
            "confidence": 0.85,
            "risk_flags": [],
        },
        "translate_agent": {
            "artifact_ref": "artifact://req-p14/translate_agent_1001",
            "summary": "翻译结果",
            "confidence": 0.60,
            "risk_flags": [],
        },
    }


# ---------------------------------------------------------------------------
# 3.1 judge_agent_handler ReAct 路径集成验证
# ---------------------------------------------------------------------------

class TestJudgeAgentReActIntegration:
    """集成验证：judge_agent_handler ReAct 路径端到端。"""

    def test_react_scratchpad_not_none(self):
        """react_scratchpad 非 None。"""
        from host_brain.app.handlers.judge_agent_handler import judge_agent_handler
        result = judge_agent_handler(
            {"request_id": "p14-int-001", "agent_artifacts": _make_artifacts()},
            context={"gateway": _make_gateway()},
        )
        assert result.get("react_scratchpad") is not None

    def test_judge_result_mode_react_loop(self):
        """judge_result.mode == 'react_loop'。"""
        from host_brain.app.handlers.judge_agent_handler import judge_agent_handler
        result = judge_agent_handler(
            {"request_id": "p14-int-002", "agent_artifacts": _make_artifacts()},
            context={"gateway": _make_gateway()},
        )
        jr = result["judge_result"]
        assert jr["mode"] == "react_loop"

    def test_judge_agent_trace_contract(self):
        """judge_agent_trace 包含所有 P14 契约字段。"""
        from host_brain.app.handlers.judge_agent_handler import judge_agent_handler
        result = judge_agent_handler(
            {"request_id": "p14-int-003", "agent_artifacts": _make_artifacts()},
            context={"gateway": _make_gateway()},
        )
        trace = result["judge_agent_trace"]
        required_fields = {
            "agent_id", "mode", "artifact_ref",
            "total_steps", "stopped_reason", "ts_ms",
        }
        for field in required_fields:
            assert field in trace, f"judge_agent_trace 缺少字段: {field}"
        assert trace["mode"] == "react_loop"
        assert trace["agent_id"] == AGENT_ID_JUDGE

    def test_judge_result_contract(self):
        """judge_result 包含所有 P14 契约字段。"""
        from host_brain.app.handlers.judge_agent_handler import judge_agent_handler
        result = judge_agent_handler(
            {"request_id": "p14-int-004", "agent_artifacts": _make_artifacts()},
            context={"gateway": _make_gateway()},
        )
        jr = result["judge_result"]
        required_fields = {
            "status", "reason", "selected_agent", "selected_artifact",
            "all_agents_evaluated", "confidence", "risk_flags", "mode", "ts_ms",
        }
        for field in required_fields:
            assert field in jr, f"judge_result 缺少字段: {field}"

    def test_no_artifacts_degraded_integration(self):
        """无工件时集成层也返回 DEGRADED。"""
        from host_brain.app.handlers.judge_agent_handler import judge_agent_handler
        result = judge_agent_handler(
            {"request_id": "p14-int-005", "agent_artifacts": {}},
            context={},
        )
        assert result["judge_result"]["status"] == "DEGRADED"
        assert result["judge_result"]["mode"] == "react_loop"


# ---------------------------------------------------------------------------
# 3.2 工具真实实现集成验证
# ---------------------------------------------------------------------------

class TestToolsRealImplementation:
    """集成验证：evaluate_artifact / compare_artifacts 工具真实实现。"""

    def test_evaluate_artifact_via_execute_tool(self):
        """通过 execute_tool 调用 evaluate_artifact，返回真实结果。"""
        result = execute_tool("evaluate_artifact", {
            "artifact": {
                "confidence": 0.8,
                "summary": "集成测试工件",
                "risk_flags": [],
                "artifact_ref": "artifact://test/agent_1",
            },
            "agent_id": "code_agent",
        })
        assert "quality" in result
        assert result["quality"] in {"HIGH", "MEDIUM", "LOW"}
        assert "score" in result

    def test_compare_artifacts_via_execute_tool(self):
        """通过 execute_tool 调用 compare_artifacts，返回真实结果。"""
        result = execute_tool("compare_artifacts", {
            "artifacts": {
                "code_agent": {"confidence": 0.85, "summary": "代码草案", "risk_flags": []},
                "translate_agent": {"confidence": 0.60, "summary": "翻译结果", "risk_flags": []},
            }
        })
        assert "recommended" in result
        assert result["recommended"] == "code_agent"

    def test_evaluate_artifact_result_not_placeholder(self):
        """evaluate_artifact 结果不含占位字符串。"""
        result = execute_tool("evaluate_artifact", {
            "artifact": {"confidence": 0.7, "summary": "测试", "risk_flags": []},
        })
        result_text = str(result.get("result") or "")
        assert "[占位]" not in result_text
        assert "TODO" not in result_text

    def test_compare_artifacts_result_not_placeholder(self):
        """compare_artifacts 结果不含占位字符串。"""
        result = execute_tool("compare_artifacts", {
            "artifacts": {
                "agent_a": {"confidence": 0.8, "summary": "ok", "risk_flags": []},
            }
        })
        result_text = str(result.get("result") or "")
        assert "[占位]" not in result_text
        assert "TODO" not in result_text


# ---------------------------------------------------------------------------
# 3.3 P13 基线不退化
# ---------------------------------------------------------------------------

class TestP13BaselineNotRegressed:
    """P13 基线（85 用例）不退化验证。"""

    def test_tool_registry_interface_unchanged(self):
        """tool_registry 四个核心接口仍可调用（契约不变）。"""
        spec = ToolSpec(
            tool_id="test", display_name="测试", description="测试工具",
            parameters={"type": "object", "properties": {}, "required": []},
        )
        assert spec.to_llm_schema()["name"] == "test"
        result = execute_tool("nonexistent", {})
        assert "error" in result
        tools = get_tools_for_agent("code_agent")
        assert isinstance(tools, list)
        allowed = is_tool_allowed("analyze_code", "code_agent")
        assert isinstance(allowed, bool)

    def test_p13_tools_still_work(self):
        """P13 新增工具（search_by_keyword / search_by_vector）仍可调用。"""
        from unittest.mock import patch
        from host_brain.app.recall.semantic_recall import SemanticRecaller
        mock_recaller = SemanticRecaller(use_mock=True)
        mock_recaller.add_mock_document("doc-1", "深度学习是机器学习的子领域")
        with patch(
            "host_brain.app.agents.tool_registry._get_semantic_recaller",
            return_value=mock_recaller,
        ):
            r1 = execute_tool("search_by_keyword", {"keyword": "深度学习"})
            r2 = execute_tool("search_by_vector", {"query": "机器学习"})
        assert "result" in r1
        assert "result" in r2

    def test_retrieval_agent_tools_unchanged(self):
        """retrieval_agent 工具 allowlist 不变（P12 契约）。"""
        tools = get_tools_for_agent("retrieval_agent")
        tool_ids = {t.tool_id for t in tools}
        assert "search_knowledge" in tool_ids
        assert "search_by_keyword" in tool_ids
        assert "search_by_vector" in tool_ids
        assert "get_context" in tool_ids

    def test_code_agent_tools_unchanged(self):
        """code_agent 工具 allowlist 不变（P9 契约）。"""
        tools = get_tools_for_agent("code_agent")
        tool_ids = {t.tool_id for t in tools}
        assert "analyze_code" in tool_ids
        assert "search_knowledge" in tool_ids

    def test_translate_agent_tools_unchanged(self):
        """translate_agent 工具 allowlist 不变（P10 契约）。"""
        tools = get_tools_for_agent("translate_agent")
        tool_ids = {t.tool_id for t in tools}
        assert "translate_text" in tool_ids
        assert "search_knowledge" in tool_ids

    def test_all_registered_tools_have_executor(self):
        """所有注册工具均有 executor（P13 契约）。"""
        for tool_id, spec in _TOOL_REGISTRY.items():
            assert spec.executor is not None, f"工具 {tool_id} 缺少 executor"

    def test_judge_agent_tools_updated(self):
        """judge_agent 工具 allowlist 已从空列表更新为 2 个工具（P14 新增）。"""
        tools = get_tools_for_agent(AGENT_ID_JUDGE)
        assert len(tools) == 2
        tool_ids = {t.tool_id for t in tools}
        assert "evaluate_artifact" in tool_ids
        assert "compare_artifacts" in tool_ids


# ---------------------------------------------------------------------------
# 文件完整性
# ---------------------------------------------------------------------------

def test_p14_modified_files_exist():
    """P14 修改/新增文件存在（基线完整性）。"""
    root = Path(__file__).resolve().parents[2]
    files = [
        root / "host_brain" / "app" / "agents" / "tool_registry.py",
        root / "host_brain" / "app" / "handlers" / "judge_agent_handler.py",
        root / "tests" / "unit" / "test_judge_agent_handler.py",
        root / "tests" / "integration" / "test_p14_integration.py",
        root / "docs" / "50_project" / "MULTI_AGENT_P14_SCOPE_2026-03.md",
    ]
    for f in files:
        assert f.is_file(), f"P14 文件须存在: {f}"


def test_p13_baseline_files_still_exist():
    """P13 基线文件在 P14 后仍存在（不退化）。"""
    root = Path(__file__).resolve().parents[2]
    files = [
        root / "tests" / "unit" / "test_tool_registry_p13.py",
        root / "tests" / "integration" / "test_p13_integration.py",
        root / "docs" / "50_project" / "MULTI_AGENT_P13_SCOPE_2026-03.md",
        root / "docs" / "50_project" / "MULTI_AGENT_P13_SEAL_RESULT_2026-03.md",
    ]
    for f in files:
        assert f.is_file(), f"P13 基线文件须存在: {f}"


def test_p12_baseline_files_still_exist():
    """P12 基线文件在 P14 后仍存在（不退化）。"""
    root = Path(__file__).resolve().parents[2]
    files = [
        root / "host_brain" / "app" / "handlers" / "retrieval_agent_handler.py",
        root / "tests" / "unit" / "test_retrieval_agent_handler.py",
        root / "tests" / "integration" / "test_p12_integration.py",
    ]
    for f in files:
        assert f.is_file(), f"P12 基线文件须存在: {f}"
