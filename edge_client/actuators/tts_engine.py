"""GPT-SoVITS TTS engine: model management, synthesis, and audio device output.

Provides GPU-resident GPT-SoVITS model lifecycle management with graceful
degradation when model or GPU is unavailable. Integrates with the
SpeechPlaybackQueue as a drop-in Synthesizer callback.
"""

from __future__ import annotations

import gc
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from host_brain.core.errors import (
    TTS_GPU_OOM,
    TTS_MODEL_UNAVAILABLE,
    TTS_SYNTH_TIMEOUT,
    VOICE_SCHEMA_INVALID,
    VOICE_SYNTH_FAILED,
)
from host_brain.core.ids import new_request_id, now_ms

logger = logging.getLogger("Natsume.edge.tts")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MODEL_PATH = "models/gpt_sovits"
DEFAULT_DEVICE = "cuda"
DEFAULT_SAMPLE_RATE = 32000
DEFAULT_MAX_TEXT_LENGTH = 500
DEFAULT_SYNTH_TIMEOUT_S = 30.0
_TORCHAUDIO_LOAD_PATCHED = False
_JAPANESE_TTS_LANGUAGE_ALIASES = frozenset({"ja", "jp", "ja-jp"})
_JAPANESE_TTS_TEXT_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uff66-\uff9f]")
_LATIN_LETTER_RE = re.compile(r"[A-Za-z\u00C0-\u024F\uFF21-\uFF3A\uFF41-\uFF5A]")


class TtsState(str, Enum):
    """TTS engine lifecycle states."""
    UNLOADED = "UNLOADED"
    LOADING = "LOADING"
    READY = "READY"
    SYNTHESIZING = "SYNTHESIZING"
    ERROR = "ERROR"
    SHUTDOWN = "SHUTDOWN"


@dataclass
class TtsConfig:
    """GPT-SoVITS engine configuration."""
    model_path: str = DEFAULT_MODEL_PATH
    device: str = DEFAULT_DEVICE
    sample_rate: int = DEFAULT_SAMPLE_RATE
    max_text_length: int = DEFAULT_MAX_TEXT_LENGTH
    synth_timeout_s: float = DEFAULT_SYNTH_TIMEOUT_S
    ref_audio_path: str | None = None
    ref_text: str | None = None
    language: str = "ja"

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_path": self.model_path,
            "device": self.device,
            "sample_rate": self.sample_rate,
            "max_text_length": self.max_text_length,
            "synth_timeout_s": self.synth_timeout_s,
            "ref_audio_path": self.ref_audio_path,
            "language": self.language,
        }


@dataclass
class TtsSynthResult:
    """Result of a single TTS synthesis attempt."""
    success: bool
    reason_code: str | None
    detail: str
    audio_ref: str = ""
    audio_data: Any = None
    duration_s: float = 0.0
    processing_time_s: float = 0.0
    sample_rate: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "reason_code": self.reason_code,
            "detail": self.detail,
            "audio_ref": self.audio_ref,
            "duration_s": self.duration_s,
            "processing_time_s": self.processing_time_s,
            "sample_rate": self.sample_rate,
        }


# ---------------------------------------------------------------------------
# M15-B: TTSPlaybackEvent（确定性 TTS 播报事件 + 审计串联）
# ---------------------------------------------------------------------------

TTS_PLAYBACK_STATUS_QUEUED = "queued"
TTS_PLAYBACK_STATUS_PLAYING = "playing"
TTS_PLAYBACK_STATUS_DONE = "done"
TTS_PLAYBACK_STATUS_INTERRUPTED = "interrupted"
TTS_PLAYBACK_STATUS_FAILED = "failed"

_VALID_PLAYBACK_STATUSES = frozenset({
    TTS_PLAYBACK_STATUS_QUEUED,
    TTS_PLAYBACK_STATUS_PLAYING,
    TTS_PLAYBACK_STATUS_DONE,
    TTS_PLAYBACK_STATUS_INTERRUPTED,
    TTS_PLAYBACK_STATUS_FAILED,
})


@dataclass(frozen=True)
class TTSPlaybackEvent:
    """
    TTS 播报事件（M15-B）。
    - 播报事件必须关联 request_id（可追溯）
    - 失败必须返回 reason_code + stage
    - 不得产生伪执行宣告
    """
    event_id: str
    request_id: str                 # 关联 Host 请求 ID（必填）
    intent_id: str | None           # 关联 intent（可选）
    text: str                       # 最终口径文本（Host QA 后）
    emotion_tag: str                # 情绪标签（来自 EmotionTag 枚举）
    status: str                     # queued | playing | done | interrupted | failed
    reason_code: str | None         # 失败时必填
    stage: str | None               # 失败阶段
    ts_ms: int
    evidence_ref: str               # 审计链引用

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "request_id": self.request_id,
            "intent_id": self.intent_id,
            "text": self.text,
            "emotion_tag": self.emotion_tag,
            "status": self.status,
            "reason_code": self.reason_code,
            "stage": self.stage,
            "ts_ms": self.ts_ms,
            "evidence_ref": self.evidence_ref,
        }


def build_tts_playback_event(
    *,
    request_id: str,
    text: str,
    emotion_tag: str,
    status: str,
    evidence_ref: str,
    intent_id: str | None = None,
    reason_code: str | None = None,
    stage: str | None = None,
    event_id: str | None = None,
    ts_ms: int | None = None,
) -> TTSPlaybackEvent:
    """
    工厂函数：构建 TTSPlaybackEvent。

    - request_id 必填（缺失时抛出 ValueError）
    - evidence_ref 必填（缺失时抛出 ValueError）
    - status 必须是合法值（否则抛出 ValueError）
    - 失败状态（failed）必须有 reason_code
    """
    from host_brain.core.errors import VOICE_SCHEMA_INVALID as _VOICE_SCHEMA_INVALID
    from host_brain.core.ids import new_request_id as _new_request_id, now_ms as _now_ms

    if not request_id or not str(request_id).strip():
        err = ValueError("TTSPlaybackEvent requires request_id")
        err.reason_code = _VOICE_SCHEMA_INVALID  # type: ignore[attr-defined]
        raise err

    if not evidence_ref or not str(evidence_ref).strip():
        err = ValueError("TTSPlaybackEvent requires evidence_ref")
        err.reason_code = _VOICE_SCHEMA_INVALID  # type: ignore[attr-defined]
        raise err

    if status not in _VALID_PLAYBACK_STATUSES:
        err = ValueError(f"invalid TTS playback status: {status!r}")
        err.reason_code = _VOICE_SCHEMA_INVALID  # type: ignore[attr-defined]
        raise err

    return TTSPlaybackEvent(
        event_id=event_id or _new_request_id(),
        request_id=str(request_id),
        intent_id=intent_id,
        text=str(text),
        emotion_tag=str(emotion_tag),
        status=status,
        reason_code=reason_code,
        stage=stage,
        ts_ms=int(ts_ms if ts_ms is not None else _now_ms()),
        evidence_ref=str(evidence_ref),
    )


