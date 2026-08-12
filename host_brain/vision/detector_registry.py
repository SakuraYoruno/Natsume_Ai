"""相机视觉事件的多检测器注册与聚合。

提供检测器注册表（DetectorConfig）、多检测器并行执行与结果聚合（MultiDetectorAggregator），
以及部分检测器不可用时的降级策略。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Callable, Dict, List, Optional, Sequence

from host_brain.core.errors import (
    VISION_CAMERA_UNAVAILABLE,
    VISION_CAMERA_TIMEOUT,
    VISION_DETECTOR_UNAVAILABLE,
    VISION_DETECTOR_TIMEOUT,
    VISION_DETECTOR_ALL_FAILED,
)

# ── 检测器类型常量 ──
DETECTOR_TYPE_FACE = "face"
DETECTOR_TYPE_OBJECT = "object"
DETECTOR_TYPE_GESTURE = "gesture"
DETECTOR_TYPE_SCENE = "scene"

VALID_DETECTOR_TYPES = frozenset({
    DETECTOR_TYPE_FACE,
    DETECTOR_TYPE_OBJECT,
    DETECTOR_TYPE_GESTURE,
    DETECTOR_TYPE_SCENE,
})

# 聚合状态
AGG_STATUS_OK = "OK"
AGG_STATUS_PARTIAL = "PARTIAL"
AGG_STATUS_ALL_FAILED = "ALL_FAILED"

# 检测器执行回调协议：(evidence_ref, frame_data) -> (raw_detections, detector_model_id)
DetectorProviderFn = Callable[[str, str], tuple[list[dict[str, Any]], str]]


@dataclass(frozen=True)
class DetectorConfig:
    """单个检测器的注册配置。"""
    detector_id: str
    type: str  # face / object / gesture / scene
    model_id: str
    confidence_threshold: float = 0.5
    enabled: bool = True
    timeout_ms: int = 500

    def __post_init__(self) -> None:
        if self.type not in VALID_DETECTOR_TYPES:
            raise ValueError(
                f"Invalid detector type '{self.type}'; "
                f"must be one of {sorted(VALID_DETECTOR_TYPES)}"
            )


@dataclass
class DetectionItem:
    """单个检测器的一条检测结果。"""
    detector_id: str
    type: str
    label: str
    confidence: float
    low_confidence: bool
    bbox: Optional[dict[str, Any]] = None
    evidence_ref: str = ""


@dataclass
class DetectorResult:
    """单个检测器的执行结果。"""
    detector_id: str
    type: str
    model_id: str
    success: bool
    reason_code: Optional[str] = None
    detail: str = ""
    detections: list[DetectionItem] = field(default_factory=list)
    latency_ms: int = 0


@dataclass
class AggregatedCameraEvent:
    """多检测器聚合后的统一事件结构。"""
    status: str  # OK / PARTIAL / ALL_FAILED
    detections: list[dict[str, Any]] = field(default_factory=list)
    detector_results: list[dict[str, Any]] = field(default_factory=list)
    failed_detectors: list[dict[str, Any]] = field(default_factory=list)
    total_detectors: int = 0
    ok_detectors: int = 0
    failed_detector_count: int = 0
    total_detections: int = 0
    low_confidence_count: int = 0
    latency_ms: int = 0
    reason_code: Optional[str] = None


class DetectorRegistry:
    """线程安全的检测器注册表。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._detectors: Dict[str, DetectorConfig] = {}

    def register(self, config: DetectorConfig) -> None:
        """注册一个检测器。若 detector_id 已存在则覆盖。"""
        with self._lock:
            self._detectors[config.detector_id] = config

    def unregister(self, detector_id: str) -> bool:
        """取消注册。返回是否存在并删除。"""
        with self._lock:
            return self._detectors.pop(detector_id, None) is not None

    def get(self, detector_id: str) -> Optional[DetectorConfig]:
        """获取单个检测器配置。"""
        with self._lock:
            return self._detectors.get(detector_id)

    def list_enabled(self) -> List[DetectorConfig]:
        # 快照按 ID 稳定排序，使同一注册表状态的聚合顺序、审计输出与回放结果
        # 不受并发注册时序影响。
        """返回所有 enabled=True 的检测器（按 detector_id 排序保证确定性）。"""
        with self._lock:
            return sorted(
                [d for d in self._detectors.values() if d.enabled],
                key=lambda d: d.detector_id,
            )

    def list_all(self) -> List[DetectorConfig]:
        """返回所有检测器（按 detector_id 排序保证确定性）。"""
        with self._lock:
            return sorted(self._detectors.values(), key=lambda d: d.detector_id)

    def count(self) -> int:
        with self._lock:
            return len(self._detectors)

    def count_enabled(self) -> int:
        with self._lock:
            return sum(1 for d in self._detectors.values() if d.enabled)

    def clear(self) -> None:
        with self._lock:
            self._detectors.clear()

    def to_snapshot(self) -> list[dict[str, Any]]:
        """输出注册表快照（用于审计/调试）。"""
        with self._lock:
            return [
                {
                    "detector_id": d.detector_id,
                    "type": d.type,
                    "model_id": d.model_id,
                    "confidence_threshold": d.confidence_threshold,
                    "enabled": d.enabled,
                    "timeout_ms": d.timeout_ms,
                }
                for d in sorted(self._detectors.values(), key=lambda d: d.detector_id)
            ]


