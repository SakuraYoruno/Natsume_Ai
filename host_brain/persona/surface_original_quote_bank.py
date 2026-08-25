from __future__ import annotations

import csv
import difflib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

from host_brain.core.runtime_helpers import _coerce_float


DEFAULT_SURFACE_ORIGINAL_QUOTE_COLLECTION = "surface_original_quotes"
DEFAULT_SURFACE_ORIGINAL_QUOTE_VECTOR_DIM = 1024
DEFAULT_RIDDLE_JOKER_DIALOGUE_COLLECTION = "riddle_joker_dialogue_zh"
DEFAULT_RIDDLE_JOKER_DIALOGUE_TSV = r"D:\HF_Datasets\Limour\riddle_joker_dialogue\RIDDLE_JOKER_zh_dialogue.tsv"
DEFAULT_RIDDLE_JOKER_DIALOGUE_MIN_SCORE = 0.42

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_KANA_RE = re.compile(r"[\u3040-\u30ff]")
_ASCII_WORD_RE = re.compile(r"[A-Za-z]{2,}")
_COPY_NORMALIZE_RE = re.compile(r"[\s，。！？!?、,.；;：:\"'“”‘’「」『』（）()\[\]{}~～…]+")
_SKIP_RISK_TIERS = frozenset({"explicit_unsafe", "unsafe_plot_line", "rhythm_fragment"})
_TEMPORAL_CORRECTION_QUOTE_SKIP_RE = re.compile(
    r"(?:现在|当前|都|几点|什么时候|下午|晚上|夜里|半夜|午夜|零点|不是早上|还早上|怎么还|怎么又).{0,12}"
    r"(?:早上好|早安|早啊|早呀|上午好|早晨好)|"
    r"(?:早上好|早安|早啊|早呀|上午好|早晨好).{0,12}"
    r"(?:现在|当前|都|几点|什么时候|下午|晚上|夜里|半夜|午夜|零点|不是早上|还早上|怎么还|怎么又)"
)

_RUNTIME_PLOT_MARKERS = (
    "新海君",
    "翔先生",
    "莲夜",
    "Artifact",
    "喷枪",
    "模型套装",
    "精神病院",
    "金拱门",
    "意大利面",
    "清洁公司",
    "沐浴露",
    "使用者",
    "战斗",
    "伙伴",
    "一个房间",
    "睡一个房间",
    "被大家遗忘",
    "别人忘了我",
    "石化",
    "连累哥哥",
    "世界消失",
    "要死了",
    "春风",
    "学姐",
    "放学",
    "小天",
    "都学姐",
    "最痛苦的是天",
    "断绝关系",
    "被讨厌",
    "拒绝了",
    "那个人",
    "搭话",
    "哥哥他呀",
    "都会帮你解决",
    "勇气可嘉",
    "感动得都要哭",
    "帮助哥哥",
    "回报哥哥",
    "哥哥的依靠",
    "迁就我的任性",
    "材料费",
    "帮你洗",
    "一起洗",
    "身上这么多东西",
    "结巴个头",
    "好消息",
    "去去就回",
    "在这等我",
    "完～全都不回来",
    "连累了哥哥",
    "肆无忌惮",
    "引人注目",
    "看她",
    "爸妈",
    "哥哥大人",
    "人家还不困",
    "不还回来也没关系",
    "每周回来一次",
    "周末回家",
    "回家住一晚",
    "以前很喜欢",
    "模仿一下",
    "万人迷美少女",
    "忘记了我",
    "忘记掉",
    "没有忘记我",
    "告白",
    "消失",
    "脆弱",
    "隐藏了起来",
    "松了一口气",
    "非常害怕",
    "任何人发现",
    "这很恶心",
    "请我一顿",
    "地方史志",
    "零花钱",
    "自己生活",
    "自己扎",
    "风尘",
    "拎清",
    "赚的钱",
    "家里给",
    "大哥哥",
    "大哥",
    "天的",
    "这座城市",
    "学校",
    "教室",
    "老师",
    "社团",
    "班长",
    "学生",
)
_RUNTIME_UNSAFE_MARKERS = (
    "裸体",
    "内裤",
    "鸡巴",
    "中出",
    "做爱",
    "亲热",
    "射出来",
    "接吻",
    "想干",
    "求你了",
    "满足我",
    "爱上哥哥",
    "求抱抱",
    "碧池",
    "哈啊",
    "舒服吗",
    "我的里面",
)
_RUNTIME_OVERBOUND_RELATION_MARKERS = (
    "喜欢好久",
    "好久好久",
    "猛烈攻势",
    "一心一意",
    "不管发生什么",
    "只属于",
    "永远",
)
_SCENE_LEXICAL_HINTS: dict[str, tuple[str, ...]] = {
    "short_continue": ("继续", "接下来", "怎么办", "那就", "行吧", "好吧", "等一下", "重来", "再给"),
    "negative_feedback": ("哈？", "谁稀罕", "才不是", "什么嘛", "好烦", "烦死", "笨蛋", "重来"),
    "temporal_mismatch_greeting": ("早上好", "早啊", "早安", "下午", "现在", "不是", "已经", "时间", "重来", "随便", "稀罕"),
    "pushed_away_boundary": ("谁稀罕", "管你", "别管", "听见没", "随便你"),
    "return_chat": ("回来", "欢迎回来", "我回来了", "看见"),
    "lover_presence": ("想你", "想见", "喜欢", "抱"),
    "ambiguous_affection": ("喜欢", "可爱", "想你", "害羞", "抱"),
    "technical_next_step": ("帮", "怎么办", "重来", "资料", "调查", "先", "别叹气"),
    "tired": ("累", "困", "辛苦", "休息", "缓", "硬撑", "没事吧"),
    "close_friend_care": ("帮", "照顾", "管你", "放心", "没事", "辛苦"),
    "explicit_memory": ("记得", "忘", "想起来", "知道", "当然"),
    "relationship_memory_reassurance": ("记得", "忘", "不记得", "别瞎想", "知道"),
}
_CURATED_SCENE_QUOTES: dict[str, tuple[tuple[str, str], ...]] = {
    # These are fixed 七海 lines from the local RIDDLE JOKER corpus.  The
    # repair set carries three different pieces of conversational posture:
    # a concrete interruption apology, plain ownership, and a non-punitive
    # first-person hurt reaction.  The mixed set preserves both sides in one
    # breath instead of teaching an emotion-management template.
    "relationship_repair": (
        ("riddle_joker_tsv::741", "啊啊,抱、抱歉。打断你说话了"),
        ("riddle_joker_tsv::1588", "而且,我也就由着性子来了我也有责任"),
        ("riddle_joker_tsv::8895", "...真不想被阿晓那样说呐。明明以前每天都是我叫你起床"),
    ),
    "mixed_affect": (
        ("riddle_joker_tsv::21108", "你这么夸我我是很高兴...不过让我更加担心了"),
        ("riddle_joker_tsv::21447", "(明明知道这是好事...心里还是觉得好不舒服...好不舒服啊啊~)"),
    ),
}
_SCENE_REQUIRES_LEXICAL_HIT = frozenset(
    {
        "short_continue",
        "negative_feedback",
        "temporal_mismatch_greeting",
        "pushed_away_boundary",
        "return_chat",
        "technical_next_step",
        "tired",
        "explicit_memory",
        "relationship_memory_reassurance",
        "lover_presence",
        "ambiguous_affection",
        "lover_low_energy",
        "close_friend_care",
        "jealousy_low_pressure",
    }
)
_STRONG_SCENE_DEFAULT_LIMITS = {
    "short_continue": 3,
    "negative_feedback": 4,
    "temporal_mismatch_greeting": 6,
    "pushed_away_boundary": 2,
    "lover_presence": 3,
    "ambiguous_affection": 1,
    "relationship_repair": 3,
    "mixed_affect": 2,
}
_SCENE_BLOCKED_MARKERS: dict[str, tuple[str, ...]] = {
    "temporal_mismatch_greeting": (
        "我现在还在上课",
        "上课呢",
        "有课的",
        "晚安",
        "回房去休息",
        "睡觉啦",
        "深夜",
        "午休",
        "早饭了",
        "睡回笼觉",
        "睡糊",
        "睡迷糊",
        "没睡醒",
        "刚醒",
        "刚睡醒",
        "迷糊",
        "糊涂",
        "脑子",
        "屏幕",
        "盯",
        "看你",
        "装傻",
        "逗我",
        "故意",
    ),
    "return_chat": ("每周回来", "周末回家", "回家住"),
    "lover_presence": (
        "一心一意",
        "别人怎么想",
        "不管发生什么",
        "现在这样的话",
        "喜欢上哥哥",
        "对不起",
    ),
    "ambiguous_affection": (
        "最喜欢",
        "太喜欢",
        "喜欢好久",
        "求抱抱",
        "超级帅",
        "对不起",
        "一心一意",
        "以前很喜欢",
        "模仿一下",
        "万人迷美少女",
    ),
}


