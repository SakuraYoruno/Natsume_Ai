import json
import unittest
from pathlib import Path

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    ORCH_DEPENDENCY_UNAVAILABLE,
    ORCH_EDGE_INVALID,
    ORCH_NODE_MISSING,
    ORCH_TIMEOUT,
)
from host_brain.integrations.graph_runtime import (
    S_ORCH_RUN,
    S_ORCH_VALIDATE,
    STATUS_DEGRADED,
    STATUS_FAILED,
    STATUS_OK,
    GraphRuntimeContract,
)


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


class TestOrchestrationGraphContract(unittest.TestCase):
    def setUp(self) -> None:
        self.ledger = SQLiteLedger(db_path=":memory:")
        self.runtime = GraphRuntimeContract(ledger=self.ledger, default_timeout_ms=1000)
        self.runtime.register_node_handler("handler.plan", _plan_handler)
        self.runtime.register_node_handler("handler.retrieve", _retrieve_handler)
        self.runtime.register_node_handler("handler.respond", _respond_handler)
        self.runtime.register_node_handler("handler.timeout", _timeout_handler)

    def tearDown(self) -> None:
        self.ledger.close()

    def test_t_orch_01_graph_runtime_output_is_stable_and_replayable(self) -> None:
        fixture = _load_json(FIXTURES / "good" / "graph_main.json")
        result = self.runtime.run_graph(
            request_id=fixture["request_id"],
            graph_config=fixture["graph"],
            payload={"input": "hello"},
            ts_ms=1_800_120_000_000,
        )

        self.assertEqual(STATUS_OK, result.status)
        self.assertIsNone(result.reason_code)
        self.assertEqual(S_ORCH_RUN, result.stage)
        self.assertEqual(STATUS_OK, result.output["graph_status"])
        self.assertEqual(f"request::{fixture['request_id']}", result.output["replay_ref"])
        self.assertEqual(["plan", "retrieve", "respond"], [row["node_id"] for row in result.output["graph_trace"]])
        self.assertEqual("OK", result.output["graph_trace"][0]["status"])
        json.loads(json.dumps(result.output, ensure_ascii=False))

    def test_t_orch_01_missing_handler_is_rejected(self) -> None:
        fixture = _load_json(FIXTURES / "bad" / "graph_missing_handler.json")
        result = self.runtime.run_graph(
            request_id=fixture["request_id"],
            graph_config=fixture["graph"],
            payload={"input": "hello"},
            ts_ms=1_800_120_000_001,
        )

        self.assertEqual(STATUS_FAILED, result.status)
        self.assertEqual(ORCH_NODE_MISSING, result.reason_code)
        self.assertEqual(S_ORCH_RUN, result.stage)
        self.assertTrue(
            any(
                row["request_id"] == fixture["request_id"] and row["reason_code"] == ORCH_NODE_MISSING
                for row in self.ledger.error_log
            ),
            msg=self.ledger.error_log,
        )

    def test_t_orch_01_invalid_edge_is_rejected(self) -> None:
        fixture = _load_json(FIXTURES / "bad" / "graph_invalid_edge.json")
        result = self.runtime.run_graph(
            request_id=fixture["request_id"],
            graph_config=fixture["graph"],
            payload={"input": "hello"},
            ts_ms=1_800_120_000_002,
        )

        self.assertEqual(STATUS_FAILED, result.status)
        self.assertEqual(ORCH_EDGE_INVALID, result.reason_code)
        self.assertEqual(S_ORCH_VALIDATE, result.stage)

    def test_t_orch_01_timeout_fallback_is_degraded_with_stable_reason(self) -> None:
        fixture = _load_json(FIXTURES / "bad" / "graph_timeout_with_fallback.json")
        result = self.runtime.run_graph(
            request_id=fixture["request_id"],
            graph_config=fixture["graph"],
            payload={"input": "hello"},
            ts_ms=1_800_120_000_003,
        )

        self.assertEqual(STATUS_DEGRADED, result.status)
        self.assertEqual(ORCH_TIMEOUT, result.reason_code)
        self.assertEqual(S_ORCH_RUN, result.stage)
        trace = result.output["graph_trace"]
        self.assertEqual("TIMEOUT", trace[1]["status"])
        self.assertEqual("respond", trace[-1]["node_id"])
        self.assertEqual("OK", trace[-1]["status"])

    def test_t_orch_01_dependency_down_fallback_is_degraded(self) -> None:
        fixture = _load_json(FIXTURES / "bad" / "graph_dependency_down_with_fallback.json")
        result = self.runtime.run_graph(
            request_id=fixture["request_id"],
            graph_config=fixture["graph"],
            payload={"input": "hello"},
            dependency_statuses=fixture["dependency_statuses"],
            ts_ms=1_800_120_000_004,
        )

        self.assertEqual(STATUS_DEGRADED, result.status)
        self.assertEqual(ORCH_DEPENDENCY_UNAVAILABLE, result.reason_code)
        self.assertEqual(S_ORCH_RUN, result.stage)
        trace = result.output["graph_trace"]
        self.assertEqual("SKIPPED", trace[1]["status"])
        self.assertEqual("respond", trace[-1]["node_id"])
        self.assertEqual("OK", trace[-1]["status"])


if __name__ == "__main__":
    unittest.main()
