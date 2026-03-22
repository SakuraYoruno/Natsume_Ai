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
    ASR_DEVICE_RECOVERED,
    ASR_DEVICE_RECOVERY_EXHAUSTED,
    ASR_GPU_OOM,
    ASR_MODEL_UNAVAILABLE,
    ASR_STREAM_INTERRUPTED,
    ASR_VAD_NO_SPEECH,
)
from host_brain.core.ids import new_request_id, now_ms
from edge_client.rpc.voice import VoiceRpcContract, VoiceRpcResult

logger = logging.getLogger("Natsume.edge.asr")

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

# ── 设备恢复常量 ──
DEFAULT_RECOVERY_MAX_ATTEMPTS = 5
DEFAULT_RECOVERY_BASE_DELAY_S = 1.0
DEFAULT_RECOVERY_MAX_DELAY_S = 8.0


class AsrState(str, Enum):
    """ASR service lifecycle states."""
    UNLOADED = "UNLOADED"
    LOADING = "LOADING"
    READY = "READY"
    STREAMING = "STREAMING"
    RECOVERING = "RECOVERING"
    ERROR = "ERROR"
    SHUTDOWN = "SHUTDOWN"


@dataclass
class DeviceRecoveryConfig:
    """ASR 麦克风设备自动恢复配置。"""
    max_attempts: int = DEFAULT_RECOVERY_MAX_ATTEMPTS
    base_delay_s: float = DEFAULT_RECOVERY_BASE_DELAY_S
    max_delay_s: float = DEFAULT_RECOVERY_MAX_DELAY_S
    auto_recover: bool = True
    try_alternate_devices: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_attempts": self.max_attempts,
            "base_delay_s": self.base_delay_s,
            "max_delay_s": self.max_delay_s,
            "auto_recover": self.auto_recover,
            "try_alternate_devices": self.try_alternate_devices,
        }


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
    vad_retry_without_filter_on_empty: bool = True
    vad_retry_min_peak: float = 0.015
    language: str = "ja"
    initial_prompt: str | None = None
    recovery: DeviceRecoveryConfig = field(default_factory=DeviceRecoveryConfig)

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
            "vad_retry_without_filter_on_empty": self.vad_retry_without_filter_on_empty,
            "vad_retry_min_peak": self.vad_retry_min_peak,
            "language": self.language,
            "recovery": self.recovery.as_dict(),
        }


@dataclass
class TranscriptionSegment:
    """Single transcription segment from faster-whisper."""
    text: str
    start: float
    end: float
    confidence: float
    words: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# M15-A: ASRTranscriptEvent（确定性 ASR 事件 + 审计串联）
# ---------------------------------------------------------------------------

LOW_CONFIDENCE_THRESHOLD = 0.6
MAX_ALTERNATES = 3


@dataclass(frozen=True)
class ASRTranscriptEvent:
    """
    ASR 转写事件（M15-A）。
    - 低置信度显式标注（low_confidence=True + reason_code=ASR_LOW_CONFIDENCE）
    - 每个事件携带 evidence_ref（可追溯）
    - alternates 最多 3 个（N-best）
    """
    event_id: str
    session_id: str
    segment_id: str
    text: str
    confidence: float               # 0.0~1.0
    ts_start_ms: int
    ts_end_ms: int
    is_final: bool
    alternates: tuple[str, ...]     # N-best（最多 3 个）
    evidence_ref: str               # 审计链引用（必填）
    low_confidence: bool            # confidence < LOW_CONFIDENCE_THRESHOLD 时为 True
    reason_code: str | None         # 低置信度时填 ASR_LOW_CONFIDENCE
    # M29-P（OPT-?）：用户情感推断字段（从语音韵律/文本推断）
    user_emotion_hint: str | None = None        # 推断的用户情感标签（如 "SAD"/"HAPPY"）
    user_emotion_confidence: float | None = None  # 情感推断置信度（0.0~1.0）

    def as_dict(self) -> dict[str, Any]:
        result = {
            "event_id": self.event_id,
            "session_id": self.session_id,
            "segment_id": self.segment_id,
            "text": self.text,
            "confidence": self.confidence,
            "ts_start_ms": self.ts_start_ms,
            "ts_end_ms": self.ts_end_ms,
            "is_final": self.is_final,
            "alternates": list(self.alternates),
            "evidence_ref": self.evidence_ref,
            "low_confidence": self.low_confidence,
            "reason_code": self.reason_code,
        }
        # M29-P：仅在有情感推断时输出
        if self.user_emotion_hint is not None:
            result["user_emotion_hint"] = self.user_emotion_hint
        if self.user_emotion_confidence is not None:
            result["user_emotion_confidence"] = self.user_emotion_confidence
        return result