@dataclass(frozen=True)
class SurfaceOriginalQuoteQuery:
    intent_tags: tuple[str, ...] = field(default_factory=tuple)
    emotion_tags: tuple[str, ...] = field(default_factory=tuple)
    style_moves: tuple[str, ...] = field(default_factory=tuple)
    relationship_stage: str = ""
    scene_id: str = ""
    user_message: str = ""
    limit: int = 12
    question_budget: int | None = None


@dataclass(frozen=True)
class SurfaceOriginalQuotePolicy:
    scene_id: str
    mode: str = "careful"
    max_quotes: int = 10
    candidate_top_k: int = 64
    note: str = ""

    @property
    def enabled(self) -> bool:
        return self.mode not in {"disabled", "need_samples"} and self.max_quotes > 0


class _RuntimeQuoteRows(list[dict[str, Any]]):
    """List-compatible result carrying whether the local corpus was present."""

    def __init__(
        self,
        rows: Iterable[Mapping[str, Any]] = (),
        *,
        source_available: bool = False,
    ) -> None:
        super().__init__(dict(row) for row in rows)
        self.source_available = bool(source_available)


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
    vector_score: float = 0.0
    risk_tier: str = "safe_full_line"
    intent_tags: tuple[str, ...] = field(default_factory=tuple)
    emotion_tags: tuple[str, ...] = field(default_factory=tuple)
    style_moves: tuple[str, ...] = field(default_factory=tuple)
    rhythm_tags: tuple[str, ...] = field(default_factory=tuple)
    lexical_cues: tuple[str, ...] = field(default_factory=tuple)
    relationship_stages: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SurfaceOriginalQuote":
        return cls(
            quote_id=str(payload.get("quote_id") or payload.get("memory_id") or "").strip(),
            text=str(payload.get("text") or "").strip(),
            source_ref=str(payload.get("source_ref") or "").strip(),
            source_digest=str(payload.get("source_digest") or payload.get("quote_id") or "").strip(),
            prompt_text=to_prompt_style_text(str(payload.get("prompt_text") or payload.get("text") or "")),
            source_score=_coerce_float(payload.get("source_score"), default=0.0),
            confidence=_coerce_float(payload.get("confidence"), default=0.0),
            character_confidence=_coerce_float(payload.get("character_confidence"), default=0.0),
            vector_score=_coerce_float(payload.get("vector_score"), default=0.0),
            risk_tier=str(payload.get("risk_tier") or "safe_full_line").strip() or "safe_full_line",
            intent_tags=tuple(_metadata_list(payload.get("intent_tags"))),
            emotion_tags=tuple(_metadata_list(payload.get("emotion_tags"))),
            style_moves=tuple(_metadata_list(payload.get("style_moves"))),
            rhythm_tags=tuple(_metadata_list(payload.get("rhythm_tags"))),
            lexical_cues=tuple(_metadata_list(payload.get("lexical_cues"))),
            relationship_stages=tuple(_metadata_list(payload.get("relationship_stages"))),
        )

    @classmethod
    def from_vector_row(cls, row: Mapping[str, Any]) -> "SurfaceOriginalQuote":
        metadata = dict(row.get("metadata") or {}) if isinstance(row.get("metadata"), Mapping) else {}
        text = str(row.get("text") or metadata.get("text") or "").strip()
        quote_id = str(metadata.get("quote_id") or row.get("memory_id") or "").strip()
        source_digest = str(metadata.get("source_digest") or quote_id or "").strip()
        return cls(
            quote_id=quote_id,
            text=text,
            source_ref=str(metadata.get("source_ref") or "").strip(),
            source_digest=source_digest,
            prompt_text=to_prompt_style_text(str(metadata.get("prompt_text") or text)),
            source_score=_coerce_float(metadata.get("source_score"), default=0.0),
            confidence=_coerce_float(metadata.get("confidence"), default=0.0),
            character_confidence=_coerce_float(metadata.get("character_confidence"), default=0.0),
            vector_score=_coerce_float(row.get("score"), default=0.0),
            risk_tier=str(metadata.get("risk_tier") or "safe_full_line").strip() or "safe_full_line",
            intent_tags=tuple(_metadata_list(metadata.get("intent_tags"))),
            emotion_tags=tuple(_metadata_list(metadata.get("emotion_tags"))),
            style_moves=tuple(_metadata_list(metadata.get("style_moves"))),
            rhythm_tags=tuple(_metadata_list(metadata.get("rhythm_tags"))),
            lexical_cues=tuple(_metadata_list(metadata.get("lexical_cues"))),
            relationship_stages=tuple(_metadata_list(metadata.get("relationship_stages"))),
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
    """Small in-memory helper kept for tests/tools; runtime uses Chroma."""

    def __init__(self, quotes: Iterable[SurfaceOriginalQuote] = ()) -> None:
        self.quotes: tuple[SurfaceOriginalQuote, ...] = tuple(
            quote for quote in quotes if quote.quote_id and quote.text and _CJK_RE.search(quote.text)
        )

    def select(self, query: SurfaceOriginalQuoteQuery, *, limit: int | None = None) -> SurfaceOriginalQuoteSelection:
        ranked = _rank_quotes(self.quotes, query=query)
        return SurfaceOriginalQuoteSelection(
            quotes=tuple(ranked[: max(1, int(limit or query.limit or 12))]),
            query=query,
        )


def clear_surface_original_quote_bank_cache() -> None:
    """Clear in-process quote/vector caches for reloads and isolated tools.

    The runtime quote bank is still active; this reset only drops derived
    process-local rows and client state so the next lookup reloads them.
    """

    _VECTOR_CLIENT_CACHE.clear()
    _VECTOR_ROW_CACHE.clear()
    _RIDDLE_JOKER_ROW_CACHE.clear()
    _RIDDLE_JOKER_QUOTE_ROW_CACHE.clear()
    _QUOTE_SELECTION_CACHE.clear()


def surface_original_quotes_enabled() -> bool:
    raw = str(os.environ.get("HOST_SURFACE_ORIGINAL_QUOTES_ENABLED", "1")).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def select_surface_original_quotes(query: SurfaceOriginalQuoteQuery, *, limit: int = 12) -> SurfaceOriginalQuoteSelection:
    if not surface_original_quotes_enabled():
        return SurfaceOriginalQuoteSelection(query=query, degraded=True, reason="SURFACE_ORIGINAL_QUOTES_DISABLED")
    policy = surface_original_quote_policy_for_scene(query.scene_id)
    if not policy.enabled:
        return SurfaceOriginalQuoteSelection(query=query, degraded=True, reason=f"SURFACE_ORIGINAL_QUOTES_{policy.mode.upper()}")

    effective_limit = max(1, min(int(limit or query.limit or 12), int(query.limit or 12), int(policy.max_quotes or 1)))
    curated_rows = _CURATED_SCENE_QUOTES.get(_clean_policy_scene(query.scene_id))
    if curated_rows:
        curated_quotes = tuple(
            SurfaceOriginalQuote(
                quote_id=quote_id,
                text=quote_text,
                source_ref=(
                    "RIDDLE_JOKER_zh_dialogue.tsv:"
                    f"{quote_id.rsplit('::', 1)[-1]}:七海"
                ),
                source_digest=quote_id,
                prompt_text=quote_text,
                source_score=1.0,
                confidence=1.0,
                character_confidence=1.0,
                intent_tags=("daily",),
                style_moves=("quick_reaction",),
                relationship_stages=(
                    "familiar",
                    "trusted_friend",
                    "close_friend",
                    "close_but_not_romantic",
                ),
            )
            for quote_id, quote_text in curated_rows
        )
        selected = tuple(
            quote
            for quote in curated_quotes
            if not _quote_exceeds_question_budget(query, quote.prompt_text or quote.text)
            and not _unsafe_runtime_quote_text(quote.prompt_text or quote.text)
            and _stage_prompt_compatible(
                quote.prompt_text or quote.text,
                stage=query.relationship_stage,
            )
        )[:effective_limit]
        return SurfaceOriginalQuoteSelection(
            quotes=selected,
            query=query,
            degraded=not bool(selected),
            reason="" if selected else "CURATED_SCENE_QUOTES_UNAVAILABLE",
        )
    cache_ttl_s = _quote_selection_cache_ttl_s()
    cache_key = _quote_selection_cache_key(query, limit=effective_limit, policy=policy)
    if cache_ttl_s > 0:
        cached = _QUOTE_SELECTION_CACHE.get(cache_key)
        now_s = time.monotonic()
        if cached and (now_s - cached[0]) <= cache_ttl_s:
            return cached[1]
    candidate_top_k = max(policy.candidate_top_k, effective_limit * 4)
    rows: list[dict[str, Any]] = []
    vector_error = ""
    local_lexical_source_available = False
    if _riddle_joker_lexical_first_enabled():
        lexical_rows = _query_riddle_joker_dialogue_tsv(
            query,
            top_k=candidate_top_k,
        )
        rows = list(lexical_rows)
        local_lexical_source_available = bool(
            getattr(lexical_rows, "source_available", False)
        )
    allow_vector_after_lexical_miss = bool(
        not local_lexical_source_available
        or _riddle_joker_vector_fallback_after_lexical_miss_enabled()
    )
    try:
        if not rows and allow_vector_after_lexical_miss:
            rows = _query_riddle_joker_dialogue_vector_collection(query, top_k=candidate_top_k)
    except Exception as exc:
        rows = []
        vector_error = f"RIDDLE_JOKER_DIALOGUE_VECTOR_UNAVAILABLE:{type(exc).__name__}"
    else:
        vector_error = ""
    try:
        fallback_rows = (
            []
            if rows or not allow_vector_after_lexical_miss
            else _query_surface_original_quote_collection(
                query,
                top_k=candidate_top_k,
            )
        )
    except Exception as exc:
        fallback_rows = []
        if not vector_error:
            vector_error = f"SURFACE_ORIGINAL_QUOTES_VECTOR_UNAVAILABLE:{type(exc).__name__}"
    else:
        if not vector_error:
            vector_error = ""
    if fallback_rows:
        rows = _dedupe_vector_rows([*rows, *fallback_rows])
    if not rows and _riddle_joker_lexical_fallback_enabled():
        local_rows = _query_riddle_joker_dialogue_tsv(query, top_k=candidate_top_k)
        if local_rows:
            rows = _dedupe_vector_rows(local_rows)
    if not rows and vector_error:
        return SurfaceOriginalQuoteSelection(query=query, degraded=True, reason=vector_error)
    quotes = [SurfaceOriginalQuote.from_vector_row(row) for row in rows]
    ranked = _rank_quotes(quotes, query=query)
    selected = tuple(ranked[:effective_limit])
    selection = SurfaceOriginalQuoteSelection(
        quotes=selected,
        query=query,
        degraded=not bool(selected),
        reason="" if selected else "SURFACE_ORIGINAL_QUOTES_EMPTY_VECTOR_RESULT",
    )
    if cache_ttl_s > 0:
        _QUOTE_SELECTION_CACHE[cache_key] = (time.monotonic(), selection)
    return selection


def _surface_quote_guidance_skip_for_temporal_correction(query: SurfaceOriginalQuoteQuery) -> bool:
    value = re.sub(r"\s+", "", str(getattr(query, "user_message", "") or ""))
    if not value:
        return False
    return bool(_TEMPORAL_CORRECTION_QUOTE_SKIP_RE.search(value))


def render_surface_original_quote_guidance(
    query: SurfaceOriginalQuoteQuery,
    *,
    limit: int = 12,
    policy: SurfaceOriginalQuotePolicy | None = None,
) -> str:
    if _surface_quote_guidance_skip_for_temporal_correction(query):
        return ""
    active_policy = policy or surface_original_quote_policy_for_scene(query.scene_id)
    if not active_policy.enabled:
        return ""
    selection = select_surface_original_quotes(query, limit=min(limit, active_policy.max_quotes))
    if not selection.quotes:
        return ""
    if active_policy.mode == "strong":
        policy_lines = (
            "以下来自独立 Chroma 原作中文台词 collection；这是强 few-shot，不是弱参考。",
            "优先学习甚至贴近使用原句的反应方式、称呼、节奏、转折、句尾、撒娇/顶嘴幅度和亲近感。",
            "当前目标是二次元 galgame 私聊数字生命，不是普通真人客服聊天；允许更鲜明、更台词化、更有角色反应。",
        )
    else:
        policy_lines = (
            "以下来自独立 Chroma 原作中文台词 collection；本场景谨慎使用，优先学习反应动作、节奏、转折和句尾。",
            "允许有二次元 galgame 台词感，但不要把原句里的剧情、地点、现实动作或过强关系直接带进来。",
            "先完成当前任务和事实边界，再给角色反应。",
        )
    lines = [
        "## 真实中文原句风格参考",
        f"quote_policy: {active_policy.mode}",
        *policy_lines,
        "这些台词是风格样本，不是当前事实；不要搬运原作地点、剧情、线下动作或未提供事实。",
        "生成内容仍然只能来自当前用户输入、ReplyControlPacket、必要事实和 must_not_do。",
    ]
    if active_policy.note:
        lines.append(f"policy_note: {active_policy.note}")
    for quote in selection.quotes:
        labels = "/".join([*quote.intent_tags[:1], *quote.style_moves[:2]]) or "daily/quick_reaction"
        if quote.risk_tier == "rhythm_fragment":
            labels = f"{labels}/rhythm"
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
        "temporal_mismatch_greeting",
        "relationship_repair",
        "mixed_affect",
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
            candidate_top_k=0,
            note="该场景暂缺可靠早安台词样例，先禁用泛化 quote，避免错喂普通日常台词。",
        )
    if scene in _STRONG_QUOTE_SCENES:
        return SurfaceOriginalQuotePolicy(
            scene_id=scene,
            mode="strong",
            max_quotes=_quote_limit_env(
                "HOST_SURFACE_ORIGINAL_QUOTES_STRONG_LIMIT",
                _STRONG_SCENE_DEFAULT_LIMITS.get(scene, 4),
            ),
            candidate_top_k=_quote_limit_env("HOST_SURFACE_ORIGINAL_QUOTES_STRONG_CANDIDATES", 96),
            note="台词感优先；原句可作为强句法和反应方式样本。",
        )
    if scene in _CAREFUL_QUOTE_SCENES:
        return SurfaceOriginalQuotePolicy(
            scene_id=scene,
            mode="need_samples",
            max_quotes=0,
            candidate_top_k=0,
            note="该场景事实/任务/照看边界优先；泛化原台词召回容易带入剧情或关系噪音，需人工认可样本后再启用。",
        )
    return SurfaceOriginalQuotePolicy(
        scene_id=scene or "default",
        mode="careful",
        max_quotes=_quote_limit_env("HOST_SURFACE_ORIGINAL_QUOTES_DEFAULT_LIMIT", 10),
        candidate_top_k=_quote_limit_env("HOST_SURFACE_ORIGINAL_QUOTES_DEFAULT_CANDIDATES", 64),
        note="默认谨慎使用原台词，防止跨场景剧情事实外溢。",
    )


