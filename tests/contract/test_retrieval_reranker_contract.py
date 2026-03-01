import json
import unittest
from pathlib import Path

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    RETR_RERANK_INPUT_INVALID,
    RETR_RERANK_TIMEOUT,
    RETR_RERANK_UNAVAILABLE,
)
from host_brain.retrieval.reranker import (
    RET_OP_RERANK,
    S_RET_RERANK,
    STATUS_DEGRADED,
    STATUS_OK,
    RerankerServiceContract,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "retrieval"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TestRetrievalRerankerContract(unittest.TestCase):
    def setUp(self) -> None:
        self.now_ms = 1_800_000_900_000
        self.ledger = SQLiteLedger(db_path=":memory:")

    def test_t_ret_02_rerank_success_output_is_stable_and_auditable(self) -> None:
        fixture = _load_json(FIXTURES / "good" / "rerank_request.json")
        svc = RerankerServiceContract(
            ledger=self.ledger,
            available=True,
            timeout_ms=fixture["payload"]["timeout_ms"],
        )

        result = svc.rerank(
            request_id=fixture["request_id"],
            query=fixture["payload"]["query"],
            candidates=fixture["payload"]["candidates"],
            top_k=fixture["payload"]["top_k"],
            ts_ms=self.now_ms,
        )

        self.assertTrue(result.success)
        self.assertEqual(STATUS_OK, result.status)
        self.assertEqual(S_RET_RERANK, result.stage)
        self.assertIsNone(result.reason_code)
        self.assertEqual(STATUS_OK, result.output["status"])
        self.assertFalse(result.output["degraded"])
        self.assertIsInstance(result.output["scores"], list)
        self.assertEqual(len(fixture["payload"]["candidates"]), len(result.output["scores"]))
        self._assert_topk_shape(result.output["topk"], fixture["payload"]["top_k"])
        json.loads(json.dumps(result.output, ensure_ascii=False))

        self.assertEqual(1, len(self.ledger.retrieval_event_log))
        row = self.ledger.retrieval_event_log[0]
        self.assertEqual(fixture["request_id"], row["request_id"])
        self.assertEqual(RET_OP_RERANK, row["op_kind"])
        self.assertEqual(STATUS_OK, row["status"])
        self.assertEqual(S_RET_RERANK, row["stage"])
        self.assertIsInstance(row["payload"], dict)
        self.assertEqual(fixture["payload"]["top_k"], row["payload"]["topk_count"])

    def test_t_ret_02_unavailable_timeout_and_invalid_keep_topk_shape(self) -> None:
        unavailable = _load_json(FIXTURES / "bad" / "rerank_unavailable_request.json")
        timeout_req = _load_json(FIXTURES / "bad" / "rerank_timeout_request.json")
        invalid_req = _load_json(FIXTURES / "bad" / "rerank_invalid_input.json")

        unavailable_svc = RerankerServiceContract(
            ledger=self.ledger,
            available=False,
            timeout_ms=unavailable["payload"]["timeout_ms"],
        )
        res_a = unavailable_svc.rerank(
            request_id=unavailable["request_id"],
            query=unavailable["payload"]["query"],
            candidates=unavailable["payload"]["candidates"],
            top_k=unavailable["payload"]["top_k"],
            ts_ms=self.now_ms + 1,
        )
        res_b = unavailable_svc.rerank(
            request_id="req_rerank_002_repeat",
            query=unavailable["payload"]["query"],
            candidates=unavailable["payload"]["candidates"],
            top_k=unavailable["payload"]["top_k"],
            ts_ms=self.now_ms + 2,
        )
        self.assertFalse(res_a.success)
        self.assertFalse(res_b.success)
        self.assertEqual(STATUS_DEGRADED, res_a.status)
        self.assertEqual(RETR_RERANK_UNAVAILABLE, res_a.reason_code)
        self.assertEqual(res_a.reason_code, res_b.reason_code)
        self.assertIn("unavailable", res_a.detail.lower())
        self._assert_topk_shape(res_a.output["topk"], unavailable["payload"]["top_k"])

        def _timeout_provider(_query: str, _candidates: list[dict]) -> tuple[list[float], str]:
            raise TimeoutError("mock timeout")

        timeout_svc = RerankerServiceContract(
            ledger=self.ledger,
            provider=_timeout_provider,
            available=True,
            timeout_ms=timeout_req["payload"]["timeout_ms"],
        )
        res_timeout = timeout_svc.rerank(
            request_id=timeout_req["request_id"],
            query=timeout_req["payload"]["query"],
            candidates=timeout_req["payload"]["candidates"],
            top_k=timeout_req["payload"]["top_k"],
            ts_ms=self.now_ms + 3,
        )
        self.assertFalse(res_timeout.success)
        self.assertEqual(RETR_RERANK_TIMEOUT, res_timeout.reason_code)
        self.assertEqual(S_RET_RERANK, res_timeout.stage)
        self.assertEqual(STATUS_DEGRADED, res_timeout.output["status"])
        self.assertTrue(res_timeout.output["degraded"])
        self._assert_topk_shape(res_timeout.output["topk"], timeout_req["payload"]["top_k"])

        res_invalid = timeout_svc.rerank(
            request_id=invalid_req["request_id"],
            query=invalid_req["payload"]["query"],
            candidates=invalid_req["payload"]["candidates"],
            top_k=invalid_req["payload"]["top_k"],
            ts_ms=self.now_ms + 4,
        )
        self.assertFalse(res_invalid.success)
        self.assertEqual(RETR_RERANK_INPUT_INVALID, res_invalid.reason_code)
        self._assert_topk_shape(res_invalid.output["topk"], 0)

        reasons = [row["reason_code"] for row in self.ledger.retrieval_event_log]
        self.assertIn(RETR_RERANK_UNAVAILABLE, reasons)
        self.assertIn(RETR_RERANK_TIMEOUT, reasons)
        self.assertIn(RETR_RERANK_INPUT_INVALID, reasons)

    def _assert_topk_shape(self, topk: list[dict], expected_count: int) -> None:
        self.assertIsInstance(topk, list)
        self.assertEqual(expected_count, len(topk))
        for row in topk:
            self.assertIsInstance(row, dict)
            self.assertIn("doc_id", row)
            self.assertIn("source_ref", row)
            self.assertIn("snippet", row)
            self.assertIn("score", row)
            self.assertIn("confidence", row)
            self.assertIn("metadata", row)
            self.assertIsInstance(row["metadata"], dict)


if __name__ == "__main__":
    unittest.main()
