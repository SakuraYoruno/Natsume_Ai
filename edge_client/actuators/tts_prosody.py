"""Bounded emotional prosody and expressive timing for Edge TTS."""

from __future__ import annotations

import logging
import math
import os
import re
from dataclasses import dataclass
from typing import Any


logger = logging.getLogger("Natsume.edge.tts")

def _clamp_float(value: float, minimum: float, maximum: float) -> float:
    # 所有韵律旋钮最终经过同一有限区间门槛；NaN/无穷值退到下界，避免环境变量或
    # 上游表达计划把无法表示的数字传入音频处理库。
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
    # 情绪标签只是选择受维护的预设，未知标签回落到中性值，不让开放文本隐式改变
    # 语速、响度或呼吸等具身输出参数。
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
    # 以全局基线为起点再按强度向 profile 边界插值：这使部署侧默认值仍是权威，
    # 情绪只能在为该 profile 预留的安全范围内产生偏移。
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
    # 显式请求的速度优先，但同样受环境配置的硬上下界约束；未指定时才允许情绪
    # profile 对默认速度做有限调节。
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
    requested_energy: float | None = None,
    emotion_intensity: float | None,
    emotion_tag: str | None = None,
) -> float:
    # ExpressionPlan 的 energy 是 0..1 语义轴，不直接等同模型增益；此处负责把它
    # 映射为受限幅度倍率，从而避免“高兴”或异常输入造成削波。
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
    if requested_energy is not None:
        try:
            normalized = _clamp_float(float(requested_energy), 0.0, 1.0)
        except (TypeError, ValueError):
            normalized = 0.5
        # ExpressionPlan energy is a 0..1 expressive axis, while the backend
        # consumes an amplitude multiplier.  Keep 0.5 neutral and bounded.
        requested_factor = 1.0 + (normalized - 0.5) * 0.56
        return round(_clamp_float(requested_factor, min_energy, max_energy), 3)
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


def _apply_synthesized_prosody(
    audio_data: Any,
    sample_rate: int,
    *,
    energy_factor: float | None,
    pitch_semitones: float | None,
) -> Any:
    """Apply controls GPT-SoVITS does not natively expose.

    The model supports speed directly.  Energy and pitch are applied after
    synthesis so Host ExpressionPlan values no longer disappear at the Edge.
    Pitch uses librosa when present in the voice-model runtime; energy remains
    available with only numpy.
    """

    import numpy as np

    # 这是模型输出进入播放缓存前的最后一道归一化层：兼容 tensor/PCM/float，
    # 同时将非有限采样清零，保证后续实时音频设备不会收到非法波形。
    if hasattr(audio_data, "detach"):
        audio_data = audio_data.detach()
    if hasattr(audio_data, "cpu"):
        audio_data = audio_data.cpu()
    if hasattr(audio_data, "numpy"):
        audio_data = audio_data.numpy()
    raw_audio = np.asarray(audio_data)
    if raw_audio.size == 0:
        return raw_audio.astype(np.float32, copy=False)

    # GPT-SoVITS TTS.run() returns signed PCM (normally int16), while older
    # adapters can return normalized floats.  Scale PCM before applying pitch
    # or energy; otherwise the later limiter turns most samples into plateaus.
    if np.issubdtype(raw_audio.dtype, np.integer):
        info = np.iinfo(raw_audio.dtype)
        pcm_scale = float(max(abs(info.min), abs(info.max)))
        audio = raw_audio.astype(np.float32) / pcm_scale
    else:
        audio = raw_audio.astype(np.float32, copy=False)
        finite_peak = (
            float(np.max(np.abs(audio[np.isfinite(audio)])))
            if np.any(np.isfinite(audio))
            else 0.0
        )
        if finite_peak > 8.0:
            pcm_scale = 32768.0 if finite_peak <= 32768.0 * 1.25 else finite_peak
            audio = audio / pcm_scale
    audio = np.nan_to_num(audio, nan=0.0, posinf=0.0, neginf=0.0).astype(
        np.float32,
        copy=False,
    )

    pitch = _clamp_float(float(pitch_semitones or 0.0), -2.0, 2.0)
    if abs(pitch) >= 0.01:
        try:
            import librosa
        except ImportError:
            # 音高是可选增强；运行时缺少 librosa 时保留已合成音频，不能让可选依赖
            # 缺失变成整段回复无法播放。
            logger.warning("TTS pitch requested but librosa is unavailable; preserving unshifted audio")
        else:
            audio = np.asarray(
                librosa.effects.pitch_shift(audio, sr=max(1, int(sample_rate)), n_steps=pitch),
                dtype=np.float32,
            )

    gain = float(energy_factor if energy_factor is not None else 1.0)
    if abs(gain - 1.0) >= 0.001:
        audio = (audio * gain).astype(np.float32, copy=False)

    # Peak-normalize only when necessary.  Hard clipping here audibly destroys
    # the model's dynamics and was the source of the previous "炸音" output.
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 0.98:
        audio = (audio * (0.98 / peak)).astype(np.float32, copy=False)
    return audio.astype(np.float32, copy=False)


_EXPRESSIVE_PUNCT_RE = re.compile(r"([^，,。.!！?？；;：:\n…]+)([，,。.!！?？；;：:\n…]*)")


def _expressive_timing_enabled() -> bool:
    raw = str(os.environ.get("EDGE_TTS_EXPRESSIVE_TIMING", "false")).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _split_expressive_text(text: str) -> list[tuple[str, str]]:
    # 停顿属于后处理的展示层，不改变送入模型的文本。段数设置上限，控制一次合成
    # 最多插入的边界数量，避免超长回复被切成不自然的密集片段。
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
    # 标点对应的字符比例只是粗略时间估计；在其附近寻找能量低谷再切分，可显著减少
    # 在元音或爆破音中间插静音造成的咔嗒声。
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
    # 呼吸噪声使用由文本和强度导出的固定种子：同一输入可重放相近结果，也不需要
    # 从外部随机源获取状态；profile 为零时严格返回静音。
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
    # 这条实验性时序链由显式开关控制，默认关闭；任何依赖缺失、形状不支持或边界
    # 搜索失败均返回原音频/安全退化结果，而不是损坏主 TTS 交付。
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
        # 只有每个文本段都找到有效切点才重组波形，避免部分插入导致语义节奏和诊断
        # 元数据不一致。
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
        # 每段边缘淡化后再插入静音/呼吸，降低拼接突变；暂停时长来自受限 profile，
        # 不能由标点数量或情绪强度无限放大。
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