def build_asr_transcript_event(
    *,
    session_id: str,
    segment_id: str,
    text: str,
    confidence: float,
    ts_start_ms: int,
    ts_end_ms: int,
    is_final: bool = True,
    alternates: list[str] | None = None,
    evidence_ref: str | None = None,
    event_id: str | None = None,
) -> ASRTranscriptEvent:
    """
    工厂函数：构建 ASRTranscriptEvent。

    - evidence_ref 必填（缺失时抛出 ValueError）
    - alternates 最多 3 个（超出时截断）
    - 低置信度自动标注 low_confidence=True + reason_code=ASR_LOW_CONFIDENCE
    """
    from host_brain.core.errors import ASR_LOW_CONFIDENCE as _ASR_LOW_CONFIDENCE
    from host_brain.core.ids import new_request_id as _new_request_id

    if not evidence_ref or not str(evidence_ref).strip():
        raise ValueError("ASRTranscriptEvent requires evidence_ref")

    # alternates 截断到最多 3 个
    safe_alternates = tuple((alternates or [])[:MAX_ALTERNATES])

    low_conf = float(confidence) < LOW_CONFIDENCE_THRESHOLD
    code = _ASR_LOW_CONFIDENCE if low_conf else None

    return ASRTranscriptEvent(
        event_id=event_id or _new_request_id(),
        session_id=str(session_id),
        segment_id=str(segment_id),
        text=str(text),
        confidence=float(confidence),
        ts_start_ms=int(ts_start_ms),
        ts_end_ms=int(ts_end_ms),
        is_final=bool(is_final),
        alternates=safe_alternates,
        evidence_ref=str(evidence_ref),
        low_confidence=low_conf,
        reason_code=code,
    )


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
        # ── 设备恢复状态（仅跟踪，WhisperModelManager 不执行恢复） ──
        self._recovery_count = 0

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
            def _collect_segments(segments_gen: Any) -> list[TranscriptionSegment]:
                rows: list[TranscriptionSegment] = []
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
                    rows.append(
                        TranscriptionSegment(
                            text=seg.text.strip(),
                            start=seg.start,
                            end=seg.end,
                            confidence=round(confidence, 4),
                            words=words,
                        )
                    )
                return rows

            def _run_once(*, use_vad_filter: bool) -> tuple[list[TranscriptionSegment], Any]:
                seg_gen, info_obj = self._model.transcribe(
                    audio_data,
                    language=language or self._config.language,
                    beam_size=beam_size or self._config.beam_size,
                    initial_prompt=initial_prompt or self._config.initial_prompt,
                    vad_filter=use_vad_filter,
                    vad_parameters=effective_vad_params if use_vad_filter else None,
                    word_timestamps=True,
                )
                return _collect_segments(seg_gen), info_obj

            attempted_vad_retry = False
            measured_peak = 0.0
            segments, info = _run_once(use_vad_filter=vad_filter)

            # Some microphones produce valid speech frames that faster-whisper VAD discards.
            # Retry once without VAD when audio peak suggests non-silent input.
            if (
                not segments
                and vad_filter
                and self._config.vad_retry_without_filter_on_empty
            ):
                measured_peak = self._estimate_audio_peak(audio_data)
                if measured_peak >= self._config.vad_retry_min_peak:
                    attempted_vad_retry = True
                    logger.info(
                        "ASR empty after VAD; retry without VAD (peak=%.5f, min=%.5f)",
                        measured_peak,
                        self._config.vad_retry_min_peak,
                    )
                    segments, info = _run_once(use_vad_filter=False)

            processing_time = time.monotonic() - t0

            if not segments:
                detail = "no speech detected in audio"
                if attempted_vad_retry:
                    detail = (
                        "no speech detected in audio (retried without VAD, "
                        f"peak={measured_peak:.5f})"
                    )
                elif vad_filter and self._config.vad_retry_without_filter_on_empty:
                    detail = (
                        "no speech detected in audio (skip no-VAD retry, "
                        f"peak={measured_peak:.5f} < min={self._config.vad_retry_min_peak:.5f})"
                    )
                return AsrResult(
                    success=True,
                    reason_code=ASR_VAD_NO_SPEECH,
                    detail=detail,
                    segments=[],
                    language=getattr(info, "language", ""),
                    language_probability=getattr(info, "language_probability", 0.0),
                    duration_s=getattr(info, "duration", 0.0),
                    processing_time_s=round(processing_time, 4),
                )

            detail = f"transcribed {len(segments)} segment(s)"
            if attempted_vad_retry:
                detail = f"{detail} (after no-VAD retry)"
            return AsrResult(
                success=True,
                reason_code=None,
                detail=detail,
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
            "recovery_count": self._recovery_count,
        }

    def _device_fallback_chain(self) -> list[str]:
        """Return ordered device list for fallback loading."""
        if self._config.device == "cuda":
            return ["cuda", "cpu"]
        return [self._config.device]

    @staticmethod
    def _estimate_audio_peak(audio_data: Any) -> float:
        try:
            import numpy as np

            arr = np.asarray(audio_data, dtype=float)
            if arr.size == 0:
                return 0.0
            arr = np.abs(arr)
            if arr.ndim > 1:
                arr = np.max(arr, axis=1)
            return float(np.max(arr))
        except Exception:
            pass
        try:
            if isinstance(audio_data, (list, tuple)):
                if not audio_data:
                    return 0.0
                return max(abs(float(v)) for v in audio_data if isinstance(v, (int, float)))
            if isinstance(audio_data, (int, float)):
                return abs(float(audio_data))
        except Exception:
            return 0.0
        return 0.0


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
        self._original_device_index = device_index
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

    def set_device_index(self, device_index: int | None) -> None:
        """切换音频设备索引（需先 stop 再 start）。"""
        self._device_index = device_index

    @staticmethod
    def enumerate_input_devices() -> list[dict[str, Any]]:
        """列举所有可用音频输入设备。

        返回 [{"index": int, "name": str, "max_input_channels": int, ...}]
        静默失败时返回空列表。
        """
        try:
            import sounddevice as sd
            devices = sd.query_devices()
            result: list[dict[str, Any]] = []
            if isinstance(devices, dict):
                devices = [devices]
            for i, d in enumerate(devices):
                if isinstance(d, dict) and d.get("max_input_channels", 0) > 0:
                    result.append({
                        "index": i,
                        "name": d.get("name", ""),
                        "max_input_channels": d.get("max_input_channels", 0),
                        "default_samplerate": d.get("default_samplerate", 0),
                    })
            return result
        except Exception as exc:
            logger.warning("Failed to enumerate audio devices: %s", exc)
            return []

    def health(self) -> dict[str, Any]:
        """Return audio capture health snapshot."""
        return {
            "running": self._running,
            "sample_rate": self._sample_rate,
            "channels": self._channels,
            "chunk_duration_s": self._chunk_duration_s,
            "device_index": self._device_index,
            "original_device_index": self._original_device_index,
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
        rpc_event_callback: Callable[[str, VoiceRpcResult], None] | None = None,
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
        self._rpc_event_callback = rpc_event_callback
        # ── 设备恢复状态 ──
        self._recovery_thread: threading.Thread | None = None
        self._recovery_attempts = 0
        self._total_recoveries = 0
        self._total_recovery_failures = 0
        self._recovery_events: list[dict[str, Any]] = []
        self._shutdown_event = threading.Event()
        self._last_callback: Callable[[Any, int], None] | None = None

    @property
    def state(self) -> AsrState:
        return self._model.state

    def set_rpc_event_callback(
        self,
        callback: Callable[[str, VoiceRpcResult], None] | None,
    ) -> None:
        self._rpc_event_callback = callback

    def startup(self) -> None:
        """Edge 启动时由 bootstrap.startup() 通过 _invoke_lifecycle 自动调用。

        内部调用 start_session() 完成模型加载和麦克风监听启动。
        失败时仅记录警告，不抛出异常，保证其他组件正常启动。
        """
        result = self.start_session()
        if not result.success:
            logger.warning(
                "ASR startup failed (reason=%s): %s",
                result.reason_code,
                result.detail,
            )

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
        self._last_callback = self._on_audio_chunk
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
        self._shutdown_event.set()
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
            "recovery": {
                "total_recoveries": self._total_recoveries,
                "total_recovery_failures": self._total_recovery_failures,
                "recovery_attempts": self._recovery_attempts,
                "recovering": self._model.state == AsrState.RECOVERING,
                "recovery_events_count": len(self._recovery_events),
            },
        }

    # -----------------------------------------------------------------
    # 麦克风设备自动恢复（TASK-0081）
    # -----------------------------------------------------------------

    def _device_recovery_loop(self) -> None:
        """指数退避设备恢复循环。

        在独立线程中运行。先尝试重新打开原始设备，若失败则枚举
        备用输入设备逐一尝试。恢复成功后上报 ASR_DEVICE_RECOVERED，
        全部失败后上报 ASR_DEVICE_RECOVERY_EXHAUSTED。
        """
        rc = self._config.recovery
        delay = rc.base_delay_s
        self._recovery_attempts = 0

        with self._lock:
            prev_state = self._model.state

        self._model._state = AsrState.RECOVERING

        logger.info(
            "ASR device recovery started (max_attempts=%d, base_delay=%.1fs)",
            rc.max_attempts, rc.base_delay_s,
        )

        for attempt in range(1, rc.max_attempts + 1):
            if self._shutdown_event.is_set():
                logger.info("ASR device recovery cancelled by shutdown")
                return

            self._recovery_attempts = attempt

            # ── 尝试设备列表：原始设备优先，然后备用 ──
            devices_to_try = self._build_device_candidates(rc)

            for dev_info in devices_to_try:
                dev_idx = dev_info["index"]
                dev_name = dev_info.get("name", f"device_{dev_idx}")

                logger.info(
                    "ASR recovery attempt %d/%d, device=%s (index=%s, delay=%.1fs)",
                    attempt, rc.max_attempts, dev_name, dev_idx, delay,
                )

                try:
                    # 先停再重新打开
                    self._audio.stop()
                    self._audio.set_device_index(dev_idx)

                    if self._last_callback is not None:
                        start_result = self._audio.start(callback=self._last_callback)
                    else:
                        start_result = self._audio.start(callback=self._on_audio_chunk)

                    if start_result.success:
                        self._total_recoveries += 1
                        self._model._state = AsrState.READY
                        self._running = True

                        event = {
                            "event": ASR_DEVICE_RECOVERED,
                            "attempt": attempt,
                            "device_index": dev_idx,
                            "device_name": dev_name,
                            "ts_ms": now_ms(),
                            "detail": f"device recovered after {attempt} attempt(s) on {dev_name}",
                        }
                        self._recovery_events.append(event)
                        logger.info(
                            "ASR device recovered after %d attempt(s) on %s",
                            attempt, dev_name,
                        )
                        return
                except Exception as exc:
                    logger.warning(
                        "ASR recovery attempt %d device %s failed: %s",
                        attempt, dev_name, exc,
                    )

            # 本轮所有设备均失败，等待退避时间
            if self._shutdown_event.wait(timeout=min(delay, rc.max_delay_s)):
                logger.info("ASR device recovery cancelled by shutdown during wait")
                return
            delay = min(delay * 2, rc.max_delay_s)

        # 全部恢复失败
        self._total_recovery_failures += 1
        self._model._state = AsrState.ERROR
        self._running = False

        event = {
            "event": ASR_DEVICE_RECOVERY_EXHAUSTED,
            "attempts": rc.max_attempts,
            "ts_ms": now_ms(),
            "detail": f"all {rc.max_attempts} device recovery attempts exhausted",
        }
        self._recovery_events.append(event)
        logger.error(
            "ASR device recovery exhausted after %d attempts", rc.max_attempts,
        )

    def _build_device_candidates(
        self, rc: DeviceRecoveryConfig,
    ) -> list[dict[str, Any]]:
        """构建设备候选列表：原始设备优先，然后备用设备。"""
        original_idx = self._audio._original_device_index
        candidates: list[dict[str, Any]] = [
            {"index": original_idx, "name": f"original_device_{original_idx}"},
        ]

        if rc.try_alternate_devices:
            available = AudioStreamCapture.enumerate_input_devices()
            for dev in available:
                if dev["index"] != original_idx:
                    candidates.append(dev)

        return candidates

    def trigger_device_recovery(self) -> AsrResult:
        """触发设备自动恢复（非阻塞）。

        通常由 audio stream error 自动触发，也可手动调用。
        如果已在恢复中则返回 no-op。
        """
        rc = self._config.recovery
        if not rc.auto_recover:
            return AsrResult(
                success=False,
                reason_code=ASR_DEVICE_ERROR,
                detail="auto_recover is disabled",
            )

        if self._model.state == AsrState.RECOVERING:
            return AsrResult(
                success=True,
                reason_code=None,
                detail="device recovery already in progress",
            )

        if self._model.state == AsrState.SHUTDOWN:
            return AsrResult(
                success=False,
                reason_code=ASR_DEVICE_ERROR,
                detail="service is shutdown",
            )

        self._shutdown_event.clear()
        self._recovery_thread = threading.Thread(
            target=self._device_recovery_loop,
            name="asr-device-recovery",
            daemon=True,
        )
        self._recovery_thread.start()

        return AsrResult(
            success=True,
            reason_code=None,
            detail="device recovery started",
        )

    def _maybe_trigger_recovery(self, reason: str) -> None:
        """音频流错误时检查是否需要自动恢复。"""
        rc = self._config.recovery
        if not rc.auto_recover:
            return
        if self._model.state in (AsrState.RECOVERING, AsrState.SHUTDOWN):
            return
        logger.warning("ASR device error (%s), triggering recovery", reason)
        self.trigger_device_recovery()

    @property
    def recovery_events(self) -> list[dict[str, Any]]:
        """返回设备恢复事件日志（可写入审计）。"""
        return list(self._recovery_events)

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

        rpc_result = self._rpc.asr_chunk(
            request_id=request_id,
            payload=payload,
            ts_ms=ts_ms,
        )
        if self._rpc_event_callback is not None:
            try:
                self._rpc_event_callback(request_id, rpc_result)
            except Exception as exc:  # pragma: no cover - callback safety
                logger.warning("ASR rpc event callback failed: %s", exc)
        return rpc_result


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


