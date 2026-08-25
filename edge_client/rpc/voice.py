"""Edge voice RPC contracts for ASR, TTS speak, and barge-in events."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from natsume_contracts.edge.v1 import (
    EdgeVoiceEventLedger,
    VOICE_LOW_CONFIDENCE,
    VOICE_OUT_OF_ORDER,
    VOICE_SCHEMA_INVALID,
    VOICE_THROTTLED,
    normalize_emotion_tag,
    now_ms,
)
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
_TTS_LANGUAGE_ALIASES = {
    "zh": "zh",
    "cn": "zh",
    "zh-cn": "zh",
    "zh-hans": "zh",
    "mandarin": "zh",
    "ja": "ja",
    "jp": "ja",
    "ja-jp": "ja",
    "auto": "auto",
}


def _normalize_emotion_tag(value: Any) -> str:
    if value is None or not str(value).strip():
        return ""
    result = normalize_emotion_tag(value)
    return result


def _default_speak_emotion_tag() -> str:
    return _normalize_emotion_tag(os.environ.get("EDGE_TTS_DEFAULT_EMOTION_TAG")) or "NEUTRAL"


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
        ledger: EdgeVoiceEventLedger | None = None,
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
        # ASR 是 Edge→Host 的观测事件，不是最终事实写入；先固定收据时间与会话键，
        # 后续排序、节流和账本记录都据此保持可重放。
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        session_id = str(payload.get("session_id") or "").strip()
        text = str(payload.get("text") or "").strip()
        seq = payload.get("seq")
        confidence = payload.get("confidence")

        if not session_id or not text or not isinstance(seq, int) or not isinstance(confidence, (int, float)):
            # 不为缺少关联键的语音片段补猜默认值：那会把跨会话音频混入同一对话链路。
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

        # 低置信度仍可作为候选上报，但用 reason_code 标注，而不是静默丢弃；Host 可据此
        # 追问或采用 alternates，同时保留端侧实际识别到的输入证据。
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
            # 每个会话的 seq 必须严格递增，拒绝重复/乱序包以维持幂等的增量消费，
            # 不能由较旧音频覆盖已经接纳的较新转写。
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
            # 节流按接收时间而非客户端时间实施，防止高频局部结果压垮 Host 与账本；
            # 被拒绝帧不会推进水位，后续合规序列仍能恢复。
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
            # ASR 入口和本地其他实时任务共享资源门。这里只申请瞬时配额，避免一段音频
            # 处理后遗留租约并影响下一轮播报或具身执行。
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

        # 只构造经过类型/范围归一化的事件载荷；原始请求中的未知字段不穿透 RPC 边界。
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
        # 只在全部校验及资源门放行后推进水位；被限流或拒绝的片段不能占用序号，
        # 否则发送端无法以相同 seq 安全重试其尚未被接纳的事件。
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
        # speak 是 Host→Edge 的播放意图收据，而非此处直接合成/播放；必须以
        # utterance_id 区分同一会话内可中断的多条输出。
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        session_id = str(payload.get("session_id") or "").strip()
        utterance_id = str(payload.get("utterance_id") or "").strip()
        text = str(payload.get("text") or "").strip()
        seq = payload.get("seq")

        if not session_id or not utterance_id or not text:
            # 不接受匿名或空文本播报，避免下游执行器无法建立取消、同步及审计归属。
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
            # 语言提示被收敛到有限别名集，禁止把任意 locale/模型名从 RPC 直接传给
            # TTS 运行时；未提供时按稳定默认中文处理。
            return self._reject(
                request_id=request_id,
                session_id=session_id,
                event_type=EVENT_SPEAK,
                direction=DIRECTION_HOST_TO_EDGE,
                seq=int(seq) if isinstance(seq, int) else None,
                stage=S_VOICE_SPEAK_EGRESS,
                reason_code=VOICE_SCHEMA_INVALID,
                detail="speak lang_hint must be zh, ja, or auto when provided",
                payload=payload,
                ts_ms=now_value,
            )

        # 韵律参数只作为受限的展示提示写入事件，实际模型选择与限幅仍由 TTS 链负责，
        # 本契约不扩大 Host 对本地后端的直接控制面。
        raw_intensity = payload.get("emotion_intensity")
        emotion_intensity: float | None = None
        if raw_intensity is not None:
            try:
                emotion_intensity = max(0.0, min(1.0, float(raw_intensity)))
            except (TypeError, ValueError):
                pass
        speed: float | None = None
        if payload.get("speed") is not None:
            try:
                speed = max(0.45, min(1.25, float(payload.get("speed"))))
            except (TypeError, ValueError):
                speed = None
        pitch: float | None = None
        if payload.get("pitch") is not None:
            try:
                pitch = max(-2.0, min(2.0, float(payload.get("pitch"))))
            except (TypeError, ValueError):
                pitch = None
        energy: float | None = None
        if payload.get("energy") is not None:
            try:
                energy = max(0.0, min(1.0, float(payload.get("energy"))))
            except (TypeError, ValueError):
                energy = None
        # 此事件是“可执行播报意图”的规范化收据；真正的合成、排队和播放结果由
        # 后续 TTS 运行时负责，不能因为 RPC 已接纳就宣称声音已经输出。
        event = {
            "request_id": request_id,
            "session_id": session_id,
            "event_type": EVENT_SPEAK,
            "direction": DIRECTION_HOST_TO_EDGE,
            "seq": int(seq) if isinstance(seq, int) else None,
            "utterance_id": utterance_id,
            "text": text,
            "emotion_tag": _normalize_emotion_tag(payload.get("emotion_tag")) or _default_speak_emotion_tag(),
            "interruptible": bool(payload.get("interruptible", True)),
            "voice_profile": str(payload.get("voice_profile") or "default"),
            "lang_hint": lang_hint,
            "ts_ms": now_value,
        }
        synchronization_id = str(payload.get("synchronization_id") or "").strip()
        if synchronization_id:
            event["synchronization_id"] = synchronization_id
        if str(payload.get("export_audio") or "").strip().lower() in {"1", "true", "yes", "on"}:
            event["export_audio"] = True
        if emotion_intensity is not None:
            event["emotion_intensity"] = emotion_intensity
        if speed is not None:
            event["speed"] = speed
        if pitch is not None:
            event["pitch"] = pitch
        if energy is not None:
            event["energy"] = energy
        if payload.get("style_note"):
            event["style_note"] = str(payload.get("style_note") or "").strip()
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
        # 打断事件只报告用户/输入源已介入；实际停止哪条播放由拥有 active_utterance_id
        # 的后续运行链决定，RPC 层不能把通知误当作已取消效果。
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

        # barge-in 仅携带观测到的打断证据和当时活跃 utterance；取消必须由持有
        # 播放控制权的运行时按该标识完成，因此不能在此层伪造成功状态。
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
        # 接受先落可选账本再返回。拒绝路径采用相同收据结构，使重启后仍能诊断每个
        # request_id 是已接纳、低置信提示还是被边界拒绝。
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
        # 返回的拒绝事件刻意不回显原始 payload；详细输入仅用于账本诊断，不能作为
        # 下游继续处理的有效语音/播报内容。
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
        # 账本缺席不影响实时语音可用性；它是审计/恢复观察点，而不是权限判定器。
        # 账本是可选观测设施。其不可用不能改变实时 RPC 的授权/接纳决定，也不能
        # 反过来让一次已验证语音事件被重复执行。
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
    # 候选转写只保留文本与数值置信度，滤除任意附带对象，防止 ASR 后端元数据泄漏进
    # Host 消息事件或改变跨版本的协议形状。
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
    # 空值有意回落到中文；非空未知值返回 None 触发 schema 拒绝，二者分别对应
    # “调用方未指定”与“调用方试图传入未授权后端提示”。
    normalized = str(value or "").strip().lower()
    if not normalized:
        return "zh"
    return _TTS_LANGUAGE_ALIASES.get(normalized)
