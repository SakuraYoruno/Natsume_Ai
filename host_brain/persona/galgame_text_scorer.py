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
_FACT_FABRICATION = (
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
    "倒杯",
    "拿药",
    "听见",
    "听到",
    "看到",
)
_LOW_ENERGY = ("嗯。", "哦。", "知道了", "好吧", "行吧。")
_WRONG_KINSHIP = ("哥哥", "我哥", "老哥", "兄长", "兄ちゃん", "お兄ちゃん")


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
    if _contains_any(normalized, _FACT_FABRICATION):
        score -= 10
        reasons.append("LIKELY_FACT_FABRICATION")
        penalties.append("likely fabricated fact")
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