def to_prompt_style_text(raw: str) -> str:
    text = str(raw or "").strip()
    text = re.sub(r"\s+", "", text)
    while len(text) >= 2 and text[0] in "「『“\"" and text[-1] in "」』”\"":
        text = text[1:-1].strip()
    return text


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


_VECTOR_CLIENT_CACHE: dict[tuple[str, int], Any] = {}
_VECTOR_ROW_CACHE: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
_RIDDLE_JOKER_ROW_CACHE: dict[str, list[dict[str, Any]]] = {}
_RIDDLE_JOKER_QUOTE_ROW_CACHE: dict[
    tuple[str, bool], tuple[tuple[SurfaceOriginalQuote, dict[str, Any]], ...]
] = {}
_QUOTE_SELECTION_CACHE: dict[tuple[Any, ...], tuple[float, SurfaceOriginalQuoteSelection]] = {}


def _quote_selection_cache_ttl_s() -> float:
    raw = str(os.environ.get("HOST_SURFACE_ORIGINAL_QUOTE_CACHE_TTL_S") or "").strip()
    try:
        value = float(raw)
    except Exception:
        value = 600.0
    return max(0.0, min(3600.0, value))


def _quote_selection_cache_key(query: SurfaceOriginalQuoteQuery, *, limit: int, policy: SurfaceOriginalQuotePolicy) -> tuple[Any, ...]:
    return (
        str(query.scene_id or ""),
        str(query.user_message or "")[:256],
        tuple(str(item or "") for item in query.intent_tags[:8]),
        tuple(str(item or "") for item in query.emotion_tags[:8]),
        tuple(str(item or "") for item in query.style_moves[:8]),
        str(query.relationship_stage or ""),
        query.question_budget,
        int(limit or query.limit or 0),
        str(policy.mode or ""),
        int(policy.max_quotes or 0),
        int(policy.candidate_top_k or 0),
    )