# ---------------------------------------------------------------------------
# M15-C: BargeInEvent（Barge-in 可打断审计事件）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BargeInEvent:
    """
    Barge-in 打断审计事件（M15-C）。
    - 打断立即生效，不构成执行事实
    - ASR 不被阻塞（asr_continued=True）
    - 写入审计链（barge_in_log）
    """
    event_id: str
    session_id: str
    interrupted_request_id: str    # 被打断的播报 request_id
    trigger_ts_ms: int             # 打断触发时间
    asr_continued: bool            # ASR 是否继续上送
    evidence_ref: str              # 审计链引用

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "session_id": self.session_id,
            "interrupted_request_id": self.interrupted_request_id,
            "trigger_ts_ms": self.trigger_ts_ms,
            "asr_continued": self.asr_continued,
            "evidence_ref": self.evidence_ref,
        }


def build_barge_in_event(
    *,
    session_id: str,
    interrupted_request_id: str,
    trigger_ts_ms: int,
    evidence_ref: str,
    asr_continued: bool = True,
    event_id: str | None = None,
) -> BargeInEvent:
    """
    工厂函数：构建 BargeInEvent。

    - interrupted_request_id 必填（缺失时抛出 ValueError）
    - evidence_ref 必填（缺失时抛出 ValueError）
    - asr_continued 默认 True（打断后 ASR 继续上送）
    - 打断不构成执行事实（无 intent_draft 字段）
    """
    from host_brain.core.errors import VOICE_SCHEMA_INVALID as _VOICE_SCHEMA_INVALID
    from host_brain.core.ids import new_request_id as _new_request_id

    if not interrupted_request_id or not str(interrupted_request_id).strip():
        err = ValueError("BargeInEvent requires interrupted_request_id")
        err.reason_code = _VOICE_SCHEMA_INVALID  # type: ignore[attr-defined]
        raise err

    if not evidence_ref or not str(evidence_ref).strip():
        err = ValueError("BargeInEvent requires evidence_ref")
        err.reason_code = _VOICE_SCHEMA_INVALID  # type: ignore[attr-defined]
        raise err

    return BargeInEvent(
        event_id=event_id or _new_request_id(),
        session_id=str(session_id),
        interrupted_request_id=str(interrupted_request_id),
        trigger_ts_ms=int(trigger_ts_ms),
        asr_continued=bool(asr_continued),
        evidence_ref=str(evidence_ref),
    )
    duration_s: float = 0.0
    processing_time_s: float = 0.0
    sample_rate: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "reason_code": self.reason_code,
            "detail": self.detail,
            "audio_ref": self.audio_ref,
            "duration_s": self.duration_s,
            "processing_time_s": self.processing_time_s,
            "sample_rate": self.sample_rate,
        }


# ---------------------------------------------------------------------------
# GPT-SoVITS Model Manager
# ---------------------------------------------------------------------------

