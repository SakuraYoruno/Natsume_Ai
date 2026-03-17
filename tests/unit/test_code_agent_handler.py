"""P9 专项单元测试：code_agent_handler ReAct 化验收。

验收标准：
  3.1 code_agent_handler 走 ReAct 循环，字段结构正确
  3.2 code_agent_trace 字段契约固化
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 辅助：构造 mock gateway
# ---------------------------------------------------------------------------

def _make_gateway(response_text: str = "这是代码草案") -> MagicMock:
    """构造一个返回 final_answer 的 mock LLM gateway。"""
    import json
    gateway = MagicMock()
    gateway.complete.return_value = {
        "content": json.dumps(
            {
                "thought": "用户需要代码草案，直接给出答案",
                "action_type": "final_answer",
                "final_answer": response_text,
            },
            ensure_ascii=False,
        )
    }
    return gateway


def _make_gateway_tool_then_answer(tool_name: str = "analyze_code") -> MagicMock:
    """构造先调用工具再给出 final_answer 的 mock gateway（两步）。"""
    import json
    gateway = MagicMock()
    call_count = [0]

    def _complete(messages):
        call_count[0] += 1
        if call_count[0] == 1:
            return {
                "content": json.dumps(
                    {
                        "thought": "先分析代码",
                        "action_type": "tool_call",
                        "action": {
                            "tool": tool_name,
                            "args": {"code": "print('hello')", "language": "python"},
                        },
                    },
                    ensure_ascii=False,
                )
            }
        return {
            "content": json.dumps(
                {
                    "thought": "分析完毕，给出答案",
                    "action_type": "final_answer",
                    "final_answer": "代码分析完成，草案已生成",
                },
                ensure_ascii=False,
            )
        }

    gateway.complete.side_effect = _complete
    return gateway


def _make_payload(user_input: str = "帮我写一个排序函数") -> dict[str, Any]:
    return {
        "request_id": "test-req-p9-001",
        "user_input": user_input,
    }


# ---------------------------------------------------------------------------
# 3.1 code_agent_handler ReAct 循环可运行
# ---------------------------------------------------------------------------

class TestReActMode:
    """验收 3.1：ReAct 模式可运行，字段结构正确。"""

    def test_returns_code_agent_trace(self):
        """code_agent_trace 存在且 mode == 'react_loop'。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        gateway = _make_gateway()
        result = code_agent_handler(
            _make_payload(),
            context={"request_id": "test-req-p9-001", "gateway": gateway},
        )
        assert "code_agent_trace" in result
        trace = result["code_agent_trace"]
        assert trace["mode"] == "react_loop"

    def test_scratchpad_steps_nonempty(self):
        """react_scratchpad.steps 非空（至少 1 步）。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        gateway = _make_gateway()
        result = code_agent_handler(
            _make_payload(),
            context={"gateway": gateway},
        )
        assert "react_scratchpad" in result
        scratchpad = result["react_scratchpad"]
        assert isinstance(scratchpad.get("steps"), list)
        assert len(scratchpad["steps"]) >= 1, "react_scratchpad.steps 至少 1 步"

    def test_stopped_reason_valid(self):
        """stopped_reason 为有效值之一。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        gateway = _make_gateway()
        result = code_agent_handler(
            _make_payload(),
            context={"gateway": gateway},
        )
        trace = result["code_agent_trace"]
        valid_reasons = {"FINAL_ANSWER", "MAX_STEPS", "ERROR", "NO_LLM"}
        assert trace["stopped_reason"] in valid_reasons

    def test_final_answer_stopped_reason(self):
        """gateway 返回 final_answer 时 stopped_reason == 'FINAL_ANSWER'。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        gateway = _make_gateway("这是最终代码草案")
        result = code_agent_handler(
            _make_payload(),
            context={"gateway": gateway},
        )
        assert result["code_agent_trace"]["stopped_reason"] == "FINAL_ANSWER"

    def test_artifact_ref_nonempty(self):
        """artifact_ref 非空且以 artifact:// 开头。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        gateway = _make_gateway()
        result = code_agent_handler(
            _make_payload(),
            context={"gateway": gateway},
        )
        trace = result["code_agent_trace"]
        assert trace.get("artifact_ref", "").startswith("artifact://")

    def test_agent_artifacts_written(self):
        """agent_artifacts[code_agent] 存在。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        from host_brain.app.agents.agent_registry import AGENT_ID_CODE
        gateway = _make_gateway()
        result = code_agent_handler(
            _make_payload(),
            context={"gateway": gateway},
        )
        artifacts = result.get("agent_artifacts") or {}
        assert AGENT_ID_CODE in artifacts
        assert artifacts[AGENT_ID_CODE].get("artifact_ref")

    def test_tool_call_then_answer(self):
        """先调用工具再给出 final_answer 时，steps >= 2，stopped_reason == 'FINAL_ANSWER'。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        gateway = _make_gateway_tool_then_answer("analyze_code")
        result = code_agent_handler(
            _make_payload("分析这段代码：print('hello')"),
            context={"gateway": gateway},
        )
        scratchpad = result.get("react_scratchpad") or {}
        steps = scratchpad.get("steps") or []
        assert len(steps) >= 2, "工具调用 + final_answer 至少 2 步"
        trace = result["code_agent_trace"]
        assert trace["stopped_reason"] == "FINAL_ANSWER"
        assert trace["total_steps"] >= 2

    def test_max_steps_reached(self):
        """gateway 持续返回 tool_call 时，达到 max_steps 后停止，stopped_reason == 'MAX_STEPS'。"""
        import json
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        gateway = MagicMock()
        gateway.complete.return_value = {
            "content": json.dumps(
                {
                    "thought": "继续调用工具",
                    "action_type": "tool_call",
                    "action": {
                        "tool": "analyze_code",
                        "args": {"code": "x=1", "language": "python"},
                    },
                },
                ensure_ascii=False,
            )
        }
        with patch.dict(os.environ, {"CODE_AGENT_REACT_MAX_STEPS": "2"}):
            result = code_agent_handler(
                _make_payload(),
                context={"gateway": gateway},
            )
        trace = result["code_agent_trace"]
        assert trace["stopped_reason"] == "MAX_STEPS"
        assert trace["total_steps"] == 2

    def test_no_gateway_degrades_gracefully(self):
        """无 gateway 时降级为 NO_LLM，不抛异常，artifact_ref 仍存在。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        result = code_agent_handler(
            _make_payload(),
            context={},
        )
        trace = result.get("code_agent_trace") or {}
        assert trace.get("stopped_reason") == "NO_LLM"
        assert trace.get("artifact_ref", "").startswith("artifact://")

    def test_only_readonly_tools(self):
        """code_agent 只读工具集不含有副作用工具。"""
        from host_brain.app.agents.agent_registry import AGENT_ID_CODE
        from host_brain.app.agents.tool_registry import get_tools_for_agent
        tools = get_tools_for_agent(AGENT_ID_CODE, allow_side_effects=False)
        for tool in tools:
            assert not tool.has_side_effects
        tool_ids = {t.tool_id for t in tools}
        assert "analyze_code" in tool_ids
        assert "search_knowledge" in tool_ids


# ---------------------------------------------------------------------------
# 3.2 code_agent_trace 字段契约固化
# ---------------------------------------------------------------------------

class TestCodeAgentTraceContract:
    """验收 3.2：code_agent_trace 字段契约固化。"""

    REQUIRED_FIELDS = {
        "agent_id", "mode", "artifact_ref",
        "total_steps", "stopped_reason", "ts_ms",
    }

    def test_trace_has_all_required_fields(self):
        """code_agent_trace 包含所有必填字段。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        gateway = _make_gateway()
        result = code_agent_handler(
            {"request_id": "p9-contract-001", "user_input": "写代码"},
            context={"gateway": gateway},
        )
        trace = result.get("code_agent_trace") or {}
        for field in self.REQUIRED_FIELDS:
            assert field in trace, f"code_agent_trace 缺少必填字段: {field!r}"

    def test_trace_field_types(self):
        """code_agent_trace 字段类型正确。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        gateway = _make_gateway()
        result = code_agent_handler(
            {"request_id": "p9-contract-002", "user_input": "写代码"},
            context={"gateway": gateway},
        )
        trace = result["code_agent_trace"]
        assert isinstance(trace["agent_id"], str)
        assert trace["mode"] == "react_loop"
        assert isinstance(trace["artifact_ref"], str) and trace["artifact_ref"]
        assert isinstance(trace["total_steps"], int) and trace["total_steps"] >= 1
        assert trace["stopped_reason"] in {"FINAL_ANSWER", "MAX_STEPS", "ERROR", "NO_LLM"}
        assert isinstance(trace["ts_ms"], int) and trace["ts_ms"] > 0

    def test_trace_agent_id_is_code_agent(self):
        """agent_id 固定为 'code_agent'。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        from host_brain.app.agents.agent_registry import AGENT_ID_CODE
        gateway = _make_gateway()
        result = code_agent_handler(
            {"request_id": "p9-contract-003", "user_input": "写代码"},
            context={"gateway": gateway},
        )
        assert result["code_agent_trace"]["agent_id"] == AGENT_ID_CODE

    def test_trace_no_react_enabled_field(self):
        """code_agent_trace 不含 react_enabled 字段（已删除冗余字段）。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        gateway = _make_gateway()
        result = code_agent_handler(
            {"request_id": "p9-contract-004", "user_input": "写代码"},
            context={"gateway": gateway},
        )
        assert "react_enabled" not in result["code_agent_trace"]

    def test_trace_no_gateway_status_field(self):
        """code_agent_trace 不含 gateway_status 字段（ReAct 模式无此字段）。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        gateway = _make_gateway()
        result = code_agent_handler(
            {"request_id": "p9-contract-005", "user_input": "写代码"},
            context={"gateway": gateway},
        )
        assert "gateway_status" not in result["code_agent_trace"]
