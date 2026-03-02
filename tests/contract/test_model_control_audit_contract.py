"""T-Audit-06 / T-Replay-02: model_control 审计入库与 request 级回放透出。

验收断言：
- 模型调用写入 model_control_log 且字段完整（request_id/node_id/model_profile_id/provider/model_id/param_hash/drift/governance_refs）
- request 级 trace_request 可聚合 model_control_log 条目
- param drift 入库后 drift_detected=True 且 reason_code=ORCH_PARAM_DRIFT
- 票据缺失入库后 reason_code=SKILL_TOKEN_MISSING
- 多次调用按序可查
- 回放报告可通过 request_id 定位到参数签名与票据锚点
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    ORCH_PARAM_DRIFT,
    SKILL_TOKEN_MISSING,
)
from host_brain.integrations.graph_runtime import (
    S_ORCH_RUN,
    STATUS_DEGRADED,
    STATUS_OK,
    GraphRuntimeContract,
)
from host_brain.integrations.graph_contract import build_model_param_hash


ROOT = Path(__file__).resolve().parents[2]

NOW_MS = 1_800_200_000_000


def _make_runtime() -> tuple[GraphRuntimeContract, SQLiteLedger]:
    ledger = SQLiteLedger(db_path=":memory:")
    runtime = GraphRuntimeContract(ledger=ledger, default_timeout_ms=5000)
    return runtime, ledger


def _inline_model_graph(*, expected_param_hash: str | None = None, require_approval: bool = False) -> dict[str, Any]:
    """构建内联模型节点图配置。"""
    binding: dict[str, Any] = {
        "node_id": "respond",
        "model_node_id": "test_model",
        "task": "CHAT",
        "required": True,
    }
    if require_approval:
        binding["require_approval_token"] = True

    model_node: dict[str, Any] = {
        "provider": "mock",
        "model_id": "mock-model-v1",
        "temperature": 0.5,
        "max_output_tokens": 200,
        "timeout_ms": 3000,
        "provider_fn": lambda _: json.dumps(
            {"narrative": {"text": "ok"}, "intent_draft": {}, "meta": {"draft_only": True}},
            ensure_ascii=False,
        ),
    }
    if expected_param_hash is not None:
        model_node["expected_param_hash"] = expected_param_hash

    return {
        "graph_id": "graph.audit.v1",
        "version": "v1",
        "entry_node": "respond",
        "model_profile_id": "audit_test_profile",
        "model_nodes": {"test_model": model_node},
        "node_bindings": [binding],
        "edges": [],
        "fallback_edges": [],
    }


# ---------------------------------------------------------------------------
# T-Audit-06: model_control 入库完整性
# ---------------------------------------------------------------------------

class TestModelControlAuditIngest:
    """model_control_log 审计入库完整性。"""

    def test_normal_call_writes_model_control_log(self) -> None:
        """正常模型调用后 model_control_log 有 1 行。"""
        runtime, ledger = _make_runtime()
        graph = _inline_model_graph()
        runtime.run_graph(request_id="req_audit_001", graph_config=graph, payload={"input": "hello"}, ts_ms=NOW_MS)

        rows = ledger.model_control_log
        assert len(rows) == 1
        row = rows[0]
        assert row["request_id"] == "req_audit_001"
        assert row["model_profile_id"] == "audit_test_profile"
        assert row["provider"] == "mock"
        assert row["model_id"] == "mock-model-v1"
        assert row["actual_param_hash"] is not None
        assert row["stage"] == S_ORCH_RUN

    def test_model_control_log_has_param_hash(self) -> None:
        """param_hash 与 build_model_param_hash 一致。"""
        runtime, ledger = _make_runtime()
        graph = _inline_model_graph()
        runtime.run_graph(request_id="req_audit_002", graph_config=graph, payload={"input": "hello"}, ts_ms=NOW_MS)

        expected = build_model_param_hash(
            provider="mock", model_id="mock-model-v1",
            temperature=0.5, max_output_tokens=200, timeout_ms=3000,
        )
        row = ledger.model_control_log[0]
        assert row["actual_param_hash"] == expected

    def test_drift_detected_writes_to_audit(self) -> None:
        """param drift 入库后 drift_detected=True + reason_code=ORCH_PARAM_DRIFT。"""
        runtime, ledger = _make_runtime()
        graph = _inline_model_graph(expected_param_hash="wrong-hash")
        runtime.run_graph(request_id="req_audit_003", graph_config=graph, payload={"input": "hello"}, ts_ms=NOW_MS)

        row = ledger.model_control_log[0]
        assert row["drift_detected"] == 1  # SQLite stores bool as int
        assert row["reason_code"] == ORCH_PARAM_DRIFT
        assert row["expected_param_hash"] == "wrong-hash"
        assert row["actual_param_hash"] != "wrong-hash"

    def test_token_missing_writes_to_audit(self) -> None:
        """票据缺失入库后 reason_code=SKILL_TOKEN_MISSING。"""
        runtime, ledger = _make_runtime()
        graph = _inline_model_graph(require_approval=True)
        runtime.run_graph(request_id="req_audit_004", graph_config=graph, payload={"input": "hello"}, ts_ms=NOW_MS)

        row = ledger.model_control_log[0]
        assert row["reason_code"] == SKILL_TOKEN_MISSING

    def test_governance_refs_in_audit(self) -> None:
        """governance_refs（approval_token_ref/evidence_ref）写入审计。"""
        runtime, ledger = _make_runtime()
        graph = _inline_model_graph()
        runtime.run_graph(
            request_id="req_audit_005",
            graph_config=graph,
            payload={"input": "hello", "approval_token_ref": "token::abc", "evidence_ref": "ev::xyz"},
            ts_ms=NOW_MS,
        )

        row = ledger.model_control_log[0]
        assert row["approval_token_ref"] == "token::abc"
        assert row["evidence_ref"] == "ev::xyz"

    def test_multiple_calls_sequential(self) -> None:
        """多次调用按序入库。"""
        runtime, ledger = _make_runtime()
        for i in range(3):
            graph = _inline_model_graph()
            runtime.run_graph(
                request_id=f"req_audit_multi_{i}",
                graph_config=graph,
                payload={"input": f"msg_{i}"},
                ts_ms=NOW_MS + i,
            )

        rows = ledger.model_control_log
        assert len(rows) == 3
        assert [r["request_id"] for r in rows] == [f"req_audit_multi_{i}" for i in range(3)]


# ---------------------------------------------------------------------------
# T-Replay-02: request 级回放透出 model_control
# ---------------------------------------------------------------------------

class TestReplayModelControl:
    """request 级回放聚合 model_control_log。"""

    def test_trace_request_includes_model_control(self) -> None:
        """trace_request 输出包含 model_control_log。"""
        runtime, ledger = _make_runtime()
        graph = _inline_model_graph()
        runtime.run_graph(request_id="req_replay_001", graph_config=graph, payload={"input": "hello"}, ts_ms=NOW_MS)
        # 需要同时写 intent + governor 才能获得 COMPLETE 状态
        ledger.log_intent({"request_id": "req_replay_001", "intent_id": "int_001", "session_id": "s1", "nonce": "n1", "ts_ms": NOW_MS})
        ledger.log_governor({"request_id": "req_replay_001", "intent_id": "int_001", "decision": "APPROVE", "reason_code": None, "stage": "S_GOV", "ts_ms": NOW_MS})

        trace = ledger.trace_request("req_replay_001")
        assert trace["status"] == "COMPLETE"
        assert "model_control_log" in trace
        assert len(trace["model_control_log"]) == 1
        assert trace["model_control_log"][0]["provider"] == "mock"

    def test_trace_request_isolates_by_request_id(self) -> None:
        """不同 request_id 的 model_control 不混入。"""
        runtime, ledger = _make_runtime()
        graph = _inline_model_graph()
        runtime.run_graph(request_id="req_replay_A", graph_config=graph, payload={"input": "a"}, ts_ms=NOW_MS)
        runtime.run_graph(request_id="req_replay_B", graph_config=graph, payload={"input": "b"}, ts_ms=NOW_MS + 1)

        trace_a = ledger.trace_request("req_replay_A")
        trace_b = ledger.trace_request("req_replay_B")
        assert len(trace_a["model_control_log"]) == 1
        assert len(trace_b["model_control_log"]) == 1
        assert trace_a["model_control_log"][0]["request_id"] == "req_replay_A"
        assert trace_b["model_control_log"][0]["request_id"] == "req_replay_B"

    def test_drift_visible_in_replay(self) -> None:
        """漂移在回放报告中可见（drift_detected + reason_code）。"""
        runtime, ledger = _make_runtime()
        graph = _inline_model_graph(expected_param_hash="bad-hash")
        runtime.run_graph(request_id="req_replay_drift", graph_config=graph, payload={"input": "hello"}, ts_ms=NOW_MS)

        trace = ledger.trace_request("req_replay_drift")
        mc = trace["model_control_log"]
        assert len(mc) == 1
        assert mc[0]["drift_detected"] == 1
        assert mc[0]["reason_code"] == ORCH_PARAM_DRIFT

    def test_empty_request_returns_incomplete(self) -> None:
        """不存在的 request_id 回放返回 INCOMPLETE。"""
        _, ledger = _make_runtime()
        trace = ledger.trace_request("nonexistent_request")
        assert trace["status"] == "INCOMPLETE"
        assert trace["model_control_log"] == []
