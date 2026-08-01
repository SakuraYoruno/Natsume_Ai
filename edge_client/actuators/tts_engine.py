"""GPT-SoVITS TTS engine: model management, synthesis, and audio device output.

Provides GPU-resident GPT-SoVITS model lifecycle management with graceful
degradation when model or GPU is unavailable. Integrates with the
SpeechPlaybackQueue as a drop-in Synthesizer callback.
"""

from __future__ import annotations

import gc
import json
import logging
import math
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import types
import wave
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from natsume_contracts.edge.v1 import (
    TTS_GPU_OOM,
    TTS_MODEL_UNAVAILABLE,
    TTS_SYNTH_TIMEOUT,
    VOICE_SCHEMA_INVALID,
    VOICE_SYNTH_FAILED,
    new_request_id,
    now_ms,
)

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
_DLL_DIRECTORY_HANDLES: list[Any] = []
_DLL_DIRECTORY_PATHS: set[str] = set()
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


@dataclass(frozen=True)
class _TtsReference:
    audio_path: str
    prompt_text: str = ""
    prompt_lang: str | None = None
    samples: int = 0
    aux_audio_paths: tuple[str, ...] = ()


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
    voice_bank_path: str | None = None
    language: str = "zh"

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_path": self.model_path,
            "device": self.device,
            "sample_rate": self.sample_rate,
            "max_text_length": self.max_text_length,
            "synth_timeout_s": self.synth_timeout_s,
            "ref_audio_path": self.ref_audio_path,
            "voice_bank_path": self.voice_bank_path,
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


def _clamp_float(value: float, minimum: float, maximum: float) -> float:
    if maximum < minimum:
        minimum, maximum = maximum, minimum
    if not math.isfinite(value):
        return minimum
    return max(minimum, min(maximum, value))


def _env_float(name: str, default: float, *, min_value: float, max_value: float) -> float:
    raw = str(os.environ.get(name) or "").strip()
    try:
        value = float(raw) if raw else float(default)
    except (TypeError, ValueError):
        value = float(default)
    return _clamp_float(value, float(min_value), float(max_value))


def _normalize_intensity(value: float | None) -> float:
    try:
        intensity = float(value) if value is not None else 0.5
    except (TypeError, ValueError):
        intensity = 0.5
    return _clamp_float(intensity, 0.0, 1.0)


@dataclass(frozen=True)
class _TtsProsodyProfile:
    min_speed: float
    default_speed: float
    max_speed: float
    min_energy: float
    default_energy: float
    max_energy: float
    pause_scale: float
    breath_ms: int = 0
    breath_level: float = 0.0


_NEUTRAL_PROSODY = _TtsProsodyProfile(
    min_speed=0.45,
    default_speed=1.0,
    max_speed=1.2,
    min_energy=0.45,
    default_energy=1.0,
    max_energy=1.15,
    pause_scale=1.0,
)

_EMOTION_PROSODY: dict[str, _TtsProsodyProfile] = {
    "NEUTRAL": _NEUTRAL_PROSODY,
    "CALM": _TtsProsodyProfile(0.88, 0.94, 1.00, 0.90, 0.95, 1.00, 1.16, 80, 0.005),
    "COMFORT": _TtsProsodyProfile(0.88, 0.94, 0.99, 0.90, 0.95, 1.00, 1.22, 105, 0.006),
    "LOVING": _TtsProsodyProfile(0.94, 0.99, 1.07, 0.96, 1.02, 1.09, 1.12, 75, 0.005),
    "SAD": _TtsProsodyProfile(0.84, 0.91, 0.99, 0.86, 0.92, 0.99, 1.34, 140, 0.009),
    "SADNESS": _TtsProsodyProfile(0.84, 0.91, 0.99, 0.86, 0.92, 0.99, 1.34, 140, 0.009),
    "HAPPY": _TtsProsodyProfile(0.98, 1.07, 1.16, 0.98, 1.06, 1.12, 0.88, 0, 0.0),
    "HAPPINESS": _TtsProsodyProfile(0.98, 1.07, 1.16, 0.98, 1.06, 1.12, 0.88, 0, 0.0),
    "JOY": _TtsProsodyProfile(0.98, 1.07, 1.16, 0.98, 1.06, 1.12, 0.88, 0, 0.0),
    "EXCITED": _TtsProsodyProfile(1.00, 1.10, 1.20, 1.00, 1.08, 1.14, 0.80, 0, 0.0),
    "EXCITEMENT": _TtsProsodyProfile(1.00, 1.10, 1.20, 1.00, 1.08, 1.14, 0.80, 0, 0.0),
    "PLAYFUL": _TtsProsodyProfile(0.99, 1.08, 1.18, 0.98, 1.06, 1.12, 0.84, 0, 0.0),
    "COQUETTISH": _TtsProsodyProfile(0.92, 0.98, 1.08, 0.94, 1.00, 1.08, 1.10, 80, 0.005),
    "SERIOUS": _TtsProsodyProfile(0.94, 0.98, 1.03, 0.98, 1.03, 1.08, 1.06, 0, 0.0),
    "ANXIOUS": _TtsProsodyProfile(1.00, 1.07, 1.16, 0.90, 0.96, 1.04, 0.90, 90, 0.008),
    "EMBARRASSED": _TtsProsodyProfile(0.90, 0.96, 1.04, 0.88, 0.94, 1.00, 1.18, 95, 0.007),
    "NOSTALGIC": _TtsProsodyProfile(0.86, 0.92, 0.99, 0.86, 0.92, 1.00, 1.28, 120, 0.008),
}


def _resolve_tts_prosody_profile(emotion_tag: str | None) -> _TtsProsodyProfile:
    key = str(emotion_tag or "NEUTRAL").strip().upper() or "NEUTRAL"
    return _EMOTION_PROSODY.get(key, _NEUTRAL_PROSODY)


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


def _emotion_prosody_enabled() -> bool:
    return _env_flag("EDGE_TTS_EMOTION_PROSODY", True)


def _resolve_emotion_axis_value(
    *,
    baseline: float,
    neutral_value: float,
    profile_value: float,
    profile_min: float,
    profile_max: float,
    emotion_intensity: float | None,
) -> float:
    if abs(profile_value - neutral_value) <= 0.0001:
        return baseline

    intensity = _normalize_intensity(emotion_intensity)
    target = profile_max if profile_value > neutral_value else profile_min
    delta = target - neutral_value
    value = baseline + delta * intensity
    return _clamp_float(value, profile_min, profile_max)


def _resolve_tts_speed_factor(
    *,
    requested_speed: float | None,
    emotion_intensity: float | None,
    emotion_tag: str | None = None,
) -> float:
    profile = _resolve_tts_prosody_profile(emotion_tag)
    min_speed = _env_float("EDGE_TTS_MIN_SPEED_FACTOR", _NEUTRAL_PROSODY.min_speed, min_value=0.45, max_value=1.2)
    max_speed = _env_float("EDGE_TTS_MAX_SPEED_FACTOR", _NEUTRAL_PROSODY.max_speed, min_value=0.55, max_value=1.5)
    if max_speed < min_speed:
        max_speed = min_speed
    default_speed = _env_float(
        "EDGE_TTS_DEFAULT_SPEED_FACTOR",
        _NEUTRAL_PROSODY.default_speed,
        min_value=min_speed,
        max_value=max_speed,
    )
    if requested_speed is not None:
        try:
            requested = float(requested_speed)
        except (TypeError, ValueError):
            requested = default_speed
        return round(_clamp_float(requested, min_speed, max_speed), 3)

    resolved = default_speed
    if profile is not _NEUTRAL_PROSODY and _emotion_prosody_enabled():
        resolved = _resolve_emotion_axis_value(
            baseline=default_speed,
            neutral_value=_NEUTRAL_PROSODY.default_speed,
            profile_value=profile.default_speed,
            profile_min=profile.min_speed,
            profile_max=profile.max_speed,
            emotion_intensity=emotion_intensity,
        )
    return round(_clamp_float(resolved, min_speed, max_speed), 3)


