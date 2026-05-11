from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from host_brain.persona.galgame_text_contract import GalgameTextContract
from host_brain.persona.galgame_taste_guard import find_galgame_taste_hits
from host_brain.persona.text_quality_gate import TextQualityGateResult


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_KANA_RE = re.compile(r"[\u3040-\u30ff]")
_ASCII_WORD_RE = re.compile(r"[A-Za-z]{2,}")
_JSONISH_RE = re.compile(r"^\s*[\[{].*[\]}]\s*$", re.DOTALL)
_STAGE_DIRECTION_RE = re.compile(r"(?:[（(【\[].{0,120}?[）)】\]]|\*.{1,120}?\*)", re.DOTALL)

_SERVICE_TONE = (
    "请问",
    "需要我",
    "我可以",
    "有什么事",
    "有事吗",
    "怎么了",
    "怎么啦",
    "说来听听",
    "说出来听听",
)
_SERVICE_TONE_EXEMPTIONS = (
    "话多怎么了",
    "烦怎么了",
    "吵怎么了",
)
_LOW_ENERGY = ("嗯。", "哦。", "知道了", "好吧", "行吧。")
_WRONG_KINSHIP = ("哥哥", "你哥", "我哥", "老哥", "兄长", "兄ちゃん", "お兄ちゃん")


@dataclass(frozen=True)
class _InferenceRule:
    token: str
    category: str
    reason_code: str
    penalty: int
    exempt_scenes: tuple[str, ...] = ()


_WORLD_FACT_RULES = (
    *tuple(
        _InferenceRule(token=token, category="world_fact", reason_code="WORLD_FACT_FORBIDDEN", penalty=14)
        for token in (
            "平时",
            "平常",
            "每次",
            "总是",
            "昨晚",
            "昨天",
            "冰箱",
            "外卖",
            "刷手机",
            "房间",
            "厨房",
            "听见",
            "听到",
            "看到",
            "声音",
            "听着",
            "听出来",
            "听得见",
            "叫这么小声",
            "屏幕",
            "周末",
            "明天不是周末",
            "明天肯定",
            "中午",
            "早饭",
            "胃",
            "胃疼",
            "瘫着",
            "没精打采",
            "一脸",
            "精神",
            "语气听着",
            "有空",
            "一大早",
            "来得正好",
            "慢吞吞",
            "一出声",
            "这边",
            "明明就是有事",
            "有事的样子",
            "闲着",
            "健忘",
            "第一天认识",
            "跟你混久",
            "混久",
            "开心的样子",
            "看你开心的样子",
            "几分钟",
            "三分钟",
            "两次",
            "三遍",
            "记性",
            "住在",
            "一个屋檐",
            "屋檐下",
            "吃这么慢",
            "等到差点饿死",
            "被水呛",
            "水呛",
            "肚子",
            "咕咕叫",
            "赶剧本",
            "起这么早",
            "这么早",
            "作息",
            "计划周末",
        )
    ),
    _InferenceRule(
        token="小声",
        category="world_fact",
        reason_code="WORLD_FACT_FORBIDDEN",
        penalty=14,
        exempt_scenes=("quiet",),
    ),
)
_SCENE_ACTION_RULES = tuple(
    _InferenceRule(token=token, category="scene_action", reason_code="SCENE_ACTION_FORBIDDEN", penalty=14)
    for token in (
        "倒杯",
        "拿药",
        "我这边",
        "待会",
        "待会儿",
        "乱动",
        "过来坐",
        "坐这儿",
        "坐下",
        "靠一下",
        "躺会儿",
        "去躺",
        "过来躺",
        "趴桌上",
        "肩膀",
        "被子",
        "盯着",
        "光标",
        "位置",
        "占好位置",
        "站着",
        "蹲那儿",
    )
)
_RELATION_DEPENDENCY_RULES = (
    *(
        _InferenceRule(
            token=token,
            category="relation_dependency",
            reason_code="RELATION_DEPENDENCY_FORBIDDEN",
            penalty=12,
        )
        for token in (
            "等你来",
            "等你",
            "找我",
            "来找我",
            "理我",
            "有空理我",
            "我一直都在",
            "一直都在",
            "在这儿",
            "我在这儿",
            "在这儿呢",
            "我在这里",
            "我在呢",
            "不会跑",
            "跑掉",
            "随你什么时候叫",
            "什么时候叫",
            "忙完记得",
            "回来找我",
            "就等你这句话",
            "等你这句话",
        )
    ),
)
_INFERENCE_FORBIDDEN_RULES = _WORLD_FACT_RULES + _SCENE_ACTION_RULES + _RELATION_DEPENDENCY_RULES
_ALLOWED_INFERENCE_MARKERS = (
    (
        "STYLE_INFERENCE_ALLOWED",
        (
            "哼",
            "诶",
            "欸",
            "喂",
            "真是",
            "有点",
            "稍微",
            "别硬撑",
            "缓一下",
            "歇一下",
            "行啦",
        ),
    ),
    (
        "RELATION_STANCE_ALLOWED",
        (
            "尼尼",
            "见外",
            "想帮你",
            "我想帮",
            "不是外人",
            "又不是外人",
            "我也有点想",
            "也想你",
            "陪你看",
            "一起看",
        ),
    ),
)
_STANDBY_EXEMPT_SCENES = {"presence"}