class MultiDetectorAggregator:
    """多检测器并行执行与结果聚合。

    - 每个检测器独立输出置信度与 evidence_ref；
    - 可用检测器正常输出，不可用检测器标注 reason_code；
    - 全部不可用时输出 ALL_FAILED + VISION_DETECTOR_ALL_FAILED。
    """

    def __init__(
        self,
        *,
        registry: DetectorRegistry,
        providers: Optional[Dict[str, DetectorProviderFn]] = None,
    ) -> None:
        self.registry = registry
        self._providers: Dict[str, DetectorProviderFn] = dict(providers or {})

    def set_provider(self, detector_id: str, provider: DetectorProviderFn) -> None:
        """为指定检测器设置执行回调。"""
        self._providers[detector_id] = provider

    def remove_provider(self, detector_id: str) -> None:
        """移除指定检测器的执行回调。"""
        self._providers.pop(detector_id, None)

    def run(
        self,
        *,
        evidence_ref: str,
        frame_data: str,
        detectors: Optional[Sequence[str]] = None,
    ) -> AggregatedCameraEvent:
        # 每个 detector 独立失败并保留原因；只有全部不可用才返回 ALL_FAILED，
        # 部分成功仍携带 evidence_ref 供上游按置信度决定是否采用。
        """对所有（或指定的）已启用检测器执行检测并聚合结果。

        Args:
            evidence_ref: 帧证据引用 URI
            frame_data: 帧数据（文本描述或编码引用）
            detectors: 若指定则只运行这些 detector_id；否则运行所有 enabled 检测器
        """
        enabled = self.registry.list_enabled()

        if detectors is not None:
            detector_ids_set = set(detectors)
            enabled = [d for d in enabled if d.detector_id in detector_ids_set]

        if not enabled:
            return AggregatedCameraEvent(
                status=AGG_STATUS_ALL_FAILED,
                reason_code=VISION_DETECTOR_ALL_FAILED,
                total_detectors=0,
                ok_detectors=0,
                failed_detector_count=0,
            )

        results: list[DetectorResult] = []
        start_all = perf_counter()

        for config in enabled:
            result = self._run_single(config, evidence_ref, frame_data)
            results.append(result)

        total_latency = int(round((perf_counter() - start_all) * 1000))
        return self._aggregate(results, total_latency)

    def _run_single(
        self,
        config: DetectorConfig,
        evidence_ref: str,
        frame_data: str,
    ) -> DetectorResult:
        # 超时后的迟到输出不得计入事件，避免低延迟消费方将过期帧误作当前感知。
        """执行单个检测器。"""
        provider = self._providers.get(config.detector_id)

        if provider is None:
            return DetectorResult(
                detector_id=config.detector_id,
                type=config.type,
                model_id=config.model_id,
                success=False,
                reason_code=VISION_DETECTOR_UNAVAILABLE,
                detail=f"no provider registered for detector '{config.detector_id}'",
                latency_ms=0,
            )

        start = perf_counter()
        try:
            raw_detections, model_id_out = provider(evidence_ref, frame_data)
        except TimeoutError:
            latency_ms = int(round((perf_counter() - start) * 1000))
            return DetectorResult(
                detector_id=config.detector_id,
                type=config.type,
                model_id=config.model_id,
                success=False,
                reason_code=VISION_DETECTOR_TIMEOUT,
                detail=f"detector '{config.detector_id}' timeout",
                latency_ms=latency_ms,
            )
        except Exception as exc:
            latency_ms = int(round((perf_counter() - start) * 1000))
            return DetectorResult(
                detector_id=config.detector_id,
                type=config.type,
                model_id=config.model_id,
                success=False,
                reason_code=VISION_DETECTOR_UNAVAILABLE,
                detail=f"detector '{config.detector_id}' failed: {exc}",
                latency_ms=latency_ms,
            )

        latency_ms = int(round((perf_counter() - start) * 1000))

        if latency_ms > config.timeout_ms:
            return DetectorResult(
                detector_id=config.detector_id,
                type=config.type,
                model_id=config.model_id,
                success=False,
                reason_code=VISION_DETECTOR_TIMEOUT,
                detail=f"detector '{config.detector_id}' slow: {latency_ms}ms>{config.timeout_ms}ms",
                latency_ms=latency_ms,
            )

        items = _normalize_detections(
            raw_detections,
            detector_id=config.detector_id,
            detector_type=config.type,
            evidence_ref=evidence_ref,
            confidence_threshold=config.confidence_threshold,
        )

        return DetectorResult(
            detector_id=config.detector_id,
            type=config.type,
            model_id=model_id_out or config.model_id,
            success=True,
            detections=items,
            latency_ms=latency_ms,
        )

    def _aggregate(
        self,
        results: list[DetectorResult],
        total_latency: int,
    ) -> AggregatedCameraEvent:
        # 聚合不合并不同 detector 的语义判断，只汇总来源、置信度与失败信息；
        # low_confidence 明确留给上游策略处理，而不是在这里伪造确定结论。
        """将各检测器结果聚合为统一事件结构。"""
        ok_results = [r for r in results if r.success]
        failed_results = [r for r in results if not r.success]

        all_detections: list[dict[str, Any]] = []
        detector_summaries: list[dict[str, Any]] = []
        failed_summaries: list[dict[str, Any]] = []

        for r in results:
            summary: dict[str, Any] = {
                "detector_id": r.detector_id,
                "type": r.type,
                "model_id": r.model_id,
                "success": r.success,
                "reason_code": r.reason_code,
                "detection_count": len(r.detections),
                "latency_ms": r.latency_ms,
            }
            detector_summaries.append(summary)

            if r.success:
                for item in r.detections:
                    detection_dict: dict[str, Any] = {
                        "detector_id": item.detector_id,
                        "type": item.type,
                        "label": item.label,
                        "confidence": item.confidence,
                        "low_confidence": item.low_confidence,
                        "evidence_ref": item.evidence_ref,
                    }
                    if item.bbox is not None:
                        detection_dict["bbox"] = item.bbox
                    all_detections.append(detection_dict)
            else:
                failed_summaries.append({
                    "detector_id": r.detector_id,
                    "type": r.type,
                    "reason_code": r.reason_code,
                    "detail": r.detail,
                })

        low_conf = sum(1 for d in all_detections if d.get("low_confidence"))

        if len(ok_results) == 0:
            status = AGG_STATUS_ALL_FAILED
            reason_code: Optional[str] = VISION_DETECTOR_ALL_FAILED
        elif len(failed_results) > 0:
            status = AGG_STATUS_PARTIAL
            reason_code = None
        else:
            status = AGG_STATUS_OK
            reason_code = None

        return AggregatedCameraEvent(
            status=status,
            detections=all_detections,
            detector_results=detector_summaries,
            failed_detectors=failed_summaries,
            total_detectors=len(results),
            ok_detectors=len(ok_results),
            failed_detector_count=len(failed_results),
            total_detections=len(all_detections),
            low_confidence_count=low_conf,
            latency_ms=total_latency,
            reason_code=reason_code,
        )


