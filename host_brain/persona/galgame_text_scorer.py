from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from host_brain.persona.galgame_text_contract import GalgameTextContract
from host_brain.persona.galgame_taste_guard import find_galgame_taste_hits
from host_brain.persona.sample_guided_text_scorer import score_sample_guided_text
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
    hard_gate_codes: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    penalties: tuple[str, ...] = ()
    bonuses: tuple[str, ...] = ()
    dimensions: dict[str, float] | None = None
    weights: dict[str, float] | None = None
    sample_scene_id: str = ""
    sample_title: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": round(float(self.score or 0.0), 3),
            "hard_fail": bool(self.hard_fail),
            "hard_gate_codes": list(self.hard_gate_codes),
            "reason_codes": list(self.reason_codes),
            "penalties": list(self.penalties),
            "bonuses": list(self.bonuses),
            "dimensions": dict(self.dimensions or {}),
            "weights": dict(self.weights or {}),
            "sample_scene_id": self.sample_scene_id,
            "sample_title": self.sample_title,
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
    sample_score = score_sample_guided_text(text=normalized, contract=contract, quality=quality)
    score = float(sample_score.score)
    hard_fail = bool(sample_score.hard_fail)
    hard_gate_codes = list(sample_score.hard_gate_codes)
    reasons: list[str] = list(sample_score.reason_codes)
    penalties: list[str] = list(sample_score.penalties)
    bonuses: list[str] = list(sample_score.bonuses)

    # Legacy contract surface and inference taxonomy are now evidence for soft
    # scoring only. They remain visible in traces so old failure analysis can
    # be migrated, but they no longer turn a candidate into an automatic reject.
    collapsed = _collapse_repeated_cjk(normalized)
    contract_hits = [
        forbidden
        for forbidden in contract.forbidden_surface
        if forbidden and (forbidden in normalized or forbidden in collapsed)
    ]
    if contract_hits:
        score -= min(18.0, 3.0 * len(contract_hits))
        reasons.append("CONTRACT_FORBIDDEN_SURFACE")
        penalties.append("soft_contract_surface:" + " / ".join(contract_hits[:5]))

    missing_groups = [
        group
        for group in contract.required_any
        if group and not any(token and token in normalized for token in group)
    ]
    if missing_groups:
        score -= min(18.0, 6.0 * len(missing_groups))
        reasons.append("CONTRACT_MISSING_REQUIRED_MOVE")
        penalties.append("soft_missing_any:" + " / ".join("|".join(group) for group in missing_groups[:3]))

    inference_hits = _find_forbidden_inference_hits(normalized, contract)
    if inference_hits:
        score -= min(22.0, sum(hit.penalty for hit in inference_hits) * 0.45)
        for hit in inference_hits:
            reasons.append(hit.reason_code)
            penalties.append(f"soft_inference:{hit.category}:{hit.token}")

    taste_hits = find_galgame_taste_hits(normalized)
    if taste_hits:
        score -= min(22.0, sum(hit.penalty for hit in taste_hits) * 0.45)
        for hit in taste_hits:
            reasons.append(hit.reason_code)
            penalties.append(f"soft_taste:{hit.category}:{hit.token}")

    if quality is not None:
        for code in quality.reason_codes or ():
            if code and code not in reasons:
                reasons.append(code)

    bonuses.extend(_find_allowed_inference_markers(normalized))
    score = max(0.0, min(100.0, score))
    if hard_fail:
        score = min(score, 35.0)
    return GalgameTextScore(
        score=score,
        hard_fail=hard_fail,
        hard_gate_codes=tuple(dict.fromkeys(hard_gate_codes)),
        reason_codes=tuple(dict.fromkeys(reasons)),
        penalties=tuple(penalties),
        bonuses=tuple(bonuses),
        dimensions={key: round(float(value), 3) for key, value in dict(sample_score.dimensions).items()},
        weights={key: round(float(value), 3) for key, value in dict(sample_score.weights).items()},
        sample_scene_id=sample_score.sample_scene_id,
        sample_title=sample_score.sample_title,
    )


__all__ = ["GalgameTextScore", "score_galgame_candidate"]