def _resolve_tts_energy_factor(
    *,
    emotion_intensity: float | None,
    emotion_tag: str | None = None,
) -> float:
    profile = _resolve_tts_prosody_profile(emotion_tag)
    min_energy = _env_float("EDGE_TTS_MIN_ENERGY_FACTOR", _NEUTRAL_PROSODY.min_energy, min_value=0.3, max_value=1.0)
    max_energy = _env_float("EDGE_TTS_MAX_ENERGY_FACTOR", _NEUTRAL_PROSODY.max_energy, min_value=0.45, max_value=1.15)
    if max_energy < min_energy:
        max_energy = min_energy
    default_energy = _env_float(
        "EDGE_TTS_DEFAULT_ENERGY_FACTOR",
        _NEUTRAL_PROSODY.default_energy,
        min_value=min_energy,
        max_value=max_energy,
    )
    resolved = default_energy
    if profile is not _NEUTRAL_PROSODY and _emotion_prosody_enabled():
        resolved = _resolve_emotion_axis_value(
            baseline=default_energy,
            neutral_value=_NEUTRAL_PROSODY.default_energy,
            profile_value=profile.default_energy,
            profile_min=profile.min_energy,
            profile_max=profile.max_energy,
            emotion_intensity=emotion_intensity,
        )
    return round(_clamp_float(resolved, min_energy, max_energy), 3)


_EXPRESSIVE_PUNCT_RE = re.compile(r"([^，,。.!！?？；;：:\n…]+)([，,。.!！?？；;：:\n…]*)")


def _expressive_timing_enabled() -> bool:
    raw = str(os.environ.get("EDGE_TTS_EXPRESSIVE_TIMING", "false")).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _split_expressive_text(text: str) -> list[tuple[str, str]]:
    normalized = str(text or "").strip()
    if not normalized:
        return []
    segments: list[tuple[str, str]] = []
    for match in _EXPRESSIVE_PUNCT_RE.finditer(normalized):
        phrase = str(match.group(1) or "").strip()
        punct = str(match.group(2) or "")
        if phrase:
            segments.append((phrase, punct))
    if not segments:
        return [(normalized, "")]
    # Keep the post-synthesis timing pass bounded; too many cuts sounds choppy.
    if len(segments) > 8:
        merged: list[tuple[str, str]] = []
        bucket_phrase = ""
        bucket_punct = ""
        for phrase, punct in segments:
            bucket_phrase = f"{bucket_phrase}{phrase}{punct}"
            bucket_punct = punct
            if len(merged) < 7 and len(bucket_phrase) >= 12:
                merged.append((bucket_phrase.strip(), bucket_punct))
                bucket_phrase = ""
                bucket_punct = ""
        if bucket_phrase.strip():
            merged.append((bucket_phrase.strip(), bucket_punct))
        segments = merged[:8]
    return segments


def _pause_ms_for_punctuation(
    punct: str,
    *,
    profile: _TtsProsodyProfile,
    emotion_intensity: float | None,
) -> int:
    if not punct:
        return 0
    if "…" in punct:
        base = 430
    elif any(item in punct for item in "。.!！?？"):
        base = 300
    elif any(item in punct for item in "；;：:"):
        base = 240
    else:
        base = 145
    intensity = _normalize_intensity(emotion_intensity)
    scale = profile.pause_scale * (0.92 + intensity * 0.18)
    return int(_clamp_float(base * scale, 80, 760))


def _find_quiet_boundary(audio: Any, estimate: int, sample_rate: int) -> int:
    try:
        import numpy as np
    except Exception:
        return int(estimate)
    arr = np.asarray(audio, dtype=np.float32)
    if arr.ndim == 2:
        mono = np.mean(np.abs(arr), axis=1)
    else:
        mono = np.abs(arr.reshape(-1))
    total = int(mono.shape[0])
    if total <= 0:
        return int(estimate)
    search = max(1, int(sample_rate * 0.28))
    start = max(1, int(estimate) - search)
    end = min(total - 1, int(estimate) + search)
    if end <= start:
        return int(_clamp_float(float(estimate), 1.0, float(max(total - 1, 1))))
    window = max(1, int(sample_rate * 0.018))
    kernel = np.ones(window, dtype=np.float32) / float(window)
    rms = np.convolve(mono[start:end], kernel, mode="same")
    return int(start + int(np.argmin(rms)))


