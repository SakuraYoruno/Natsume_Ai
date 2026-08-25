"""Deterministic judge boundary and proposal metadata tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from host_brain.app.handlers.judge_agent_handler import (
    judge_agent_handler,
    _parse_judge_final_answer,
)


def _make_payload(
    artifacts: dict[str, Any] | None = None,
    user_input: str = "请裁判工件",
) -> dict[str, Any]:
    default_artifacts = {
        "agent_worker": {
            "artifact_ref": "artifact://req-001/agent_worker_1000",
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
# Deterministic application boundary
# ---------------------------------------------------------------------------

class TestArtifactPolicyBoundary:
    def test_selects_highest_confidence_artifact_without_gateway(self):
        payload = _make_payload()
        result = judge_agent_handler(payload, context={"request_id": "test-req-p14-001"})

        judge = result["judge_result"]
        trace = result["judge_agent_trace"]
        assert judge["status"] == "OK"
        assert judge["mode"] == "artifact_policy"
        assert judge["selected_agent"] == "agent_worker"
        assert judge["all_agents_evaluated"] == ["agent_worker", "translate_agent"]
        assert trace["mode"] == "artifact_policy"
        assert trace["side_effects_executed"] is False
        assert "react_scratchpad" not in result

    def test_gateway_is_ignored_at_policy_boundary(self):
        gateway = MagicMock()
        result = judge_agent_handler(_make_payload(), context={"gateway": gateway})

        gateway.complete.assert_not_called()
        assert result["judge_result"]["selected_agent"] == "agent_worker"

    def test_no_artifacts_degrades_with_stable_reason(self):
        result = judge_agent_handler(_make_payload(artifacts={}))

        assert result["judge_result"]["status"] == "DEGRADED"
        assert result["judge_result"]["reason"] == "NO_ARTIFACTS"
        assert result["judge_agent_trace"]["artifacts_evaluated"] == 0

    def test_high_risk_artifact_is_never_approved(self):
        artifacts = {
            "agent_worker": {
                "artifact_ref": "artifact://req/code_1",
                "summary": "代码草案",
                "confidence": 0.85,
                "risk_flags": ["SAFETY_RISK:HIGH"],
            }
        }
        result = judge_agent_handler(_make_payload(artifacts=artifacts))

        assert result["judge_result"]["status"] == "DEGRADED"
        assert result["judge_result"]["reason"] == "ARTIFACT_POLICY_RISK"

    def test_all_error_artifacts_are_rejected(self):
        artifacts = {
            "agent_a": {"confidence": 0.9, "error": "FAILED"},
            "agent_b": {"confidence": 0.8, "error": "TIMEOUT"},
        }
        result = judge_agent_handler(_make_payload(artifacts=artifacts))

        assert result["judge_result"]["selected_agent"] is None
        assert result["judge_result"]["reason"] == "ALL_ARTIFACTS_REJECTED"


# ---------------------------------------------------------------------------
# _parse_judge_final_answer 解析逻辑
# ---------------------------------------------------------------------------

class TestParseJudgeFinalAnswer:
    """_parse_judge_final_answer 解析逻辑。"""

    def test_parse_selected_agent(self):
        """正确解析 selected_agent。"""
        artifacts = {
            "agent_worker": {"confidence": 0.85, "risk_flags": []},
            "translate_agent": {"confidence": 0.60, "risk_flags": []},
        }
        agent, conf, reason = _parse_judge_final_answer(
            "selected_agent=agent_worker, confidence=0.85, reason=最高置信度",
            artifacts,
        )
        assert agent == "agent_worker"
        assert conf == pytest.approx(0.85, abs=0.01)

    def test_parse_confidence(self):
        """正确解析 confidence。"""
        artifacts = {"agent_worker": {"confidence": 0.9, "risk_flags": []}}
        _, conf, _ = _parse_judge_final_answer(
            "selected_agent=agent_worker, confidence=0.9, reason=ok",
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
        artifacts = {"agent_worker": {"confidence": 0.7, "risk_flags": []}}
        agent, conf, reason = _parse_judge_final_answer("", artifacts)
        assert agent == "agent_worker"

    def test_empty_artifacts(self):
        """空工件时返回 None。"""
        agent, conf, reason = _parse_judge_final_answer("selected_agent=agent_worker", {})
        assert agent is None

    def test_unknown_agent_fallback(self):
        """final_answer 中的 agent_id 不在工件中时回退。"""
        artifacts = {"agent_worker": {"confidence": 0.7, "risk_flags": []}}
        agent, conf, reason = _parse_judge_final_answer(
            "selected_agent=unknown_agent, confidence=0.9, reason=ok",
            artifacts,
        )
        # 回退到 agent_worker
        assert agent == "agent_worker"
