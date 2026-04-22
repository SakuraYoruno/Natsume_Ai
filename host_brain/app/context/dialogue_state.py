from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Mapping


_IDENTITY_SHORT_RE = re.compile(r"^[、，,\s]*(?:你是(?:谁)?|你谁|你是谁)[?？!！。…~]*$")
_BOUNDARY_RE = re.compile(r"(我们.*不熟|不熟吧|很熟吗|我们很熟吗|我们熟吗)")
_MOTIVATION_RE = re.compile(r"(为什么要帮我|为什么帮我|为什么要管我|为什么管我)")
_HELP_CONTINUITY_RE = re.compile(r"(还会帮我吗|还会理我吗|还会管我吗)")
_REJECTION_RE = re.compile(r"(我不喜欢这样|我不喜欢你这样|别这么说|别这样说|别这么热心|别这样)")
_CHALLENGING_MARKER_RE = re.compile(r"(吧|吗|？|\?|就|还|又|不是|没)")

_SELF_IDENTIFY_RE = re.compile(r"(我是夏目蓝|我叫夏目蓝|我的名字是夏目蓝|我？我的名字是夏目蓝)")
_BOUNDARY_ANSWER_RE = re.compile(r"(我们并?不熟|我们不熟|不熟)")
_MOTIVATION_ANSWER_RE = re.compile(r"(我只是觉得|因为你问了|看见了，也没有装作没看见|装作没看见|既然你遇到了麻烦)")
_HELP_COMMITMENT_ANSWER_RE = re.compile(r"(会帮|会理|会回|会答|不会不理|多半不会不理)")
_GENERIC_ASSISTIVE_RE = re.compile(r"(有什么事吗|有什么事|需要什么帮助|需要我|我可以帮)")


@dataclass(frozen=True)
class DialogueStateBlock:
    last_user_act: str = ""
    last_assistant_act: str = ""
    current_user_act: str = ""
    answered_topics_recent: tuple[str, ...] = ()
    repeat_question_count: int = 0
    repeat_surface_group: str = ""
    recent_identity_answered: bool = False
    recent_boundary_answered: bool = False
    user_stance: str = "neutral"
    dialogue_state_reason_tags: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "last_user_act": self.last_user_act,
            "last_assistant_act": self.last_assistant_act,
            "current_user_act": self.current_user_act,
            "answered_topics_recent": list(self.answered_topics_recent),
            "repeat_question_count": int(self.repeat_question_count or 0),
            "repeat_surface_group": self.repeat_surface_group,
            "recent_identity_answered": bool(self.recent_identity_answered),
            "recent_boundary_answered": bool(self.recent_boundary_answered),
            "user_stance": self.user_stance,
            "dialogue_state_reason_tags": list(self.dialogue_state_reason_tags),
        }


def _normalize_text(raw: Any) -> str:
    return str(raw or "").strip()


def classify_user_act(message: str) -> str:
    text = _normalize_text(message)
    if not text:
        return ""
    if _REJECTION_RE.search(text):
        return "rejection"
    if _HELP_CONTINUITY_RE.search(text):
        return "help_continuity_probe"
    if _MOTIVATION_RE.search(text):
        return "motivation_probe"
    if _BOUNDARY_RE.search(text):
        return "boundary_probe"
    if _IDENTITY_SHORT_RE.match(text):
        return "identity_probe"
    if len(text) <= 8:
        return "neutral_short_query"
    return ""


def extract_answered_topics(message: str) -> set[str]:
    text = _normalize_text(message)
    topics: set[str] = set()
    if not text:
        return topics
    if _SELF_IDENTIFY_RE.search(text):
        topics.add("identity")
    if _BOUNDARY_ANSWER_RE.search(text):
        topics.add("relationship_boundary")
    if _MOTIVATION_ANSWER_RE.search(text):
        topics.add("motivation")
    if _HELP_COMMITMENT_ANSWER_RE.search(text):
        topics.add("help_commitment")
    return topics


def classify_assistant_act(message: str) -> str:
    text = _normalize_text(message)
    if not text:
        return ""
    if _SELF_IDENTIFY_RE.search(text):
        return "self_identify"
    if _BOUNDARY_ANSWER_RE.search(text):
        return "boundary_answer"
    if _MOTIVATION_ANSWER_RE.search(text):
        return "motivation_answer"
    if _HELP_COMMITMENT_ANSWER_RE.search(text):
        return "help_commitment_answer"
    if _GENERIC_ASSISTIVE_RE.search(text):
        return "generic_assistive_answer"
    return ""


def normalize_repeat_surface_group(message: str, current_user_act: str) -> str:
    text = _normalize_text(message)
    if current_user_act == "identity_probe" and _IDENTITY_SHORT_RE.match(text):
        return "identity_short"
    if current_user_act in {"boundary_probe", "motivation_probe"}:
        return "relationship_motivation"
    if current_user_act == "help_continuity_probe":
        return "help_continuity"
    return ""


def _turn_role(turn: Any) -> str:
    if isinstance(turn, Mapping):
        return _normalize_text(turn.get("role")).lower()
    return ""


