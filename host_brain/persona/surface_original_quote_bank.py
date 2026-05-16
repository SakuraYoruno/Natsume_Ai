from __future__ import annotations

import difflib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SURFACE_ORIGINAL_QUOTE_BANK_PATH = ROOT / "data" / "style_bank" / "surface_original_quote_bank.jsonl"

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_COPY_NORMALIZE_RE = re.compile(r"[\s，。！？!?、,.；;：:\"'“”‘’「」『』（）()\[\]{}~～…]+")


@dataclass(frozen=True)
class SurfaceOriginalQuoteQuery:
    intent_tags: tuple[str, ...] = field(default_factory=tuple)
    emotion_tags: tuple[str, ...] = field(default_factory=tuple)
    style_moves: tuple[str, ...] = field(default_factory=tuple)
    relationship_stage: str = ""
    scene_id: str = ""
    limit: int = 6


@dataclass(frozen=True)
class SurfaceOriginalQuotePolicy:
    scene_id: str
    mode: str = "careful"
    max_quotes: int = 4
    note: str = ""

    @property
    def enabled(self) -> bool:
        return self.mode not in {"disabled", "need_samples"} and self.max_quotes > 0


@dataclass(frozen=True)
class SurfaceOriginalQuote:
    quote_id: str
    text: str
    source_ref: str
    source_digest: str
    prompt_text: str = ""
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
            prompt_text=to_prompt_style_text(str(payload.get("prompt_text") or payload.get("text") or "")),
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
    query: SurfaceOriginalQuoteQuery = field(default_factory=SurfaceOriginalQuoteQuery)
    degraded: bool = False
    reason: str = ""

    def source_refs(self) -> list[str]:
        return [quote.source_ref for quote in self.quotes]


class SurfaceOriginalQuoteBank:
    def __init__(self, quotes: Iterable[SurfaceOriginalQuote] = ()) -> None:
        self.quotes: tuple[SurfaceOriginalQuote, ...] = tuple(
            quote for quote in quotes if quote.quote_id and quote.text and _CJK_RE.search(quote.text)
        )

    def select(self, query: SurfaceOriginalQuoteQuery, *, limit: int | None = None) -> SurfaceOriginalQuoteSelection:
        if not self.quotes:
            return SurfaceOriginalQuoteSelection(query=query, degraded=True, reason="NO_SURFACE_ORIGINAL_QUOTES")
        intent_set = set(query.intent_tags)
        emotion_set = set(query.emotion_tags)
        move_set = set(query.style_moves)
        stage = str(query.relationship_stage or "").strip()

        def tag_relevance(quote: SurfaceOriginalQuote) -> int:
            return (
                len(intent_set.intersection(quote.intent_tags))
                + len(move_set.intersection(quote.style_moves))
                + len(emotion_set.intersection(quote.emotion_tags))
            )

        def relevance(quote: SurfaceOriginalQuote) -> int:
            return tag_relevance(quote) + (1 if stage and stage in quote.relationship_stages else 0)

        candidates = tuple(
            quote
            for quote in self.quotes
            if quote.prompt_text and _CJK_RE.search(quote.prompt_text)
            and _stage_prompt_compatible(quote.prompt_text, stage=stage)
        )
        if intent_set or emotion_set or move_set:
            relevant = tuple(quote for quote in candidates if tag_relevance(quote) > 0)
            if relevant:
                candidates = relevant
        elif stage:
            relevant = tuple(quote for quote in candidates if stage in quote.relationship_stages)
            if relevant:
                candidates = relevant

        def score(quote: SurfaceOriginalQuote) -> float:
            value = float(quote.confidence or 0.0) + float(quote.source_score or 0.0) * 0.08
            value += len(intent_set.intersection(quote.intent_tags)) * 1.15
            value += len(move_set.intersection(quote.style_moves)) * 0.62
            value += len(emotion_set.intersection(quote.emotion_tags)) * 0.48
            if stage and stage in quote.relationship_stages:
                value += 0.42
            if intent_set and not intent_set.intersection(quote.intent_tags):
                value -= 1.05
            if move_set and not move_set.intersection(quote.style_moves):
                value -= 0.5
            value -= abs(len(quote.prompt_text or quote.text) - 18) * 0.006
            return value

        selected_limit = max(1, min(int(limit or query.limit or 6), 8))
        ranked = sorted(
            candidates,
            key=lambda quote: (score(quote), tag_relevance(quote), relevance(quote), quote.confidence, quote.quote_id),
            reverse=True,
        )
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


