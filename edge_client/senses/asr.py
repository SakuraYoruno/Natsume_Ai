"""Edge ASR service: faster-whisper SDK integration with audio capture and VAD.

Provides the full pipeline: microphone input -> VAD segmentation -> faster-whisper
transcription -> asr_chunk event emission via VoiceRpcContract.

GPU resident by default; graceful degradation when model or device is unavailable.
"""

from __future__ import annotations

import logging
import json
import os
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


def _short_log_text(value: Any, *, limit: int = 160) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 3)]}..."


def _format_ts_ms(ts_ms: int) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts_ms) / 1000.0))
    except Exception:
        return "unknown"

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
DEFAULT_PRECHECK_MIN_PEAK = 0.01
DEFAULT_PRECHECK_MIN_RMS = 0.002

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
    precheck_min_peak: float = DEFAULT_PRECHECK_MIN_PEAK
    precheck_min_rms: float = DEFAULT_PRECHECK_MIN_RMS
    use_vad_filter: bool = True
    vad_retry_without_filter_on_empty: bool = True
    vad_retry_min_peak: float = 0.015
    adaptive_vad_enabled: bool = True
    adaptive_vad_bootstrap_chunks: int = 4
    adaptive_vad_peak_ratio: float = 1.8
    adaptive_vad_rms_ratio: float = 1.7
    adaptive_vad_noise_alpha: float = 0.08
    language: str = "zh"
    initial_prompt: str | None = None
    barge_in_on_speech_enabled: bool = True
    barge_in_min_active_chunks: int = 2
    barge_in_cooldown_ms: int = 1200
    barge_in_min_peak: float = 0.035
    barge_in_min_rms: float = 0.006
    barge_in_voiceprint_window_chunks: int = 4
    barge_in_require_voiceprint_during_playback: bool = True
    playback_echo_suppression_enabled: bool = True
    speaker_verification_enabled: bool = False
    speaker_verification_fail_open: bool = True
    speaker_verification_min_confidence: float = 0.68
    speaker_profile_path: str | None = None
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
            "precheck_min_peak": self.precheck_min_peak,
            "precheck_min_rms": self.precheck_min_rms,
            "use_vad_filter": self.use_vad_filter,
            "vad_retry_without_filter_on_empty": self.vad_retry_without_filter_on_empty,
            "vad_retry_min_peak": self.vad_retry_min_peak,
            "adaptive_vad_enabled": self.adaptive_vad_enabled,
            "adaptive_vad_bootstrap_chunks": self.adaptive_vad_bootstrap_chunks,
            "adaptive_vad_peak_ratio": self.adaptive_vad_peak_ratio,
            "adaptive_vad_rms_ratio": self.adaptive_vad_rms_ratio,
            "adaptive_vad_noise_alpha": self.adaptive_vad_noise_alpha,
            "language": self.language,
            "barge_in_on_speech_enabled": self.barge_in_on_speech_enabled,
            "barge_in_min_active_chunks": self.barge_in_min_active_chunks,
            "barge_in_cooldown_ms": self.barge_in_cooldown_ms,
            "barge_in_min_peak": self.barge_in_min_peak,
            "barge_in_min_rms": self.barge_in_min_rms,
            "barge_in_voiceprint_window_chunks": self.barge_in_voiceprint_window_chunks,
            "barge_in_require_voiceprint_during_playback": self.barge_in_require_voiceprint_during_playback,
            "playback_echo_suppression_enabled": self.playback_echo_suppression_enabled,
            "speaker_verification_enabled": self.speaker_verification_enabled,
            "speaker_verification_fail_open": self.speaker_verification_fail_open,
            "speaker_verification_min_confidence": self.speaker_verification_min_confidence,
            "speaker_profile_path": self.speaker_profile_path,
            "recovery": self.recovery.as_dict(),
        }


