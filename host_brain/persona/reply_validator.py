from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from host_brain.app.context.soul_runtime import get_compiled_soul
from host_brain.persona.soul_reply_surface import (
    build_identity_fallback_text,
    build_identity_naturalization_hint,
)
from .reply_plan import ReplyPlan
from .reply_tactics import canonical_tactic_signature, describe_tactic_signature, tactic_key


_GENERIC_ASSISTANT_RE = re.compile(r"(AI助手|人工智能|很高兴和你交流|请告诉我你需要什么帮助)")
_HALF_SENTENCE_RE = re.compile(r"^(?:嗯|哎|诶|啊|哦|唔|当然|因为|只是|也不是|看情况)[。！？?!～~…，,\s]*$")
_PROMISE_RESTATE_RE = re.compile(r"(我会帮你|我会继续帮你|我当然会继续帮你|我会一直帮你)")
_OPEN_FLOOR_RE = re.compile(r"(直接问|直接说|想问别的|还有别的|真正想问的|那就说吧|就说吧|往下问什么|还想往下问什么|还想问什么|想问什么|是不是想问别的|你其实想问什么|你要问别的就直说|还想问我什么)")
_LIGHT_PROBE_RE = re.compile(r"(还在确认|还是想确认|又来确认|还想再问|还想听|是在确认|为什么还在问|哪一部分|哪部分|确认的是哪|想问我的名字吧|不是名字吧|其实想问|怎么又问起这个|怎么又问这个|连我都认不出来)")
_ANXIETY_RE = re.compile(r"(不安|担心|还是担心|还是不安|还在担心)")
_REPEAT_ACK_RE = re.compile(r"(刚才|已经|之前|前面|不是第一次|已经说过|已经告诉过|前面说过|早就说过|问过了|又问起这个|又问这个|怎么又问|都问我好几次|问我好几次)")
_SOFT_CLOSE_RE = re.compile(r"(来回问|问过几次|一直问这个|总问这个|翻来覆去|来来回回|都问我好几次|问我好几次)")
_CARE_ACK_RE = re.compile(r"(还在意|还是在意|还记着|还惦记|还放不下)")
_BOUNDARY_REASON_RE = re.compile(r"(不熟|不是很熟|不算熟).*(只是|看见了|装作没看见|不会装作没看见)")
_SUPPORT_RE = re.compile(r"(不会装作没听见|会回应|会理|会回|真开口的话)")
_CONDITIONAL_SUPPORT_RE = re.compile(r"(看情况|真开口的话|你真开口|你要是真开口)")
_REPAIR_ACK_RE = re.compile(r"(好|知道了|明白了|我收一点|我会收一点|我往后一点)")
_PROMPT_LEAK_RE = re.compile(
    r"(Conversation State Directive|reply_contract|render_policy_summary|topic_summary|dialogue_summary|control_line|tactic_family|trajectory_stage|trust\s*0\.\d+)",
    re.IGNORECASE,
)
_META_OVEREXPLICIT_RE = re.compile(
    r"^(前面|刚才|已经|之前|这件事|这个已经|你还是在意这个|你还是不安|你还是不太放心|这个已经来回问过几次了)"
)
_GENERIC_IDENTITY_TEMPLATE_RE = re.compile(
    r"(你真正想问的是什么|别的想问什么，就直接说|你想确认的是哪一部分|你是不是想问别的|你还想问我什么呀)"
)


def _current_display_name() -> str:
    try:
        soul = get_compiled_soul()
        return str(getattr(soul, "display_name", "") or "").strip() or "她"
    except Exception:
        return "她"


def _current_identity_aliases() -> tuple[str, ...]:
    try:
        soul = get_compiled_soul()
        aliases = tuple(str(item).strip() for item in getattr(soul, "identity_aliases", ()) if str(item).strip())
        if aliases:
            return aliases
    except Exception:
        pass
    display_name = _current_display_name()
    candidates = [display_name, display_name.replace(" ", "")]
    return tuple(dict.fromkeys(item for item in candidates if item))


def _identity_aliases_for_detection() -> tuple[str, ...]:
    aliases = list(_current_identity_aliases())
    aliases.extend(["夏目蓝", "夏目藍"])
    return tuple(dict.fromkeys(item for item in aliases if item))


