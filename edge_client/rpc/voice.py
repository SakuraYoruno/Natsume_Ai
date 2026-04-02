"""Edge voice RPC contracts for ASR, TTS speak, and barge-in events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    VOICE_LOW_CONFIDENCE,
    VOICE_OUT_OF_ORDER,
    VOICE_SCHEMA_INVALID,
    VOICE_THROTTLED,
)
from host_brain.core.ids import now_ms
from edge_client.rpc.resource_gate import TASK_ASR_INGRESS, VoiceResourceGate


EVENT_ASR_CHUNK = "ASR_CHUNK"
EVENT_SPEAK = "SPEAK"
EVENT_BARGE_IN = "BARGE_IN"

DIRECTION_EDGE_TO_HOST = "EDGE_TO_HOST"
DIRECTION_HOST_TO_EDGE = "HOST_TO_EDGE"

STATUS_ACCEPTED = "ACCEPTED"
STATUS_REJECTED = "REJECTED"

S_VOICE_ASR_INGRESS = "S_VOICE_ASR_INGRESS"
S_VOICE_SPEAK_EGRESS = "S_VOICE_SPEAK_EGRESS"
S_VOICE_BARGE_IN = "S_VOICE_BARGE_IN"
_JAPANESE_TTS_LANGUAGE_ALIASES = frozenset({"ja", "jp", "ja-jp"})


@dataclass(frozen=True)
class VoiceRpcResult:
    success: bool
    status: str
    reason_code: str | None
    stage: str
    detail: str
    event: dict[str, Any]


class VoiceRpcContract:
    """Deterministic voice RPC schema validation and event logging."""

    def __init__(
        self,
        *,
        ledger: SQLiteLedger | None = None,
        resource_gate: VoiceResourceGate | None = None,
        asr_min_confidence: float = 0.60,
        asr_min_interval_ms: int = 120,
    ) -> None:
        self.ledger = ledger
        self.resource_gate = resource_gate
        self.asr_min_confidence = float(asr_min_confidence)
        self.asr_min_interval_ms = int(asr_min_interval_ms)
        self._last_asr_seq: dict[str, int] = {}
        self._last_asr_ts_ms: dict[str, int] = {}

    def asr_chunk(
        self,
        *,
        request_id: str,
        payload: dict[str, Any],
        ts_ms: int | None = None,
    ) -> VoiceRpcResult:
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        session_id = str(payload.get("session_id") or "").strip()
        text = str(payload.get("text") or "").strip()
        seq = payload.get("seq")
        confidence = payload.get("confidence")

        if not session_id or not text or not isinstance(seq, int) or not isinstance(confidence, (int, float)):
            return self._reject(
                request_id=request_id,
                session_id=session_id,
                event_type=EVENT_ASR_CHUNK,
                direction=DIRECTION_EDGE_TO_HOST,
                seq=int(seq) if isinstance(seq, int) else None,
                stage=S_VOICE_ASR_INGRESS,
                reason_code=VOICE_SCHEMA_INVALID,
                detail="asr_chunk requires session_id/text/seq/confidence",
                payload=payload,
                ts_ms=now_value,
            )

        conf_value = float(confidence)
        is_low_confidence = conf_value < self.asr_min_confidence
        emotion_tag = str(payload.get("emotion_tag") or "").strip().upper() or None
        emotion_intensity_raw = payload.get("emotion_intensity")
        emotion_intensity: float | None = None
        if emotion_intensity_raw is not None:
            try:
                emotion_intensity = max(0.0, min(1.0, float(emotion_intensity_raw)))
            except (TypeError, ValueError):
                emotion_intensity = None
        last_seq = self._last_asr_seq.get(session_id)
        if last_seq is not None and int(seq) <= int(last_seq):
            return self._reject(
                request_id=request_id,
                session_id=session_id,
                event_type=EVENT_ASR_CHUNK,
                direction=DIRECTION_EDGE_TO_HOST,
                seq=int(seq),
                stage=S_VOICE_ASR_INGRESS,
                reason_code=VOICE_OUT_OF_ORDER,
                detail=f"seq out of order: got {seq}, last {last_seq}",
                payload=payload,
                ts_ms=now_value,
            )

        last_ts = self._last_asr_ts_ms.get(session_id)
        if last_ts is not None and (now_value - int(last_ts)) < self.asr_min_interval_ms:
            return self._reject(
                request_id=request_id,
                session_id=session_id,
                event_type=EVENT_ASR_CHUNK,
                direction=DIRECTION_EDGE_TO_HOST,
                seq=int(seq),
                stage=S_VOICE_ASR_INGRESS,
                reason_code=VOICE_THROTTLED,
                detail=f"asr event throttled: interval<{self.asr_min_interval_ms}ms",
                payload=payload,
                ts_ms=now_value,
            )

        if self.resource_gate is not None:
            gate_decision = self.resource_gate.request(
                request_id=request_id,
                task=TASK_ASR_INGRESS,
                session_id=session_id,
                metrics={
                    "vram_total_mb": payload.get("vram_total_mb", 0),
                    "vram_used_mb": payload.get("vram_used_mb", 0),
                    "queue_depth": payload.get("queue_depth", 0),
                },
                hold=False,
                ts_ms=now_value,
            )
            if not gate_decision.admitted:
                return self._reject(
                    request_id=request_id,
                    session_id=session_id,
                    event_type=EVENT_ASR_CHUNK,
                    direction=DIRECTION_EDGE_TO_HOST,
                    seq=int(seq),
                    stage=gate_decision.stage,
                    reason_code=str(gate_decision.reason_code or VOICE_THROTTLED),
                    detail=f"asr blocked by resource gate: {gate_decision.detail}",
                    payload=payload,
                    ts_ms=now_value,
                )

        event = {
            "session_id": session_id,
            "event_type": EVENT_ASR_CHUNK,
            "direction": DIRECTION_EDGE_TO_HOST,
            "seq": int(seq),
            "text": text,
            "confidence": conf_value,
            "low_confidence": is_low_confidence,
            "alternates": _normalize_alternates(payload.get("alternates")),
            "final": bool(payload.get("final", False)),
            "ts_ms": now_value,
        }
        if emotion_tag is not None:
            event["emotion_tag"] = emotion_tag
        if emotion_intensity is not None:
            event["emotion_intensity"] = emotion_intensity
        self._last_asr_seq[session_id] = int(seq)
        self._last_asr_ts_ms[session_id] = now_value
        return self._accept(
            request_id=request_id,
            session_id=session_id,
            event_type=EVENT_ASR_CHUNK,
            direction=DIRECTION_EDGE_TO_HOST,
            seq=int(seq),
            stage=S_VOICE_ASR_INGRESS,
            reason_code=VOICE_LOW_CONFIDENCE if is_low_confidence else None,
            detail=(
                f"asr_chunk accepted with low confidence: {conf_value:.3f} < {self.asr_min_confidence:.2f}"
                if is_low_confidence
                else "asr_chunk accepted"
            ),
            event=event,
            ts_ms=now_value,
        )

    def speak(
        self,
        *,
        request_id: str,
        payload: dict[str, Any],
        ts_ms: int | None = None,
    ) -> VoiceRpcResult:
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        session_id = str(payload.get("session_id") or "").strip()
        utterance_id = str(payload.get("utterance_id") or "").strip()
        text = str(payload.get("text") or "").strip()
        seq = payload.get("seq")

        if not session_id or not utterance_id or not text:
            return self._reject(
                request_id=request_id,
                session_id=session_id,
                event_type=EVENT_SPEAK,
                direction=DIRECTION_HOST_TO_EDGE,
                seq=int(seq) if isinstance(seq, int) else None,
                stage=S_VOICE_SPEAK_EGRESS,
                reason_code=VOICE_SCHEMA_INVALID,
                detail="speak requires session_id/utterance_id/text",
                payload=payload,
                ts_ms=now_value,
            )

        lang_hint = _normalize_tts_lang_hint(payload.get("lang_hint"))
        if lang_hint is None:
            return self._reject(
                request_id=request_id,
                session_id=session_id,
                event_type=EVENT_SPEAK,
                direction=DIRECTION_HOST_TO_EDGE,
                seq=int(seq) if isinstance(seq, int) else None,
                stage=S_VOICE_SPEAK_EGRESS,
                reason_code=VOICE_SCHEMA_INVALID,
                detail="speak lang_hint must be ja when provided",
                payload=payload,
                ts_ms=now_value,
            )

        raw_intensity = payload.get("emotion_intensity")
        emotion_intensity: float | None = None
        if raw_intensity is not None:
            try:
                emotion_intensity = max(0.0, min(1.0, float(raw_intensity)))
            except (TypeError, ValueError):
                pass
        event = {
            "session_id": session_id,
            "event_type": EVENT_SPEAK,
            "direction": DIRECTION_HOST_TO_EDGE,
            "seq": int(seq) if isinstance(seq, int) else None,
            "utterance_id": utterance_id,
            "text": text,
            "emotion_tag": str(payload.get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL",
            "interruptible": bool(payload.get("interruptible", True)),
            "voice_profile": str(payload.get("voice_profile") or "default"),
            "lang_hint": lang_hint,
            "ts_ms": now_value,
        }
        if emotion_intensity is not None:
            event["emotion_intensity"] = emotion_intensity
        return self._accept(
            request_id=request_id,
            session_id=session_id,
            event_type=EVENT_SPEAK,
            direction=DIRECTION_HOST_TO_EDGE,
            seq=event["seq"],
            stage=S_VOICE_SPEAK_EGRESS,
            detail="speak accepted",
            event=event,
            ts_ms=now_value,
        )

    def barge_in(
        self,
        *,
        request_id: str,
        payload: dict[str, Any],
        ts_ms: int | None = None,
    ) -> VoiceRpcResult:
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        session_id = str(payload.get("session_id") or "").strip()
        source = str(payload.get("source") or "").strip()
        seq = payload.get("seq")

        if not session_id or not source:
            return self._reject(
                request_id=request_id,
                session_id=session_id,
                event_type=EVENT_BARGE_IN,
                direction=DIRECTION_EDGE_TO_HOST,
                seq=int(seq) if isinstance(seq, int) else None,
                stage=S_VOICE_BARGE_IN,
                reason_code=VOICE_SCHEMA_INVALID,
                detail="barge_in requires session_id/source",
                payload=payload,
                ts_ms=now_value,
            )

        event = {
            "session_id": session_id,
            "event_type": EVENT_BARGE_IN,
            "direction": DIRECTION_EDGE_TO_HOST,
            "seq": int(seq) if isinstance(seq, int) else None,
            "source": source,
            "active_utterance_id": str(payload.get("active_utterance_id") or ""),
            "ts_ms": now_value,
        }
        return self._accept(
            request_id=request_id,
            session_id=session_id,
            event_type=EVENT_BARGE_IN,
            direction=DIRECTION_EDGE_TO_HOST,
            seq=event["seq"],
            stage=S_VOICE_BARGE_IN,
            detail="barge_in accepted",
            event=event,
            ts_ms=now_value,
        )

    def _accept(
        self,
        *,
        request_id: str,
        session_id: str,
        event_type: str,
        direction: str,
        seq: int | None,
        stage: str,
        reason_code: str | None = None,
        detail: str,
        event: dict[str, Any],
        ts_ms: int,
    ) -> VoiceRpcResult:
        self._log_event(
            request_id=request_id,
            session_id=session_id,
            event_type=event_type,
            direction=direction,
            seq=seq,
            status=STATUS_ACCEPTED,
            reason_code=reason_code,
            stage=stage,
            detail=detail,
            payload=event,
            ts_ms=ts_ms,
        )
        return VoiceRpcResult(
            success=True,
            status=STATUS_ACCEPTED,
            reason_code=reason_code,
            stage=stage,
            detail=detail,
            event=event,
        )

    def _reject(
        self,
        *,
        request_id: str,
        session_id: str,
        event_type: str,
        direction: str,
        seq: int | None,
        stage: str,
        reason_code: str,
        detail: str,
        payload: dict[str, Any],
        ts_ms: int,
    ) -> VoiceRpcResult:
        event = {
            "session_id": session_id,
            "event_type": event_type,
            "direction": direction,
            "seq": seq,
            "ts_ms": ts_ms,
        }
        self._log_event(
            request_id=request_id,
            session_id=session_id,
            event_type=event_type,
            direction=direction,
            seq=seq,
            status=STATUS_REJECTED,
            reason_code=reason_code,
            stage=stage,
            detail=detail,
            payload=payload,
            ts_ms=ts_ms,
        )
        return VoiceRpcResult(
            success=False,
            status=STATUS_REJECTED,
            reason_code=reason_code,
            stage=stage,
            detail=detail,
            event=event,
        )

    def _log_event(
        self,
        *,
        request_id: str,
        session_id: str,
        event_type: str,
        direction: str,
        seq: int | None,
        status: str,
        reason_code: str | None,
        stage: str,
        detail: str,
        payload: dict[str, Any],
        ts_ms: int,
    ) -> None:
        if self.ledger is None:
            return
        self.ledger.log_voice_event(
            {
                "request_id": request_id,
                "session_id": session_id,
                "event_type": event_type,
                "direction": direction,
                "seq": seq,
                "status": status,
                "reason_code": reason_code,
                "stage": stage,
                "payload": payload,
                "detail": detail,
                "ts_ms": ts_ms,
            }
        )


def _normalize_alternates(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        conf = item.get("confidence")
        if not text or not isinstance(conf, (int, float)):
            continue
        out.append({"text": text, "confidence": float(conf)})
    return out


def _normalize_tts_lang_hint(value: Any) -> str | None:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return "ja"
    if normalized in _JAPANESE_TTS_LANGUAGE_ALIASES:
        return "ja"
    return None