class SimpleVoiceprintVerifier:
    """Lightweight local voiceprint profile based on normalized FFT buckets.

    This is a deterministic gate for realtime barge-in, not a replacement for a
    production speaker verification model. It gives the ASR pipeline a stable
    profile/enroll/verify contract that can later be backed by a stronger model.
    """

    def __init__(
        self,
        *,
        profile_path: str,
        embedding_bins: int = 32,
        min_confidence: float = 0.68,
        fail_open: bool = True,
    ) -> None:
        self.profile_path = str(profile_path or "").strip()
        self.embedding_bins = max(8, min(256, int(embedding_bins)))
        self.min_confidence = max(0.0, min(1.0, float(min_confidence)))
        self.fail_open = bool(fail_open)
        self._profile: dict[str, Any] | None = None
        self._load_profile()

    def status(self) -> dict[str, Any]:
        return {
            "type": "simple_fft_voiceprint",
            "profile_path": self.profile_path,
            "profile_loaded": self._profile is not None,
            "embedding_bins": int(self.embedding_bins),
            "min_confidence": float(self.min_confidence),
            "fail_open": bool(self.fail_open),
            "label": (self._profile or {}).get("label"),
            "created_at_ms": (self._profile or {}).get("created_at_ms"),
        }

    def verify(self, audio_data: Any) -> dict[str, Any]:
        if self._profile is None:
            return {
                "accepted": bool(self.fail_open),
                "confidence": None,
                "reason": "voiceprint_profile_missing",
                "fail_open": bool(self.fail_open),
            }
        reference = self._profile.get("embedding")
        candidate = self._embedding(audio_data, bins=self.embedding_bins)
        if not reference or not candidate:
            return {
                "accepted": bool(self.fail_open),
                "confidence": None,
                "reason": "voiceprint_embedding_unavailable",
                "fail_open": bool(self.fail_open),
            }
        confidence = self._cosine_similarity(reference, candidate)
        accepted = confidence >= self.min_confidence
        return {
            "accepted": bool(accepted),
            "confidence": round(float(confidence), 4),
            "reason": "voiceprint_match" if accepted else "voiceprint_mismatch",
            "min_confidence": float(self.min_confidence),
            "label": self._profile.get("label"),
        }

    def enroll(self, audio_data: Any, *, label: str | None = None, sample_rate: int | None = None) -> dict[str, Any]:
        embedding = self._embedding(audio_data, bins=self.embedding_bins)
        if not embedding:
            return {
                "success": False,
                "reason_code": "VOICEPRINT_AUDIO_EMPTY",
                "detail": "voiceprint enrollment audio is empty",
            }
        profile = {
            "version": 1,
            "type": "simple_fft_voiceprint",
            "label": str(label or "primary_user").strip() or "primary_user",
            "created_at_ms": int(now_ms()),
            "sample_rate": int(sample_rate or 0),
            "embedding_bins": int(self.embedding_bins),
            "embedding": embedding,
        }
        os.makedirs(os.path.dirname(self.profile_path) or ".", exist_ok=True)
        tmp_path = f"{self.profile_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(profile, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp_path, self.profile_path)
        self._profile = profile
        return {
            "success": True,
            "reason_code": None,
            "detail": "voiceprint profile enrolled",
            "status": self.status(),
        }

    def enroll_from_wav(self, audio_path: str, *, label: str | None = None) -> dict[str, Any]:
        path = str(audio_path or "").strip()
        if not path:
            return {
                "success": False,
                "reason_code": "VOICEPRINT_AUDIO_PATH_REQUIRED",
                "detail": "voiceprint enrollment requires audio_path",
            }
        try:
            import wave
            import numpy as np
            with wave.open(path, "rb") as wav:
                channels = max(1, int(wav.getnchannels()))
                sample_rate = int(wav.getframerate())
                sample_width = int(wav.getsampwidth())
                frames = wav.readframes(wav.getnframes())
            if sample_width == 2:
                audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
            elif sample_width == 4:
                audio = np.frombuffer(frames, dtype=np.int32).astype(np.float32) / 2147483648.0
            else:
                audio = np.frombuffer(frames, dtype=np.uint8).astype(np.float32)
                audio = (audio - 128.0) / 128.0
            if channels > 1 and audio.size >= channels:
                audio = audio.reshape(-1, channels).mean(axis=1)
            return self.enroll(audio, label=label, sample_rate=sample_rate)
        except Exception as exc:
            return {
                "success": False,
                "reason_code": "VOICEPRINT_ENROLL_FAILED",
                "detail": str(exc),
            }

    def _load_profile(self) -> None:
        if not self.profile_path or not os.path.exists(self.profile_path):
            self._profile = None
            return
        try:
            with open(self.profile_path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            self._profile = dict(loaded) if isinstance(loaded, dict) else None
        except Exception:
            self._profile = None

    @staticmethod
    def _embedding(audio_data: Any, *, bins: int) -> list[float]:
        try:
            import numpy as np
            audio = np.asarray(audio_data, dtype=np.float32).reshape(-1)
        except Exception:
            return []
        if audio.size < 128:
            return []
        audio = audio - float(np.mean(audio))
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if peak <= 1e-7:
            return []
        audio = audio / peak
        window = np.hanning(audio.size).astype(np.float32)
        spectrum = np.abs(np.fft.rfft(audio * window))
        if spectrum.size < bins:
            spectrum = np.pad(spectrum, (0, bins - spectrum.size))
        buckets = np.array_split(spectrum, bins)
        vector = np.asarray([float(np.log1p(np.mean(bucket))) for bucket in buckets], dtype=np.float32)
        norm = float(np.linalg.norm(vector))
        if norm <= 1e-7:
            return []
        vector = vector / norm
        return [round(float(value), 6) for value in vector.tolist()]

    @staticmethod
    def _cosine_similarity(left: Any, right: Any) -> float:
        try:
            import numpy as np
            a = np.asarray(left, dtype=np.float32).reshape(-1)
            b = np.asarray(right, dtype=np.float32).reshape(-1)
        except Exception:
            return 0.0
        size = min(a.size, b.size)
        if size <= 0:
            return 0.0
        a = a[:size]
        b = b[:size]
        denom = float(np.linalg.norm(a) * np.linalg.norm(b))
        if denom <= 1e-7:
            return 0.0
        return max(0.0, min(1.0, float(np.dot(a, b) / denom)))


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
    # M29-P（OPT-?）：用户情绪文本提示。当前 ASR 只提供转写文本，
    # 不提供声学/韵律情绪；真正声学情绪必须由独立传感器或专用模型写入。
    user_emotion_hint: str | None = None        # 文本提示的用户情绪标签（如 "SAD"/"HAPPY"）
    user_emotion_confidence: float | None = None  # 文本提示置信度（0.0~1.0）

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
        # M29-P：仅在有情绪文本提示时输出
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

    @staticmethod
    def _is_gpu_related_error(error: Exception | str) -> bool:
        error_str = str(error).lower()
        gpu_markers = (
            "cuda",
            "cublas",
            "cudnn",
            "cufft",
            "curand",
            "cusolver",
            "cudart",
            "nvcuda",
            "nvrtc",
            "gpu",
        )
        return "oom" in error_str or any(marker in error_str for marker in gpu_markers)

    def _reload_on_cpu_after_gpu_failure(self, cause: Exception) -> AsrResult:
        with self._lock:
            try:
                from faster_whisper import WhisperModel
            except ImportError as e:
                self._state = AsrState.ERROR
                self._load_error = str(e)
                logger.error(
                    "CPU fallback import failed after GPU runtime error: %s",
                    e,
                )
                return AsrResult(
                    success=False,
                    reason_code=ASR_MODEL_UNAVAILABLE,
                    detail=f"cpu fallback import failed: {e}",
                )

            self._state = AsrState.LOADING
            self._model = None
            try:
                self._model = WhisperModel(
                    self._config.model_size,
                    device="cpu",
                    compute_type="int8",
                )
            except Exception as e:
                self._state = AsrState.ERROR
                self._load_error = str(e)
                logger.error(
                    "CPU fallback load failed after GPU runtime error (%s): %s",
                    cause,
                    e,
                )
                return AsrResult(
                    success=False,
                    reason_code=ASR_MODEL_UNAVAILABLE,
                    detail=f"cpu fallback load failed: {e}",
                )

            self._actual_device = "cpu"
            self._state = AsrState.READY
            self._load_error = None
            self._recovery_count += 1

        logger.warning(
            "ASR runtime GPU failure detected on cuda (%s); reloaded model on cpu",
            cause,
        )
        return AsrResult(
            success=True,
            reason_code=None,
            detail="reloaded model on cpu after gpu runtime failure",
        )

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
                    if device == "cuda" and self._is_gpu_related_error(e):
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
            measured_peak = self._estimate_audio_peak(audio_data)
            measured_rms = self._estimate_audio_rms(audio_data)
            if (
                measured_peak < self._config.precheck_min_peak
                and measured_rms < self._config.precheck_min_rms
            ):
                logger.info(
                    "ASR skipped low-energy audio (peak=%.5f, rms=%.5f, min_peak=%.5f, min_rms=%.5f)",
                    measured_peak,
                    measured_rms,
                    self._config.precheck_min_peak,
                    self._config.precheck_min_rms,
                )
                return AsrResult(
                    success=True,
                    reason_code=ASR_VAD_NO_SPEECH,
                    detail=(
                        "audio below energy gate "
                        f"(peak={measured_peak:.5f}, rms={measured_rms:.5f})"
                    ),
                    segments=[],
                    language=language or self._config.language,
                    language_probability=0.0,
                    duration_s=0.0,
                    processing_time_s=round(time.monotonic() - t0, 4),
                )

            def _collect_segments(segments_gen: Any) -> list[TranscriptionSegment]:
                rows: list[TranscriptionSegment] = []
                for seg in segments_gen:
                    words = []
                    word_confidences: list[float] = []
                    if hasattr(seg, "words") and seg.words:
                        for w in seg.words:
                            probability = max(0.0, min(float(w.probability), 1.0))
                            word_confidences.append(probability)
                            words.append(
                                {
                                    "word": w.word,
                                    "start": w.start,
                                    "end": w.end,
                                    "probability": probability,
                                }
                            )
                    avg_prob = seg.avg_log_prob if hasattr(seg, "avg_log_prob") else 0.0
                    # Convert log probability to confidence (0~1)
                    import math
                    confidence_from_log = math.exp(avg_prob) if avg_prob < 0 else min(avg_prob, 1.0)
                    if word_confidences:
                        confidence = sum(word_confidences) / len(word_confidences)
                        confidence = max(confidence, confidence_from_log)
                    else:
                        confidence = confidence_from_log
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
                logger.info(
                    "ASR no speech detected (vad=%s, attempted_retry=%s, peak=%.5f)",
                    vad_filter,
                    attempted_vad_retry,
                    measured_peak,
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
            if self._is_gpu_related_error(e):
                reason = ASR_GPU_OOM
                if self._actual_device == "cuda":
                    fallback_result = self._reload_on_cpu_after_gpu_failure(e)
                    if fallback_result.success:
                        return self.transcribe(
                            audio_data,
                            language=language,
                            beam_size=beam_size,
                            initial_prompt=initial_prompt,
                            vad_filter=vad_filter,
                            vad_parameters=vad_parameters,
                        )
            else:
                reason = ASR_MODEL_UNAVAILABLE
            logger.error(
                "Transcription failed on %s (peak=%.5f): %s",
                self._actual_device,
                self._estimate_audio_peak(audio_data),
                e,
            )
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

    @staticmethod
    def _estimate_audio_rms(audio_data: Any) -> float:
        try:
            import numpy as np

            arr = np.asarray(audio_data, dtype=float)
            if arr.size == 0:
                return 0.0
            if arr.ndim > 1:
                arr = np.mean(arr, axis=1)
            return float(np.sqrt(np.mean(np.square(arr))))
        except Exception:
            pass
        try:
            if isinstance(audio_data, (list, tuple)) and audio_data:
                values = [float(v) for v in audio_data if isinstance(v, (int, float))]
                if not values:
                    return 0.0
                return (sum(v * v for v in values) / len(values)) ** 0.5
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
        self._buffer_duration_s: float = 0.0
        self._trailing_silence_duration_s: float = 0.0
        self._trailing_silence_chunks: int = 0
        self._running = False
        self._rpc_event_callback = rpc_event_callback
        self._playback_state_provider: Callable[[], dict[str, Any] | None] | None = None
        self._barge_in_callback: Callable[[str, dict[str, Any], int], Any] | None = None
        self._voiceprint_verifier = (
            SimpleVoiceprintVerifier(
                profile_path=str(self._config.speaker_profile_path or ""),
                min_confidence=float(self._config.speaker_verification_min_confidence),
                fail_open=bool(self._config.speaker_verification_fail_open),
            )
            if self._config.speaker_profile_path
            else None
        )
        self._speaker_verifier: Callable[[Any], dict[str, Any] | bool] | None = (
            self._voiceprint_verifier.verify if self._voiceprint_verifier is not None else None
        )
        self._speech_activity_chunks = 0
        self._last_barge_in_ts_ms = 0
        self._last_realtime_dialogue_debug: dict[str, Any] = {}
        self._adaptive_vad_noise_peak = 0.0
        self._adaptive_vad_noise_rms = 0.0
        self._adaptive_vad_noise_samples = 0
        self._barge_in_audio_window: list[Any] = []
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

    def set_playback_state_provider(
        self,
        provider: Callable[[], dict[str, Any] | None] | None,
    ) -> None:
        self._playback_state_provider = provider

    def set_barge_in_callback(
        self,
        callback: Callable[[str, dict[str, Any], int], Any] | None,
    ) -> None:
        self._barge_in_callback = callback

    def set_speaker_verifier(
        self,
        verifier: Callable[[Any], dict[str, Any] | bool] | None,
    ) -> None:
        self._speaker_verifier = verifier

    def voiceprint_status(self) -> dict[str, Any]:
        if self._voiceprint_verifier is None:
            return {
                "available": False,
                "detail": "voiceprint profile path is not configured",
            }
        return {
            "available": True,
            "detail": "voiceprint verifier configured",
            **self._voiceprint_verifier.status(),
        }

    def enroll_voiceprint_from_wav(self, audio_path: str, *, label: str | None = None) -> dict[str, Any]:
        if self._voiceprint_verifier is None:
            return {
                "success": False,
                "reason_code": "VOICEPRINT_PROFILE_PATH_MISSING",
                "detail": "EDGE_ASR_SPEAKER_PROFILE_PATH is not configured",
            }
        return self._voiceprint_verifier.enroll_from_wav(audio_path, label=label)

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
            if self._running:
                return AsrResult(
                    success=True,
                    reason_code=None,
                    detail=f"ASR session already running: {self._session_id}",
                )
            self._shutdown_event.clear()
            self._session_id = session_id or f"asr_{new_request_id()}"
            self._seq = 0
            self._reset_audio_window_locked()

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

        asr_result = self._model.transcribe(
            audio_data,
            vad_filter=self._config.use_vad_filter,
        )
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
        if final:
            final_groups = self._build_final_emit_groups(asr_result.segments)
            for group in final_groups:
                combined_text = self._combine_segment_texts(
                    seg.text for seg in group
                )
                if not combined_text:
                    continue
                combined_alternates = self._collect_segment_alternates(group)
                average_confidence = round(
                    sum(seg.confidence for seg in group)
                    / len(group),
                    4,
                )
                first_segment = group[0]
                last_segment = group[-1]
                results.append(
                    self._emit_rpc_event(
                        request_id=(
                            req_id
                            if len(final_groups) == 1
                            else new_request_id()
                        ),
                        text=combined_text,
                        confidence=average_confidence,
                        start_ms=int(first_segment.start * 1000),
                        end_ms=int(last_segment.end * 1000),
                        alternates=combined_alternates,
                        final=True,
                        ts_ms=timestamp + max(0, int(first_segment.start * 1000)),
                    )
                )
            if results:
                return results

        for seg in asr_result.segments:
            alternates: list[dict[str, Any]] = []
            if seg.words:
                # Use word-level data as alternates
                for w in seg.words[:3]:
                    alternates.append({
                        "text": w.get("word", ""),
                        "confidence": round(w.get("probability", 0.0), 4),
                    })

            segment_ts = timestamp + max(0, int(seg.start * 1000))
            rpc_result = self._emit_rpc_event(
                request_id=req_id,
                text=seg.text,
                confidence=seg.confidence,
                start_ms=int(seg.start * 1000),
                end_ms=int(seg.end * 1000),
                alternates=alternates,
                final=(seg == asr_result.segments[-1] and final),
                ts_ms=segment_ts,
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
            "realtime_dialogue": {
                "barge_in_on_speech_enabled": bool(self._config.barge_in_on_speech_enabled),
                "playback_echo_suppression_enabled": bool(self._config.playback_echo_suppression_enabled),
                "playback_state_provider": self._playback_state_provider is not None,
                "barge_in_callback": self._barge_in_callback is not None,
                "speech_activity_chunks": int(self._speech_activity_chunks),
                "last_barge_in_ts_ms": int(self._last_barge_in_ts_ms),
                "last_debug": dict(self._last_realtime_dialogue_debug),
                "vad": {
                    "adaptive_enabled": bool(self._config.adaptive_vad_enabled),
                    "noise_peak": round(float(self._adaptive_vad_noise_peak), 5),
                    "noise_rms": round(float(self._adaptive_vad_noise_rms), 5),
                    "noise_samples": int(self._adaptive_vad_noise_samples),
                    "thresholds": self._adaptive_vad_thresholds(),
                },
                "speaker_verification": {
                    "enabled": bool(self._config.speaker_verification_enabled),
                    "verifier_available": self._speaker_verifier is not None,
                    "fail_open": bool(self._config.speaker_verification_fail_open),
                    "min_confidence": float(self._config.speaker_verification_min_confidence),
                    "voiceprint": self.voiceprint_status(),
                },
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

        peak = WhisperModelManager._estimate_audio_peak(audio_chunk)
        rms = WhisperModelManager._estimate_audio_rms(audio_chunk)
        playback_snapshot = self._playback_snapshot()
        playback_active = playback_snapshot is not None
        is_silent_like = self._chunk_looks_silent_with_metrics(
            peak=peak,
            rms=rms,
            allow_noise_update=not playback_active,
        )
        barge_triggered = False
        if not is_silent_like:
            barge_triggered = self._maybe_barge_in_on_speech_activity(
                audio_chunk=audio_chunk,
                ts_ms=ts_ms,
                peak=peak,
                rms=rms,
                playback_snapshot=playback_snapshot,
            )
        else:
            self._speech_activity_chunks = 0

        if (
            playback_active
            and self._config.playback_echo_suppression_enabled
            and not barge_triggered
        ):
            self._trim_barge_in_audio_window(reset=True)
            self._last_realtime_dialogue_debug = {
                "state": "suppressed_playback_echo",
                "ts_ms": int(ts_ms),
                "peak": round(float(peak), 5),
                "rms": round(float(rms), 5),
                "playback_request_id": str((playback_snapshot or {}).get("request_id") or ""),
                "speech_activity_chunks": int(self._speech_activity_chunks),
            }
            return

        if is_silent_like:
            with self._lock:
                if not self._audio_buffer:
                    return

        with self._lock:
            if not self._audio_buffer:
                self._buffer_start_ts = ts_ms
            self._audio_buffer.append(audio_chunk)
            self._buffer_duration_s += self._config.chunk_duration_s
            if is_silent_like:
                self._trailing_silence_duration_s += self._config.chunk_duration_s
                self._trailing_silence_chunks += 1
            else:
                self._trailing_silence_duration_s = 0.0
                self._trailing_silence_chunks = 0
            buffer_duration = self._buffer_duration_s
            trailing_silence_duration = self._trailing_silence_duration_s

        if buffer_duration >= self._config.max_speech_duration_s:
            self._flush_buffer(final=False)
        elif trailing_silence_duration >= self._config.silence_duration_s:
            self._flush_buffer(final=True)

    def _chunk_looks_silent(self, audio_chunk: Any) -> bool:
        peak = WhisperModelManager._estimate_audio_peak(audio_chunk)
        rms = WhisperModelManager._estimate_audio_rms(audio_chunk)
        return self._chunk_looks_silent_with_metrics(peak=peak, rms=rms)

    def _chunk_looks_silent_with_metrics(
        self,
        *,
        peak: float,
        rms: float,
        allow_noise_update: bool = True,
    ) -> bool:
        fixed_silent = (
            peak < self._config.precheck_min_peak
            and rms < self._config.precheck_min_rms
        )
        if not self._config.adaptive_vad_enabled:
            return fixed_silent

        bootstrap_chunks = max(1, int(self._config.adaptive_vad_bootstrap_chunks))
        if allow_noise_update and (
            fixed_silent
            or self._adaptive_vad_noise_samples < bootstrap_chunks
        ):
            self._update_adaptive_vad_noise_floor(peak=peak, rms=rms)

        thresholds = self._adaptive_vad_thresholds()
        active = (
            peak >= float(thresholds["peak"])
            or rms >= float(thresholds["rms"])
        )
        return not active

    def _update_adaptive_vad_noise_floor(self, *, peak: float, rms: float) -> None:
        peak = max(0.0, float(peak))
        rms = max(0.0, float(rms))
        if self._adaptive_vad_noise_samples <= 0:
            self._adaptive_vad_noise_peak = peak
            self._adaptive_vad_noise_rms = rms
            self._adaptive_vad_noise_samples = 1
            return
        alpha = max(0.001, min(1.0, float(self._config.adaptive_vad_noise_alpha)))
        self._adaptive_vad_noise_peak = (
            (1.0 - alpha) * self._adaptive_vad_noise_peak
            + alpha * peak
        )
        self._adaptive_vad_noise_rms = (
            (1.0 - alpha) * self._adaptive_vad_noise_rms
            + alpha * rms
        )
        self._adaptive_vad_noise_samples += 1

    def _adaptive_vad_thresholds(self) -> dict[str, Any]:
        if not self._config.adaptive_vad_enabled or self._adaptive_vad_noise_samples <= 0:
            return {
                "peak": round(float(self._config.precheck_min_peak), 5),
                "rms": round(float(self._config.precheck_min_rms), 5),
                "source": "fixed",
            }
        peak_threshold = max(
            float(self._config.precheck_min_peak),
            float(self._adaptive_vad_noise_peak)
            * max(1.0, float(self._config.adaptive_vad_peak_ratio)),
        )
        rms_threshold = max(
            float(self._config.precheck_min_rms),
            float(self._adaptive_vad_noise_rms)
            * max(1.0, float(self._config.adaptive_vad_rms_ratio)),
        )
        return {
            "peak": round(float(peak_threshold), 5),
            "rms": round(float(rms_threshold), 5),
            "source": "adaptive",
        }

    def _playback_snapshot(self) -> dict[str, Any] | None:
        provider = self._playback_state_provider
        if provider is None:
            return None
        try:
            snapshot = provider()
        except Exception as exc:
            self._last_realtime_dialogue_debug = {
                "state": "playback_provider_error",
                "error": str(exc),
            }
            return None
        return dict(snapshot) if isinstance(snapshot, dict) and snapshot else None

    def _maybe_barge_in_on_speech_activity(
        self,
        *,
        audio_chunk: Any,
        ts_ms: int,
        peak: float,
        rms: float,
        playback_snapshot: dict[str, Any] | None,
    ) -> bool:
        if not self._config.barge_in_on_speech_enabled:
            return False
        if playback_snapshot is None or self._barge_in_callback is None:
            return False
        self._remember_barge_in_audio_chunk(audio_chunk)
        vad_thresholds = self._adaptive_vad_thresholds()
        peak_threshold = max(
            float(self._config.barge_in_min_peak),
            float(vad_thresholds["peak"]),
        )
        rms_threshold = max(
            float(self._config.barge_in_min_rms),
            float(vad_thresholds["rms"]),
        )
        if peak < peak_threshold and rms < rms_threshold:
            self._speech_activity_chunks = 0
            self._last_realtime_dialogue_debug = {
                "state": "playback_audio_below_barge_threshold",
                "ts_ms": int(ts_ms),
                "peak": round(float(peak), 5),
                "rms": round(float(rms), 5),
                "thresholds": {
                    "peak": round(float(peak_threshold), 5),
                    "rms": round(float(rms_threshold), 5),
                    "source": str(vad_thresholds.get("source") or ""),
                },
            }
            return False

        speaker_audio = self._combined_barge_in_audio_window()
        if speaker_audio is None:
            speaker_audio = audio_chunk
        speaker_decision = self._verify_speaker_for_barge_in(
            speaker_audio,
            force=bool(self._config.barge_in_require_voiceprint_during_playback),
        )
        if not bool(speaker_decision.get("accepted", False)):
            self._speech_activity_chunks = 0
            self._last_realtime_dialogue_debug = {
                "state": "speaker_rejected_barge_in",
                "ts_ms": int(ts_ms),
                "peak": round(float(peak), 5),
                "rms": round(float(rms), 5),
                "thresholds": {
                    "peak": round(float(peak_threshold), 5),
                    "rms": round(float(rms_threshold), 5),
                    "source": str(vad_thresholds.get("source") or ""),
                },
                "speaker_verification": speaker_decision,
            }
            return False

        self._speech_activity_chunks += 1
        required_chunks = max(1, int(self._config.barge_in_min_active_chunks))
        if self._speech_activity_chunks < required_chunks:
            self._last_realtime_dialogue_debug = {
                "state": "speech_activity_pending_barge_in",
                "ts_ms": int(ts_ms),
                "peak": round(float(peak), 5),
                "rms": round(float(rms), 5),
                "thresholds": {
                    "peak": round(float(peak_threshold), 5),
                    "rms": round(float(rms_threshold), 5),
                    "source": str(vad_thresholds.get("source") or ""),
                },
                "speech_activity_chunks": int(self._speech_activity_chunks),
                "required_chunks": int(required_chunks),
                "speaker_verification": speaker_decision,
            }
            return False

        cooldown_ms = max(0, int(self._config.barge_in_cooldown_ms))
        if self._last_barge_in_ts_ms and ts_ms - self._last_barge_in_ts_ms < cooldown_ms:
            self._last_realtime_dialogue_debug = {
                "state": "barge_in_cooldown",
                "ts_ms": int(ts_ms),
                "last_barge_in_ts_ms": int(self._last_barge_in_ts_ms),
                "cooldown_ms": int(cooldown_ms),
            }
            return False

        session_id = str(
            playback_snapshot.get("session_id")
            or ((playback_snapshot.get("speak_event") or {}) if isinstance(playback_snapshot.get("speak_event"), dict) else {}).get("session_id")
            or self._session_id
            or f"asr_{new_request_id()}"
        ).strip()
        request_id = f"asr_barge_{new_request_id()}"
        payload = {
            "session_id": session_id,
            "source": "asr_vad",
            "text": "[VAD_SPEECH_ACTIVITY]",
            "seq": self._seq + 1,
            "vad": {
                "peak": round(float(peak), 5),
                "rms": round(float(rms), 5),
                "active_chunks": int(self._speech_activity_chunks),
                "required_chunks": int(required_chunks),
                "thresholds": {
                    "peak": round(float(peak_threshold), 5),
                    "rms": round(float(rms_threshold), 5),
                    "source": str(vad_thresholds.get("source") or ""),
                },
            },
            "speaker_verification": speaker_decision,
            "interrupted_playback_request_id": str(playback_snapshot.get("request_id") or ""),
        }
        try:
            self._barge_in_callback(request_id, payload, int(ts_ms))
            self._last_barge_in_ts_ms = int(ts_ms)
            self._speech_activity_chunks = 0
            self._trim_barge_in_audio_window(reset=True)
            self._last_realtime_dialogue_debug = {
                "state": "barge_in_triggered",
                "ts_ms": int(ts_ms),
                "request_id": request_id,
                "session_id": session_id,
                "peak": round(float(peak), 5),
                "rms": round(float(rms), 5),
                "thresholds": {
                    "peak": round(float(peak_threshold), 5),
                    "rms": round(float(rms_threshold), 5),
                    "source": str(vad_thresholds.get("source") or ""),
                },
                "speaker_verification": speaker_decision,
            }
            return True
        except Exception as exc:
            self._last_realtime_dialogue_debug = {
                "state": "barge_in_callback_error",
                "ts_ms": int(ts_ms),
                "error": str(exc),
            }
            return False

    def _remember_barge_in_audio_chunk(self, audio_chunk: Any) -> None:
        self._barge_in_audio_window.append(audio_chunk)
        self._trim_barge_in_audio_window()

    def _trim_barge_in_audio_window(self, *, reset: bool = False) -> None:
        if reset:
            self._barge_in_audio_window.clear()
            return
        max_chunks = max(1, int(self._config.barge_in_voiceprint_window_chunks))
        overflow = len(self._barge_in_audio_window) - max_chunks
        if overflow > 0:
            del self._barge_in_audio_window[:overflow]

    def _combined_barge_in_audio_window(self) -> Any | None:
        if not self._barge_in_audio_window:
            return None
        try:
            import numpy as np
            chunks = [
                np.asarray(chunk, dtype=np.float32).reshape(-1)
                for chunk in self._barge_in_audio_window
            ]
            chunks = [chunk for chunk in chunks if chunk.size > 0]
            if not chunks:
                return None
            return np.concatenate(chunks)
        except Exception:
            return self._barge_in_audio_window[-1]

    def _verify_speaker_for_barge_in(self, audio_chunk: Any, *, force: bool = False) -> dict[str, Any]:
        if not self._config.speaker_verification_enabled and not force:
            return {
                "enabled": False,
                "accepted": True,
                "reason": "speaker_verification_disabled",
            }
        verifier = self._speaker_verifier
        if verifier is None:
            return {
                "enabled": True,
                "accepted": bool(self._config.speaker_verification_fail_open),
                "reason": "speaker_verifier_unavailable",
                "fail_open": bool(self._config.speaker_verification_fail_open),
                "forced": bool(force),
            }
        try:
            raw = verifier(audio_chunk)
        except Exception as exc:
            return {
                "enabled": True,
                "accepted": bool(self._config.speaker_verification_fail_open),
                "reason": "speaker_verifier_error",
                "detail": str(exc),
                "fail_open": bool(self._config.speaker_verification_fail_open),
                "forced": bool(force),
            }
        if isinstance(raw, dict):
            confidence = raw.get("confidence")
            accepted = bool(raw.get("accepted", False))
            if isinstance(confidence, (int, float)):
                accepted = accepted and float(confidence) >= float(self._config.speaker_verification_min_confidence)
            return {
                "enabled": True,
                **dict(raw),
                "accepted": bool(accepted),
                "forced": bool(force),
            }
        return {
            "enabled": True,
            "accepted": bool(raw),
            "reason": "speaker_verifier_bool_result",
            "forced": bool(force),
        }

    def _flush_buffer(self, final: bool = False) -> None:
        """Flush accumulated audio buffer through transcription."""
        with self._lock:
            if not self._audio_buffer:
                return
            buffer_copy = list(self._audio_buffer)
            start_ts = self._buffer_start_ts
            trailing_silence_chunks = (
                self._trailing_silence_chunks if final else 0
            )
            self._reset_audio_window_locked()

        if 0 < trailing_silence_chunks < len(buffer_copy):
            buffer_copy = buffer_copy[:-trailing_silence_chunks]
        if not buffer_copy:
            return

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

    def _reset_audio_window_locked(self) -> None:
        self._audio_buffer = []
        self._buffer_start_ts = 0
        self._buffer_duration_s = 0.0
        self._trailing_silence_duration_s = 0.0
        self._trailing_silence_chunks = 0

    @staticmethod
    def _combine_segment_texts(parts: list[str] | tuple[str, ...] | Any) -> str:
        combined = ""
        for raw_part in parts:
            part = str(raw_part or "").strip()
            if not part:
                continue
            if combined and combined[-1].isalnum() and part[0].isalnum():
                combined = f"{combined} {part}"
            else:
                combined = f"{combined}{part}"
        return combined

    @staticmethod
    def _collect_segment_alternates(
        segments: list[TranscriptionSegment],
    ) -> list[dict[str, Any]]:
        alternates: list[dict[str, Any]] = []
        for seg in segments:
            if not seg.words:
                continue
            for word in seg.words:
                alternates.append({
                    "text": word.get("word", ""),
                    "confidence": round(word.get("probability", 0.0), 4),
                })
                if len(alternates) >= 3:
                    return alternates
        return alternates

    def _build_final_emit_groups(
        self,
        segments: list[TranscriptionSegment],
    ) -> list[list[TranscriptionSegment]]:
        if not segments:
            return []
        if len(segments) == 1:
            return [list(segments)]

        total_duration_s = max(0.0, segments[-1].end - segments[0].start)
        if total_duration_s < 3.0:
            return [list(segments)]

        groups: list[list[TranscriptionSegment]] = []
        current_group: list[TranscriptionSegment] = [segments[0]]
        for segment in segments[1:]:
            if self._should_start_new_final_group(
                current_group=current_group,
                next_segment=segment,
                total_duration_s=total_duration_s,
            ):
                groups.append(current_group)
                current_group = [segment]
            else:
                current_group.append(segment)
        if current_group:
            groups.append(current_group)
        return groups

    def _should_start_new_final_group(
        self,
        *,
        current_group: list[TranscriptionSegment],
        next_segment: TranscriptionSegment,
        total_duration_s: float,
    ) -> bool:
        previous_segment = current_group[-1]
        previous_text = str(previous_segment.text or "").strip()
        next_text = str(next_segment.text or "").strip()
        if not previous_text or not next_text:
            return False

        if (
            self._is_micro_transcript(previous_text)
            or self._is_micro_transcript(next_text)
        ):
            return False

        gap_s = max(0.0, float(next_segment.start) - float(previous_segment.end))
        if gap_s >= max(0.35, self._config.chunk_duration_s * 0.75):
            return True

        if total_duration_s >= 6.0:
            return True

        if self._ends_with_terminal_punctuation(previous_text):
            return True

        if self._is_digit_like_text(previous_text) or self._is_digit_like_text(next_text):
            return True

        return False

    @staticmethod
    def _is_micro_transcript(text: str) -> bool:
        normalized = "".join(ch for ch in str(text or "").strip() if ch.isalnum())
        if not normalized:
            return True
        return len(normalized) <= 2

    @staticmethod
    def _ends_with_terminal_punctuation(text: str) -> bool:
        stripped = str(text or "").strip()
        if not stripped:
            return False
        return stripped.endswith(("。", "！", "？", "!", "?", ";", "；"))

    @staticmethod
    def _is_digit_like_text(text: str) -> bool:
        normalized = "".join(ch for ch in str(text or "").strip() if ch.isalnum())
        if not normalized:
            return False
        return normalized.isdigit()

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
        event = dict(rpc_result.event or {})
        event_ts = int(event.get("ts_ms") or ts_ms)
        event_text = _short_log_text(event.get("text") or payload.get("text"), limit=180)
        event_conf = event.get("confidence")
        conf_display = (
            f"{float(event_conf):.3f}"
            if isinstance(event_conf, (int, float))
            else "n/a"
        )
        logger.info(
            "[EDGE_VOICE_MON][ASR] ts=%s req=%s session=%s seq=%s final=%s low_conf=%s conf=%s status=%s reason=%s stage=%s text=\"%s\"",
            _format_ts_ms(event_ts),
            request_id,
            str(event.get("session_id") or session_id or ""),
            str(event.get("seq") or seq),
            bool(event.get("final", final)),
            bool(event.get("low_confidence", False)),
            conf_display,
            rpc_result.status,
            rpc_result.reason_code,
            rpc_result.stage,
            event_text,
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
