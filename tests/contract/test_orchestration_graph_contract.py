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
        self.assertEqual("langgraph+langchain", result.output["runtime_backend"])
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


    def test_t_orch_01_model_node_is_driven_by_graph_config(self) -> None:
        graph = {
            "graph_id": "graph.model.v1",
            "version": "v1",
            "entry_node": "respond",
            "model_profile_id": "chat_ja_24x7_v1",
            "model_nodes": {
                "renderer_ja_c_style": {
                    "provider": "mock",
                    "model_id": "gemini-2.5-flash",
                    "temperature": 0.55,
                    "max_output_tokens": 360,
                    "provider_fn": lambda _payload: json.dumps({"narrative": {"text": "ok"}, "intent_draft": {}, "meta": {"draft_only": True}}, ensure_ascii=False),
                }
            },
            "node_bindings": [
                {
                    "node_id": "respond",
                    "model_node_id": "renderer_ja_c_style",
                    "required": True,
                }
            ],
            "edges": [],
            "fallback_edges": [],
        }
        result = self.runtime.run_graph(
            request_id="req_orch_model_001",
            graph_config=graph,
            payload={"input": "hello"},
            ts_ms=1_800_120_000_010,
        )

        self.assertEqual(STATUS_OK, result.status)
        self.assertEqual("langgraph+langchain", result.output["runtime_backend"])
        self.assertEqual("chat_ja_24x7_v1", result.output["model_profile_id"])
        self.assertEqual(1, len(result.output["model_control"]))
        model_call = result.output["model_control"][0]
        self.assertEqual("mock", model_call["provider"])
        self.assertEqual("gemini-2.5-flash", model_call["model_id"])
        self.assertEqual(0.55, model_call["temperature"])
        self.assertEqual(360, model_call["max_output_tokens"])

    def test_t_orch_02_model_plane_profile_loading_without_inline_model_nodes(self) -> None:
        graph = {
            "graph_id": "graph.model_plane.v1",
            "version": "v1",
            "entry_node": "respond",
            "model_profile_id": "chat_ja_24x7_v1",
            "node_bindings": [
                {
                    "node_id": "respond",
                    "model_node_id": "renderer_ja_c_style",
                    "task": "CHAT",
                    "required": True,
                }
            ],
            "edges": [],
            "fallback_edges": [],
        }
        result = self.runtime.run_graph(
            request_id="req_orch_model_002",
            graph_config=graph,
            payload={"input": "hello"},
            dependency_statuses={"gemini": {"api_key": "demo-key", "available": True}},
            ts_ms=1_800_120_000_011,
        )

        self.assertEqual(STATUS_OK, result.status)
        self.assertEqual("chat_ja_24x7_v1", result.output["model_profile_id"])
        self.assertEqual(1, len(result.output["model_control"]))
        call = result.output["model_control"][0]
        self.assertEqual("gemini", call["provider"])
        self.assertEqual("gemini-2.5-flash", call["model_id"])
        self.assertTrue(call["param_hash"])
        self.assertEqual(9000, call["timeout_ms"])

        ledger_rows = self.ledger.model_control_log
        self.assertEqual(1, len(ledger_rows))
        self.assertEqual("chat_ja_24x7_v1", ledger_rows[0]["model_profile_id"])
        self.assertEqual(call["actual_param_hash"], ledger_rows[0]["actual_param_hash"])

    def test_t_orch_02_missing_model_profile_rejected_at_validate_stage(self) -> None:
        graph = {
            "graph_id": "graph.model_plane.v2",
            "version": "v1",
            "entry_node": "respond",
            "model_profile_id": "missing_profile_v999",
            "node_bindings": [
                {
                    "node_id": "respond",
                    "model_node_id": "renderer_ja_c_style",
                    "task": "CHAT",
                    "required": True,
                }
            ],
            "edges": [],
            "fallback_edges": [],
        }
        result = self.runtime.run_graph(
            request_id="req_orch_model_003",
            graph_config=graph,
            payload={"input": "hello"},
            ts_ms=1_800_120_000_012,
        )

        self.assertEqual(STATUS_FAILED, result.status)
        self.assertEqual(ORCH_NODE_MISSING, result.reason_code)
        self.assertEqual(S_ORCH_VALIDATE, result.stage)

    def test_t_orch_03_param_drift_is_degraded_with_stable_reason(self) -> None:
        graph = {
            "graph_id": "graph.model_drift.v1",
            "version": "v1",
            "entry_node": "respond",
            "model_profile_id": "chat_ja_24x7_v1",
            "model_nodes": {
                "renderer_ja_c_style": {
                    "provider": "mock",
                    "model_id": "gemini-2.5-flash",
                    "temperature": 0.55,
                    "max_output_tokens": 360,
                    "timeout_ms": 1200,
                    "expected_param_hash": "mismatch-hash",
                    "provider_fn": lambda _payload: json.dumps({"narrative": {"text": "ok"}, "intent_draft": {}, "meta": {"draft_only": True}}, ensure_ascii=False),
                }
            },
            "node_bindings": [
                {
                    "node_id": "respond",
                    "model_node_id": "renderer_ja_c_style",
                    "task": "CHAT",
                    "required": True,
                }
            ],
            "edges": [],
            "fallback_edges": [],
        }
        result = self.runtime.run_graph(
            request_id="req_orch_model_004",
            graph_config=graph,
            payload={"input": "hello"},
            ts_ms=1_800_120_000_013,
        )

        self.assertEqual(STATUS_DEGRADED, result.status)
        self.assertEqual(ORCH_PARAM_DRIFT, result.reason_code)
        model_call = result.output["model_control"][0]
        self.assertTrue(model_call["drift_detected"])
        self.assertEqual("mismatch-hash", model_call["expected_param_hash"])
        self.assertNotEqual("mismatch-hash", model_call["actual_param_hash"])

    def test_t_orch_04_profile_selector_is_deterministic_with_allowlist(self) -> None:
        graph = {
            "graph_id": "graph.selector.v1",
            "version": "v1",
            "entry_node": "respond",
            "env": "prod",
            "node_allowlist": ["renderer_ja_c_style"],
            "model_profile_id": "chat_ja_24x7_v1",
            "node_bindings": [
                {
                    "node_id": "respond",
                    "task": "CHAT",
                    "required": True,
                }
            ],
            "edges": [],
            "fallback_edges": [],
        }
        result = self.runtime.run_graph(
            request_id="req_orch_selector_001",
            graph_config=graph,
            payload={"input": "hello"},
            dependency_statuses={"gemini": {"api_key": "demo-key", "available": True}},
            ts_ms=1_800_120_000_014,
        )

        self.assertIn(result.status, {STATUS_OK, STATUS_DEGRADED})
        call = result.output["model_control"][0]
        self.assertEqual("renderer_ja_c_style", call["selected_node"])
        self.assertIn("renderer_ja_c_style", call["candidate_nodes"])
        self.assertEqual("prod", call["selector_env"])

    def test_t_orch_04_no_candidate_node_is_rejected(self) -> None:
        graph = {
            "graph_id": "graph.selector.v2",
            "version": "v1",
            "entry_node": "respond",
            "env": "prod",
            "node_allowlist": ["not-exist-node"],
            "model_profile_id": "chat_ja_24x7_v1",
            "node_bindings": [
                {
                    "node_id": "respond",
                    "task": "CHAT",
                    "required": True,
                }
            ],
            "edges": [],
            "fallback_edges": [],
        }
        result = self.runtime.run_graph(
            request_id="req_orch_selector_002",
            graph_config=graph,
            payload={"input": "hello"},
            ts_ms=1_800_120_000_015,
        )

        self.assertEqual(STATUS_FAILED, result.status)
        self.assertEqual(ORCH_NODE_MISSING, result.reason_code)
        self.assertEqual(S_ORCH_VALIDATE, result.stage)

    def test_t_orch_05_model_node_requires_approval_token(self) -> None:
        graph = {
            "graph_id": "graph.token.v1",
            "version": "v1",
            "entry_node": "respond",
            "model_profile_id": "chat_ja_24x7_v1",
            "node_bindings": [
                {
                    "node_id": "respond",
                    "task": "CHAT",
                    "require_approval_token": True,
                    "required": True,
                }
            ],
            "edges": [],
            "fallback_edges": [],
        }
        result = self.runtime.run_graph(
            request_id="req_orch_token_001",
            graph_config=graph,
            payload={"input": "hello", "intent_id": "intent_001"},
            ts_ms=1_800_120_000_016,
        )

        self.assertEqual(STATUS_DEGRADED, result.status)
        self.assertEqual(SKILL_TOKEN_MISSING, result.reason_code)
        call = result.output["model_control"][0]
        self.assertEqual(SKILL_TOKEN_MISSING, call["reason_code"])
        self.assertEqual("intent_001", call["governance_refs"]["intent_id"])
        self.assertIsNone(call["governance_refs"]["approval_token_ref"])
        self.assertEqual(SKILL_TOKEN_MISSING, self.ledger.model_control_log[0]["reason_code"])

    def test_t_orch_05_model_node_carries_governance_refs(self) -> None:
        graph = {
            "graph_id": "graph.token.v2",
            "version": "v1",
            "entry_node": "respond",
            "model_profile_id": "chat_ja_24x7_v1",
            "node_bindings": [
                {
                    "node_id": "respond",
                    "task": "CHAT",
                    "required": True,
                }
            ],
            "edges": [],
            "fallback_edges": [],
        }
        result = self.runtime.run_graph(
            request_id="req_orch_token_002",
            graph_config=graph,
            payload={
                "input": "hello",
                "intent_id": "intent_002",
                "approval_token_ref": "approve::abc",
                "evidence_ref": "evidence::xyz",
            },
            dependency_statuses={"gemini": {"api_key": "demo-key", "available": True}},
            ts_ms=1_800_120_000_017,
        )

        self.assertIn(result.status, {STATUS_OK, STATUS_DEGRADED})
        call = result.output["model_control"][0]
        self.assertEqual("intent_002", call["governance_refs"]["intent_id"])
        self.assertEqual("approve::abc", call["governance_refs"]["approval_token_ref"])
        self.assertEqual("evidence::xyz", call["governance_refs"]["evidence_ref"])


if __name__ == "__main__":
    unittest.main()
