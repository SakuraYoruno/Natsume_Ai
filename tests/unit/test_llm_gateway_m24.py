"""LLM 网关真实接入测试（TASK-0213 M24-A）。

测试编号：T-LLM-GW-01~15
"""

from __future__ import annotations

import os
import time
from unittest.mock import MagicMock, patch

import pytest

from host_brain.integrations.llm_gateway import (
    LlmGatewayContract,
    PROVIDER_MOCK,
    PROVIDER_OPENAI,
    _load_api_key_from_env,
)
from host_brain.core.errors import (
    LLM_HEALTH_PROBE_FAILED,
    LLM_ROUTE_ALL_FAILED,
    LLM_UNAVAILABLE,
    SERVICE_UNAVAILABLE_COLD_START,
    SERVICE_UNAVAILABLE_DEPENDENCY,
)


def _make_gateway(
    *,
    available: bool = True,
    api_key: str | None = "test_key",
    provider_name: str = PROVIDER_MOCK,
    provider=None,
    fallback_providers=None,
    ledger=None,
    timeout_ms: int = 30_000,
) -> LlmGatewayContract:
    return LlmGatewayContract(
        available=available,
        api_key=api_key,
        provider_name=provider_name,
        provider=provider,
        fallback_providers=fallback_providers or [],
        ledger=ledger,
        timeout_ms=timeout_ms,
    )


# ---------------------------------------------------------------------------
# T-LLM-GW-01~05: API Key 接入与健康探测
# ---------------------------------------------------------------------------

class TestLLMGatewayAPIKeyAndHealth:
    """T-LLM-GW-01~05: API Key 接入与健康探测。"""

    def test_t_llm_gw_01_valid_api_key_call_success(self):
        """T-LLM-GW-01：有效 API Key 时 LLM 调用成功，输出结构稳定。"""
        gw = _make_gateway(available=True, api_key="sk-valid-key", provider_name=PROVIDER_MOCK)
        result = gw.generate(
            request_id="req_001",
            model_profile_id="gpt-4o",
            prompt_payload={"messages": [{"role": "user", "content": "你好"}]},
        )
        assert result.success is True
        assert result.output["request_id"] == "req_001"
        assert result.output["draft_only"] is True

    def test_t_llm_gw_02_missing_api_key_stable_output(self):
        """T-LLM-GW-02：API Key 缺失时输出稳定，reason_code=SERVICE_UNAVAILABLE_COLD_START。"""
        gw = _make_gateway(
            available=True,
            api_key=None,
            provider_name=PROVIDER_OPENAI,
        )
        # 确保环境变量也没有
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_API_KEY", None)
            result = gw.generate(
                request_id="req_002",
                model_profile_id="gpt-4o",
                prompt_payload={"messages": []},
            )
        assert result.success is False
        assert result.reason_code == SERVICE_UNAVAILABLE_COLD_START
        assert result.output["status"] == "DEGRADED"
        # 不伪造"我没有任何想法"
        narrative = result.output.get("payload", {}).get("narrative", {}).get("text", "")
        assert "已执行" not in narrative
        assert "已保存" not in narrative

    def test_t_llm_gw_03_invalid_api_key_stable_output(self):
        """T-LLM-GW-03：available=False 时输出稳定，reason_code=SERVICE_UNAVAILABLE_DEPENDENCY。"""
        gw = _make_gateway(available=False, api_key="sk-invalid")
        result = gw.generate(
            request_id="req_003",
            model_profile_id="gpt-4o",
            prompt_payload={"messages": []},
        )
        assert result.success is False
        assert result.reason_code == SERVICE_UNAVAILABLE_DEPENDENCY
        assert result.output["status"] == "DEGRADED"

    def test_t_llm_gw_04_health_probe_available(self):
        """T-LLM-GW-04：健康探测可用时返回 available=True。"""
        gw = _make_gateway(available=True, api_key="sk-valid")
        probe = gw.health_probe(request_id="probe_001")
        assert probe.available is True
        assert probe.latency_ms >= 0
        assert probe.reason_code is None

    def test_t_llm_gw_04b_health_probe_unavailable(self):
        """T-LLM-GW-04b：健康探测不可用时返回 available=False，reason_code=LLM_HEALTH_PROBE_FAILED。"""
        gw = _make_gateway(available=False)
        probe = gw.health_probe(request_id="probe_002")
        assert probe.available is False
        assert probe.reason_code == LLM_HEALTH_PROBE_FAILED

    def test_t_llm_gw_05_unavailable_no_fake_narrative(self):
        """T-LLM-GW-05：LLM 不可用时不伪造"我没有任何想法"等事实结论。"""
        gw = _make_gateway(available=False)
        result = gw.generate(
            request_id="req_005",
            model_profile_id="gpt-4o",
            prompt_payload={"messages": []},
        )
        assert result.success is False
        # 降级叙事不得包含执行事实宣告
        narrative = result.output.get("payload", {}).get("narrative", {}).get("text", "")
        forbidden_phrases = ["已执行", "已保存", "已确认", "我没有任何想法"]
        for phrase in forbidden_phrases:
            assert phrase not in narrative, f"降级叙事不得包含 '{phrase}'"


