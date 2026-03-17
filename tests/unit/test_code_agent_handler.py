"""P9 专项单元测试：code_agent_handler ReAct 化验收。

验收标准：
  3.1 CODE_AGENT_REACT_ENABLED=true 时 ReAct 循环可运行
  3.2 CODE_AGENT_REACT_ENABLED=false 时行为与 P8 基线完全一致（不退化）
  3.3 code_agent_trace 字段契约固化（两种模式必填字段）
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
# 3.1 CODE_AGENT_REACT_ENABLED=true 时 ReAct 循环可运行
# ---------------------------------------------------------------------------

class TestReActModeEnabled:
    """验收 3.1：ReAct 模式可运行，字段结构正确。"""

    def test_react_mode_returns_code_agent_trace(self):
        """ReAct 模式下 code_agent_trace 存在且 mode == 'react_loop'。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        gateway = _make_gateway()
        with patch.dict(os.environ, {"CODE_AGENT_REACT_ENABLED": "true"}):
            result = code_agent_handler(
                _make_payload(),
                context={"request_id": "test-req-p9-001", "gateway": gateway},
            )
        assert "code_agent_trace" in result
        trace = result["code_agent_trace"]
        assert trace["mode"] == "react_loop"
        assert trace["react_enabled"] is True

    def test_react_mode_scratchpad_steps_nonempty(self):
        """ReAct 模式下 react_scratchpad.steps 非空（至少 1 步）。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        gateway = _make_gateway()
        with patch.dict(os.environ, {"CODE_AGENT_REACT_ENABLED": "true"}):
            result = code_agent_handler(
                _make_payload(),
                context={"gateway": gateway},
            )
        assert "react_scratchpad" in result
        scratchpad = result["react_scratchpad"]
        assert isinstance(scratchpad.get("steps"), list)
        assert len(scratchpad["steps"]) >= 1, "react_scratchpad.steps 至少 1 步"

    def test_react_mode_stopped_reason_valid(self):
        """ReAct 模式下 stopped_reason 为有效值之一。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        gateway = _make_gateway()
        with patch.dict(os.environ, {"CODE_AGENT_REACT_ENABLED": "true"}):
            result = code_agent_handler(
                _make_payload(),
                context={"gateway": gateway},
            )
        trace = result["code_agent_trace"]
        valid_reasons = {"FINAL_ANSWER", "MAX_STEPS", "ERROR", "NO_LLM"}
        assert trace["stopped_reason"] in valid_reasons, (
            f"stopped_reason={trace['stopped_reason']!r} 不在有效值集合 {valid_reasons}"
        )

    def test_react_mode_final_answer_is_final_answer(self):
        """gateway 返回 final_answer 时 stopped_reason == 'FINAL_ANSWER'。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        gateway = _make_gateway("这是最终代码草案")
        with patch.dict(os.environ, {"CODE_AGENT_REACT_ENABLED": "true"}):
            result = code_agent_handler(
                _make_payload(),
                context={"gateway": gateway},
            )
        trace = result["code_agent_trace"]
        assert trace["stopped_reason"] == "FINAL_ANSWER"

    def test_react_mode_artifact_ref_nonempty(self):
        """ReAct 模式下 artifact_ref 非空。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        gateway = _make_gateway()
        with patch.dict(os.environ, {"CODE_AGENT_REACT_ENABLED": "true"}):
            result = code_agent_handler(
                _make_payload(),
                context={"gateway": gateway},
            )
        trace = result["code_agent_trace"]
        assert trace.get("artifact_ref"), "artifact_ref 不得为空"
        assert trace["artifact_ref"].startswith("artifact://")

    def test_react_mode_agent_artifacts_written(self):
        """ReAct 模式下 agent_artifacts[code_agent] 存在。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        from host_brain.app.agents.agent_registry import AGENT_ID_CODE
        gateway = _make_gateway()
        with patch.dict(os.environ, {"CODE_AGENT_REACT_ENABLED": "true"}):
            result = code_agent_handler(
                _make_payload(),
                context={"gateway": gateway},
            )
        artifacts = result.get("agent_artifacts") or {}
        assert AGENT_ID_CODE in artifacts, "agent_artifacts[code_agent] 须存在"
        assert artifacts[AGENT_ID_CODE].get("artifact_ref"), "artifact_ref 须非空"

    def test_react_mode_tool_call_then_answer(self):
        """先调用工具再给出 final_answer 时，steps >= 2，stopped_reason == 'FINAL_ANSWER'。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        gateway = _make_gateway_tool_then_answer("analyze_code")
        with patch.dict(os.environ, {"CODE_AGENT_REACT_ENABLED": "true"}):
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

    def test_react_mode_max_steps_reached(self):
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
        with patch.dict(os.environ, {
            "CODE_AGENT_REACT_ENABLED": "true",
            "CODE_AGENT_REACT_MAX_STEPS": "2",
        }):
            result = code_agent_handler(
                _make_payload(),
                context={"gateway": gateway},
            )
        trace = result["code_agent_trace"]
        assert trace["stopped_reason"] == "MAX_STEPS"
        assert trace["total_steps"] == 2

    def test_react_mode_no_gateway_degrades_gracefully(self):
        """无 gateway 时降级为 NO_LLM，不抛异常，artifact_ref 仍存在。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        with patch.dict(os.environ, {"CODE_AGENT_REACT_ENABLED": "true"}):
            result = code_agent_handler(
                _make_payload(),
                context={},  # 无 gateway
            )
        trace = result.get("code_agent_trace") or {}
        assert trace.get("stopped_reason") == "NO_LLM"
        assert trace.get("artifact_ref"), "降级时 artifact_ref 仍须存在"

    def test_react_mode_only_readonly_tools(self):
        """code_agent 只读工具集不含有副作用工具。"""
        from host_brain.app.agents.agent_registry import AGENT_ID_CODE
        from host_brain.app.agents.tool_registry import get_tools_for_agent
        tools = get_tools_for_agent(AGENT_ID_CODE, allow_side_effects=False)
        for tool in tools:
            assert not tool.has_side_effects, (
                f"code_agent 只读工具集不得含有副作用工具: {tool.tool_id}"
            )
        # code_agent 应有 analyze_code 和 search_knowledge
        tool_ids = {t.tool_id for t in tools}
        assert "analyze_code" in tool_ids
        assert "search_knowledge" in tool_ids


# ---------------------------------------------------------------------------
# 3.2 CODE_AGENT_REACT_ENABLED=false 时行为与 P8 基线完全一致（不退化）
# ---------------------------------------------------------------------------

class TestLegacyModeUnchanged:
    """验收 3.2：legacy 模式行为与 P8 基线完全一致。"""

    def _make_legacy_payload(self) -> dict[str, Any]:
        return {
            "request_id": "test-req-p9-legacy-001",
            "user_input": "帮我写一个排序函数",
            "workbench_code_assist": {
                "gateway_status": "OK",
                "payload": {
                    "code_draft": "def sort_list(lst): return sorted(lst)",
                    "response_text": "代码草案已生成",
                },
            },
        }

    def test_legacy_mode_trace_mode_is_legacy_chain(self):
        """legacy 模式下 code_agent_trace.mode == 'legacy_chain'。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        with patch.dict(os.environ, {"CODE_AGENT_REACT_ENABLED": "false"}):
            result = code_agent_handler(self._make_legacy_payload())
        trace = result.get("code_agent_trace") or {}
        assert trace["mode"] == "legacy_chain"
        assert trace["react_enabled"] is False

    def test_legacy_mode_no_react_scratchpad(self):
        """legacy 模式下 react_scratchpad 不写入（或为空）。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        with patch.dict(os.environ, {"CODE_AGENT_REACT_ENABLED": "false"}):
            result = code_agent_handler(self._make_legacy_payload())
        # react_scratchpad 不应存在，或为空
        scratchpad = result.get("react_scratchpad")
        assert not scratchpad, "legacy 模式下 react_scratchpad 不得写入"

    def test_legacy_mode_artifact_ref_nonempty(self):
        """legacy 模式下 artifact_ref 非空。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        with patch.dict(os.environ, {"CODE_AGENT_REACT_ENABLED": "false"}):
            result = code_agent_handler(self._make_legacy_payload())
        trace = result.get("code_agent_trace") or {}
        assert trace.get("artifact_ref"), "legacy 模式 artifact_ref 不得为空"
        assert trace["artifact_ref"].startswith("artifact://")

    def test_legacy_mode_agent_artifacts_written(self):
        """legacy 模式下 agent_artifacts[code_agent] 存在。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        from host_brain.app.agents.agent_registry import AGENT_ID_CODE
        with patch.dict(os.environ, {"CODE_AGENT_REACT_ENABLED": "false"}):
            result = code_agent_handler(self._make_legacy_payload())
        artifacts = result.get("agent_artifacts") or {}
        assert AGENT_ID_CODE in artifacts

    def test_legacy_mode_gateway_status_ok(self):
        """legacy 模式下 gateway_status 为 OK。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        with patch.dict(os.environ, {"CODE_AGENT_REACT_ENABLED": "false"}):
            result = code_agent_handler(self._make_legacy_payload())
        trace = result.get("code_agent_trace") or {}
        assert trace.get("gateway_status") == "OK"

    def test_legacy_mode_default_env_is_false(self):
        """默认环境（未设置 CODE_AGENT_REACT_ENABLED）走 legacy 模式。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        env = {k: v for k, v in os.environ.items() if k != "CODE_AGENT_REACT_ENABLED"}
        with patch.dict(os.environ, env, clear=True):
            result = code_agent_handler(self._make_legacy_payload())
        trace = result.get("code_agent_trace") or {}
        assert trace.get("mode") == "legacy_chain", "默认应走 legacy_chain"

    def test_legacy_mode_no_gateway_needed(self):
        """legacy 模式不需要 gateway，不传 context 也不抛异常。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        with patch.dict(os.environ, {"CODE_AGENT_REACT_ENABLED": "false"}):
            result = code_agent_handler(self._make_legacy_payload())
        assert "code_agent_trace" in result