class SoVITSModelManager:
    """Manages GPT-SoVITS model lifecycle with GPU-resident priority.

    Lazy-loads the model on first use. Handles GPU OOM by reporting error
    (no CPU fallback for SoVITS as it requires GPU for quality).
    Thread-safe singleton pattern per instance.
    """

    def __init__(self, config: TtsConfig | None = None) -> None:
        self._config = config or TtsConfig()
        self._model: Any = None
        self._state = TtsState.UNLOADED
        self._lock = threading.Lock()
        self._actual_device: str = self._config.device
        self._backend_mode: str = "inprocess"
        self._load_error: str | None = None
        self._last_synth_error: str | None = None
        self._last_recovery_detail: str | None = None
        self._recovery_count = 0

    @property
    def state(self) -> TtsState:
        return self._state

    @property
    def actual_device(self) -> str:
        return self._actual_device

    def startup(self) -> None:
        result = self.load()
        if not result.success:
            logger.warning("GPT-SoVITS startup degraded: %s", result.detail)

    def shutdown(self) -> None:
        self.unload()

    def load(self) -> TtsSynthResult:
        """Load the GPT-SoVITS model. Thread-safe, idempotent."""
        with self._lock:
            if self._state == TtsState.READY and self._model is not None:
                return TtsSynthResult(
                    success=True,
                    reason_code=None,
                    detail=f"model already loaded on {self._actual_device}",
                )

            self._state = TtsState.LOADING

            # 尝试加载 GPT-SoVITS 推理引擎
            try:
                model = _load_sovits_model(
                    model_path=self._config.model_path,
                    device=self._config.device,
                    ref_audio_path=self._config.ref_audio_path,
                    ref_text=self._config.ref_text,
                    language=self._config.language,
                )
                self._model = model
                self._actual_device = self._config.device
                self._backend_mode = "inprocess"
                self._state = TtsState.READY
                self._load_error = None
                logger.info(
                    "GPT-SoVITS model loaded: %s on %s",
                    self._config.model_path,
                    self._config.device,
                )
                return TtsSynthResult(
                    success=True,
                    reason_code=None,
                    detail=f"model loaded on {self._config.device}",
                )
            except ImportError as e:
                self._state = TtsState.ERROR
                self._load_error = str(e)
                logger.error("GPT-SoVITS SDK not installed: %s", e)
                return TtsSynthResult(
                    success=False,
                    reason_code=TTS_MODEL_UNAVAILABLE,
                    detail=f"GPT-SoVITS package not available: {e}",
                )
            except RuntimeError as e:
                error_str = str(e).lower()
                self._state = TtsState.ERROR
                self._load_error = str(e)
                if _is_retryable_invalid_argument_error(e):
                    fallback = self._activate_fresh_process_backend(
                        detail_prefix="recovered via fresh subprocess backend after load failure"
                    )
                    if fallback.success:
                        logger.warning(
                            "TTS load hit invalid argument; activating fresh subprocess backend: %s",
                            e,
                        )
                        return fallback
                if "cuda" in error_str or "oom" in error_str or "gpu" in error_str:
                    logger.error("GPU load failed: %s", e)
                    return TtsSynthResult(
                        success=False,
                        reason_code=TTS_GPU_OOM,
                        detail=f"GPU memory insufficient for GPT-SoVITS: {e}",
                    )
                logger.error("Model load failed: %s", e)
                return TtsSynthResult(
                    success=False,
                    reason_code=TTS_MODEL_UNAVAILABLE,
                    detail=f"model load failed: {e}",
                )
            except Exception as e:
                self._state = TtsState.ERROR
                self._load_error = str(e)
                if _is_retryable_invalid_argument_error(e):
                    fallback = self._activate_fresh_process_backend(
                        detail_prefix="recovered via fresh subprocess backend after load failure"
                    )
                    if fallback.success:
                        logger.warning(
                            "TTS load hit invalid argument; activating fresh subprocess backend: %s",
                            e,
                        )
                        return fallback
                logger.error("Unexpected error loading model: %s", e)
                return TtsSynthResult(
                    success=False,
                    reason_code=TTS_MODEL_UNAVAILABLE,
                    detail=f"model load error: {e}",
                )

    def synthesize(
        self,
        text: str,
        *,
        language: str | None = None,
        emotion_tag: str | None = None,
        emotion_intensity: float | None = None,
        voice_profile: str | None = None,
    ) -> TtsSynthResult:
        """Synthesize text to audio using loaded model.

        Args:
            text: Text to synthesize.
            language: Override language code.
            emotion_tag: Emotion tag for prosody control.
            emotion_intensity: 情感强度（0.0~1.0），影响 TTS 语气幅度。缺省为 0.5。
                               超出范围时截断到 [0.0, 1.0]。
            voice_profile: Voice profile override.
        """
        # M26-I：emotion_intensity 范围截断（不拒绝，防止链路中断）
        if emotion_intensity is not None:
            emotion_intensity = max(0.0, min(1.0, float(emotion_intensity)))

        normalized_text = str(text or "").strip()
        if not normalized_text:
            return TtsSynthResult(
                success=False,
                reason_code=VOICE_SYNTH_FAILED,
                detail="empty text for synthesis",
            )

        if len(normalized_text) > self._config.max_text_length:
            normalized_text = normalized_text[: self._config.max_text_length]
            logger.warning("Text truncated to %d chars", self._config.max_text_length)

        normalized_language = _normalize_japanese_tts_language(language or self._config.language)
        if normalized_language is None:
            return TtsSynthResult(
                success=False,
                reason_code=VOICE_SCHEMA_INVALID,
                detail="unsupported tts language; only ja is supported",
            )
        if not _is_supported_japanese_tts_text(normalized_text):
            return TtsSynthResult(
                success=False,
                reason_code=VOICE_SCHEMA_INVALID,
                detail="tts text must contain Japanese characters and no Latin letters",
            )

        if self._state != TtsState.READY or self._model is None:
            load_result = self.load()
            if not load_result.success:
                return load_result

        t0 = time.monotonic()
        self._state = TtsState.SYNTHESIZING
        try:
            audio_data, sr = self._synthesize_with_recovery(
                text=normalized_text,
                language=normalized_language,
                emotion_tag=emotion_tag,
                emotion_intensity=emotion_intensity,
            )
            processing_time = time.monotonic() - t0
            self._state = TtsState.READY
            self._last_synth_error = None

            # 计算音频时长
            duration_s = 0.0
            if audio_data is not None and hasattr(audio_data, "__len__"):
                duration_s = len(audio_data) / max(sr, 1)

            audio_ref = f"memory://tts_{new_request_id()}"
            return TtsSynthResult(
                success=True,
                reason_code=None,
                detail=f"synthesized {duration_s:.2f}s audio",
                audio_ref=audio_ref,
                audio_data=audio_data,
                duration_s=round(duration_s, 4),
                processing_time_s=round(processing_time, 4),
                sample_rate=sr,
            )

        except TimeoutError as e:
            processing_time = time.monotonic() - t0
            self._state = TtsState.READY
            self._last_synth_error = str(e)
            logger.error("TTS synthesis timeout: %s", e)
            return TtsSynthResult(
                success=False,
                reason_code=TTS_SYNTH_TIMEOUT,
                detail=f"synthesis timeout after {processing_time:.1f}s: {e}",
                processing_time_s=round(processing_time, 4),
            )

        except Exception as e:
            processing_time = time.monotonic() - t0
            self._state = TtsState.READY
            self._last_synth_error = str(e)
            error_str = str(e).lower()
            if "oom" in error_str or "cuda" in error_str:
                reason = TTS_GPU_OOM
            else:
                reason = VOICE_SYNTH_FAILED
            logger.error("TTS synthesis failed: %s", e)
            return TtsSynthResult(
                success=False,
                reason_code=reason,
                detail=f"synthesis error: {e}",
                processing_time_s=round(processing_time, 4),
            )

    def unload(self) -> None:
        """Unload model and free GPU memory."""
        with self._lock:
            model = self._model
            self._model = None
            self._state = TtsState.UNLOADED
            self._backend_mode = "inprocess"
        self._cleanup_model_resources(model)
        self._release_gpu_cache()
        logger.info("GPT-SoVITS model unloaded")

    def _synthesize_with_recovery(
        self,
        *,
        text: str,
        language: str,
        emotion_tag: str | None,
        emotion_intensity: float | None,
    ) -> tuple[Any, int]:
        try:
            return _synthesize_audio(
                model=self._model,
                text=text,
                language=language,
                emotion_tag=emotion_tag,
                emotion_intensity=emotion_intensity,
                timeout_s=self._config.synth_timeout_s,
            )
        except Exception as exc:
            if not _is_retryable_invalid_argument_error(exc):
                raise
            logger.warning(
                "TTS synth hit invalid argument; reloading model and retrying once: %s",
                exc,
            )
            recovery = self._recover_after_invalid_argument()
            if not recovery.success or self._model is None:
                self._last_recovery_detail = (
                    f"reload failed after invalid argument: {recovery.detail}"
                )
                if _allow_tts_subprocess_fallback():
                    result = self._synthesize_via_fresh_process(
                        text=text,
                        language=language,
                        emotion_tag=emotion_tag,
                        emotion_intensity=emotion_intensity,
                    )
                    activation = self._activate_fresh_process_backend(
                        detail_prefix="recovered via fresh subprocess after reload failure"
                    )
                    if activation.success:
                        logger.warning("%s", activation.detail)
                    return result
                raise RuntimeError(self._last_recovery_detail) from exc
            try:
                result = _synthesize_audio(
                    model=self._model,
                    text=text,
                    language=language,
                    emotion_tag=emotion_tag,
                    emotion_intensity=emotion_intensity,
                    timeout_s=self._config.synth_timeout_s,
                )
            except Exception as retry_exc:
                self._last_recovery_detail = (
                    f"reload retry failed after invalid argument: {retry_exc}"
                )
                if _allow_tts_subprocess_fallback():
                    result = self._synthesize_via_fresh_process(
                        text=text,
                        language=language,
                        emotion_tag=emotion_tag,
                        emotion_intensity=emotion_intensity,
                    )
                    activation = self._activate_fresh_process_backend(
                        detail_prefix="recovered via fresh subprocess after retry failure"
                    )
                    if activation.success:
                        logger.warning("%s", activation.detail)
                    return result
                raise RuntimeError(self._last_recovery_detail) from retry_exc
            self._recovery_count += 1
            self._last_recovery_detail = (
                f"recovered from invalid argument via model reload #{self._recovery_count}"
            )
            return result

    def _recover_after_invalid_argument(self) -> TtsSynthResult:
        self.unload()
        return self.load()

    def _activate_fresh_process_backend(self, *, detail_prefix: str) -> TtsSynthResult:
        if not _allow_tts_subprocess_fallback():
            return TtsSynthResult(
                success=False,
                reason_code=TTS_MODEL_UNAVAILABLE,
                detail="fresh subprocess fallback disabled",
            )
        self._model = _FreshProcessSoVITSAdapter(config=self._config)
        self._actual_device = self._config.device
        self._backend_mode = "subprocess"
        self._state = TtsState.READY
        self._load_error = None
        self._recovery_count += 1
        self._last_recovery_detail = f"{detail_prefix} #{self._recovery_count}"
        return TtsSynthResult(
            success=True,
            reason_code=None,
            detail=self._last_recovery_detail,
        )

    def _synthesize_via_fresh_process(
        self,
        *,
        text: str,
        language: str,
        emotion_tag: str | None,
        emotion_intensity: float | None,
    ) -> tuple[Any, int]:
        return _run_tts_fresh_process(
            config=self._config,
            text=text,
            text_language=language,
            emotion_tag=emotion_tag,
            emotion_intensity=emotion_intensity,
        )

    @staticmethod
    def _cleanup_model_resources(model: Any) -> None:
        if model is None:
            return
        for attr in ("shutdown", "close"):
            method = getattr(model, attr, None)
            if not callable(method):
                continue
            try:
                method()
            except Exception:
                logger.debug("TTS model %s cleanup failed", attr, exc_info=True)

    @staticmethod
    def _release_gpu_cache() -> None:
        gc.collect()
        try:
            import torch
        except ImportError:
            return
        try:
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            logger.debug("torch cuda cache release failed", exc_info=True)

    def health(self) -> dict[str, Any]:
        """Return engine health snapshot."""
        return {
            "state": self._state.value,
            "model_path": self._config.model_path,
            "device": self._actual_device,
            "backend_mode": self._backend_mode,
            "config_device": self._config.device,
            "sample_rate": self._config.sample_rate,
            "load_error": self._load_error,
            "last_synth_error": self._last_synth_error,
            "last_recovery_detail": self._last_recovery_detail,
            "recovery_count": self._recovery_count,
        }