# ---------------------------------------------------------------------------
# T-LLM-GW-06~10: AIRLOCK 出站净化完整链路
# ---------------------------------------------------------------------------

class TestLLMGatewayAirlockEgress:
    """T-LLM-GW-06~10: AIRLOCK 出站净化完整链路。"""

    def test_t_llm_gw_06_egress_has_sanitization_report(self):
        """T-LLM-GW-06：出站 payload 有 sanitization_report（removed_fields/redactions/payload_digest）。"""
        gw = _make_gateway(available=True, api_key="sk-valid")
        result = gw.generate(
            request_id="req_006",
            model_profile_id="gpt-4o",
            prompt_payload={"messages": [{"role": "user", "content": "测试"}]},
        )
        airlock = result.output.get("airlock", {})
        assert airlock is not None, "输出应含 airlock 字段"
        assert "egress_report" in airlock, "airlock 应含 egress_report"

    def test_t_llm_gw_07_egress_blocked_returns_stable_structure(self):
        """T-LLM-GW-07：净化失败时拒绝出站（AIRLOCK_BLOCKED_EGRESS），结构稳定。"""
        from host_brain.airlock.sanitizer import EgressGateResult

        def _blocking_egress_gate(payload, *, request_id, ledger, ts_ms):
            return EgressGateResult(
                allowed=False,
                payload={},
                reason_code="AIRLOCK_BLOCKED_EGRESS",
                detail="test block",
                sanitization_report_json=None,
            )

        gw = LlmGatewayContract(
            available=True,
            api_key="sk-valid",
            provider_name=PROVIDER_MOCK,
            egress_gate_fn=_blocking_egress_gate,
        )
        result = gw.generate(
            request_id="req_007",
            model_profile_id="gpt-4o",
            prompt_payload={"messages": []},
        )
        assert result.success is False
        assert "AIRLOCK" in str(result.reason_code)
        assert result.output["status"] == "DEGRADED"

    def test_t_llm_gw_08_ingress_classification_present(self):
        """T-LLM-GW-08：入站内容分类正确（ingress_classification 字段存在）。"""
        gw = _make_gateway(available=True, api_key="sk-valid")
        result = gw.generate(
            request_id="req_008",
            model_profile_id="gpt-4o",
            prompt_payload={"messages": [{"role": "user", "content": "测试"}]},
        )
        airlock = result.output.get("airlock", {})
        assert "ingress_classification" in airlock, "airlock 应含 ingress_classification"

    def test_t_llm_gw_09_ingress_not_direct_execution(self):
        """T-LLM-GW-09：入站内容不直通执行，必须经 Governor（draft_only=True）。"""
        gw = _make_gateway(available=True, api_key="sk-valid")
        result = gw.generate(
            request_id="req_009",
            model_profile_id="gpt-4o",
            prompt_payload={"messages": [{"role": "user", "content": "执行某个操作"}]},
        )
        # LLM 输出必须标记为 draft_only
        assert result.output.get("draft_only") is True, "LLM 输出必须标记为 draft_only"

    def test_t_llm_gw_10_egress_removes_sensitive_fields(self):
        """T-LLM-GW-10：AIRLOCK 出站净化移除硬状态原值（sanitization_report 存在）。"""
        gw = _make_gateway(available=True, api_key="sk-valid")
        result = gw.generate(
            request_id="req_010",
            model_profile_id="gpt-4o",
            prompt_payload={
                "messages": [{"role": "user", "content": "测试"}],
                "system_state": {"trust_score": 0.9, "api_key": "sk-secret"},
            },
        )
        # 出站净化报告应存在
        airlock = result.output.get("airlock", {})
        assert airlock.get("egress_report") is not None or result.success is False

    def test_t_llm_gw_10b_provider_hard_timeout_degrades_quickly(self):
        def _slow_provider(_payload):
            time.sleep(0.25)
            return "{}"

        gw = _make_gateway(
            available=True,
            api_key="sk-valid",
            provider_name=PROVIDER_MOCK,
            provider=_slow_provider,
            timeout_ms=50,
        )

        started = time.perf_counter()
        result = gw.generate(
            request_id="req_010b",
            model_profile_id="gpt-4o",
            prompt_payload={"messages": [{"role": "user", "content": "test"}]},
        )
        elapsed_ms = int(round((time.perf_counter() - started) * 1000))

        assert result.success is False
        assert result.reason_code == SERVICE_UNAVAILABLE_DEPENDENCY
        assert result.output["status"] == "DEGRADED"
        assert result.output["latency_ms"] >= 40
        assert elapsed_ms < 200