def select_surface_original_quotes(query: SurfaceOriginalQuoteQuery, *, limit: int = 6) -> SurfaceOriginalQuoteSelection:
    if not surface_original_quotes_enabled():
        return SurfaceOriginalQuoteSelection(query=query, degraded=True, reason="SURFACE_ORIGINAL_QUOTES_DISABLED")
    policy = surface_original_quote_policy_for_scene(query.scene_id)
    if not policy.enabled:
        return SurfaceOriginalQuoteSelection(query=query, degraded=True, reason=f"SURFACE_ORIGINAL_QUOTES_{policy.mode.upper()}")
    effective_limit = max(1, min(int(limit or query.limit or 6), int(query.limit or 6), int(policy.max_quotes or 1)))
    return get_surface_original_quote_bank().select(query, limit=effective_limit)


def render_surface_original_quote_guidance(
    query: SurfaceOriginalQuoteQuery,
    *,
    limit: int = 6,
    policy: SurfaceOriginalQuotePolicy | None = None,
) -> str:
    active_policy = policy or surface_original_quote_policy_for_scene(query.scene_id)
    if not active_policy.enabled:
        return ""
    selection = select_surface_original_quotes(query, limit=min(limit, active_policy.max_quotes))
    if not selection.quotes:
        return ""
    if active_policy.mode == "strong":
        policy_lines = (
            "以下直接来自真实中文台词；这是强 few-shot，不是弱参考。",
            "优先学习甚至贴近使用原句的反应方式、称呼、节奏、转折、句尾、撒娇/顶嘴幅度和亲近感。",
            "当前目标是二次元 galgame 私聊数字生命，不是普通真人客服聊天；允许更鲜明、更台词化、更有角色反应。",
        )
    else:
        policy_lines = (
            "以下直接来自真实中文台词；本场景谨慎使用，优先学习反应动作、节奏、转折和句尾。",
            "允许有二次元 galgame 台词感，但不要把原句里的剧情、地点、现实动作或过强关系直接带进来。",
            "先完成当前任务和事实边界，再给角色反应。",
        )
    lines = [
        "## 真实中文原句风格参考",
        f"quote_policy: {active_policy.mode}",
        *policy_lines,
        "不要搬运原作地点、剧情、线下动作或未提供事实；这些不是本轮事实，也不是候选内容。",
        "生成内容仍然只能来自当前用户输入、ReplyControlPacket、必要事实和 must_not_do。",
    ]
    if active_policy.note:
        lines.append(f"policy_note: {active_policy.note}")
    for quote in selection.quotes:
        labels = "/".join([*quote.intent_tags[:1], *quote.style_moves[:2]]) or "daily/quick_reaction"
        lines.append(f"- {labels}: 「{quote.prompt_text or quote.text}」")
    return "\n".join(lines)


_STRONG_QUOTE_SCENES = frozenset(
    {
        "short_continue",
        "negative_feedback",
        "return_chat",
        "lover_presence",
        "ambiguous_affection",
        "pushed_away_boundary",
        "jealousy_low_pressure",
        "low_intensity_sibling_distance",
        "back",
        "miss_you",
        "presence",
        "short_ack",
        "annoying_tease",
        "style_repair",
    }
)
_CAREFUL_QUOTE_SCENES = frozenset(
    {
        "technical_next_step",
        "technical_help",
        "next_step",
        "help_takeover",
        "write_takeover",
        "tired",
        "lover_low_energy",
        "close_friend_care",
        "relationship_memory_reassurance",
        "explicit_memory",
        "recall_memory",
        "memory_followup",
        "headache",
        "bad_mood",
        "sleepy",
        "small_failure",
        "advice_continue",
    }
)
_DISABLED_QUOTE_SCENES = frozenset(
    {
        "daily_morning",
        "daily_good_morning",
        "good_morning",
    }
)
_SCENE_POLICY_ALIASES = {
    "daily_back": "return_chat",
    "reply_back": "return_chat",
    "welcome_back": "return_chat",
    "relationship_boundary": "pushed_away_boundary",
    "boundary_hurt": "pushed_away_boundary",
    "respect_boundary": "pushed_away_boundary",
    "recall": "explicit_memory",
}


