import json
import unittest
from pathlib import Path

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    VISION_CAMERA_EVIDENCE_REQUIRED,
    VISION_CAMERA_TIMEOUT,
    VISION_CAMERA_UNAVAILABLE,
)
from host_brain.vision.camera_event import (
    EVENT_CAMERA_RECOGNITION,
    S_VISION_CAMERA,
    STATUS_DEGRADED,
    STATUS_OK,
    CameraRecognitionServiceContract,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "vision"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TestVisionCameraEventContract(unittest.TestCase):
    def setUp(self) -> None:
        self.now_ms = 1_800_001_200_000
        self.ledger = SQLiteLedger(db_path=":memory:")

    def test_t_vision_02_camera_event_is_replayable_by_evidence_ref(self) -> None:
        fixture = _load_json(FIXTURES / "good" / "camera_event_request.json")
        svc = CameraRecognitionServiceContract(ledger=self.ledger, available=True)

        result = svc.detect(
            request_id=fixture["request_id"],
            intent_id=fixture["intent_id"],
            evidence_ref=fixture["payload"]["evidence_ref"],
            frame_text=fixture["payload"]["frame_text"],
            timeout_ms=fixture["payload"]["timeout_ms"],
            ts_ms=self.now_ms,
        )

        self.assertTrue(result.success)
        self.assertEqual(STATUS_OK, result.status)
        self.assertEqual(S_VISION_CAMERA, result.stage)
        self.assertEqual(STATUS_OK, result.output["status"])
        self.assertFalse(result.output["degraded"])
        self.assertEqual(fixture["payload"]["evidence_ref"], result.output["evidence_ref"])
        self.assertIsInstance(result.output["objects"], list)
        self.assertEqual(2, len(result.output["objects"]))
        self.assertEqual(1, result.output["summary"]["low_confidence_count"])
        self.assertEqual(2, result.output["summary"]["object_count"])
        for obj in result.output["objects"]:
            self.assertIn("label", obj)
            self.assertIn("conf", obj)
            self.assertIn("low_confidence", obj)
            if "bbox" in obj:
                self.assertIsInstance(obj["bbox"], dict)
                self.assertIn("space", obj["bbox"])
        json.loads(json.dumps(result.output, ensure_ascii=False))

        rows = [row for row in self.ledger.vision_event_log if row["request_id"] == fixture["request_id"]]
        self.assertEqual(1, len(rows))
        row = rows[0]
        self.assertEqual(EVENT_CAMERA_RECOGNITION, row["event_type"])
        self.assertEqual(STATUS_OK, row["status"])
        self.assertEqual(S_VISION_CAMERA, row["stage"])
        self.assertEqual(fixture["payload"]["evidence_ref"], row["source_ref"])
        self.assertEqual(2, row["payload"]["object_count"])

    def test_t_vision_02_missing_evidence_and_unavailable_are_stable(self) -> None:
        missing = _load_json(FIXTURES / "bad" / "camera_event_missing_evidence.json")
        unavailable = _load_json(FIXTURES / "bad" / "camera_event_unavailable_request.json")

        missing_svc = CameraRecognitionServiceContract(ledger=self.ledger, available=True)
        res_missing = missing_svc.detect(
            request_id=missing["request_id"],
            intent_id=missing["intent_id"],
            evidence_ref=missing["payload"]["evidence_ref"],
            frame_text=missing["payload"]["frame_text"],
            timeout_ms=missing["payload"]["timeout_ms"],
            ts_ms=self.now_ms + 1,
        )
        self.assertFalse(res_missing.success)
        self.assertEqual(STATUS_DEGRADED, res_missing.status)
        self.assertEqual(VISION_CAMERA_EVIDENCE_REQUIRED, res_missing.reason_code)
        self.assertEqual([], res_missing.output["objects"])

        unavailable_svc = CameraRecognitionServiceContract(ledger=self.ledger, available=False)
        res_a = unavailable_svc.detect(
            request_id=unavailable["request_id"],
            intent_id=unavailable["intent_id"],
            evidence_ref=unavailable["payload"]["evidence_ref"],
            frame_text=unavailable["payload"]["frame_text"],
            timeout_ms=unavailable["payload"]["timeout_ms"],
            ts_ms=self.now_ms + 2,
        )
        res_b = unavailable_svc.detect(
            request_id="req_vision_cam_003_repeat",
            intent_id="intent_vision_cam_003_repeat",
            evidence_ref=unavailable["payload"]["evidence_ref"],
            frame_text=unavailable["payload"]["frame_text"],
            timeout_ms=unavailable["payload"]["timeout_ms"],
            ts_ms=self.now_ms + 3,
        )
        self.assertFalse(res_a.success)
        self.assertFalse(res_b.success)
        self.assertEqual(VISION_CAMERA_UNAVAILABLE, res_a.reason_code)
        self.assertEqual(res_a.reason_code, res_b.reason_code)

        def _timeout_provider(_evidence_ref: str, _frame_text: str) -> tuple[list[dict], str]:
            raise TimeoutError("mock timeout")

        timeout_svc = CameraRecognitionServiceContract(
            ledger=self.ledger,
            available=True,
            provider=_timeout_provider,
            timeout_ms=100,
        )
        res_timeout = timeout_svc.detect(
            request_id="req_vision_cam_004",
            intent_id="intent_vision_cam_004",
            evidence_ref="evd://camera/frame_004",
            frame_text="bird:0.88",
            timeout_ms=100,
            ts_ms=self.now_ms + 4,
        )
        self.assertFalse(res_timeout.success)
        self.assertEqual(VISION_CAMERA_TIMEOUT, res_timeout.reason_code)

        reasons = [row["reason_code"] for row in self.ledger.vision_event_log if row["event_type"] == EVENT_CAMERA_RECOGNITION]
        self.assertIn(VISION_CAMERA_EVIDENCE_REQUIRED, reasons)
        self.assertIn(VISION_CAMERA_UNAVAILABLE, reasons)
        self.assertIn(VISION_CAMERA_TIMEOUT, reasons)


if __name__ == "__main__":
    unittest.main()