# ---------------------------------------------------------------------------
# M10-B: Canon 热词注入（ASR 热词 ← World Canon SSOT）
# M10-E: _CANON_HOTWORDS_CACHE 增加 TTL 自动失效（F-42）
# ---------------------------------------------------------------------------

class _HotwordsCache:
    """带 TTL 自动失效的热词缓存（M10-E F-42）。

    线程安全：使用 threading.Lock 保护读写操作。
    TTL 到期后，get() 返回空列表，下次 inject_canon_hotwords() 调用时自动刷新。
    """

    def __init__(self, ttl_s: float = 300.0) -> None:
        """
        Args:
            ttl_s: 缓存有效期（秒），默认 300s（5 分钟）。
        """
        self._hotwords: list[str] = []
        self._ttl_s = ttl_s
        self._updated_at: float = 0.0
        self._lock = threading.Lock()

    def set(self, hotwords: list[str]) -> None:
        """更新热词列表并重置 TTL 计时。"""
        with self._lock:
            self._hotwords = list(hotwords)
            self._updated_at = time.monotonic()

    def get(self) -> list[str]:
        """返回热词列表只读副本；TTL 到期时返回空列表。"""
        with self._lock:
            if self._is_expired():
                return []
            return list(self._hotwords)

    def clear(self) -> None:
        """清空缓存并重置计时。"""
        with self._lock:
            self._hotwords = []
            self._updated_at = 0.0

    def is_valid(self) -> bool:
        """返回缓存是否在有效期内且非空。"""
        with self._lock:
            return bool(self._hotwords) and not self._is_expired()

    def age_s(self) -> float:
        """返回缓存已存活秒数（0 表示从未更新）。"""
        with self._lock:
            if self._updated_at == 0.0:
                return 0.0
            return time.monotonic() - self._updated_at

    def _is_expired(self) -> bool:
        """内部：判断是否已过期（调用方需持有锁）。"""
        if self._updated_at == 0.0:
            return True
        return (time.monotonic() - self._updated_at) > self._ttl_s


