import json
import unittest
from pathlib import Path

from edge_client.rpc.fallback_llm import (
    EVENT_FALLBACK_ASR_COMPILE,
    EVENT_FALLBACK_CONTEXT_COMPRESS,
    EVENT_FALLBACK_PRESENCE,
    EVENT_FALLBACK_RETRIEVAL_HINTS,
    S_VOICE_EDGE_FALLBACK_ASR_COMPILE,
    S_VOICE_EDGE_FALLBACK_CONTEXT_COMPRESS,
    S_VOICE_EDGE_FALLBACK_PRESENCE,
    S_VOICE_EDGE_FALLBACK_RETRIEVAL_HINTS,
    STATUS_ACCEPTED,
    STATUS_REJECTED,
    EdgeFallbackRpcContract,
)
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    VOICE_DECISION_FIELD_FORBIDDEN,
    VOICE_DRAFT_ONLY_REQUIRED,
    VOICE_EXECUTION_CLAIM_FORBIDDEN,
    VOICE_FALLBACK_UNAVAILABLE,
    VOICE_SCHEMA_INVALID,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "voice"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TestVoiceFallbackContract(unittest.TestCase):
    def setUp(self) -> None:
        self.now_ms = 1_800_000_600_000
        self.ledger = SQLiteLedger(db_path=":memory:")

    def test_t_voice_04_unavailable_is_fast_fail_and_countable(self) -> None:
        presence_input = _load_json(FIXTURES / "good" / "fallback_presence_valid.json")
        unavailable = _load_json(FIXTURES / "bad" / "fallback_unavailable_request.json")

        rpc = EdgeFallbackRpcContract(
            ledger=self.ledger,
            available=False,
            default_unloaded=True,
        )

        presence = rpc.presence(
            request_id=presence_input["request_id"],
            payload=presence_input["payload"],
            ts_ms=self.now_ms,
        )
        self.assertTrue(presence.success)
        self.assertEqual(STATUS_ACCEPTED, presence.status)
        self.assertEqual(S_VOICE_EDGE_FALLBACK_PRESENCE, presence.stage)
        self.assertEqual(EVENT_FALLBACK_PRESENCE, presence.event["event_type"])
        self.assertEqual(VOICE_FALLBACK_UNAVAILABLE, presence.reason_code)
        self.assertTrue(presence.event["draft_only"])

        fail = rpc.asr_compile(
            request_id=unavailable["request_id"],
            payload=unavailable["payload"],
            ts_ms=self.now_ms + 1,
        )
        self.assertFalse(fail.success)
        self.assertEqual(STATUS_REJECTED, fail.status)
        self.assertEqual(VOICE_FALLBACK_UNAVAILABLE, fail.reason_code)
        self.assertEqual(S_VOICE_EDGE_FALLBACK_ASR_COMPILE, fail.stage)

        self.assertTrue(
            any(
                row["event_type"] == EVENT_FALLBACK_ASR_COMPILE
                and row["reason_code"] == VOICE_FALLBACK_UNAVAILABLE
                for row in self.ledger.voice_event_log
            ),
            msg=self.ledger.voice_event_log,
        )

    def test_t_voice_04_draft_contract_forbids_decision_and_execution_claims(self) -> None:
        asr_input = _load_json(FIXTURES / "good" / "fallback_asr_compile_valid.json")
        retrieval_input = _load_json(FIXTURES / "good" / "fallback_retrieval_hints_valid.json")
        compress_input = _load_json(FIXTURES / "good" / "fallback_context_compress_valid.json")
        decision_bad = _load_json(FIXTURES / "bad" / "fallback_output_decision_field.json")
        claim_bad = _load_json(FIXTURES / "bad" / "fallback_output_false_execution_claim.json")
        draft_flag_bad = _load_json(FIXTURES / "bad" / "fallback_output_draft_only_false.json")

        def _bad_generator(endpoint: str, _payload: dict) -> dict:
            if endpoint == "asr_compile":
                return decision_bad
            if endpoint == "retrieval_hints":
                return claim_bad
            return draft_flag_bad

        rpc = EdgeFallbackRpcContract(
            ledger=self.ledger,
            available=True,
            generator=_bad_generator,
        )

        decision_res = rpc.asr_compile(
            request_id=asr_input["request_id"],
            payload=asr_input["payload"],
            ts_ms=self.now_ms + 10,
        )
        self.assertFalse(decision_res.success)
        self.assertEqual(VOICE_DECISION_FIELD_FORBIDDEN, decision_res.reason_code)
        self.assertEqual(S_VOICE_EDGE_FALLBACK_ASR_COMPILE, decision_res.stage)

        claim_res = rpc.retrieval_hints(
            request_id=retrieval_input["request_id"],
            payload=retrieval_input["payload"],
            ts_ms=self.now_ms + 11,
        )
        self.assertFalse(claim_res.success)
        self.assertEqual(VOICE_EXECUTION_CLAIM_FORBIDDEN, claim_res.reason_code)
        self.assertEqual(S_VOICE_EDGE_FALLBACK_RETRIEVAL_HINTS, claim_res.stage)

        draft_flag_res = rpc.context_compress(
            request_id=compress_input["request_id"],
            payload=compress_input["payload"],
            ts_ms=self.now_ms + 12,
        )
        self.assertFalse(draft_flag_res.success)
        self.assertEqual(VOICE_DRAFT_ONLY_REQUIRED, draft_flag_res.reason_code)
        self.assertEqual(S_VOICE_EDGE_FALLBACK_CONTEXT_COMPRESS, draft_flag_res.stage)

    def test_t_voice_04_valid_outputs_are_draft_only_and_replayable(self) -> None:
        asr_input = _load_json(FIXTURES / "good" / "fallback_asr_compile_valid.json")
        retrieval_input = _load_json(FIXTURES / "good" / "fallback_retrieval_hints_valid.json")
        compress_input = _load_json(FIXTURES / "good" / "fallback_context_compress_valid.json")
        missing = _load_json(FIXTURES / "bad" / "fallback_missing_field.json")

        rpc = EdgeFallbackRpcContract(
            ledger=self.ledger,
            available=True,
        )

        asr_ok = rpc.asr_compile(
            request_id=asr_input["request_id"],
            payload=asr_input["payload"],
            ts_ms=self.now_ms + 20,
        )
        self.assertTrue(asr_ok.success)
        self.assertEqual(STATUS_ACCEPTED, asr_ok.status)
        self.assertEqual(EVENT_FALLBACK_ASR_COMPILE, asr_ok.event["event_type"])
        self.assertTrue(asr_ok.event["draft_only"])

        retrieval_ok = rpc.retrieval_hints(
            request_id=retrieval_input["request_id"],
            payload=retrieval_input["payload"],
            ts_ms=self.now_ms + 21,
        )
        self.assertTrue(retrieval_ok.success)
        self.assertEqual(STATUS_ACCEPTED, retrieval_ok.status)
        self.assertEqual(EVENT_FALLBACK_RETRIEVAL_HINTS, retrieval_ok.event["event_type"])
        self.assertTrue(retrieval_ok.event["draft_only"])

        compress_ok = rpc.context_compress(
            request_id=compress_input["request_id"],
            payload=compress_input["payload"],
            ts_ms=self.now_ms + 22,
        )
        self.assertTrue(compress_ok.success)
        self.assertEqual(STATUS_ACCEPTED, compress_ok.status)
        self.assertEqual(EVENT_FALLBACK_CONTEXT_COMPRESS, compress_ok.event["event_type"])
        self.assertTrue(compress_ok.event["draft_only"])

        bad_schema = rpc.asr_compile(
            request_id=missing["request_id"],
            payload=missing["payload"],
            ts_ms=self.now_ms + 23,
        )
        self.assertFalse(bad_schema.success)
        self.assertEqual(VOICE_SCHEMA_INVALID, bad_schema.reason_code)
        self.assertEqual(S_VOICE_EDGE_FALLBACK_ASR_COMPILE, bad_schema.stage)

        rows = self.ledger.voice_event_log
        self.assertGreaterEqual(len(rows), 4)
        accepted_rows = [row for row in rows if row["status"] == STATUS_ACCEPTED]
        self.assertTrue(
            any(row["event_type"] == EVENT_FALLBACK_ASR_COMPILE for row in accepted_rows),
            msg=rows,
        )
        self.assertTrue(
            any(row["event_type"] == EVENT_FALLBACK_RETRIEVAL_HINTS for row in accepted_rows),
            msg=rows,
        )
        self.assertTrue(
            any(row["event_type"] == EVENT_FALLBACK_CONTEXT_COMPRESS for row in accepted_rows),
            msg=rows,
        )
        self.assertTrue(
            all(isinstance(row["payload"], dict) for row in rows),
            msg=rows,
        )


if __name__ == "__main__":
    unittest.main()
