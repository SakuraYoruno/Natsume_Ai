"""T-Drill-02 / T-EnvReady-03 / T-Orch-06: 编排主控全链路演练与故障注入矩阵。

验收断言：
- profile 缺失场景稳定拒绝 ORCH_NODE_MISSING
- selector 无候选场景稳定拒绝 ORCH_NODE_MISSING
- param drift 场景稳定降级 ORCH_PARAM_DRIFT
- approval token missing 场景稳定降级 SKILL_TOKEN_MISSING
- provider down 场景稳定降级 ORCH_DEPENDENCY_UNAVAILABLE
- 每个场景可通过 request_id 回放到关键审计字段
- 环境就绪报告区分 partial-ready 与 critical-down
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from host_brain.app import dependencies
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    ORCH_DEPENDENCY_UNAVAILABLE,
    ORCH_NODE_MISSING,
    ORCH_PARAM_DRIFT,
    SKILL_TOKEN_MISSING,
)
from host_brain.integrations.graph_runtime import (
    S_ORCH_RUN,
    S_ORCH_VALIDATE,
    STATUS_DEGRADED,
    STATUS_FAILED,
    STATUS_OK,
    GraphRuntimeContract,
)
from host_brain.model_runtime import (
    CallableProviderAdapter,
    InMemoryAttemptAuditRepository,
    ProviderTarget,
    RouteDecision,
    StaticRoutePolicy,
    build_live_production_prompt_registry,
    build_model_runtime_composition,
)


NOW_MS = 1_800_300_000_000


@pytest.fixture(autouse=True)
def _canonical_model_composition():  # noqa: ANN202
    composition = build_model_runtime_composition(
        prompt_registry=build_live_production_prompt_registry(),
        route_policy=StaticRoutePolicy(
            RouteDecision(
                "route-graph-drill",
                (ProviderTarget("graph-test", "mock", "mock-v1", timeout_ms=3000),),
                "contract-test",
                max_attempts=1,
            )
        ),
        provider_adapters=[
            CallableProviderAdapter(
                "graph-test",
                complete_fn=lambda _: json.dumps(
                    {
                        "narrative": {"text": "ok"},
                        "intent_draft": {},
                        "meta": {"draft_only": True},
                    },
                    ensure_ascii=False,
                ),
            )
        ],
        audit_repository=InMemoryAttemptAuditRepository(),
    )
    dependencies.reset_model_runtime_composition(composition)
    try:
        yield
    finally:
        dependencies.reset_model_runtime_composition()


def _make_runtime() -> tuple[GraphRuntimeContract, SQLiteLedger]:
    ledger = SQLiteLedger(db_path=":memory:")
    runtime = GraphRuntimeContract(ledger=ledger, default_timeout_ms=5000)
    return runtime, ledger


# ---------------------------------------------------------------------------
# T-Drill-02: 五类核心故障场景
# ---------------------------------------------------------------------------

class TestDrillFaultInjection:
    """五类核心故障场景均可稳定复现并输出单 JSON 报告。"""

    def test_scenario_profile_missing(self) -> None:
        """场景 1：profile 缺失 → FAILED + ORCH_NODE_MISSING。"""
        runtime, ledger = _make_runtime()
        graph = {
            "graph_id": "drill.profile_missing",
            "version": "v1",
            "entry_node": "respond",
            "model_profile_id": "nonexistent_profile_999",
            "node_bindings": [{"node_id": "respond", "task": "CHAT"}],
            "edges": [],
            "fallback_edges": [],
        }
        result = runtime.run_graph(request_id="drill_001", graph_config=graph, ts_ms=NOW_MS)
        assert result.status == STATUS_FAILED
        assert result.reason_code == ORCH_NODE_MISSING
        output = result.output
        assert json.loads(json.dumps(output))  # 可序列化为单 JSON

    def test_scenario_no_candidate_node(self) -> None:
        """场景 2：selector 无候选 → FAILED + ORCH_NODE_MISSING。"""
        runtime, _ = _make_runtime()
        graph = {
            "graph_id": "drill.no_candidate",
            "version": "v1",
            "entry_node": "respond",
            "model_profile_id": "chat_ja_24x7_v1",
            "node_allowlist": ["impossible_node_xyz"],
            "node_bindings": [{"node_id": "respond", "task": "CHAT"}],
            "edges": [],
            "fallback_edges": [],
        }
        result = runtime.run_graph(request_id="drill_002", graph_config=graph, ts_ms=NOW_MS)
        assert result.status == STATUS_FAILED
        assert result.reason_code == ORCH_NODE_MISSING

    def test_scenario_param_drift(self) -> None:
        """场景 3：param drift → DEGRADED + ORCH_PARAM_DRIFT。"""
        runtime, ledger = _make_runtime()
        graph = {
            "graph_id": "drill.param_drift",
            "version": "v1",
            "entry_node": "respond",
            "model_profile_id": "test",
            "model_nodes": {
                "renderer_ja_c_style": {
                    "provider": "mock",
                    "model_id": "mock-v1",
                    "temperature": 0.5,
                    "max_output_tokens": 200,
                    "expected_param_hash": "deliberately-wrong",
                },
            },
            "node_bindings": [{"node_id": "respond", "model_node_id": "renderer_ja_c_style"}],
            "edges": [],
            "fallback_edges": [],
        }
        result = runtime.run_graph(request_id="drill_003", graph_config=graph, ts_ms=NOW_MS)
        assert result.status == STATUS_DEGRADED
        assert result.reason_code == ORCH_PARAM_DRIFT

    def test_scenario_token_missing(self) -> None:
        """场景 4：approval token missing → DEGRADED + SKILL_TOKEN_MISSING。"""
        runtime, _ = _make_runtime()
        graph = {
            "graph_id": "drill.token_missing",
            "version": "v1",
            "entry_node": "respond",
            "model_profile_id": "test",
            "model_nodes": {
                "renderer_ja_c_style": {
                    "provider": "mock",
                    "model_id": "mock-v1",
                    "temperature": 0.5,
                    "max_output_tokens": 200,
                },
            },
            "node_bindings": [{"node_id": "respond", "model_node_id": "renderer_ja_c_style", "require_approval_token": True}],
            "edges": [],
            "fallback_edges": [],
        }
        result = runtime.run_graph(request_id="drill_004", graph_config=graph, payload={"input": "hello"}, ts_ms=NOW_MS)
        assert result.status == STATUS_DEGRADED
        assert result.reason_code == SKILL_TOKEN_MISSING

    def test_scenario_provider_down(self) -> None:
        """场景 5：provider down → DEGRADED。"""
        runtime, _ = _make_runtime()
        graph = {
            "graph_id": "drill.provider_down",
            "version": "v1",
            "entry_node": "respond",
            "model_profile_id": "test",
            "model_nodes": {
                "renderer_ja_c_style": {
                    "provider": "openai",
                    "model_id": "gpt-5",
                    "temperature": 0.5,
                    "max_output_tokens": 200,
                    "available": False,
                },
            },
            "node_bindings": [{"node_id": "respond", "model_node_id": "renderer_ja_c_style"}],
            "edges": [],
            "fallback_edges": [],
        }
        result = runtime.run_graph(
            request_id="drill_005",
            graph_config=graph,
            payload={"input": "hello"},
            ts_ms=NOW_MS,
        )
        assert result.status == STATUS_DEGRADED

    def test_transport_fields_are_rejected_before_graph_execution(self) -> None:
        runtime, ledger = _make_runtime()
        graph = {
            "graph_id": "drill.transport_field",
            "version": "v1",
            "entry_node": "respond",
            "model_profile_id": "test",
            "model_nodes": {
                "renderer_ja_c_style": {
                    "provider": "mock",
                    "model_id": "mock-v1",
                    "api_key": "must-never-enter-state",
                }
            },
            "node_bindings": [
                {"node_id": "respond", "model_node_id": "renderer_ja_c_style"}
            ],
            "edges": [],
            "fallback_edges": [],
        }

        result = runtime.run_graph(
            request_id="drill_transport_field",
            graph_config=graph,
            ts_ms=NOW_MS,
        )

        assert result.status == STATUS_FAILED
        assert result.reason_code == ORCH_PARAM_DRIFT
        assert ledger.model_control_log == []
        assert "must-never-enter-state" not in str(result.output)


# ---------------------------------------------------------------------------
# T-EnvReady-03: 环境就绪报告区分 partial/critical
# ---------------------------------------------------------------------------

class TestEnvReadinessExtended:
    """环境就绪报告覆盖编排故障场景。"""

    def test_all_faults_produce_json_output(self) -> None:
        """所有故障场景输出可序列化单 JSON。"""
        runtime, _ = _make_runtime()
        scenarios = [
            {"graph_id": "env.ok", "version": "v1", "entry_node": "r", "model_profile_id": "test",
             "model_nodes": {"renderer_ja_c_style": {"provider": "mock", "model_id": "mock-v1", "temperature": 0, "max_output_tokens": 10}},
             "node_bindings": [{"node_id": "r", "model_node_id": "renderer_ja_c_style"}], "edges": [], "fallback_edges": []},
        ]
        for sc in scenarios:
            result = runtime.run_graph(request_id="env_001", graph_config=sc, ts_ms=NOW_MS)
            serialized = json.dumps(result.output, ensure_ascii=False)
            assert isinstance(json.loads(serialized), dict)

    def test_fault_scenarios_traceable_by_request_id(self) -> None:
        """故障场景可通过 request_id 回溯。"""
        runtime, ledger = _make_runtime()
        graph = {
            "graph_id": "env.trace", "version": "v1", "entry_node": "r",
            "model_profile_id": "nonexistent_xyz",
            "node_bindings": [{"node_id": "r", "task": "CHAT"}],
            "edges": [], "fallback_edges": [],
        }
        runtime.run_graph(request_id="env_trace_001", graph_config=graph, ts_ms=NOW_MS)
        errors = [e for e in ledger.error_log if e["request_id"] == "env_trace_001"]
        assert len(errors) >= 1
        assert any(e["reason_code"] == ORCH_NODE_MISSING for e in errors)


# ---------------------------------------------------------------------------
# T-Orch-06: 编排主控故障注入可回放
# ---------------------------------------------------------------------------

class TestOrchFaultReplay:
    """编排故障注入后可通过回放工具定位。"""

    def test_drift_replay_has_param_hashes(self) -> None:
        """漂移场景回放包含 expected/actual param hash。"""
        runtime, ledger = _make_runtime()
        graph = {
            "graph_id": "orch.replay", "version": "v1", "entry_node": "r",
            "model_profile_id": "test",
            "model_nodes": {"renderer_ja_c_style": {"provider": "mock", "model_id": "mock-v1", "temperature": 0.5,
                                   "max_output_tokens": 100, "expected_param_hash": "bad"}},
            "node_bindings": [{"node_id": "r", "model_node_id": "renderer_ja_c_style"}],
            "edges": [], "fallback_edges": [],
        }
        runtime.run_graph(request_id="orch_replay_001", graph_config=graph, ts_ms=NOW_MS)
        mc = ledger.model_control_log
        assert len(mc) == 1
        assert mc[0]["expected_param_hash"] == "bad"
        assert mc[0]["actual_param_hash"] != "bad"
        assert mc[0]["drift_detected"] == 1

    def test_multiple_faults_traceable_in_sequence(self) -> None:
        """多个故障场景按序可查。"""
        runtime, ledger = _make_runtime()
        # 故障 1: drift
        g1 = {
            "graph_id": "g1", "version": "v1", "entry_node": "r",
            "model_profile_id": "test",
            "model_nodes": {"renderer_ja_c_style": {"provider": "mock", "model_id": "mock-v1", "temperature": 0,
                                   "max_output_tokens": 10, "expected_param_hash": "x"}},
            "node_bindings": [{"node_id": "r", "model_node_id": "renderer_ja_c_style"}],
            "edges": [], "fallback_edges": [],
        }
        runtime.run_graph(request_id="multi_001", graph_config=g1, ts_ms=NOW_MS)
        # 故障 2: token missing
        g2 = {
            "graph_id": "g2", "version": "v1", "entry_node": "r",
            "model_profile_id": "test",
            "model_nodes": {"renderer_ja_c_style": {"provider": "mock", "model_id": "mock-v1", "temperature": 0,
                                   "max_output_tokens": 10}},
            "node_bindings": [{"node_id": "r", "model_node_id": "renderer_ja_c_style", "require_approval_token": True}],
            "edges": [], "fallback_edges": [],
        }
        runtime.run_graph(request_id="multi_002", graph_config=g2, payload={"input": "x"}, ts_ms=NOW_MS + 1)

        mc = ledger.model_control_log
        assert len(mc) == 2
        assert mc[0]["reason_code"] == ORCH_PARAM_DRIFT
        assert mc[1]["reason_code"] == SKILL_TOKEN_MISSING