# ---------------------------------------------------------------------------
# 3.3 code_agent_trace 字段契约固化
# ---------------------------------------------------------------------------

class TestCodeAgentTraceContract:
    """验收 3.3：code_agent_trace 字段契约固化。"""

    # ReAct 模式必填字段
    REACT_REQUIRED_FIELDS = {
        "agent_id", "mode", "artifact_ref",
        "total_steps", "stopped_reason", "react_enabled", "ts_ms",
    }

    # Legacy 模式必填字段
    LEGACY_REQUIRED_FIELDS = {
        "agent_id", "mode", "artifact_ref",
        "gateway_status", "react_enabled", "ts_ms",
    }

    def test_react_trace_has_all_required_fields(self):
        """ReAct 模式 code_agent_trace 包含所有必填字段。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        gateway = _make_gateway()
        with patch.dict(os.environ, {"CODE_AGENT_REACT_ENABLED": "true"}):
            result = code_agent_handler(
                {"request_id": "p9-contract-001", "user_input": "写代码"},
                context={"gateway": gateway},
            )
        trace = result.get("code_agent_trace") or {}
        for field in self.REACT_REQUIRED_FIELDS:
            assert field in trace, f"ReAct 模式 code_agent_trace 缺少必填字段: {field!r}"

    def test_react_trace_field_types(self):
        """ReAct 模式 code_agent_trace 字段类型正确。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        gateway = _make_gateway()
        with patch.dict(os.environ, {"CODE_AGENT_REACT_ENABLED": "true"}):
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
        assert trace["react_enabled"] is True
        assert isinstance(trace["ts_ms"], int) and trace["ts_ms"] > 0

    def test_react_trace_agent_id_is_code_agent(self):
        """ReAct 模式 agent_id 固定为 'code_agent'。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        from host_brain.app.agents.agent_registry import AGENT_ID_CODE
        gateway = _make_gateway()
        with patch.dict(os.environ, {"CODE_AGENT_REACT_ENABLED": "true"}):
            result = code_agent_handler(
                {"request_id": "p9-contract-003", "user_input": "写代码"},
                context={"gateway": gateway},
            )
        trace = result["code_agent_trace"]
        assert trace["agent_id"] == AGENT_ID_CODE

    def test_legacy_trace_has_all_required_fields(self):
        """Legacy 模式 code_agent_trace 包含所有必填字段。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        payload = {
            "request_id": "p9-contract-004",
            "user_input": "写代码",
            "workbench_code_assist": {
                "gateway_status": "OK",
                "payload": {"code_draft": "pass"},
            },
        }
        with patch.dict(os.environ, {"CODE_AGENT_REACT_ENABLED": "false"}):
            result = code_agent_handler(payload)
        trace = result.get("code_agent_trace") or {}
        for field in self.LEGACY_REQUIRED_FIELDS:
            assert field in trace, f"Legacy 模式 code_agent_trace 缺少必填字段: {field!r}"

    def test_legacy_trace_field_types(self):
        """Legacy 模式 code_agent_trace 字段类型正确。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        payload = {
            "request_id": "p9-contract-005",
            "user_input": "写代码",
            "workbench_code_assist": {
                "gateway_status": "OK",
                "payload": {"code_draft": "pass"},
            },
        }
        with patch.dict(os.environ, {"CODE_AGENT_REACT_ENABLED": "false"}):
            result = code_agent_handler(payload)
        trace = result["code_agent_trace"]
        assert isinstance(trace["agent_id"], str)
        assert trace["mode"] == "legacy_chain"
        assert isinstance(trace["artifact_ref"], str) and trace["artifact_ref"]
        assert isinstance(trace["gateway_status"], str)
        assert trace["react_enabled"] is False
        assert isinstance(trace["ts_ms"], int) and trace["ts_ms"] > 0

    def test_legacy_trace_no_total_steps_field(self):
        """Legacy 模式 code_agent_trace 不含 total_steps 字段（模式互斥）。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        payload = {
            "request_id": "p9-contract-006",
            "user_input": "写代码",
            "workbench_code_assist": {
                "gateway_status": "OK",
                "payload": {"code_draft": "pass"},
            },
        }
        with patch.dict(os.environ, {"CODE_AGENT_REACT_ENABLED": "false"}):
            result = code_agent_handler(payload)
        trace = result["code_agent_trace"]
        assert "total_steps" not in trace, "legacy 模式不应有 total_steps 字段"

    def test_react_trace_no_gateway_status_field(self):
        """ReAct 模式 code_agent_trace 不含 gateway_status 字段（模式互斥）。"""
        from host_brain.app.handlers.code_agent_handler import code_agent_handler
        gateway = _make_gateway()
        with patch.dict(os.environ, {"CODE_AGENT_REACT_ENABLED": "true"}):
            result = code_agent_handler(
                {"request_id": "p9-contract-007", "user_input": "写代码"},
                context={"gateway": gateway},
            )
        trace = result["code_agent_trace"]
        assert "gateway_status" not in trace, "react 模式不应有 gateway_status 字段"


