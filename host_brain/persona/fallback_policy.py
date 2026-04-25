from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping


SURFACE_CACHE_POLICY_VERSION = "surface-v5-sticker-emotion-20260425"

_COMPACT_RE = re.compile(r"[\s\u3000。！？?!，,、~～…；;：:\"“”'‘’（）()\[\]【】]+")
_HOST_MODEL_MARKERS = ("fallback", "emergency", "deterministic", "placeholder", "ack")
_HOST_SOURCE_MARKERS = ("fallback", "emergency", "transport_ack", "quick_ack", "gateway_ack", "placeholder")

_FORBIDDEN_SURFACE_MARKERS = (
    "突然这么可爱",
    "想让我哄",
    "想跟我撒娇",
    "真正想问",
    "别的想问",
    "想问别的",
    "哪一部分",
    "没熟到那种地步",
    "我知道了",
    "在怎么了",
    "在呀在呀怎么啦",
    "哥哥你问哪个",
    "哥哥你指哪件事",
    "哥哥你说哪张图",
    "哥哥你说哪个表情",
    "刚刚只是顺口应了一声",
    "刚才只是顺口应了一声",
    "刚刚只是顺口应了一声啦没有讲正事",
    "刚刚只是顺口应了一声啦不是在解释什么",
    "刚才那句没接住",
    "刚才我回得太短",
    "不是突然换话题",
    "不是在敷衍",
    "你是在问前面",
    "是不是在问",
    "是不是想问",
    "想知道我刚才那个",
    "唔是什么意思",
    "唔是啥意思",
    "那个意思啦",
    "就是那个",
    "多想了一点",
    "会错意",
    "闹别扭",
    "故意拿来晃",
    "猫耳这张",
    "这张猫耳",
    "这张图里的猫猫",
    "这小猫耳朵",
    "我也觉得这张",
    "是不是觉得这张",
    "好像有点熟悉",
    "在哪儿见过",
    "在哪里见过",
    "见过似的",
    "这张图",
    "委屈成这样",
    "软乎乎的表情",
    "拿这个小表情",
    "怎么突然丢",
    "突然发这个给我看",
    "这张我看到了先不乱猜",
    "你慢慢说",
    "我在刚才那句",
)


@dataclass(frozen=True)
class FallbackRequest:
    reason_code: str = "fallback"
    primary_topic: str = ""
    response_form_reason: str = ""
    render_target_lang: str = "zh"
    user_message: str = ""
    observation: str = ""
    previous_assistant_text: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FallbackResult:
    text: str
    reason_code: str
    degraded: bool = True
    cacheable: bool = False
    source: str = "fallback_policy"


def compact_forbidden_text(text: str) -> str:
    return _COMPACT_RE.sub("", str(text or "")).strip()


def is_forbidden_surface_text(text: str) -> bool:
    compact = compact_forbidden_text(text)
    if not compact:
        return True
    if len(compact) <= 1:
        return True
    return any(marker in compact for marker in _FORBIDDEN_SURFACE_MARKERS)


def metadata_message_kind(metadata: Mapping[str, Any] | None) -> str:
    return str((metadata or {}).get("message_kind") or "").strip().lower()


def is_transport_ack_metadata(metadata: Mapping[str, Any] | None) -> bool:
    kind = metadata_message_kind(metadata)
    if kind == "transport_ack":
        return True
    source = str((metadata or {}).get("source") or (metadata or {}).get("reply_source") or "").strip().lower()
    return any(marker in source for marker in _HOST_SOURCE_MARKERS if marker != "fallback")


def is_degraded_reply_metadata(metadata: Mapping[str, Any] | None) -> bool:
    payload = dict(metadata or {})
    if bool(payload.get("fallback") or payload.get("degraded") or payload.get("reply_used_fallback")):
        return True
    model_id = str(payload.get("model_id") or "").strip().lower()
    provider_name = str(payload.get("provider_name") or "").strip().lower()
    source = str(payload.get("source") or payload.get("reply_source") or "").strip().lower()
    if provider_name == "host":
        return True
    if any(marker in model_id for marker in _HOST_MODEL_MARKERS):
        return True
    return any(marker in source for marker in _HOST_SOURCE_MARKERS)


def is_surface_cacheable_reply(
    *,
    text: str,
    metadata: Mapping[str, Any] | None = None,
    provider_name: str = "",
    model_id: str = "",
    source: str = "",
    fallback: bool = False,
) -> bool:
    if is_forbidden_surface_text(text):
        return False
    merged = dict(metadata or {})
    if provider_name:
        merged.setdefault("provider_name", provider_name)
    if model_id:
        merged.setdefault("model_id", model_id)
    if source:
        merged.setdefault("source", source)
    if fallback:
        merged.setdefault("fallback", True)
    if is_transport_ack_metadata(merged) or is_degraded_reply_metadata(merged):
        return False
    kind = metadata_message_kind(merged)
    return kind in {"", "final_reply"}


def build_fallback_reply(request: FallbackRequest) -> FallbackResult:
    """Return a small, explicitly degraded fallback.

    This is not a persona writer. It exists only when both the surface renderer
    and the full model path fail, so the text must stay neutral and non-cacheable.
    """
    lang = str(request.render_target_lang or "").strip().lower()
    topic = str(request.primary_topic or "").strip()
    reason = str(request.reason_code or "fallback").strip() or "fallback"
    if lang == "ja":
        if topic in {"emoji_social_reply", "emoji_social_followup"}:
            text = "画像は見えてるけど、今の返しはうまく接げなかった。"
        elif topic == "assistant_reply_followup":
            text = "さっきの返し、短すぎてうまく接げなかった。"
        else:
            text = "今の返し、うまく接げなかった。"
    elif topic in {"emoji_social_reply", "emoji_social_followup"}:
        text = "这下我没接稳，先别按刚才那句算。"
    elif topic == "assistant_reply_followup":
        text = "刚才那句太短了，这次没接稳。"
    elif topic in {"identity", "relationship_boundary", "motivation", "help_commitment"}:
        text = "这句我没接稳，先别按刚才那版算。"
    else:
        text = "这条我没接稳，先别按刚才那版算。"
    return FallbackResult(
        text=text,
        reason_code=reason,
        degraded=True,
        cacheable=False,
        source="fallback_policy",
    )


__all__ = [
    "FallbackRequest",
    "FallbackResult",
    "SURFACE_CACHE_POLICY_VERSION",
    "build_fallback_reply",
    "compact_forbidden_text",
    "is_degraded_reply_metadata",
    "is_forbidden_surface_text",
    "is_surface_cacheable_reply",
    "is_transport_ack_metadata",
    "metadata_message_kind",
]