def _fade_audio_edges(audio: Any, sample_rate: int, *, fade_ms: int = 6) -> Any:
    try:
        import numpy as np
    except Exception:
        return audio
    arr = np.asarray(audio, dtype=np.float32)
    if arr.shape[0] <= 2:
        return arr
    frames = min(max(1, int(sample_rate * fade_ms / 1000.0)), max(1, arr.shape[0] // 4))
    if frames <= 0:
        return arr
    fade_in = np.linspace(0.0, 1.0, frames, dtype=np.float32)
    fade_out = np.linspace(1.0, 0.0, frames, dtype=np.float32)
    if arr.ndim == 2:
        arr = arr.copy()
        arr[:frames, :] *= fade_in[:, None]
        arr[-frames:, :] *= fade_out[:, None]
    else:
        arr = arr.copy()
        arr[:frames] *= fade_in
        arr[-frames:] *= fade_out
    return arr


def _make_expressive_breath(
    *,
    sample_rate: int,
    channels: int,
    peak: float,
    profile: _TtsProsodyProfile,
    emotion_intensity: float | None,
    seed: int,
) -> Any:
    try:
        import numpy as np
    except Exception:
        return []
    breath_ms = int(profile.breath_ms)
    if breath_ms <= 0 or profile.breath_level <= 0:
        return np.zeros((0, channels), dtype=np.float32) if channels > 1 else np.zeros(0, dtype=np.float32)
    frames = max(1, int(sample_rate * breath_ms / 1000.0))
    rng = np.random.default_rng(int(seed) & 0xFFFF_FFFF)
    noise = rng.normal(0.0, 1.0, frames).astype(np.float32)
    smooth = max(1, int(sample_rate * 0.012))
    kernel = np.ones(smooth, dtype=np.float32) / float(smooth)
    noise = np.convolve(noise, kernel, mode="same")
    envelope = np.sin(np.linspace(0.0, 3.14159, frames, dtype=np.float32))
    intensity = _normalize_intensity(emotion_intensity)
    amp = max(float(peak), 1.0) * profile.breath_level * (0.8 + intensity * 0.5)
    breath = (noise * envelope * amp).astype(np.float32)
    if channels > 1:
        breath = np.repeat(breath[:, None], channels, axis=1)
    return breath


def _apply_expressive_audio_timing(
    *,
    audio_data: Any,
    sample_rate: int,
    text: str,
    emotion_tag: str | None,
    emotion_intensity: float | None,
) -> tuple[Any, dict[str, Any]]:
    if not _expressive_timing_enabled():
        return audio_data, {"enabled": False, "reason": "disabled"}
    try:
        import numpy as np
    except Exception:
        return audio_data, {"enabled": False, "reason": "numpy_unavailable"}

    if hasattr(audio_data, "detach"):
        audio_data = audio_data.detach()
    if hasattr(audio_data, "cpu"):
        audio_data = audio_data.cpu()
    if hasattr(audio_data, "numpy"):
        audio_data = audio_data.numpy()

    arr = np.asarray(audio_data, dtype=np.float32)
    if arr.size <= 0:
        return audio_data, {"enabled": False, "reason": "empty_audio"}
    channels = 1
    if arr.ndim == 2:
        if arr.shape[0] <= 8 and arr.shape[1] > arr.shape[0]:
            arr = arr.T
        if arr.shape[1] == 1:
            arr = arr[:, 0]
        else:
            channels = int(arr.shape[1])
    elif arr.ndim != 1:
        return audio_data, {"enabled": False, "reason": "unsupported_audio_shape", "shape": list(arr.shape)}

    segments = _split_expressive_text(text)
    if len(segments) <= 1:
        return arr, {"enabled": True, "segments": 1, "inserted_pause_s": 0.0, "breath_count": 0}

    total_frames = int(arr.shape[0])
    weights = [max(1, len(re.sub(r"\s+", "", phrase))) for phrase, _punct in segments]
    total_weight = max(1, sum(weights))
    boundaries: list[int] = []
    cumulative = 0
    last_boundary = 0
    min_gap = max(1, int(sample_rate * 0.12))
    for weight in weights[:-1]:
        cumulative += weight
        estimate = int(total_frames * (cumulative / total_weight))
        boundary = _find_quiet_boundary(arr, estimate, sample_rate)
        boundary = max(last_boundary + min_gap, min(total_frames - min_gap, boundary))
        if boundary <= last_boundary or boundary >= total_frames:
            continue
        boundaries.append(boundary)
        last_boundary = boundary

    if len(boundaries) != len(segments) - 1:
        return arr, {
            "enabled": True,
            "segments": len(segments),
            "inserted_pause_s": 0.0,
            "breath_count": 0,
            "reason": "boundary_search_failed",
        }

    profile = _resolve_tts_prosody_profile(emotion_tag)
    seed_base = sum(ord(ch) for ch in str(text or "")) + int(_normalize_intensity(emotion_intensity) * 1000)
    peak = float(np.max(np.abs(arr))) if arr.size else 1.0
    parts: list[Any] = []
    inserted_frames = 0
    breath_count = 0
    points = [0, *boundaries, total_frames]
    for idx, (start, end) in enumerate(zip(points[:-1], points[1:])):
        chunk = arr[start:end]
        if chunk.size:
            parts.append(_fade_audio_edges(chunk, sample_rate))
        punct = segments[idx][1]
        if idx >= len(segments) - 1:
            continue
        pause_ms = _pause_ms_for_punctuation(
            punct,
            profile=profile,
            emotion_intensity=emotion_intensity,
        )
        if pause_ms <= 0:
            continue
        pause_frames = max(1, int(sample_rate * pause_ms / 1000.0))
        breath = _make_expressive_breath(
            sample_rate=sample_rate,
            channels=channels,
            peak=peak,
            profile=profile,
            emotion_intensity=emotion_intensity,
            seed=seed_base + idx * 97,
        )
        breath_frames = int(breath.shape[0]) if hasattr(breath, "shape") else 0
        silence_frames = max(0, pause_frames - breath_frames)
        left = silence_frames // 2
        right = silence_frames - left
        shape_left = (left, channels) if channels > 1 else (left,)
        shape_right = (right, channels) if channels > 1 else (right,)
        if left:
            parts.append(np.zeros(shape_left, dtype=np.float32))
        if breath_frames:
            parts.append(breath)
            breath_count += 1
        if right:
            parts.append(np.zeros(shape_right, dtype=np.float32))
        inserted_frames += pause_frames

    if not parts:
        return arr, {"enabled": False, "reason": "no_parts"}
    output = np.concatenate(parts, axis=0).astype(np.float32, copy=False)
    return output, {
        "enabled": True,
        "segments": len(segments),
        "boundaries": len(boundaries),
        "inserted_pause_s": round(inserted_frames / max(1, sample_rate), 4),
        "breath_count": breath_count,
        "pause_scale": round(float(profile.pause_scale), 3),
        "emotion_tag": str(emotion_tag or "NEUTRAL").strip().upper() or "NEUTRAL",
    }


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
    if not request_id or not str(request_id).strip():
        err = ValueError("TTSPlaybackEvent requires request_id")
        err.reason_code = VOICE_SCHEMA_INVALID  # type: ignore[attr-defined]
        raise err

    if not evidence_ref or not str(evidence_ref).strip():
        err = ValueError("TTSPlaybackEvent requires evidence_ref")
        err.reason_code = VOICE_SCHEMA_INVALID  # type: ignore[attr-defined]
        raise err

    if status not in _VALID_PLAYBACK_STATUSES:
        err = ValueError(f"invalid TTS playback status: {status!r}")
        err.reason_code = VOICE_SCHEMA_INVALID  # type: ignore[attr-defined]
        raise err

    return TTSPlaybackEvent(
        event_id=event_id or new_request_id(),
        request_id=str(request_id),
        intent_id=intent_id,
        text=str(text),
        emotion_tag=str(emotion_tag),
        status=status,
        reason_code=reason_code,
        stage=stage,
        ts_ms=int(ts_ms if ts_ms is not None else now_ms()),
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
    if not interrupted_request_id or not str(interrupted_request_id).strip():
        err = ValueError("BargeInEvent requires interrupted_request_id")
        err.reason_code = VOICE_SCHEMA_INVALID  # type: ignore[attr-defined]
        raise err

    if not evidence_ref or not str(evidence_ref).strip():
        err = ValueError("BargeInEvent requires evidence_ref")
        err.reason_code = VOICE_SCHEMA_INVALID  # type: ignore[attr-defined]
        raise err

    return BargeInEvent(
        event_id=event_id or new_request_id(),
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
        self._lock = threading.RLock()
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
                if _use_persistent_tts_worker():
                    model = _PersistentProcessSoVITSAdapter(config=self._config)
                    start_detail = model.start()
                    self._model = model
                    self._actual_device = self._config.device
                    self._backend_mode = "persistent_worker"
                    self._state = TtsState.READY
                    self._load_error = None
                    logger.info(
                        "GPT-SoVITS persistent worker loaded: %s on %s (%s)",
                        self._config.model_path,
                        self._config.device,
                        start_detail,
                    )
                    return TtsSynthResult(
                        success=True,
                        reason_code=None,
                        detail=f"persistent worker loaded on {self._config.device}: {start_detail}",
                    )
                model = _load_sovits_model(
                    model_path=self._config.model_path,
                    device=self._config.device,
                    ref_audio_path=self._config.ref_audio_path,
                    ref_text=self._config.ref_text,
                    voice_bank_path=self._config.voice_bank_path,
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
        speed_factor: float | None = None,
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

        normalized_language = _normalize_tts_language(language or self._config.language)
        if normalized_language is None:
            return TtsSynthResult(
                success=False,
                reason_code=VOICE_SCHEMA_INVALID,
                detail="unsupported tts language; supported values: zh, ja, auto",
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
                speed_factor=speed_factor,
                voice_profile=voice_profile,
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
        speed_factor: float | None,
        voice_profile: str | None,
    ) -> tuple[Any, int]:
        try:
            return _synthesize_audio(
                model=self._model,
                text=text,
                language=language,
                emotion_tag=emotion_tag,
                emotion_intensity=emotion_intensity,
                speed_factor=speed_factor,
                voice_profile=voice_profile,
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
                        speed_factor=speed_factor,
                        voice_profile=voice_profile,
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
                    speed_factor=speed_factor,
                    voice_profile=voice_profile,
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
                        speed_factor=speed_factor,
                        voice_profile=voice_profile,
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
        speed_factor: float | None,
        voice_profile: str | None,
    ) -> tuple[Any, int]:
        return _run_tts_fresh_process(
            config=self._config,
            text=text,
            text_language=language,
            emotion_tag=emotion_tag,
            emotion_intensity=emotion_intensity,
            speed_factor=speed_factor,
            voice_profile=voice_profile,
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
            "model_artifacts": _summarize_tts_model_artifacts(
                model_path=self._config.model_path,
                voice_bank_path=self._config.voice_bank_path,
            ),
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
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._on_playback_finished: Callable[[], None] | None = None
        self._last_playback_started_ts_ms: int | None = None
        self._last_playback_finished_ts_ms: int | None = None
        self._last_playback_error: str | None = None
        self._last_audio_stats: dict[str, Any] = {}

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
            audio_data, audio_stats = self._prepare_audio_for_playback(audio_data)
            with self._lock:
                self._last_audio_stats = audio_stats
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
                "last_audio_stats": dict(self._last_audio_stats),
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

    @staticmethod
    def _prepare_audio_for_playback(audio_data: Any) -> tuple[Any, dict[str, Any]]:
        """Normalize synthesized audio before it reaches the OS mixer."""
        try:
            import numpy as np
        except Exception:
            return audio_data, {"prepared": False, "reason": "numpy_unavailable"}

        if hasattr(audio_data, "detach"):
            audio_data = audio_data.detach()
        if hasattr(audio_data, "cpu"):
            audio_data = audio_data.cpu()
        if hasattr(audio_data, "numpy"):
            audio_data = audio_data.numpy()
        arr = np.asarray(audio_data, dtype=np.float32)
        if arr.size <= 0:
            return audio_data, {"prepared": False, "reason": "empty_audio"}
        if arr.ndim == 2 and arr.shape[0] <= 8 and arr.shape[1] > arr.shape[0]:
            arr = arr.T

        arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
        peak_before = float(np.max(np.abs(arr))) if arr.size else 0.0

        target_peak = _env_float("EDGE_TTS_PLAYBACK_TARGET_PEAK", 0.55, min_value=0.05, max_value=0.95)
        output_gain = _env_float("EDGE_TTS_PLAYBACK_GAIN", 0.65, min_value=0.05, max_value=1.0)
        scale = output_gain
        if peak_before > target_peak > 0:
            scale *= target_peak / peak_before
        prepared = np.clip(arr * scale, -0.98, 0.98).astype(np.float32, copy=False)
        peak_after = float(np.max(np.abs(prepared))) if prepared.size else 0.0
        stats = {
            "prepared": True,
            "dtype": str(prepared.dtype),
            "shape": list(prepared.shape),
            "peak_before": round(peak_before, 4),
            "peak_after": round(peak_after, 4),
            "target_peak": round(float(target_peak), 4),
            "gain": round(float(output_gain), 4),
            "scale": round(float(scale), 8),
        }
        return prepared, stats

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

def _resolve_tts_mouth_frame_interval_s() -> float:
    raw = str(os.environ.get("EDGE_TTS_MOUTH_FRAME_INTERVAL_S") or "").strip()
    if not raw:
        return 0.08
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.08
    return max(0.02, min(0.2, value))


def _compute_tts_mouth_energy_frames(
    *,
    audio_data: Any,
    sample_rate: int,
    frame_interval_s: float,
) -> list[float]:
    if audio_data is None:
        return []
    try:
        import numpy as np
    except Exception:
        return []
    try:
        if hasattr(audio_data, "detach"):
            audio_data = audio_data.detach().cpu().numpy()
        arr = np.asarray(audio_data, dtype=np.float32)
        if arr.size <= 0:
            return []
        if arr.ndim == 2:
            if arr.shape[0] <= 8 and arr.shape[1] > arr.shape[0]:
                arr = arr.T
            mono = np.mean(arr, axis=1)
        else:
            mono = arr.reshape(-1)
        if mono.size <= 0:
            return []

        peak = float(np.max(np.abs(mono))) if mono.size else 0.0
        if peak > 1.5:
            mono = mono / max(peak, 1.0)

        frame_size = max(1, int(max(sample_rate, 1) * frame_interval_s))
        values: list[float] = []
        for start in range(0, int(mono.shape[0]), frame_size):
            frame = mono[start : start + frame_size]
            if frame.size <= 0:
                continue
            values.append(float(np.sqrt(np.mean(np.square(frame)))))
        if not values:
            return []

        rms = np.asarray(values, dtype=np.float32)
        floor = float(np.percentile(rms, 12))
        ceil = float(np.percentile(rms, 96))
        if ceil <= floor + 1e-6:
            ceil = float(np.max(rms))
        scale = max(ceil - floor, 1e-6)
        normalized = np.clip((rms - floor) / scale, 0.0, 1.0)
        return [round(float(item), 4) for item in normalized.tolist()]
    except Exception:
        return []


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
        voice_profile = str(
            speak_event.get("voice_profile")
            or speak_event.get("style_note")
            or "default"
        )
        lang_hint = str(speak_event.get("lang_hint") or "").strip() or None
        # M26-I：透传 emotion_intensity
        raw_intensity = speak_event.get("emotion_intensity")
        emotion_intensity: float | None = None
        if raw_intensity is not None:
            try:
                emotion_intensity = float(raw_intensity)
            except (TypeError, ValueError):
                emotion_intensity = None
        raw_speed = speak_event.get("speed")
        speed_factor: float | None = None
        if raw_speed is not None:
            try:
                speed_factor = float(raw_speed)
            except (TypeError, ValueError):
                speed_factor = None

        result = engine.synthesize(
            text,
            language=lang_hint,
            emotion_tag=emotion_tag or None,
            emotion_intensity=emotion_intensity,
            speed_factor=speed_factor,
            voice_profile=voice_profile or None,
        )

        if not result.success:
            raise RuntimeError(
                f"TTS synthesis failed [{result.reason_code}]: {result.detail}"
            )
        audio_data = result.audio_data
        sample_rate = int(result.sample_rate or DEFAULT_SAMPLE_RATE)
        audio_data, expressive_timing = _apply_expressive_audio_timing(
            audio_data=audio_data,
            sample_rate=sample_rate,
            text=text,
            emotion_tag=emotion_tag or None,
            emotion_intensity=emotion_intensity,
        )
        duration_s = float(result.duration_s or 0.0)
        if audio_data is not None and hasattr(audio_data, "__len__"):
            duration_s = len(audio_data) / max(sample_rate, 1)
        mouth_frame_interval_s = _resolve_tts_mouth_frame_interval_s()
        mouth_frames = _compute_tts_mouth_energy_frames(
            audio_data=audio_data,
            sample_rate=sample_rate,
            frame_interval_s=mouth_frame_interval_s,
        )
        audio_path: str | None = None
        if _tts_export_requested(speak_event):
            audio_path = _export_tts_audio_wav(
                audio_data=audio_data,
                sample_rate=sample_rate,
                speak_event=speak_event,
            )

        synth_event = {
            "audio_ref": result.audio_ref,
            "audio_data": audio_data,
            "duration_s": round(duration_s, 4),
            "processing_time_s": result.processing_time_s,
            "sample_rate": sample_rate,
            "expressive_timing": expressive_timing,
        }
        if mouth_frames:
            synth_event["rms_frames"] = mouth_frames
            synth_event["mouth_frames"] = mouth_frames
            synth_event["mouth_frame_interval_s"] = round(mouth_frame_interval_s, 4)
            synth_event["mouth_frame_count"] = len(mouth_frames)
        if audio_path:
            synth_event["audio_path"] = audio_path
            synth_event["audio_mime"] = "audio/wav"
        return synth_event

    return _synthesize


def _tts_export_requested(speak_event: dict[str, Any]) -> bool:
    raw = speak_event.get("export_audio")
    if raw is None:
        raw = os.environ.get("EDGE_TTS_ALWAYS_EXPORT_AUDIO")
    return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}


def _export_tts_audio_wav(
    *,
    audio_data: Any,
    sample_rate: int,
    speak_event: dict[str, Any],
) -> str:
    import numpy as np

    if audio_data is None:
        raise RuntimeError("cannot export empty TTS audio")
    if hasattr(audio_data, "detach"):
        audio_data = audio_data.detach().cpu().numpy()
    arr = np.asarray(audio_data)
    if arr.size <= 0:
        raise RuntimeError("cannot export empty TTS audio")
    if arr.ndim == 2 and arr.shape[0] <= 8 and arr.shape[1] > arr.shape[0]:
        arr = arr.T
    if arr.ndim > 2:
        arr = arr.reshape((-1, arr.shape[-1]))

    arr_float = arr.astype(np.float32, copy=False)
    peak = float(np.max(np.abs(arr_float))) if arr_float.size else 0.0
    if peak <= 1.5:
        pcm = np.clip(arr_float, -1.0, 1.0) * 32767.0
    else:
        pcm = np.clip(arr_float, -32768.0, 32767.0)
    pcm_i16 = pcm.astype(np.int16, copy=False)
    if pcm_i16.ndim == 1:
        channels = 1
    else:
        channels = int(pcm_i16.shape[1])

    export_dir = _resolve_tts_export_dir()
    os.makedirs(export_dir, exist_ok=True)
    request_id = _safe_audio_export_token(str(speak_event.get("request_id") or "req"))
    utterance_id = _safe_audio_export_token(str(speak_event.get("utterance_id") or "utt"))
    ts_ms = int(time.time() * 1000)
    path = os.path.join(export_dir, f"{request_id}_{utterance_id}_{ts_ms}.wav")
    with wave.open(path, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(int(sample_rate or DEFAULT_SAMPLE_RATE))
        handle.writeframes(pcm_i16.tobytes())
    return os.path.abspath(path)


def _resolve_tts_export_dir() -> str:
    raw = str(os.environ.get("EDGE_TTS_EXPORT_DIR") or "").strip()
    if raw:
        return os.path.abspath(raw)
    return os.path.join(_resolve_tts_repo_root(), "runtime", "edge", "tts_exports")


def _safe_audio_export_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    return (token.strip("._-") or "tts")[:80]


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
    voice_bank_path: str | None,
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
            voice_bank_path=voice_bank_path,
            language=language,
        )


def _load_modern_sovits_model(
    *,
    model_root: str,
    device: str,
    ref_audio_path: str | None,
    ref_text: str | None,
    voice_bank_path: str | None,
    language: str,
) -> Any:
    if not os.path.isdir(model_root):
        raise ImportError(
            f"GPT-SoVITS model path does not exist: {model_root}. "
            "Set EDGE_TTS_MODEL_PATH to your GPT-SoVITS root directory."
        )

    _ensure_modern_sdk_paths(model_root)
    _ensure_jieba_fast_alias()
    _ensure_opencc_compat()
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

    custom_cfg = _build_modern_tts_custom_config(
        model_root=model_root,
        device=device,
        voice_bank_path=voice_bank_path,
    )
    tts = TTS(TTS_Config({"custom": custom_cfg}))

    manifest = _load_tts_model_manifest(model_root=model_root, voice_bank_path=voice_bank_path)
    _validate_tts_manifest_language(manifest=manifest, requested_language=language)
    manifest_references = _load_tts_manifest_references(
        model_root=model_root,
        manifest=manifest,
    )

    effective_ref_audio = str(ref_audio_path or "").strip() or None
    effective_ref_text = str(ref_text or "").strip()
    effective_ref_lang: str | None = None
    if not effective_ref_audio and manifest_references:
        default_ref = _select_tts_reference(manifest_references, emotion_tag="NEUTRAL")
        if default_ref is not None:
            effective_ref_audio = default_ref.audio_path
            effective_ref_text = default_ref.prompt_text
            effective_ref_lang = default_ref.prompt_lang
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
    default_reference = _TtsReference(
        audio_path=effective_ref_audio,
        prompt_text=effective_ref_text,
        prompt_lang=effective_ref_lang or language,
    )
    reference_bank = dict(manifest_references)
    reference_bank.setdefault("NEUTRAL", default_reference)
    reference_bank.setdefault("DEFAULT", default_reference)

    logger.info(
        "TTS reference selected: audio=%s prompt_text_chars=%d bank_size=%d",
        effective_ref_audio,
        len(effective_ref_text),
        len(reference_bank),
    )

    return _ModernSoVITSAdapter(
        tts=tts,
        reference_bank=reference_bank,
        default_language=language,
        working_directory=model_root,
    )


class _ModernSoVITSAdapter:
    """Adapter exposing a legacy-like synthesize() API over TTS_infer_pack.TTS."""

    def __init__(
        self,
        *,
        tts: Any,
        reference_bank: dict[str, _TtsReference],
        default_language: str,
        working_directory: str,
    ) -> None:
        self._tts = tts
        self._reference_bank = dict(reference_bank)
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
        voice_profile: str | None = None,
    ) -> dict[str, Any]:
        import numpy as np

        text_lang = self._normalize_language(language or self._default_language)
        reference = _select_tts_reference(
            self._reference_bank,
            emotion_tag=emotion,
            voice_profile=voice_profile,
        )
        if reference is None:
            raise RuntimeError("GPT-SoVITS reference bank is empty.")
        prompt_lang = self._normalize_language(reference.prompt_lang or self._default_language)
        prompt_text = reference.prompt_text

        inputs = {
            "text": text,
            "text_lang": text_lang,
            "ref_audio_path": reference.audio_path,
            "aux_ref_audio_paths": list(reference.aux_audio_paths),
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
        normalized = _normalize_tts_language(language) or "zh"

        languages = set(getattr(getattr(self._tts, "configs", None), "languages", []) or [])
        if normalized in languages:
            return normalized
        if "auto" in languages:
            return "auto"
        if languages:
            return sorted(languages)[0]
        return "zh"


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
        voice_profile: str | None = None,
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
            speed_factor=speed_factor,
            voice_profile=voice_profile,
        )
        return {
            "audio": audio,
            "sample_rate": sample_rate,
        }

    def close(self) -> None:
        return None


class _PersistentProcessSoVITSAdapter:
    """Persistent GPT-SoVITS worker backed by the model runtime Python."""

    def __init__(self, *, config: TtsConfig) -> None:
        self._config = config
        self._process: subprocess.Popen[str] | None = None
        self._stderr_handle: Any = None
        self._stdout_queue: queue.Queue[str] = queue.Queue()
        self._reader_thread: threading.Thread | None = None
        self._lock = threading.RLock()

    def start(self) -> str:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return "already running"
            self._start_process_locked()
            ready = self._read_message(
                expected_event="ready",
                request_id=None,
                timeout_s=_env_float("EDGE_TTS_WORKER_START_TIMEOUT_S", 120.0, min_value=5.0, max_value=300.0),
            )
            if not bool(ready.get("success")):
                raise RuntimeError(str(ready.get("detail") or "persistent worker failed to start"))
            detail = str(ready.get("detail") or "ready")
            load_s = ready.get("load_s")
            if load_s is not None:
                detail = f"{detail}; load_s={load_s}"
            return detail

    def synthesize(
        self,
        *,
        text: str,
        language: str,
        emotion: str | None = None,
        speed_factor: float | None = None,
        energy_factor: float | None = None,
        voice_profile: str | None = None,
    ) -> dict[str, Any]:
        import numpy as np

        with self._lock:
            self.start()
            fd, output_path = tempfile.mkstemp(prefix="edge_tts_worker_", suffix=".npz")
            os.close(fd)
            request_id = new_request_id()
            payload = {
                "request_id": request_id,
                "text": text,
                "language": language,
                "emotion_tag": emotion,
                "voice_profile": voice_profile,
                "emotion_intensity": _infer_emotion_intensity_from_prosody(
                    speed_factor=speed_factor,
                    energy_factor=energy_factor,
                ),
                "speed_factor": speed_factor,
                "output_path": output_path,
            }
            try:
                process = self._require_process()
                assert process.stdin is not None
                process.stdin.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
                process.stdin.flush()
                response = self._read_message(
                    expected_event="synth_result",
                    request_id=request_id,
                    timeout_s=_env_float(
                        "EDGE_TTS_WORKER_SYNTH_TIMEOUT_S",
                        max(float(self._config.synth_timeout_s) + 15.0, 45.0),
                        min_value=5.0,
                        max_value=300.0,
                    ),
                )
                if not bool(response.get("success")):
                    raise RuntimeError(str(response.get("detail") or "persistent worker synth failed"))
                with np.load(output_path) as data:
                    audio = np.asarray(data["audio"], dtype=np.float32)
                    sample_rate = int(data["sample_rate"])
                return {
                    "audio": audio,
                    "sample_rate": sample_rate,
                }
            finally:
                try:
                    os.remove(output_path)
                except OSError:
                    pass

    def close(self) -> None:
        with self._lock:
            process = self._process
            self._process = None
            if process is not None and process.poll() is None:
                try:
                    assert process.stdin is not None
                    process.stdin.write(json.dumps({"command": "shutdown"}, separators=(",", ":")) + "\n")
                    process.stdin.flush()
                    process.wait(timeout=3)
                except Exception:
                    try:
                        process.terminate()
                        process.wait(timeout=3)
                    except Exception:
                        try:
                            process.kill()
                        except Exception:
                            pass
            if self._stderr_handle is not None:
                try:
                    self._stderr_handle.close()
                except Exception:
                    pass
                self._stderr_handle = None

    def _start_process_locked(self) -> None:
        repo_root = _resolve_tts_repo_root()
        logs_dir = os.path.join(repo_root, "runtime", "edge", "logs")
        os.makedirs(logs_dir, exist_ok=True)
        self._stderr_handle = open(
            os.path.join(logs_dir, "tts_worker.stderr.log"),
            "a",
            encoding="utf-8",
            errors="ignore",
        )
        env = dict(os.environ)
        env["EDGE_TTS_PERSISTENT_WORKER_CHILD"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONPATH"] = repo_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        cmd = [
            _resolve_tts_worker_python(self._config),
            "-u",
            os.path.join(repo_root, "edge_client", "actuators", "tts_persistent_worker.py"),
            "--model-path",
            str(self._config.model_path),
            "--device",
            str(self._config.device),
            "--sample-rate",
            str(self._config.sample_rate),
            "--language",
            str(self._config.language),
        ]
        if self._config.ref_audio_path:
            cmd.extend(["--ref-audio-path", str(self._config.ref_audio_path)])
        if self._config.ref_text:
            cmd.extend(["--ref-text", str(self._config.ref_text)])
        if self._config.voice_bank_path:
            cmd.extend(["--voice-bank-path", str(self._config.voice_bank_path)])
        self._process = subprocess.Popen(
            cmd,
            cwd=repo_root,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr_handle,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self._stdout_queue = queue.Queue()
        self._reader_thread = threading.Thread(
            target=self._read_stdout_loop,
            name="edge-tts-worker-stdout",
            daemon=True,
        )
        self._reader_thread.start()

    def _read_stdout_loop(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            self._stdout_queue.put(line)

    def _require_process(self) -> subprocess.Popen[str]:
        process = self._process
        if process is None or process.poll() is not None:
            self._start_process_locked()
        process = self._process
        if process is None or process.poll() is not None:
            raise RuntimeError("persistent worker process is not running")
        return process

    def _read_message(
        self,
        *,
        expected_event: str,
        request_id: str | None,
        timeout_s: float,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + float(timeout_s)
        while True:
            process = self._process
            if process is not None and process.poll() is not None and self._stdout_queue.empty():
                raise RuntimeError(f"persistent worker exited with code {process.returncode}")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"persistent worker timed out waiting for {expected_event}")
            try:
                raw = self._stdout_queue.get(timeout=min(remaining, 0.25))
            except queue.Empty:
                continue
            try:
                payload = json.loads(str(raw or "").strip())
            except Exception:
                logger.debug("ignoring non-json TTS worker stdout: %s", str(raw or "").strip()[:200])
                continue
            if str(payload.get("event") or "") != expected_event:
                continue
            if request_id is not None and str(payload.get("request_id") or "") != str(request_id):
                continue
            return dict(payload)


def _sys_path_key(path: str) -> str:
    try:
        return os.path.normcase(os.path.abspath(path))
    except Exception:
        return os.path.normcase(str(path or ""))


def _preload_preferred_torch(runtime_site_packages: str) -> None:
    if not runtime_site_packages or not os.path.isdir(runtime_site_packages):
        return

    runtime_key = _sys_path_key(runtime_site_packages)
    original_path = list(sys.path)
    filtered_path = [path for path in original_path if _sys_path_key(path) != runtime_key]

    existing_torch = sys.modules.get("torch")
    existing_file = str(getattr(existing_torch, "__file__", "") or "") if existing_torch is not None else ""
    if existing_torch is not None and getattr(existing_torch, "nn", None) is not None:
        return
    if existing_torch is not None and existing_file and _sys_path_key(existing_file).startswith(runtime_key):
        for name in list(sys.modules):
            if name == "torch" or name.startswith("torch."):
                sys.modules.pop(name, None)

    try:
        import importlib

        sys.path[:] = filtered_path
        torch = importlib.import_module("torch")
        if getattr(torch, "nn", None) is None:
            raise ImportError("preferred torch import has no nn module")
    except Exception as exc:
        logger.debug("preferred torch preload failed before GPT-SoVITS runtime path append: %s", exc)
    finally:
        sys.path[:] = original_path


def _ensure_modern_sdk_paths(model_root: str) -> None:
    runtime_site_packages = os.path.join(model_root, "runtime", "Lib", "site-packages")
    runtime_key = _sys_path_key(runtime_site_packages)
    if runtime_site_packages and os.path.isdir(runtime_site_packages):
        sys.path[:] = [path for path in sys.path if _sys_path_key(path) != runtime_key]
        _preload_preferred_torch(runtime_site_packages)

    paths = [
        model_root,
        os.path.join(model_root, "GPT_SoVITS"),
        os.path.join(model_root, "GPT_SoVITS", "eres2net"),
    ]
    for path in reversed(paths):
        if path and os.path.isdir(path) and path not in sys.path:
            sys.path.insert(0, path)
    if (
        runtime_site_packages
        and os.path.isdir(runtime_site_packages)
        and runtime_site_packages not in sys.path
    ):
        sys.path.append(runtime_site_packages)
    _ensure_model_runtime_dll_dirs(runtime_site_packages)


def _ensure_model_runtime_dll_dirs(site_packages: str) -> None:
    if not site_packages or not os.path.isdir(site_packages):
        return
    candidates = [
        os.path.join(site_packages, "opencc", "clib", "bin"),
    ]
    for path in candidates:
        if not os.path.isdir(path):
            continue
        normalized = os.path.normcase(os.path.abspath(path))
        if normalized in _DLL_DIRECTORY_PATHS:
            continue
        _DLL_DIRECTORY_PATHS.add(normalized)
        if hasattr(os, "add_dll_directory"):
            try:
                _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(path))  # type: ignore[attr-defined]
                continue
            except OSError as exc:
                logger.debug("failed to add model runtime dll directory %s: %s", path, exc)
        current_path = os.environ.get("PATH", "")
        if normalized not in {
            os.path.normcase(os.path.abspath(segment))
            for segment in current_path.split(os.pathsep)
            if segment
        }:
            os.environ["PATH"] = path + os.pathsep + current_path


def _ensure_jieba_fast_alias() -> None:
    if "jieba_fast" in sys.modules:
        return
    try:
        import jieba as jieba_fallback
    except ImportError:
        return
    sys.modules["jieba_fast"] = jieba_fallback


def _ensure_opencc_compat() -> None:
    try:
        from opencc import OpenCC  # type: ignore[import-not-found]

        converted = OpenCC("s2tw").convert("测试")
        if isinstance(converted, str):
            return
    except Exception as exc:
        logger.warning("OpenCC unavailable for GPT-SoVITS; using identity converter: %s", exc)

    sys.modules.pop("opencc", None)
    sys.modules.pop("opencc.clib", None)
    sys.modules.pop("opencc.clib.opencc_clib", None)

    module = types.ModuleType("opencc")

    class _IdentityOpenCC:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            return None

        @staticmethod
        def convert(text: Any) -> str:
            return str(text)

    module.OpenCC = _IdentityOpenCC  # type: ignore[attr-defined]
    module.CONFIGS = []  # type: ignore[attr-defined]
    module.__version__ = "identity-fallback"  # type: ignore[attr-defined]
    sys.modules["opencc"] = module


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


def _use_persistent_tts_worker() -> bool:
    if str(os.environ.get("EDGE_TTS_PERSISTENT_WORKER_CHILD") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return False
    raw = str(os.environ.get("EDGE_TTS_PERSISTENT_WORKER_ENABLED") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _resolve_tts_worker_python(config: TtsConfig) -> str:
    override = str(os.environ.get("EDGE_TTS_WORKER_PYTHON") or "").strip()
    if override:
        return override
    model_runtime_python = os.path.join(str(config.model_path), "runtime", "python.exe")
    if os.path.isfile(model_runtime_python):
        return model_runtime_python
    return sys.executable


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
    speed_factor: float | None = None,
    voice_profile: str | None = None,
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
    if config.voice_bank_path:
        cmd.extend(["--voice-bank-path", str(config.voice_bank_path)])
    if emotion_tag:
        cmd.extend(["--emotion-tag", str(emotion_tag)])
    if voice_profile:
        cmd.extend(["--voice-profile", str(voice_profile)])
    if emotion_intensity is not None:
        cmd.extend(["--emotion-intensity", str(float(emotion_intensity))])
    if speed_factor is not None:
        cmd.extend(["--speed-factor", str(float(speed_factor))])

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


def _build_modern_tts_custom_config(
    *,
    model_root: str,
    device: str,
    voice_bank_path: str | None = None,
) -> dict[str, Any]:
    t2s_weights_path, vits_weights_path = _resolve_modern_weights(
        model_root,
        voice_bank_path=voice_bank_path,
    )
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


def _resolve_modern_weights(model_root: str, *, voice_bank_path: str | None = None) -> tuple[str, str]:
    manifest_weights = _resolve_manifest_weight_paths(model_root, voice_bank_path=voice_bank_path)
    if manifest_weights is not None:
        return manifest_weights

    # 2) Prefer model root config.py assignments (sovits_path/gpt_path).
    config_path = os.path.join(model_root, "config.py")
    rel_sovits = _extract_python_string_assignment(config_path, "sovits_path")
    rel_gpt = _extract_python_string_assignment(config_path, "gpt_path")
    if rel_sovits and rel_gpt:
        sovits_abs = os.path.normpath(os.path.join(model_root, rel_sovits))
        gpt_abs = os.path.normpath(os.path.join(model_root, rel_gpt))
        if os.path.isfile(sovits_abs) and os.path.isfile(gpt_abs):
            return gpt_abs, sovits_abs

    # 3) Fallback to latest available checkpoints in preferred directories.
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


def _summarize_tts_model_artifacts(*, model_path: str, voice_bank_path: str | None) -> dict[str, Any]:
    model_root = os.path.abspath(model_path)
    summary: dict[str, Any] = {
        "model_root": model_root,
        "manifest": None,
        "weights": None,
    }

    try:
        manifest = _load_tts_model_manifest(model_root=model_root, voice_bank_path=voice_bank_path)
        if manifest:
            raw_refs = manifest.get("references")
            valid_refs = _load_tts_manifest_references(model_root=model_root, manifest=manifest)
            summary["manifest"] = {
                "path": manifest.get("_manifest_path"),
                "schema_version": manifest.get("schema_version"),
                "model_id": str(manifest.get("model_id") or "").strip() or None,
                "project": str(manifest.get("project") or "").strip() or None,
                "voice_language": str(manifest.get("voice_language") or "").strip() or None,
                "reference_count": len(raw_refs) if isinstance(raw_refs, list) else 0,
                "valid_reference_count": len(valid_refs),
                "reference_tags": sorted(valid_refs.keys()),
                "has_weights": isinstance(manifest.get("weights"), dict),
            }
    except Exception as exc:
        summary["manifest"] = {"error": str(exc)}

    try:
        t2s_weights_path, vits_weights_path = _resolve_modern_weights(
            model_root,
            voice_bank_path=voice_bank_path,
        )
        summary["weights"] = {
            "t2s": t2s_weights_path,
            "vits": vits_weights_path,
            "t2s_exists": os.path.isfile(t2s_weights_path),
            "vits_exists": os.path.isfile(vits_weights_path),
            "version": _detect_model_version(
                t2s_weights_path=t2s_weights_path,
                vits_weights_path=vits_weights_path,
            ),
        }
    except Exception as exc:
        summary["weights"] = {"error": str(exc)}

    return summary


def _resolve_manifest_weight_paths(model_root: str, *, voice_bank_path: str | None = None) -> tuple[str, str] | None:
    manifest = _load_tts_model_manifest(model_root=model_root, voice_bank_path=voice_bank_path)
    weights = manifest.get("weights")
    if not isinstance(weights, dict):
        return None

    raw_t2s = _first_manifest_weight_value(
        weights,
        "t2s",
        "gpt",
        "gpt_path",
        "t2s_weights_path",
    )
    raw_vits = _first_manifest_weight_value(
        weights,
        "vits",
        "sovits",
        "sovits_path",
        "vits_weights_path",
    )
    if not raw_t2s or not raw_vits:
        return None

    t2s_path = _resolve_model_root_path(model_root, raw_t2s)
    vits_path = _resolve_model_root_path(model_root, raw_vits)
    if os.path.isfile(t2s_path) and os.path.isfile(vits_path):
        return t2s_path, vits_path

    logger.warning(
        "GPT-SoVITS manifest weights are incomplete or missing: t2s=%s exists=%s vits=%s exists=%s",
        t2s_path,
        os.path.isfile(t2s_path),
        vits_path,
        os.path.isfile(vits_path),
    )
    return None


def _first_manifest_weight_value(weights: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = str(weights.get(key) or "").strip()
        if value:
            return value
    return None


def _resolve_model_root_path(model_root: str, path: str) -> str:
    raw = str(path or "").strip()
    if os.path.isabs(raw):
        return os.path.normpath(raw)
    return os.path.normpath(os.path.join(model_root, raw))


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
        files = [
            os.path.join(base, f)
            for f in os.listdir(base)
            if f.lower().endswith(suffix.lower())
        ]
        if files:
            return max(files, key=lambda path: (os.path.getmtime(path), os.path.basename(path)))
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


def _load_tts_model_manifest(*, model_root: str, voice_bank_path: str | None) -> dict[str, Any]:
    candidates: list[str] = []
    if voice_bank_path:
        candidates.append(str(voice_bank_path))
    candidates.append(os.path.join(model_root, "natsume_gpt_sovits_model_manifest.json"))

    for candidate in candidates:
        path = str(candidate or "").strip()
        if not path:
            continue
        if not os.path.isabs(path):
            path = os.path.join(model_root, path)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8-sig") as handle:
                payload = json.load(handle)
            if isinstance(payload, dict):
                payload["_manifest_path"] = path
                return payload
        except Exception as exc:
            raise RuntimeError(f"invalid GPT-SoVITS model manifest: {path}: {exc}") from exc
    return {}


def _validate_tts_manifest_language(*, manifest: dict[str, Any], requested_language: str) -> None:
    voice_language = _normalize_tts_language(str(manifest.get("voice_language") or ""))
    requested = _normalize_tts_language(requested_language)
    if not voice_language or not requested or requested == "auto":
        return
    allow_mismatch = str(os.environ.get("EDGE_TTS_ALLOW_LANGUAGE_MISMATCH") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if allow_mismatch or voice_language == requested:
        return
    model_id = str(manifest.get("model_id") or manifest.get("project") or "unknown").strip()
    raise RuntimeError(
        "GPT-SoVITS model language mismatch: "
        f"model={model_id!r} voice_language={voice_language!r}, requested={requested!r}. "
        "Train/package the matching voice model on the host node, or set EDGE_TTS_LANGUAGE correctly."
    )


def _load_tts_manifest_references(
    *,
    model_root: str,
    manifest: dict[str, Any],
) -> dict[str, _TtsReference]:
    raw_refs = manifest.get("references")
    if not isinstance(raw_refs, list):
        return {}

    out: dict[str, _TtsReference] = {}
    for item in raw_refs:
        if not isinstance(item, dict):
            continue
        raw_path = str(item.get("audio_path") or "").strip()
        if not raw_path:
            continue
        audio_path = raw_path if os.path.isabs(raw_path) else os.path.join(model_root, raw_path)
        audio_path = os.path.normpath(audio_path)
        if not os.path.isfile(audio_path):
            continue
        voice_style_key = _normalize_tts_emotion_key(item.get("voice_style"))
        emotion_key = _normalize_tts_emotion_key(item.get("emotion_tag"))
        if not voice_style_key and not emotion_key:
            emotion_key = "NEUTRAL"
        aux_audio_paths: list[str] = []
        raw_aux = item.get("aux_audio_paths")
        if isinstance(raw_aux, list):
            for raw_aux_path in raw_aux[:4]:
                resolved_aux = str(raw_aux_path or "").strip()
                if not resolved_aux:
                    continue
                resolved_aux = (
                    resolved_aux
                    if os.path.isabs(resolved_aux)
                    else os.path.join(model_root, resolved_aux)
                )
                resolved_aux = os.path.normpath(resolved_aux)
                if os.path.isfile(resolved_aux) and resolved_aux != audio_path:
                    aux_audio_paths.append(resolved_aux)
        reference = _TtsReference(
            audio_path=audio_path,
            prompt_text=str(item.get("prompt_text") or "").strip(),
            prompt_lang=_normalize_tts_language(str(item.get("prompt_lang") or "")),
            aux_audio_paths=tuple(aux_audio_paths),
        )
        for key in (voice_style_key, emotion_key):
            if key:
                out.setdefault(key, reference)
                if key == "NEUTRAL":
                    out.setdefault("DEFAULT", reference)
    return out


def _normalize_tts_emotion_key(value: Any) -> str:
    normalized = str(value or "").strip().upper()
    return re.sub(r"[^A-Z0-9_]+", "_", normalized).strip("_")


def _select_tts_reference(
    reference_bank: dict[str, _TtsReference],
    *,
    emotion_tag: str | None,
    voice_profile: str | None = None,
) -> _TtsReference | None:
    if not reference_bank:
        return None
    profile_key = _normalize_tts_emotion_key(voice_profile)
    emotion_key = _normalize_tts_emotion_key(emotion_tag)
    aliases = {
        "COMFORT": "CALM",
        "SOOTHING": "CALM",
        "JOY": "HAPPY",
        "HAPPINESS": "HAPPY",
        "PLAYFUL": "HAPPY",
        "LOVE": "LOVING",
        "AFFECTION": "LOVING",
        "COQUETTISH": "LOVING",
        "SADNESS": "SAD",
        "EXCITEMENT": "EXCITED",
    }
    for candidate in (
        profile_key,
        aliases.get(profile_key, ""),
        emotion_key,
        aliases.get(emotion_key, ""),
        "NEUTRAL",
        "DEFAULT",
    ):
        if candidate and candidate in reference_bank:
            return reference_bank[candidate]
    return next(iter(reference_bank.values()))


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


def _normalize_tts_language(language: str | None) -> str | None:
    normalized = str(language or "").strip().lower()
    if not normalized:
        return "zh"
    return _TTS_LANGUAGE_ALIASES.get(normalized)


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
    speed_factor: float | None = None,
    voice_profile: str | None = None,
    timeout_s: float,
) -> tuple[Any, int]:
    """Run synthesis on model. Returns (audio_ndarray, sample_rate).

    Raises TimeoutError if synthesis exceeds timeout_s.
    """
    import numpy as np

    t0 = time.monotonic()

    # Keep neutral speech model-native, while letting emotion nudge speed/energy
    # inside narrow bounds. Explicit speed/env overrides remain manual controls.
    speed_factor = _resolve_tts_speed_factor(
        requested_speed=speed_factor,
        emotion_intensity=emotion_intensity,
        emotion_tag=emotion_tag,
    )
    energy_factor = _resolve_tts_energy_factor(
        emotion_intensity=emotion_intensity,
        emotion_tag=emotion_tag,
    )

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
            voice_profile=voice_profile,
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
