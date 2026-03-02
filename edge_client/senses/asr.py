"""Edge ASR service: faster-whisper SDK integration with audio capture and VAD.

Provides the full pipeline: microphone input -> VAD segmentation -> faster-whisper
transcription -> asr_chunk event emission via VoiceRpcContract.

GPU resident by default; graceful degradation when model or device is unavailable.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from host_brain.core.errors import (
    ASR_DEVICE_ERROR,
    ASR_GPU_OOM,
    ASR_MODEL_UNAVAILABLE,
    ASR_STREAM_INTERRUPTED,
    ASR_VAD_NO_SPEECH,
)
from host_brain.core.ids import new_request_id, now_ms
from edge_client.rpc.voice import VoiceRpcContract, VoiceRpcResult

logger = logging.getLogger("natsume.edge.asr")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MODEL_SIZE = "large-v3"
DEFAULT_DEVICE = "cuda"
DEFAULT_COMPUTE_TYPE = "float16"
DEFAULT_BEAM_SIZE = 5
DEFAULT_SAMPLE_RATE = 16000
DEFAULT_CHANNELS = 1
DEFAULT_CHUNK_DURATION_S = 0.5
DEFAULT_VAD_THRESHOLD = 0.5
DEFAULT_SILENCE_DURATION_S = 0.8
DEFAULT_MIN_SPEECH_DURATION_S = 0.3
DEFAULT_MAX_SPEECH_DURATION_S = 30.0


class AsrState(str, Enum):
    """ASR service lifecycle states."""
    UNLOADED = "UNLOADED"
    LOADING = "LOADING"
    READY = "READY"
    STREAMING = "STREAMING"
    ERROR = "ERROR"
    SHUTDOWN = "SHUTDOWN"


@dataclass
class AsrConfig:
    """ASR service configuration."""
    model_size: str = DEFAULT_MODEL_SIZE
    device: str = DEFAULT_DEVICE
    compute_type: str = DEFAULT_COMPUTE_TYPE
    beam_size: int = DEFAULT_BEAM_SIZE
    sample_rate: int = DEFAULT_SAMPLE_RATE
    channels: int = DEFAULT_CHANNELS
    chunk_duration_s: float = DEFAULT_CHUNK_DURATION_S
    vad_threshold: float = DEFAULT_VAD_THRESHOLD
    silence_duration_s: float = DEFAULT_SILENCE_DURATION_S
    min_speech_duration_s: float = DEFAULT_MIN_SPEECH_DURATION_S
    max_speech_duration_s: float = DEFAULT_MAX_SPEECH_DURATION_S
    language: str = "ja"
    initial_prompt: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_size": self.model_size,
            "device": self.device,
            "compute_type": self.compute_type,
            "beam_size": self.beam_size,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "chunk_duration_s": self.chunk_duration_s,
            "vad_threshold": self.vad_threshold,
            "silence_duration_s": self.silence_duration_s,
            "min_speech_duration_s": self.min_speech_duration_s,
            "max_speech_duration_s": self.max_speech_duration_s,
            "language": self.language,
        }


@dataclass
class TranscriptionSegment:
    """Single transcription segment from faster-whisper."""
    text: str
    start: float
    end: float
    confidence: float
    words: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class AsrResult:
    """Result of a single ASR transcription attempt."""
    success: bool
    reason_code: str | None
    detail: str
    segments: list[TranscriptionSegment] = field(default_factory=list)
    language: str = ""
    language_probability: float = 0.0
    duration_s: float = 0.0
    processing_time_s: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "reason_code": self.reason_code,
            "detail": self.detail,
            "segments": [
                {
                    "text": s.text,
                    "start": s.start,
                    "end": s.end,
                    "confidence": s.confidence,
                    "words": s.words,
                }
                for s in self.segments
            ],
            "language": self.language,
            "language_probability": self.language_probability,
            "duration_s": self.duration_s,
            "processing_time_s": self.processing_time_s,
        }


# ---------------------------------------------------------------------------
# Model Manager (lazy-load, GPU-resident, graceful degradation)
# ---------------------------------------------------------------------------

class WhisperModelManager:
    """Manages faster-whisper model lifecycle with GPU graceful degradation.

    Lazy-loads the model on first use. Handles GPU OOM by falling back to CPU.
    Thread-safe singleton pattern per instance.
    """

    def __init__(self, config: AsrConfig | None = None) -> None:
        self._config = config or AsrConfig()
        self._model: Any = None
        self._state = AsrState.UNLOADED
        self._lock = threading.Lock()
        self._actual_device: str = self._config.device
        self._load_error: str | None = None

    @property
    def state(self) -> AsrState:
        return self._state

    @property
    def actual_device(self) -> str:
        return self._actual_device

    def load(self) -> AsrResult:
        """Load the faster-whisper model. Thread-safe, idempotent."""
        with self._lock:
            if self._state == AsrState.READY and self._model is not None:
                return AsrResult(
                    success=True,
                    reason_code=None,
                    detail=f"model already loaded on {self._actual_device}",
                )

            self._state = AsrState.LOADING
            try:
                from faster_whisper import WhisperModel
            except ImportError as e:
                self._state = AsrState.ERROR
                self._load_error = str(e)
                logger.error("faster-whisper not installed: %s", e)
                return AsrResult(
                    success=False,
                    reason_code=ASR_MODEL_UNAVAILABLE,
                    detail=f"faster-whisper package not available: {e}",
                )

            # Try GPU first, fallback to CPU
            for device in self._device_fallback_chain():
                try:
                    self._model = WhisperModel(
                        self._config.model_size,
                        device=device,
                        compute_type=(
                            self._config.compute_type
                            if device == "cuda"
                            else "int8"
                        ),
                    )
                    self._actual_device = device
                    self._state = AsrState.READY
                    logger.info(
                        "Whisper model loaded: %s on %s",
                        self._config.model_size,
                        device,
                    )
                    return AsrResult(
                        success=True,
                        reason_code=None,
                        detail=f"model loaded on {device}",
                    )
                except Exception as e:
                    error_str = str(e).lower()
                    if "cuda" in error_str or "oom" in error_str or "gpu" in error_str:
                        logger.warning(
                            "GPU load failed (%s), trying next device: %s",
                            device,
                            e,
                        )
                        continue
                    # Non-GPU error, stop trying
                    self._state = AsrState.ERROR
                    self._load_error = str(e)
                    logger.error("Model load failed on %s: %s", device, e)
                    return AsrResult(
                        success=False,
                        reason_code=ASR_MODEL_UNAVAILABLE,
                        detail=f"model load failed: {e}",
                    )

            # All devices exhausted
            self._state = AsrState.ERROR
            self._load_error = "all devices exhausted"
            return AsrResult(
                success=False,
                reason_code=ASR_GPU_OOM,
                detail="model load failed on all devices (GPU OOM / unavailable)",
            )

    def transcribe(
        self,
        audio_data: Any,
        *,
        language: str | None = None,
        beam_size: int | None = None,
        initial_prompt: str | None = None,
        vad_filter: bool = True,
        vad_parameters: dict[str, Any] | None = None,
    ) -> AsrResult:
        """Transcribe audio data using loaded model.

        Args:
            audio_data: numpy array of audio samples (float32, mono, 16kHz).
            language: Override language code (e.g. 'ja', 'zh', 'en').
            beam_size: Override beam size for this transcription.
            vad_filter: Enable/disable VAD filtering.
            vad_parameters: Override VAD parameters.
        """
        if self._state != AsrState.READY or self._model is None:
            load_result = self.load()
            if not load_result.success:
                return load_result

        effective_vad_params = vad_parameters or {
            "threshold": self._config.vad_threshold,
            "min_silence_duration_ms": int(self._config.silence_duration_s * 1000),
            "min_speech_duration_ms": int(self._config.min_speech_duration_s * 1000),
        }

        t0 = time.monotonic()
        try:
            segments_gen, info = self._model.transcribe(
                audio_data,
                language=language or self._config.language,
                beam_size=beam_size or self._config.beam_size,
                initial_prompt=initial_prompt or self._config.initial_prompt,
                vad_filter=vad_filter,
                vad_parameters=effective_vad_params if vad_filter else None,
                word_timestamps=True,
            )

            segments: list[TranscriptionSegment] = []
            for seg in segments_gen:
                words = []
                if hasattr(seg, "words") and seg.words:
                    words = [
                        {
                            "word": w.word,
                            "start": w.start,
                            "end": w.end,
                            "probability": w.probability,
                        }
                        for w in seg.words
                    ]
                avg_prob = seg.avg_log_prob if hasattr(seg, "avg_log_prob") else 0.0
                # Convert log probability to confidence (0~1)
                import math
                confidence = math.exp(avg_prob) if avg_prob < 0 else min(avg_prob, 1.0)

                segments.append(
                    TranscriptionSegment(
                        text=seg.text.strip(),
                        start=seg.start,
                        end=seg.end,
                        confidence=round(confidence, 4),
                        words=words,
                    )
                )

            processing_time = time.monotonic() - t0

            if not segments:
                return AsrResult(
                    success=True,
                    reason_code=ASR_VAD_NO_SPEECH,
                    detail="no speech detected in audio",
                    segments=[],
                    language=getattr(info, "language", ""),
                    language_probability=getattr(info, "language_probability", 0.0),
                    duration_s=getattr(info, "duration", 0.0),
                    processing_time_s=round(processing_time, 4),
                )

            return AsrResult(
                success=True,
                reason_code=None,
                detail=f"transcribed {len(segments)} segment(s)",
                segments=segments,
                language=getattr(info, "language", ""),
                language_probability=getattr(info, "language_probability", 0.0),
                duration_s=getattr(info, "duration", 0.0),
                processing_time_s=round(processing_time, 4),
            )

        except Exception as e:
            processing_time = time.monotonic() - t0
            error_str = str(e).lower()
            if "oom" in error_str or "cuda" in error_str:
                reason = ASR_GPU_OOM
            else:
                reason = ASR_MODEL_UNAVAILABLE
            logger.error("Transcription failed: %s", e)
            return AsrResult(
                success=False,
                reason_code=reason,
                detail=f"transcription error: {e}",
                processing_time_s=round(processing_time, 4),
            )

    def unload(self) -> None:
        """Unload model and free GPU memory."""
        with self._lock:
            self._model = None
            self._state = AsrState.UNLOADED
            logger.info("Whisper model unloaded")

    def health(self) -> dict[str, Any]:
        """Return model health snapshot."""
        return {
            "state": self._state.value,
            "model_size": self._config.model_size,
            "device": self._actual_device,
            "config_device": self._config.device,
            "load_error": self._load_error,
        }

    def _device_fallback_chain(self) -> list[str]:
        """Return ordered device list for fallback loading."""
        if self._config.device == "cuda":
            return ["cuda", "cpu"]
        return [self._config.device]


# ---------------------------------------------------------------------------
# Audio Stream Capture
# ---------------------------------------------------------------------------

class AudioStreamCapture:
    """Captures audio from microphone in chunks for ASR processing.

    Uses sounddevice for cross-platform audio input. Falls back gracefully
    when audio device is unavailable.
    """

    def __init__(
        self,
        *,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        channels: int = DEFAULT_CHANNELS,
        chunk_duration_s: float = DEFAULT_CHUNK_DURATION_S,
        device_index: int | None = None,
    ) -> None:
        self._sample_rate = sample_rate
        self._channels = channels
        self._chunk_duration_s = chunk_duration_s
        self._device_index = device_index
        self._stream: Any = None
        self._running = False
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._running

    def start(self, callback: Callable[[Any, int], None]) -> AsrResult:
        """Start audio capture stream.

        Args:
            callback: Called with (audio_chunk_ndarray, timestamp_ms) for each chunk.
        """
        with self._lock:
            if self._running:
                return AsrResult(
                    success=True,
                    reason_code=None,
                    detail="audio stream already running",
                )

            try:
                import sounddevice as sd
                import numpy as np
            except ImportError as e:
                logger.error("sounddevice/numpy not installed: %s", e)
                return AsrResult(
                    success=False,
                    reason_code=ASR_DEVICE_ERROR,
                    detail=f"audio capture dependency not available: {e}",
                )

            chunk_samples = int(self._sample_rate * self._chunk_duration_s)

            def _audio_callback(indata: Any, frames: int, time_info: Any, status: Any) -> None:
                if status:
                    logger.warning("Audio stream status: %s", status)
                ts = int(now_ms())
                # Copy data to avoid buffer reuse issues
                chunk = np.copy(indata[:, 0] if indata.ndim > 1 else indata).flatten()
                callback(chunk, ts)

            try:
                self._stream = sd.InputStream(
                    samplerate=self._sample_rate,
                    channels=self._channels,
                    dtype="float32",
                    blocksize=chunk_samples,
                    device=self._device_index,
                    callback=_audio_callback,
                )
                self._stream.start()
                self._running = True
                logger.info(
                    "Audio capture started: rate=%d, channels=%d, chunk=%.2fs",
                    self._sample_rate,
                    self._channels,
                    self._chunk_duration_s,
                )
                return AsrResult(
                    success=True,
                    reason_code=None,
                    detail="audio capture started",
                )
            except Exception as e:
                logger.error("Failed to start audio capture: %s", e)
                return AsrResult(
                    success=False,
                    reason_code=ASR_DEVICE_ERROR,
                    detail=f"audio device error: {e}",
                )

    def stop(self) -> AsrResult:
        """Stop audio capture stream."""
        with self._lock:
            if not self._running:
                return AsrResult(
                    success=True,
                    reason_code=None,
                    detail="audio stream not running",
                )

            try:
                if self._stream is not None:
                    self._stream.stop()
                    self._stream.close()
                    self._stream = None
                self._running = False
                logger.info("Audio capture stopped")
                return AsrResult(
                    success=True,
                    reason_code=None,
                    detail="audio capture stopped",
                )
            except Exception as e:
                self._running = False
                logger.error("Error stopping audio capture: %s", e)
                return AsrResult(
                    success=False,
                    reason_code=ASR_STREAM_INTERRUPTED,
                    detail=f"error stopping stream: {e}",
                )

    def health(self) -> dict[str, Any]:
        """Return audio capture health snapshot."""
        return {
            "running": self._running,
            "sample_rate": self._sample_rate,
            "channels": self._channels,
            "chunk_duration_s": self._chunk_duration_s,
            "device_index": self._device_index,
        }


# ---------------------------------------------------------------------------
# ASR Service (orchestrates model + audio + RPC)
# ---------------------------------------------------------------------------

class AsrService:
    """End-to-end ASR service: audio capture -> transcription -> asr_chunk events.

    Orchestrates WhisperModelManager, AudioStreamCapture, and VoiceRpcContract
    into a unified pipeline. Manages session state, sequence numbering, and
    accumulated audio buffer for VAD-based segmentation.
    """

    def __init__(
        self,
        *,
        rpc: VoiceRpcContract,
        config: AsrConfig | None = None,
        model_manager: WhisperModelManager | None = None,
        audio_capture: AudioStreamCapture | None = None,
    ) -> None:
        self._config = config or AsrConfig()
        self._rpc = rpc
        self._model = model_manager or WhisperModelManager(self._config)
        self._audio = audio_capture or AudioStreamCapture(
            sample_rate=self._config.sample_rate,
            channels=self._config.channels,
            chunk_duration_s=self._config.chunk_duration_s,
        )
        self._session_id: str = ""
        self._seq: int = 0
        self._lock = threading.Lock()
        self._audio_buffer: list[Any] = []
        self._buffer_start_ts: int = 0
        self._running = False

    @property
    def state(self) -> AsrState:
        return self._model.state

    def start_session(self, session_id: str | None = None) -> AsrResult:
        """Initialize ASR model and start audio capture for a session."""
        with self._lock:
            self._session_id = session_id or f"asr_{new_request_id()}"
            self._seq = 0
            self._audio_buffer = []
            self._buffer_start_ts = 0

        # Load model first
        load_result = self._model.load()
        if not load_result.success:
            return load_result

        # Start audio capture
        capture_result = self._audio.start(callback=self._on_audio_chunk)
        if not capture_result.success:
            return capture_result

        self._running = True
        logger.info("ASR session started: %s", self._session_id)
        return AsrResult(
            success=True,
            reason_code=None,
            detail=f"ASR session started: {self._session_id}",
        )

    def stop_session(self) -> AsrResult:
        """Stop audio capture and flush remaining buffer."""
        self._running = False
        # Flush any remaining audio
        self._flush_buffer(final=True)
        result = self._audio.stop()
        logger.info("ASR session stopped: %s", self._session_id)
        return result

    def transcribe_audio(
        self,
        audio_data: Any,
        *,
        request_id: str | None = None,
        ts_ms: int | None = None,
        final: bool = False,
    ) -> list[VoiceRpcResult]:
        """Manually transcribe audio data and emit asr_chunk events.

        This is the synchronous API for non-streaming use cases
        (e.g. testing or file-based transcription).
        """
        req_id = request_id or new_request_id()
        timestamp = ts_ms or int(now_ms())

        asr_result = self._model.transcribe(audio_data)
        if not asr_result.success:
            # Emit a failed event through RPC
            return [
                self._emit_rpc_event(
                    request_id=req_id,
                    text="",
                    confidence=0.0,
                    start_ms=0,
                    end_ms=0,
                    alternates=[],
                    final=final,
                    ts_ms=timestamp,
                    reason_code=asr_result.reason_code,
                )
            ]

        results: list[VoiceRpcResult] = []
        for seg in asr_result.segments:
            alternates: list[dict[str, Any]] = []
            if seg.words:
                # Use word-level data as alternates
                for w in seg.words[:3]:
                    alternates.append({
                        "text": w.get("word", ""),
                        "confidence": round(w.get("probability", 0.0), 4),
                    })

            rpc_result = self._emit_rpc_event(
                request_id=req_id,
                text=seg.text,
                confidence=seg.confidence,
                start_ms=int(seg.start * 1000),
                end_ms=int(seg.end * 1000),
                alternates=alternates,
                final=(seg == asr_result.segments[-1] and final),
                ts_ms=timestamp,
            )
            results.append(rpc_result)

        if not results:
            # No speech detected, emit empty event
            results.append(
                self._emit_rpc_event(
                    request_id=req_id,
                    text="",
                    confidence=0.0,
                    start_ms=0,
                    end_ms=0,
                    alternates=[],
                    final=final,
                    ts_ms=timestamp,
                    reason_code=ASR_VAD_NO_SPEECH,
                )
            )

        return results

    def health(self) -> dict[str, Any]:
        """Return ASR service health snapshot."""
        return {
            "service": "edge.asr",
            "session_id": self._session_id,
            "seq": self._seq,
            "running": self._running,
            "model": self._model.health(),
            "audio": self._audio.health(),
            "config": self._config.as_dict(),
        }

    # -- Internal --

    def _on_audio_chunk(self, audio_chunk: Any, ts_ms: int) -> None:
        """Callback from AudioStreamCapture. Buffers audio and triggers
        transcription when enough audio is accumulated."""
        if not self._running:
            return

        with self._lock:
            if not self._audio_buffer:
                self._buffer_start_ts = ts_ms
            self._audio_buffer.append(audio_chunk)

        # Check if buffer exceeds max speech duration
        buffer_duration = (ts_ms - self._buffer_start_ts) / 1000.0
        if buffer_duration >= self._config.max_speech_duration_s:
            self._flush_buffer(final=False)
        elif buffer_duration >= self._config.silence_duration_s:
            # Try VAD-based flush
            self._flush_buffer(final=False)

    def _flush_buffer(self, final: bool = False) -> None:
        """Flush accumulated audio buffer through transcription."""
        with self._lock:
            if not self._audio_buffer:
                return
            buffer_copy = list(self._audio_buffer)
            self._audio_buffer = []
            start_ts = self._buffer_start_ts

        try:
            import numpy as np
            audio_data = np.concatenate(buffer_copy)
        except (ImportError, ValueError):
            return

        req_id = new_request_id()
        self.transcribe_audio(
            audio_data,
            request_id=req_id,
            ts_ms=start_ts,
            final=final,
        )

    def _emit_rpc_event(
        self,
        *,
        request_id: str,
        text: str,
        confidence: float,
        start_ms: int,
        end_ms: int,
        alternates: list[dict[str, Any]],
        final: bool,
        ts_ms: int,
        reason_code: str | None = None,
    ) -> VoiceRpcResult:
        """Emit an asr_chunk event through VoiceRpcContract."""
        with self._lock:
            self._seq += 1
            seq = self._seq
            session_id = self._session_id

        payload: dict[str, Any] = {
            "session_id": session_id,
            "text": text,
            "seq": seq,
            "confidence": confidence,
            "t_start_ms": start_ms,
            "t_end_ms": end_ms,
            "alternates": alternates,
            "final": final,
        }

        # If there's a failure reason, we still emit through RPC
        # but text may be empty
        if reason_code and not text:
            payload["text"] = f"[{reason_code}]"
            payload["confidence"] = 0.0

        return self._rpc.asr_chunk(
            request_id=request_id,
            payload=payload,
            ts_ms=ts_ms,
        )


# ---------------------------------------------------------------------------
# Convenience function (backward-compatible)
# ---------------------------------------------------------------------------

def emit_asr_chunk(
    *,
    rpc: VoiceRpcContract,
    request_id: str,
    payload: dict[str, Any],
    ts_ms: int | None = None,
) -> VoiceRpcResult:
    """Emit a single asr_chunk event (backward-compatible thin adapter)."""
    return rpc.asr_chunk(
        request_id=request_id,
        payload=payload,
        ts_ms=ts_ms,
    )
