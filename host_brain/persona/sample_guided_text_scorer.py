from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Mapping

from host_brain.persona.galgame_text_contract import GalgameTextContract
from host_brain.persona.positive_style_bank import PositiveStyleSample, get_positive_style_sample
from host_brain.persona.text_quality_gate import TextQualityGateResult


SCORING_DIMENSIONS: tuple[str, ...] = (
    "naturalness",
    "role_fit",
    "context_fit",
    "usefulness",
    "non_customer_service",
    "non_oily",
    "distance_control",
    "technical_helpfulness",
    "emotional_containment",
)

_PUNCT_RE = re.compile(r"[\s，。！？!?,.、~～…“”\"'：:；;（）()\[\]{}]+")
_JSONISH_RE = re.compile(r"^\s*[\[{].*[\]}]\s*$", re.DOTALL)
_STAGE_DIRECTION_RE = re.compile(r"(?:[（(【\[].{0,120}?[）)】\]]|\*.{1,120}?\*)", re.DOTALL)
_BROKEN_REPEAT_RE = re.compile(r"(.{1,8})\1{3,}")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_KANA_RE = re.compile(r"[\u3040-\u30ff]")
_ASCII_WORD_RE = re.compile(r"[A-Za-z]{2,}")

_ABSOLUTE_HARD_QUALITY_CODES = {
    "EMPTY_TEXT",
    "INTERNAL_FIELD_LEAK",
    "MOJIBAKE",
    "JSON_OR_STRUCTURED_OUTPUT",
    "BROKEN_REPETITION",
    "STAGE_DIRECTION_OUTPUT",
    "EXPECTED_JAPANESE_VOICE_TEXT",
    "EXPECTED_ZH_TEXT",
    "SEMANTIC_EXPECTED_JA_VOICE",
    "SEMANTIC_EXPECTED_ZH_TEXT",
}

_TECHNICAL_SCENES = {
    "help_takeover",
    "write_takeover",
    "advice_continue",
    "next_step",
    "complaint",
    "small_failure",
}
_DAILY_SCENES = {
    "daily_good_morning",
    "daily_greeting_en",
    "daily_greeting_cn",
    "daily_greeting",
    "presence",
    "wait",
    "back",
    "busy",
    "bored",
    "nothing",
    "meal_share",
    "hungry",
    "sleepy",
    "what_doing",
    "play_invite",
    "quiet",
    "talkative",
    "who_called",
    "happy",
    "short_ack",
    "followup",
    "late_night",
}
_HIGH_EMOTION_SCENES = {
    "annoying_tease",
    "apology",
    "bad_mood",
    "boundary_hurt",
    "relationship_boundary",
    "give_up",
    "headache",
    "miss_you",
    "praise",
}

_SERVICE_TONE = (
    "请问",
    "有什么可以帮",
    "我可以帮",
    "我可以协助",
    "请提供",
    "具体要求",
    "需要我",
    "很高兴",
    "随时找我",
    "作为AI",
    "作为 ai",
)
_OBVIOUS_SERVICE_TEMPLATES = (
    "您好，请问有什么可以帮",
    "你好，请问有什么可以帮",
    "请问有什么可以帮",
    "如需帮助请",
)
_ROLE_SOFT_TERMS = (
    "哥哥",
    "老哥",
    "兄长",
    "你哥",
    "我哥",
)
_IDENTITY_HARD_TERMS = (
    "我是AI",
    "我是 ai",
    "我是人工智能",
    "我是助手",
    "我是系统",
    "ChatGPT",
)
_RELATION_DISTANCE_TERMS = (
    "哥哥",
    "找我",
    "等你",
    "等你来",
    "我在这",
    "我在这里",
    "我一直在",
    "随时找我",
    "回来找我",
    "忙完找我",
)
_OILY_TERMS = (
    "亲爱的",
    "宝贝",
    "爱你",
    "永远陪着",
    "存在就是为了",
    "主人",
    "命都给你",
)
_REAL_ACTION_CLAIMS = (
    "我去给你",
    "给你倒",
    "给你拿药",
    "坐你旁边",
    "我过来",
    "我刚洗",
    "我正在吃",
    "我在玩游戏",
)
_TECH_HELPFUL_ANCHORS = (
    "发",
    "贴",
    "拿",
    "看",
    "先",
    "一步",
    "一处",
    "一点",
    "小改",
    "停",
    "收",
    "改",
    "报错",
    "截图",
    "材料",
    "红字",
)
_GALGAME_MARKERS = (
    "哼",
    "诶",
    "欸",
    "喂",
    "尼尼",
    "行啦",
    "真是",
    "别",
    "啦",
    "呀",
)


