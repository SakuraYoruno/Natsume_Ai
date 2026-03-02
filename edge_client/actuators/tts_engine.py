"""GPT-SoVITS TTS engine: model management, synthesis, and audio device output.

Provides GPU-resident GPT-SoVITS model lifecycle management with graceful
degradation when model or GPU is unavailable. Integrates with the
SpeechPlaybackQueue as a drop-in Synthesizer callback.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from host_brain.core.errors import (
    TTS_GPU_OOM,
    TTS_MODEL_UNAVAILABLE,
    TTS_SYNTH_TIMEOUT,
    VOICE_SYNTH_FAILED,
)
from host_brain.core.ids import new_request_id, now_ms

logger = logging.getLogger("Nastume.edge.tts")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MODEL_PATH = "models/gpt_sovits"
DEFAULT_DEVICE = "cuda"
DEFAULT_SAMPLE_RATE = 32000
DEFAULT_MAX_TEXT_LENGTH = 500
DEFAULT_SYNTH_TIMEOUT_S = 30.0


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
        self._load_error: str | None = None

    @property
    def state(self) -> TtsState:
        return self._state

    @property
    def actual_device(self) -> str:
        return self._actual_device

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
                )
                self._model = model
                self._actual_device = self._config.device
                self._state = TtsState.READY
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
        voice_profile: str | None = None,
    ) -> TtsSynthResult:
        """Synthesize text to audio using loaded model.

        Args:
            text: Text to synthesize.
            language: Override language code.
            emotion_tag: Emotion tag for prosody control.
            voice_profile: Voice profile override.
        """
        if self._state != TtsState.READY or self._model is None:
            load_result = self.load()
            if not load_result.success:
                return load_result

        if not text or not text.strip():
            return TtsSynthResult(
                success=False,
                reason_code=VOICE_SYNTH_FAILED,
                detail="empty text for synthesis",
            )

        if len(text) > self._config.max_text_length:
            text = text[: self._config.max_text_length]
            logger.warning("Text truncated to %d chars", self._config.max_text_length)

        t0 = time.monotonic()
        self._state = TtsState.SYNTHESIZING
        try:
            audio_data, sr = _synthesize_audio(
                model=self._model,
                text=text.strip(),
                language=language or self._config.language,
                emotion_tag=emotion_tag,
                timeout_s=self._config.synth_timeout_s,
            )
            processing_time = time.monotonic() - t0
            self._state = TtsState.READY

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
            self._model = None
            self._state = TtsState.UNLOADED
            logger.info("GPT-SoVITS model unloaded")

    def health(self) -> dict[str, Any]:
        """Return engine health snapshot."""
        return {
            "state": self._state.value,
            "model_path": self._config.model_path,
            "device": self._actual_device,
            "config_device": self._config.device,
            "sample_rate": self._config.sample_rate,
            "load_error": self._load_error,
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

    @property
    def playing(self) -> bool:
        return self._playing

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
                return TtsSynthResult(
                    success=False,
                    reason_code=VOICE_SYNTH_FAILED,
                    detail="audio device already playing",
                )

            self._stop_event.clear()
            self._playing = True

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
            return TtsSynthResult(
                success=True,
                reason_code=None,
                detail="playback started",
                sample_rate=sr,
            )
        except ImportError as e:
            with self._lock:
                self._playing = False
            return TtsSynthResult(
                success=False,
                reason_code=TTS_MODEL_UNAVAILABLE,
                detail=f"sounddevice not available: {e}",
            )
        except Exception as e:
            with self._lock:
                self._playing = False
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
        return {
            "playing": self._playing,
            "sample_rate": self._sample_rate,
            "device_index": self._device_index,
        }


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

        result = engine.synthesize(
            text,
            emotion_tag=emotion_tag or None,
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
) -> Any:
    """Load GPT-SoVITS model. Raises ImportError or RuntimeError."""
    try:
        # 尝试导入 GPT-SoVITS 推理模块
        # 实际项目中此处为 GPT-SoVITS 的 Python SDK 入口
        from GPT_SoVITS.inference import TTS_Model  # type: ignore[import-not-found]
    except ImportError:
        raise ImportError(
            "GPT-SoVITS inference module not found. "
            "Ensure GPT-SoVITS is installed and available on PYTHONPATH."
        )

    model = TTS_Model(
        model_path=model_path,
        device=device,
        ref_audio_path=ref_audio_path,
        ref_text=ref_text,
    )
    return model


def _synthesize_audio(
    *,
    model: Any,
    text: str,
    language: str,
    emotion_tag: str | None,
    timeout_s: float,
) -> tuple[Any, int]:
    """Run synthesis on model. Returns (audio_ndarray, sample_rate).

    Raises TimeoutError if synthesis exceeds timeout_s.
    """
    import numpy as np

    t0 = time.monotonic()

    # 调用模型推理
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