def _query_surface_original_quote_collection(
    query: SurfaceOriginalQuoteQuery,
    *,
    top_k: int,
) -> list[dict[str, Any]]:
    from host_brain.memory.chroma_client import (
        PersistentChromaClient,
        embed_text,
        load_chroma_runtime_config,
    )

    collection = _quote_collection_name()
    dim = _quote_vector_dim()
    cfg_result = load_chroma_runtime_config()
    if not cfg_result.success or cfg_result.config is None or not cfg_result.config.enabled:
        raise RuntimeError(f"chroma config invalid: {cfg_result.reason_code}")
    cfg = cfg_result.config
    cache_key = (collection, dim)
    client = _VECTOR_CLIENT_CACHE.get(cache_key)
    if client is None:
        profiles = dict(cfg.collection_profiles)
        profiles[collection] = dim
        client = PersistentChromaClient(
            endpoint=cfg.endpoint,
            tenant=cfg.tenant,
            database=cfg.database,
            collection_profiles=profiles,
            default_collection=cfg.default_collection,
        )
        _VECTOR_CLIENT_CACHE[cache_key] = client
    query_text = _vector_query_text(query)
    if _quote_embedding_mode() == "hash":
        return _query_surface_original_quote_collection_lexical_fallback(
            query,
            top_k=top_k,
            collection=collection,
            dim=dim,
            endpoint=cfg.endpoint,
            tenant=cfg.tenant,
            database=cfg.database,
        )
    else:
        embedding, model_id = embed_text(query_text, dim=dim)
        if model_id == "hash-embed-v1":
            return _query_surface_original_quote_collection_lexical_fallback(
                query,
                top_k=top_k,
                collection=collection,
                dim=dim,
                endpoint=cfg.endpoint,
                tenant=cfg.tenant,
                database=cfg.database,
            )
    rows = client.search(
        query_embedding=list(embedding or []),
        top_k=max(1, int(top_k or 1)),
        collection=collection,
    )
    if _clean_policy_scene(query.scene_id) in _SCENE_REQUIRES_LEXICAL_HIT:
        try:
            lexical_rows = _query_surface_original_quote_collection_lexical_fallback(
                query,
                top_k=max(1, int(top_k or 1)),
                collection=collection,
                dim=dim,
                endpoint=cfg.endpoint,
                tenant=cfg.tenant,
                database=cfg.database,
            )
            rows = _dedupe_vector_rows([*lexical_rows, *rows])
        except Exception:
            pass
    for row in rows:
        metadata = dict(row.get("metadata") or {}) if isinstance(row.get("metadata"), Mapping) else {}
        metadata["query_embedding_model_id"] = model_id
        row["metadata"] = metadata
    return rows