@dataclass(frozen=True)
class SampleGuidedTextScore:
    score: float
    hard_fail: bool = False
    hard_gate_codes: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    penalties: tuple[str, ...] = ()
    bonuses: tuple[str, ...] = ()
    dimensions: Mapping[str, float] = field(default_factory=dict)
    weights: Mapping[str, float] = field(default_factory=dict)
    sample_scene_id: str = "default"
    sample_title: str = ""
    good_examples: tuple[str, ...] = ()
    bad_examples: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": round(float(self.score or 0.0), 3),
            "hard_fail": bool(self.hard_fail),
            "hard_gate_codes": list(self.hard_gate_codes),
            "reason_codes": list(self.reason_codes),
            "penalties": list(self.penalties),
            "bonuses": list(self.bonuses),
            "dimensions": {key: round(float(value), 3) for key, value in dict(self.dimensions).items()},
            "weights": {key: round(float(value), 3) for key, value in dict(self.weights).items()},
            "sample_scene_id": self.sample_scene_id,
            "sample_title": self.sample_title,
            "good_examples": list(self.good_examples),
            "bad_examples": list(self.bad_examples),
        }


def _clean_text(raw: Any) -> str:
    return str(raw or "").strip()


def _plain(raw: Any) -> str:
    return _PUNCT_RE.sub("", _clean_text(raw)).lower()


def _contains_any(text: str, terms: tuple[str, ...]) -> list[str]:
    lowered = text.lower()
    return [term for term in terms if term and term.lower() in lowered]


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def _drop(dimensions: dict[str, float], key: str, amount: float) -> None:
    dimensions[key] = _clamp(float(dimensions.get(key, 100.0)) - amount)


def _raise(dimensions: dict[str, float], key: str, amount: float) -> None:
    dimensions[key] = _clamp(float(dimensions.get(key, 100.0)) + amount)


def _weights_for_scene(scene_id: str) -> dict[str, float]:
    weights = {dimension: 1.0 for dimension in SCORING_DIMENSIONS}
    if scene_id in _TECHNICAL_SCENES:
        weights["usefulness"] = 1.45
        weights["technical_helpfulness"] = 1.55
        weights["context_fit"] = 1.2
        weights["role_fit"] = 0.8
        weights["distance_control"] = 0.85
    elif scene_id in _HIGH_EMOTION_SCENES:
        weights["context_fit"] = 1.45
        weights["emotional_containment"] = 1.45
        weights["distance_control"] = 1.15
        weights["usefulness"] = 0.8
    elif scene_id in _DAILY_SCENES:
        weights["naturalness"] = 1.35
        weights["distance_control"] = 1.3
        weights["context_fit"] = 1.15
        weights["technical_helpfulness"] = 0.55
    return weights


def _weighted_average(dimensions: Mapping[str, float], weights: Mapping[str, float]) -> float:
    total = 0.0
    weight_total = 0.0
    for key in SCORING_DIMENSIONS:
        weight = float(weights.get(key, 1.0))
        total += float(dimensions.get(key, 100.0)) * weight
        weight_total += weight
    return total / weight_total if weight_total else 0.0


def _sample_for_contract(contract: GalgameTextContract) -> PositiveStyleSample:
    scene_id = _clean_text(getattr(contract, "scene_id", "")) or "default"
    if scene_id == "daily_greeting":
        scene_id = "daily_greeting_cn"
    return get_positive_style_sample(scene_id)


def _hard_gate_codes(text: str, quality: TextQualityGateResult | None) -> list[str]:
    codes: list[str] = []
    if not text:
        codes.append("EMPTY_TEXT")
    if _JSONISH_RE.match(text):
        codes.append("JSON_OR_STRUCTURED_OUTPUT")
    if _STAGE_DIRECTION_RE.search(text):
        codes.append("STAGE_DIRECTION_OUTPUT")
    if _BROKEN_REPEAT_RE.search(text):
        codes.append("BROKEN_REPETITION")
    if _KANA_RE.search(text) or not _CJK_RE.search(text):
        codes.append("EXPECTED_ZH_TEXT")
    if _contains_any(text, _IDENTITY_HARD_TERMS):
        codes.append("IDENTITY_ERROR")
    if _contains_any(text, _OBVIOUS_SERVICE_TEMPLATES):
        codes.append("OBVIOUS_CUSTOMER_SERVICE_TEMPLATE")
    if _contains_any(text, _REAL_ACTION_CLAIMS):
        codes.append("REAL_ACTION_FABRICATION")
    if quality is not None:
        for code in quality.reason_codes or ():
            if code in _ABSOLUTE_HARD_QUALITY_CODES:
                codes.append(code)
    return list(dict.fromkeys(codes))


