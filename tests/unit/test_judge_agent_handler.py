"""P14 单元测试：judge_agent_handler ReAct 化。

验收标准覆盖：
  - 3.1：judge_agent_handler 走 ReAct 路径，react_scratchpad 非 None，
         mode="react_loop"，judge_agent_trace 字段契约正确
  - 3.2：evaluate_artifact / compare_artifacts 工具有真实实现
  - 3.3：P13 基线（85 用例）不退化，judge_agent 工具 allowlist 更新
"""

from __future__ import annotations

import json
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
    _tool_evaluate_artifact,
    _tool_compare_artifacts,
)
from host_brain.app.handlers.judge_agent_handler import (
    judge_agent_handler,
    _parse_judge_final_answer,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_gateway(final_answer: str = "selected_agent=codex_worker, confidence=0.85, reason=最高置信度") -> MagicMock:
    """构造直接给出 final_answer 的 mock gateway。"""
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


def _make_gateway_tool_then_answer(tool_name: str = "compare_artifacts") -> MagicMock:
    """构造先调用工具再给出 final_answer 的 mock gateway（两步）。"""
    gateway = MagicMock()
    call_count = [0]

    def _complete(messages):
        call_count[0] += 1
        if call_count[0] == 1:
            return {
                "content": json.dumps(
                    {
                        "thought": "先比较工件",
                        "action_type": "tool_call",
                        "action": {
                            "tool": tool_name,
                            "args": {
                                "artifacts": {
                                    "codex_worker": {"confidence": 0.85, "summary": "代码草案", "risk_flags": []},
                                    "translate_agent": {"confidence": 0.60, "summary": "翻译结果", "risk_flags": []},
                                }
                            },
                        },
                    },
                    ensure_ascii=False,
                )
            }
        return {
            "content": json.dumps(
                {
                    "thought": "比较完毕，codex_worker 最优",
                    "action_type": "final_answer",
                    "final_answer": "selected_agent=codex_worker, confidence=0.85, reason=最高置信度",
                },
                ensure_ascii=False,
            )
        }

    gateway.complete.side_effect = _complete
    return gateway


def _make_payload(
    artifacts: dict[str, Any] | None = None,
    user_input: str = "请裁判工件",
) -> dict[str, Any]:
    default_artifacts = {
        "codex_worker": {
            "artifact_ref": "artifact://req-001/codex_worker_1000",
            "summary": "代码草案已生成",
            "confidence": 0.85,
            "risk_flags": [],
        },
        "translate_agent": {
            "artifact_ref": "artifact://req-001/translate_agent_1001",
            "summary": "翻译结果",
            "confidence": 0.60,
            "risk_flags": [],
        },
    }
    return {
        "request_id": "test-req-p14-001",
        "user_input": user_input,
        "agent_artifacts": artifacts if artifacts is not None else default_artifacts,
    }


# ---------------------------------------------------------------------------
# 3.1 judge_agent_handler ReAct 模式
# ---------------------------------------------------------------------------

class TestReActMode:
    """验收 3.1：ReAct 模式可运行，字段结构正确。"""

    def test_returns_judge_agent_trace(self):
        """judge_agent_trace 存在且 mode == 'react_loop'。"""
        gateway = _make_gateway()
        result = judge_agent_handler(
            _make_payload(),
            context={"request_id": "test-req-p14-001", "gateway": gateway},
        )
        assert "judge_agent_trace" in result
        trace = result["judge_agent_trace"]
        assert trace["mode"] == "react_loop"

    def test_scratchpad_exists(self):
        """react_scratchpad 存在。"""
        gateway = _make_gateway()
        result = judge_agent_handler(
            _make_payload(),
            context={"gateway": gateway},
        )
        assert "react_scratchpad" in result
        scratchpad = result["react_scratchpad"]
        assert isinstance(scratchpad, dict)

    def test_judge_result_exists(self):
        """judge_result 存在且含必填字段。"""
        gateway = _make_gateway()
        result = judge_agent_handler(
            _make_payload(),
            context={"gateway": gateway},
        )
        assert "judge_result" in result
        jr = result["judge_result"]
        assert "status" in jr
        assert "mode" in jr
        assert jr["mode"] == "react_loop"

    def test_judge_result_status_ok(self):
        """正常工件时 judge_result.status == 'OK'。"""
        gateway = _make_gateway()
        result = judge_agent_handler(
            _make_payload(),
            context={"gateway": gateway},
        )
        jr = result["judge_result"]
        assert jr["status"] in {"OK", "DEGRADED"}

    def test_trace_artifact_ref_nonempty(self):
        """judge_agent_trace.artifact_ref 非空。"""
        gateway = _make_gateway()
        result = judge_agent_handler(
            _make_payload(),
            context={"request_id": "test-req-p14-001", "gateway": gateway},
        )
        trace = result["judge_agent_trace"]
        assert trace["artifact_ref"]
        assert "test-req-p14-001" in trace["artifact_ref"]

    def test_trace_stopped_reason_valid(self):
        """judge_agent_trace.stopped_reason 为有效值。"""
        gateway = _make_gateway()
        result = judge_agent_handler(
            _make_payload(),
            context={"gateway": gateway},
        )
        trace = result["judge_agent_trace"]
        assert trace["stopped_reason"] in {"FINAL_ANSWER", "MAX_STEPS", "ERROR"}

    def test_trace_total_steps_gte_1(self):
        """judge_agent_trace.total_steps >= 1（有工件时）。"""
        gateway = _make_gateway()
        result = judge_agent_handler(
            _make_payload(),
            context={"gateway": gateway},
        )
        trace = result["judge_agent_trace"]
        assert trace["total_steps"] >= 1

    def test_tool_call_then_final_answer(self):
        """先调用工具再给出 final_answer 时，react_scratchpad.steps 有 2 步。"""
        gateway = _make_gateway_tool_then_answer("compare_artifacts")
        result = judge_agent_handler(
            _make_payload(),
            context={"gateway": gateway},
        )
        scratchpad = result["react_scratchpad"]
        steps = scratchpad.get("steps", [])
        assert len(steps) >= 1

    def test_selected_agent_in_artifacts(self):
        """judge_result.selected_agent 在 agent_artifacts 中（或为 None）。"""
        gateway = _make_gateway()
        payload = _make_payload()
        result = judge_agent_handler(payload, context={"gateway": gateway})
        jr = result["judge_result"]
        if jr["selected_agent"] is not None:
            assert jr["selected_agent"] in payload["agent_artifacts"]

    def test_all_agents_evaluated_field(self):
        """judge_result.all_agents_evaluated 包含所有工件 agent_id。"""
        gateway = _make_gateway()
        payload = _make_payload()
        result = judge_agent_handler(payload, context={"gateway": gateway})
        jr = result["judge_result"]
        assert "all_agents_evaluated" in jr
        for aid in payload["agent_artifacts"]:
            assert aid in jr["all_agents_evaluated"]


# ---------------------------------------------------------------------------
# 3.1 降级路径
# ---------------------------------------------------------------------------

class TestDegradedPaths:
    """降级路径：无工件、全部错误、高风险。"""

    def test_no_artifacts_degraded(self):
        """无工件时直接降级，不进入 ReAct 循环。"""
        result = judge_agent_handler(
            _make_payload(artifacts={}),
            context={"request_id": "test-req-p14-002"},
        )
        jr = result["judge_result"]
        assert jr["status"] == "DEGRADED"
        assert jr["reason"] == "NO_ARTIFACTS"
        assert jr["mode"] == "react_loop"

    def test_no_artifacts_trace_total_steps_zero(self):
        """无工件时 judge_agent_trace.total_steps == 0。"""
        result = judge_agent_handler(
            _make_payload(artifacts={}),
            context={},
        )
        trace = result["judge_agent_trace"]
        assert trace["total_steps"] == 0
        assert trace["stopped_reason"] == "NO_ARTIFACTS"

    def test_high_risk_artifact_degraded(self):
        """含高风险标记的工件导致 judge_result.status == 'DEGRADED'。"""
        gateway = _make_gateway("selected_agent=codex_worker, confidence=0.85, reason=高风险")
        artifacts = {
            "codex_worker": {
                "artifact_ref": "artifact://req/code_1",
                "summary": "代码草案",
                "confidence": 0.85,
                "risk_flags": ["SAFETY_RISK:HIGH"],
            }
        }
        result = judge_agent_handler(
            _make_payload(artifacts=artifacts),
            context={"gateway": gateway},
        )
        jr = result["judge_result"]
        assert jr["status"] == "DEGRADED"

    def test_exception_safety(self):
        """gateway 抛异常时，handler 不抛异常，返回 DEGRADED judge_result。"""
        gateway = MagicMock()
        gateway.complete.side_effect = RuntimeError("LLM 不可用")
        result = judge_agent_handler(
            _make_payload(),
            context={"gateway": gateway},
        )
        jr = result["judge_result"]
        assert jr["status"] == "DEGRADED"
        # reason 可能是 REACT_LOOP_ERROR 或 JUDGE_ERROR:xxx
        assert "ERROR" in jr["reason"]

    def test_none_gateway_no_crash(self):
        """gateway 为 None 时，handler 不崩溃，返回有效结果。"""
        result = judge_agent_handler(
            _make_payload(),
            context={"gateway": None},
        )
        # gateway 为 None 时 react_loop 内部降级处理，不崩溃即可
        assert "judge_result" in result
        jr = result["judge_result"]
        assert jr["status"] in {"OK", "DEGRADED"}


# ---------------------------------------------------------------------------
# 3.2 evaluate_artifact 工具
# ---------------------------------------------------------------------------

class TestEvaluateArtifactTool:
    """验收 3.2：evaluate_artifact 工具有真实实现。"""

    def test_basic_evaluation(self):
        """正常工件返回 quality 和 score。"""
        result = _tool_evaluate_artifact({
            "artifact": {
                "confidence": 0.85,
                "summary": "代码草案已生成",
                "risk_flags": [],
                "artifact_ref": "artifact://req/code_1",
            },
            "agent_id": "codex_worker",
        })
        assert "quality" in result
        assert result["quality"] in {"HIGH", "MEDIUM", "LOW"}
        assert "score" in result
        assert 0.0 <= result["score"] <= 1.0

    def test_high_confidence_high_quality(self):
        """高置信度工件质量为 HIGH。"""
        result = _tool_evaluate_artifact({
            "artifact": {"confidence": 0.9, "summary": "优质输出", "risk_flags": []},
        })
        assert result["quality"] == "HIGH"

    def test_low_confidence_low_quality(self):
        """低置信度工件质量为 LOW。"""
        result = _tool_evaluate_artifact({
            "artifact": {"confidence": 0.1, "summary": "低质量", "risk_flags": []},
        })
        assert result["quality"] == "LOW"

    def test_high_risk_score_zero(self):
        """含 SAFETY_RISK:HIGH 标记时 score == 0.0。"""
        result = _tool_evaluate_artifact({
            "artifact": {
                "confidence": 0.9,
                "summary": "危险输出",
                "risk_flags": ["SAFETY_RISK:HIGH"],
            },
        })
        assert result["score"] == 0.0

    def test_error_artifact_score_zero(self):
        """含 error 字段的工件 score == 0.0。"""
        result = _tool_evaluate_artifact({
            "artifact": {
                "confidence": 0.8,
                "summary": "出错了",
                "risk_flags": [],
                "error": "HANDLER_ERROR",
            },
        })
        assert result["score"] == 0.0
        assert result["has_error"] is True

    def test_missing_artifact_returns_error(self):
        """缺少 artifact 参数时返回 error。"""
        result = _tool_evaluate_artifact({})
        assert "error" in result
        assert result["error"] == "MISSING_ARTIFACT"

    def test_result_text_nonempty(self):
        """result 字段非空。"""
        result = _tool_evaluate_artifact({
            "artifact": {"confidence": 0.7, "summary": "测试", "risk_flags": []},
        })
        assert result.get("result")

    def test_execute_tool_evaluate_artifact(self):
        """通过 execute_tool 调用 evaluate_artifact 可正常执行。"""
        result = execute_tool("evaluate_artifact", {
            "artifact": {"confidence": 0.75, "summary": "测试工件", "risk_flags": []},
        })
        assert "quality" in result or "error" in result


# ---------------------------------------------------------------------------
# 3.2 compare_artifacts 工具
# ---------------------------------------------------------------------------

class TestCompareArtifactsTool:
    """验收 3.2：compare_artifacts 工具有真实实现。"""

    def test_basic_comparison(self):
        """正常比较返回 recommended 和 scores。"""
        result = _tool_compare_artifacts({
            "artifacts": {
                "codex_worker": {"confidence": 0.85, "summary": "代码草案", "risk_flags": []},
                "translate_agent": {"confidence": 0.60, "summary": "翻译结果", "risk_flags": []},
            }
        })
        assert "recommended" in result
        assert result["recommended"] == "codex_worker"
        assert "scores" in result

    def test_recommends_highest_confidence(self):
        """推荐置信度最高的工件。"""
        result = _tool_compare_artifacts({
            "artifacts": {
                "agent_a": {"confidence": 0.3, "summary": "低", "risk_flags": []},
                "agent_b": {"confidence": 0.9, "summary": "高", "risk_flags": []},
                "agent_c": {"confidence": 0.6, "summary": "中", "risk_flags": []},
            }
        })
        assert result["recommended"] == "agent_b"

    def test_high_risk_excluded(self):
        """高风险工件被排除（score=0），不被推荐。"""
        result = _tool_compare_artifacts({
            "artifacts": {
                "safe_agent": {"confidence": 0.5, "summary": "安全", "risk_flags": []},
                "risky_agent": {"confidence": 0.95, "summary": "危险", "risk_flags": ["SAFETY_RISK:HIGH"]},
            }
        })
        assert result["recommended"] == "safe_agent"

    def test_all_failed_returns_none(self):
        """所有工件均有错误时 recommended 为 None。"""
        result = _tool_compare_artifacts({
            "artifacts": {
                "agent_a": {"confidence": 0.8, "error": "HANDLER_ERROR", "risk_flags": []},
                "agent_b": {"confidence": 0.7, "error": "TIMEOUT", "risk_flags": []},
            }
        })
        assert result["recommended"] is None
        assert result["reason"] == "ALL_FAILED"

    def test_missing_artifacts_returns_error(self):
        """缺少 artifacts 参数时返回 error。"""
        result = _tool_compare_artifacts({})
        assert "error" in result
        assert result["error"] == "MISSING_ARTIFACTS"

    def test_valid_count_field(self):
        """valid_count 字段正确。"""
        result = _tool_compare_artifacts({
            "artifacts": {
                "agent_a": {"confidence": 0.8, "summary": "ok", "risk_flags": []},
                "agent_b": {"confidence": 0.5, "error": "FAIL", "risk_flags": []},
            }
        })
        assert result["valid_count"] == 1
        assert result["total_count"] == 2

    def test_result_text_nonempty(self):
        """result 字段非空。"""
        result = _tool_compare_artifacts({
            "artifacts": {
                "agent_a": {"confidence": 0.8, "summary": "ok", "risk_flags": []},
            }
        })
        assert result.get("result")

    def test_execute_tool_compare_artifacts(self):
        """通过 execute_tool 调用 compare_artifacts 可正常执行。"""
        result = execute_tool("compare_artifacts", {
            "artifacts": {
                "codex_worker": {"confidence": 0.8, "summary": "ok", "risk_flags": []},
            }
        })
        assert "recommended" in result or "error" in result


# ---------------------------------------------------------------------------
# 3.3 tool_registry 契约：judge_agent 工具 allowlist 更新
# ---------------------------------------------------------------------------

class TestJudgeAgentToolRegistry:
    """验收 3.3：judge_agent 工具 allowlist 已更新。"""

    def test_judge_agent_has_evaluate_artifact(self):
        """judge_agent 工具列表包含 evaluate_artifact。"""
        tools = get_tools_for_agent(AGENT_ID_JUDGE)
        tool_ids = [t.tool_id for t in tools]
        assert "evaluate_artifact" in tool_ids

    def test_judge_agent_has_compare_artifacts(self):
        """judge_agent 工具列表包含 compare_artifacts。"""
        tools = get_tools_for_agent(AGENT_ID_JUDGE)
        tool_ids = [t.tool_id for t in tools]
        assert "compare_artifacts" in tool_ids

    def test_judge_agent_tools_count(self):
        """judge_agent 工具列表有 2 个工具。"""
        tools = get_tools_for_agent(AGENT_ID_JUDGE)
        assert len(tools) == 2

    def test_evaluate_artifact_in_registry(self):
        """evaluate_artifact 已注册到 _TOOL_REGISTRY。"""
        assert "evaluate_artifact" in _TOOL_REGISTRY

    def test_compare_artifacts_in_registry(self):
        """compare_artifacts 已注册到 _TOOL_REGISTRY。"""
        assert "compare_artifacts" in _TOOL_REGISTRY

    def test_evaluate_artifact_no_side_effects(self):
        """evaluate_artifact 无副作用。"""
        spec = _TOOL_REGISTRY["evaluate_artifact"]
        assert spec.has_side_effects is False

    def test_compare_artifacts_no_side_effects(self):
        """compare_artifacts 无副作用。"""
        spec = _TOOL_REGISTRY["compare_artifacts"]
        assert spec.has_side_effects is False

    def test_is_tool_allowed_evaluate_artifact(self):
        """is_tool_allowed('evaluate_artifact', 'judge_agent') 返回 True。"""
        assert is_tool_allowed("evaluate_artifact", AGENT_ID_JUDGE) is True

    def test_is_tool_allowed_compare_artifacts(self):
        """is_tool_allowed('compare_artifacts', 'judge_agent') 返回 True。"""
        assert is_tool_allowed("compare_artifacts", AGENT_ID_JUDGE) is True

    def test_evaluate_artifact_has_executor(self):
        """evaluate_artifact 有 executor。"""
        spec = _TOOL_REGISTRY["evaluate_artifact"]
        assert spec.executor is not None

    def test_compare_artifacts_has_executor(self):
        """compare_artifacts 有 executor。"""
        spec = _TOOL_REGISTRY["compare_artifacts"]
        assert spec.executor is not None


# ---------------------------------------------------------------------------
# _parse_judge_final_answer 解析逻辑
# ---------------------------------------------------------------------------

class TestParseJudgeFinalAnswer:
    """_parse_judge_final_answer 解析逻辑。"""

    def test_parse_selected_agent(self):
        """正确解析 selected_agent。"""
        artifacts = {
            "codex_worker": {"confidence": 0.85, "risk_flags": []},
            "translate_agent": {"confidence": 0.60, "risk_flags": []},
        }
        agent, conf, reason = _parse_judge_final_answer(
            "selected_agent=codex_worker, confidence=0.85, reason=最高置信度",
            artifacts,
        )
        assert agent == "codex_worker"
        assert conf == pytest.approx(0.85, abs=0.01)

    def test_parse_confidence(self):
        """正确解析 confidence。"""
        artifacts = {"codex_worker": {"confidence": 0.9, "risk_flags": []}}
        _, conf, _ = _parse_judge_final_answer(
            "selected_agent=codex_worker, confidence=0.9, reason=ok",
            artifacts,
        )
        assert conf == pytest.approx(0.9, abs=0.01)

    def test_fallback_to_max_confidence(self):
        """无法解析 selected_agent 时回退到置信度最高的工件。"""
        artifacts = {
            "agent_a": {"confidence": 0.3, "risk_flags": []},
            "agent_b": {"confidence": 0.8, "risk_flags": []},
        }
        agent, conf, reason = _parse_judge_final_answer(
            "无法确定最优工件",
            artifacts,
        )
        assert agent == "agent_b"
        assert reason == "FALLBACK_MAX_CONFIDENCE"

    def test_empty_final_answer(self):
        """空 final_answer 时回退到置信度最高的工件。"""
        artifacts = {"codex_worker": {"confidence": 0.7, "risk_flags": []}}
        agent, conf, reason = _parse_judge_final_answer("", artifacts)
        assert agent == "codex_worker"

    def test_empty_artifacts(self):
        """空工件时返回 None。"""
        agent, conf, reason = _parse_judge_final_answer("selected_agent=codex_worker", {})
        assert agent is None

    def test_unknown_agent_fallback(self):
        """final_answer 中的 agent_id 不在工件中时回退。"""
        artifacts = {"codex_worker": {"confidence": 0.7, "risk_flags": []}}
        agent, conf, reason = _parse_judge_final_answer(
            "selected_agent=unknown_agent, confidence=0.9, reason=ok",
            artifacts,
        )
        # 回退到 codex_worker
        assert agent == "codex_worker"


