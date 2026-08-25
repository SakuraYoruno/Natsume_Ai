"""多模态上下文处理器（Phase XI 方案 7）。

职责：支持图片/表情包/截图的理解，以及多模态输出（文字+表情+图片）。
设计：定义标准接口，支持 Vision LLM 插拔。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

_logger = logging.getLogger("Natsume.multimodal.context")


# ---------------------------------------------------------------------------
# 模态类型
# ---------------------------------------------------------------------------
class ModalityType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    STICKER = "sticker"       # 表情包
    SCREENSHOT = "screenshot"
    AUDIO = "audio"
    EMOJI = "emoji"           # Unicode 表情


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------
@dataclass
class ModalityItem:
    """单个模态输入/输出项。"""
    modality: ModalityType
    content: str  # 文本内容/文件路径/URL/描述
    metadata: dict[str, Any] = field(default_factory=dict)
    analysis: str = ""  # Vision LLM 分析结果（输入侧）

    def as_dict(self) -> dict[str, Any]:
        return {
            "modality": self.modality.value,
            "content_preview": self.content[:100] if self.content else "",
            "has_analysis": bool(self.analysis),
            "metadata": dict(self.metadata),
        }


@dataclass
class MultimodalInput:
    """多模态输入——一条用户消息可能包含多种模态。"""
    text: str = ""
    items: list[ModalityItem] = field(default_factory=list)
    primary_modality: ModalityType = ModalityType.TEXT
    emotion_signals: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text[:100],
            "items_count": len(self.items),
            "modalities": [i.modality.value for i in self.items],
            "primary_modality": self.primary_modality.value,
            "emotion_signals": list(self.emotion_signals[:5]),
        }


@dataclass
class MultimodalOutput:
    """多模态输出——AI 回复可以包含多种模态。"""
    text: str = ""
    items: list[ModalityItem] = field(default_factory=list)
    suggested_sticker: str | None = None
    suggested_emoji: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text[:100],
            "items_count": len(self.items),
            "suggested_sticker": self.suggested_sticker,
            "suggested_emoji": self.suggested_emoji,
        }


# ---------------------------------------------------------------------------
# 表情/颜文字映射
# ---------------------------------------------------------------------------
EMOTION_EMOJI_MAP: dict[str, list[str]] = {
    "happiness": ["😊", "🥰", "✨", "🌸"],
    "sadness": ["😢", "🥺", "💧"],
    "comfort": ["🫂", "💗", "🌙"],
    "playful": ["😝", "🤭", "✌️"],
    "coquettish": ["🥺", "💕", "~"],
    "excitement": ["🎉", "✨", "💪"],
    "anger": ["😤", "💢"],
    "neutral": ["😊"],
}

EMOTION_KAOMOJI_MAP: dict[str, list[str]] = {
    "happiness": ["(◕‿◕)", "(´▽`)", "♪"],
    "sadness": ["(´;ω;`)", "(T_T)"],
    "comfort": ["(づ￣ ³￣)づ", "(っ´▽`)っ"],
    "playful": ["(≧▽≦)", "(╯°□°)╯"],
    "coquettish": ["(〃ω〃)", "(*/ω＼*)"],
}

# ---------------------------------------------------------------------------
# MultimodalProcessor
# ---------------------------------------------------------------------------
class MultimodalProcessor:
    """多模态上下文处理器。

    输入侧：解析用户消息中的多模态内容，调用 Vision LLM 分析图片。
    输出侧：根据情感上下文为 AI 回复附加表情/颜文字建议。

    用法::

        processor = MultimodalProcessor()

        # 输入处理
        mm_input = processor.process_input(
            text="看看这张图",
            attachments=[{"type": "image", "url": "..."}],
        )

        # 输出增强
        mm_output = processor.enhance_output(
            text="好可爱啊！",
            emotion_tag="happiness",
            bond_stage="CLOSE_FRIEND",
        )
        # mm_output.suggested_emoji → "😊"
    """

    def __init__(
        self,
        *,
        vision_analyzer: Callable[[str], str] | None = None,
        enable_emoji: bool = True,
        enable_kaomoji: bool = True,
    ) -> None:
        self._vision_analyzer = vision_analyzer
        self._enable_emoji = bool(enable_emoji)
        self._enable_kaomoji = bool(enable_kaomoji)

    def process_input(
        self,
        *,
        text: str = "",
        attachments: list[dict[str, Any]] | None = None,
    ) -> MultimodalInput:
        """处理多模态输入。"""
        items: list[ModalityItem] = []
        emotion_signals: list[str] = []
        text = str(text or "").strip()

        # 1. 处理文本中的 emoji
        emojis = self._extract_emojis(text)
        if emojis:
            for emoji_char in emojis:
                emotion = self._emoji_to_emotion(emoji_char)
                if emotion:
                    emotion_signals.append(emotion)
            items.append(ModalityItem(
                modality=ModalityType.EMOJI,
                content=" ".join(emojis),
                metadata={"count": len(emojis)},
            ))

        # 2. 处理附件
        for att in list(attachments or []):
            att_type = str(att.get("type") or "").strip().lower()
            att_url = str(att.get("url") or att.get("path") or "")

            if att_type in ("image", "photo", "picture"):
                item = ModalityItem(
                    modality=ModalityType.IMAGE,
                    content=att_url,
                    metadata=dict(att),
                )
                # 调用 Vision LLM 分析
                if self._vision_analyzer and att_url:
                    try:
                        analysis = self._vision_analyzer(att_url)
                        item.analysis = str(analysis or "")
                    except Exception as exc:
                        _logger.warning("Vision analysis failed: %s", exc)
                items.append(item)

            elif att_type in ("sticker", "emoticon"):
                items.append(ModalityItem(
                    modality=ModalityType.STICKER,
                    content=att_url,
                    metadata=dict(att),
                ))
                emotion_signals.append("sticker_positive")

        # 判断主模态
        primary = ModalityType.TEXT
        if any(i.modality == ModalityType.IMAGE for i in items):
            primary = ModalityType.IMAGE

        return MultimodalInput(
            text=text,
            items=items,
            primary_modality=primary,
            emotion_signals=emotion_signals,
        )

    def enhance_output(
        self,
        *,
        text: str,
        emotion_tag: str = "",
        bond_stage: str = "",
        render_lang: str = "zh",
    ) -> MultimodalOutput:
        """为 AI 回复添加多模态增强。"""
        text = str(text or "").strip()
        etag = str(emotion_tag or "neutral").strip().lower()
        stage = str(bond_stage or "").strip().upper()

        items: list[ModalityItem] = []
        emoji_suggestion: str | None = None
        sticker_suggestion: str | None = None

        # 1. Emoji 建议（亲密度≥FRIEND 才加）
        if self._enable_emoji and stage in ("FRIEND", "CLOSE_FRIEND", "CONFIDANT", "SOULMATE"):
            emojis = EMOTION_EMOJI_MAP.get(etag, [])
            if emojis:
                emoji_suggestion = emojis[0]

        # 2. 颜文字建议（日语 or 高亲密度）
        if self._enable_kaomoji:
            if render_lang == "ja" or stage in ("CLOSE_FRIEND", "CONFIDANT", "SOULMATE"):
                kaomojis = EMOTION_KAOMOJI_MAP.get(etag, [])
                if kaomojis:
                    items.append(ModalityItem(
                        modality=ModalityType.EMOJI,
                        content=kaomojis[0],
                        metadata={"type": "kaomoji", "emotion": etag},
                    ))

        return MultimodalOutput(
            text=text,
            items=items,
            suggested_sticker=sticker_suggestion,
            suggested_emoji=emoji_suggestion,
        )

    # ── 内部方法 ──────────────────────────────────────────────────────────

    @staticmethod
    def _extract_emojis(text: str) -> list[str]:
        """从文本中提取 Unicode emoji。"""
        emoji_pattern = re.compile(
            "["
            "\U0001F600-\U0001F64F"  # emoticons
            "\U0001F300-\U0001F5FF"  # symbols & pictographs
            "\U0001F680-\U0001F6FF"  # transport & map
            "\U0001F1E0-\U0001F1FF"  # flags
            "\U00002702-\U000027B0"
            "\U0001f900-\U0001f9FF"
            "\U0001FA00-\U0001FA6F"
            "\U0001FA70-\U0001FAFF"
            "]+",
            flags=re.UNICODE,
        )
        return emoji_pattern.findall(text)

    @staticmethod
    def _emoji_to_emotion(emoji: str) -> str | None:
        """将常见 emoji 映射到情绪信号。"""
        happy_emojis = {"😊", "😄", "😃", "🥰", "😍", "🤗", "💕", "❤️", "✨", "🎉"}
        sad_emojis = {"😢", "😭", "😞", "😔", "💔", "🥺"}
        angry_emojis = {"😤", "😡", "💢", "🤬"}

        if emoji in happy_emojis:
            return "happiness"
        if emoji in sad_emojis:
            return "sadness"
        if emoji in angry_emojis:
            return "anger"
        return None