# 模块级热词缓存（TTL=300s，M10-E F-42）
_CANON_HOTWORDS_CACHE = _HotwordsCache(ttl_s=300.0)

def inject_canon_hotwords(
    host_url: str,
    *,
    max_words: int = 200,
    timeout_s: float = 2.0,
) -> int:
    """从 Host `GET /canon/hotwords` 拉取热词并注入到 ASR 引擎。

    M10-B FR-WCANON-001：Edge ASR 感知层扩展，将 World Canon 关键词注入热词表，
    提升专有名词（人名/地名/术语）的语音识别准确率。

    Args:
        host_url: Host Brain 服务地址（例如 "http://localhost:8000"）。
        max_words: 最大热词数量（默认 200）。
        timeout_s: HTTP 请求超时秒数（默认 2.0s）。

    Returns:
        成功注入的热词数量。Host 不可达或热词列表为空时返回 0（降级，不抛异常）。
    """
    try:
        import urllib.request
        import json as _json

        url = f"{host_url.rstrip('/')}/canon/hotwords?max_words={max_words}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})

        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            if resp.status != 200:
                logger.warning(
                    "inject_canon_hotwords: unexpected status %d from %s",
                    resp.status, url,
                )
                return 0
            data = _json.loads(resp.read().decode("utf-8"))

        hotwords: list[str] = data.get("hotwords", [])
        if not hotwords:
            return 0

        # 注入到模块级热词缓存（M10-E：TTL 自动失效，300s 后过期）
        _CANON_HOTWORDS_CACHE.set(hotwords)

        logger.info(
            "inject_canon_hotwords: injected %d hotwords from %s (TTL=%.0fs)",
            len(hotwords), host_url, _CANON_HOTWORDS_CACHE._ttl_s,
        )
        return len(hotwords)

    except Exception as exc:
        logger.warning("inject_canon_hotwords: failed to fetch hotwords: %s", exc)
        return 0


# 模块级热词缓存已在上方 _HotwordsCache 类定义处初始化（M10-E F-42）


def get_canon_hotwords() -> list[str]:
    """返回当前缓存的 Canon 热词列表（只读副本）。

    TTL 到期后返回空列表，下次 inject_canon_hotwords() 调用时自动刷新。
    """
    return _CANON_HOTWORDS_CACHE.get()
