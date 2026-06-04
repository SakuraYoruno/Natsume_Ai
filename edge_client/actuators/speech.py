"""Speech actuator adapters plus deterministic TTS queue orchestration."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import threading
import time
from typing import Any
from typing import Callable

from edge_client.rpc.voice import (
    DIRECTION_HOST_TO_EDGE,
    EVENT_BARGE_IN,
    S_VOICE_BARGE_IN,
    STATUS_ACCEPTED,
    STATUS_REJECTED,
    VoiceRpcContract,
    VoiceRpcResult,
)
from edge_client.rpc.resource_gate import TASK_PLAYBACK, TASK_TTS_SYNTH, VoiceResourceGate
from host_brain.core.errors import VOICE_INTERRUPTED, VOICE_PLAYBACK_FAILED, VOICE_SYNTH_FAILED
from host_brain.core.ids import now_ms


EVENT_TTS_QUEUED = "TTS_QUEUED"
EVENT_TTS_SYNTH_READY = "TTS_SYNTH_READY"
EVENT_TTS_SYNTH_FAILED = "TTS_SYNTH_FAILED"
EVENT_TTS_PLAYBACK_STARTED = "TTS_PLAYBACK_STARTED"
EVENT_TTS_PLAYBACK_FINISHED = "TTS_PLAYBACK_FINISHED"
EVENT_TTS_PLAYBACK_INTERRUPTED = "TTS_PLAYBACK_INTERRUPTED"

S_VOICE_TTS_QUEUE = "S_VOICE_TTS_QUEUE"
S_VOICE_TTS_SYNTH = "S_VOICE_TTS_SYNTH"
S_VOICE_TTS_PLAYBACK = "S_VOICE_TTS_PLAYBACK"

Synthesizer = Callable[[dict[str, Any]], dict[str, Any]]
PlaybackStarter = Callable[[dict[str, Any], dict[str, Any]], None]
# 在播放启动前触发的钩子（speak_event, synth_event），用于提前驱动 VTS 表情/口型
BeforePlaybackHook = Callable[[dict[str, Any], dict[str, Any]], None]


@dataclass(frozen=True)
class SpeechQueueResult:
    success: bool
    status: str
    reason_code: str | None
    stage: str
    detail: str
    event: dict[str, Any]


class SpeechPlaybackQueue:
    """Deterministic `speak -> synth -> playback` queue with auditable events."""

    def __init__(
        self,
        *,
        rpc: VoiceRpcContract,
        resource_gate: VoiceResourceGate | None = None,
        synthesizer: Synthesizer | None = None,
        playback_starter: PlaybackStarter | None = None,
        before_playback_hook: BeforePlaybackHook | None = None,
    ) -> None:
        self.rpc = rpc
        self.resource_gate = resource_gate
        self._lock = threading.RLock()
        self._queue: deque[dict[str, Any]] = deque()
        self._active: dict[str, Any] | None = None
        self._synthesizer = synthesizer or _default_synthesizer
        self._playback_starter = playback_starter or _default_playback_starter
        self._before_playback_hook: BeforePlaybackHook | None = before_playback_hook

    @property
    def queue_depth(self) -> int:
        with self._lock:
            return len(self._queue)

    @property
    def active_playback(self) -> dict[str, Any] | None:
        with self._lock:
            if self._active is None:
                return None
            return dict(self._active)

    def enqueue_speak(
        self,
        *,
        request_id: str,
        payload: dict[str, Any],
        ts_ms: int | None = None,
        auto_start: bool = False,
    ) -> SpeechQueueResult:
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        speak = request_speak(
            rpc=self.rpc,
            request_id=request_id,
            payload=payload,
            ts_ms=now_value,
        )
        if not speak.success:
            return _from_voice_result(speak)

        item = {
            "request_id": request_id,
            "speak_event": speak.event,
            "payload": dict(payload),
        }
        self._queue.append(item)

        event = {
            "request_id": request_id,
            "session_id": speak.event["session_id"],
            "utterance_id": speak.event["utterance_id"],
            "event_type": EVENT_TTS_QUEUED,
            "queue_depth": len(self._queue),
            "ts_ms": now_value,
        }
        self._log_event(
            request_id=request_id,
            session_id=speak.event["session_id"],
            event_type=EVENT_TTS_QUEUED,
            seq=speak.event.get("seq"),
            status=STATUS_ACCEPTED,
            reason_code=None,
            stage=S_VOICE_TTS_QUEUE,
            detail="tts queued",
            payload=event,
            ts_ms=now_value,
        )
        # auto_start=True 且当前无正在播放的项目时，立即启动播放
        if auto_start and self._active is None:
            start_result = self.start_next(ts_ms=now_value)
            if start_result is not None:
                return start_result

        return SpeechQueueResult(
            success=True,
            status=STATUS_ACCEPTED,
            reason_code=None,
            stage=S_VOICE_TTS_QUEUE,
            detail="tts queued",
            event=event,
        )

    def start_next(self, *, ts_ms: int | None = None) -> SpeechQueueResult | None:
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        if self._active is not None:
            return SpeechQueueResult(
                success=False,
                status=STATUS_REJECTED,
                reason_code=VOICE_PLAYBACK_FAILED,
                stage=S_VOICE_TTS_PLAYBACK,
                detail="cannot start next while playback is active",
                event={
                    "event_type": EVENT_TTS_PLAYBACK_STARTED,
                    "ts_ms": now_value,
                },
            )
        if not self._queue:
            return None

        item = self._queue.popleft()
        request_id = str(item["request_id"])
        speak_event = dict(item["speak_event"])
        payload = dict(item.get("payload") or {})
        session_id = str(speak_event.get("session_id") or "")
        seq = speak_event.get("seq")

        if self.resource_gate is not None:
            synth_gate = self.resource_gate.request(
                request_id=request_id,
                task=TASK_TTS_SYNTH,
                session_id=session_id,
                metrics={"queue_depth": len(self._queue)},
                hold=False,
                ts_ms=now_value,
            )
            if not synth_gate.admitted:
                detail = f"tts synth blocked by resource gate: {synth_gate.detail}"
                event = {
                    "request_id": request_id,
                    "session_id": session_id,
                    "utterance_id": speak_event.get("utterance_id"),
                    "event_type": EVENT_TTS_SYNTH_FAILED,
                    "ts_ms": now_value,
                }
                self._log_event(
                    request_id=request_id,
                    session_id=session_id,
                    event_type=EVENT_TTS_SYNTH_FAILED,
                    seq=seq,
                    status=STATUS_REJECTED,
                    reason_code=synth_gate.reason_code,
                    stage=synth_gate.stage,
                    detail=detail,
                    payload={**event, "gate": synth_gate.event},
                    ts_ms=now_value,
                )
                return SpeechQueueResult(
                    success=False,
                    status=STATUS_REJECTED,
                    reason_code=synth_gate.reason_code,
                    stage=synth_gate.stage,
                    detail=detail,
                    event=event,
                )

        stage_t0 = time.monotonic()
        try:
            synth = self._synthesizer(speak_event)
            synth_event = dict(synth) if isinstance(synth, dict) else {"value": synth}
        except Exception as exc:  # pragma: no cover - defensive catch for callback
            detail = f"tts synth failed: {exc}"
            event = {
                "request_id": request_id,
                "session_id": session_id,
                "utterance_id": speak_event.get("utterance_id"),
                "event_type": EVENT_TTS_SYNTH_FAILED,
                "ts_ms": now_value,
            }
            self._log_event(
                request_id=request_id,
                session_id=session_id,
                event_type=EVENT_TTS_SYNTH_FAILED,
                seq=seq,
                status=STATUS_REJECTED,
                reason_code=VOICE_SYNTH_FAILED,
                stage=S_VOICE_TTS_SYNTH,
                detail=detail,
                payload={**event, "error": str(exc)},
                ts_ms=now_value,
            )
            return SpeechQueueResult(
                success=False,
                status=STATUS_REJECTED,
                reason_code=VOICE_SYNTH_FAILED,
                stage=S_VOICE_TTS_SYNTH,
                detail=detail,
                event=event,
            )

        synth_elapsed_s = time.monotonic() - stage_t0
        synth_log = {
            "request_id": request_id,
            "session_id": session_id,
            "utterance_id": speak_event.get("utterance_id"),
            "event_type": EVENT_TTS_SYNTH_READY,
            "audio_ref": synth_event.get("audio_ref"),
            "synth_elapsed_s": round(synth_elapsed_s, 4),
            "ts_ms": now_value,
        }
        if synth_event.get("audio_path"):
            synth_log["audio_path"] = synth_event.get("audio_path")
            synth_log["audio_mime"] = synth_event.get("audio_mime") or "audio/wav"
        self._log_event(
            request_id=request_id,
            session_id=session_id,
            event_type=EVENT_TTS_SYNTH_READY,
            seq=seq,
            status=STATUS_ACCEPTED,
            reason_code=None,
            stage=S_VOICE_TTS_SYNTH,
            detail="tts synth ready",
            payload=synth_log,
            ts_ms=now_value,
        )

        playback_gate_acquired = False
        if self.resource_gate is not None:
            playback_gate = self.resource_gate.request(
                request_id=request_id,
                task=TASK_PLAYBACK,
                session_id=session_id,
                metrics={"queue_depth": len(self._queue)},
                hold=True,
                ts_ms=now_value,
            )
            if not playback_gate.admitted:
                detail = f"tts playback blocked by resource gate: {playback_gate.detail}"
                event = {
                    "request_id": request_id,
                    "session_id": session_id,
                    "utterance_id": speak_event.get("utterance_id"),
                    "event_type": EVENT_TTS_PLAYBACK_STARTED,
                    "ts_ms": now_value,
                }
                self._log_event(
                    request_id=request_id,
                    session_id=session_id,
                    event_type=EVENT_TTS_PLAYBACK_STARTED,
                    seq=seq,
                    status=STATUS_REJECTED,
                    reason_code=playback_gate.reason_code,
                    stage=playback_gate.stage,
                    detail=detail,
                    payload={**event, "gate": playback_gate.event},
                    ts_ms=now_value,
                )
                return SpeechQueueResult(
                    success=False,
                    status=STATUS_REJECTED,
                    reason_code=playback_gate.reason_code,
                    stage=playback_gate.stage,
                    detail=detail,
                    event=event,
                )
            playback_gate_acquired = True

        self._active = {
            "request_id": request_id,
            "session_id": session_id,
            "seq": seq,
            "utterance_id": speak_event.get("utterance_id"),
            "speak_event": speak_event,
            "payload": payload,
            "synth_event": synth_event,
        }
        # 在播放启动前触发钩子（如 VTS 表情/口型同步），确保与音频同步
        hook_t0 = time.monotonic()
        hook_elapsed_s = 0.0
        if self._before_playback_hook is not None:
            try:
                self._before_playback_hook(speak_event, synth_event)
            except Exception as hook_exc:  # pragma: no cover - 钩子失败不阻断播放
                import logging as _logging
                _logging.getLogger("Natsume.edge.speech").warning(
                    "before_playback_hook raised: %s", hook_exc
                )
            finally:
                hook_elapsed_s = time.monotonic() - hook_t0
        playback_t0 = time.monotonic()
        try:
            self._playback_starter(speak_event, synth_event)
        except Exception as exc:  # pragma: no cover - defensive catch for callback
            self._active = None
            detail = f"tts playback failed to start: {exc}"
            if playback_gate_acquired and self.resource_gate is not None:
                self.resource_gate.release(
                    request_id=request_id,
                    task=TASK_PLAYBACK,
                    session_id=session_id,
                    ts_ms=now_value,
                )
            event = {
                "request_id": request_id,
                "session_id": session_id,
                "utterance_id": speak_event.get("utterance_id"),
                "event_type": EVENT_TTS_PLAYBACK_STARTED,
                "ts_ms": now_value,
            }
            self._log_event(
                request_id=request_id,
                session_id=session_id,
                event_type=EVENT_TTS_PLAYBACK_STARTED,
                seq=seq,
                status=STATUS_REJECTED,
                reason_code=VOICE_PLAYBACK_FAILED,
                stage=S_VOICE_TTS_PLAYBACK,
                detail=detail,
                payload={**event, "error": str(exc)},
                ts_ms=now_value,
            )
            return SpeechQueueResult(
                success=False,
                status=STATUS_REJECTED,
                reason_code=VOICE_PLAYBACK_FAILED,
                stage=S_VOICE_TTS_PLAYBACK,
                detail=detail,
                event=event,
            )
        playback_elapsed_s = time.monotonic() - playback_t0
        total_elapsed_s = time.monotonic() - stage_t0
        playback_event = {
            "request_id": request_id,
            "session_id": session_id,
            "utterance_id": speak_event.get("utterance_id"),
            "event_type": EVENT_TTS_PLAYBACK_STARTED,
            "audio_ref": synth_event.get("audio_ref"),
            "synth_elapsed_s": round(synth_elapsed_s, 4),
            "before_playback_hook_elapsed_s": round(hook_elapsed_s, 4),
            "playback_start_elapsed_s": round(playback_elapsed_s, 4),
            "start_next_elapsed_s": round(total_elapsed_s, 4),
            "ts_ms": now_value,
        }
        if synth_event.get("audio_path"):
            playback_event["audio_path"] = synth_event.get("audio_path")
            playback_event["audio_mime"] = synth_event.get("audio_mime") or "audio/wav"
        for key in (
            "duration_s",
            "sample_rate",
            "rms_frames",
            "mouth_frames",
            "mouth_frame_interval_s",
            "mouth_frame_count",
        ):
            if key in synth_event:
                playback_event[key] = synth_event.get(key)
        self._log_event(
            request_id=request_id,
            session_id=session_id,
            event_type=EVENT_TTS_PLAYBACK_STARTED,
            seq=seq,
            status=STATUS_ACCEPTED,
            reason_code=None,
            stage=S_VOICE_TTS_PLAYBACK,
            detail="tts playback started",
            payload=playback_event,
            ts_ms=now_value,
        )
        return SpeechQueueResult(
            success=True,
            status=STATUS_ACCEPTED,
            reason_code=None,
            stage=S_VOICE_TTS_PLAYBACK,
            detail="tts playback started",
            event=playback_event,
        )

    def complete_active(self, *, ts_ms: int | None = None) -> SpeechQueueResult | None:
        if self._active is None:
            return None
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        active = self._active
        self._active = None

        if self.resource_gate is not None:
            self.resource_gate.release(
                request_id=str(active["request_id"]),
                task=TASK_PLAYBACK,
                session_id=str(active["session_id"]),
                ts_ms=now_value,
            )

        event = {
            "request_id": active["request_id"],
            "session_id": active["session_id"],
            "utterance_id": active.get("utterance_id"),
            "event_type": EVENT_TTS_PLAYBACK_FINISHED,
            "ts_ms": now_value,
        }
        self._log_event(
            request_id=str(active["request_id"]),
            session_id=str(active["session_id"]),
            event_type=EVENT_TTS_PLAYBACK_FINISHED,
            seq=active.get("seq"),
            status=STATUS_ACCEPTED,
            reason_code=None,
            stage=S_VOICE_TTS_PLAYBACK,
            detail="tts playback finished",
            payload=event,
            ts_ms=now_value,
        )
        return SpeechQueueResult(
            success=True,
            status=STATUS_ACCEPTED,
            reason_code=None,
            stage=S_VOICE_TTS_PLAYBACK,
            detail="tts playback finished",
            event=event,
        )

    def handle_barge_in(
        self,
        *,
        request_id: str,
        payload: dict[str, Any],
        ts_ms: int | None = None,
    ) -> SpeechQueueResult:
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        barge = request_barge_in(
            rpc=self.rpc,
            request_id=request_id,
            payload=payload,
            ts_ms=now_value,
        )
        if not barge.success:
            return _from_voice_result(barge)

        session_id = str(barge.event.get("session_id") or payload.get("session_id") or "")
        if self._active is not None and str(self._active.get("session_id") or "") == session_id:
            active = self._active
            self._active = None
            if self.resource_gate is not None:
                self.resource_gate.release(
                    request_id=str(active["request_id"]),
                    task=TASK_PLAYBACK,
                    session_id=session_id,
                    ts_ms=now_value,
                )
            interrupt_event = {
                "request_id": str(active["request_id"]),
                "session_id": session_id,
                "utterance_id": active.get("utterance_id"),
                "event_type": EVENT_TTS_PLAYBACK_INTERRUPTED,
                "interrupted_by_request_id": request_id,
                "barge_event_type": EVENT_BARGE_IN,
                "ts_ms": now_value,
            }
            self._log_event(
                request_id=str(active["request_id"]),
                session_id=session_id,
                event_type=EVENT_TTS_PLAYBACK_INTERRUPTED,
                seq=active.get("seq"),
                status=STATUS_ACCEPTED,
                reason_code=VOICE_INTERRUPTED,
                stage=S_VOICE_TTS_PLAYBACK,
                detail="tts playback interrupted by barge_in",
                payload=interrupt_event,
                ts_ms=now_value,
            )
            return SpeechQueueResult(
                success=True,
                status=STATUS_ACCEPTED,
                reason_code=VOICE_INTERRUPTED,
                stage=S_VOICE_TTS_PLAYBACK,
                detail="tts playback interrupted by barge_in",
                event=interrupt_event,
            )

        return SpeechQueueResult(
            success=True,
            status=STATUS_ACCEPTED,
            reason_code=None,
            stage=S_VOICE_BARGE_IN,
            detail="barge_in accepted; no active playback for session",
            event=barge.event,
        )

    def _log_event(
        self,
        *,
        request_id: str,
        session_id: str,
        event_type: str,
        seq: int | None,
        status: str,
        reason_code: str | None,
        stage: str,
        detail: str,
        payload: dict[str, Any],
        ts_ms: int,
    ) -> None:
        if self.rpc.ledger is None:
            return
        self.rpc.ledger.log_voice_event(
            {
                "request_id": request_id,
                "session_id": session_id,
                "event_type": event_type,
                "direction": DIRECTION_HOST_TO_EDGE,
                "seq": seq,
                "status": status,
                "reason_code": reason_code,
                "stage": stage,
                "payload": payload,
                "detail": detail,
                "ts_ms": ts_ms,
            }
        )


def request_speak(
    *,
    rpc: VoiceRpcContract,
    request_id: str,
    payload: dict[str, Any],
    ts_ms: int | None = None,
) -> VoiceRpcResult:
    return rpc.speak(
        request_id=request_id,
        payload=payload,
        ts_ms=ts_ms,
    )


def request_barge_in(
    *,
    rpc: VoiceRpcContract,
    request_id: str,
    payload: dict[str, Any],
    ts_ms: int | None = None,
) -> VoiceRpcResult:
    return rpc.barge_in(
        request_id=request_id,
        payload=payload,
        ts_ms=ts_ms,
    )


def _from_voice_result(result: VoiceRpcResult) -> SpeechQueueResult:
    return SpeechQueueResult(
        success=result.success,
        status=result.status,
        reason_code=result.reason_code,
        stage=result.stage,
        detail=result.detail,
        event=dict(result.event),
    )


def _default_synthesizer(speak_event: dict[str, Any]) -> dict[str, Any]:
    utterance_id = str(speak_event.get("utterance_id") or "unknown")
    return {"audio_ref": f"memory://{utterance_id}"}


def _default_playback_starter(speak_event: dict[str, Any], synth_event: dict[str, Any]) -> None:
    _ = (speak_event, synth_event)
