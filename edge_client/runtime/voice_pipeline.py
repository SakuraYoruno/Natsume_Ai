"""Edge voice pipeline orchestration (ASR upstream + playback linkage)."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import threading
import time
from typing import Any

from edge_client.actuators.speech import (
    EVENT_TTS_PLAYBACK_FINISHED,
    EVENT_TTS_PLAYBACK_STARTED,
    SpeechPlaybackQueue,
    SpeechQueueResult,
    BeforePlaybackHook,
)
from edge_client.actuators.vts_driver import VTubeStudioDriver, VtsDriverResult, VtsState
from edge_client.rpc.event_reporter import BatchReportResult, EdgeEventReporter
from edge_client.rpc.resource_gate import VoiceResourceGate
from edge_client.rpc.voice import EVENT_ASR_CHUNK, VoiceRpcContract, VoiceRpcResult
from edge_client.runtime.emotion_inference import infer_asr_emotion
from host_brain.core.errors import VOICE_INTERRUPTED
from host_brain.core.ids import now_ms

LOGGER = logging.getLogger("Natsume.edge.voice_pipeline")


@dataclass(frozen=True)
class EdgeAsrUpstreamResult:
    success: bool
    rpc_result: VoiceRpcResult
    report_result: BatchReportResult | None
    flush_result: BatchReportResult | None


@dataclass(frozen=True)
class EdgePlaybackResult:
    success: bool
    queue_result: SpeechQueueResult
    start_result: SpeechQueueResult | None
    expression_result: VtsDriverResult | None
    talk_result: VtsDriverResult | None
    mouth_sync_debug: dict[str, Any] | None


@dataclass(frozen=True)
class EdgeBargeInResult:
    success: bool
    barge_result: SpeechQueueResult
    vts_result: VtsDriverResult | None


class EdgeVoicePipeline:
    """Orchestrates ASR upstream reporting, TTS queue flow, and VTS linkage."""

    def __init__(
        self,
        *,
        voice_rpc: VoiceRpcContract,
        speech_queue: SpeechPlaybackQueue,
        event_reporter: EdgeEventReporter,
        vts_driver: VTubeStudioDriver | None = None,
        resource_gate: VoiceResourceGate | None = None,
    ) -> None:
        self.voice_rpc = voice_rpc
        self.speech_queue = speech_queue
        self.event_reporter = event_reporter
        self.vts_driver = vts_driver
        self.resource_gate = resource_gate
        self._mouth_sync_stop = threading.Event()
        self._mouth_sync_thread: threading.Thread | None = None
        self._mouth_sync_debug: dict[str, Any] = {
            "state": "idle",
            "started": False,
            "reason": "not_started",
        }
        # 将 VTS 驱动注册为播放前钩子，确保表情/口型在音频播放前就绪
        self.speech_queue._before_playback_hook = self._vts_before_playback_hook

    def asr_chunk(
        self,
        *,
        request_id: str,
        payload: dict[str, Any],
        ts_ms: int | None = None,
    ) -> EdgeAsrUpstreamResult:
        event_ts = int(ts_ms if ts_ms is not None else now_ms())
        merged_payload = dict(payload)
        merged_payload.update(self._resource_metrics())

        rpc_result = self.voice_rpc.asr_chunk(
            request_id=request_id,
            payload=merged_payload,
            ts_ms=event_ts,
        )
        report_result = self._report_asr_rpc_result(
            request_id=request_id,
            rpc_result=rpc_result,
        )
        flush_result = None
        if report_result is not None and report_result.success:
            flush_result = self.event_reporter.flush_cache()
        success = bool(rpc_result.success and (report_result is None or report_result.success))
        return EdgeAsrUpstreamResult(
            success=success,
            rpc_result=rpc_result,
            report_result=report_result,
            flush_result=flush_result,
        )

    def on_asr_rpc_result(self, request_id: str, rpc_result: VoiceRpcResult) -> None:
        report_result = self._report_asr_rpc_result(
            request_id=request_id,
            rpc_result=rpc_result,
        )
        if report_result is not None and report_result.success:
            self.event_reporter.flush_cache()

    def enqueue_speak(
        self,
        *,
        request_id: str,
        payload: dict[str, Any],
        ts_ms: int | None = None,
        auto_start: bool = True,
    ) -> EdgePlaybackResult:
        event_ts = int(ts_ms if ts_ms is not None else now_ms())
        queue_result = self.speech_queue.enqueue_speak(
            request_id=request_id,
            payload=payload,
            ts_ms=event_ts,
        )
        self._report_voice_queue_result(request_id=request_id, result=queue_result)
        start_result: SpeechQueueResult | None = None
        expression_result: VtsDriverResult | None = None
        talk_result: VtsDriverResult | None = None
        mouth_sync_debug: dict[str, Any] | None = None

        if queue_result.success and auto_start:
            # 重置 VTS 钩子的结果缓存，准备接收本次播放的 VTS 结果
            self._pending_vts_result: dict[str, Any] = {}
            start_result = self.speech_queue.start_next(ts_ms=event_ts)
            self._report_voice_queue_result(request_id=request_id, result=start_result)
            if (
                start_result is not None
                and start_result.success
                and start_result.event.get("event_type") == EVENT_TTS_PLAYBACK_STARTED
            ):
                # VTS 已在 before_playback_hook 中提前驱动，从缓存中取结果
                vts_cached = getattr(self, "_pending_vts_result", {})
                if not vts_cached:
                    # hook 未触发（如 speech_queue 被 mock），手动兜底驱动 VTS
                    self._vts_before_playback_hook(
                        speak_event=payload,
                        synth_event={},
                    )
                    vts_cached = getattr(self, "_pending_vts_result", {})
                expression_result = vts_cached.get("expression_result")
                talk_result = vts_cached.get("talk_result")
                mouth_sync_debug = vts_cached.get("mouth_sync_debug")

        success = bool(
            queue_result.success
            and (
                start_result is None
                or start_result.success
            )
        )
        return EdgePlaybackResult(
            success=success,
            queue_result=queue_result,
            start_result=start_result,
            expression_result=expression_result,
            talk_result=talk_result,
            mouth_sync_debug=mouth_sync_debug,
        )

    def complete_playback(self, *, ts_ms: int | None = None) -> SpeechQueueResult | None:
        event_ts = int(ts_ms if ts_ms is not None else now_ms())
        result = self.speech_queue.complete_active(ts_ms=event_ts)
        self._report_voice_queue_result(
            request_id=str(result.event.get("request_id") or "") if result is not None else "",
            result=result,
        )
        if (
            result is not None
            and result.success
            and result.event.get("event_type") == EVENT_TTS_PLAYBACK_FINISHED
            and self.vts_driver is not None
        ):
            self._stop_mouth_sync()
            self.vts_driver.talk_end()
            self.vts_driver.reset_to_neutral()
        if result is not None and result.success:
            # before_playback_hook 会在 start_next 内部自动驱动 VTS，无需额外调用
            next_start_result = self.speech_queue.start_next(ts_ms=event_ts)
            self._report_voice_queue_result(
                request_id=str(result.event.get("request_id") or ""),
                result=next_start_result,
            )
        return result

    def barge_in(
        self,
        *,
        request_id: str,
        payload: dict[str, Any],
        ts_ms: int | None = None,
    ) -> EdgeBargeInResult:
        event_ts = int(ts_ms if ts_ms is not None else now_ms())
        barge_result = self.speech_queue.handle_barge_in(
            request_id=request_id,
            payload=payload,
            ts_ms=event_ts,
        )
        vts_result: VtsDriverResult | None = None
        if (
            barge_result.success
            and barge_result.reason_code == VOICE_INTERRUPTED
            and self.vts_driver is not None
        ):
            self._stop_mouth_sync()
            vts_result = self.vts_driver.reset_to_neutral()
        return EdgeBargeInResult(
            success=barge_result.success,
            barge_result=barge_result,
            vts_result=vts_result,
        )

    def _report_asr_rpc_result(
        self,
        *,
        request_id: str,
        rpc_result: VoiceRpcResult,
    ) -> BatchReportResult | None:
        event = dict(rpc_result.event)
        if event.get("event_type") != EVENT_ASR_CHUNK:
            return None
        session_id = str(event.get("session_id") or "").strip()
        if not session_id:
            return None
        emotion_report = self._resolve_asr_emotion_report(event)
        payload = {
            **event,
            **self._resource_metrics(),
        }
        emotion_tag = str(emotion_report.get("emotion_tag") or "").strip().upper() or None
        emotion_intensity = emotion_report.get("emotion_intensity")
        if emotion_tag:
            payload["emotion_tag"] = emotion_tag
        if isinstance(emotion_intensity, (int, float)):
            payload["emotion_intensity"] = max(0.0, min(1.0, float(emotion_intensity)))
        mood_state = str(emotion_report.get("mood_state") or "").strip().lower()
        if mood_state:
            payload["mood_state"] = mood_state
        emotion_source = str(emotion_report.get("emotion_source") or "").strip()
        if emotion_source:
            payload["emotion_source"] = emotion_source
        matched_terms = emotion_report.get("matched_terms")
        if isinstance(matched_terms, list) and matched_terms:
            payload["emotion_terms"] = list(matched_terms)

        report_event = {
            "event_type": EVENT_ASR_CHUNK,
            "ts_ms": int(event.get("ts_ms") or now_ms()),
            "request_id": str(request_id),
            "session_id": session_id,
            "source": "edge",
            "direction": "EDGE_TO_HOST",
            "seq": event.get("seq"),
            "status": rpc_result.status,
            "reason_code": rpc_result.reason_code,
            "stage": rpc_result.stage,
            "detail": rpc_result.detail,
            "payload": payload,
        }
        if emotion_tag:
            report_event["emotion_tag"] = emotion_tag
        if isinstance(emotion_intensity, (int, float)):
            report_event["emotion_intensity"] = max(0.0, min(1.0, float(emotion_intensity)))
        return self.event_reporter.report([report_event])

    def _report_voice_queue_result(
        self,
        *,
        request_id: str,
        result: SpeechQueueResult | None,
    ) -> BatchReportResult | None:
        if result is None:
            return None
        event = dict(result.event or {})
        event_type = str(event.get("event_type") or "").strip()
        session_id = str(event.get("session_id") or "").strip()
        if not event_type or not session_id:
            return None
        report_event = {
            "event_type": event_type,
            "ts_ms": int(event.get("ts_ms") or now_ms()),
            "request_id": str(event.get("request_id") or request_id or ""),
            "session_id": session_id,
            "source": "edge",
            "direction": "EDGE_TO_HOST",
            "seq": event.get("seq"),
            "status": result.status,
            "reason_code": result.reason_code,
            "stage": result.stage,
            "detail": result.detail,
            "payload": {
                **event,
                **self._resource_metrics(),
            },
        }
        report_result = self.event_reporter.report([report_event])
        if report_result.success:
            self.event_reporter.flush_cache()
        return report_result

    def _resource_metrics(self) -> dict[str, Any]:
        queue_depth = int(self.speech_queue.queue_depth)
        if self.resource_gate is None:
            return {
                "vram_total_mb": 0,
                "vram_used_mb": 0,
                "queue_depth": queue_depth,
            }
        snapshot = self.resource_gate.snapshot()
        metrics = dict(snapshot.get("metrics") or {})
        return {
            "vram_total_mb": int(metrics.get("vram_total_mb", 0)),
            "vram_used_mb": int(metrics.get("vram_used_mb", 0)),
            "queue_depth": queue_depth,
        }

    def _vts_before_playback_hook(
        self,
        speak_event: dict[str, Any],
        synth_event: dict[str, Any],
    ) -> None:
        """在音频播放启动前驱动 VTS 表情和口型，消除滞后。"""
        payload = dict(speak_event)
        # 合并 emotion_tag / emotion_intensity
        if not payload.get("emotion_tag") and speak_event.get("emotion_tag"):
            payload["emotion_tag"] = str(speak_event["emotion_tag"]).strip().upper()
        _etag = str(payload.get("emotion_tag") or "NEUTRAL").strip().upper()
        _has_audio = bool(synth_event and synth_event.get("audio_data"))
        LOGGER.info(
            "VTS start (pre-playback): emotion_tag=%s synth_has_audio=%s",
            _etag, _has_audio,
        )
        expression_result, talk_result, mouth_sync_debug = self._start_vts_for_payload(
            payload,
            synth_event=synth_event,
        )
        # 缓存结果供 enqueue_speak / complete_playback 读取
        self._pending_vts_result = {
            "expression_result": expression_result,
            "talk_result": talk_result,
            "mouth_sync_debug": mouth_sync_debug,
        }

    def _start_vts_for_payload(
        self,
        payload: dict[str, Any],
        *,
        synth_event: dict[str, Any] | None = None,
    ) -> tuple[VtsDriverResult | None, VtsDriverResult | None, dict[str, Any] | None]:
        if self.vts_driver is None:
            self._mouth_sync_debug = {
                "state": "skipped",
                "started": False,
                "reason": "vts_driver_unavailable",
            }
            return None, None, dict(self._mouth_sync_debug)
        self._stop_mouth_sync()
        if self.vts_driver.state != VtsState.AUTHENTICATED:
            conn_result = self.vts_driver.connect()
            if not conn_result.success:
                self._mouth_sync_debug = {
                    "state": "blocked",
                    "started": False,
                    "reason": "vts_connect_failed",
                    "detail": conn_result.detail,
                }
                return conn_result, None, dict(self._mouth_sync_debug)
        emotion_tag = str(payload.get("emotion_tag") or "NEUTRAL").strip().upper() or "NEUTRAL"
        raw_intensity = payload.get("emotion_intensity")
        intensity = 0.5
        if raw_intensity is not None:
            try:
                intensity = max(0.0, min(1.0, float(raw_intensity)))
            except (TypeError, ValueError):
                intensity = 0.5
        if emotion_tag == "NEUTRAL":
            expression_result = self.vts_driver.reset_to_neutral()
        else:
            self.vts_driver.reset_to_neutral()
            expression_result = self.vts_driver.set_expression(emotion_tag, intensity=intensity)
        sub_emotion = payload.get("sub_emotion")
        if sub_emotion:
            from host_brain.channels.vts_mapping_store import sub_emotion_to_hotkey

            hotkey_id = sub_emotion_to_hotkey(sub_emotion)
            if hotkey_id:
                self.vts_driver.trigger_hotkey(hotkey_id)
        talk_result = self.vts_driver.talk_start()
        vts_health = self.vts_driver.health()
        mouth_sync_debug = {
            "state": "talk_started" if talk_result.success else "talk_failed",
            "started": False,
            "emotion_tag": emotion_tag,
            "intensity": round(float(intensity), 4),
            "vts_parameters": dict(vts_health.get("mouth_parameters") or {}),
        }
        if synth_event and talk_result.success:
            mouth_sync_debug = self._start_mouth_sync(synth_event)
        elif not synth_event:
            mouth_sync_debug["reason"] = "missing_synth_event"
            LOGGER.warning(
                "VTS mouth sync skipped: no synth_event (Edge needs local TTS with audio_data, e.g. SoVITS, for lip sync)"
            )
        self._mouth_sync_debug = dict(mouth_sync_debug)
        return expression_result, talk_result, dict(mouth_sync_debug)

    def _stop_mouth_sync(self) -> None:
        self._mouth_sync_stop.set()
        thread = self._mouth_sync_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=0.2)
        self._mouth_sync_thread = None
        self._mouth_sync_stop = threading.Event()
        current_state = str(self._mouth_sync_debug.get("state") or "")
        if current_state == "running":
            self._mouth_sync_debug = {
                **self._mouth_sync_debug,
                "state": "stopped",
            }

    def _start_mouth_sync(self, synth_event: dict[str, Any]) -> dict[str, Any]:
        if self.vts_driver is None:
            debug = {
                "state": "skipped",
                "started": False,
                "reason": "vts_driver_unavailable",
            }
            self._mouth_sync_debug = dict(debug)
            return debug
        audio_data = synth_event.get("audio_data")
        sample_rate_raw = synth_event.get("sample_rate")
        try:
            sample_rate = int(sample_rate_raw)
        except (TypeError, ValueError):
            sample_rate = 0
        envelope = self._build_mouth_envelope(audio_data, sample_rate)
        debug = {
            "state": "prepared",
            "started": False,
            "audio_type": type(audio_data).__name__,
            "sample_rate": sample_rate,
            "envelope_points": len(envelope),
            "source": "synth_event.audio_data",
        }
        if not envelope:
            debug["state"] = "skipped"
            debug["reason"] = "empty_envelope"
            self._mouth_sync_debug = dict(debug)
            LOGGER.warning(
                "VTS mouth sync skipped: empty_envelope (synth_event has no audio_data or sample_rate; "
                "ensure Edge TTS returns audio_data, e.g. SoVITS synthesizer)"
            )
            return debug
        debug["envelope_min"] = round(min(envelope), 4)
        debug["envelope_max"] = round(max(envelope), 4)
        debug["envelope_avg"] = round(sum(envelope) / len(envelope), 4)

        stop_event = self._mouth_sync_stop
        frame_interval_s = max(0.02, min(0.08, 1.0 / 24.0))
        debug["frame_interval_s"] = round(frame_interval_s, 4)
        debug["state"] = "running"
        debug["started"] = True
        debug["frames_sent"] = 0
        debug["last_value"] = 0.0
        self._mouth_sync_debug = dict(debug)

        def _runner() -> None:
            for value in envelope:
                if stop_event.is_set():
                    break
                result = self.vts_driver.set_mouth_open_level(value)
                debug["frames_sent"] = int(debug.get("frames_sent") or 0) + 1
                debug["last_value"] = round(float(value), 4)
                if not result.success:
                    debug["last_error"] = result.detail
                time.sleep(frame_interval_s)
            if not stop_event.is_set():
                self.vts_driver.set_mouth_open_level(0.0)
                debug["state"] = "finished"
            else:
                debug["state"] = "stopped"
            self._mouth_sync_debug = dict(debug)

        plan = getattr(self.vts_driver, "_mouth_parameter_plan", None) or {}
        param_names = list(plan.get("selected") or plan.get("mouth") or [])
        LOGGER.info(
            "mouth sync started: envelope_points=%s param_names=%s",
            len(envelope), param_names or "(fallback)",
        )
        self._mouth_sync_thread = threading.Thread(
            target=_runner,
            name="edge-vts-mouth-sync",
            daemon=True,
        )
        self._mouth_sync_thread.start()
        return dict(debug)

    @staticmethod
    def _build_mouth_envelope(audio_data: Any, sample_rate: int) -> list[float]:
        if audio_data is None or sample_rate <= 0:
            return []
        samples = EdgeVoicePipeline._coerce_audio_samples(audio_data)
        if not samples:
            return []

        peak = max(samples)
        if peak <= 0.0:
            return []

        frame_size = max(1, int(sample_rate / 24))
        values: list[float] = []
        smooth = 0.0
        for start in range(0, len(samples), frame_size):
            chunk = samples[start : start + frame_size]
            if not chunk:
                continue
            amplitude = (sum(chunk) / len(chunk)) / peak
            target = max(0.0, min(1.0, amplitude * 3.2))
            if target > 0.03:
                target = max(0.14, target)
            smooth = (smooth * 0.35) + (target * 0.65)
            values.append(round(max(0.0, min(1.0, smooth)), 4))
        return values

    @staticmethod
    def _coerce_audio_samples(audio_data: Any) -> list[float]:
        try:
            import numpy as np  # type: ignore[import]

            arr = np.asarray(audio_data, dtype=float)
            if arr.size == 0:
                return []
            if arr.ndim > 1:
                arr = np.mean(np.abs(arr), axis=1)
            else:
                arr = np.abs(arr)
            return [float(value) for value in arr.tolist()]
        except Exception:
            pass

        raw = audio_data
        if hasattr(raw, "tolist"):
            try:
                raw = raw.tolist()
            except Exception:
                return []
        if isinstance(raw, (bytes, bytearray, memoryview)):
            return []
        if not isinstance(raw, (list, tuple)):
            return [abs(float(raw))] if isinstance(raw, (int, float)) else []

        values: list[float] = []
        for item in raw:
            if isinstance(item, (int, float)):
                values.append(abs(float(item)))
                continue
            if isinstance(item, (list, tuple)):
                channel_values = [
                    abs(float(channel))
                    for channel in item
                    if isinstance(channel, (int, float))
                ]
                if channel_values:
                    values.append(sum(channel_values) / len(channel_values))
        return values

    def health(self) -> dict[str, Any]:
        thread = self._mouth_sync_thread
        return {
            "mouth_sync": dict(self._mouth_sync_debug),
            "mouth_sync_thread_alive": bool(thread is not None and thread.is_alive()),
        }

    def _resolve_asr_emotion_report(self, event: dict[str, Any]) -> dict[str, Any]:
        explicit_tag = str(event.get("emotion_tag") or "").strip().upper()
        explicit_intensity = event.get("emotion_intensity")
        if explicit_tag:
            report = {
                "emotion_tag": explicit_tag,
                "emotion_source": "edge_explicit",
                "mood_state": str(event.get("mood_state") or "").strip().lower() or "steady",
                "matched_terms": [],
            }
            if isinstance(explicit_intensity, (int, float)):
                report["emotion_intensity"] = max(0.0, min(1.0, float(explicit_intensity)))
            else:
                report["emotion_intensity"] = 0.5
            return report
        return infer_asr_emotion(
            text=str(event.get("text") or ""),
            confidence=event.get("confidence") if isinstance(event.get("confidence"), (int, float)) else None,
            final=bool(event.get("final", False)),
        )
