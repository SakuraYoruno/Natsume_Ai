"""上下文感知 TTS 路由器（Phase X 方案 6）。

职责：根据 LLM 输出文本的情感色彩 + 当前情绪状态动态调整 TTS 参数。
设计：在 Host 端计算参数，通过 Edge RPC 传递给 TTS 引擎。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from host_brain.channels.vts_runtime_mapping import normalize_runtime_emotion_tag

_logger = logging.getLogger("Natsume.tts.context_router")

# ---------------------------------------------------------------------------
# TTS 情感风格预设
# key 使用 canonical VTS emotion_tag 的小写形式，所有输入经
# vts_runtime_mapping.normalize_runtime_emotion_tag() 归一化后直接查找。
# VTS 别名链（VTS_EMOTION_TAG_ALIASES）会自动将 COQUETTISH→LOVING、
# SERIOUS→THINKING 等转换到 canonical tag，无需本文件额外维护映射。
# ---------------------------------------------------------------------------
@dataclass
class TtsStyleParams:
    """TTS 风格参数。"""
    speed_scale: float = 1.0       # 语速倍率 [0.7, 1.3]
    pitch_shift: float = 0.0       # 音调偏移 [-2.0, 2.0] semitones
    energy_scale: float = 1.0      # 能量/音量 [0.7, 1.3]
    emotion_tag: str = "neutral"   # 情感标签（canonical VTS tag 小写）
    style_note: str = ""           # 给 TTS 引擎的风格提示
    lang_hint: str = "zh"          # 语言提示 zh/ja

    def as_dict(self) -> dict[str, Any]:
        return {
            "speed_scale": round(self.speed_scale, 2),
            "pitch_shift": round(self.pitch_shift, 2),
            "energy_scale": round(self.energy_scale, 2),
            "emotion_tag": self.emotion_tag,
            "style_note": self.style_note,
            "lang_hint": self.lang_hint,
        }


EMOTION_STYLE_MAP: dict[str, TtsStyleParams] = {
    # positive / warm
    "happy": TtsStyleParams(speed_scale=1.10, pitch_shift=0.5, energy_scale=1.10, emotion_tag="happy", style_note="活泼、明亮"),
    "excited": TtsStyleParams(speed_scale=1.15, pitch_shift=1.0, energy_scale=1.20, emotion_tag="excited", style_note="兴奋、高能量"),
    "grateful": TtsStyleParams(speed_scale=0.92, pitch_shift=0.2, energy_scale=1.00, emotion_tag="grateful", style_note="感激、温暖"),
    "proud": TtsStyleParams(speed_scale=0.95, pitch_shift=0.3, energy_scale=1.05, emotion_tag="proud", style_note="自信、得意"),
    # gentle / soft
    "calm": TtsStyleParams(speed_scale=0.85, pitch_shift=-0.5, energy_scale=0.90, emotion_tag="calm", style_note="温柔、缓慢、轻声"),
    "loving": TtsStyleParams(speed_scale=0.92, pitch_shift=0.5, energy_scale=0.95, emotion_tag="loving", style_note="撒娇、软萌"),
    "embarrassed": TtsStyleParams(speed_scale=0.90, pitch_shift=0.3, energy_scale=0.90, emotion_tag="embarrassed", style_note="羞涩、小声"),
    # low energy
    "sad": TtsStyleParams(speed_scale=0.85, pitch_shift=-0.5, energy_scale=0.85, emotion_tag="sad", style_note="低沉、柔和"),
    "anxious": TtsStyleParams(speed_scale=1.00, pitch_shift=-0.2, energy_scale=0.90, emotion_tag="anxious", style_note="不安、犹豫"),
    "bored": TtsStyleParams(speed_scale=0.95, pitch_shift=-0.3, energy_scale=0.88, emotion_tag="bored", style_note="平淡、无精打采"),
    # high activation
    "angry": TtsStyleParams(speed_scale=1.05, pitch_shift=-0.5, energy_scale=1.10, emotion_tag="angry", style_note="严厉、低沉有力"),
    "frustrated": TtsStyleParams(speed_scale=1.00, pitch_shift=-0.3, energy_scale=1.05, emotion_tag="frustrated", style_note="烦躁、短促"),
    "surprised": TtsStyleParams(speed_scale=1.10, pitch_shift=0.8, energy_scale=1.15, emotion_tag="surprised", style_note="惊讶、上扬"),
    # neutral / cognitive
    "thinking": TtsStyleParams(speed_scale=0.92, pitch_shift=-0.1, energy_scale=0.95, emotion_tag="thinking", style_note="沉思、谨慎"),
    "confused": TtsStyleParams(speed_scale=0.90, pitch_shift=0.1, energy_scale=0.95, emotion_tag="confused", style_note="困惑、迟疑"),
    # default
    "neutral": TtsStyleParams(),
}

# ---------------------------------------------------------------------------
# 文本→情感分析关键词
# ---------------------------------------------------------------------------
_COMFORT_PATTERNS = re.compile(
    r"没关系|不要紧|抱抱|辛苦了|心疼|休息吧|我在|陪你|别难过|没事的",
)
_HAPPY_PATTERNS = re.compile(
    r"太好了|开心|高兴|棒|厉害|恭喜|好耶|万岁|哈哈|嘿嘿",
)
_PLAYFUL_PATTERNS = re.compile(
    r"哼|笨蛋|人家|嘻嘻|偷笑|才不是|下不为例|真是的|拿你没办法",
)
_SAD_PATTERNS = re.compile(
    r"难过|伤心|哭|眼泪|心痛|舍不得|遗憾|可惜",
)
_SERIOUS_PATTERNS = re.compile(
    r"重要|注意|必须|一定要|小心|警告|认真|严肃",
)
_COQUETTISH_PATTERNS = re.compile(
    r"嘛|啦|呐|诶|嗯~|好嘛|可以吗|行不行|求你了",
)

# 日语检测
_KANA_PATTERN = re.compile(r"[\u3040-\u30FF]")


# ---------------------------------------------------------------------------
# TtsContextRouter
# ---------------------------------------------------------------------------
class TtsContextRouter:
    """上下文感知 TTS 参数路由器。

    用法::

        router = TtsContextRouter()
        params = router.route(
            text="没关系的，我一直在这里陪你。",
            emotion_tag="CALM",
            bond_stage="CLOSE_FRIEND",
        )
        # params.speed_scale = 0.85, params.pitch_shift = -0.5, ...
    """

    def __init__(
        self,
        *,
        style_map: dict[str, TtsStyleParams] | None = None,
    ) -> None:
        self._styles = dict(style_map or EMOTION_STYLE_MAP)

    def route(
        self,
        *,
        text: str,
        emotion_tag: str = "",
        bond_stage: str = "",
        valence: float | None = None,
        arousal: float | None = None,
    ) -> TtsStyleParams:
        """根据文本和上下文路由到最佳 TTS 风格参数。

        优先级：显式 emotion_tag > 文本分析 > VAD 推断 > 默认
        """
        text = str(text or "").strip()
        etag = normalize_runtime_emotion_tag(emotion_tag).lower()

        # 1. 语言检测
        lang = "ja" if _KANA_PATTERN.search(text) else "zh"

        # 2. 优先用 emotion_tag
        if etag and etag in self._styles:
            params = self._styles[etag]
            result = TtsStyleParams(
                speed_scale=params.speed_scale,
                pitch_shift=params.pitch_shift,
                energy_scale=params.energy_scale,
                emotion_tag=params.emotion_tag,
                style_note=params.style_note,
                lang_hint=lang,
            )
            return self._apply_bond_adjustment(result, bond_stage)

        # 3. 文本分析
        detected = self._detect_emotion_from_text(text)
        if detected and detected in self._styles:
            params = self._styles[detected]
            result = TtsStyleParams(
                speed_scale=params.speed_scale,
                pitch_shift=params.pitch_shift,
                energy_scale=params.energy_scale,
                emotion_tag=params.emotion_tag,
                style_note=params.style_note,
                lang_hint=lang,
            )
            return self._apply_bond_adjustment(result, bond_stage)

        # 4. VAD 推断
        if valence is not None and arousal is not None:
            vad_style = self._infer_from_vad(valence, arousal)
            if vad_style and vad_style in self._styles:
                params = self._styles[vad_style]
                result = TtsStyleParams(
                    speed_scale=params.speed_scale,
                    pitch_shift=params.pitch_shift,
                    energy_scale=params.energy_scale,
                    emotion_tag=params.emotion_tag,
                    style_note=params.style_note,
                    lang_hint=lang,
                )
                return self._apply_bond_adjustment(result, bond_stage)

        # 5. 默认
        return TtsStyleParams(lang_hint=lang)

    @staticmethod
    def _detect_emotion_from_text(text: str) -> str | None:
        """从文本关键词检测情感（返回 canonical VTS tag 小写）。"""
        if _COMFORT_PATTERNS.search(text):
            return "calm"
        if _COQUETTISH_PATTERNS.search(text):
            return "loving"
        if _PLAYFUL_PATTERNS.search(text):
            return "happy"
        if _HAPPY_PATTERNS.search(text):
            return "happy"
        if _SAD_PATTERNS.search(text):
            return "sad"
        if _SERIOUS_PATTERNS.search(text):
            return "thinking"
        return None

    @staticmethod
    def _infer_from_vad(valence: float, arousal: float) -> str | None:
        """从 VAD 值推断情感（返回 canonical VTS tag 小写）。"""
        v, a = float(valence), float(arousal)
        if v > 0.6 and a > 0.6:
            return "excited"
        if v > 0.4 and a > 0.3:
            return "happy"
        if v > 0.3 and a < 0.3:
            return "calm"
        if v < 0.3 and a < 0.4:
            return "sad"
        if v < 0.4 and a > 0.5:
            return "frustrated"
        return None

    @staticmethod
    def _apply_bond_adjustment(
        params: TtsStyleParams,
        bond_stage: str,
    ) -> TtsStyleParams:
        """根据 Bond 阶段微调 TTS 参数。

        亲密度越高→语气越柔软（pitch 略升，speed 略慢）。
        """
        stage = str(bond_stage or "").strip().upper()
        _intimacy_bonus = {
            "FRIEND": 0.1,
            "CLOSE_FRIEND": 0.15,
            "CONFIDANT": 0.2,
            "SOULMATE": 0.25,
        }
        bonus = _intimacy_bonus.get(stage, 0.0)
        if bonus > 0:
            return TtsStyleParams(
                speed_scale=max(0.7, params.speed_scale - bonus * 0.1),
                pitch_shift=params.pitch_shift + bonus * 0.5,
                energy_scale=params.energy_scale,
                emotion_tag=params.emotion_tag,
                style_note=params.style_note,
                lang_hint=params.lang_hint,
            )
        return params
