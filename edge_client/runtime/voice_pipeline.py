"""Edge voice pipeline orchestration (ASR upstream + playback linkage)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from edge_client.actuators.speech import (
    EVENT_TTS_PLAYBACK_FINISHED,
    EVENT_TTS_PLAYBACK_STARTED,
    SpeechPlaybackQueue,
    SpeechQueueResult,
)
from edge_client.actuators.vts_driver import VTubeStudioDriver, VtsDriverResult
from edge_client.rpc.event_reporter import BatchReportResult, EdgeEventReporter
from edge_client.rpc.resource_gate import VoiceResourceGate
from edge_client.rpc.voice import EVENT_ASR_CHUNK, VoiceRpcContract, VoiceRpcResult
from host_brain.core.errors import VOICE_INTERRUPTED
from host_brain.core.ids import now_ms


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
    vts_result: VtsDriverResult | None


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
        start_result: SpeechQueueResult | None = None
        vts_result: VtsDriverResult | None = None

        if queue_result.success and auto_start:
            start_result = self.speech_queue.start_next(ts_ms=event_ts)
            if (
                start_result is not None
                and start_result.success
                and start_result.event.get("event_type") == EVENT_TTS_PLAYBACK_STARTED
                and self.vts_driver is not None
            ):
                vts_result = self.vts_driver.talk_start()

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
            vts_result=vts_result,
        )

    def complete_playback(self, *, ts_ms: int | None = None) -> SpeechQueueResult | None:
        event_ts = int(ts_ms if ts_ms is not None else now_ms())
        result = self.speech_queue.complete_active(ts_ms=event_ts)
        if (
            result is not None
            and result.success
            and result.event.get("event_type") == EVENT_TTS_PLAYBACK_FINISHED
            and self.vts_driver is not None
        ):
            self.vts_driver.talk_end()
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
            "payload": {
                **event,
                **self._resource_metrics(),
            },
        }
        return self.event_reporter.report([report_event])

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