# ---------------------------------------------------------------------------
# Audio Playback Device
# ---------------------------------------------------------------------------

class AudioPlaybackDevice:
    """Cross-platform audio output via sounddevice.

    Plays synthesized audio through the default audio device.
    Supports interrupt (stop) for barge-in scenarios.
    """

    def __init__(
        self,
        *,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        device_index: int | None = None,
    ) -> None:
        self._sample_rate = sample_rate
        self._device_index = device_index
        self._playing = False
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._on_playback_finished: Callable[[], None] | None = None
        self._last_playback_started_ts_ms: int | None = None
        self._last_playback_finished_ts_ms: int | None = None
        self._last_playback_error: str | None = None

    @property
    def playing(self) -> bool:
        return self._playing

    def set_on_playback_finished(self, callback: Callable[[], None] | None) -> None:
        with self._lock:
            self._on_playback_finished = callback

    def play(
        self,
        audio_data: Any,
        *,
        sample_rate: int | None = None,
        blocking: bool = False,
    ) -> TtsSynthResult:
        """Play audio data through output device."""
        with self._lock:
            if self._playing:
                self._last_playback_error = "audio device already playing"
                return TtsSynthResult(
                    success=False,
                    reason_code=VOICE_SYNTH_FAILED,
                    detail="audio device already playing",
                )

            self._stop_event.clear()
            self._playing = True
            self._last_playback_error = None
            self._last_playback_started_ts_ms = now_ms()

        sr = sample_rate or self._sample_rate

        try:
            import sounddevice as sd
            sd.play(
                audio_data,
                samplerate=sr,
                device=self._device_index,
                blocking=blocking,
            )
            if blocking:
                with self._lock:
                    self._playing = False
                    self._last_playback_finished_ts_ms = now_ms()
                self._notify_playback_finished()
            else:
                threading.Thread(
                    target=self._await_nonblocking_completion,
                    args=(sd,),
                    name="edge-tts-playback-monitor",
                    daemon=True,
                ).start()
            return TtsSynthResult(
                success=True,
                reason_code=None,
                detail="playback started",
                sample_rate=sr,
            )
        except ImportError as e:
            with self._lock:
                self._playing = False
                self._last_playback_error = f"sounddevice not available: {e}"
            return TtsSynthResult(
                success=False,
                reason_code=TTS_MODEL_UNAVAILABLE,
                detail=f"sounddevice not available: {e}",
            )
        except Exception as e:
            with self._lock:
                self._playing = False
                self._last_playback_error = f"playback error: {e}"
            return TtsSynthResult(
                success=False,
                reason_code=VOICE_SYNTH_FAILED,
                detail=f"playback error: {e}",
            )

    def stop(self) -> TtsSynthResult:
        """Stop current playback (for barge-in)."""
        with self._lock:
            was_playing = self._playing
            self._playing = False
            self._stop_event.set()
            if was_playing:
                self._last_playback_finished_ts_ms = now_ms()

        if was_playing:
            try:
                import sounddevice as sd
                sd.stop()
            except Exception:
                pass

        return TtsSynthResult(
            success=True,
            reason_code=None,
            detail="playback stopped" if was_playing else "no active playback",
        )

    def health(self) -> dict[str, Any]:
        with self._lock:
            snapshot = {
                "playing": self._playing,
                "sample_rate": self._sample_rate,
                "device_index": self._device_index,
                "configured_device_index": self._device_index,
                "last_playback_started_ts_ms": self._last_playback_started_ts_ms,
                "last_playback_finished_ts_ms": self._last_playback_finished_ts_ms,
                "last_playback_error": self._last_playback_error,
            }
        probe_enabled = str(os.environ.get("EDGE_TTS_HEALTH_PROBE_DEVICES", "false")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if probe_enabled:
            snapshot.update(self._probe_device_health())
        else:
            snapshot.update(
                {
                    "sounddevice_available": None,
                    "device_probe_skipped": True,
                    "resolved_output_device": None,
                    "default_output_device": None,
                }
            )
        return snapshot

    def _await_nonblocking_completion(self, sd_module: Any) -> None:
        try:
            sd_module.wait()
        except Exception:
            logger.debug("non-blocking playback wait failed", exc_info=True)
        finally:
            with self._lock:
                self._playing = False
                self._last_playback_finished_ts_ms = now_ms()
            self._notify_playback_finished()

    def _probe_device_health(self) -> dict[str, Any]:
        try:
            import sounddevice as sd
        except ImportError as exc:
            return {
                "sounddevice_available": False,
                "device_probe_error": f"sounddevice not available: {exc}",
                "resolved_output_device": None,
                "default_output_device": None,
            }
        except Exception as exc:  # pragma: no cover - defensive guard
            return {
                "sounddevice_available": False,
                "device_probe_error": f"sounddevice import failed: {exc}",
                "resolved_output_device": None,
                "default_output_device": None,
            }

        try:
            default_device = getattr(getattr(sd, "default", None), "device", None)
        except Exception as exc:  # pragma: no cover - defensive guard
            default_device = None
            default_error = f"default device probe failed: {exc}"
        else:
            default_error = None

        default_output_index = self._extract_output_device_index(default_device)
        resolved_output_index = self._device_index if self._device_index is not None else default_output_index

        probe = {
            "sounddevice_available": True,
            "resolved_output_device": self._query_device(sd, resolved_output_index),
            "default_output_device": self._query_device(sd, default_output_index),
        }
        if default_error:
            probe["device_probe_error"] = default_error
        return probe

    @staticmethod
    def _extract_output_device_index(default_device: Any) -> int | None:
        if isinstance(default_device, (list, tuple)):
            if len(default_device) >= 2:
                candidate = default_device[1]
            elif len(default_device) == 1:
                candidate = default_device[0]
            else:
                return None
        else:
            candidate = default_device
        try:
            if candidate is None:
                return None
            index = int(candidate)
        except (TypeError, ValueError):
            return None
        return index if index >= 0 else None

    @staticmethod
    def _query_device(sd_module: Any, device_index: int | None) -> dict[str, Any] | None:
        if device_index is None:
            return None
        try:
            raw = sd_module.query_devices(device_index)
        except Exception as exc:
            return {
                "index": int(device_index),
                "query_error": str(exc),
            }
        info = dict(raw or {})
        return {
            "index": int(device_index),
            "name": str(info.get("name") or ""),
            "hostapi": info.get("hostapi"),
            "max_output_channels": info.get("max_output_channels"),
            "default_samplerate": info.get("default_samplerate"),
        }

    def _notify_playback_finished(self) -> None:
        callback: Callable[[], None] | None
        with self._lock:
            callback = self._on_playback_finished
        if callback is None:
            return
        try:
            callback()
        except Exception:
            logger.debug("playback finished callback failed", exc_info=True)


# ---------------------------------------------------------------------------
# Synthesizer callback factory (for SpeechPlaybackQueue integration)
# ---------------------------------------------------------------------------

def make_sovits_synthesizer(
    engine: SoVITSModelManager,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Create a Synthesizer callback compatible with SpeechPlaybackQueue.

    Returns a callable that accepts a speak_event dict and returns
    a synth_event dict with audio_ref and metadata.
    """

    def _synthesize(speak_event: dict[str, Any]) -> dict[str, Any]:
        text = str(speak_event.get("text") or "")
        emotion_tag = str(speak_event.get("emotion_tag") or "")
        voice_profile = str(speak_event.get("voice_profile") or "default")
        lang_hint = str(speak_event.get("lang_hint") or "").strip() or None
        # M26-I：透传 emotion_intensity
        raw_intensity = speak_event.get("emotion_intensity")
        emotion_intensity: float | None = None
        if raw_intensity is not None:
            try:
                emotion_intensity = float(raw_intensity)
            except (TypeError, ValueError):
                emotion_intensity = None

        result = engine.synthesize(
            text,
            language=lang_hint,
            emotion_tag=emotion_tag or None,
            emotion_intensity=emotion_intensity,
            voice_profile=voice_profile or None,
        )

        if not result.success:
            raise RuntimeError(
                f"TTS synthesis failed [{result.reason_code}]: {result.detail}"
            )

        return {
            "audio_ref": result.audio_ref,
            "audio_data": result.audio_data,
            "duration_s": result.duration_s,
            "processing_time_s": result.processing_time_s,
            "sample_rate": result.sample_rate,
        }

    return _synthesize


def make_audio_playback_starter(
    device: AudioPlaybackDevice,
) -> Callable[[dict[str, Any], dict[str, Any]], None]:
    """Create a PlaybackStarter callback compatible with SpeechPlaybackQueue."""

    def _start_playback(
        speak_event: dict[str, Any],
        synth_event: dict[str, Any],
    ) -> None:
        audio_data = synth_event.get("audio_data")
        if audio_data is None:
            raise RuntimeError("no audio_data in synth_event")

        sr = synth_event.get("sample_rate", DEFAULT_SAMPLE_RATE)
        result = device.play(audio_data, sample_rate=sr, blocking=False)
        if not result.success:
            raise RuntimeError(
                f"Playback failed [{result.reason_code}]: {result.detail}"
            )

    return _start_playback


# ---------------------------------------------------------------------------
# Internal: GPT-SoVITS SDK bridge (isolates actual SDK dependency)
# ---------------------------------------------------------------------------

def _load_sovits_model(
    *,
    model_path: str,
    device: str,
    ref_audio_path: str | None,
    ref_text: str | None,
    language: str,
) -> Any:
    """Load GPT-SoVITS model using legacy or modern SDK backends."""
    model_root = os.path.abspath(model_path)
    with _temporary_working_directory(model_root):
        # Legacy SDK path: GPT_SoVITS.inference.TTS_Model
        try:
            from GPT_SoVITS.inference import TTS_Model  # type: ignore[import-not-found]

            return TTS_Model(
                model_path=model_path,
                device=device,
                ref_audio_path=ref_audio_path,
                ref_text=ref_text,
            )
        except ImportError:
            pass

        # Modern SDK path: GPT_SoVITS.TTS_infer_pack.TTS
        return _load_modern_sovits_model(
            model_root=model_root,
            device=device,
            ref_audio_path=ref_audio_path,
            ref_text=ref_text,
            language=language,
        )


def _load_modern_sovits_model(
    *,
    model_root: str,
    device: str,
    ref_audio_path: str | None,
    ref_text: str | None,
    language: str,
) -> Any:
    if not os.path.isdir(model_root):
        raise ImportError(
            f"GPT-SoVITS model path does not exist: {model_root}. "
            "Set EDGE_TTS_MODEL_PATH to your GPT-SoVITS root directory."
        )

    _ensure_modern_sdk_paths(model_root)
    _ensure_jieba_fast_alias()
    _patch_torchaudio_load_fallback()
    _configure_modern_sv_path(model_root)

    try:
        from GPT_SoVITS.TTS_infer_pack.TTS import (  # type: ignore[import-not-found]
            TTS,
            TTS_Config,
        )
    except ImportError as exc:
        raise ImportError(
            "GPT-SoVITS modern SDK not found. "
            "Ensure EDGE_TTS_MODEL_PATH points to a GPT-SoVITS root directory."
        ) from exc

    custom_cfg = _build_modern_tts_custom_config(model_root=model_root, device=device)
    tts = TTS(TTS_Config({"custom": custom_cfg}))

    effective_ref_audio = str(ref_audio_path or "").strip() or None
    effective_ref_text = str(ref_text or "").strip()
    if not effective_ref_audio:
        auto_ref = _find_ref_audio_with_prompt(
            model_root=model_root,
            language=language,
        )
        if auto_ref is not None:
            effective_ref_audio, auto_ref_text = auto_ref
            if not effective_ref_text:
                effective_ref_text = auto_ref_text

    if not effective_ref_audio:
        effective_ref_audio = _find_default_ref_audio(model_root)
    if not effective_ref_audio:
        raise RuntimeError(
            "EDGE_TTS_REF_AUDIO_PATH is required when no 3-10s reference wav "
            "is found under model logs."
        )

    logger.info(
        "TTS reference selected: audio=%s prompt_text_chars=%d",
        effective_ref_audio,
        len(effective_ref_text),
    )

    return _ModernSoVITSAdapter(
        tts=tts,
        ref_audio_path=effective_ref_audio,
        ref_text=(effective_ref_text or None),
        default_language=language,
        working_directory=model_root,
    )


class _ModernSoVITSAdapter:
    """Adapter exposing a legacy-like synthesize() API over TTS_infer_pack.TTS."""

    def __init__(
        self,
        *,
        tts: Any,
        ref_audio_path: str,
        ref_text: str | None,
        default_language: str,
        working_directory: str,
    ) -> None:
        self._tts = tts
        self._ref_audio_path = ref_audio_path
        self._ref_text = (ref_text or "").strip()
        self._default_language = default_language
        self._working_directory = working_directory

    def synthesize(
        self,
        *,
        text: str,
        language: str,
        emotion: str | None = None,
        speed_factor: float | None = None,
        energy_factor: float | None = None,
    ) -> dict[str, Any]:
        import numpy as np

        text_lang = self._normalize_language(language or self._default_language)
        prompt_lang = self._normalize_language(self._default_language)
        prompt_text = self._ref_text

        inputs = {
            "text": text,
            "text_lang": text_lang,
            "ref_audio_path": self._ref_audio_path,
            "aux_ref_audio_paths": [],
            "prompt_text": prompt_text,
            "prompt_lang": prompt_lang if prompt_text else text_lang,
            "seed": 42,
            "parallel_infer": True,
            "return_fragment": False,
            "streaming_mode": False,
            "fragment_interval": 0.0,
        }
        if speed_factor is not None:
            inputs["speed_factor"] = float(speed_factor)
        if energy_factor is not None:
            inputs["energy_factor"] = float(energy_factor)

        def _collect_audio(run_inputs: dict[str, Any]) -> tuple[list[Any], int]:
            chunks: list[Any] = []
            sample_rate = DEFAULT_SAMPLE_RATE
            for sr_i, audio_i in self._tts.run(run_inputs):
                sample_rate = int(sr_i)
                if audio_i is not None and hasattr(audio_i, "__len__") and len(audio_i) > 0:
                    chunks.append(np.asarray(audio_i))
            return chunks, sample_rate

        with _temporary_working_directory(self._working_directory):
            try:
                chunks, sample_rate = _collect_audio(inputs)
            except TypeError as exc:
                message = str(exc)
                if "speed_factor" in message or "energy_factor" in message or "unexpected keyword" in message:
                    fallback_inputs = dict(inputs)
                    fallback_inputs.pop("speed_factor", None)
                    fallback_inputs.pop("energy_factor", None)
                    logger.debug(
                        "Modern SoVITS backend does not accept speed/energy args; retry without them: %s",
                        message,
                    )
                    chunks, sample_rate = _collect_audio(fallback_inputs)
                else:
                    raise

        if not chunks:
            raise RuntimeError("GPT-SoVITS produced empty audio.")

        return {
            "audio": np.concatenate(chunks),
            "sample_rate": sample_rate,
        }

    def _normalize_language(self, language: str) -> str:
        normalized = _normalize_japanese_tts_language(language) or "ja"

        languages = set(getattr(getattr(self._tts, "configs", None), "languages", []) or [])
        if normalized in languages:
            return normalized
        if "auto" in languages:
            return "auto"
        if languages:
            return sorted(languages)[0]
        return "ja"


class _FreshProcessSoVITSAdapter:
    """Persistent subprocess-backed adapter used after in-process backend failure."""

    def __init__(self, *, config: TtsConfig) -> None:
        self._config = config

    def synthesize(
        self,
        *,
        text: str,
        language: str,
        emotion: str | None = None,
        speed_factor: float | None = None,
        energy_factor: float | None = None,
    ) -> dict[str, Any]:
        audio, sample_rate = _run_tts_fresh_process(
            config=self._config,
            text=text,
            text_language=language,
            emotion_tag=emotion,
            emotion_intensity=_infer_emotion_intensity_from_prosody(
                speed_factor=speed_factor,
                energy_factor=energy_factor,
            ),
        )
        return {
            "audio": audio,
            "sample_rate": sample_rate,
        }

    def close(self) -> None:
        return None


def _ensure_modern_sdk_paths(model_root: str) -> None:
    paths = [
        model_root,
        os.path.join(model_root, "GPT_SoVITS"),
        os.path.join(model_root, "GPT_SoVITS", "eres2net"),
    ]
    for path in reversed(paths):
        if path and os.path.isdir(path) and path not in sys.path:
            sys.path.insert(0, path)


def _ensure_jieba_fast_alias() -> None:
    if "jieba_fast" in sys.modules:
        return
    try:
        import jieba as jieba_fallback
    except ImportError:
        return
    sys.modules["jieba_fast"] = jieba_fallback


def _patch_torchaudio_load_fallback() -> None:
    """Patch torchaudio.load to fallback to soundfile on torchcodec runtime failures."""
    global _TORCHAUDIO_LOAD_PATCHED
    if _TORCHAUDIO_LOAD_PATCHED:
        return

    try:
        import torchaudio
    except ImportError:
        return

    original_load = getattr(torchaudio, "load", None)
    if original_load is None:
        return

    doc = str(getattr(original_load, "__doc__", "") or "").lower()
    # Torchaudio >= 2.9 routes decoding through torchcodec.
    if "torchcodec" not in doc:
        _TORCHAUDIO_LOAD_PATCHED = True
        return

    try:
        import numpy as np
        import soundfile as sf
        import torch
    except ImportError:
        # Keep original behavior if fallback dependencies are unavailable.
        return

    def _load_with_soundfile_fallback(
        uri: Any,
        frame_offset: int = 0,
        num_frames: int = -1,
        normalize: bool = True,  # noqa: ARG001 - compatibility with torchaudio.load
        channels_first: bool = True,
        format: str | None = None,  # noqa: ARG001
        buffer_size: int = 4096,  # noqa: ARG001
        backend: str | None = None,  # noqa: ARG001
    ) -> tuple[Any, int]:
        if hasattr(uri, "read"):
            waveform, sample_rate = sf.read(uri, dtype="float32", always_2d=True)
        else:
            waveform, sample_rate = sf.read(str(uri), dtype="float32", always_2d=True)

        start = max(int(frame_offset), 0)
        if start:
            waveform = waveform[start:]
        if int(num_frames) >= 0:
            waveform = waveform[: int(num_frames)]

        if channels_first:
            waveform = waveform.T

        tensor = torch.from_numpy(np.asarray(waveform, dtype=np.float32))
        logger.debug("torchaudio.load replaced with soundfile decoder for uri=%s", uri)
        return tensor, int(sample_rate)

    setattr(torchaudio, "load", _load_with_soundfile_fallback)
    _TORCHAUDIO_LOAD_PATCHED = True


def _resolve_tts_repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _allow_tts_subprocess_fallback() -> bool:
    raw = str(os.environ.get("EDGE_TTS_SUBPROCESS_HELPER") or "").strip().lower()
    return raw not in {"1", "true", "yes", "on"}


def _infer_emotion_intensity_from_prosody(
    *,
    speed_factor: float | None,
    energy_factor: float | None,
) -> float | None:
    values: list[float] = []
    if speed_factor is not None:
        values.append((float(speed_factor) - 0.7) / 0.5)
    if energy_factor is not None:
        values.append((float(energy_factor) - 0.6) / 0.8)
    if not values:
        return None
    intensity = sum(values) / len(values)
    return max(0.0, min(1.0, intensity))


def _run_tts_fresh_process(
    *,
    config: TtsConfig,
    text: str,
    text_language: str,
    emotion_tag: str | None,
    emotion_intensity: float | None,
) -> tuple[Any, int]:
    import numpy as np

    fd, output_path = tempfile.mkstemp(prefix="edge_tts_fallback_", suffix=".npz")
    os.close(fd)
    cmd = [
        sys.executable,
        "-m",
        "edge_client.actuators.tts_subprocess_helper",
        "--output",
        output_path,
        "--model-path",
        str(config.model_path),
        "--device",
        str(config.device),
        "--sample-rate",
        str(config.sample_rate),
        "--language",
        str(config.language),
        "--text",
        str(text),
        "--text-language",
        str(text_language),
    ]
    if config.ref_audio_path:
        cmd.extend(["--ref-audio-path", str(config.ref_audio_path)])
    if config.ref_text:
        cmd.extend(["--ref-text", str(config.ref_text)])
    if emotion_tag:
        cmd.extend(["--emotion-tag", str(emotion_tag)])
    if emotion_intensity is not None:
        cmd.extend(["--emotion-intensity", str(float(emotion_intensity))])

    env = dict(os.environ)
    env["EDGE_TTS_SUBPROCESS_HELPER"] = "1"
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=max(15.0, float(config.synth_timeout_s) + 15.0),
            cwd=_resolve_tts_repo_root(),
            env=env,
            check=False,
        )
        if completed.returncode != 0:
            stderr = str(completed.stderr or completed.stdout or "").strip()
            raise RuntimeError(
                f"fresh subprocess synth failed (exit={completed.returncode}): {stderr}"
            )
        payload = np.load(output_path)
        audio = np.asarray(payload["audio"], dtype=np.float32)
        sample_rate = int(payload["sample_rate"])
        return audio, sample_rate
    finally:
        try:
            os.remove(output_path)
        except OSError:
            pass


@contextmanager
def _temporary_working_directory(path: str | None):
    target = str(path or "").strip()
    if not target or not os.path.isdir(target):
        yield
        return
    try:
        previous = os.getcwd()
    except OSError:
        previous = None
    try:
        os.chdir(target)
    except OSError:
        logger.debug("failed to switch cwd to %s", target, exc_info=True)
        yield
        return
    try:
        yield
    finally:
        if previous:
            try:
                os.chdir(previous)
            except OSError:
                logger.debug("failed to restore cwd to %s", previous, exc_info=True)


def _configure_modern_sv_path(model_root: str) -> None:
    """Patch GPT-SoVITS sv checkpoint path to absolute path when available."""
    sv_abs = os.path.join(
        model_root, "GPT_SoVITS", "pretrained_models", "sv", "pretrained_eres2netv2w24s4ep4.ckpt"
    )
    if not os.path.isfile(sv_abs):
        return
    try:
        import sv as sv_module  # type: ignore[import-not-found]
    except Exception:
        return
    try:
        setattr(sv_module, "sv_path", sv_abs)
    except Exception:
        return


def _build_modern_tts_custom_config(*, model_root: str, device: str) -> dict[str, Any]:
    t2s_weights_path, vits_weights_path = _resolve_modern_weights(model_root)
    version = _detect_model_version(t2s_weights_path=t2s_weights_path, vits_weights_path=vits_weights_path)

    bert_base_path = os.path.join(
        model_root, "GPT_SoVITS", "pretrained_models", "chinese-roberta-wwm-ext-large"
    )
    cnhuhbert_base_path = os.path.join(
        model_root, "GPT_SoVITS", "pretrained_models", "chinese-hubert-base"
    )
    if not os.path.isdir(bert_base_path) or not os.path.isdir(cnhuhbert_base_path):
        raise RuntimeError(
            "GPT-SoVITS pretrained models are missing. "
            "Expected chinese-roberta-wwm-ext-large and chinese-hubert-base."
        )

    return {
        "device": device,
        "is_half": "cuda" in str(device).lower(),
        "version": version,
        "t2s_weights_path": t2s_weights_path,
        "vits_weights_path": vits_weights_path,
        "bert_base_path": bert_base_path,
        "cnhuhbert_base_path": cnhuhbert_base_path,
    }


def _resolve_modern_weights(model_root: str) -> tuple[str, str]:
    # 1) Prefer model root config.py assignments (sovits_path/gpt_path).
    config_path = os.path.join(model_root, "config.py")
    rel_sovits = _extract_python_string_assignment(config_path, "sovits_path")
    rel_gpt = _extract_python_string_assignment(config_path, "gpt_path")
    if rel_sovits and rel_gpt:
        sovits_abs = os.path.normpath(os.path.join(model_root, rel_sovits))
        gpt_abs = os.path.normpath(os.path.join(model_root, rel_gpt))
        if os.path.isfile(sovits_abs) and os.path.isfile(gpt_abs):
            return gpt_abs, sovits_abs

    # 2) Fallback to first available checkpoints in preferred directories.
    candidates = [
        ("GPT_weights_v2ProPlus", ".ckpt"),
        ("GPT_weights_v2Pro", ".ckpt"),
        ("GPT_weights_v4", ".ckpt"),
        ("GPT_weights_v3", ".ckpt"),
        ("GPT_weights_v2", ".ckpt"),
        ("GPT_weights", ".ckpt"),
    ]
    t2s_weights_path = _find_first_weight(model_root, candidates)

    candidates = [
        ("SoVITS_weights_v2ProPlus", ".pth"),
        ("SoVITS_weights_v2Pro", ".pth"),
        ("SoVITS_weights_v4", ".pth"),
        ("SoVITS_weights_v3", ".pth"),
        ("SoVITS_weights_v2", ".pth"),
        ("SoVITS_weights", ".pth"),
    ]
    vits_weights_path = _find_first_weight(model_root, candidates)

    if not t2s_weights_path or not vits_weights_path:
        raise RuntimeError(
            "Cannot locate GPT-SoVITS checkpoint files under model path. "
            "Expected .ckpt in GPT_weights* and .pth in SoVITS_weights*."
        )
    return t2s_weights_path, vits_weights_path


def _extract_python_string_assignment(path: str, name: str) -> str | None:
    if not os.path.isfile(path):
        return None
    text = ""
    with open(path, "r", encoding="utf-8", errors="ignore") as handle:
        text = handle.read()
    match = re.search(rf"^{name}\s*=\s*['\"]([^'\"]+)['\"]", text, flags=re.MULTILINE)
    return match.group(1).strip() if match else None


def _find_first_weight(model_root: str, candidates: list[tuple[str, str]]) -> str | None:
    for folder, suffix in candidates:
        base = os.path.join(model_root, folder)
        if not os.path.isdir(base):
            continue
        files = sorted(
            [
                os.path.join(base, f)
                for f in os.listdir(base)
                if f.lower().endswith(suffix.lower())
            ]
        )
        if files:
            return files[-1]
    return None


def _detect_model_version(*, t2s_weights_path: str, vits_weights_path: str) -> str:
    probe = f"{t2s_weights_path} {vits_weights_path}".lower()
    if "v2proplus" in probe:
        return "v2ProPlus"
    if "v2pro" in probe:
        return "v2Pro"
    if "v4" in probe:
        return "v4"
    if "v3" in probe:
        return "v3"
    if "v2" in probe:
        return "v2"
    return "v1"


def _find_ref_audio_with_prompt(*, model_root: str, language: str) -> tuple[str, str] | None:
    logs_dir = os.path.join(model_root, "logs")
    if not os.path.isdir(logs_dir):
        return None

    want_ja = str(language or "").strip().lower().startswith("ja")
    candidates = _collect_ref_prompt_candidates(
        logs_dir=logs_dir,
        require_japanese=want_ja,
    )
    if not candidates and want_ja:
        candidates = _collect_ref_prompt_candidates(
            logs_dir=logs_dir,
            require_japanese=False,
        )
    if not candidates:
        return None

    # Prefer clips around ~5 seconds at 32 kHz for stable reference quality.
    candidates.sort(key=lambda row: (abs(row[2] - 160_000), row[0]))
    audio_path, prompt_text, _samples = candidates[0]
    return audio_path, prompt_text


def _collect_ref_prompt_candidates(
    *,
    logs_dir: str,
    require_japanese: bool,
) -> list[tuple[str, str, int]]:
    pattern = re.compile(r"_(\d{10})_(\d{10})\.wav$", flags=re.IGNORECASE)
    out: list[tuple[str, str, int]] = []

    for project_name in sorted(os.listdir(logs_dir)):
        project_root = os.path.join(logs_dir, project_name)
        if not os.path.isdir(project_root):
            continue

        text_path = os.path.join(project_root, "2-name2text.txt")
        wav_dir = os.path.join(project_root, "5-wav32k")
        if not os.path.isfile(text_path) or not os.path.isdir(wav_dir):
            continue

        try:
            with open(text_path, "r", encoding="utf-8", errors="ignore") as handle:
                for raw_line in handle:
                    parts = raw_line.rstrip("\n").split("\t")
                    if len(parts) < 4:
                        continue
                    clip_name = parts[0].strip()
                    prompt_text = parts[3].strip()
                    if not clip_name or not prompt_text or not clip_name.lower().endswith(".wav"):
                        continue
                    if require_japanese and not _looks_japanese_text(prompt_text):
                        continue

                    match = pattern.search(clip_name)
                    if not match:
                        continue
                    start = int(match.group(1))
                    end = int(match.group(2))
                    samples = max(end - start, 0)
                    if not (96_000 <= samples <= 320_000):
                        continue

                    audio_path = os.path.normpath(os.path.join(wav_dir, clip_name))
                    if not os.path.isfile(audio_path):
                        continue
                    out.append((audio_path, prompt_text, samples))
        except OSError:
            continue

    return out


def _looks_japanese_text(text: str) -> bool:
    return bool(re.search(r"[\u3040-\u30ff\u3400-\u9fff]", str(text)))


def _normalize_japanese_tts_language(language: str | None) -> str | None:
    normalized = str(language or "").strip().lower()
    if not normalized:
        return "ja"
    if normalized in _JAPANESE_TTS_LANGUAGE_ALIASES:
        return "ja"
    return None


def _is_supported_japanese_tts_text(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    if _LATIN_LETTER_RE.search(normalized):
        return False
    return bool(_JAPANESE_TTS_TEXT_RE.search(normalized))


def _find_default_ref_audio(model_root: str) -> str | None:
    logs_dir = os.path.join(model_root, "logs")
    if not os.path.isdir(logs_dir):
        return None

    pattern = re.compile(r"_(\d{10})_(\d{10})$")
    fallback: str | None = None
    for root, _dirs, files in os.walk(logs_dir):
        for name in sorted(files):
            if not name.lower().endswith(".wav"):
                continue
            path = os.path.join(root, name)
            if fallback is None:
                fallback = path

            stem = os.path.splitext(name)[0]
            match = pattern.search(stem)
            if not match:
                continue
            start = int(match.group(1))
            end = int(match.group(2))
            samples = max(end - start, 0)
            # GPT-SoVITS ref audio requirement: 3~10 seconds (at 32kHz => 96k~320k).
            if 96_000 <= samples <= 320_000:
                return path

    return fallback


def _synthesize_audio(
    *,
    model: Any,
    text: str,
    language: str,
    emotion_tag: str | None,
    emotion_intensity: float | None = None,
    timeout_s: float,
) -> tuple[Any, int]:
    """Run synthesis on model. Returns (audio_ndarray, sample_rate).

    Raises TimeoutError if synthesis exceeds timeout_s.
    """
    import numpy as np

    t0 = time.monotonic()

    # M28-S（OPT-47）：将 emotion_intensity 映射为底层 SDK 参数并真正传递
    # emotion_intensity → speed_factor（0.3→0.85, 0.5→1.0, 0.9→1.2）
    # emotion_intensity → energy_factor（0.3→0.8, 0.5→1.0, 0.9→1.3）
    _intensity = float(emotion_intensity) if emotion_intensity is not None else 0.5
    _intensity = max(0.0, min(1.0, _intensity))
    speed_factor = round(0.7 + _intensity * 0.5, 3)    # [0.7, 1.2]
    energy_factor = round(0.6 + _intensity * 0.8, 3)   # [0.6, 1.4]

    # 调用底层模型推理，传递情感强度参数。
    # 兼容旧适配器：若不支持 speed_factor/energy_factor，回退到基础调用。
    try:
        result = model.synthesize(
            text=text,
            language=language,
            emotion=emotion_tag,
            # M28-S：真正传递 emotion_intensity 映射后的参数
            speed_factor=speed_factor,
            energy_factor=energy_factor,
        )
    except Exception as exc:
        message = str(exc or "")
        lowered = message.lower()
        can_retry_without_prosody = (
            ("unexpected keyword argument" in lowered)
            or ("invalid argument" in lowered)
            or ("errno 22" in lowered)
        )
        if not can_retry_without_prosody:
            raise
        logger.warning(
            "TTS synth with speed/energy failed, retrying without prosody controls: %s",
            message,
        )
        result = model.synthesize(
            text=text,
            language=language,
            emotion=emotion_tag,
        )

    elapsed = time.monotonic() - t0
    if elapsed > timeout_s:
        raise TimeoutError(f"synthesis took {elapsed:.1f}s > timeout {timeout_s:.1f}s")

    # 提取音频数据
    if isinstance(result, dict):
        audio = result.get("audio", np.zeros(1, dtype=np.float32))
        sr = int(result.get("sample_rate", DEFAULT_SAMPLE_RATE))
    elif isinstance(result, tuple) and len(result) >= 2:
        audio, sr = result[0], int(result[1])
    else:
        audio = np.zeros(1, dtype=np.float32)
        sr = DEFAULT_SAMPLE_RATE

    return audio, sr


def _is_retryable_invalid_argument_error(exc: BaseException) -> bool:
    lowered = str(exc or "").strip().lower()
    return "invalid argument" in lowered or "errno 22" in lowered
