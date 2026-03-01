import json
import unittest
from pathlib import Path

from edge_client.rpc.voice import (
    DIRECTION_EDGE_TO_HOST,
    DIRECTION_HOST_TO_EDGE,
    EVENT_ASR_CHUNK,
    EVENT_BARGE_IN,
    EVENT_SPEAK,
    S_VOICE_ASR_INGRESS,
    S_VOICE_BARGE_IN,
    S_VOICE_SPEAK_EGRESS,
    STATUS_ACCEPTED,
    STATUS_REJECTED,
    VoiceRpcContract,
)
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    VOICE_LOW_CONFIDENCE,
    VOICE_OUT_OF_ORDER,
    VOICE_SCHEMA_INVALID,
    VOICE_THROTTLED,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "voice"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TestVoiceRpcContract(unittest.TestCase):
    def setUp(self) -> None:
        self.now_ms = 1_800_000_400_000
        self.ledger = SQLiteLedger(db_path=":memory:")
        self.rpc = VoiceRpcContract(
            ledger=self.ledger,
            asr_min_confidence=0.60,
            asr_min_interval_ms=120,
        )

    def test_t_voice_01_asr_schema_order_throttle_and_low_confidence(self) -> None:
        valid = _load_json(FIXTURES / "good" / "asr_chunk_valid.json")
        missing = _load_json(FIXTURES / "bad" / "asr_missing_fields.json")
        out_of_order = _load_json(FIXTURES / "bad" / "asr_out_of_order.json")
        low_conf = _load_json(FIXTURES / "bad" / "asr_low_confidence.json")
        throttled = _load_json(FIXTURES / "bad" / "asr_throttled.json")

        ok = self.rpc.asr_chunk(
            request_id=valid["request_id"],
            payload=valid["payload"],
            ts_ms=self.now_ms,
        )
        self.assertTrue(ok.success)
        self.assertEqual(STATUS_ACCEPTED, ok.status)
        self.assertEqual(S_VOICE_ASR_INGRESS, ok.stage)
        self.assertEqual(EVENT_ASR_CHUNK, ok.event["event_type"])
        self.assertEqual(DIRECTION_EDGE_TO_HOST, ok.event["direction"])

        bad_schema = self.rpc.asr_chunk(
            request_id=missing["request_id"],
            payload=missing["payload"],
            ts_ms=self.now_ms + 200,
        )
        self.assertFalse(bad_schema.success)
        self.assertEqual(STATUS_REJECTED, bad_schema.status)
        self.assertEqual(VOICE_SCHEMA_INVALID, bad_schema.reason_code)
        self.assertEqual(S_VOICE_ASR_INGRESS, bad_schema.stage)

        bad_order = self.rpc.asr_chunk(
            request_id=out_of_order["request_id"],
            payload=out_of_order["payload"],
            ts_ms=self.now_ms + 260,
        )
        self.assertFalse(bad_order.success)
        self.assertEqual(VOICE_OUT_OF_ORDER, bad_order.reason_code)

        bad_low_conf = self.rpc.asr_chunk(
            request_id=low_conf["request_id"],
            payload=low_conf["payload"],
            ts_ms=self.now_ms + 300,
        )
        self.assertTrue(bad_low_conf.success)
        self.assertEqual(STATUS_ACCEPTED, bad_low_conf.status)
        self.assertEqual(VOICE_LOW_CONFIDENCE, bad_low_conf.reason_code)
        self.assertTrue(bad_low_conf.event["low_confidence"])
        self.assertEqual([], bad_low_conf.event["alternates"])

        bad_throttle = self.rpc.asr_chunk(
            request_id=throttled["request_id"],
            payload=throttled["payload"],
            ts_ms=self.now_ms + 50,
        )
        self.assertFalse(bad_throttle.success)
        self.assertEqual(VOICE_THROTTLED, bad_throttle.reason_code)

        self.assertEqual(5, len(self.ledger.voice_event_log))
        first_row = self.ledger.voice_event_log[0]
        self.assertEqual(valid["request_id"], first_row["request_id"])
        self.assertEqual(EVENT_ASR_CHUNK, first_row["event_type"])
        self.assertEqual(DIRECTION_EDGE_TO_HOST, first_row["direction"])
        self.assertEqual(STATUS_ACCEPTED, first_row["status"])
        self.assertEqual(S_VOICE_ASR_INGRESS, first_row["stage"])
        self.assertIsInstance(first_row["payload"], dict)

    def test_t_voice_02_speak_schema_and_replayable_event(self) -> None:
        valid = _load_json(FIXTURES / "good" / "speak_valid.json")
        missing_text = _load_json(FIXTURES / "bad" / "speak_missing_text.json")

        ok = self.rpc.speak(
            request_id=valid["request_id"],
            payload=valid["payload"],
            ts_ms=self.now_ms + 10,
        )
        self.assertTrue(ok.success)
        self.assertEqual(STATUS_ACCEPTED, ok.status)
        self.assertEqual(S_VOICE_SPEAK_EGRESS, ok.stage)
        self.assertEqual(EVENT_SPEAK, ok.event["event_type"])
        self.assertEqual(DIRECTION_HOST_TO_EDGE, ok.event["direction"])

        bad = self.rpc.speak(
            request_id=missing_text["request_id"],
            payload=missing_text["payload"],
            ts_ms=self.now_ms + 11,
        )
        self.assertFalse(bad.success)
        self.assertEqual(STATUS_REJECTED, bad.status)
        self.assertEqual(VOICE_SCHEMA_INVALID, bad.reason_code)
        self.assertEqual(S_VOICE_SPEAK_EGRESS, bad.stage)

        rows = [r for r in self.ledger.voice_event_log if r["event_type"] == EVENT_SPEAK]
        self.assertEqual(2, len(rows))
        self.assertEqual(valid["request_id"], rows[0]["request_id"])
        self.assertEqual(missing_text["request_id"], rows[1]["request_id"])
        self.assertEqual(S_VOICE_SPEAK_EGRESS, rows[0]["stage"])

    def test_t_voice_03_barge_in_schema_and_replayable_event(self) -> None:
        valid = _load_json(FIXTURES / "good" / "barge_in_valid.json")
        missing = _load_json(FIXTURES / "bad" / "barge_in_missing_session.json")

        ok = self.rpc.barge_in(
            request_id=valid["request_id"],
            payload=valid["payload"],
            ts_ms=self.now_ms + 20,
        )
        self.assertTrue(ok.success)
        self.assertEqual(STATUS_ACCEPTED, ok.status)
        self.assertEqual(S_VOICE_BARGE_IN, ok.stage)
        self.assertEqual(EVENT_BARGE_IN, ok.event["event_type"])

        bad = self.rpc.barge_in(
            request_id=missing["request_id"],
            payload=missing["payload"],
            ts_ms=self.now_ms + 21,
        )
        self.assertFalse(bad.success)
        self.assertEqual(VOICE_SCHEMA_INVALID, bad.reason_code)
        self.assertEqual(S_VOICE_BARGE_IN, bad.stage)

        rows = [r for r in self.ledger.voice_event_log if r["event_type"] == EVENT_BARGE_IN]
        self.assertEqual(2, len(rows))
        self.assertEqual(DIRECTION_EDGE_TO_HOST, rows[0]["direction"])
        self.assertEqual(valid["request_id"], rows[0]["request_id"])
        self.assertEqual(missing["request_id"], rows[1]["request_id"])


if __name__ == "__main__":
    unittest.main()
