"""Screen OCR contract with Gaming gate and auditable structured output."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
import re
from typing import Any, Callable

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    VISION_GATE_GAMING_BLOCKED,
    VISION_OCR_INPUT_INVALID,
    VISION_OCR_TIMEOUT,
    VISION_OCR_UNAVAILABLE,
)
from host_brain.core.ids import now_ms


EVENT_SCREEN_OCR = "SCREEN_OCR"

S_VISION_GATE = "S_VISION_GATE"
S_VISION_OCR = "S_VISION_OCR"

STATUS_OK = "OK"
STATUS_DEGRADED = "DEGRADED"

OCR_ENGINE_ID = "ocr-heuristic-v1"
BBOX_SPACE = "xywh_norm_v1"

OcrProvider = Callable[[str, str], tuple[list[dict[str, Any]], str]]


@dataclass(frozen=True)
class ScreenOcrResult:
    success: bool
    status: str
    reason_code: str | None
    stage: str
    detail: str
    output: dict[str, Any]


class ScreenOcrServiceContract:
    """Host screen OCR service with deterministic gate/degrade behavior."""

    def __init__(
        self,
        *,
        ledger: SQLiteLedger | None = None,
        provider: OcrProvider | None = None,
        available: bool = True,
        timeout_ms: int = 500,
    ) -> None:
        self.ledger = ledger
        self.provider = provider or _default_provider
        self.available = bool(available)
        self.timeout_ms = int(timeout_ms)

    def recognize(
        self,
        *,
        request_id: str,
        source_ref: str,
        frame_text: str,
        sys_mode: str = "normal",
        intent_id: str | None = None,
        trigger: str = "on_demand",
        timeout_ms: int | None = None,
        ts_ms: int | None = None,
    ) -> ScreenOcrResult:
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        timeout_value = int(timeout_ms if timeout_ms is not None else self.timeout_ms)
        mode = str(sys_mode or "").strip().lower() or "normal"
        source_value = str(source_ref or "").strip()
        trigger_value = str(trigger or "").strip() or "on_demand"

        if mode == "gaming":
            return self._degrade(
                request_id=request_id,
                intent_id=intent_id,
                stage=S_VISION_GATE,
                reason_code=VISION_GATE_GAMING_BLOCKED,
                detail="screen OCR blocked in gaming mode",
                source_ref=source_value,
                engine_id=OCR_ENGINE_ID,
                text_blocks=[],
                trigger=trigger_value,
                sys_mode=mode,
                latency_ms=0,
                ts_ms=now_value,
            )

        if not _valid_source_ref(source_value):
            return self._degrade(
                request_id=request_id,
                intent_id=intent_id,
                stage=S_VISION_OCR,
                reason_code=VISION_OCR_INPUT_INVALID,
                detail="invalid source_ref: expected replayable URI",
                source_ref=source_value,
                engine_id=OCR_ENGINE_ID,
                text_blocks=[],
                trigger=trigger_value,
                sys_mode=mode,
                latency_ms=0,
                ts_ms=now_value,
            )

        if not self.available:
            return self._degrade(
                request_id=request_id,
                intent_id=intent_id,
                stage=S_VISION_OCR,
                reason_code=VISION_OCR_UNAVAILABLE,
                detail="ocr engine unavailable; output degraded",
                source_ref=source_value,
                engine_id=OCR_ENGINE_ID,
                text_blocks=[],
                trigger=trigger_value,
                sys_mode=mode,
                latency_ms=0,
                ts_ms=now_value,
            )

        start = perf_counter()
        try:
            raw_blocks, engine_id = self.provider(source_value, str(frame_text or ""))
        except TimeoutError:
            latency_ms = int(round((perf_counter() - start) * 1000))
            return self._degrade(
                request_id=request_id,
                intent_id=intent_id,
                stage=S_VISION_OCR,
                reason_code=VISION_OCR_TIMEOUT,
                detail=f"ocr timeout>{timeout_value}ms",
                source_ref=source_value,
                engine_id=OCR_ENGINE_ID,
                text_blocks=[],
                trigger=trigger_value,
                sys_mode=mode,
                latency_ms=latency_ms,
                ts_ms=now_value,
            )
        except Exception as exc:
            latency_ms = int(round((perf_counter() - start) * 1000))
            return self._degrade(
                request_id=request_id,
                intent_id=intent_id,
                stage=S_VISION_OCR,
                reason_code=VISION_OCR_UNAVAILABLE,
                detail=f"ocr engine unavailable: {exc}",
                source_ref=source_value,
                engine_id=OCR_ENGINE_ID,
                text_blocks=[],
                trigger=trigger_value,
                sys_mode=mode,
                latency_ms=latency_ms,
                ts_ms=now_value,
            )

        latency_ms = int(round((perf_counter() - start) * 1000))
        if latency_ms > timeout_value:
            return self._degrade(
                request_id=request_id,
                intent_id=intent_id,
                stage=S_VISION_OCR,
                reason_code=VISION_OCR_TIMEOUT,
                detail=f"ocr slow: {latency_ms}ms>{timeout_value}ms",
                source_ref=source_value,
                engine_id=str(engine_id or OCR_ENGINE_ID),
                text_blocks=[],
                trigger=trigger_value,
                sys_mode=mode,
                latency_ms=latency_ms,
                ts_ms=now_value,
            )

        blocks = _normalize_blocks(raw_blocks)
        output = {
            "request_id": request_id,
            "intent_id": intent_id,
            "ts_ms": now_value,
            "source_ref": source_value,
            "engine_id": str(engine_id or OCR_ENGINE_ID),
            "bbox_space": BBOX_SPACE,
            "text_blocks": blocks,
            "status": STATUS_OK,
            "stage": S_VISION_OCR,
            "reason_code": None,
            "degraded": False,
            "latency_ms": latency_ms,
            "quality": {"stage": S_VISION_OCR, "reason_code": None},
        }
        self._log_event(
            request_id=request_id,
            intent_id=intent_id,
            status=STATUS_OK,
            reason_code=None,
            stage=S_VISION_OCR,
            source_ref=source_value,
            detail="ocr completed",
            payload={
                "engine_id": output["engine_id"],
                "sys_mode": mode,
                "trigger": trigger_value,
                "text_block_count": len(blocks),
                "latency_ms": latency_ms,
                "bbox_space": BBOX_SPACE,
            },
            ts_ms=now_value,
        )
        return ScreenOcrResult(
            success=True,
            status=STATUS_OK,
            reason_code=None,
            stage=S_VISION_OCR,
            detail="ocr completed",
            output=output,
        )

    def _degrade(
        self,
        *,
        request_id: str,
        intent_id: str | None,
        stage: str,
        reason_code: str,
        detail: str,
        source_ref: str,
        engine_id: str,
        text_blocks: list[dict[str, Any]],
        trigger: str,
        sys_mode: str,
        latency_ms: int,
        ts_ms: int,
    ) -> ScreenOcrResult:
        output = {
            "request_id": request_id,
            "intent_id": intent_id,
            "ts_ms": ts_ms,
            "source_ref": source_ref,
            "engine_id": str(engine_id),
            "bbox_space": BBOX_SPACE,
            "text_blocks": list(text_blocks),
            "status": STATUS_DEGRADED,
            "stage": stage,
            "reason_code": reason_code,
            "degraded": True,
            "latency_ms": int(latency_ms),
            "quality": {"stage": stage, "reason_code": reason_code},
        }
        self._log_event(
            request_id=request_id,
            intent_id=intent_id,
            status=STATUS_DEGRADED,
            reason_code=reason_code,
            stage=stage,
            source_ref=source_ref,
            detail=detail,
            payload={
                "engine_id": str(engine_id),
                "sys_mode": sys_mode,
                "trigger": trigger,
                "text_block_count": len(text_blocks),
                "latency_ms": int(latency_ms),
                "bbox_space": BBOX_SPACE,
            },
            ts_ms=ts_ms,
        )
        return ScreenOcrResult(
            success=False,
            status=STATUS_DEGRADED,
            reason_code=reason_code,
            stage=stage,
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
        stage: str,
        source_ref: str,
        detail: str,
        payload: dict[str, Any],
        ts_ms: int,
    ) -> None:
        if self.ledger is not None and hasattr(self.ledger, "log_vision_event"):
            self.ledger.log_vision_event(
                {
                    "request_id": request_id,
                    "intent_id": intent_id,
                    "event_type": EVENT_SCREEN_OCR,
                    "status": status,
                    "reason_code": reason_code,
                    "stage": stage,
                    "source_ref": source_ref,
                    "payload": payload,
                    "detail": detail,
                    "ts_ms": ts_ms,
                }
            )
            return

        if self.ledger is not None and hasattr(self.ledger, "log_error") and reason_code is not None:
            self.ledger.log_error(
                request_id=request_id,
                stage=stage,
                reason_code=reason_code,
                detail=detail,
            )


def _default_provider(source_ref: str, frame_text: str) -> tuple[list[dict[str, Any]], str]:
    normalized = str(frame_text or "").strip()
    if not normalized:
        return [], OCR_ENGINE_ID
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    if not lines:
        lines = [normalized]
    count = max(1, len(lines))
    blocks: list[dict[str, Any]] = []
    for idx, line in enumerate(lines):
        block_h = round(1.0 / float(count), 6)
        blocks.append(
            {
                "text": line,
                "bbox": {
                    "x": 0.0,
                    "y": round(block_h * idx, 6),
                    "w": 1.0,
                    "h": block_h,
                    "space": BBOX_SPACE,
                },
                "conf": round(max(0.5, 0.95 - 0.05 * idx), 6),
                "source_ref": source_ref,
            }
        )
    return blocks, OCR_ENGINE_ID


def _normalize_blocks(raw_blocks: list[dict[str, Any]] | Any) -> list[dict[str, Any]]:
    if not isinstance(raw_blocks, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in raw_blocks:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        bbox = item.get("bbox")
        if not isinstance(bbox, dict):
            bbox = {}
        normalized.append(
            {
                "text": text,
                "bbox": {
                    "x": _coerce_unit_float(bbox.get("x"), 0.0),
                    "y": _coerce_unit_float(bbox.get("y"), 0.0),
                    "w": _coerce_unit_float(bbox.get("w"), 1.0),
                    "h": _coerce_unit_float(bbox.get("h"), 1.0),
                    "space": BBOX_SPACE,
                },
                "conf": _coerce_unit_float(item.get("conf"), 0.5),
            }
        )
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


def _valid_source_ref(source_ref: str) -> bool:
    if not source_ref:
        return False
    return re.match(r"^[a-z][a-z0-9+.-]*://.+", source_ref, flags=re.IGNORECASE) is not None