# ── 内部工具函数 ──

def _normalize_detections(
    raw: list[dict[str, Any]] | Any,
    *,
    detector_id: str,
    detector_type: str,
    evidence_ref: str,
    confidence_threshold: float,
) -> list[DetectionItem]:
    """将原始检测输出归一化为 DetectionItem 列表。"""
    if not isinstance(raw, list):
        return []

    items: list[DetectionItem] = []
    for idx, entry in enumerate(raw):
        if not isinstance(entry, dict):
            continue
        label = str(entry.get("label") or f"{detector_type}_{idx + 1}").strip()
        conf = _coerce_unit_float(entry.get("conf"), 0.5)
        bbox = entry.get("bbox")
        bbox_dict: Optional[dict[str, Any]] = None
        if isinstance(bbox, dict):
            bbox_dict = {
                "x": _coerce_unit_float(bbox.get("x"), 0.0),
                "y": _coerce_unit_float(bbox.get("y"), 0.0),
                "w": _coerce_unit_float(bbox.get("w"), 1.0),
                "h": _coerce_unit_float(bbox.get("h"), 1.0),
                "space": "xywh_norm_v1",
            }

        items.append(DetectionItem(
            detector_id=detector_id,
            type=detector_type,
            label=label,
            confidence=conf,
            low_confidence=bool(conf < confidence_threshold),
            bbox=bbox_dict,
            evidence_ref=evidence_ref,
        ))

    return items


def _coerce_unit_float(value: Any, default: float) -> float:
    """将值强制转换为 [0.0, 1.0] 的浮点数。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    if number < 0.0:
        number = 0.0
    if number > 1.0:
        number = 1.0
    return round(number, 6)
