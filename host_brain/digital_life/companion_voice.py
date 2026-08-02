"""Small runtime policy for the companion's spoken language.

The visible Surface reply remains Chinese.  When Japanese companion voice is
enabled, the same model response may additionally carry one natural Japanese
speech rendering for the selected same-language voice backend.  The current
candidate is GPT-SoVITS V4 with human-selected Nanami references.  Missing or
invalid speech text degrades voice only; it never blocks the visible
conversation.
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
    """Return the configured companion speech language (``zh`` or ``ja``)."""

    source = os.environ if environ is None else environ
    raw = str(source.get(COMPANION_TTS_LANGUAGE_ENV) or "zh").strip().lower()
    normalized = raw.replace("_", "-")
    if normalized.startswith("ja") or normalized in {"jp", "japanese"}:
        return "ja"
    return "zh"


def japanese_companion_voice_enabled(
    environ: Mapping[str, Any] | None = None,
) -> bool:
    return companion_tts_language(environ) == "ja"


def normalize_japanese_speech_text(value: Any, *, limit: int = 4_000) -> str:
    """Return a bounded Japanese utterance or an empty degradation result."""

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
