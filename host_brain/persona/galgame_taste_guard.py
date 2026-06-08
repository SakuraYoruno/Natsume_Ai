from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GalgameTastePattern:
    category: str
    tokens: tuple[str, ...]
    penalty: int = 16

    @property
    def reason_code(self) -> str:
        return f"TASTE_{self.category}"


@dataclass(frozen=True)
class GalgameTasteHit:
    category: str
    token: str
    reason_code: str
    penalty: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "token": self.token,
            "reason_code": self.reason_code,
            "penalty": int(self.penalty),
        }


_STAGED_PRAISE_REACTION_GROUPS: tuple[tuple[str, ...], ...] = (
    ("突然夸我干嘛", "夸我干嘛", "突然说这个干嘛", "干嘛突然说", "突然夸我", "突然夸"),
    ("怪让人不好意思", "怪不好意思"),
    ("只许说一次", "只准说一次", "老哥你这话", "就收下啦", "我就收下", "收下啦"),
)

_TASTE_PATTERNS: tuple[GalgameTastePattern, ...] = (
    GalgameTastePattern(
        category="PARENTAL_SCOLD_TONE",
        penalty=18,
        tokens=(
            "叫人",
            "记得叫",
            "先叫我",
            "叫我一声",
            "先跟我打招呼",
            "还知道",
            "算你记得",
            "算你识相",
            "算你过关",
            "算你会先叫",
            "算你有记着我",
            "有点良心",
            "挺自觉",
            "自觉",
        ),
    ),
    GalgameTastePattern(
        category="UNSUPPORTED_HISTORY_GUESS",
        penalty=16,
        tokens=(
            "再不理我",
            "不理我",
            "不打算理我",
            "没理我",
            "不搭理",
            "我就不说话",
            "你这也太晚",
            "太晚了吧",
            "没让我等太久",
            "等睡着",
            "快等睡着",
            "等你开口",
            "等了好一会儿",
            "平时也没见",
            "之前不是",
            "以前不是",
            "才多久不见",
        ),
    ),
    GalgameTastePattern(
        category="TOOL_COMPANION_FRAMING",
        penalty=16,
        tokens=(
            "陪你聊天",
            "陪你说说话",
            "陪你说话",
            "可以陪你聊",
            "我可以陪你",
            "想聊什么",
            "我在这里",
            "我一直在",
            "在这儿呢",
            "我在这儿呢",
        ),
    ),
    GalgameTastePattern(
        category="HELP_SEEKING_MISREAD",
        penalty=18,
        tokens=(
            "想让我帮",
            "想让我干",
            "帮你做什么",
            "有事求我",
            "有求于我",
            "该不会是要我",
            "该不会是有求于我",
            "是不是又要我",
        ),
    ),
    GalgameTastePattern(
        category="OVERAGGRESSIVE_TEASE",
        penalty=18,
        tokens=(
            "能拿我怎么样",
            "偏要更烦",
            "更烦一点",
            "你才烦",
            "自己才麻烦",
            "说清楚点",
            "别糊弄我",
        ),
    ),
    GalgameTastePattern(
        category="STALE_SELF_IMPORTANT_BIT",
        penalty=14,
        tokens=(
            "本天才",
            "本小姐",
            "保管",
            "保证让你",
            "保证你",
        ),
    ),
)


def _find_staged_praise_reaction_hit(haystack: str) -> GalgameTasteHit | None:
    group_hits: list[str] = []
    for group in _STAGED_PRAISE_REACTION_GROUPS:
        hit = next((token for token in group if token and token in haystack), "")
        if hit:
            group_hits.append(hit)
    if len(group_hits) < 2:
        return None
    return GalgameTasteHit(
        category="STAGED_PRAISE_REACTION",
        token=" + ".join(group_hits[:3]),
        reason_code="TASTE_STAGED_PRAISE_REACTION",
        penalty=18,
    )


def find_galgame_taste_hits(text: Any) -> tuple[GalgameTasteHit, ...]:
    haystack = str(text or "").strip()
    if not haystack:
        return ()
    hits: list[GalgameTasteHit] = []
    staged_hit = _find_staged_praise_reaction_hit(haystack)
    if staged_hit is not None:
        hits.append(staged_hit)
    for pattern in _TASTE_PATTERNS:
        for token in pattern.tokens:
            if token and token in haystack:
                hits.append(
                    GalgameTasteHit(
                        category=pattern.category,
                        token=token,
                        reason_code=pattern.reason_code,
                        penalty=pattern.penalty,
                    )
                )
    return tuple(hits)


__all__ = ["GalgameTasteHit", "GalgameTastePattern", "find_galgame_taste_hits"]
