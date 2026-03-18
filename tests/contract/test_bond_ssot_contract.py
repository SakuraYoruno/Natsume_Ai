import ast
import json
import unittest
from pathlib import Path

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.bond.bond_dynamics import (
    S_BOND_UPDATE,
    STATUS_OK,
    STATUS_REJECTED,
    BondDynamicsWriter,
)
from host_brain.bond.neo4j_client import (
    REL_ABOUT,
    REL_AFFECTS,
    REL_HAS_EVENT,
    InMemoryNeo4jBondClient,
)
from host_brain.core.errors import BOND_EVIDENCE_REQUIRED, BOND_SINGLE_WRITER_VIOLATION


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "bond"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


class TestBondSsotContract(unittest.TestCase):
    def setUp(self) -> None:
        self.ledger = SQLiteLedger(db_path=":memory:")
        self.client = InMemoryNeo4jBondClient()
        self.writer = BondDynamicsWriter(ledger=self.ledger, neo4j_client=self.client)
        self.now_ms = 1_800_010_000_000

    def test_t_50_single_writer_update_is_logged_and_replayable(self) -> None:
        fixture = _load_json(FIXTURES / "good" / "bond_update_request.json")
        result = self.writer.apply_update(
            request_id=fixture["request_id"],
            intent_id=fixture["intent_id"],
            session_id=fixture["session_id"],
            seq=fixture["seq"],
            user_id=fixture["user_id"],
            topic_id=fixture["topic_id"],
            capability_hint=fixture["capability_hint"],
            evidence_refs=fixture["evidence_refs"],
            payload=fixture["payload"],
            update_kind=fixture["update_kind"],
            ts_ms=self.now_ms,
        )

        self.assertTrue(result.success)
        self.assertEqual(STATUS_OK, result.status)
        self.assertEqual(S_BOND_UPDATE, result.stage)
        self.assertIsNone(result.reason_code)
        event = result.event
        self.assertEqual(STATUS_OK, event["status"])
        self.assertEqual(fixture["user_id"], event["user_id"])
        self.assertEqual(fixture["evidence_refs"], event["evidence_refs"])
        self.assertTrue(str(event["event_id"]).startswith("bond_evt_"))

        self.assertEqual(1, len(self.ledger.bond_update_log))
        row = self.ledger.bond_update_log[0]
        self.assertEqual(fixture["request_id"], row["request_id"])
        self.assertEqual(STATUS_OK, row["status"])
        self.assertIsNone(row["reason_code"])
        self.assertEqual(S_BOND_UPDATE, row["stage"])
        self.assertEqual(fixture["evidence_refs"], row["evidence_refs"])

        graph_event = self.client.get_event(event["event_id"])
        self.assertIsNotNone(graph_event)
        assert graph_event is not None
        self.assertEqual(event["event_id"], graph_event["event_id"])
        self.assertEqual(fixture["evidence_refs"], graph_event["evidence_refs"])

    def test_t_51_missing_evidence_is_rejected_and_logged(self) -> None:
        fixture = _load_json(FIXTURES / "bad" / "bond_update_missing_evidence.json")
        result = self.writer.apply_update(
            request_id=fixture["request_id"],
            intent_id=fixture["intent_id"],
            session_id=fixture["session_id"],
            seq=fixture["seq"],
            user_id=fixture["user_id"],
            topic_id=fixture["topic_id"],
            capability_hint=fixture["capability_hint"],
            evidence_refs=fixture["evidence_refs"],
            payload=fixture["payload"],
            update_kind=fixture["update_kind"],
            ts_ms=self.now_ms + 1,
        )

        self.assertFalse(result.success)
        self.assertEqual(STATUS_REJECTED, result.status)
        self.assertEqual(BOND_EVIDENCE_REQUIRED, result.reason_code)
        self.assertEqual(STATUS_REJECTED, result.event["status"])
        self.assertEqual(BOND_EVIDENCE_REQUIRED, result.event["reason_code"])
        self.assertEqual([], self.client.events)

        self.assertEqual(1, len(self.ledger.bond_update_log))
        row = self.ledger.bond_update_log[0]
        self.assertEqual(STATUS_REJECTED, row["status"])
        self.assertEqual(BOND_EVIDENCE_REQUIRED, row["reason_code"])
        self.assertEqual([], row["evidence_refs"])

    def test_t_52_runtime_single_writer_boundary_blocks_external_writer(self) -> None:
        with self.assertRaises(PermissionError) as ctx:
            self.client.write_bond_event(
                writer_id="host_brain.external.module",
                event_id="bond_evt_forbidden",
                user_id="user_bond_forbidden",
                session_id="session_bond_forbidden",
                topic_id="topic_bond_forbidden",
                capability_hint="SUPPORTIVE",
                evidence_refs=["evidence::bond::forbidden"],
                payload={"etype": "FORBIDDEN"},
                ts_ms=self.now_ms + 2,
            )
        self.assertEqual(BOND_SINGLE_WRITER_VIOLATION, str(ctx.exception))

    def test_t_53_trace_user_events_keeps_event_and_edge_mapping(self) -> None:
        fixture = _load_json(FIXTURES / "good" / "bond_update_request.json")
        result_a = self.writer.apply_update(
            request_id=fixture["request_id"],
            intent_id=fixture["intent_id"],
            session_id=fixture["session_id"],
            seq=fixture["seq"],
            user_id=fixture["user_id"],
            topic_id=fixture["topic_id"],
            capability_hint=fixture["capability_hint"],
            evidence_refs=fixture["evidence_refs"],
            payload=fixture["payload"],
            update_kind=fixture["update_kind"],
            event_id="bond_evt_trace_001",
            ts_ms=self.now_ms + 3,
        )
        result_b = self.writer.apply_update(
            request_id="req_bond_003",
            intent_id="intent_bond_003",
            session_id=fixture["session_id"],
            seq=3,
            user_id=fixture["user_id"],
            topic_id="topic_boundary",
            capability_hint="CAUTIOUS",
            evidence_refs=["evidence::bond::003"],
            payload={"etype": "BOUNDARY", "intensity": "MID"},
            update_kind="DERIVED",
            event_id="bond_evt_trace_002",
            ts_ms=self.now_ms + 4,
        )
        self.assertTrue(result_a.success)
        self.assertTrue(result_b.success)

        trace = self.client.trace_user_events(fixture["user_id"])
        self.assertEqual(2, len(trace))
        self.assertEqual("bond_evt_trace_001", trace[0]["event_id"])
        self.assertEqual("bond_evt_trace_002", trace[1]["event_id"])
        self.assertEqual(["evidence::bond::003"], trace[1]["evidence_refs"])

        rel_types = {edge["rel_type"] for edge in self.client.edges}
        self.assertIn(REL_HAS_EVENT, rel_types)
        self.assertIn(REL_ABOUT, rel_types)
        self.assertIn(REL_AFFECTS, rel_types)

    def test_t_54_constraints_loaded_and_summarizer_has_no_write_call(self) -> None:
        statements = self.client.constraint_statements
        self.assertGreaterEqual(len(statements), 5)
        self.assertTrue(any("CREATE CONSTRAINT" in stmt for stmt in statements))
        self.assertTrue(any("bond_event_event_id" in stmt for stmt in statements))
        self.assertTrue(any("CREATE INDEX" in stmt for stmt in statements))

        summarizer_path = ROOT / "host_brain" / "bond" / "summarizer.py"
        tree = ast.parse(summarizer_path.read_text(encoding="utf-8-sig"), filename=str(summarizer_path))
        writes = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute) and node.func.attr == "write_bond_event":
                    writes.append(node.func.attr)
                if isinstance(node.func, ast.Name) and node.func.id == "write_bond_event":
                    writes.append(node.func.id)
        self.assertEqual([], writes)


if __name__ == "__main__":
    unittest.main()