@dataclass(frozen=True)
class GalgameTextScore:
    score: float = 0.0
    hard_fail: bool = False
    reason_codes: tuple[str, ...] = ()
    penalties: tuple[str, ...] = ()
    bonuses: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": round(float(self.score or 0.0), 3),
            "hard_fail": bool(self.hard_fail),
            "reason_codes": list(self.reason_codes),
            "penalties": list(self.penalties),
            "bonuses": list(self.bonuses),
        }


def _clean_text(raw: Any) -> str:
    return str(raw or "").strip()


def _question_count(text: str) -> int:
    return text.count("?") + text.count("？")


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(item and item in text for item in needles)


def _contains_service_tone(text: str) -> bool:
    for item in _SERVICE_TONE:
        if not item or item not in text:
            continue
        if item in {"怎么了", "怎么啦"} and any(token in text for token in _SERVICE_TONE_EXEMPTIONS):
            continue
        return True
    return False


def _collapse_repeated_cjk(text: str) -> str:
    return re.sub(r"([\u4e00-\u9fff])\1+", r"\1", text)


def _find_forbidden_inference_hits(text: str, contract: GalgameTextContract) -> tuple[_InferenceRule, ...]:
    hits: list[_InferenceRule] = []
    seen: set[tuple[str, str]] = set()
    for rule in _INFERENCE_FORBIDDEN_RULES:
        if contract.scene_id in rule.exempt_scenes:
            continue
        if rule.reason_code == "RELATION_DEPENDENCY_FORBIDDEN" and contract.scene_id in _STANDBY_EXEMPT_SCENES:
            continue
        if not rule.token or rule.token not in text:
            continue
        key = (rule.category, rule.token)
        if key in seen:
            continue
        seen.add(key)
        hits.append(rule)
    return tuple(hits)


def _find_allowed_inference_markers(text: str) -> tuple[str, ...]:
    markers: list[str] = []
    for category, tokens in _ALLOWED_INFERENCE_MARKERS:
        for token in tokens:
            if token and token in text:
                markers.append(f"{category}:{token}")
                break
    return tuple(markers)