def _query_riddle_joker_dialogue_vector_collection(
    query: SurfaceOriginalQuoteQuery,
    *,
    top_k: int,
) -> list[dict[str, Any]]:
    from host_brain.memory.chroma_client import (
        PersistentChromaClient,
        embed_text,
        load_chroma_runtime_config,
    )

    collection = _riddle_joker_dialogue_collection_name()
    dim = _riddle_joker_dialogue_vector_dim()
    cfg_result = load_chroma_runtime_config()
    if not cfg_result.success or cfg_result.config is None or not cfg_result.config.enabled:
        raise RuntimeError(f"chroma config invalid: {cfg_result.reason_code}")
    cfg = cfg_result.config
    cache_key = (collection, dim)
    client = _VECTOR_CLIENT_CACHE.get(cache_key)
    if client is None:
        profiles = dict(cfg.collection_profiles)
        profiles[collection] = dim
        client = PersistentChromaClient(
            endpoint=cfg.endpoint,
            tenant=cfg.tenant,
            database=cfg.database,
            collection_profiles=profiles,
            default_collection=cfg.default_collection,
        )
        _VECTOR_CLIENT_CACHE[cache_key] = client
    query_text = _vector_query_text(query)
    embedding, model_id = embed_text(query_text, dim=dim)
    rows = client.search(
        query_embedding=list(embedding or []),
        top_k=max(1, int(top_k or 1)),
        collection=collection,
    )
    min_score = _riddle_joker_dialogue_min_score()
    filtered: list[dict[str, Any]] = []
    for row in rows:
        score = _coerce_float(row.get("score"), default=0.0)
        if score < min_score:
            continue
        metadata = dict(row.get("metadata") or {}) if isinstance(row.get("metadata"), Mapping) else {}
        metadata["query_embedding_model_id"] = model_id
        metadata["min_score"] = min_score
        metadata["score_passed"] = True
        row["metadata"] = metadata
        filtered.append(dict(row))
    return filtered


def _query_riddle_joker_dialogue_tsv(
    query: SurfaceOriginalQuoteQuery,
    *,
    top_k: int,
) -> _RuntimeQuoteRows:
    quote_rows = _fetch_riddle_joker_dialogue_tsv_quote_rows()
    if not quote_rows:
        return _RuntimeQuoteRows(source_available=False)
    scored: list[tuple[float, dict[str, Any]]] = []
    for quote, row in quote_rows:
        score = _lexical_quote_row_score(quote, query=query)
        if score <= -100.0:
            continue
        metadata = dict(row.get("metadata") or {}) if isinstance(row.get("metadata"), Mapping) else {}
        metadata["query_embedding_model_id"] = "riddle-joker-tsv-lexical"
        scored.append((score, {**row, "score": round(score / 100.0, 6), "metadata": metadata}))
    scored.sort(
        key=lambda item: (
            item[0],
            float((item[1].get("metadata") or {}).get("character_confidence") or 0.0),
            float((item[1].get("metadata") or {}).get("confidence") or 0.0),
            str(item[1].get("memory_id") or ""),
        ),
        reverse=True,
    )
    return _RuntimeQuoteRows(
        [row for _, row in scored[: max(1, int(top_k or 1))]],
        source_available=True,
    )


def _fetch_riddle_joker_dialogue_tsv_quote_rows(
) -> tuple[tuple[SurfaceOriginalQuote, dict[str, Any]], ...]:
    """Cache runtime-ready rows and prefer 七海's own dialogue voice.

    The source contains tens of thousands of lines from the whole cast.  The
    old hot path rebuilt every row into a quote object on every turn even
    though this runtime is specifically expressing 七海.  Keeping the parsed
    objects in memory preserves lexical ranking while reducing each new scene
    query to the relevant speaker subset.
    """

    path = str(
        Path(
            str(os.environ.get("HOST_RIDDLE_JOKER_DIALOGUE_TSV") or "").strip()
            or DEFAULT_RIDDLE_JOKER_DIALOGUE_TSV
        )
    )
    primary_only = _riddle_joker_primary_character_only_enabled()
    cache_key = (path, primary_only)
    cached = _RIDDLE_JOKER_QUOTE_ROW_CACHE.get(cache_key)
    if cached is not None:
        return cached

    rows = list(_fetch_riddle_joker_dialogue_tsv_rows())
    if primary_only:
        primary_rows = [
            row
            for row in rows
            if _coerce_float(
                (row.get("metadata") or {}).get("character_confidence")
                if isinstance(row.get("metadata"), Mapping)
                else 0.0,
                default=0.0,
            )
            >= 0.99
        ]
        if primary_rows:
            rows = primary_rows
    prepared = tuple(
        (SurfaceOriginalQuote.from_vector_row(row), row)
        for row in rows
    )
    _RIDDLE_JOKER_QUOTE_ROW_CACHE[cache_key] = prepared
    return prepared


