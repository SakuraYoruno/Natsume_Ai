"""P10 专项单元测试：translate_agent_handler ReAct 化验收。

验收标准：
  3.1 translate_agent_handler 走 ReAct 循环，字段结构正确
  3.2 translate_agent_trace 字段契约固化
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 辅助：构造 mock gateway
# ---------------------------------------------------------------------------

def _make_gateway(response_text: str = "这是翻译草案") -> MagicMock:
    """构造一个返回 final_answer 的 mock LLM gateway。"""
    import json
    gateway = MagicMock()
    gateway.complete.return_value = {
        "content": json.dumps(
            {
                "thought": "用户需要翻译草案，直接给出答案",
                "action_type": "final_answer",
                "final_answer": response_text,
            },
            ensure_ascii=False,
        )
    }
    return gateway


def _make_gateway_tool_then_answer(tool_name: str = "search_knowledge") -> MagicMock:
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
                        "thought": "先查询相关术语",
                        "action_type": "tool_call",
                        "action": {
                            "tool": tool_name,
                            "args": {"query": "机器学习术语翻译"},
                        },
                    },
                    ensure_ascii=False,
                )
            }
        return {
            "content": json.dumps(
                {
                    "thought": "查询完毕，给出翻译草案",
                    "action_type": "final_answer",
                    "final_answer": "翻译草案已生成（含术语参考）",
                },
                ensure_ascii=False,
            )
        }

    gateway.complete.side_effect = _complete
    return gateway


def _make_payload(user_input: str = "请将以下内容翻译成英文：人工智能") -> dict[str, Any]:
    return {
        "request_id": "test-req-p10-001",
        "user_input": user_input,
    }


# ---------------------------------------------------------------------------
# 3.1 translate_agent_handler ReAct 循环可运行
# ---------------------------------------------------------------------------

class TestReActMode:
    """验收 3.1：ReAct 模式可运行，字段结构正确。"""

    def test_returns_translate_agent_trace(self):
        """translate_agent_trace 存在且 mode == 'react_loop'。"""
        from host_brain.app.handlers.translate_agent_handler import translate_agent_handler
        gateway = _make_gateway()
        result = translate_agent_handler(
            _make_payload(),
            context={"request_id": "test-req-p10-001", "gateway": gateway},
        )
        assert "translate_agent_trace" in result
        assert result["translate_agent_trace"]["mode"] == "react_loop"

    def test_scratchpad_steps_nonempty(self):
        """react_scratchpad.steps 非空（至少 1 步）。"""
        from host_brain.app.handlers.translate_agent_handler import translate_agent_handler
        gateway = _make_gateway()
        result = translate_agent_handler(
            _make_payload(),
            context={"gateway": gateway},
        )
        assert "react_scratchpad" in result
        scratchpad = result["react_scratchpad"]
        assert isinstance(scratchpad.get("steps"), list)
        assert len(scratchpad["steps"]) >= 1

    def test_react_prompt_receives_agent_context_envelope(self):
        """ReAct prompt receives bounded AgentContextEnvelope instead of raw context."""
        from host_brain.app.handlers.translate_agent_handler import translate_agent_handler

        gateway = _make_gateway()
        translate_agent_handler(
            _make_payload("翻译刚才那段"),
            context={
                "request_id": "test-req-p10-envelope",
                "gateway": gateway,
                "agent_context_envelope": {
                    "schema_version": "Natsume.sub_agent.context.v1",
                    "task_brief": "翻译刚才那段",
                    "memory_packet": {"facts": ["长期记忆：用户偏好翻译保留术语表。"]},
                    "forbidden_context": ["raw_history_not_available"],
                },
                "recent_turns": [{"role": "user", "content": "raw should not be read directly"}],
            },
        )

        messages = gateway.complete.call_args[0][0]
        rendered = "\n".join(str(item.get("content") or "") for item in messages)
        assert "AgentContextEnvelope" in rendered
        assert "用户偏好翻译保留术语表" in rendered
        assert "raw_history_not_available" in rendered
        assert "raw should not be read directly" not in rendered

    def test_stopped_reason_valid(self):
        """stopped_reason 为有效值之一。"""
        from host_brain.app.handlers.translate_agent_handler import translate_agent_handler
        gateway = _make_gateway()
        result = translate_agent_handler(
            _make_payload(),
            context={"gateway": gateway},
        )
        trace = result["translate_agent_trace"]
        valid_reasons = {"FINAL_ANSWER", "MAX_STEPS", "ERROR", "NO_LLM"}
        assert trace["stopped_reason"] in valid_reasons

    def test_final_answer_stopped_reason(self):
        """gateway 返回 final_answer 时 stopped_reason == 'FINAL_ANSWER'。"""
        from host_brain.app.handlers.translate_agent_handler import translate_agent_handler
        gateway = _make_gateway("这是最终翻译草案")
        result = translate_agent_handler(
            _make_payload(),
            context={"gateway": gateway},
        )
        assert result["translate_agent_trace"]["stopped_reason"] == "FINAL_ANSWER"

    def test_artifact_ref_nonempty(self):
        """artifact_ref 非空且以 artifact:// 开头。"""
        from host_brain.app.handlers.translate_agent_handler import translate_agent_handler
        gateway = _make_gateway()
        result = translate_agent_handler(
            _make_payload(),
            context={"gateway": gateway},
        )
        trace = result["translate_agent_trace"]
        assert trace.get("artifact_ref", "").startswith("artifact://")

    def test_agent_artifacts_written(self):
        """agent_artifacts[translate_agent] 存在。"""
        from host_brain.app.handlers.translate_agent_handler import translate_agent_handler
        from host_brain.app.agents.agent_registry import AGENT_ID_TRANSLATE
        gateway = _make_gateway()
        result = translate_agent_handler(
            _make_payload(),
            context={"gateway": gateway},
        )
        artifacts = result.get("agent_artifacts") or {}
        assert AGENT_ID_TRANSLATE in artifacts
        assert artifacts[AGENT_ID_TRANSLATE].get("artifact_ref")

    def test_tool_call_then_answer(self):
        """先调用工具再给出 final_answer 时，steps >= 2，stopped_reason == 'FINAL_ANSWER'。"""
        from host_brain.app.handlers.translate_agent_handler import translate_agent_handler
        gateway = _make_gateway_tool_then_answer("search_knowledge")
        result = translate_agent_handler(
            _make_payload("请翻译机器学习相关术语"),
            context={"gateway": gateway},
        )
        scratchpad = result.get("react_scratchpad") or {}
        steps = scratchpad.get("steps") or []
        assert len(steps) >= 2
        trace = result["translate_agent_trace"]
        assert trace["stopped_reason"] == "FINAL_ANSWER"
        assert trace["total_steps"] >= 2

    def test_max_steps_reached(self):
        """gateway 持续返回 tool_call 时，达到 max_steps 后停止，stopped_reason == 'MAX_STEPS'。"""
        import json
        from host_brain.app.handlers.translate_agent_handler import translate_agent_handler
        gateway = MagicMock()
        gateway.complete.return_value = {
            "content": json.dumps(
                {
                    "thought": "继续查询术语",
                    "action_type": "tool_call",
                    "action": {"tool": "search_knowledge", "args": {"query": "翻译术语"}},
                },
                ensure_ascii=False,
            )
        }
        with patch.dict(os.environ, {"TRANSLATE_AGENT_REACT_MAX_STEPS": "2"}):
            result = translate_agent_handler(
                _make_payload(),
                context={"gateway": gateway},
            )
        trace = result["translate_agent_trace"]
        assert trace["stopped_reason"] == "MAX_STEPS"
        assert trace["total_steps"] == 2

    def test_no_gateway_degrades_gracefully(self):
        """无 gateway 时降级为 NO_LLM，不抛异常，artifact_ref 仍存在。"""
        from host_brain.app.handlers.translate_agent_handler import translate_agent_handler
        result = translate_agent_handler(
            _make_payload(),
            context={},
        )
        trace = result.get("translate_agent_trace") or {}
        assert trace.get("stopped_reason") == "NO_LLM"
        assert trace.get("artifact_ref", "").startswith("artifact://")

    def test_only_readonly_tools(self):
        """translate_agent 只读工具集不含有副作用工具。"""
        from host_brain.app.agents.agent_registry import AGENT_ID_TRANSLATE
        from host_brain.app.agents.tool_registry import get_tools_for_agent
        tools = get_tools_for_agent(AGENT_ID_TRANSLATE, allow_side_effects=False)
        for tool in tools:
            assert not tool.has_side_effects
        tool_ids = {t.tool_id for t in tools}
        assert "search_knowledge" in tool_ids


