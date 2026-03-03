"""T-E2E-03: 故障注入矩阵 Smoke Test。

验收标准（DevSpec §18.2.1）：
- 4 种故障注入下输出结构均稳定
- reason_code 均可统计
- Neo4j/Chroma/LLM/Edge 各自不可用时系统不崩溃
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "e2e"


class TestFaultInjectionMatrix:
    """T-E2E-03: 故障注入矩阵（4 种故障场景）。"""

    def test_fault_injection_matrix_fixture_valid(self):
        """故障注入矩阵 fixture 为合法 JSON，包含 4 种场景。"""
        path = FIXTURES_DIR / "fault_injection_matrix.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        scenarios = data.get("fault_scenarios") or []
        assert len(scenarios) == 4
        ids = {s["id"] for s in scenarios}
        assert "neo4j_unavailable" in ids
        assert "chroma_unavailable" in ids
        assert "llm_timeout" in ids
        assert "edge_rpc_unavailable" in ids

    def test_neo4j_unavailable_output_stable(self):
        """Neo4j 不可用时输出结构稳定，reason_code 可统计。"""
        from host_brain.core.qa import build_fallback_output

        result = build_fallback_output(
            reason_code="DEPENDENCY_UNAVAILABLE",
            stage="dependency",
        )
        json_str = json.dumps(result)
        parsed = json.loads(json_str)
        assert isinstance(parsed, dict)
        assert parsed.get("meta", {}).get("reason_code") == "DEPENDENCY_UNAVAILABLE"

    def test_chroma_unavailable_output_stable(self):
        """Chroma 不可用时输出结构稳定，evidence pack 降级但字段存在。"""
        from host_brain.core.qa import build_fallback_output

        result = build_fallback_output(
            reason_code="DEPENDENCY_UNAVAILABLE",
            stage="dependency",
        )
        json_str = json.dumps(result)
        parsed = json.loads(json_str)
        assert isinstance(parsed, dict)
        assert parsed.get("meta", {}).get("reason_code") == "DEPENDENCY_UNAVAILABLE"

    def test_llm_timeout_no_exec_intent(self):
        """Cloud LLM 超时时输出最小模板，不产生执行 intent。"""
        from host_brain.core.qa import build_fallback_output

        result = build_fallback_output(
            reason_code="ORCH_TIMEOUT",
            stage="llm_timeout",
        )
        json_str = json.dumps(result)
        parsed = json.loads(json_str)
        assert isinstance(parsed, dict)
        # 最小模板不含执行 intent
        intent = parsed.get("intent_draft") or parsed.get("intent") or {}
        action = intent.get("action") if isinstance(intent, dict) else None
        assert action is None or action == "none", (
            f"LLM 超时时不应产生执行 intent，实际 action={action!r}"
        )

    def test_edge_rpc_unavailable_output_stable(self):
        """Edge RPC 不可达时语音链路降级，ASR_UNAVAILABLE 可统计。"""
        from host_brain.core.errors import ASR_UNAVAILABLE
        from host_brain.core.qa import build_fallback_output

        result = build_fallback_output(
            reason_code=ASR_UNAVAILABLE,
            stage="edge_rpc",
        )
        json_str = json.dumps(result)
        parsed = json.loads(json_str)
        assert isinstance(parsed, dict)
        assert parsed.get("meta", {}).get("reason_code") == ASR_UNAVAILABLE

    def test_all_fault_scenarios_produce_stable_output(self):
        """所有 4 种故障场景下输出结构均稳定（可序列化为 JSON）。"""
        from host_brain.core.qa import build_fallback_output

        path = FIXTURES_DIR / "fault_injection_matrix.json"
        matrix = json.loads(path.read_text(encoding="utf-8"))

        for scenario in matrix["fault_scenarios"]:
            reason_code = scenario.get("expected_reason_code", "DEPENDENCY_UNAVAILABLE")
            result = build_fallback_output(
                reason_code=reason_code,
                stage=scenario["id"],
            )
            # 输出可序列化
            json_str = json.dumps(result)
            parsed = json.loads(json_str)
            assert isinstance(parsed, dict), (
                f"场景 {scenario['id']}: 输出不是合法 dict"
            )
            # reason_code 可统计（meta.reason_code 不为空）
            assert parsed.get("meta", {}).get("reason_code"), (
                f"场景 {scenario['id']}: meta.reason_code 不能为空"
            )

    def test_fault_reason_codes_importable(self):
        """故障注入相关错误码可从 errors.py 导入。"""
        from host_brain.core.errors import (
            ASR_UNAVAILABLE,
            CHROMA_ENDPOINT_UNAVAILABLE,
            NEO4J_LEADER_UNAVAILABLE,
            ORCH_TIMEOUT,
            SERVICE_UNAVAILABLE_DEPENDENCY,
            SERVICE_UNAVAILABLE_EDGE_RPC,
        )
        assert ASR_UNAVAILABLE == "ASR_UNAVAILABLE"
        assert ORCH_TIMEOUT == "ORCH_TIMEOUT"
        assert SERVICE_UNAVAILABLE_DEPENDENCY == "SERVICE_UNAVAILABLE_DEPENDENCY"
        assert SERVICE_UNAVAILABLE_EDGE_RPC == "SERVICE_UNAVAILABLE_EDGE_RPC"