def _fetch_riddle_joker_dialogue_tsv_rows() -> list[dict[str, Any]]:
    path = Path(
        str(os.environ.get("HOST_RIDDLE_JOKER_DIALOGUE_TSV") or "").strip()
        or DEFAULT_RIDDLE_JOKER_DIALOGUE_TSV
    )
    cache_key = str(path)
    cached = _RIDDLE_JOKER_ROW_CACHE.get(cache_key)
    if cached is not None:
        return cached
    if not path.exists():
        _RIDDLE_JOKER_ROW_CACHE[cache_key] = []
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            for raw in reader:
                line_no = str(raw.get("line_no") or "").strip()
                speaker = str(raw.get("speaker") or "").strip()
                text = str(raw.get("text") or "").strip()
                if not line_no or not text or speaker in {"旁白", "地文"}:
                    continue
                text = re.sub(r"\s+", "", text)
                if not text or _unsafe_runtime_quote_text(text):
                    continue
                rows.append(_riddle_joker_tsv_quote_row(line_no=line_no, speaker=speaker, text=text, path=path))
    except Exception:
        rows = []
    _RIDDLE_JOKER_ROW_CACHE[cache_key] = rows
    return rows


def _riddle_joker_tsv_quote_row(*, line_no: str, speaker: str, text: str, path: Path) -> dict[str, Any]:
    quote_id = f"riddle_joker_tsv::{line_no}"
    tags = ["daily"]
    moves = ["quick_reaction"]
    if any(marker in text for marker in ("不是", "才不是", "哈？", "什么嘛", "笨蛋", "随便", "稀罕")):
        tags.append("annoyed")
        moves.extend(["quick_rebuttal", "familiar_pushback"])
    if any(marker in text for marker in ("早上好", "早啊", "迟到", "早饭", "睡", "等一下", "重来", "再给")):
        moves.append("question_chain")
    metadata = {
        "quote_id": quote_id,
        "source_ref": f"{path.name}:{line_no}:{speaker}",
        "source_digest": quote_id,
        "prompt_text": text,
        "source_score": 1.0,
        "confidence": 0.82,
        "character_confidence": 1.0 if speaker == "七海" else 0.62,
        "risk_tier": "safe_full_line",
        "intent_tags": "|".join(dict.fromkeys(tags)),
        "style_moves": "|".join(dict.fromkeys(moves)),
        "relationship_stages": "familiar|trusted_friend|close_friend|close_but_not_romantic",
        "lexical_cues": "|".join(_quote_lexical_cues(text)),
    }
    return {
        "memory_id": quote_id,
        "text": text,
        "score": 0.0,
        "metadata": metadata,
        "collection": "riddle_joker_dialogue_tsv",
    }


def _quote_lexical_cues(text: str) -> tuple[str, ...]:
    cues = []
    for marker in (
        "早上好",
        "早啊",
        "不是",
        "才不是",
        "现在",
        "已经",
        "等一下",
        "重来",
        "随便",
        "稀罕",
        "迟到",
        "早饭",
    ):
        if marker in text:
            cues.append(marker)
    return tuple(cues)


def _query_surface_original_quote_collection_lexical_fallback(
    query: SurfaceOriginalQuoteQuery,
    *,
    top_k: int,
    collection: str,
    dim: int,
    endpoint: str,
    tenant: str,
    database: str,
) -> list[dict[str, Any]]:
    rows = _fetch_surface_original_quote_collection_rows(
        collection=collection,
        dim=dim,
        endpoint=endpoint,
        tenant=tenant,
        database=database,
    )
    scored: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        quote = SurfaceOriginalQuote.from_vector_row(row)
        score = _lexical_quote_row_score(quote, query=query)
        if score <= -100.0:
            continue
        metadata = dict(row.get("metadata") or {}) if isinstance(row.get("metadata"), Mapping) else {}
        metadata["query_embedding_model_id"] = "hash-lexical-fallback"
        enriched = {**row, "score": round(score / 100.0, 6), "metadata": metadata}
        scored.append((score, enriched))
    scored.sort(
        key=lambda item: (
            item[0],
            float((item[1].get("metadata") or {}).get("confidence") or 0.0),
            str(item[1].get("memory_id") or ""),
        ),
        reverse=True,
    )
    return [row for _, row in scored[: max(1, int(top_k or 1))]]


def _fetch_surface_original_quote_collection_rows(
    *,
    collection: str,
    dim: int,
    endpoint: str,
    tenant: str,
    database: str,
) -> list[dict[str, Any]]:
    cache_key = (str(endpoint), str(collection), int(dim))
    cached = _VECTOR_ROW_CACHE.get(cache_key)
    if cached is not None:
        return cached

    import chromadb

    parsed = urlparse(str(endpoint or "http://127.0.0.1:8000").strip())
    host = parsed.hostname or "127.0.0.1"
    port = int(parsed.port or 8000)
    ssl = parsed.scheme.lower() == "https"
    try:
        raw_client = chromadb.HttpClient(host=host, port=port, ssl=ssl, tenant=tenant, database=database)
    except Exception:
        raw_client = chromadb.HttpClient(host=host, port=port, ssl=ssl)
    coll = raw_client.get_collection(name=collection)
    result = coll.get(include=["documents", "metadatas"])
    ids = list(result.get("ids") or [])
    docs = list(result.get("documents") or [])
    metadatas = list(result.get("metadatas") or [])
    rows: list[dict[str, Any]] = []
    for idx, row_id in enumerate(ids):
        text = str(docs[idx]) if idx < len(docs) and docs[idx] is not None else ""
        metadata = dict(metadatas[idx] or {}) if idx < len(metadatas) and isinstance(metadatas[idx], Mapping) else {}
        rows.append(
            {
                "memory_id": str(row_id),
                "text": text,
                "score": 0.0,
                "metadata": metadata,
                "collection": collection,
            }
        )
    _VECTOR_ROW_CACHE[cache_key] = rows
    return rows