def surface_original_quote_policy_for_scene(scene_id: str) -> SurfaceOriginalQuotePolicy:
    scene = _clean_policy_scene(scene_id)
    if scene in _DISABLED_QUOTE_SCENES:
        return SurfaceOriginalQuotePolicy(
            scene_id=scene,
            mode="need_samples",
            max_quotes=0,
            note="该场景暂缺可靠原台词样例，先禁用泛化 quote，避免把日常台词错喂进早安。",
        )
    if scene in _STRONG_QUOTE_SCENES:
        return SurfaceOriginalQuotePolicy(
            scene_id=scene,
            mode="strong",
            max_quotes=8,
            note="台词感优先；原句可作为强句法和反应方式样本。",
        )
    if scene in _CAREFUL_QUOTE_SCENES:
        return SurfaceOriginalQuotePolicy(
            scene_id=scene,
            mode="careful",
            max_quotes=4,
            note="只取反应节奏和角色动作，事实、任务和照看边界优先。",
        )
    return SurfaceOriginalQuotePolicy(
        scene_id=scene or "default",
        mode="careful",
        max_quotes=4,
        note="默认谨慎使用原台词，防止跨场景剧情事实外溢。",
    )


def _clean_policy_scene(scene_id: str) -> str:
    scene = str(scene_id or "").strip()
    return _SCENE_POLICY_ALIASES.get(scene, scene)


def to_prompt_style_text(raw: str) -> str:
    text = str(raw or "").strip()
    text = re.sub(r"\s+", "", text)
    while len(text) >= 2 and text[0] in "「『“\"" and text[-1] in "」』”\"":
        text = text[1:-1].strip()
    return text


def _stage_prompt_compatible(text: str, *, stage: str = "") -> bool:
    clean = str(text or "")
    if stage in {"ambiguous", "pre_confession"} and any(
        marker in clean
        for marker in ("喜欢你", "最喜欢", "喜欢好久", "求抱抱", "猛烈攻势", "超级帅", "我喜欢上你", "不喜欢我")
    ):
        return False
    return True


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
        for reference_text in (quote.text, quote.prompt_text):
            quote_norm = _copy_normalized(reference_text)
            if len(quote_norm) < 5:
                continue
            ratio = difflib.SequenceMatcher(None, normalized, quote_norm).ratio()
            contained = (
                min(len(normalized), len(quote_norm)) >= 8
                and (normalized in quote_norm or quote_norm in normalized)
            )
            if ratio >= ratio_threshold or contained:
                hits.append(quote.source_digest or quote.quote_id)
                break
    return tuple(dict.fromkeys(hits))


__all__ = [
    "DEFAULT_SURFACE_ORIGINAL_QUOTE_BANK_PATH",
    "SurfaceOriginalQuote",
    "SurfaceOriginalQuoteBank",
    "SurfaceOriginalQuotePolicy",
    "SurfaceOriginalQuoteQuery",
    "SurfaceOriginalQuoteSelection",
    "clear_surface_original_quote_bank_cache",
    "get_surface_original_quote_bank",
    "load_surface_original_quote_bank",
    "render_surface_original_quote_guidance",
    "select_surface_original_quotes",
    "surface_original_quote_policy_for_scene",
    "surface_original_quote_similarity_hits",
    "surface_original_quotes_enabled",
    "to_prompt_style_text",
]