def score_galgame_candidate(
    *,
    text: Any,
    contract: GalgameTextContract,
    quality: TextQualityGateResult | None = None,
) -> GalgameTextScore:
    normalized = _clean_text(text)
    score = 100.0
    hard_fail = False
    reasons: list[str] = []
    penalties: list[str] = []
    bonuses: list[str] = []

    if not normalized:
        return GalgameTextScore(
            score=0.0,
            hard_fail=True,
            reason_codes=("EMPTY_TEXT",),
            penalties=("empty text",),
        )

    if _JSONISH_RE.match(normalized):
        score -= 80
        hard_fail = True
        reasons.append("JSON_OR_STRUCTURED_OUTPUT")
        penalties.append("json output")
    if _STAGE_DIRECTION_RE.search(normalized):
        score -= 65
        hard_fail = True
        reasons.append("STAGE_DIRECTION_OUTPUT")
        penalties.append("stage direction")
    if _KANA_RE.search(normalized):
        score -= 70
        hard_fail = True
        reasons.append("EXPECTED_ZH_TEXT")
        penalties.append("kana in zh text")
    if not _CJK_RE.search(normalized):
        score -= 70
        hard_fail = True
        reasons.append("NO_ZH_TEXT")
        penalties.append("no chinese text")
    if _ASCII_WORD_RE.search(normalized):
        score -= 25
        reasons.append("ASCII_WORD_IN_ZH_TEXT")
        penalties.append("ascii word in zh text")
    if _contains_any(normalized, _WRONG_KINSHIP):
        score -= 85
        hard_fail = True
        reasons.append("WRONG_KINSHIP_TERM")
        penalties.append("wrong kinship term")

    if quality is not None:
        for code in quality.reason_codes or ():
            if code in {
                "INTERNAL_FIELD_LEAK",
                "MOJIBAKE",
                "JSON_OR_STRUCTURED_OUTPUT",
                "STAGE_DIRECTION_OUTPUT",
                "EXPECTED_JAPANESE_VOICE_TEXT",
                "EXPECTED_ZH_TEXT",
            }:
                score -= 55
                hard_fail = True
                reasons.append(code)
                penalties.append(code.lower())
            elif code == "FORBIDDEN_TEMPLATE_TEXT":
                score -= 18
                reasons.append(code)
                penalties.append("quality gate forbidden template")
            elif code:
                score -= 8
                reasons.append(code)
                penalties.append(code.lower())

    collapsed = _collapse_repeated_cjk(normalized)
    for forbidden in contract.forbidden_surface:
        if forbidden and (forbidden in normalized or forbidden in collapsed):
            score -= 24
            reasons.append("CONTRACT_FORBIDDEN_SURFACE")
            penalties.append(f"forbidden:{forbidden}")

    for group in contract.required_any:
        if group and not any(token and token in normalized for token in group):
            score -= 18
            reasons.append("CONTRACT_MISSING_REQUIRED_MOVE")
            penalties.append("missing_any:" + "|".join(group))

    questions = _question_count(normalized)
    if questions > contract.max_questions:
        extra = questions - contract.max_questions
        score -= min(24, 8 * extra)
        reasons.append("TOO_MANY_QUESTIONS")
        penalties.append(f"questions:{questions}>{contract.max_questions}")

    length = len(normalized)
    if length < contract.target_min_chars:
        score -= 14
        reasons.append("TOO_SHORT")
        penalties.append(f"too_short:{length}")
    if length > contract.target_max_chars:
        score -= min(24, 4 + (length - contract.target_max_chars) * 0.5)
        reasons.append("TOO_LONG")
        penalties.append(f"too_long:{length}")

    if _contains_service_tone(normalized):
        score -= 14
        reasons.append("SERVICE_TONE")
        penalties.append("service tone")
    inference_hits = _find_forbidden_inference_hits(normalized, contract)
    if inference_hits:
        score -= min(42, sum(hit.penalty for hit in inference_hits))
        for hit in inference_hits:
            reasons.append(hit.reason_code)
            penalties.append(f"inference:{hit.category}:{hit.token}")
    if normalized in _LOW_ENERGY:
        score -= 12
        reasons.append("LOW_INFORMATION_REPLY")
        penalties.append("low energy")
    taste_hits = find_galgame_taste_hits(normalized)
    if taste_hits:
        score -= min(44, sum(hit.penalty for hit in taste_hits))
        for hit in taste_hits:
            reasons.append(hit.reason_code)
            penalties.append(f"taste:{hit.category}:{hit.token}")

    if any(marker in normalized for marker in ("哼", "诶", "欸", "喂", "尼尼", "行啦", "真是")):
        score += 4
        bonuses.append("galgame marker")
    if 6 <= length <= 36:
        score += 3
        bonuses.append("compact length")
    if contract.response_move != "reply_current_turn":
        score += 1
        bonuses.append("scene contract")
    bonuses.extend(_find_allowed_inference_markers(normalized))

    score = max(0.0, min(100.0, score))
    if hard_fail:
        score = min(score, 35.0)
    return GalgameTextScore(
        score=score,
        hard_fail=hard_fail,
        reason_codes=tuple(dict.fromkeys(reasons)),
        penalties=tuple(penalties),
        bonuses=tuple(bonuses),
    )


__all__ = ["GalgameTextScore", "score_galgame_candidate"]
