"""Small, deterministic language cues for shared mixed resonance.

The cues are fragments rather than final replies.  They keep the real model
away from generic assistant advice without adding a second model call, a
repository write, or a fixed visible response template.
"""

from __future__ import annotations

import hashlib


_JOY_FRAGMENTS = (
    "我也跟着高兴起来",
    "听得我心里也高兴了一下",
    "我是真替你开心",
    "我这边也跟着高兴了一点",
    "这句话让我也开心",
    "我也忍不住替你高兴",
    "我心里也跟着开心了一下",
    "我也替你高兴",
)
_CAUTION_FRAGMENTS = (
    "我心里也跟着悬了一下",
    "那点怕也让我有点悬",
    "我这边也跟着紧了一下",
    "你一说不踏实，我也有点担心",
    "我心里也留着一点担心",
    "那点悬心也让我跟着紧了一下",
    "我心里也还有一点不踏实",
    "你那点怕也让我有点悬",
)


def mixed_resonance_style_cues(latest_user_content: str) -> tuple[str, str]:
    """Return two distinct clause-level cues stable for the current wording."""

    normalized = " ".join(str(latest_user_content or "").split()) or "mixed-resonance"
    seed = int.from_bytes(
        hashlib.sha256(normalized.encode("utf-8")).digest()[:4],
        "big",
    )
    index = seed % len(_JOY_FRAGMENTS)
    first = (
        f"{_JOY_FRAGMENTS[index]}；"
        f"{_CAUTION_FRAGMENTS[(index + 3) % len(_CAUTION_FRAGMENTS)]}"
    )
    second = (
        f"{_JOY_FRAGMENTS[(index + 4) % len(_JOY_FRAGMENTS)]}；"
        f"{_CAUTION_FRAGMENTS[(index + 6) % len(_CAUTION_FRAGMENTS)]}"
    )
    return first, second


__all__ = ["mixed_resonance_style_cues"]
