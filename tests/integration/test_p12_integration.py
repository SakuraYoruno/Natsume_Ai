"""P12 集成测试：retrieval_agent ReAct 化 + P11 基线不退化。

验收标准覆盖：
  - 3.1：retrieval_agent_handler 走 ReAct 路径，react_scratchpad 非 None，
         mode="react_loop"，artifact_ref 非空
  - 3.2：P11 基线（三图主链 + code_agent/translate_agent/retrieval_agent 路径）不退化
  - 文件完整性：P12 修改文件存在，P11 基线文件不退化
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from host_brain.app.agents.agent_registry import AGENT_ID_RETRIEVAL


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _make_supervised_payload(request_id: str = "p12-int-001") -> dict[str, Any]:
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


def _make_gateway(final_answer: str = "检索完成，找到相关文档") -> MagicMock:
    gateway = MagicMock()
    gateway.complete.return_value = {
        "content": json.dumps({
            "thought": "已完成检索",
            "action_type": "final_answer",
            "final_answer": final_answer,
        }, ensure_ascii=False)
    }
    return gateway


# ---------------------------------------------------------------------------
# P12-1a：retrieval_agent ReAct 路径可运行（验收 3.1）
# ---------------------------------------------------------------------------

class TestRetrievalAgentReActPath:
    """retrieval_agent_handler ReAct 路径集成验证。"""

    def test_handler_runs_react_path(self):
        """retrieval_agent_handler 走 ReAct 路径，不抛异常。"""
        from host_brain.app.handlers.retrieval_agent_handler import retrieval_agent_handler
        payload = _make_supervised_payload()
        payload["_gateway"] = _make_gateway()
        result = retrieval_agent_handler(
            payload,
            context={"request_id": "p12-int-001", "execution_profile": "WORKFLOW_SUPERVISED"},
        )
        assert "retrieval_agent_trace" in result
        assert result["retrieval_agent_trace"]["mode"] == "react_loop"

    def test_react_scratchpad_non_none(self):
        """react_scratchpad 非 None，包含至少一个 step。"""
        from host_brain.app.handlers.retrieval_agent_handler import retrieval_agent_handler
        payload = _make_supervised_payload()
        payload["_gateway"] = _make_gateway()
        result = retrieval_agent_handler(payload)
        scratchpad = result.get("react_scratchpad")
        assert scratchpad is not None, "react_scratchpad 须非 None"
        assert len(scratchpad.get("steps") or []) >= 1

    def test_artifact_ref_non_empty(self):
        """agent_artifacts[retrieval_agent].artifact_ref 非空。"""
        from host_brain.app.handlers.retrieval_agent_handler import retrieval_agent_handler
        payload = _make_supervised_payload()
        payload["_gateway"] = _make_gateway()
        result = retrieval_agent_handler(payload)
        artifacts = result.get("agent_artifacts") or {}
        art = artifacts.get(AGENT_ID_RETRIEVAL)
        assert art is not None
        assert art.get("artifact_ref"), "artifact_ref 不得为空"

    def test_trace_mode_react_loop(self):
        """retrieval_agent_trace.mode 为 react_loop。"""
        from host_brain.app.handlers.retrieval_agent_handler import retrieval_agent_handler
        payload = _make_supervised_payload()
        payload["_gateway"] = _make_gateway()
        result = retrieval_agent_handler(payload)
        trace = result["retrieval_agent_trace"]
        assert trace["mode"] == "react_loop"

    def test_trace_total_steps_ge_1(self):
        """retrieval_agent_trace.total_steps ≥ 1。"""
        from host_brain.app.handlers.retrieval_agent_handler import retrieval_agent_handler
        payload = _make_supervised_payload()
        payload["_gateway"] = _make_gateway()
        result = retrieval_agent_handler(payload)
        trace = result["retrieval_agent_trace"]
        assert trace["total_steps"] >= 1

    def test_trace_stopped_reason_final_answer(self):
        """有 gateway 时 stopped_reason 为 FINAL_ANSWER。"""
        from host_brain.app.handlers.retrieval_agent_handler import retrieval_agent_handler
        payload = _make_supervised_payload()
        payload["_gateway"] = _make_gateway()
        result = retrieval_agent_handler(payload)
        trace = result["retrieval_agent_trace"]
        assert trace["stopped_reason"] == "FINAL_ANSWER"

    def test_trace_contract_all_fields(self):
        """retrieval_agent_trace 包含 P12 §4 全部 8 个字段。"""
        from host_brain.app.handlers.retrieval_agent_handler import retrieval_agent_handler
        payload = _make_supervised_payload()
        payload["_gateway"] = _make_gateway()
        result = retrieval_agent_handler(payload)
        trace = result["retrieval_agent_trace"]
        required = {
            "agent_id", "mode", "artifact_ref",
            "retrieval_source", "result_count",
            "total_steps", "stopped_reason", "ts_ms",
        }
        for field in required:
            assert field in trace, f"trace 缺少字段: {field!r}"

    def test_no_legacy_chain_mode(self):
        """P12 后 mode 不再为 legacy_chain。"""
        from host_brain.app.handlers.retrieval_agent_handler import retrieval_agent_handler
        payload = _make_supervised_payload()
        payload["_gateway"] = _make_gateway()
        result = retrieval_agent_handler(payload)
        trace = result["retrieval_agent_trace"]
        assert trace["mode"] != "legacy_chain"

    def test_does_not_modify_supervisor_decision(self):
        """handler 不修改 supervisor_decision。"""
        from host_brain.app.handlers.retrieval_agent_handler import retrieval_agent_handler
        payload = _make_supervised_payload()
        payload["_gateway"] = _make_gateway()
        original = dict(payload["supervisor_decision"])
        result = retrieval_agent_handler(payload)
        assert result.get("supervisor_decision") == original


# ---------------------------------------------------------------------------
# P12-1b：P11 基线不退化（验收 3.2）
# ---------------------------------------------------------------------------

class TestP11BaselineNotDegraded:
    """P11 基线不退化：P12 改造不破坏 P11 已封板契约。"""

    def test_code_agent_handler_importable(self):
        """P9 code_agent_handler 仍可导入。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler  # noqa: F401

    def test_code_agent_trace_react_mode_still_works(self):
        """P9 code_agent_handler ReAct 模式在 P12 后仍正常工作。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        gateway = _make_gateway("代码草案")
        payload = {"request_id": "p12-p9-baseline-001", "user_input": "写代码"}
        result = code_agent_handler(payload, context={"gateway": gateway})
        trace = result.get("code_agent_trace") or {}
        assert trace.get("mode") == "react_loop"

    def test_translate_agent_handler_importable(self):
        """P10 translate_agent_handler 仍可导入。"""
        from host_brain.app.handlers.translate_agent_handler import translate_agent_handler  # noqa: F401

    def test_translate_agent_trace_react_mode_still_works(self):
        """P10 translate_agent_handler ReAct 模式在 P12 后仍正常工作。"""
        from host_brain.app.handlers.translate_agent_handler import translate_agent_handler
        gateway = _make_gateway("Artificial Intelligence")
        payload = {"request_id": "p12-p10-baseline-001", "user_input": "翻译这段文字"}
        result = translate_agent_handler(payload, context={"gateway": gateway})
        trace = result.get("translate_agent_trace") or {}
        assert trace.get("mode") == "react_loop"

    def test_three_agents_coexist_no_interference(self):
        """code_agent、translate_agent、retrieval_agent 在同一进程中交替调用不互相干扰。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        from host_brain.app.handlers.translate_agent_handler import translate_agent_handler
        from host_brain.app.handlers.retrieval_agent_handler import retrieval_agent_handler

        gw = _make_gateway("结果")
        r1 = code_agent_handler({"request_id": "p12-coexist-001", "user_input": "写代码"}, context={"gateway": gw})
        r2 = translate_agent_handler({"request_id": "p12-coexist-002", "user_input": "翻译"}, context={"gateway": gw})
        r3 = retrieval_agent_handler({"request_id": "p12-coexist-003", "user_input": "检索"}, context={"gateway": gw})

        assert r1.get("code_agent_trace", {}).get("mode") == "react_loop"
        assert r2.get("translate_agent_trace", {}).get("mode") == "react_loop"
        assert r3.get("retrieval_agent_trace", {}).get("mode") == "react_loop"
        # 互不干扰
        assert "translate_agent_trace" not in r1
        assert "retrieval_agent_trace" not in r1
        assert "code_agent_trace" not in r2
        assert "retrieval_agent_trace" not in r2
        assert "code_agent_trace" not in r3
        assert "translate_agent_trace" not in r3

    def test_p11_integration_test_still_importable(self):
        """P11 集成测试模块仍可导入（不退化）。"""
        import tests.integration.test_p11_integration  # noqa: F401

    def test_p10_integration_test_still_importable(self):
        """P10 集成测试模块仍可导入（不退化）。"""
        import tests.integration.test_p10_integration  # noqa: F401

    def test_agent_dispatch_allowlist_unchanged(self):
        """agent_dispatch allowlist 仍包含三个 agent（P12 不改 allowlist）。"""
        from host_brain.app.agents.agent_registry import AGENT_ID_CODE, AGENT_ID_TRANSLATE
        from host_brain.integrations.graph_executor import _AGENT_HANDLER_MAP
        assert AGENT_ID_CODE in _AGENT_HANDLER_MAP
        assert AGENT_ID_TRANSLATE in _AGENT_HANDLER_MAP
        assert AGENT_ID_RETRIEVAL in _AGENT_HANDLER_MAP