# ---------------------------------------------------------------------------
# 3.2 translate_agent_trace 字段契约固化
# ---------------------------------------------------------------------------

class TestTranslateAgentTraceContract:
    """验收 3.2：translate_agent_trace 字段契约固化。"""

    REQUIRED_FIELDS = {
        "agent_id", "mode", "artifact_ref",
        "total_steps", "stopped_reason", "ts_ms",
    }

    def test_trace_has_all_required_fields(self):
        """translate_agent_trace 包含所有必填字段。"""
        from host_brain.app.handlers.translate_agent_handler import translate_agent_handler
        gateway = _make_gateway()
        result = translate_agent_handler(
            {"request_id": "p10-contract-001", "user_input": "翻译这段文字"},
            context={"gateway": gateway},
        )
        trace = result.get("translate_agent_trace") or {}
        for field in self.REQUIRED_FIELDS:
            assert field in trace, f"translate_agent_trace 缺少必填字段: {field!r}"

    def test_trace_field_types(self):
        """translate_agent_trace 字段类型正确。"""
        from host_brain.app.handlers.translate_agent_handler import translate_agent_handler
        gateway = _make_gateway()
        result = translate_agent_handler(
            {"request_id": "p10-contract-002", "user_input": "翻译这段文字"},
            context={"gateway": gateway},
        )
        trace = result["translate_agent_trace"]
        assert isinstance(trace["agent_id"], str)
        assert trace["mode"] == "react_loop"
        assert isinstance(trace["artifact_ref"], str) and trace["artifact_ref"]
        assert isinstance(trace["total_steps"], int) and trace["total_steps"] >= 1
        assert trace["stopped_reason"] in {"FINAL_ANSWER", "MAX_STEPS", "ERROR", "NO_LLM"}
        assert isinstance(trace["ts_ms"], int) and trace["ts_ms"] > 0

    def test_trace_agent_id_is_translate_agent(self):
        """agent_id 固定为 'translate_agent'。"""
        from host_brain.app.handlers.translate_agent_handler import translate_agent_handler
        from host_brain.app.agents.agent_registry import AGENT_ID_TRANSLATE
        gateway = _make_gateway()
        result = translate_agent_handler(
            {"request_id": "p10-contract-003", "user_input": "翻译这段文字"},
            context={"gateway": gateway},
        )
        assert result["translate_agent_trace"]["agent_id"] == AGENT_ID_TRANSLATE

    def test_trace_no_react_enabled_field(self):
        """translate_agent_trace 不含 react_enabled 字段（已删除冗余字段）。"""
        from host_brain.app.handlers.translate_agent_handler import translate_agent_handler
        gateway = _make_gateway()
        result = translate_agent_handler(
            {"request_id": "p10-contract-004", "user_input": "翻译这段文字"},
            context={"gateway": gateway},
        )
        assert "react_enabled" not in result["translate_agent_trace"]

    def test_trace_no_gateway_status_field(self):
        """translate_agent_trace 不含 gateway_status 字段（ReAct 模式无此字段）。"""
        from host_brain.app.handlers.translate_agent_handler import translate_agent_handler
        gateway = _make_gateway()
        result = translate_agent_handler(
            {"request_id": "p10-contract-005", "user_input": "翻译这段文字"},
            context={"gateway": gateway},
        )
        assert "gateway_status" not in result["translate_agent_trace"]