def score_sample_guided_text(
    *,
    text: Any,
    contract: GalgameTextContract,
    quality: TextQualityGateResult | None = None,
) -> SampleGuidedTextScore:
    normalized = _clean_text(text)
    sample = _sample_for_contract(contract)
    scene_id = _clean_text(sample.scene_id or getattr(contract, "scene_id", "")) or "default"
    dimensions = {dimension: 86.0 for dimension in SCORING_DIMENSIONS}
    weights = _weights_for_scene(scene_id)
    reasons: list[str] = []
    penalties: list[str] = []
    bonuses: list[str] = []

    hard_codes = _hard_gate_codes(normalized, quality)
    if hard_codes:
        for dimension in SCORING_DIMENSIONS:
            dimensions[dimension] = min(dimensions[dimension], 35.0)
        reasons.extend(hard_codes)
        penalties.extend(f"hard_gate:{code}" for code in hard_codes)

    length = len(normalized)
    plain = _plain(normalized)
    if length <= 2:
        _drop(dimensions, "naturalness", 34)
        _drop(dimensions, "context_fit", 18)
        reasons.append("TOO_SHORT")
        penalties.append("naturalness:too_short")
    elif length <= 5:
        _drop(dimensions, "naturalness", 14)
        reasons.append("TOO_SHORT")
        penalties.append("naturalness:thin_short_reply")
    if length > getattr(contract, "target_max_chars", 48):
        _drop(dimensions, "naturalness", min(30, 6 + (length - getattr(contract, "target_max_chars", 48)) * 0.5))
        reasons.append("TOO_LONG")
        penalties.append(f"naturalness:too_long:{length}")
    if normalized.count("？") + normalized.count("?") > getattr(contract, "max_questions", 1):
        _drop(dimensions, "context_fit", 10)
        _drop(dimensions, "usefulness", 8)
        reasons.append("TOO_MANY_QUESTIONS")
        penalties.append("context_fit:too_many_questions")
    if normalized.count("尼尼") > 1:
        _drop(dimensions, "naturalness", 6)
        penalties.append("naturalness:address_stacking")
    if _ASCII_WORD_RE.search(normalized):
        _drop(dimensions, "naturalness", 10)
        _drop(dimensions, "context_fit", 8)
        reasons.append("ASCII_WORD_IN_ZH_TEXT")
        penalties.append("context_fit:ascii_word")

    for group in getattr(contract, "required_any", ()) or ():
        if group and not any(token and token in normalized for token in group):
            _drop(dimensions, "context_fit", 18)
            _drop(dimensions, "usefulness", 8)
            reasons.append("CONTEXT_MISSING_REQUIRED_MOVE")
            penalties.append("context_fit:missing_any:" + "|".join(group))

    contract_hits = [
        item
        for item in getattr(contract, "forbidden_surface", ()) or ()
        if item and item in normalized
    ]
    if contract_hits:
        _drop(dimensions, "context_fit", min(34, 7 * len(contract_hits)))
        _drop(dimensions, "distance_control", min(24, 5 * len(contract_hits)))
        reasons.append("CONTEXT_SURFACE_PENALTY")
        penalties.append("context_fit:legacy_surface:" + " / ".join(contract_hits[:5]))

    service_hits = _contains_any(normalized, _SERVICE_TONE)
    if service_hits:
        _drop(dimensions, "non_customer_service", min(55, 18 * len(service_hits)))
        _drop(dimensions, "role_fit", 14)
        reasons.append("CUSTOMER_SERVICE_TONE")
        penalties.append("non_customer_service:" + " / ".join(service_hits[:4]))

    role_hits = _contains_any(normalized, _ROLE_SOFT_TERMS)
    if role_hits:
        _drop(dimensions, "role_fit", min(42, 18 * len(role_hits)))
        _drop(dimensions, "distance_control", min(30, 12 * len(role_hits)))
        reasons.append("ROLE_KINSHIP_SOFT_MISMATCH")
        penalties.append("role_fit:kinship:" + " / ".join(role_hits[:4]))

    oily_hits = _contains_any(normalized, _OILY_TERMS)
    if oily_hits:
        _drop(dimensions, "non_oily", min(58, 22 * len(oily_hits)))
        _drop(dimensions, "distance_control", min(36, 12 * len(oily_hits)))
        reasons.append("OILY_OR_OVERBOUND_TONE")
        penalties.append("non_oily:" + " / ".join(oily_hits[:4]))

    distance_hits = _contains_any(normalized, _RELATION_DISTANCE_TERMS)
    if distance_hits:
        amount = 7 if scene_id in {"presence", "wait", "back", "miss_you", "bad_mood"} else 13
        _drop(dimensions, "distance_control", min(42, amount * len(distance_hits)))
        reasons.append("DISTANCE_CONTROL_SOFT_PENALTY")
        penalties.append("distance_control:" + " / ".join(distance_hits[:4]))

    good_examples = tuple(item for item in (sample.good_examples or sample.safe_fragments) if item)
    bad_examples = tuple(item for item in (sample.bad_examples or sample.avoid) if item)
    good_hits = [item for item in good_examples if item and item in normalized]
    bad_hits = [item for item in bad_examples if item and item in normalized]
    if good_hits:
        _raise(dimensions, "role_fit", min(10, 3 * len(good_hits)))
        _raise(dimensions, "context_fit", min(10, 3 * len(good_hits)))
        bonuses.append("sample_good_hit:" + " / ".join(good_hits[:4]))
    if any(_plain(example) == plain for example in good_examples):
        _drop(dimensions, "naturalness", 8)
        reasons.append("SAMPLE_EXACT_COPY")
        penalties.append("naturalness:sample_exact_copy")
    if bad_hits:
        _drop(dimensions, "context_fit", min(36, 8 * len(bad_hits)))
        _drop(dimensions, "non_customer_service", min(20, 5 * len(bad_hits)))
        reasons.append("BAD_SAMPLE_SIMILARITY")
        penalties.append("sample_bad_hit:" + " / ".join(bad_hits[:4]))

    if any(marker in normalized for marker in _GALGAME_MARKERS):
        _raise(dimensions, "role_fit", 4)
        _raise(dimensions, "naturalness", 3)
        bonuses.append("galgame_marker")
    if 6 <= length <= 42:
        _raise(dimensions, "naturalness", 5)
        bonuses.append("compact_chat_length")

    if scene_id in _TECHNICAL_SCENES:
        tech_hits = _contains_any(normalized, _TECH_HELPFUL_ANCHORS)
        if tech_hits:
            _raise(dimensions, "technical_helpfulness", 10)
            _raise(dimensions, "usefulness", 8)
            bonuses.append("technical_anchor:" + " / ".join(tech_hits[:4]))
        else:
            _drop(dimensions, "technical_helpfulness", 32)
            _drop(dimensions, "usefulness", 24)
            reasons.append("LOW_TECHNICAL_HELPFULNESS")
            penalties.append("technical_helpfulness:missing_actionable_anchor")
    else:
        dimensions["technical_helpfulness"] = max(dimensions["technical_helpfulness"], 82.0)

    if scene_id in _HIGH_EMOTION_SCENES:
        if normalized.count("？") + normalized.count("?") >= 2:
            _drop(dimensions, "emotional_containment", 18)
            reasons.append("EMOTIONAL_CONTAINMENT_TOO_QUESTIONY")
            penalties.append("emotional_containment:too_questiony")
        if _contains_any(normalized, ("滚", "闭嘴", "懒得理你", "不管你了", "随便你")):
            _drop(dimensions, "emotional_containment", 34)
            reasons.append("EMOTIONAL_CONTAINMENT_ESCALATION")
            penalties.append("emotional_containment:escalation")

    if quality is not None:
        soft_quality_codes = [
            code for code in quality.reason_codes or () if code not in _ABSOLUTE_HARD_QUALITY_CODES
        ]
        if soft_quality_codes:
            _drop(dimensions, "naturalness", min(18, 4 * len(soft_quality_codes)))
            _drop(dimensions, "context_fit", min(18, 4 * len(soft_quality_codes)))
            reasons.extend(f"QUALITY_SOFT_{code}" for code in soft_quality_codes)
            penalties.append("quality_soft:" + " / ".join(soft_quality_codes[:5]))

    score = _weighted_average(dimensions, weights)
    if hard_codes:
        score = min(score, 35.0)
    return SampleGuidedTextScore(
        score=_clamp(score),
        hard_fail=bool(hard_codes),
        hard_gate_codes=tuple(hard_codes),
        reason_codes=tuple(dict.fromkeys(reasons)),
        penalties=tuple(penalties),
        bonuses=tuple(dict.fromkeys(bonuses)),
        dimensions={key: _clamp(value) for key, value in dimensions.items()},
        weights=weights,
        sample_scene_id=scene_id,
        sample_title=sample.title,
        good_examples=good_examples,
        bad_examples=bad_examples,
    )


__all__ = [
    "SCORING_DIMENSIONS",
    "SampleGuidedTextScore",
    "score_sample_guided_text",
]