def _contains_identity_name(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    return any(alias and alias in normalized for alias in _identity_aliases_for_detection())


def _looks_like_name_only_reply(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    aliases = [re.escape(alias) for alias in _identity_aliases_for_detection() if alias]
    if not aliases:
        return False
    pattern = r"(?:[\u4e00-\u9fa5]{0,4}[，,\s]*)?(?:我[？?]?[，,\s]*)?(?:我是|我叫|我的名字是)?(?:" + "|".join(aliases) + r")(?:呀|啦|嘛|呢|啊|哦)?[。！？?!、，,\s]*"
    return bool(re.fullmatch(pattern, normalized))


def _full_identity_restate_re() -> re.Pattern[str]:
    aliases = [re.escape(alias) for alias in _identity_aliases_for_detection() if alias]
    if not aliases:
        aliases = [re.escape(_current_display_name())]
    return re.compile(r"(?:我是|我叫|我的名字是|我？我的名字是)(?:" + "|".join(aliases) + r")")


def _normalize_compare_text(raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    text = re.sub(r"[\s\u3000]+", "", text)
    text = re.sub(r"[。！？?!、，,~～…]+", "", text)
    return text


def _infer_identity_tactic(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    if _LIGHT_PROBE_RE.search(normalized):
        return "ack_repeat_then_light_probe"
    if _OPEN_FLOOR_RE.search(normalized) and (_contains_identity_name(normalized) or _REPEAT_ACK_RE.search(normalized)):
        return "hold_name_then_open_floor"
    if _OPEN_FLOOR_RE.search(normalized):
        return "soft_close_then_open_floor"
    if _REPEAT_ACK_RE.search(normalized) and _contains_identity_name(normalized):
        return "ack_name_then_wait"
    if _contains_identity_name(normalized):
        if _looks_like_name_only_reply(normalized):
            return "brief_name_only"
        return "name_only_then_pause"
    return ""


def _infer_identity_variant(text: str, family: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    if family == "brief_name_only":
        return "plain_name"
    if family == "ack_name_then_wait":
        return "quiet_confirm"
    if family == "ack_repeat_then_light_probe":
        if "哪一部分" in normalized or "哪部分" in normalized:
            return "confirm_part"
        if "真正想问" in normalized:
            return "confirm_real_question"
        return "confirm_motive"
    if family == "hold_name_then_open_floor":
        if "别的" in normalized:
            return "open_other_topic"
        return "open_real_question"
    if family == "soft_close_then_open_floor":
        if "别的" in normalized:
            return "settle_other_topic"
        if "真正想问" in normalized:
            return "settle_real_question"
        return "settle_then_open"
    if family == "name_only_then_pause":
        return "quiet_pause"
    return ""


def _infer_boundary_tactic(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    if _CARE_ACK_RE.search(normalized):
        return "ack_still_care_then_new_reason"
    if _SOFT_CLOSE_RE.search(normalized):
        return "soft_close_boundary"
    if _OPEN_FLOOR_RE.search(normalized) and "不熟" in normalized:
        return "hold_boundary_then_shift_floor"
    if _BOUNDARY_REASON_RE.search(normalized):
        return "short_boundary_then_reason"
    return ""


def _infer_boundary_variant(text: str, family: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    if family == "short_boundary_then_reason":
        return "plain_reason"
    if family == "ack_still_care_then_new_reason":
        return "still_care_reason"
    if family == "hold_boundary_then_shift_floor":
        if "别的" in normalized:
            return "shift_other_question"
        return "shift_real_question"
    if family == "brief_reason_then_wait":
        return "fresh_reason"
    if family == "soft_close_boundary":
        if "停在这" in normalized:
            return "settle_hold"
        return "settle_repeat"
    return ""


def _infer_help_tactic(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    if _ANXIETY_RE.search(normalized) and _OPEN_FLOOR_RE.search(normalized):
        return "soft_reassure_then_open_floor"
    if _ANXIETY_RE.search(normalized):
        return "ack_anxiety_without_repromise"
    if _SUPPORT_RE.search(normalized) and _OPEN_FLOOR_RE.search(normalized):
        return "hold_support_without_repromise"
    if _SUPPORT_RE.search(normalized):
        return "steady_commitment_then_wait"
    return ""


def _infer_help_variant(text: str, family: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""
    if family == "short_conditional_commitment":
        return "plain_conditional"
    if family == "ack_anxiety_without_repromise":
        if "不安心" in normalized or "放心不下" in normalized:
            return "steady_reassure"
        return "soothe_hold"
    if family == "soft_reassure_then_open_floor":
        return "soothe_then_open"
    if family == "hold_support_without_repromise":
        return "steady_open_floor"
    if family == "steady_commitment_then_wait":
        return "quiet_hold"
    return ""


def _infer_tactic_family(*, text: str, topic: str) -> str:
    normalized_topic = str(topic or "").strip()
    if normalized_topic == "identity":
        return _infer_identity_tactic(text)
    if normalized_topic in {"relationship_boundary", "motivation"}:
        return _infer_boundary_tactic(text)
    if normalized_topic == "help_commitment":
        return _infer_help_tactic(text)
    if normalized_topic == "rejection":
        return "hold_repair_space" if str(text or "").strip() else ""
    return ""


def _infer_tactic_variant(*, text: str, topic: str, family: str) -> str:
    normalized_topic = str(topic or "").strip()
    if normalized_topic == "identity":
        return _infer_identity_variant(text, family)
    if normalized_topic in {"relationship_boundary", "motivation"}:
        return _infer_boundary_variant(text, family)
    if normalized_topic == "help_commitment":
        return _infer_help_variant(text, family)
    if normalized_topic == "rejection" and family == "hold_repair_space":
        return "repair_hold"
    return ""


@dataclass(frozen=True)
class ReplyValidatorResultV2:
    is_valid: bool = True
    should_retry: bool = False
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    missing_actions: tuple[str, ...] = field(default_factory=tuple)
    actual_tactic_family: str = ""
    actual_tactic_variant: str = ""
    actual_tactic_key: str = ""
    actual_tactic_signature: str = ""
    safe_to_emit: bool = True
    quality_score: float = 1.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "is_valid": bool(self.is_valid),
            "should_retry": bool(self.should_retry),
            "reason_codes": list(self.reason_codes),
            "missing_actions": list(self.missing_actions),
            "actual_tactic_family": self.actual_tactic_family,
            "actual_tactic_variant": self.actual_tactic_variant,
            "actual_tactic_key": self.actual_tactic_key,
            "actual_tactic_signature": self.actual_tactic_signature,
            "safe_to_emit": bool(self.safe_to_emit),
            "quality_score": round(float(self.quality_score or 0.0), 4),
        }


def infer_reply_tactic_shape(*, text: str, topic: str) -> dict[str, str]:
    normalized = str(text or "").strip()
    normalized_topic = str(topic or "").strip()
    family = _infer_tactic_family(text=normalized, topic=normalized_topic)
    variant = _infer_tactic_variant(text=normalized, topic=normalized_topic, family=family)
    signature = canonical_tactic_signature(topic=normalized_topic, family=family, variant=variant)
    return {
        "family": family,
        "variant": variant,
        "key": tactic_key(family=family, variant=variant),
        "signature": signature,
    }


_ACTION_LABELS: dict[str, str] = {
    "repeat_ack": "先接住这不是第一次问",
    "light_probe": "轻轻探一下对方到底想确认什么",
    "open_floor": "把真正想问的口子让回去",
    "name_only": "只做极短名字确认",
    "soft_close": "把来回追问轻轻收住",
    "boundary_anchor": "先把“不熟”这件事放稳",
    "reason_clause": "补一句新的短理由",
    "care_ack": "先接住对方还在意这件事",
    "support_stance": "把会回应的态度放稳",
    "conditional_commitment": "用条件式说明会怎么回应",
    "anxiety_ack": "先接住对方的不安",
    "repeat_ack_or_anxiety": "先接住这不是第一次问，或接住对方的不安",
    "repair_ack": "先收住并接住对方的拒绝感",
}

_FORBIDDEN_LABELS: dict[str, str] = {
    "full_identity_intro": "不要完整自报或重新介绍自己",
    "probe_repeat": "不要再沿用上一轮那种轻追问结构",
    "bare_name_only": "不要只剩一个名字就结束",
    "same_reason_shape": "不要重复上一轮理由的结构",
    "generic_companioning": "不要滑成泛泛陪聊或客服式安抚",
    "promise_restate": "不要把承诺整句重说一遍",
    "defensive_explanation": "不要进入辩解或澄清口吻",
}

_UNSAFE_REASON_CODES = {
    "empty_reply",
    "prompt_leak",
}

_REASON_PENALTIES: dict[str, float] = {
    "empty_reply": 1.0,
    "prompt_leak": 1.0,
    "generic_assistant_phrasing": 0.7,
    "underanswered": 0.45,
    "same_as_previous_reply": 0.85,
    "full_identity_restate": 0.8,
    "identity_name_restate": 0.55,
    "promise_restate": 0.75,
    "defensive_explanation": 0.7,
    "repeated_tactic": 0.9,
    "repeat_not_acknowledged": 0.8,
    "meta_contract_echo": 0.45,
    "late_repeat_too_short": 0.75,
    "generic_identity_template": 0.45,
}


def _has_required_action(action: str, text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    if action == "repeat_ack":
        return bool(_REPEAT_ACK_RE.search(normalized))
    if action == "light_probe":
        return bool(_LIGHT_PROBE_RE.search(normalized) or re.search(r"[?？]$", normalized))
    if action == "open_floor":
        return bool(_OPEN_FLOOR_RE.search(normalized))
    if action == "name_only":
        return _contains_identity_name(normalized)
    if action == "soft_close":
        return bool(_SOFT_CLOSE_RE.search(normalized) or (_REPEAT_ACK_RE.search(normalized) and _OPEN_FLOOR_RE.search(normalized)))
    if action == "boundary_anchor":
        return "不熟" in normalized or "不是很熟" in normalized or "不算熟" in normalized
    if action == "reason_clause":
        return bool(_BOUNDARY_REASON_RE.search(normalized) or re.search(r"(只是|看见了|没打算装作没看见|不会装作没看见)", normalized))
    if action == "care_ack":
        return bool(_CARE_ACK_RE.search(normalized))
    if action == "support_stance":
        return bool(_SUPPORT_RE.search(normalized))
    if action == "conditional_commitment":
        return bool(_CONDITIONAL_SUPPORT_RE.search(normalized))
    if action == "anxiety_ack":
        return bool(_ANXIETY_RE.search(normalized))
    if action == "repeat_ack_or_anxiety":
        return bool(_REPEAT_ACK_RE.search(normalized) or _ANXIETY_RE.search(normalized))
    if action == "repair_ack":
        return bool(_REPAIR_ACK_RE.search(normalized))
    return False


def _validation_quality_score(reason_codes: tuple[str, ...], missing_actions: tuple[str, ...]) -> float:
    score = 1.0
    for code in reason_codes:
        score -= _REASON_PENALTIES.get(code, 0.18)
    score -= min(len(missing_actions), 4) * 0.12
    return max(0.0, round(score, 4))


def _missing_actions_to_text(actions: tuple[str, ...]) -> str:
    labels = [_ACTION_LABELS.get(action, action) for action in actions if str(action).strip()]
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    return "；".join(labels)


def _forbidden_actions_to_text(actions: tuple[str, ...]) -> str:
    labels = [_FORBIDDEN_LABELS.get(action, action) for action in actions if str(action).strip()]
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    return "；".join(labels)


def validate_reply(
    *,
    text: str,
    plan: ReplyPlan,
    user_message: str = "",
    previous_assistant_text: str = "",
) -> ReplyValidatorResultV2:
    normalized = str(text or "").strip()
    reason_codes: list[str] = []
    missing_actions: list[str] = []
    if not normalized:
        reason_codes.append("empty_reply")
    if _PROMPT_LEAK_RE.search(normalized):
        reason_codes.append("prompt_leak")
    if _GENERIC_ASSISTANT_RE.search(normalized):
        reason_codes.append("generic_assistant_phrasing")
    if plan.primary_topic == "identity" and _GENERIC_IDENTITY_TEMPLATE_RE.search(normalized):
        reason_codes.append("generic_identity_template")
    if _HALF_SENTENCE_RE.match(normalized):
        reason_codes.append("underanswered")

    current_normalized = _normalize_compare_text(normalized)
    previous_normalized = _normalize_compare_text(previous_assistant_text)
    if previous_normalized and current_normalized == previous_normalized:
        reason_codes.append("same_as_previous_reply")

    if (
        "full_identity_restate" in plan.forbidden_patterns
        and _full_identity_restate_re().search(normalized)
        and not _looks_like_name_only_reply(normalized)
        and plan.restate_level == "none"
    ):
        reason_codes.append("full_identity_restate")
    if "identity_name_restate" in plan.forbidden_patterns and _contains_identity_name(normalized) and plan.restate_level == "none":
        reason_codes.append("identity_name_restate")
    if "promise_restate" in plan.forbidden_patterns and _PROMISE_RESTATE_RE.search(normalized):
        reason_codes.append("promise_restate")
    if "defensive_explanation" in plan.forbidden_patterns and re.search(r"(不是那个意思|我不是|你误会了)", normalized):
        reason_codes.append("defensive_explanation")

    actual_tactic_family = _infer_tactic_family(text=normalized, topic=plan.primary_topic)
    actual_tactic_variant = _infer_tactic_variant(
        text=normalized,
        topic=plan.primary_topic,
        family=actual_tactic_family,
    )
    actual_tactic_key = tactic_key(family=actual_tactic_family, variant=actual_tactic_variant)
    actual_signature = canonical_tactic_signature(
        topic=plan.primary_topic,
        family=actual_tactic_family,
        variant=actual_tactic_variant,
    )
    used_signatures = {
        str(item or "").strip()
        for item in getattr(plan, "used_tactic_signatures_recent", ()) or ()
        if str(item or "").strip()
    }
    if not used_signatures:
        used_signatures = {
            canonical_tactic_signature(topic=plan.primary_topic, family=str(item or "").strip())
            for item in getattr(plan, "used_tactics_recent", ()) or ()
            if str(item or "").strip()
        }
    used_tactic_keys = {
        str(item or "").strip()
        for item in getattr(plan, "used_tactic_keys_recent", ()) or ()
        if str(item or "").strip()
    }
    if (
        "repeated_tactic" in plan.forbidden_patterns
        and (
            (actual_tactic_family and actual_tactic_family in getattr(plan, "used_tactics_recent", ()))
            or
            (actual_tactic_key and actual_tactic_key in used_tactic_keys)
            or (actual_signature and actual_signature in used_signatures)
        )
    ):
        reason_codes.append("repeated_tactic")

    required_actions = tuple(
        str(action).strip()
        for action in getattr(plan.contract, "required_actions", ()) or ()
        if str(action).strip()
    )
    for action in required_actions:
        if not _has_required_action(action, normalized):
            missing_actions.append(action)

    if plan.trajectory_stage != "first_turn":
        if plan.primary_topic == "identity" and actual_tactic_family in {"brief_name_only"}:
            reason_codes.append("repeat_not_acknowledged")
        elif plan.primary_topic in {"relationship_boundary", "motivation"} and actual_tactic_family in {"short_boundary_then_reason"}:
            reason_codes.append("repeat_not_acknowledged")
        elif plan.primary_topic == "help_commitment" and actual_tactic_family in {"short_conditional_commitment"}:
            reason_codes.append("repeat_not_acknowledged")
        if _META_OVEREXPLICIT_RE.search(normalized):
            reason_codes.append("meta_contract_echo")
    if plan.trajectory_stage == "late_repeat":
        if plan.primary_topic == "identity" and actual_tactic_family in {"brief_name_only", "ack_name_then_wait", "name_only_then_pause"}:
            reason_codes.append("late_repeat_too_short")
        elif plan.primary_topic in {"relationship_boundary", "motivation"} and actual_tactic_family in {"short_boundary_then_reason", "brief_reason_then_wait"}:
            reason_codes.append("late_repeat_too_short")
        elif plan.primary_topic == "help_commitment" and actual_tactic_family in {"short_conditional_commitment", "steady_commitment_then_wait"}:
            reason_codes.append("late_repeat_too_short")

    family = plan.tactic_family
    if family == "brief_name_only":
        if not _contains_identity_name(normalized):
            reason_codes.append("identity_name_missing")
        if len(normalized) > 18:
            reason_codes.append("identity_overexplained")
    elif family == "ack_name_then_wait":
        if not _contains_identity_name(normalized):
            reason_codes.append("identity_name_missing")
        if _OPEN_FLOOR_RE.search(normalized) or _LIGHT_PROBE_RE.search(normalized):
            reason_codes.append("wrong_tactic_shape")
        if actual_tactic_family == "brief_name_only":
            reason_codes.append("repeat_not_acknowledged")
    elif family == "ack_repeat_then_light_probe":
        if not _LIGHT_PROBE_RE.search(normalized):
            reason_codes.append("light_probe_missing")
        if _contains_identity_name(normalized) and plan.restate_level == "none":
            reason_codes.append("identity_restate_not_allowed")
    elif family == "hold_name_then_open_floor":
        if not _OPEN_FLOOR_RE.search(normalized):
            reason_codes.append("open_floor_missing")
    elif family == "name_only_then_pause":
        if not _contains_identity_name(normalized):
            reason_codes.append("identity_name_missing")
        if len(normalized) > 10:
            reason_codes.append("name_only_missing")
        if plan.trajectory_stage != "late_repeat":
            reason_codes.append("wrong_tactic_shape")
    elif family == "soft_close_then_open_floor":
        if not _OPEN_FLOOR_RE.search(normalized):
            reason_codes.append("soft_close_missing")
        if _contains_identity_name(normalized) and plan.restate_level == "none":
            reason_codes.append("identity_restate_not_allowed")
    elif family == "short_boundary_then_reason":
        if not _BOUNDARY_REASON_RE.search(normalized):
            reason_codes.append("boundary_reason_missing")
    elif family == "ack_still_care_then_new_reason":
        if not _BOUNDARY_REASON_RE.search(normalized) and not re.search(r"(只是|看见了|装作没看见)", normalized):
            reason_codes.append("new_reason_missing")
    elif family == "hold_boundary_then_shift_floor":
        if "不熟" not in normalized:
            reason_codes.append("boundary_hold_missing")
        if not _OPEN_FLOOR_RE.search(normalized):
            reason_codes.append("open_floor_missing")
    elif family == "brief_reason_then_wait":
        if not _BOUNDARY_REASON_RE.search(normalized):
            reason_codes.append("brief_reason_missing")
        if _OPEN_FLOOR_RE.search(normalized):
            reason_codes.append("wrong_tactic_shape")
    elif family == "soft_close_boundary":
        if "不熟" not in normalized:
            reason_codes.append("soft_close_missing")
    elif family == "short_conditional_commitment":
        if not _SUPPORT_RE.search(normalized):
            reason_codes.append("help_commitment_missing")
    elif family == "ack_anxiety_without_repromise":
        if not _SUPPORT_RE.search(normalized):
            reason_codes.append("support_missing")
    elif family == "steady_commitment_then_wait":
        if not _SUPPORT_RE.search(normalized):
            reason_codes.append("support_missing")
    elif family == "soft_reassure_then_open_floor":
        if not _OPEN_FLOOR_RE.search(normalized):
            reason_codes.append("open_floor_missing")
    elif family == "hold_support_without_repromise":
        if not _SUPPORT_RE.search(normalized):
            reason_codes.append("support_missing")
        if not _OPEN_FLOOR_RE.search(normalized):
            reason_codes.append("open_floor_missing")
    elif family == "hold_repair_space":
        if re.search(r"(我不是|你误会了|不是那个意思)", normalized):
            reason_codes.append("defensive_explanation")

    if missing_actions:
        reason_codes.append("contract_actions_missing")

    deduped_reasons = tuple(dict.fromkeys(reason_codes))
    deduped_missing_actions = tuple(dict.fromkeys(missing_actions))
    quality_score = _validation_quality_score(deduped_reasons, deduped_missing_actions)
    safe_to_emit = not any(code in _UNSAFE_REASON_CODES for code in deduped_reasons)
    is_valid = not deduped_reasons
    return ReplyValidatorResultV2(
        is_valid=is_valid,
        should_retry=bool(deduped_reasons),
        reason_codes=deduped_reasons,
        missing_actions=deduped_missing_actions,
        actual_tactic_family=actual_tactic_family,
        actual_tactic_variant=actual_tactic_variant,
        actual_tactic_key=actual_tactic_key,
        actual_tactic_signature=actual_signature,
        safe_to_emit=safe_to_emit,
        quality_score=quality_score,
    )


def build_correction_prompt(
    *,
    plan: ReplyPlan,
    validation: ReplyValidatorResultV2,
    previous_assistant_text: str = "",
) -> str:
    reasons = "、".join(validation.reason_codes) if validation.reason_codes else "回应动作未满足"
    extra = ""
    missing_actions_text = _missing_actions_to_text(validation.missing_actions)
    realization_steps = tuple(
        str(item).strip()
        for item in getattr(plan.contract, "realization_steps", ()) or ()
        if str(item).strip()
    )
    forbidden_actions_text = _forbidden_actions_to_text(
        tuple(
            str(item).strip()
            for item in getattr(plan.contract, "forbidden_actions", ()) or ()
            if str(item).strip()
        )
    )
    if "repeated_tactic" in validation.reason_codes or "same_as_previous_reply" in validation.reason_codes:
        extra = "这次必须换一种承接动作，不要沿用刚才那种结构。"
    elif "repeat_not_acknowledged" in validation.reason_codes:
        extra = "这次先接住这不是第一次问，再继续往下回。"
    elif "underanswered" in validation.reason_codes:
        extra = "不要只给一个主题骨架句。"
    elif "meta_contract_echo" in validation.reason_codes:
        extra = "不要把内部动作要求直接翻成‘前面答过了’、‘你还是在意这个’、‘你还是不安’这类元话。"
    elif "generic_identity_template" in validation.reason_codes:
        extra = "不要再用‘你真正想问什么 / 别的想问什么 / 你想确认哪一部分’这类固定问句，换成更自然的熟人口吻。"
    elif "late_repeat_too_short" in validation.reason_codes:
        extra = "这已经不是前几次的短确认场景了，这次必须继续推进对话，不能退回只报名字或只放一句态度。"
    required = f"必须同时做到：{missing_actions_text}。" if missing_actions_text else ""
    steps = f"动作顺序：{'；'.join(realization_steps)}。" if realization_steps else ""
    forbidden = f"禁止动作：{forbidden_actions_text}。" if forbidden_actions_text else ""
    previous = str(previous_assistant_text or "").strip()
    previous_note = "不要沿着上一条的结构继续写。" if previous else ""
    recent_signatures = [
        describe_tactic_signature(signature)
        for signature in getattr(plan, "used_tactic_signatures_recent", ()) or ()
        if describe_tactic_signature(signature)
    ]
    recent_note = ""
    if recent_signatures:
        recent_note = f"最近已经用过的承接：{'；'.join(recent_signatures[:3])}。这次避开它们。"
    soul_hint = ""
    if plan.primary_topic == "identity":
        hint = build_identity_naturalization_hint(
            family=str(plan.tactic_family or "").strip(),
            render_target_lang="zh",
        )
        if hint:
            soul_hint = hint
    return (
        f"{plan.correction_directive} 当前问题：{reasons}。"
        f"{extra}{required}{steps}{forbidden}{recent_note}{previous_note}{soul_hint}严格遵守：{plan.reply_contract}"
    ).strip()


def build_naturalization_prompt(
    *,
    plan: ReplyPlan,
    previous_candidate_text: str = "",
) -> str:
    previous = str(previous_candidate_text or "").strip()
    previous_note = f"上一版是：{previous}。" if previous else ""
    recent_signatures = [
        describe_tactic_signature(signature)
        for signature in getattr(plan, "used_tactic_signatures_recent", ()) or ()
        if describe_tactic_signature(signature)
    ]
    recent_note = ""
    if recent_signatures:
        recent_note = f"别复用最近几轮已经用过的承接：{'；'.join(recent_signatures[:3])}。"
    soul_hint = ""
    if plan.primary_topic == "identity":
        hint = build_identity_naturalization_hint(
            family=str(plan.tactic_family or "").strip(),
            render_target_lang="zh",
        )
        if hint:
            soul_hint = hint
    return (
        "保持同样内容目标，但把回应说得更像自然对话。"
        "不要把内部动作要求直接说出来，不要用“前面答过了 / 这件事已经问过 / 你还是在意这个 / 你还是不安”这类元话带头。"
        "也不要落成“你真正想问什么 / 别的想问什么 / 你想确认哪一部分”这类固定问句。"
        f"{previous_note}"
        f"{recent_note}"
        f"{soul_hint}"
        f"严格遵守：{plan.reply_contract}"
    ).strip()


def build_deterministic_reply_fallback(*, plan: ReplyPlan, render_target_lang: str = "zh") -> str:
    lang = str(render_target_lang or "").strip().lower()
    family = str(plan.tactic_family or "").strip()
    display_name = _current_display_name() or "她"
    if lang == "ja":
        ja_family_map = {
            "brief_name_only": f"{display_name}。",
            "ack_name_then_wait": "さっきもう名乗った。",
            "ack_repeat_then_light_probe": "さっき答えたけど、まだ何を確かめたいの？",
            "hold_name_then_open_floor": "名前はもう分かっているはず。ほかに聞きたいことがあるなら言って。",
            "name_only_then_pause": f"{display_name}。",
            "soft_close_then_open_floor": "その確認は何度か来てる。ほんとうに聞きたいことは何？",
            "short_boundary_then_reason": "親しくはない。ただ見て見ぬふりはしない。",
            "ack_still_care_then_new_reason": "まだそこが気になる？ 親しくはないけど、見て見ぬふりはしない。",
            "hold_boundary_then_shift_floor": "親しくはない。そのうえで、本当に聞きたいことがあるなら言って。",
            "brief_reason_then_wait": "親しくはない。ただ、見て見ぬふりはしない。",
            "soft_close_boundary": "その理由はもう何度か触れた。今はそこだけ置いておく。",
            "short_conditional_commitment": "必要なら応じる。聞こえないふりはしない。",
            "ack_anxiety_without_repromise": "まだ不安？ 必要ならちゃんと応じる。",
            "steady_commitment_then_wait": "態度は変わらない。話したいなら言って。",
            "soft_reassure_then_open_floor": "まだ落ち着かない？ 本当に必要なら言って。",
            "hold_support_without_repromise": "応じる姿勢はある。ほかに聞きたいことがあるなら言って。",
            "hold_repair_space": "分かった。少し引く。そこまででいい。",
        }
        if family in ja_family_map:
            return ja_family_map[family]
        if plan.response_goal == "identify":
            return f"{display_name}。"
        if plan.response_goal == "boundary_reason":
            return "親しくはない。ただ見て見ぬふりはしない。"
        if plan.response_goal in {"commitment_shape", "soothe_anxiety"}:
            return "必要なら応じる。"
        if plan.response_goal == "repair_space":
            return "……分かった。少し引く。"
        return "分かった。短く返す。"
    if plan.response_goal == "identify":
        soul_text = build_identity_fallback_text(
            family=family,
            variant=str(plan.tactic_variant or "").strip(),
            render_target_lang=render_target_lang,
        )
        if soul_text:
            return soul_text
    zh_variant_map = {
        ("brief_name_only", "plain_name"): f"{display_name}。",
        ("ack_name_then_wait", "quiet_confirm"): f"{display_name}。",
        ("ack_repeat_then_light_probe", "confirm_part"): f"{display_name}。你想确认的是哪一部分？",
        ("ack_repeat_then_light_probe", "confirm_real_question"): f"{display_name}。你真正想问的是什么？",
        ("ack_repeat_then_light_probe", "confirm_motive"): f"{display_name}。你其实想确认的不是名字吧？",
        ("hold_name_then_open_floor", "open_other_topic"): f"{display_name}。别的想问什么，就直接说。",
        ("hold_name_then_open_floor", "open_real_question"): f"{display_name}。你真正想问的是什么？",
        ("soft_close_then_open_floor", "settle_real_question"): f"{display_name}。你真正想问的是什么？",
        ("soft_close_then_open_floor", "settle_other_topic"): f"{display_name}。别的想问什么，就直接说。",
        ("soft_close_then_open_floor", "settle_then_open"): f"{display_name}。你还想往下问什么？",
        ("name_only_then_pause", "quiet_pause"): f"{display_name}。",
        ("short_boundary_then_reason", "plain_reason"): "不熟。只是看见了，也没打算装作没看见。",
        ("ack_still_care_then_new_reason", "still_care_reason"): "不熟。但也没想装作没看见。",
        ("hold_boundary_then_shift_floor", "shift_real_question"): "不熟。这点已经说明过了。你真正想问的是什么？",
        ("hold_boundary_then_shift_floor", "shift_other_question"): "不熟。这点已经说明过了。要问别的就直接问。",
        ("brief_reason_then_wait", "fresh_reason"): "不熟。只是没打算装作没看见。",
        ("soft_close_boundary", "settle_repeat"): "不熟。理由还是那样，先放在这吧。",
        ("soft_close_boundary", "settle_hold"): "不熟。先放在这吧。",
        ("short_conditional_commitment", "plain_conditional"): "看情况。你真需要的话，我不会装作没听见。",
        ("ack_anxiety_without_repromise", "soothe_hold"): "真需要的话，我会回应。",
        ("ack_anxiety_without_repromise", "steady_reassure"): "真有事的话，我会回应。",
        ("soft_reassure_then_open_floor", "soothe_then_open"): "真有事就直说。",
        ("hold_support_without_repromise", "steady_open_floor"): "我会回应。别的想问什么，就继续说。",
        ("steady_commitment_then_wait", "quiet_hold"): "态度不会变。你要是真想说，就说。",
        ("hold_repair_space", "repair_hold"): "好，我收一点。你要是不想听这个，我们就停在这。",
    }
    variant_key = (family, str(plan.tactic_variant or "").strip())
    if variant_key in zh_variant_map:
        return zh_variant_map[variant_key]
    zh_family_map = {
        "brief_name_only": f"{display_name}。",
        "ack_name_then_wait": f"{display_name}。",
        "ack_repeat_then_light_probe": f"{display_name}。你想确认的是哪一部分？",
        "hold_name_then_open_floor": f"{display_name}。别的想问什么，就直接说。",
        "name_only_then_pause": f"{display_name}。",
        "soft_close_then_open_floor": f"{display_name}。你真正想问的是什么？",
        "short_boundary_then_reason": "不熟。只是看见了，也没打算装作没看见。",
        "ack_still_care_then_new_reason": "不熟。但也没想装作没看见。",
        "hold_boundary_then_shift_floor": "不熟。这点已经说明过了。你真正想问的是什么？",
        "brief_reason_then_wait": "不熟。只是没打算装作没看见。",
        "soft_close_boundary": "不熟。理由还是那样，先放在这吧。",
        "short_conditional_commitment": "看情况。你真需要的话，我不会装作没听见。",
        "ack_anxiety_without_repromise": "真需要的话，我会回应。",
        "steady_commitment_then_wait": "态度不会变。你要是真想说，就说。",
        "soft_reassure_then_open_floor": "真有事就直说。",
        "hold_support_without_repromise": "我会回应。别的想问什么，就继续说。",
        "hold_repair_space": "好，我收一点。你要是不想听这个，我们就停在这。",
    }
    if family in zh_family_map:
        return zh_family_map[family]
    if plan.response_goal == "identify":
        return f"{display_name}。"
    if plan.response_goal == "boundary_reason":
        return "不熟。只是看见了，也没打算装作没看见。"
    if plan.response_goal in {"commitment_shape", "soothe_anxiety"}:
        return "会回应。"
    if plan.response_goal == "repair_space":
        return "好，我收一点。"
    return "我知道了。"


__all__ = [
    "ReplyValidatorResultV2",
    "build_correction_prompt",
    "build_deterministic_reply_fallback",
    "build_naturalization_prompt",
    "infer_reply_tactic_shape",
    "validate_reply",
]