# ---------------------------------------------------------------------------
# 开关行为一致性（补充）
# ---------------------------------------------------------------------------

class TestSwitchBehavior:
    """开关行为一致性验证。"""

    def test_switch_true_values(self):
        """CODE_AGENT_REACT_ENABLED 多种 true 值均生效。"""
        from host_brain.app.handlers.code_agent_handler import _parse_bool_env
        for val in ("1", "true", "True", "TRUE", "yes", "on"):
            with patch.dict(os.environ, {"CODE_AGENT_REACT_ENABLED": val}):
                assert _parse_bool_env("CODE_AGENT_REACT_ENABLED") is True, (
                    f"值 {val!r} 应解析为 True"
                )

    def test_switch_false_values(self):
        """CODE_AGENT_REACT_ENABLED 多种 false 值均生效。"""
        from host_brain.app.handlers.code_agent_handler import _parse_bool_env
        for val in ("0", "false", "False", "FALSE", "no", "off"):
            with patch.dict(os.environ, {"CODE_AGENT_REACT_ENABLED": val}):
                assert _parse_bool_env("CODE_AGENT_REACT_ENABLED") is False, (
                    f"值 {val!r} 应解析为 False"
                )

    def test_switch_default_is_false(self):
        """未设置时默认为 False。"""
        from host_brain.app.handlers.code_agent_handler import _parse_bool_env
        env = {k: v for k, v in os.environ.items() if k != "CODE_AGENT_REACT_ENABLED"}
        with patch.dict(os.environ, env, clear=True):
            assert _parse_bool_env("CODE_AGENT_REACT_ENABLED") is False
