import json
import unittest
from pathlib import Path

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    RETR_EMBED_INPUT_INVALID,
    RETR_EMBED_TIMEOUT,
    RETR_EMBED_UNAVAILABLE,
)
from host_brain.retrieval.embedding import (
    RET_OP_EMBEDDING,
    S_RET_EMBEDDING,
    STATUS_DEGRADED,
    STATUS_OK,
    EmbeddingServiceContract,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "retrieval"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


class TestRetrievalEmbeddingContract(unittest.TestCase):
    def setUp(self) -> None:
        self.now_ms = 1_800_000_800_000
        self.ledger = SQLiteLedger(db_path=":memory:")

    def test_t_ret_01_embedding_success_output_is_stable_and_auditable(self) -> None:
        fixture = _load_json(FIXTURES / "good" / "embedding_request.json")
        svc = EmbeddingServiceContract(
            ledger=self.ledger,
            available=True,
            timeout_ms=fixture["payload"]["timeout_ms"],
        )

        result = svc.embed(
            request_id=fixture["request_id"],
            text=fixture["payload"]["text"],
            ts_ms=self.now_ms,
        )

        self.assertTrue(result.success)
        self.assertEqual(STATUS_OK, result.status)
        self.assertEqual(S_RET_EMBEDDING, result.stage)
        self.assertIsNone(result.reason_code)
        self.assertIsInstance(result.output["embedding_vector"], list)
        self.assertGreater(len(result.output["embedding_vector"]), 0)
        self.assertEqual(STATUS_OK, result.output["status"])
        self.assertFalse(result.output["degraded"])
        json.loads(json.dumps(result.output, ensure_ascii=False))

        self.assertEqual(1, len(self.ledger.retrieval_event_log))
        row = self.ledger.retrieval_event_log[0]
        self.assertEqual(fixture["request_id"], row["request_id"])
        self.assertEqual(RET_OP_EMBEDDING, row["op_kind"])
        self.assertEqual(STATUS_OK, row["status"])
        self.assertEqual(S_RET_EMBEDDING, row["stage"])
        self.assertIsInstance(row["payload"], dict)

    def test_t_ret_01_unavailable_and_timeout_have_stable_reason_code(self) -> None:
        unavailable = _load_json(FIXTURES / "bad" / "embedding_unavailable_request.json")
        timeout_req = _load_json(FIXTURES / "bad" / "embedding_timeout_request.json")
        empty_req = _load_json(FIXTURES / "bad" / "embedding_empty_text.json")

        unavailable_svc = EmbeddingServiceContract(
            ledger=self.ledger,
            available=False,
            timeout_ms=unavailable["payload"]["timeout_ms"],
        )
        res_a = unavailable_svc.embed(
            request_id=unavailable["request_id"],
            text=unavailable["payload"]["text"],
            ts_ms=self.now_ms + 1,
        )
        res_b = unavailable_svc.embed(
            request_id="req_ret_002_repeat",
            text=unavailable["payload"]["text"],
            ts_ms=self.now_ms + 2,
        )
        self.assertFalse(res_a.success)
        self.assertFalse(res_b.success)
        self.assertEqual(STATUS_DEGRADED, res_a.status)
        self.assertEqual(RETR_EMBED_UNAVAILABLE, res_a.reason_code)
        self.assertEqual(res_a.reason_code, res_b.reason_code)
        self.assertIn("unavailable", res_a.detail.lower())
        self.assertNotIn("memory empty", res_a.detail.lower())

        def _timeout_provider(_text: str) -> tuple[list[float], str]:
            raise TimeoutError("mock timeout")

        timeout_svc = EmbeddingServiceContract(
            ledger=self.ledger,
            provider=_timeout_provider,
            available=True,
            timeout_ms=timeout_req["payload"]["timeout_ms"],
        )
        res_timeout = timeout_svc.embed(
            request_id=timeout_req["request_id"],
            text=timeout_req["payload"]["text"],
            ts_ms=self.now_ms + 3,
        )
        self.assertFalse(res_timeout.success)
        self.assertEqual(RETR_EMBED_TIMEOUT, res_timeout.reason_code)
        self.assertEqual(S_RET_EMBEDDING, res_timeout.stage)
        self.assertEqual(STATUS_DEGRADED, res_timeout.output["status"])
        self.assertTrue(res_timeout.output["degraded"])

        res_empty = timeout_svc.embed(
            request_id=empty_req["request_id"],
            text=empty_req["payload"]["text"],
            ts_ms=self.now_ms + 4,
        )
        self.assertFalse(res_empty.success)
        self.assertEqual(RETR_EMBED_INPUT_INVALID, res_empty.reason_code)

        reasons = [row["reason_code"] for row in self.ledger.retrieval_event_log]
        self.assertIn(RETR_EMBED_UNAVAILABLE, reasons)
        self.assertIn(RETR_EMBED_TIMEOUT, reasons)
        self.assertIn(RETR_EMBED_INPUT_INVALID, reasons)


if __name__ == "__main__":
    unittest.main()
