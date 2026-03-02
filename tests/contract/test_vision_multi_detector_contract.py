"""T-Vision-03: Multi-detector registration, aggregation, and partial degradation stability.

验证：
- 多检测器注册表的注册/注销/列表/快照；
- 多检测器并行输出结构稳定且各自 evidence_ref 独立；
- 部分检测器不可用时输出仍合法且降级可解释；
- 全部不可用时输出 ALL_FAILED；
- 与 CameraRecognitionServiceContract.detect_multi() 的集成；
- 现有 T-Vision-02 无回归。
"""

import json
import unittest
from pathlib import Path
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    VISION_CAMERA_EVIDENCE_REQUIRED,
    VISION_CAMERA_UNAVAILABLE,
    VISION_DETECTOR_ALL_FAILED,
    VISION_DETECTOR_TIMEOUT,
    VISION_DETECTOR_UNAVAILABLE,
)
from host_brain.vision.camera_event import (
    EVENT_CAMERA_RECOGNITION,
    S_VISION_CAMERA,
    STATUS_DEGRADED,
    STATUS_OK,
    CameraRecognitionServiceContract,
)
from host_brain.vision.detector_registry import (
    AGG_STATUS_ALL_FAILED,
    AGG_STATUS_OK,
    AGG_STATUS_PARTIAL,
    DETECTOR_TYPE_FACE,
    DETECTOR_TYPE_GESTURE,
    DETECTOR_TYPE_OBJECT,
    DETECTOR_TYPE_SCENE,
    AggregatedCameraEvent,
    DetectorConfig,
    DetectorRegistry,
    DetectorResult,
    MultiDetectorAggregator,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "vision"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ── Mock Providers ──

def _make_ok_provider(label: str, conf: float, det_type: str):
    """返回一个正常的检测器 provider。"""
    def _provider(evidence_ref: str, frame_data: str) -> tuple[list[dict[str, Any]], str]:
        return [
            {"label": label, "conf": conf, "bbox": {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}},
        ], f"{det_type}-mock-v1"
    return _provider


def _timeout_provider(evidence_ref: str, frame_data: str) -> tuple[list[dict[str, Any]], str]:
    raise TimeoutError("mock timeout")


def _error_provider(evidence_ref: str, frame_data: str) -> tuple[list[dict[str, Any]], str]:
    raise RuntimeError("mock detector error")


class TestDetectorRegistryUnit(unittest.TestCase):
    """检测器注册表单元测试。"""

    def test_register_and_list(self) -> None:
        reg = DetectorRegistry()
        cfg1 = DetectorConfig(detector_id="d1", type=DETECTOR_TYPE_FACE, model_id="m1")
        cfg2 = DetectorConfig(detector_id="d2", type=DETECTOR_TYPE_OBJECT, model_id="m2", enabled=False)
        reg.register(cfg1)
        reg.register(cfg2)
        self.assertEqual(2, reg.count())
        self.assertEqual(1, reg.count_enabled())
        enabled = reg.list_enabled()
        self.assertEqual(1, len(enabled))
        self.assertEqual("d1", enabled[0].detector_id)

    def test_unregister(self) -> None:
        reg = DetectorRegistry()
        cfg = DetectorConfig(detector_id="d1", type=DETECTOR_TYPE_GESTURE, model_id="m1")
        reg.register(cfg)
        self.assertTrue(reg.unregister("d1"))
        self.assertFalse(reg.unregister("d1"))
        self.assertEqual(0, reg.count())

    def test_get(self) -> None:
        reg = DetectorRegistry()
        cfg = DetectorConfig(detector_id="d1", type=DETECTOR_TYPE_SCENE, model_id="m1")
        reg.register(cfg)
        self.assertIsNotNone(reg.get("d1"))
        self.assertIsNone(reg.get("no_such"))

    def test_snapshot_is_deterministic(self) -> None:
        reg = DetectorRegistry()
        reg.register(DetectorConfig(detector_id="b", type=DETECTOR_TYPE_OBJECT, model_id="m2"))
        reg.register(DetectorConfig(detector_id="a", type=DETECTOR_TYPE_FACE, model_id="m1"))
        snap = reg.to_snapshot()
        self.assertEqual(2, len(snap))
        self.assertEqual("a", snap[0]["detector_id"])
        self.assertEqual("b", snap[1]["detector_id"])
        # 序列化稳定性
        json.loads(json.dumps(snap, ensure_ascii=False))

    def test_invalid_type_raises(self) -> None:
        with self.assertRaises(ValueError):
            DetectorConfig(detector_id="d1", type="invalid_type", model_id="m1")

    def test_clear(self) -> None:
        reg = DetectorRegistry()
        reg.register(DetectorConfig(detector_id="d1", type=DETECTOR_TYPE_FACE, model_id="m1"))
        reg.clear()
        self.assertEqual(0, reg.count())

    def test_overwrite_existing(self) -> None:
        reg = DetectorRegistry()
        cfg1 = DetectorConfig(detector_id="d1", type=DETECTOR_TYPE_FACE, model_id="m1", enabled=True)
        cfg2 = DetectorConfig(detector_id="d1", type=DETECTOR_TYPE_FACE, model_id="m1-v2", enabled=False)
        reg.register(cfg1)
        reg.register(cfg2)
        self.assertEqual(1, reg.count())
        self.assertEqual(0, reg.count_enabled())
        self.assertEqual("m1-v2", reg.get("d1").model_id)


class TestMultiDetectorAggregatorUnit(unittest.TestCase):
    """多检测器聚合器单元测试。"""

    def _make_registry(self, configs: list[DetectorConfig]) -> DetectorRegistry:
        reg = DetectorRegistry()
        for c in configs:
            reg.register(c)
        return reg

    def test_all_ok(self) -> None:
        """所有检测器都正常运行。"""
        configs = [
            DetectorConfig(detector_id="det-face", type=DETECTOR_TYPE_FACE, model_id="face-m1"),
            DetectorConfig(detector_id="det-obj", type=DETECTOR_TYPE_OBJECT, model_id="obj-m1"),
        ]
        reg = self._make_registry(configs)
        agg = MultiDetectorAggregator(registry=reg)
        agg.set_provider("det-face", _make_ok_provider("alice", 0.95, "face"))
        agg.set_provider("det-obj", _make_ok_provider("cat", 0.82, "object"))

        result = agg.run(evidence_ref="evd://camera/test_001", frame_data="test")

        self.assertEqual(AGG_STATUS_OK, result.status)
        self.assertIsNone(result.reason_code)
        self.assertEqual(2, result.total_detectors)
        self.assertEqual(2, result.ok_detectors)
        self.assertEqual(0, result.failed_detector_count)
        self.assertEqual(2, result.total_detections)
        self.assertEqual(0, len(result.failed_detectors))

        # 每条 detection 都有独立的 evidence_ref 和 detector_id
        for det in result.detections:
            self.assertIn("detector_id", det)
            self.assertIn("type", det)
            self.assertIn("label", det)
            self.assertIn("confidence", det)
            self.assertIn("evidence_ref", det)
            self.assertEqual("evd://camera/test_001", det["evidence_ref"])

    def test_partial_failure(self) -> None:
        """部分检测器不可用（face OK, object timeout）。"""
        configs = [
            DetectorConfig(detector_id="det-face", type=DETECTOR_TYPE_FACE, model_id="face-m1"),
            DetectorConfig(detector_id="det-obj", type=DETECTOR_TYPE_OBJECT, model_id="obj-m1"),
        ]
        reg = self._make_registry(configs)
        agg = MultiDetectorAggregator(registry=reg)
        agg.set_provider("det-face", _make_ok_provider("alice", 0.91, "face"))
        agg.set_provider("det-obj", _timeout_provider)

        result = agg.run(evidence_ref="evd://camera/test_002", frame_data="test")

        self.assertEqual(AGG_STATUS_PARTIAL, result.status)
        self.assertIsNone(result.reason_code)
        self.assertEqual(2, result.total_detectors)
        self.assertEqual(1, result.ok_detectors)
        self.assertEqual(1, result.failed_detector_count)
        self.assertEqual(1, result.total_detections)
        self.assertEqual(1, len(result.failed_detectors))
        failed = result.failed_detectors[0]
        self.assertEqual("det-obj", failed["detector_id"])
        self.assertEqual(VISION_DETECTOR_TIMEOUT, failed["reason_code"])

    def test_all_failed(self) -> None:
        """全部检测器不可用。"""
        configs = [
            DetectorConfig(detector_id="det-face", type=DETECTOR_TYPE_FACE, model_id="face-m1"),
            DetectorConfig(detector_id="det-obj", type=DETECTOR_TYPE_OBJECT, model_id="obj-m1"),
        ]
        reg = self._make_registry(configs)
        agg = MultiDetectorAggregator(registry=reg)
        agg.set_provider("det-face", _error_provider)
        agg.set_provider("det-obj", _timeout_provider)

        result = agg.run(evidence_ref="evd://camera/test_003", frame_data="test")

        self.assertEqual(AGG_STATUS_ALL_FAILED, result.status)
        self.assertEqual(VISION_DETECTOR_ALL_FAILED, result.reason_code)
        self.assertEqual(0, result.ok_detectors)
        self.assertEqual(2, result.failed_detector_count)
        self.assertEqual(0, result.total_detections)

    def test_no_provider_is_unavailable(self) -> None:
        """检测器已注册但没有 provider → VISION_DETECTOR_UNAVAILABLE。"""
        configs = [
            DetectorConfig(detector_id="det-face", type=DETECTOR_TYPE_FACE, model_id="face-m1"),
        ]
        reg = self._make_registry(configs)
        agg = MultiDetectorAggregator(registry=reg)
        # 不设置 provider

        result = agg.run(evidence_ref="evd://camera/test_004", frame_data="test")

        self.assertEqual(AGG_STATUS_ALL_FAILED, result.status)
        self.assertEqual(1, result.failed_detector_count)
        failed = result.failed_detectors[0]
        self.assertEqual(VISION_DETECTOR_UNAVAILABLE, failed["reason_code"])

    def test_empty_registry(self) -> None:
        """空注册表 → ALL_FAILED。"""
        reg = DetectorRegistry()
        agg = MultiDetectorAggregator(registry=reg)
        result = agg.run(evidence_ref="evd://camera/test_005", frame_data="test")
        self.assertEqual(AGG_STATUS_ALL_FAILED, result.status)
        self.assertEqual(VISION_DETECTOR_ALL_FAILED, result.reason_code)

    def test_specific_detectors_filter(self) -> None:
        """指定 detectors 参数只运行部分检测器。"""
        configs = [
            DetectorConfig(detector_id="det-face", type=DETECTOR_TYPE_FACE, model_id="face-m1"),
            DetectorConfig(detector_id="det-obj", type=DETECTOR_TYPE_OBJECT, model_id="obj-m1"),
            DetectorConfig(detector_id="det-gest", type=DETECTOR_TYPE_GESTURE, model_id="gest-m1"),
        ]
        reg = self._make_registry(configs)
        agg = MultiDetectorAggregator(registry=reg)
        agg.set_provider("det-face", _make_ok_provider("alice", 0.95, "face"))
        agg.set_provider("det-obj", _make_ok_provider("cat", 0.82, "object"))
        agg.set_provider("det-gest", _make_ok_provider("wave", 0.76, "gesture"))

        result = agg.run(
            evidence_ref="evd://camera/test_006",
            frame_data="test",
            detectors=["det-face", "det-gest"],
        )
        self.assertEqual(AGG_STATUS_OK, result.status)
        self.assertEqual(2, result.total_detectors)
        # 只有 face 和 gesture 的结果
        det_ids = {d["detector_id"] for d in result.detections}
        self.assertEqual({"det-face", "det-gest"}, det_ids)

    def test_low_confidence_count(self) -> None:
        """低置信度检测计数。"""
        def _low_conf_provider(ev: str, fd: str) -> tuple[list[dict], str]:
            return [
                {"label": "face_1", "conf": 0.3},
                {"label": "face_2", "conf": 0.9},
            ], "face-m1"

        configs = [
            DetectorConfig(detector_id="det-face", type=DETECTOR_TYPE_FACE, model_id="face-m1", confidence_threshold=0.5),
        ]
        reg = self._make_registry(configs)
        agg = MultiDetectorAggregator(registry=reg)
        agg.set_provider("det-face", _low_conf_provider)

        result = agg.run(evidence_ref="evd://camera/test_007", frame_data="test")
        self.assertEqual(1, result.low_confidence_count)

    def test_output_json_serializable(self) -> None:
        """聚合结果可 JSON 序列化。"""
        configs = [
            DetectorConfig(detector_id="det-face", type=DETECTOR_TYPE_FACE, model_id="face-m1"),
        ]
        reg = self._make_registry(configs)
        agg = MultiDetectorAggregator(registry=reg)
        agg.set_provider("det-face", _make_ok_provider("alice", 0.92, "face"))
        result = agg.run(evidence_ref="evd://camera/test_008", frame_data="test")
        # 确认序列化不报错
        json.loads(json.dumps({
            "status": result.status,
            "detections": result.detections,
            "detector_results": result.detector_results,
            "failed_detectors": result.failed_detectors,
        }, ensure_ascii=False))

    def test_remove_provider(self) -> None:
        """移除 provider 后运行检测器 → UNAVAILABLE。"""
        configs = [
            DetectorConfig(detector_id="det-face", type=DETECTOR_TYPE_FACE, model_id="face-m1"),
        ]
        reg = self._make_registry(configs)
        agg = MultiDetectorAggregator(registry=reg)
        agg.set_provider("det-face", _make_ok_provider("alice", 0.95, "face"))
        agg.remove_provider("det-face")
        result = agg.run(evidence_ref="evd://camera/test_009", frame_data="test")
        self.assertEqual(AGG_STATUS_ALL_FAILED, result.status)


class TestCameraServiceMultiDetectIntegration(unittest.TestCase):
    """CameraRecognitionServiceContract.detect_multi() 集成测试。"""

    def setUp(self) -> None:
        self.now_ms = 1_800_002_400_000
        self.ledger = SQLiteLedger(db_path=":memory:")

    def test_multi_detect_all_ok(self) -> None:
        """多检测器全部成功 → STATUS_OK。"""
        fixture = _load_json(FIXTURES / "good" / "multi_detector_mixed_request.json")
        reg = DetectorRegistry()
        for d in fixture["payload"]["detectors"]:
            reg.register(DetectorConfig(
                detector_id=d["detector_id"],
                type=d["type"],
                model_id=d["model_id"],
                confidence_threshold=d["confidence_threshold"],
                enabled=d["enabled"],
                timeout_ms=d["timeout_ms"],
            ))
        agg = MultiDetectorAggregator(registry=reg)
        agg.set_provider("det-face-v1", _make_ok_provider("alice", 0.92, "face"))
        agg.set_provider("det-object-v1", _make_ok_provider("cat", 0.85, "object"))
        agg.set_provider("det-gesture-v1", _make_ok_provider("wave", 0.78, "gesture"))

        svc = CameraRecognitionServiceContract(ledger=self.ledger, available=True)
        result = svc.detect_multi(
            request_id=fixture["request_id"],
            intent_id=fixture["intent_id"],
            evidence_ref=fixture["payload"]["evidence_ref"],
            frame_data=fixture["payload"]["frame_data"],
            aggregator=agg,
            ts_ms=self.now_ms,
        )

        self.assertTrue(result.success)
        self.assertEqual(STATUS_OK, result.status)
        self.assertEqual(S_VISION_CAMERA, result.stage)
        self.assertFalse(result.output["degraded"])
        self.assertEqual("multi", result.output["mode"])
        self.assertEqual(3, result.output["summary"]["total_detectors"])
        self.assertEqual(3, result.output["summary"]["ok_detectors"])
        self.assertEqual(0, result.output["summary"]["failed_detector_count"])
        self.assertEqual(3, result.output["summary"]["total_detections"])
        self.assertIsInstance(result.output["detections"], list)
        for det in result.output["detections"]:
            self.assertIn("detector_id", det)
            self.assertIn("type", det)
            self.assertIn("evidence_ref", det)
        json.loads(json.dumps(result.output, ensure_ascii=False))

        # 审计日志
        rows = [r for r in self.ledger.vision_event_log if r["request_id"] == fixture["request_id"]]
        self.assertEqual(1, len(rows))
        self.assertEqual(STATUS_OK, rows[0]["status"])

    def test_multi_detect_partial_degraded(self) -> None:
        """部分检测器失败 → STATUS_DEGRADED + PARTIAL。"""
        fixture = _load_json(FIXTURES / "bad" / "multi_detector_partial_unavailable.json")
        reg = DetectorRegistry()
        for d in fixture["payload"]["detectors"]:
            reg.register(DetectorConfig(
                detector_id=d["detector_id"],
                type=d["type"],
                model_id=d["model_id"],
                confidence_threshold=d["confidence_threshold"],
                enabled=d["enabled"],
                timeout_ms=d["timeout_ms"],
            ))
        agg = MultiDetectorAggregator(registry=reg)
        # face OK, object timeout, gesture no provider
        agg.set_provider("det-face-v1", _make_ok_provider("person", 0.88, "face"))
        agg.set_provider("det-object-v1", _timeout_provider)
        # det-gesture-v1 没有 provider → UNAVAILABLE

        svc = CameraRecognitionServiceContract(ledger=self.ledger, available=True)
        result = svc.detect_multi(
            request_id=fixture["request_id"],
            intent_id=fixture["intent_id"],
            evidence_ref=fixture["payload"]["evidence_ref"],
            frame_data=fixture["payload"]["frame_data"],
            aggregator=agg,
            ts_ms=self.now_ms,
        )

        self.assertFalse(result.success)
        self.assertEqual(STATUS_DEGRADED, result.status)
        self.assertTrue(result.output["degraded"])
        self.assertEqual(3, result.output["summary"]["total_detectors"])
        self.assertEqual(1, result.output["summary"]["ok_detectors"])
        self.assertEqual(2, result.output["summary"]["failed_detector_count"])
        self.assertEqual(1, result.output["summary"]["total_detections"])
        # 失败检测器列表
        failed = result.output["failed_detectors"]
        self.assertEqual(2, len(failed))
        failed_ids = {f["detector_id"] for f in failed}
        self.assertIn("det-object-v1", failed_ids)
        self.assertIn("det-gesture-v1", failed_ids)
        failed_codes = {f["reason_code"] for f in failed}
        self.assertIn(VISION_DETECTOR_TIMEOUT, failed_codes)
        self.assertIn(VISION_DETECTOR_UNAVAILABLE, failed_codes)

    def test_multi_detect_all_failed(self) -> None:
        """全部检测器失败 → STATUS_DEGRADED + ALL_FAILED。"""
        fixture = _load_json(FIXTURES / "bad" / "multi_detector_all_failed.json")
        reg = DetectorRegistry()
        for d in fixture["payload"]["detectors"]:
            reg.register(DetectorConfig(
                detector_id=d["detector_id"],
                type=d["type"],
                model_id=d["model_id"],
                confidence_threshold=d["confidence_threshold"],
                enabled=d["enabled"],
                timeout_ms=d["timeout_ms"],
            ))
        agg = MultiDetectorAggregator(registry=reg)
        agg.set_provider("det-face-v1", _error_provider)
        agg.set_provider("det-object-v1", _timeout_provider)
        # det-gesture-v1 无 provider

        svc = CameraRecognitionServiceContract(ledger=self.ledger, available=True)
        result = svc.detect_multi(
            request_id=fixture["request_id"],
            intent_id=fixture["intent_id"],
            evidence_ref=fixture["payload"]["evidence_ref"],
            frame_data=fixture["payload"]["frame_data"],
            aggregator=agg,
            ts_ms=self.now_ms,
        )

        self.assertFalse(result.success)
        self.assertEqual(STATUS_DEGRADED, result.status)
        self.assertEqual(VISION_DETECTOR_ALL_FAILED, result.reason_code)
        self.assertTrue(result.output["degraded"])
        self.assertEqual(0, result.output["summary"]["ok_detectors"])
        self.assertEqual(3, result.output["summary"]["failed_detector_count"])
        self.assertEqual([], result.output["detections"])

    def test_multi_detect_missing_evidence_ref(self) -> None:
        """evidence_ref 缺失 → VISION_CAMERA_EVIDENCE_REQUIRED。"""
        svc = CameraRecognitionServiceContract(ledger=self.ledger, available=True)
        result = svc.detect_multi(
            request_id="req_multi_no_evd",
            evidence_ref="  ",
            frame_data="test",
            aggregator=MultiDetectorAggregator(registry=DetectorRegistry()),
            ts_ms=self.now_ms,
        )
        self.assertFalse(result.success)
        self.assertEqual(VISION_CAMERA_EVIDENCE_REQUIRED, result.reason_code)

    def test_multi_detect_no_aggregator(self) -> None:
        """没有传入 aggregator → VISION_CAMERA_UNAVAILABLE。"""
        svc = CameraRecognitionServiceContract(ledger=self.ledger, available=True)
        result = svc.detect_multi(
            request_id="req_multi_no_agg",
            evidence_ref="evd://camera/frame_no_agg",
            frame_data="test",
            ts_ms=self.now_ms,
        )
        self.assertFalse(result.success)
        self.assertEqual(VISION_CAMERA_UNAVAILABLE, result.reason_code)

    def test_multi_detect_output_is_json_serializable(self) -> None:
        """detect_multi 输出必须可 JSON 序列化。"""
        reg = DetectorRegistry()
        reg.register(DetectorConfig(detector_id="d1", type=DETECTOR_TYPE_FACE, model_id="m1"))
        agg = MultiDetectorAggregator(registry=reg)
        agg.set_provider("d1", _make_ok_provider("face", 0.9, "face"))
        svc = CameraRecognitionServiceContract(ledger=self.ledger, available=True)
        result = svc.detect_multi(
            request_id="req_json_test",
            evidence_ref="evd://camera/frame_json",
            frame_data="test",
            aggregator=agg,
            ts_ms=self.now_ms,
        )
        json.loads(json.dumps(result.output, ensure_ascii=False))


class TestExistingTVision02NoRegression(unittest.TestCase):
    """确认现有 T-Vision-02 测试口径无回归。"""

    def setUp(self) -> None:
        self.now_ms = 1_800_001_200_000
        self.ledger = SQLiteLedger(db_path=":memory:")

    def test_single_detect_still_works(self) -> None:
        """单检测器 detect() 接口保持向后兼容。"""
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
        self.assertFalse(result.output["degraded"])
        self.assertEqual(fixture["payload"]["evidence_ref"], result.output["evidence_ref"])

    def test_single_detect_unavailable_stable(self) -> None:
        """单检测器不可用路径保持不变。"""
        fixture = _load_json(FIXTURES / "bad" / "camera_event_unavailable_request.json")
        svc = CameraRecognitionServiceContract(ledger=self.ledger, available=False)
        result = svc.detect(
            request_id=fixture["request_id"],
            intent_id=fixture["intent_id"],
            evidence_ref=fixture["payload"]["evidence_ref"],
            frame_text=fixture["payload"]["frame_text"],
            timeout_ms=fixture["payload"]["timeout_ms"],
            ts_ms=self.now_ms,
        )
        self.assertFalse(result.success)
        self.assertEqual(STATUS_DEGRADED, result.status)
        from host_brain.core.errors import VISION_CAMERA_UNAVAILABLE as VCU
        self.assertEqual(VCU, result.reason_code)


if __name__ == "__main__":
    unittest.main()
