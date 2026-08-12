"""Camera recognition contract with evidence-bound replay outputs."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
import re
from typing import Any, Callable

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    VISION_CAMERA_EVIDENCE_REQUIRED,
    VISION_CAMERA_TIMEOUT,
    VISION_CAMERA_UNAVAILABLE,
)
from host_brain.core.ids import now_ms


EVENT_CAMERA_RECOGNITION = "CAMERA_RECOGNITION"
S_VISION_CAMERA = "S_VISION_CAMERA"

STATUS_OK = "OK"
STATUS_DEGRADED = "DEGRADED"

CAMERA_DETECTOR_ID = "vision-camera-heuristic-v1"
BBOX_SPACE = "xywh_norm_v1"

CameraProvider = Callable[[str, str], tuple[list[dict[str, Any]], str]]


@dataclass(frozen=True)
class CameraEventResult:
    success: bool
    status: str
    reason_code: str | None
    stage: str
    detail: str
    output: dict[str, Any]


class CameraRecognitionServiceContract:
    """Host camera recognition service with evidence-bound audit output."""

    def __init__(
        self,
        *,
        ledger: SQLiteLedger | None = None,
        provider: CameraProvider | None = None,
        available: bool = True,
        timeout_ms: int = 500,
        low_conf_threshold: float = 0.6,
    ) -> None:
        self.ledger = ledger
        self.provider = provider or _default_provider
        self.available = bool(available)
        self.timeout_ms = int(timeout_ms)
        self.low_conf_threshold = _coerce_unit_float(low_conf_threshold, 0.6)

    def detect(
        self,
        *,
        request_id: str,
        evidence_ref: str,
        frame_text: str,
        intent_id: str | None = None,
        timeout_ms: int | None = None,
        ts_ms: int | None = None,
    ) -> CameraEventResult:
        # frame_text 仅供本次 provider 调用；结果与审计以 evidence_ref 绑定可回放来源，
        # 防止把易变的内存帧内容当作可复核的视觉事实。
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        timeout_value = int(timeout_ms if timeout_ms is not None else self.timeout_ms)
        evidence_value = str(evidence_ref or "").strip()

        if not _valid_ref(evidence_value):
            # 缺少证据引用时返回降级而非尝试识别：调用者可明确看到“未处理”，
            # 不能把无来源的对象标签用于后续情境、记忆或用户状态推断。
            return self._degrade(
                request_id=request_id,
                intent_id=intent_id,
                reason_code=VISION_CAMERA_EVIDENCE_REQUIRED,
                detail="camera event requires replayable evidence_ref",
                evidence_ref=evidence_value,
                detector_id=CAMERA_DETECTOR_ID,
                objects=[],
                latency_ms=0,
                ts_ms=now_value,
            )

        if not self.available:
            # 引擎不可用保持同一输出形状和审计事件，使上游可按 reason_code 降级，
            # 而不需要把服务异常解释成“画面中没有对象”。
            return self._degrade(
                request_id=request_id,
                intent_id=intent_id,
                reason_code=VISION_CAMERA_UNAVAILABLE,
                detail="camera recognizer unavailable",
                evidence_ref=evidence_value,
                detector_id=CAMERA_DETECTOR_ID,
                objects=[],
                latency_ms=0,
                ts_ms=now_value,
            )

        start = perf_counter()
        try:
            raw_objects, detector_id = self.provider(evidence_value, str(frame_text or ""))
        except TimeoutError:
            latency_ms = int(round((perf_counter() - start) * 1000))
            return self._degrade(
                request_id=request_id,
                intent_id=intent_id,
                reason_code=VISION_CAMERA_TIMEOUT,
                detail=f"camera recognizer timeout>{timeout_value}ms",
                evidence_ref=evidence_value,
                detector_id=CAMERA_DETECTOR_ID,
                objects=[],
                latency_ms=latency_ms,
                ts_ms=now_value,
            )
        except Exception as exc:
            latency_ms = int(round((perf_counter() - start) * 1000))
            return self._degrade(
                request_id=request_id,
                intent_id=intent_id,
                reason_code=VISION_CAMERA_UNAVAILABLE,
                detail=f"camera recognizer unavailable: {exc}",
                evidence_ref=evidence_value,
                detector_id=CAMERA_DETECTOR_ID,
                objects=[],
                latency_ms=latency_ms,
                ts_ms=now_value,
            )

        latency_ms = int(round((perf_counter() - start) * 1000))
        if latency_ms > timeout_value:
            # 即使 provider 最终返回，越过调用契约的结果也不进入成功路径；迟到视觉
            # 数据可能已与当前交互脱节，必须作为 timeout 降级丢弃。
            return self._degrade(
                request_id=request_id,
                intent_id=intent_id,
                reason_code=VISION_CAMERA_TIMEOUT,
                detail=f"camera recognizer slow: {latency_ms}ms>{timeout_value}ms",
                evidence_ref=evidence_value,
                detector_id=str(detector_id or CAMERA_DETECTOR_ID),
                objects=[],
                latency_ms=latency_ms,
                ts_ms=now_value,
            )

        objects = _normalize_objects(raw_objects, low_conf_threshold=self.low_conf_threshold)
        # 归一化时显式保留低置信标记，后续消费者可选择保守呈现；不能把检测器的
        # 概率阈值在这里静默抹平为确定对象事实。
        low_conf_count = sum(1 for item in objects if item["low_confidence"])
        # M28-M（OPT-35）：从识别结果推断用户情感提示
        user_emotion_hint = _infer_user_emotion_hint(objects)
        output = {
            "request_id": request_id,
            "intent_id": intent_id,
            "ts_ms": now_value,
            "evidence_ref": evidence_value,
            "detector_id": str(detector_id or CAMERA_DETECTOR_ID),
            "objects": objects,
            "status": STATUS_OK,
            "stage": S_VISION_CAMERA,
            "reason_code": None,
            "degraded": False,
            "latency_ms": latency_ms,
            "summary": {
                "object_count": len(objects),
                "low_confidence_count": low_conf_count,
            },
            "quality": {"stage": S_VISION_CAMERA, "reason_code": None},
            # M28-M（OPT-35）：用户视觉情感提示
            "user_emotion_hint": user_emotion_hint,
        }
        self._log_event(
            request_id=request_id,
            intent_id=intent_id,
            status=STATUS_OK,
            reason_code=None,
            evidence_ref=evidence_value,
            detail="camera recognition completed",
            payload={
                "detector_id": output["detector_id"],
                "object_count": len(objects),
                "low_confidence_count": low_conf_count,
                "latency_ms": latency_ms,
            },
            ts_ms=now_value,
        )
        return CameraEventResult(
            success=True,
            status=STATUS_OK,
            reason_code=None,
            stage=S_VISION_CAMERA,
            detail="camera recognition completed",
            output=output,
        )

    def detect_multi(
        self,
        *,
        request_id: str,
        evidence_ref: str,
        frame_data: str,
        intent_id: str | None = None,
        ts_ms: int | None = None,
        aggregator: Any = None,
        detectors: Any = None,
    ) -> CameraEventResult:
        """多检测器聚合检测入口。

        委托 MultiDetectorAggregator 并行执行所有已注册检测器，
        将聚合结果封装为与 detect() 一致的 CameraEventResult 输出结构。
        """
        from host_brain.vision.detector_registry import (
            MultiDetectorAggregator,
            AGG_STATUS_OK,
            AGG_STATUS_PARTIAL,
            AGG_STATUS_ALL_FAILED,
            VISION_DETECTOR_ALL_FAILED,
        )

        now_value = int(ts_ms if ts_ms is not None else now_ms())
        evidence_value = str(evidence_ref or "").strip()

        if not _valid_ref(evidence_value):
            return self._degrade(
                request_id=request_id,
                intent_id=intent_id,
                reason_code=VISION_CAMERA_EVIDENCE_REQUIRED,
                detail="multi-detect requires replayable evidence_ref",
                evidence_ref=evidence_value,
                detector_id="multi",
                objects=[],
                latency_ms=0,
                ts_ms=now_value,
            )

        if aggregator is None or not isinstance(aggregator, MultiDetectorAggregator):
            return self._degrade(
                request_id=request_id,
                intent_id=intent_id,
                reason_code=VISION_CAMERA_UNAVAILABLE,
                detail="no MultiDetectorAggregator provided",
                evidence_ref=evidence_value,
                detector_id="multi",
                objects=[],
                latency_ms=0,
                ts_ms=now_value,
            )

        agg_event = aggregator.run(
            evidence_ref=evidence_value,
            frame_data=str(frame_data or ""),
            detectors=detectors,
        )

        if agg_event.status == AGG_STATUS_ALL_FAILED:
            # 全部检测器失败时输出空 detections 仅表示“没有可信结果”，不是“没有
            # 物体”；状态与失败 detector 列表会被保留以支持后续诊断和安全降级。
            self._log_event(
                request_id=request_id,
                intent_id=intent_id,
                status=STATUS_DEGRADED,
                reason_code=agg_event.reason_code or VISION_DETECTOR_ALL_FAILED,
                evidence_ref=evidence_value,
                detail="all detectors failed",
                payload={
                    "mode": "multi",
                    "total_detectors": agg_event.total_detectors,
                    "ok_detectors": 0,
                    "failed_detectors": agg_event.failed_detectors,
                    "latency_ms": agg_event.latency_ms,
                },
                ts_ms=now_value,
            )
            output = {
                "request_id": request_id,
                "intent_id": intent_id,
                "ts_ms": now_value,
                "evidence_ref": evidence_value,
                "mode": "multi",
                "detections": [],
                "detector_results": agg_event.detector_results,
                "failed_detectors": agg_event.failed_detectors,
                "status": STATUS_DEGRADED,
                "stage": S_VISION_CAMERA,
                "reason_code": agg_event.reason_code,
                "degraded": True,
                "latency_ms": agg_event.latency_ms,
                "summary": {
                    "total_detectors": agg_event.total_detectors,
                    "ok_detectors": 0,
                    "failed_detector_count": agg_event.failed_detector_count,
                    "total_detections": 0,
                    "low_confidence_count": 0,
                },
            }
            return CameraEventResult(
                success=False,
                status=STATUS_DEGRADED,
                reason_code=agg_event.reason_code,
                stage=S_VISION_CAMERA,
                detail="all detectors failed",
                output=output,
            )

        # OK 或 PARTIAL
        is_partial = agg_event.status == AGG_STATUS_PARTIAL
        # 部分成功仍以 DEGRADED 返回，阻止调用方忽略 failed_detectors 后把聚合输出
        # 当作完整覆盖；success 字段因此刻意与“有部分结果”区分。
        status_out = STATUS_DEGRADED if is_partial else STATUS_OK
        success_out = not is_partial

        self._log_event(
            request_id=request_id,
            intent_id=intent_id,
            status=status_out,
            reason_code=None,
            evidence_ref=evidence_value,
            detail=f"multi-detect completed ({agg_event.status})",
            payload={
                "mode": "multi",
                "total_detectors": agg_event.total_detectors,
                "ok_detectors": agg_event.ok_detectors,
                "failed_detector_count": agg_event.failed_detector_count,
                "total_detections": agg_event.total_detections,
                "low_confidence_count": agg_event.low_confidence_count,
                "latency_ms": agg_event.latency_ms,
            },
            ts_ms=now_value,
        )

        output = {
            "request_id": request_id,
            "intent_id": intent_id,
            "ts_ms": now_value,
            "evidence_ref": evidence_value,
            "mode": "multi",
            "detections": agg_event.detections,
            "detector_results": agg_event.detector_results,
            "failed_detectors": agg_event.failed_detectors,
            "status": status_out,
            "stage": S_VISION_CAMERA,
            "reason_code": None,
            "degraded": is_partial,
            "latency_ms": agg_event.latency_ms,
            "summary": {
                "total_detectors": agg_event.total_detectors,
                "ok_detectors": agg_event.ok_detectors,
                "failed_detector_count": agg_event.failed_detector_count,
                "total_detections": agg_event.total_detections,
                "low_confidence_count": agg_event.low_confidence_count,
            },
        }

        return CameraEventResult(
            success=success_out,
            status=status_out,
            reason_code=None,
            stage=S_VISION_CAMERA,
            detail=f"multi-detect completed ({agg_event.status})",
            output=output,
        )

    def _degrade(
        self,
        *,
        request_id: str,
        intent_id: str | None,
        reason_code: str,
        detail: str,
        evidence_ref: str,
        detector_id: str,
        objects: list[dict[str, Any]],
        latency_ms: int,
        ts_ms: int,
    ) -> CameraEventResult:
        low_conf_count = sum(1 for item in objects if item.get("low_confidence"))
        output = {
            "request_id": request_id,
            "intent_id": intent_id,
            "ts_ms": ts_ms,
            "evidence_ref": evidence_ref,
            "detector_id": str(detector_id),
            "objects": list(objects),
            "status": STATUS_DEGRADED,
            "stage": S_VISION_CAMERA,
            "reason_code": reason_code,
            "degraded": True,
            "latency_ms": int(latency_ms),
            "summary": {
                "object_count": len(objects),
                "low_confidence_count": low_conf_count,
            },
            "quality": {"stage": S_VISION_CAMERA, "reason_code": reason_code},
        }
        self._log_event(
            request_id=request_id,
            intent_id=intent_id,
            status=STATUS_DEGRADED,
            reason_code=reason_code,
            evidence_ref=evidence_ref,
            detail=detail,
            payload={
                "detector_id": str(detector_id),
                "object_count": len(objects),
                "low_confidence_count": low_conf_count,
                "latency_ms": int(latency_ms),
            },
            ts_ms=ts_ms,
        )
        return CameraEventResult(
            success=False,
            status=STATUS_DEGRADED,
            reason_code=reason_code,
            stage=S_VISION_CAMERA,
            detail=detail,
            output=output,
        )

    def _log_event(
        self,
        *,
        request_id: str,
        intent_id: str | None,
        status: str,
        reason_code: str | None,
        evidence_ref: str,
        detail: str,
        payload: dict[str, Any],
        ts_ms: int,
    ) -> None:
        if self.ledger is not None and hasattr(self.ledger, "log_vision_event"):
            self.ledger.log_vision_event(
                {
                    "request_id": request_id,
                    "intent_id": intent_id,
                    "event_type": EVENT_CAMERA_RECOGNITION,
                    "status": status,
                    "reason_code": reason_code,
                    "stage": S_VISION_CAMERA,
                    "source_ref": evidence_ref,
                    "payload": payload,
                    "detail": detail,
                    "ts_ms": ts_ms,
                }
            )
            return

        if self.ledger is not None and hasattr(self.ledger, "log_error") and reason_code is not None:
            self.ledger.log_error(
                request_id=request_id,
                stage=S_VISION_CAMERA,
                reason_code=reason_code,
                detail=detail,
            )


def _default_provider(evidence_ref: str, frame_text: str) -> tuple[list[dict[str, Any]], str]:
    text = str(frame_text or "").strip()
    if not text:
        return [], CAMERA_DETECTOR_ID

    objects: list[dict[str, Any]] = []
    rows = [row.strip() for row in text.splitlines() if row.strip()]
    if not rows:
        rows = [text]
    total = max(1, len(rows))
    for idx, row in enumerate(rows):
        if ":" in row:
            label_part, conf_part = row.split(":", 1)
            label = str(label_part).strip() or f"object_{idx + 1}"
            conf = _coerce_unit_float(conf_part.strip(), 0.5)
        else:
            label = row.split(" ", 1)[0] if row else f"object_{idx + 1}"
            conf = _coerce_unit_float(0.8 - 0.2 * idx, 0.5)
        objects.append(
            {
                "label": label,
                "conf": conf,
                "bbox": {
                    "x": 0.0,
                    "y": round(float(idx) / float(total), 6),
                    "w": 0.5,
                    "h": round(1.0 / float(total), 6),
                    "space": BBOX_SPACE,
                },
                "evidence_ref": evidence_ref,
            }
        )
    return objects, CAMERA_DETECTOR_ID


def _normalize_objects(
    raw_objects: list[dict[str, Any]] | Any,
    *,
    low_conf_threshold: float,
) -> list[dict[str, Any]]:
    if not isinstance(raw_objects, list):
        return []
    normalized: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_objects):
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or f"object_{idx + 1}").strip()
        conf = _coerce_unit_float(item.get("conf"), 0.5)
        bbox = item.get("bbox")
        normalized_item = {
            "label": label,
            "conf": conf,
            "low_confidence": bool(conf < low_conf_threshold),
        }
        if isinstance(bbox, dict):
            normalized_item["bbox"] = {
                "x": _coerce_unit_float(bbox.get("x"), 0.0),
                "y": _coerce_unit_float(bbox.get("y"), 0.0),
                "w": _coerce_unit_float(bbox.get("w"), 1.0),
                "h": _coerce_unit_float(bbox.get("h"), 1.0),
                "space": BBOX_SPACE,
            }
        normalized.append(normalized_item)
    return normalized


def _coerce_unit_float(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    if number < 0.0:
        number = 0.0
    if number > 1.0:
        number = 1.0
    return round(number, 6)


def _valid_ref(value: str) -> bool:
    if not value:
        return False
    return re.match(r"^[a-z][a-z0-9+.-]*://.+", value, flags=re.IGNORECASE) is not None


# M28-M（OPT-35）：视觉情感推断映射
_VISUAL_LABEL_TO_EMOTION: dict[str, str] = {
    "smile": "HAPPY",
    "laugh": "EXCITED",
    "cry": "SAD",
    "frown": "SAD",
    "angry": "ANGRY",
    "surprised": "SURPRISED",
    "neutral": "NEUTRAL",
    "thinking": "THINKING",
    "happy": "HAPPY",
    "sad": "SAD",
    "fear": "ANXIOUS",
    "disgust": "FRUSTRATED",
}


def _infer_user_emotion_hint(objects: list[dict[str, Any]]) -> str | None:
    """从识别到的对象标签推断用户情感提示（M28-M OPT-35）。

    Args:
        objects: 归一化后的识别对象列表

    Returns:
        情感标签字符串（如 "HAPPY"），或 None（无法推断时）
    """
    for obj in objects:
        label = str(obj.get("label") or "").strip().lower()
        # 跳过低置信度对象
        if obj.get("low_confidence"):
            continue
        for keyword, emotion in _VISUAL_LABEL_TO_EMOTION.items():
            if keyword in label:
                return emotion
    return None
