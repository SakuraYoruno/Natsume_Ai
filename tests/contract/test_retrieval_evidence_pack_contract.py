import json
import unittest
from pathlib import Path

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import RETR_EPACK_INPUT_INVALID, RETR_RERANK_UNAVAILABLE
from host_brain.retrieval.evidence_pack import (
    RET_OP_EVIDENCE_PACK,
    S_RET_EVIDENCE_PACK,
    SERIALIZATION_VERSION,
    STATUS_DEGRADED,
    STATUS_OK,
    EvidencePackServiceContract,
)
from host_brain.retrieval.reranker import RerankerServiceContract


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "retrieval"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TestRetrievalEvidencePackContract(unittest.TestCase):
    def setUp(self) -> None:
        self.now_ms = 1_800_001_000_000
        self.ledger = SQLiteLedger(db_path=":memory:")

    def test_t_ret_03_pack_fields_are_stable_and_replayable(self) -> None:
        fixture = _load_json(FIXTURES / "good" / "evidence_pack_request.json")
        svc = EvidencePackServiceContract(ledger=self.ledger)

        result = svc.build_pack(
            request_id=fixture["request_id"],
            intent_id=fixture["intent_id"],
            query=fixture["payload"]["query"],
            topk=fixture["payload"]["topk"],
            ts_ms=self.now_ms,
        )

        self.assertTrue(result.success)
        self.assertEqual(STATUS_OK, result.status)
        self.assertEqual(S_RET_EVIDENCE_PACK, result.stage)
        self.assertIsNone(result.reason_code)
        self.assertEqual(STATUS_OK, result.output["status"])
        self.assertFalse(result.output["degraded"])
        self.assertEqual(SERIALIZATION_VERSION, result.output["serialization_version"])
        self.assertEqual(fixture["request_id"], result.output["request_id"])
        self.assertEqual(fixture["intent_id"], result.output["intent_id"])
        self.assertEqual(2, len(result.output["topk"]))
        self.assertEqual(2, len(result.output["evidence_refs"]))
        self._assert_topk_shape(result.output["topk"])
        self.assertTrue(str(result.output["pack_id"]).startswith("epk_"))

        serialized = svc.serialize_pack(result.output)
        reparsed = json.loads(serialized)
        self.assertEqual(result.output["pack_id"], reparsed["pack_id"])

        repeated = svc.build_pack(
            request_id=fixture["request_id"],
            intent_id=fixture["intent_id"],
            query=fixture["payload"]["query"],
            topk=fixture["payload"]["topk"],
            ts_ms=self.now_ms + 1,
        )
        self.assertEqual(result.output["pack_id"], repeated.output["pack_id"])

        rows = [row for row in self.ledger.retrieval_event_log if row["op_kind"] == RET_OP_EVIDENCE_PACK]
        self.assertGreaterEqual(len(rows), 2)
        first = rows[0]
        self.assertEqual(STATUS_OK, first["status"])
        self.assertEqual(S_RET_EVIDENCE_PACK, first["stage"])
        self.assertIsInstance(first["payload"], dict)
        self.assertEqual(2, first["payload"]["evidence_refs_count"])

    def test_t_ret_03_degraded_rerank_and_invalid_input_are_stable(self) -> None:
        rerank_fixture = _load_json(FIXTURES / "bad" / "rerank_unavailable_request.json")
        invalid_fixture = _load_json(FIXTURES / "bad" / "evidence_pack_invalid_input.json")

        rerank_svc = RerankerServiceContract(ledger=self.ledger, available=False)
        rerank_result = rerank_svc.rerank(
            request_id=rerank_fixture["request_id"],
            query=rerank_fixture["payload"]["query"],
            candidates=rerank_fixture["payload"]["candidates"],
            top_k=rerank_fixture["payload"]["top_k"],
            ts_ms=self.now_ms + 2,
        )
        self.assertEqual(RETR_RERANK_UNAVAILABLE, rerank_result.reason_code)

        pack_svc = EvidencePackServiceContract(ledger=self.ledger)
        degraded_pack = pack_svc.build_pack(
            request_id=rerank_fixture["request_id"],
            query=rerank_fixture["payload"]["query"],
            rerank_output=rerank_result.output,
            ts_ms=self.now_ms + 3,
        )
        self.assertFalse(degraded_pack.success)
        self.assertEqual(STATUS_DEGRADED, degraded_pack.status)
        self.assertEqual(RETR_RERANK_UNAVAILABLE, degraded_pack.reason_code)
        self.assertTrue(degraded_pack.output["degraded"])
        self.assertEqual(RETR_RERANK_UNAVAILABLE, degraded_pack.output["reason_code"])
        self._assert_topk_shape(degraded_pack.output["topk"])

        invalid_pack = pack_svc.build_pack(
            request_id=invalid_fixture["request_id"],
            intent_id=invalid_fixture["intent_id"],
            query=invalid_fixture["payload"]["query"],
            topk=invalid_fixture["payload"]["topk"],
            ts_ms=self.now_ms + 4,
        )
        self.assertFalse(invalid_pack.success)
        self.assertEqual(RETR_EPACK_INPUT_INVALID, invalid_pack.reason_code)
        self.assertEqual(S_RET_EVIDENCE_PACK, invalid_pack.stage)
        self.assertEqual([], invalid_pack.output["topk"])
        self.assertEqual([], invalid_pack.output["evidence_refs"])

        reasons = [row["reason_code"] for row in self.ledger.retrieval_event_log if row["op_kind"] == RET_OP_EVIDENCE_PACK]
        self.assertIn(RETR_RERANK_UNAVAILABLE, reasons)
        self.assertIn(RETR_EPACK_INPUT_INVALID, reasons)

    def _assert_topk_shape(self, topk: list[dict]) -> None:
        self.assertIsInstance(topk, list)
        self.assertGreater(len(topk), 0)
        for row in topk:
            self.assertIsInstance(row, dict)
            self.assertIn("doc_id", row)
            self.assertIn("source_ref", row)
            self.assertIn("snippet", row)
            self.assertIn("score", row)
            self.assertIn("confidence", row)
            self.assertIn("metadata", row)
            self.assertIn("evidence_ref", row)
            self.assertIsInstance(row["metadata"], dict)


if __name__ == "__main__":
    unittest.main()
