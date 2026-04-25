from __future__ import annotations

import re

from host_brain.persona.fallback_policy import FallbackRequest, build_fallback_reply


_SURFACE_PUNCT_RE = re.compile(r"[\s\u3000。！？?!，,、~～…；;：:\"“”'‘’（）()\[\]【】]+")
_REPEAT_REQUEST_RE = re.compile(
    r"(刚才|刚刚|刚|前面|上一句|上句|之前).{0,8}(说什么|说了什么|讲什么|讲了什么)"
)
_PRONOUN_SAY_WHAT_RE = re.compile(r"(你|我|她|他|它)?说什么")
_SPECIFIC_REFERENCE_RE = re.compile(
    r"(知道什么|什么事|什么东西|什么情况|哪个|哪一个|哪件|哪一件|哪张|哪一张|哪种|哪类|指什么|啥)"
)
_MEANING_QUERY_RE = re.compile(r"(什么意思|啥意思|是什么意思|什么梗)")
_CONTEXT_ECHO_RE = re.compile(r"(你问|你说)?(这个|这句|这句话|它)?(是什么意思|什么意思|啥意思)")
_FILLER_MEANING_RESOLUTION_RE = re.compile(
    r"(顺口|应了一声|应一声|唔了一下|嗯了一下|只是|没说|没有说|没讲|没有讲|不是在解释|没什么特别意思|没别的意思)"
)
_FILLER_RE = re.compile(r"^(?:嗯+|唔+|咦+|哼+|啊+|哦+|噢+|诶+|哈+)$")
_VAGUE_MEANING_RE = re.compile(r"(问那个|那个呀|那个意思|就是嗯|就是那个|就那样)")
_REFERENCE_RESOLUTION_RE = re.compile(
    r"(指|哪个|哪一个|哪件|哪一件|哪种|哪类|说的是|具体|哪边|哪回事|哪句|哪张)"
)
_COMMON_ANCHOR_MARKERS = ("秘密", "小事", "事情", "图片", "表情", "这张图", "这句话", "上一句")


def compact_surface_text(raw_text: str) -> str:
    compacted = _SURFACE_PUNCT_RE.sub("", str(raw_text or ""))
    compacted = re.sub(r"^(哥哥|哥|お兄ちゃん)+", "", compacted)
    compacted = re.sub(r"(呀|啊|呢|啦|嘛|吧)+$", "", compacted)
    return compacted


def context_is_repeat_request(raw_context: str) -> bool:
    compact = compact_surface_text(raw_context)
    if not compact:
        return False
    return bool(_REPEAT_REQUEST_RE.search(compact) or _PRONOUN_SAY_WHAT_RE.search(compact))


def context_needs_specific_reference(raw_context: str) -> bool:
    compact = compact_surface_text(raw_context)
    if not compact or context_is_repeat_request(compact):
        return False
    return bool(_SPECIFIC_REFERENCE_RE.search(compact))


def context_is_meaning_query(raw_context: str) -> bool:
    return bool(_MEANING_QUERY_RE.search(compact_surface_text(raw_context)))


def source_is_filler_reply(source_text: str) -> bool:
    return bool(_FILLER_RE.fullmatch(compact_surface_text(source_text)))


def extract_context_anchor_terms(raw_context: str) -> list[str]:
    compact = compact_surface_text(raw_context)
    terms: list[str] = []
    for pattern in (
        r"(?:哪个|哪一个|哪件|哪一件|哪张|哪一张|哪种|哪类)([\u4e00-\u9fff]{2,8})",
        r"(?:什么|啥)([\u4e00-\u9fff]{2,8})",
    ):
        for match in re.finditer(pattern, compact):
            term = str(match.group(1) or "").strip()
            term = re.sub(r"(呀|啊|呢|啦|嘛|吧|吗)+$", "", term)
            if len(term) >= 2 and term not in terms:
                terms.append(term)
    for marker in ("小秘密", "新秘密", *_COMMON_ANCHOR_MARKERS):
        if marker in compact and marker not in terms:
            terms.append(marker)
    collapsed: list[str] = []
    for term in terms:
        if any(term != other and term in other for other in terms):
            continue
        collapsed.append(term)
    return collapsed[:3]


def context_anchor_variants(raw_context: str) -> list[str]:
    variants: list[str] = []
    for term in extract_context_anchor_terms(raw_context):
        candidates = [term, term[-2:]]
        for marker in _COMMON_ANCHOR_MARKERS:
            if marker in term:
                candidates.append(marker)
        for item in candidates:
            item = str(item or "").strip()
            if len(item) >= 2 and item not in variants:
                variants.append(item)
    return variants


def candidate_repeats_context_without_progress(candidate_text: str, raw_context: str) -> bool:
    candidate_compact = compact_surface_text(candidate_text)
    context_compact = compact_surface_text(raw_context)
    if not candidate_compact or not context_compact:
        return False
    if context_compact in candidate_compact and len(candidate_compact) <= len(context_compact) + 8:
        return True
    if _MEANING_QUERY_RE.search(context_compact) and _CONTEXT_ECHO_RE.search(candidate_compact):
        return not re.search(r"(刚才|刚刚|之前|前面|唔|只|还没|哪句|哪张|哪件|指)", candidate_compact)
    return False


def candidate_resolves_filler_meaning(candidate_text: str, raw_context: str, source_text: str) -> bool:
    if not context_is_meaning_query(raw_context) or not source_is_filler_reply(source_text):
        return True
    candidate_compact = compact_surface_text(candidate_text)
    if not candidate_compact or _VAGUE_MEANING_RE.search(candidate_compact):
        return False
    return bool(_FILLER_MEANING_RESOLUTION_RE.search(candidate_compact))


def candidate_resolves_specific_reference(candidate_text: str, raw_context: str) -> bool:
    candidate_compact = compact_surface_text(candidate_text)
    if not candidate_compact:
        return False
    variants = context_anchor_variants(raw_context)
    if variants and not any(term in candidate_compact for term in variants):
        return False
    return bool(_REFERENCE_RESOLUTION_RE.search(candidate_compact))


def build_emergency_surface_reply(*, raw_context: str, source_text: str = "") -> str:
    """Deprecated local authoring hook.

    Emergency wording now goes through FallbackPolicy so it is explicitly
    degraded, non-cacheable, and never tries to impersonate a natural reply.
    """
    result = build_fallback_reply(
        FallbackRequest(
            reason_code="assistant_followup_surface_unavailable",
            primary_topic="assistant_reply_followup",
            user_message=raw_context,
            previous_assistant_text=source_text,
        )
    )
    return result.text


__all__ = [
    "candidate_repeats_context_without_progress",
    "candidate_resolves_filler_meaning",
    "candidate_resolves_specific_reference",
    "build_emergency_surface_reply",
    "compact_surface_text",
    "context_anchor_variants",
    "context_is_meaning_query",
    "context_is_repeat_request",
    "context_needs_specific_reference",
    "extract_context_anchor_terms",
    "source_is_filler_reply",
]