def _rank_quotes(
    quotes: Iterable[SurfaceOriginalQuote],
    *,
    query: SurfaceOriginalQuoteQuery,
) -> list[SurfaceOriginalQuote]:
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

    def usable(quote: SurfaceOriginalQuote) -> bool:
        text = quote.prompt_text or quote.text
        if not quote.quote_id or not text or not _CJK_RE.search(text):
            return False
        if _quote_exceeds_question_budget(query, text):
            return False
        if _unsafe_runtime_quote_text(text):
            return False
        if quote.risk_tier in _SKIP_RISK_TIERS:
            return False
        if _clean_policy_scene(query.scene_id) in _SCENE_REQUIRES_LEXICAL_HIT and not any(
            keyword in text for keyword in _required_lexical_keywords_for_query(query)
        ):
            return False
        if not _scene_prompt_compatible(text, scene_id=query.scene_id):
            return False
        return _stage_prompt_compatible(text, stage=stage)

    def score(quote: SurfaceOriginalQuote) -> float:
        value = float(quote.vector_score or 0.0) * 1.15
        value += float(quote.confidence or 0.0) * 0.78
        value += float(quote.character_confidence or 0.0) * 0.18
        value += float(quote.source_score or 0.0) * 0.05
        value += len(intent_set.intersection(quote.intent_tags)) * 1.15
        value += len(move_set.intersection(quote.style_moves)) * 0.72
        value += len(emotion_set.intersection(quote.emotion_tags)) * 0.55
        if stage and stage in quote.relationship_stages:
            value += 0.5
        if intent_set and not intent_set.intersection(quote.intent_tags):
            value -= 0.5
        if move_set and not move_set.intersection(quote.style_moves):
            value -= 0.25
        if quote.risk_tier == "rhythm_fragment":
            value -= 0.18
        value -= abs(len(quote.prompt_text or quote.text) - 22) * 0.004
        return value

    ranked = sorted(
        (quote for quote in quotes if usable(quote)),
        key=lambda quote: (score(quote), tag_relevance(quote), quote.confidence, quote.vector_score, quote.quote_id),
        reverse=True,
    )
    return ranked


def _lexical_quote_row_score(quote: SurfaceOriginalQuote, *, query: SurfaceOriginalQuoteQuery) -> float:
    text = quote.prompt_text or quote.text
    if not text or not _CJK_RE.search(text) or _unsafe_runtime_quote_text(text):
        return -999.0
    if _quote_exceeds_question_budget(query, text):
        return -999.0
    if quote.risk_tier in _SKIP_RISK_TIERS:
        return -999.0
    if not _scene_prompt_compatible(text, scene_id=query.scene_id):
        return -999.0
    if not _stage_prompt_compatible(text, stage=query.relationship_stage):
        return -999.0

    intent_set = set(query.intent_tags)
    emotion_set = set(query.emotion_tags)
    move_set = set(query.style_moves)
    keywords = _lexical_keywords_for_query(query)
    required_keywords = _required_lexical_keywords_for_query(query)
    keyword_hits = tuple(keyword for keyword in keywords if keyword and keyword in text)
    scene = _clean_policy_scene(query.scene_id)
    if scene in _SCENE_REQUIRES_LEXICAL_HIT and not any(keyword and keyword in text for keyword in required_keywords):
        return -999.0

    score = 0.0
    score += float(quote.confidence or 0.0) * 22.0
    score += float(quote.character_confidence or 0.0) * 7.0
    score += min(float(quote.source_score or 0.0), 7.0) * 2.2
    score += len(intent_set.intersection(quote.intent_tags)) * 12.0
    score += len(move_set.intersection(quote.style_moves)) * 8.0
    score += len(emotion_set.intersection(quote.emotion_tags)) * 6.0
    if query.relationship_stage and query.relationship_stage in quote.relationship_stages:
        score += 5.0

    query_text = str(query.user_message or "").strip()
    if len(query_text) >= 2 and query_text in text:
        score += 18.0
    score += len(keyword_hits) * 7.0
    if any(marker in text for marker in ("哈？", "哼", "什么嘛", "才不是", "谁稀罕", "老哥", "哥哥", "人家")):
        score += 4.5
    if any(punct in text for punct in ("！", "？", "……")):
        score += 2.0
    text_len = len(text)
    if 4 <= text_len <= 34:
        score += 3.0
    score -= max(0, text_len - 46) * 0.6
    return score


def _quote_exceeds_question_budget(
    query: SurfaceOriginalQuoteQuery,
    text: str,
) -> bool:
    budget = query.question_budget
    if budget is None:
        return False
    normalized = str(text or "")
    question_count = normalized.count("？") + normalized.count("?")
    return question_count > max(0, int(budget))


def _lexical_keywords_for_query(query: SurfaceOriginalQuoteQuery) -> tuple[str, ...]:
    scene = _clean_policy_scene(query.scene_id)
    keywords: list[str] = list(_SCENE_LEXICAL_HINTS.get(scene, ()))
    raw_user = str(query.user_message or "").strip()
    if len(raw_user) >= 2:
        keywords.append(raw_user)
    if "question_chain" in query.style_moves:
        keywords.extend(["怎么", "为什么", "怎么办", "接下来"])
    if "quick_rebuttal" in query.style_moves:
        keywords.extend(["哈？", "才不是", "什么嘛", "谁稀罕"])
    if "soft_reassurance" in query.style_moves:
        keywords.extend(["没事", "放心", "别", "休息"])
    return tuple(dict.fromkeys(item for item in keywords if item))


def _required_lexical_keywords_for_query(query: SurfaceOriginalQuoteQuery) -> tuple[str, ...]:
    scene = _clean_policy_scene(query.scene_id)
    if scene == "temporal_mismatch_greeting":
        return (
            "早上好",
            "早啊",
            "早安",
            "下午",
            "现在",
            "已经",
            "迟到",
            "早饭",
            "睡回笼觉",
        )
    keywords: list[str] = list(_SCENE_LEXICAL_HINTS.get(scene, ()))
    raw_user = str(query.user_message or "").strip()
    if len(raw_user) >= 2:
        keywords.append(raw_user)
    return tuple(dict.fromkeys(item for item in keywords if item))


def _unsafe_runtime_quote_text(text: str) -> bool:
    clean = str(text or "")
    if len(clean) > 56:
        return True
    if _KANA_RE.search(clean) or _ASCII_WORD_RE.search(clean):
        return True
    if any(marker in clean for marker in _RUNTIME_PLOT_MARKERS):
        return True
    if any(marker in clean for marker in _RUNTIME_UNSAFE_MARKERS):
        return True
    if any(marker in clean for marker in _RUNTIME_OVERBOUND_RELATION_MARKERS):
        return True
    return False


def _metadata_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        return [str(item).strip() for item in raw if str(item).strip()]
    text = str(raw or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, list):
        return [str(item).strip() for item in parsed if str(item).strip()]
    if "|" in text:
        return [item.strip() for item in text.split("|") if item.strip()]
    if "," in text:
        return [item.strip() for item in text.split(",") if item.strip()]
    return [text]


