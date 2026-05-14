from __future__ import annotations

import difflib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from host_brain.persona.style_reference_bank import StyleReferenceQuery


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SURFACE_ORIGINAL_QUOTE_BANK_PATH = ROOT / "data" / "style_bank" / "surface_original_quote_bank.jsonl"

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_COPY_NORMALIZE_RE = re.compile(r"[\s，。！？!?、,.；;：:\"'“”‘’「」『』（）()\[\]{}~～…]+")


@dataclass(frozen=True)
class SurfaceOriginalQuote:
    quote_id: str
    text: str
    source_ref: str
    source_digest: str
    source_score: float = 0.0
    confidence: float = 0.0
    character_confidence: float = 0.0
    intent_tags: tuple[str, ...] = field(default_factory=tuple)
    emotion_tags: tuple[str, ...] = field(default_factory=tuple)
    style_moves: tuple[str, ...] = field(default_factory=tuple)
    rhythm_tags: tuple[str, ...] = field(default_factory=tuple)
    lexical_cues: tuple[str, ...] = field(default_factory=tuple)
    relationship_stages: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SurfaceOriginalQuote":
        return cls(
            quote_id=str(payload.get("quote_id") or ""),
            text=str(payload.get("text") or "").strip(),
            source_ref=str(payload.get("source_ref") or ""),
            source_digest=str(payload.get("source_digest") or ""),
            source_score=float(payload.get("source_score") or 0.0),
            confidence=float(payload.get("confidence") or 0.0),
            character_confidence=float(payload.get("character_confidence") or 0.0),
            intent_tags=tuple(str(item) for item in payload.get("intent_tags") or [] if str(item).strip()),
            emotion_tags=tuple(str(item) for item in payload.get("emotion_tags") or [] if str(item).strip()),
            style_moves=tuple(str(item) for item in payload.get("style_moves") or [] if str(item).strip()),
            rhythm_tags=tuple(str(item) for item in payload.get("rhythm_tags") or [] if str(item).strip()),
            lexical_cues=tuple(str(item) for item in payload.get("lexical_cues") or [] if str(item).strip()),
            relationship_stages=tuple(str(item) for item in payload.get("relationship_stages") or [] if str(item).strip()),
        )


@dataclass(frozen=True)
class SurfaceOriginalQuoteSelection:
    quotes: tuple[SurfaceOriginalQuote, ...] = field(default_factory=tuple)
    query: StyleReferenceQuery = field(default_factory=StyleReferenceQuery)
    degraded: bool = False
    reason: str = ""

    def source_refs(self) -> list[str]:
        return [quote.source_ref for quote in self.quotes]


class SurfaceOriginalQuoteBank:
    def __init__(self, quotes: Iterable[SurfaceOriginalQuote] = ()) -> None:
        self.quotes: tuple[SurfaceOriginalQuote, ...] = tuple(
            quote for quote in quotes if quote.quote_id and quote.text and _CJK_RE.search(quote.text)
        )

    def select(self, query: StyleReferenceQuery, *, limit: int | None = None) -> SurfaceOriginalQuoteSelection:
        if not self.quotes:
            return SurfaceOriginalQuoteSelection(query=query, degraded=True, reason="NO_SURFACE_ORIGINAL_QUOTES")
        intent_set = set(query.intent_tags)
        emotion_set = set(query.emotion_tags)
        move_set = set(query.style_moves)
        stage = str(query.relationship_stage or "").strip()

        def score(quote: SurfaceOriginalQuote) -> float:
            value = float(quote.confidence or 0.0) + float(quote.source_score or 0.0) * 0.08
            value += len(intent_set.intersection(quote.intent_tags)) * 0.45
            value += len(move_set.intersection(quote.style_moves)) * 0.32
            value += len(emotion_set.intersection(quote.emotion_tags)) * 0.24
            if stage and stage in quote.relationship_stages:
                value += 0.35
            if intent_set and not intent_set.intersection(quote.intent_tags):
                value -= 0.28
            if move_set and not move_set.intersection(quote.style_moves):
                value -= 0.18
            value -= abs(len(quote.text) - 18) * 0.006
            return value

        selected_limit = max(1, min(int(limit or query.limit or 6), 8))
        ranked = sorted(self.quotes, key=lambda quote: (score(quote), quote.confidence, quote.quote_id), reverse=True)
        selected = tuple(ranked[:selected_limit])
        return SurfaceOriginalQuoteSelection(quotes=selected, query=query)