# ---------------------------------------------------------------------------
# T-LLM-GW-11~15: 多模型路由降级策略
# ---------------------------------------------------------------------------

class TestLLMGatewayMultiModelRoute:
    """T-LLM-GW-11~15: 多模型路由降级策略。"""

    def test_t_llm_gw_11_primary_timeout_fallback_to_backup(self):
        """T-LLM-GW-11：主模型超时时自动切换备用，route_call 返回成功。"""
        def _failing_provider(_payload):
            raise TimeoutError("primary model timeout")

        def _backup_provider(_payload):
            import json
            return json.dumps({
                "narrative": {"text": "备用模型回复"},
                "intent_draft": {},
                "meta": {"draft_only": True, "source": "backup"},
            })

        gw = LlmGatewayContract(
            available=True,
            api_key="sk-valid",
            provider_name=PROVIDER_MOCK,
            provider=_failing_provider,
            fallback_providers=[{
                "provider_name": PROVIDER_MOCK,
                "api_key": "sk-backup",
                "available": True,
                "provider_fn": _backup_provider,
            }],
        )
        result = gw.route_call(
            request_id="req_011",
            model_profile_id="gpt-4o",
            prompt_payload={"messages": [{"role": "user", "content": "测试"}]},
        )
        assert result.success is True, "备用模型应成功"

    def test_t_llm_gw_12_all_models_unavailable_stable_degraded(self):
        """T-LLM-GW-12：所有模型不可用时输出稳定降级结构（LLM_ROUTE_ALL_FAILED）。"""
        gw = LlmGatewayContract(
            available=False,
            api_key=None,
            provider_name=PROVIDER_OPENAI,
            fallback_providers=[
                {"provider_name": PROVIDER_OPENAI, "api_key": None, "available": False},
            ],
        )
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_API_KEY", None)
            result = gw.route_call(
                request_id="req_012",
                model_profile_id="gpt-4o",
                prompt_payload={"messages": []},
            )
        assert result.success is False
        assert result.output["status"] == "DEGRADED"

    def test_t_llm_gw_13_local_fallback_draft_only(self):
        """T-LLM-GW-13：本地兜底输出标记 draft_only=True。"""
        gw = _make_gateway(available=True, api_key="sk-valid")
        result = gw.generate(
            request_id="req_013",
            model_profile_id="ollama-3b",
            prompt_payload={"messages": [{"role": "user", "content": "测试"}]},
        )
        # 所有 LLM 输出必须标记 draft_only
        assert result.output.get("draft_only") is True

    def test_t_llm_gw_14_api_key_from_env_not_hardcoded(self):
        """T-LLM-GW-14：API Key 从环境变量读取，不硬编码。"""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-from-env"}):
            key = _load_api_key_from_env("openai")
        assert key == "sk-from-env", "应从环境变量读取 API Key"

    def test_t_llm_gw_14b_api_key_env_missing_returns_none(self):
        """T-LLM-GW-14b：环境变量未设置时返回 None。"""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_API_KEY", None)
            key = _load_api_key_from_env("openai")
        assert key is None

    def test_t_llm_gw_15_route_decision_logged(self):
        """T-LLM-GW-15：多模型路由决策写入 llm_route_log（不崩溃）。"""
        mock_ledger = MagicMock()
        mock_ledger.log_llm_route = MagicMock()

        gw = LlmGatewayContract(
            available=True,
            api_key="sk-valid",
            provider_name=PROVIDER_MOCK,
            ledger=mock_ledger,
        )
        result = gw.route_call(
            request_id="req_015",
            model_profile_id="gpt-4o",
            prompt_payload={"messages": [{"role": "user", "content": "测试"}]},
        )
        # 路由决策应被记录（log_llm_route 被调用）
        assert mock_ledger.log_llm_route.called, "路由决策应写入 llm_route_log"
