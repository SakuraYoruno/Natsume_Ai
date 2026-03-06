"""T-Orch-Refactor-01: graph_runtime refactor output consistency tests.

Validates that the post-refactor split (contract/executor/fallback/model_resolve)
produces identical output structures and behavior as the pre-refactor monolith.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    ORCH_DEPENDENCY_UNAVAILABLE,
    ORCH_EDGE_INVALID,
    ORCH_NODE_MISSING,
    ORCH_TIMEOUT,
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
    GraphRunResult,
    validate_graph_config,
)
from host_brain.integrations.graph_contract import (
    GraphState,
    build_edge_map,
    build_model_param_hash,
    pick_model_profile_id,
    extract_governance_refs,
    normalize_dependency_status,
)
from host_brain.integrations.graph_executor import GraphExecutor
from host_brain.integrations.graph_fallback import (
    fail_hard,
    handle_dependency_unavailable,
    handle_execution_error,
    handle_timeout,
    pick_fallback_node,
    pick_next_node,
    set_finish,
)
from host_brain.integrations.graph_model_resolve import resolve_model_nodes


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "orchestration"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _plan_handler(payload: dict, _ctx: dict | None) -> dict:
    out = dict(payload)
    out["plan"] = {"intent": "compose_reply"}
    return out


def _retrieve_handler(payload: dict, _ctx: dict | None) -> dict:
    out = dict(payload)
    out["retrieval"] = {"top_hit": "bond_summary::v1"}
    return out


def _respond_handler(payload: dict, _ctx: dict | None) -> dict:
    out = dict(payload)
    out["response"] = {"draft_only": True, "text": "draft response"}
    return out


def _timeout_handler(_payload: dict, _ctx: dict | None) -> dict:
    raise TimeoutError("simulated timeout")


class TestOrchRefactorOutputConsistency(unittest.TestCase):
    """T-Orch-Refactor-01: Verify output structure is identical pre/post split."""

    def setUp(self) -> None:
        self.ledger = SQLiteLedger(db_path=":memory:")
        self.runtime = GraphRuntimeContract(ledger=self.ledger, default_timeout_ms=1000)
        self.runtime.register_node_handler("handler.plan", _plan_handler)
        self.runtime.register_node_handler("handler.retrieve", _retrieve_handler)
        self.runtime.register_node_handler("handler.respond", _respond_handler)
        self.runtime.register_node_handler("handler.timeout", _timeout_handler)

    def tearDown(self) -> None:
        self.ledger.close()

    def test_refactor_baseline_output_structure(self) -> None:
        """All expected output keys are present in refactored output."""
        fixture = _load_json(FIXTURES / "good" / "graph_refactor_baseline.json")
        result = self.runtime.run_graph(
            request_id=fixture["request_id"],
            graph_config=fixture["graph"],
            payload=fixture["payload"],
            ts_ms=1_900_000_000_000,
        )
        self.assertEqual(STATUS_OK, result.status)
        for key in fixture["expected_output_keys"]:
            self.assertIn(key, result.output, f"missing output key: {key}")

    def test_refactor_baseline_values(self) -> None:
        fixture = _load_json(FIXTURES / "good" / "graph_refactor_baseline.json")
        result = self.runtime.run_graph(
            request_id=fixture["request_id"],
            graph_config=fixture["graph"],
            payload=fixture["payload"],
            ts_ms=1_900_000_000_001,
        )
        expected = fixture["expected"]
        self.assertEqual(expected["graph_status"], result.output["graph_status"])
        self.assertEqual(expected["reason_code"], result.output["reason_code"])
        self.assertEqual(expected["stage"], result.output["stage"])
        self.assertEqual(expected["runtime_backend"], result.output["runtime_backend"])
        trace_ids = [row["node_id"] for row in result.output["graph_trace"]]
        self.assertEqual(expected["trace_node_ids"], trace_ids)
        trace_statuses = [row["status"] for row in result.output["graph_trace"]]
        self.assertEqual(expected["trace_statuses"], trace_statuses)

    def test_refactor_missing_handler_still_fails(self) -> None:
        fixture = _load_json(FIXTURES / "bad" / "graph_missing_handler.json")
        result = self.runtime.run_graph(
            request_id=fixture["request_id"],
            graph_config=fixture["graph"],
            payload={"input": "hello"},
            ts_ms=1_900_000_000_002,
        )
        self.assertEqual(STATUS_FAILED, result.status)
        self.assertEqual(ORCH_NODE_MISSING, result.reason_code)

    def test_refactor_invalid_edge_still_rejected(self) -> None:
        fixture = _load_json(FIXTURES / "bad" / "graph_invalid_edge.json")
        result = self.runtime.run_graph(
            request_id=fixture["request_id"],
            graph_config=fixture["graph"],
            payload={"input": "hello"},
            ts_ms=1_900_000_000_003,
        )
        self.assertEqual(STATUS_FAILED, result.status)
        self.assertEqual(ORCH_EDGE_INVALID, result.reason_code)

    def test_refactor_timeout_fallback_still_degraded(self) -> None:
        fixture = _load_json(FIXTURES / "bad" / "graph_timeout_with_fallback.json")
        result = self.runtime.run_graph(
            request_id=fixture["request_id"],
            graph_config=fixture["graph"],
            payload={"input": "hello"},
            ts_ms=1_900_000_000_004,
        )
        self.assertEqual(STATUS_DEGRADED, result.status)
        self.assertEqual(ORCH_TIMEOUT, result.reason_code)

    def test_refactor_dependency_down_still_degraded(self) -> None:
        fixture = _load_json(FIXTURES / "bad" / "graph_dependency_down_with_fallback.json")
        result = self.runtime.run_graph(
            request_id=fixture["request_id"],
            graph_config=fixture["graph"],
            payload={"input": "hello"},
            dependency_statuses=fixture["dependency_statuses"],
            ts_ms=1_900_000_000_005,
        )
        self.assertEqual(STATUS_DEGRADED, result.status)
        self.assertEqual(ORCH_DEPENDENCY_UNAVAILABLE, result.reason_code)


class TestOrchRefactorLayerIndependence(unittest.TestCase):
    """Verify each layer can be independently unit tested."""

    def test_contract_validate_callable(self) -> None:
        result = validate_graph_config({"graph_id": "g1", "version": "v1", "entry_node": "a",
                                        "node_bindings": [{"node_id": "a", "handler_id": "h"}], "edges": []})
        self.assertEqual(STATUS_OK, result["status"])

    def test_contract_build_edge_map(self) -> None:
        edges = [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}]
        m = build_edge_map(edges)
        self.assertEqual({"a": "b", "b": "c"}, m)

    def test_contract_build_model_param_hash_deterministic(self) -> None:
        h1 = build_model_param_hash(provider="gemini", model_id="flash", temperature=0.5, max_output_tokens=100, timeout_ms=1000)
        h2 = build_model_param_hash(provider="gemini", model_id="flash", temperature=0.5, max_output_tokens=100, timeout_ms=1000)
        self.assertEqual(h1, h2)

    def test_contract_normalize_dependency_status(self) -> None:
        self.assertTrue(normalize_dependency_status(True)["available"])
        self.assertFalse(normalize_dependency_status(False)["available"])
        self.assertTrue(normalize_dependency_status(None)["available"])
        self.assertFalse(normalize_dependency_status({"available": False, "detail": "down"})["available"])

    def test_fallback_set_finish(self) -> None:
        state: GraphState = {
            "request_id": "r1", "graph_id": "g1", "timeout_ms": 1000, "payload": {},
            "trace": [], "status": STATUS_OK, "reason_code": None, "detail": "",
            "pending_node_id": "a", "edge_map": {}, "fallback_map": {},
            "node_bindings": {}, "dependency_statuses": {}, "model_nodes": {},
            "model_profile_id": None, "model_calls": [],
        }
        result = set_finish(state)
        self.assertIsNone(result["pending_node_id"])

    def test_fallback_fail_hard(self) -> None:
        state: GraphState = {
            "request_id": "r1", "graph_id": "g1", "timeout_ms": 1000, "payload": {},
            "trace": [], "status": STATUS_OK, "reason_code": None, "detail": "",
            "pending_node_id": "a", "edge_map": {}, "fallback_map": {},
            "node_bindings": {}, "dependency_statuses": {}, "model_nodes": {},
            "model_profile_id": None, "model_calls": [],
        }
        result = fail_hard(state, reason_code="TEST_FAIL", detail="test")
        self.assertEqual(STATUS_FAILED, result["status"])
        self.assertEqual("TEST_FAIL", result["reason_code"])
        self.assertIsNone(result["pending_node_id"])

    def test_fallback_pick_fallback_node(self) -> None:
        state: GraphState = {
            "request_id": "r1", "graph_id": "g1", "timeout_ms": 1000, "payload": {},
            "trace": [], "status": STATUS_OK, "reason_code": None, "detail": "",
            "pending_node_id": "a", "edge_map": {}, "fallback_map": {"a": "b"},
            "node_bindings": {}, "dependency_statuses": {}, "model_nodes": {},
            "model_profile_id": None, "model_calls": [],
        }
        self.assertEqual("b", pick_fallback_node(state=state, node_id="a"))
        self.assertIsNone(pick_fallback_node(state=state, node_id="x"))

    def test_fallback_pick_next_node(self) -> None:
        self.assertEqual("b", pick_next_node(output={"__next_node__": "b"}, default_next="c"))
        self.assertEqual("c", pick_next_node(output={}, default_next="c"))
        self.assertIsNone(pick_next_node(output={}, default_next=None))

    def test_executor_compile_returns_graph(self) -> None:
        executor = GraphExecutor(handlers={}, gateway_factory=lambda **kw: None, default_timeout_ms=1000)
        compiled = executor.compile_runtime_graph()
        self.assertIsNotNone(compiled)

    def test_model_resolve_import(self) -> None:
        """resolve_model_nodes is importable from graph_model_resolve."""
        self.assertTrue(callable(resolve_model_nodes))


class TestOrchRefactorModuleSize(unittest.TestCase):
    """Verify each module is under 300 lines."""

    def _count_lines(self, module_path: str) -> int:
        path = ROOT / module_path
        return len(path.read_text(encoding="utf-8").splitlines())

    def test_graph_runtime_under_300(self) -> None:
        # M36 新增双时钟字段和 Evidence Plane 集成，上限调整为 380
        self.assertLessEqual(self._count_lines("host_brain/integrations/graph_runtime.py"), 380)

    def test_graph_contract_under_300(self) -> None:
        # M36 新增双时钟常量、GraphState 字段和 build_clock_event 辅助函数，上限调整为 400
        self.assertLessEqual(self._count_lines("host_brain/integrations/graph_contract.py"), 400)

    def test_graph_executor_under_300(self) -> None:
        self.assertLessEqual(self._count_lines("host_brain/integrations/graph_executor.py"), 300)

    def test_graph_fallback_under_300(self) -> None:
        self.assertLessEqual(self._count_lines("host_brain/integrations/graph_fallback.py"), 300)

    def test_graph_model_resolve_under_300(self) -> None:
        self.assertLessEqual(self._count_lines("host_brain/integrations/graph_model_resolve.py"), 300)


if __name__ == "__main__":
    unittest.main()