def load_surface_original_quote_bank(path: str | os.PathLike[str] | None = None) -> SurfaceOriginalQuoteBank:
    bank_path = Path(path or os.environ.get("HOST_SURFACE_ORIGINAL_QUOTE_BANK_PATH") or DEFAULT_SURFACE_ORIGINAL_QUOTE_BANK_PATH)
    if not bank_path.exists():
        return SurfaceOriginalQuoteBank()
    quotes: list[SurfaceOriginalQuote] = []
    with bank_path.open("r", encoding="utf-8-sig") as file:
        for raw_line in file:
            line = raw_line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if isinstance(payload, Mapping):
                quotes.append(SurfaceOriginalQuote.from_dict(payload))
    return SurfaceOriginalQuoteBank(quotes)


_BANK_CACHE: dict[str, SurfaceOriginalQuoteBank] = {}


def get_surface_original_quote_bank(path: str | os.PathLike[str] | None = None) -> SurfaceOriginalQuoteBank:
    bank_path = Path(path or os.environ.get("HOST_SURFACE_ORIGINAL_QUOTE_BANK_PATH") or DEFAULT_SURFACE_ORIGINAL_QUOTE_BANK_PATH)
    key = str(bank_path.resolve())
    cached = _BANK_CACHE.get(key)
    if cached is None:
        cached = load_surface_original_quote_bank(bank_path)
        _BANK_CACHE[key] = cached
    return cached


def clear_surface_original_quote_bank_cache() -> None:
    _BANK_CACHE.clear()


def surface_original_quotes_enabled() -> bool:
    raw = str(os.environ.get("HOST_SURFACE_ORIGINAL_QUOTES_ENABLED", "1")).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def select_surface_original_quotes(query: StyleReferenceQuery, *, limit: int = 6) -> SurfaceOriginalQuoteSelection:
    if not surface_original_quotes_enabled():
        return SurfaceOriginalQuoteSelection(query=query, degraded=True, reason="SURFACE_ORIGINAL_QUOTES_DISABLED")
    return get_surface_original_quote_bank().select(query, limit=limit)


def render_surface_original_quote_guidance(query: StyleReferenceQuery, *, limit: int = 6) -> str:
    selection = select_surface_original_quotes(query, limit=limit)
    if not selection.quotes:
        return ""
    lines = [
        "## 真实中文原句风格参考",
        "以下是真实中文台词原句，只学习节奏、转折、句尾和反应方式；严禁照抄、近似复述，严禁搬运原作人物、地点、剧情或关系。",
        "原句里的第一人称和称呼不是夏目蓝的人格设定；不要继承“人家、老哥、哥哥”等称呼习惯。",
        "生成内容仍然只能来自当前用户输入、ReplyControlPacket、必要事实和 must_not_do。",
    ]
    for quote in selection.quotes:
        labels = "/".join([*quote.intent_tags[:1], *quote.style_moves[:2]]) or "daily/quick_reaction"
        lines.append(f"- {labels}: 「{quote.text}」")
    return "\n".join(lines)


def _copy_normalized(text: str) -> str:
    return _COPY_NORMALIZE_RE.sub("", str(text or "")).strip()


def surface_original_quote_similarity_hits(
    text: str,
    quotes: Iterable[SurfaceOriginalQuote],
    *,
    ratio_threshold: float = 0.82,
) -> tuple[str, ...]:
    normalized = _copy_normalized(text)
    if len(normalized) < 5:
        return ()
    hits: list[str] = []
    for quote in quotes:
        quote_norm = _copy_normalized(quote.text)
        if len(quote_norm) < 5:
            continue
        ratio = difflib.SequenceMatcher(None, normalized, quote_norm).ratio()
        contained = (
            min(len(normalized), len(quote_norm)) >= 8
            and (normalized in quote_norm or quote_norm in normalized)
        )
        if ratio >= ratio_threshold or contained:
            hits.append(quote.source_digest or quote.quote_id)
    return tuple(dict.fromkeys(hits))


__all__ = [
    "DEFAULT_SURFACE_ORIGINAL_QUOTE_BANK_PATH",
    "SurfaceOriginalQuote",
    "SurfaceOriginalQuoteBank",
    "SurfaceOriginalQuoteSelection",
    "clear_surface_original_quote_bank_cache",
    "get_surface_original_quote_bank",
    "load_surface_original_quote_bank",
    "render_surface_original_quote_guidance",
    "select_surface_original_quotes",
    "surface_original_quote_similarity_hits",
    "surface_original_quotes_enabled",
]
