"""陪伴语音语言的轻量运行时策略。

Surface 可见回复始终保持中文。启用日语陪伴语音时，同一次模型回复可额外携带
一份自然日语朗读文本，供同语言的语音后端使用；当前候选为带人工选择 Nanami
参考的 GPT-SoVITS V4。朗读文本缺失或不合法时只降级语音，绝不阻断可见对话。
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any


COMPANION_TTS_LANGUAGE_ENV = "HOST_COMPANION_TTS_LANGUAGE"
_KANA_RE = re.compile(r"[\u3040-\u30ff]")


def companion_tts_language(
    environ: Mapping[str, Any] | None = None,
) -> str:
    """读取受限的语音语言配置，只规范化为 ``zh`` 或 ``ja``。

    未识别的环境值默认落回中文，避免部署错误把可见语言策略扩散到任意后端。
    """

    source = os.environ if environ is None else environ
    raw = str(source.get(COMPANION_TTS_LANGUAGE_ENV) or "zh").strip().lower()
    normalized = raw.replace("_", "-")
    if normalized.startswith("ja") or normalized in {"jp", "japanese"}:
        return "ja"
    return "zh"


def japanese_companion_voice_enabled(
    environ: Mapping[str, Any] | None = None,
) -> bool:
    """提供给呈现链的单一开关；不在此处执行任何设备侧能力探测。"""
    return companion_tts_language(environ) == "ja"


def normalize_japanese_speech_text(value: Any, *, limit: int = 4_000) -> str:
    """返回受长度限制的日语朗读文本，或空串表示可恢复的语音降级。

    代码块、NUL 字节和不含假名的内容都不会转交 TTS；空串让上游继续发送中文
    可见回复，而不是尝试用不匹配的声线朗读或让整个对话失败。
    """

    text = str(value or "").strip()
    if not text or len(text) > max(1, int(limit)):
        return ""
    if "```" in text or "\x00" in text or not _KANA_RE.search(text):
        return ""
    return text


__all__ = [
    "COMPANION_TTS_LANGUAGE_ENV",
    "companion_tts_language",
    "japanese_companion_voice_enabled",
    "normalize_japanese_speech_text",
]
