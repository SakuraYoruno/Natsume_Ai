import json
import unittest
from pathlib import Path

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    VISION_GATE_GAMING_BLOCKED,
    VISION_OCR_UNAVAILABLE,
)
from host_brain.vision.screen_ocr import (
    BBOX_SPACE,
    EVENT_SCREEN_OCR,
    S_VISION_GATE,
    S_VISION_OCR,
    STATUS_DEGRADED,
    STATUS_OK,
    ScreenOcrServiceContract,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "vision"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TestVisionScreenOcrContract(unittest.TestCase):
    def setUp(self) -> None:
        self.now_ms = 1_800_001_100_000
        self.ledger = SQLiteLedger(db_path=":memory:")

    def test_t_vision_gate_01_gaming_blocks_ocr_with_stable_reason(self) -> None:
        fixture = _load_json(FIXTURES / "bad" / "ocr_gaming_blocked_request.json")
        svc = ScreenOcrServiceContract(ledger=self.ledger, available=True)

        result = svc.recognize(
            request_id=fixture["request_id"],
            intent_id=fixture["intent_id"],
            source_ref=fixture["payload"]["source_ref"],
            frame_text=fixture["payload"]["frame_text"],
            sys_mode=fixture["payload"]["sys_mode"],
            trigger=fixture["payload"]["trigger"],
            timeout_ms=fixture["payload"]["timeout_ms"],
            ts_ms=self.now_ms,
        )

        self.assertFalse(result.success)
        self.assertEqual(STATUS_DEGRADED, result.status)
        self.assertEqual(VISION_GATE_GAMING_BLOCKED, result.reason_code)
        self.assertEqual(S_VISION_GATE, result.stage)
        self.assertEqual([], result.output["text_blocks"])
        self.assertEqual(VISION_GATE_GAMING_BLOCKED, result.output["quality"]["reason_code"])
        self.assertEqual(S_VISION_GATE, result.output["quality"]["stage"])
        self.assertNotIn("saw", result.detail.lower())
        self.assertNotIn("looked", result.detail.lower())

        rows = self.ledger.vision_event_log
        self.assertEqual(1, len(rows))
        row = rows[0]
        self.assertEqual(fixture["request_id"], row["request_id"])
        self.assertEqual(EVENT_SCREEN_OCR, row["event_type"])
        self.assertEqual(STATUS_DEGRADED, row["status"])
        self.assertEqual(VISION_GATE_GAMING_BLOCKED, row["reason_code"])
        self.assertEqual(S_VISION_GATE, row["stage"])
        self.assertEqual(fixture["payload"]["source_ref"], row["source_ref"])

    def test_t_vision_01_non_gaming_ocr_output_is_structured_and_replayable(self) -> None:
        fixture = _load_json(FIXTURES / "good" / "ocr_non_gaming_request.json")
        svc = ScreenOcrServiceContract(ledger=self.ledger, available=True)

        result = svc.recognize(
            request_id=fixture["request_id"],
            intent_id=fixture["intent_id"],
            source_ref=fixture["payload"]["source_ref"],
            frame_text=fixture["payload"]["frame_text"],
            sys_mode=fixture["payload"]["sys_mode"],
            trigger=fixture["payload"]["trigger"],
            timeout_ms=fixture["payload"]["timeout_ms"],
            ts_ms=self.now_ms + 1,
        )

        self.assertTrue(result.success)
        self.assertEqual(STATUS_OK, result.status)
        self.assertEqual(S_VISION_OCR, result.stage)
        self.assertEqual(STATUS_OK, result.output["status"])
        self.assertFalse(result.output["degraded"])
        self.assertEqual(BBOX_SPACE, result.output["bbox_space"])
        self.assertEqual(fixture["payload"]["source_ref"], result.output["source_ref"])
        self.assertIsInstance(result.output["text_blocks"], list)
        self.assertGreater(len(result.output["text_blocks"]), 0)
        for block in result.output["text_blocks"]:
            self.assertIn("text", block)
            self.assertIn("bbox", block)
            self.assertIn("conf", block)
            self.assertIsInstance(block["bbox"], dict)
            for key in ("x", "y", "w", "h", "space"):
                self.assertIn(key, block["bbox"])
            self.assertEqual(BBOX_SPACE, block["bbox"]["space"])
        json.loads(json.dumps(result.output, ensure_ascii=False))

        rows = [row for row in self.ledger.vision_event_log if row["request_id"] == fixture["request_id"]]
        self.assertEqual(1, len(rows))
        row = rows[0]
        self.assertEqual(EVENT_SCREEN_OCR, row["event_type"])
        self.assertEqual(STATUS_OK, row["status"])
        self.assertEqual(S_VISION_OCR, row["stage"])
        self.assertEqual(fixture["payload"]["source_ref"], row["source_ref"])
        self.assertEqual(len(result.output["text_blocks"]), row["payload"]["text_block_count"])

    def test_ocr_unavailable_reason_code_is_stable(self) -> None:
        fixture = _load_json(FIXTURES / "bad" / "ocr_engine_unavailable_request.json")
        svc = ScreenOcrServiceContract(ledger=self.ledger, available=False)

        a = svc.recognize(
            request_id=fixture["request_id"],
            intent_id=fixture["intent_id"],
            source_ref=fixture["payload"]["source_ref"],
            frame_text=fixture["payload"]["frame_text"],
            sys_mode=fixture["payload"]["sys_mode"],
            trigger=fixture["payload"]["trigger"],
            timeout_ms=fixture["payload"]["timeout_ms"],
            ts_ms=self.now_ms + 2,
        )
        b = svc.recognize(
            request_id="req_vision_ocr_003_repeat",
            intent_id="intent_vision_ocr_003_repeat",
            source_ref=fixture["payload"]["source_ref"],
            frame_text=fixture["payload"]["frame_text"],
            sys_mode=fixture["payload"]["sys_mode"],
            trigger=fixture["payload"]["trigger"],
            timeout_ms=fixture["payload"]["timeout_ms"],
            ts_ms=self.now_ms + 3,
        )
        self.assertEqual(VISION_OCR_UNAVAILABLE, a.reason_code)
        self.assertEqual(a.reason_code, b.reason_code)
        self.assertEqual([], a.output["text_blocks"])
        self.assertNotIn("no text", a.detail.lower())


if __name__ == "__main__":
    unittest.main()