# ---------------------------------------------------------------------------
# P12-1c：文件完整性
# ---------------------------------------------------------------------------

def test_p12_modified_files_exist():
    """P12 修改/新增文件存在（基线完整性）。"""
    root = Path(__file__).resolve().parents[2]
    files = [
        root / "host_brain" / "app" / "handlers" / "retrieval_agent_handler.py",
        root / "tests" / "unit" / "test_retrieval_agent_handler.py",
        root / "tests" / "integration" / "test_p12_integration.py",
        root / "docs" / "50_project" / "MULTI_AGENT_P12_SCOPE_2026-03.md",
    ]
    for f in files:
        assert f.is_file(), f"P12 文件须存在: {f}"


def test_p11_baseline_files_still_exist():
    """P11 基线文件在 P12 后仍存在（不退化）。"""
    root = Path(__file__).resolve().parents[2]
    files = [
        root / "tests" / "integration" / "test_p11_integration.py",
        root / "docs" / "50_project" / "MULTI_AGENT_P11_SCOPE_2026-03.md",
        root / "docs" / "50_project" / "MULTI_AGENT_P11_SEAL_RESULT_2026-03.md",
    ]
    for f in files:
        assert f.is_file(), f"P11 基线文件须存在: {f}"


def test_p9_p10_baseline_files_still_exist():
    """P9/P10 基线文件在 P12 后仍存在（不退化）。"""
    root = Path(__file__).resolve().parents[2]
    files = [
        root / "host_brain" / "app" / "handlers" / "code_agent_handler.py",
        root / "host_brain" / "app" / "handlers" / "translate_agent_handler.py",
        root / "host_brain" / "app" / "agents" / "react_loop.py",
        root / "host_brain" / "app" / "agents" / "tool_registry.py",
        root / "tests" / "unit" / "test_code_agent_handler.py",
        root / "tests" / "unit" / "test_translate_agent_handler.py",
        root / "tests" / "integration" / "test_p9_integration.py",
        root / "tests" / "integration" / "test_p10_integration.py",
    ]
    for f in files:
        assert f.is_file(), f"P9/P10 基线文件须存在: {f}"