def _dedupe_vector_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for raw in rows:
        row = dict(raw)
        metadata = dict(row.get("metadata") or {}) if isinstance(row.get("metadata"), Mapping) else {}
        key = str(metadata.get("quote_id") or row.get("memory_id") or row.get("text") or "").strip()
        if not key:
            continue
        existing = out.get(key)
        if existing is None or _coerce_float(row.get("score"), default=0.0) > _coerce_float(
            existing.get("score"),
            default=0.0,
        ):
            out[key] = row
    return list(out.values())


def _quote_collection_name() -> str:
    return (
        str(os.environ.get("HOST_SURFACE_ORIGINAL_QUOTE_COLLECTION") or "").strip()
        or DEFAULT_SURFACE_ORIGINAL_QUOTE_COLLECTION
    )


def _quote_vector_dim() -> int:
    return _quote_limit_env("HOST_SURFACE_ORIGINAL_QUOTE_VECTOR_DIM", DEFAULT_SURFACE_ORIGINAL_QUOTE_VECTOR_DIM)


def _quote_embedding_mode() -> str:
    mode = str(os.environ.get("HOST_SURFACE_ORIGINAL_QUOTE_EMBEDDING_MODE") or "auto").strip().lower()
    return "auto" if mode == "auto" else "hash"


def _riddle_joker_dialogue_collection_name() -> str:
    return (
        str(os.environ.get("HOST_RIDDLE_JOKER_DIALOGUE_COLLECTION") or "").strip()
        or DEFAULT_RIDDLE_JOKER_DIALOGUE_COLLECTION
    )


def _riddle_joker_dialogue_vector_dim() -> int:
    return _quote_limit_env("HOST_RIDDLE_JOKER_DIALOGUE_VECTOR_DIM", DEFAULT_SURFACE_ORIGINAL_QUOTE_VECTOR_DIM)


def _riddle_joker_dialogue_min_score() -> float:
    try:
        value = float(str(os.environ.get("HOST_RIDDLE_JOKER_DIALOGUE_MIN_SCORE") or "").strip() or DEFAULT_RIDDLE_JOKER_DIALOGUE_MIN_SCORE)
    except (TypeError, ValueError):
        value = DEFAULT_RIDDLE_JOKER_DIALOGUE_MIN_SCORE
    return max(0.0, min(float(value), 1.0))


def _riddle_joker_lexical_fallback_enabled() -> bool:
    raw = str(os.environ.get("HOST_RIDDLE_JOKER_DIALOGUE_LEXICAL_FALLBACK", "0")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _riddle_joker_lexical_first_enabled() -> bool:
    raw = str(os.environ.get("HOST_SURFACE_RIDDLE_JOKER_LEXICAL_FIRST", "1")).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _riddle_joker_primary_character_only_enabled() -> bool:
    raw = str(
        os.environ.get("HOST_SURFACE_RIDDLE_JOKER_PRIMARY_CHARACTER_ONLY", "1")
    ).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _riddle_joker_vector_fallback_after_lexical_miss_enabled() -> bool:
    raw = str(
        os.environ.get(
            "HOST_SURFACE_RIDDLE_JOKER_VECTOR_FALLBACK_AFTER_LEXICAL_MISS",
            "0",
        )
    ).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _quote_limit_env(name: str, default: int) -> int:
    try:
        return max(1, int(str(os.environ.get(name) or "").strip() or default))
    except (TypeError, ValueError):
        return max(1, int(default))


def _vector_query_text(query: SurfaceOriginalQuoteQuery) -> str:
    parts = [
        f"scene:{query.scene_id}",
        f"relationship:{query.relationship_stage}",
        query.user_message,
        *[f"intent:{item}" for item in query.intent_tags],
        *[f"emotion:{item}" for item in query.emotion_tags],
        *[f"style:{item}" for item in query.style_moves],
    ]
    return "；".join(str(item).strip() for item in parts if str(item).strip())


def _clean_policy_scene(scene_id: str) -> str:
    scene = str(scene_id or "").strip()
    return _SCENE_POLICY_ALIASES.get(scene, scene)


def _stage_prompt_compatible(text: str, *, stage: str = "") -> bool:
    clean = str(text or "")
    normalized = _copy_normalized(clean)
    if stage in {"familiar", "trusted_friend", "close_friend", "close_but_not_romantic", "familiar_not_romantic"} and any(
        marker in clean
        for marker in ("喜欢你", "喜欢哥哥", "最喜欢", "想你", "想见", "抱抱", "在一起", "很幸福")
    ):
        return False
    if stage in {"ambiguous", "pre_confession"} and any(
        marker in clean
        for marker in (
            "喜欢你",
            "喜欢哥哥",
            "最喜欢",
            "喜欢好久",
            "求抱抱",
            "猛烈攻势",
            "超级帅",
            "我喜欢上你",
            "喜欢上哥哥",
            "不喜欢我",
            "真的喜欢",
            "一直",
            "对不起",
            "喜欢，喜欢",
            "太喜欢",
        )
    ):
        return False
    if stage in {"ambiguous", "pre_confession"} and (
        "喜欢哥哥" in normalized or ("喜欢" in normalized and "哥哥" in normalized)
    ):
        return False
    return True


def _scene_prompt_compatible(text: str, *, scene_id: str = "") -> bool:
    scene = _clean_policy_scene(scene_id)
    clean = str(text or "")
    normalized = _copy_normalized(clean)
    if any(marker in clean for marker in _SCENE_BLOCKED_MARKERS.get(scene, ())):
        return False
    if scene == "temporal_mismatch_greeting" and normalized in {"早上好", "早安", "早啊", "嗯早上好"}:
        return False
    if scene == "ambiguous_affection" and ("喜欢哥哥" in normalized or ("喜欢" in normalized and "哥哥" in normalized)):
        return False
    return True


def _copy_normalized(text: str) -> str:
    return _COPY_NORMALIZE_RE.sub("", str(text or "")).strip()


__all__ = [
    "DEFAULT_SURFACE_ORIGINAL_QUOTE_COLLECTION",
    "DEFAULT_SURFACE_ORIGINAL_QUOTE_VECTOR_DIM",
    "SurfaceOriginalQuote",
    "SurfaceOriginalQuoteBank",
    "SurfaceOriginalQuotePolicy",
    "SurfaceOriginalQuoteQuery",
    "SurfaceOriginalQuoteSelection",
    "clear_surface_original_quote_bank_cache",
    "render_surface_original_quote_guidance",
    "select_surface_original_quotes",
    "surface_original_quote_policy_for_scene",
    "surface_original_quote_similarity_hits",
    "surface_original_quotes_enabled",
    "to_prompt_style_text",
]