def _turn_content(turn: Any) -> str:
    if isinstance(turn, Mapping):
        return _normalize_text(turn.get("content"))
    return ""


def _turn_user_group(turn: Any) -> str:
    if _turn_role(turn) != "user":
        return ""
    return normalize_repeat_surface_group(_turn_content(turn), classify_user_act(_turn_content(turn)))


def _resolve_user_stance(
    *,
    user_message: str,
    current_user_act: str,
    repeat_question_count: int,
) -> str:
    text = _normalize_text(user_message)
    if current_user_act == "rejection":
        return "rejecting"
    if current_user_act in {"identity_probe", "boundary_probe", "motivation_probe", "help_continuity_probe"}:
        if repeat_question_count > 0 and _CHALLENGING_MARKER_RE.search(text):
            return "challenging"
        return "probing"
    return "neutral"


def analyze_dialogue_state(
    *,
    recent_turns: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...] | None,
    user_message: str,
) -> DialogueStateBlock:
    turns = [dict(turn) for turn in list(recent_turns or []) if isinstance(turn, Mapping)][-6:]
    current_user_act = classify_user_act(user_message)
    repeat_surface_group = normalize_repeat_surface_group(user_message, current_user_act)

    last_user_act = ""
    last_assistant_act = ""
    answered_topics_recent: set[str] = set()
    repeat_question_count = 0
    recent_assistant_topics: list[set[str]] = []
    reason_tags: list[str] = []

    for turn in turns:
        role = _turn_role(turn)
        content = _turn_content(turn)
        if role == "user":
            act = classify_user_act(content)
            if act:
                last_user_act = act
            if repeat_surface_group and _turn_user_group(turn) == repeat_surface_group:
                repeat_question_count += 1
        elif role == "assistant":
            act = classify_assistant_act(content)
            if act:
                last_assistant_act = act
            topics = extract_answered_topics(content)
            if topics:
                answered_topics_recent.update(topics)
                recent_assistant_topics.append(topics)

    recent_assistant_topics = recent_assistant_topics[-2:]
    recent_identity_answered = any("identity" in topics for topics in recent_assistant_topics)
    recent_boundary_answered = any(
        "relationship_boundary" in topics or "motivation" in topics
        for topics in recent_assistant_topics
    )
    user_stance = _resolve_user_stance(
        user_message=user_message,
        current_user_act=current_user_act,
        repeat_question_count=repeat_question_count,
    )

    if current_user_act:
        reason_tags.append(f"current_user_act:{current_user_act}")
    if last_user_act:
        reason_tags.append(f"last_user_act:{last_user_act}")
    if last_assistant_act:
        reason_tags.append(f"last_assistant_act:{last_assistant_act}")
    if repeat_surface_group:
        reason_tags.append(f"repeat_surface:{repeat_surface_group}")
    if repeat_question_count > 0:
        reason_tags.append(f"repeat_count:{repeat_question_count}")
    if recent_identity_answered:
        reason_tags.append("recent_identity_answered")
    if recent_boundary_answered:
        reason_tags.append("recent_boundary_answered")
    if user_stance and user_stance != "neutral":
        reason_tags.append(f"user_stance:{user_stance}")

    return DialogueStateBlock(
        last_user_act=last_user_act,
        last_assistant_act=last_assistant_act,
        current_user_act=current_user_act,
        answered_topics_recent=tuple(sorted(answered_topics_recent)),
        repeat_question_count=repeat_question_count,
        repeat_surface_group=repeat_surface_group,
        recent_identity_answered=recent_identity_answered,
        recent_boundary_answered=recent_boundary_answered,
        user_stance=user_stance,
        dialogue_state_reason_tags=tuple(reason_tags),
    )


def coerce_dialogue_state_block(raw: Mapping[str, Any] | None) -> DialogueStateBlock:
    payload = dict(raw or {}) if isinstance(raw, Mapping) else {}
    return DialogueStateBlock(
        last_user_act=_normalize_text(payload.get("last_user_act")),
        last_assistant_act=_normalize_text(payload.get("last_assistant_act")),
        current_user_act=_normalize_text(payload.get("current_user_act")),
        answered_topics_recent=tuple(
            str(item).strip()
            for item in list(payload.get("answered_topics_recent") or [])
            if str(item).strip()
        ),
        repeat_question_count=int(payload.get("repeat_question_count") or 0),
        repeat_surface_group=_normalize_text(payload.get("repeat_surface_group")),
        recent_identity_answered=bool(payload.get("recent_identity_answered") or False),
        recent_boundary_answered=bool(payload.get("recent_boundary_answered") or False),
        user_stance=_normalize_text(payload.get("user_stance")) or "neutral",
        dialogue_state_reason_tags=tuple(
            str(item).strip()
            for item in list(payload.get("dialogue_state_reason_tags") or [])
            if str(item).strip()
        ),
    )


__all__ = [
    "DialogueStateBlock",
    "classify_assistant_act",
    "classify_user_act",
    "extract_answered_topics",
    "normalize_repeat_surface_group",
    "analyze_dialogue_state",
    "coerce_dialogue_state_block",
]
