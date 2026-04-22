from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Mapping

from .topic_ledger import TopicLedgerBlock


_IDENTITY_SHORT_RE = re.compile(r"^[、，,\s]*(?:你是(?:谁)?|你谁|你是谁)[?？!！。…~]*$")
_BOUNDARY_RE = re.compile(r"(我们.*不熟|不熟吧|很熟吗|我们很熟吗|我们熟吗)")
_MOTIVATION_RE = re.compile(r"(为什么要帮我|为什么帮我|为什么要管我|为什么管我)")
_HELP_CONTINUITY_RE = re.compile(r"(还会帮我吗|还会理我吗|还会管我吗)")
_REJECTION_RE = re.compile(r"(我不喜欢这样|我不喜欢你这样|别这么说|别这样说|别这么热心|别这样)")
_LIGHT_TEASE_RE = re.compile(r"(记不住|想听我多说|还想听|逗我|考我|又来问)")
_CHALLENGING_RE = re.compile(r"(吧|吗|？|\?|就|还|又|不是|没|到底)")

_SELF_IDENTIFY_RE = re.compile(
    r"(我是夏目蓝|我叫夏目蓝|我的名字是夏目蓝|我？我的名字是夏目蓝|^(?:我[？?]?[，,\s]*)?夏目蓝[。！？?!、，,\s]*$)"
)
_BOUNDARY_ANSWER_RE = re.compile(r"(我们并?不熟|我们不熟|不熟)")
_MOTIVATION_ANSWER_RE = re.compile(r"(我只是觉得|因为你问了|看见了，也没有装作没看见|装作没看见|既然你遇到了麻烦)")
_HELP_COMMITMENT_ANSWER_RE = re.compile(r"(会帮|会理|会回|会答|不会不理|多半不会不理|不会装作没听见)")
_GENERIC_ASSISTIVE_RE = re.compile(r"(有什么事吗|有什么事|需要什么帮助|需要我|我可以帮)")


def _normalize_text(raw: Any) -> str:
    return str(raw or "").strip()


def _normalize_topic(raw: str) -> str:
    value = str(raw or "").strip()
    return value or "identity"


def classify_literal_topic(message: str) -> str:
    text = _normalize_text(message)
    if not text:
        return ""
    if _REJECTION_RE.search(text):
        return "rejection"
    if _HELP_CONTINUITY_RE.search(text):
        return "help_commitment"
    if _MOTIVATION_RE.search(text):
        return "motivation"
    if _BOUNDARY_RE.search(text):
        return "relationship_boundary"
    if _IDENTITY_SHORT_RE.match(text):
        return "identity"
    return ""


def normalize_surface_group(message: str, literal_topic: str) -> str:
    text = _normalize_text(message)
    if literal_topic == "identity" and _IDENTITY_SHORT_RE.match(text):
        return "identity_short"
    if literal_topic in {"relationship_boundary", "motivation"}:
        return "relationship_motivation"
    if literal_topic == "help_commitment":
        return "help_commitment"
    if literal_topic == "rejection":
        return "rejection"
    return ""


def classify_assistant_answer_style(message: str) -> str:
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


def _extract_answered_topics(message: str) -> set[str]:
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


def _turn_role(turn: Any) -> str:
    if isinstance(turn, Mapping):
        return _normalize_text(turn.get("role")).lower()
    return ""


def _turn_content(turn: Any) -> str:
    if isinstance(turn, Mapping):
        return _normalize_text(turn.get("content"))
    return ""


@dataclass(frozen=True)
class ConversationInterpretation:
    literal_topic: str = ""
    interaction_move: str = ""
    freshness: str = "new"
    user_stance: str = "neutral"
    repeat_question_count: int = 0
    repeat_surface_group: str = ""
    last_user_act: str = ""
    last_assistant_act: str = ""
    answered_topics_recent: tuple[str, ...] = ()
    recent_identity_answered: bool = False
    recent_boundary_answered: bool = False
    reason_tags: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "literal_topic": self.literal_topic,
            "interaction_move": self.interaction_move,
            "freshness": self.freshness,
            "user_stance": self.user_stance,
            "repeat_question_count": int(self.repeat_question_count or 0),
            "repeat_surface_group": self.repeat_surface_group,
            "last_user_act": self.last_user_act,
            "last_assistant_act": self.last_assistant_act,
            "answered_topics_recent": list(self.answered_topics_recent),
            "recent_identity_answered": bool(self.recent_identity_answered),
            "recent_boundary_answered": bool(self.recent_boundary_answered),
            "reason_tags": list(self.reason_tags),
        }


def coerce_conversation_interpretation(raw: Mapping[str, Any] | None) -> ConversationInterpretation:
    payload = dict(raw or {}) if isinstance(raw, Mapping) else {}
    return ConversationInterpretation(
        literal_topic=_normalize_text(payload.get("literal_topic")),
        interaction_move=_normalize_text(payload.get("interaction_move")),
        freshness=_normalize_text(payload.get("freshness")) or "new",
        user_stance=_normalize_text(payload.get("user_stance")) or "neutral",
        repeat_question_count=int(payload.get("repeat_question_count") or 0),
        repeat_surface_group=_normalize_text(payload.get("repeat_surface_group")),
        last_user_act=_normalize_text(payload.get("last_user_act")),
        last_assistant_act=_normalize_text(payload.get("last_assistant_act")),
        answered_topics_recent=tuple(
            str(item).strip()
            for item in list(payload.get("answered_topics_recent") or [])
            if str(item).strip()
        ),
        recent_identity_answered=bool(payload.get("recent_identity_answered") or False),
        recent_boundary_answered=bool(payload.get("recent_boundary_answered") or False),
        reason_tags=tuple(
            str(item).strip()
            for item in list(payload.get("reason_tags") or [])
            if str(item).strip()
        ),
    )


def interpret_conversation(
    *,
    recent_turns: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...] | None,
    user_message: str,
    topic_ledger: TopicLedgerBlock | None = None,
) -> ConversationInterpretation:
    turns = [dict(turn) for turn in list(recent_turns or []) if isinstance(turn, Mapping)][-6:]
    literal_topic = classify_literal_topic(user_message)
    repeat_surface_group = normalize_surface_group(user_message, literal_topic)

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
            user_topic = classify_literal_topic(content)
            if user_topic:
                last_user_act = user_topic
            if repeat_surface_group and normalize_surface_group(content, user_topic) == repeat_surface_group:
                repeat_question_count += 1
        elif role == "assistant":
            answer_style = classify_assistant_answer_style(content)
            if answer_style:
                last_assistant_act = answer_style
            topics = _extract_answered_topics(content)
            if topics:
                answered_topics_recent.update(topics)
                recent_assistant_topics.append(topics)

    recent_assistant_topics = recent_assistant_topics[-2:]
    recent_identity_answered = any("identity" in topics for topics in recent_assistant_topics)
    recent_boundary_answered = any(
        "relationship_boundary" in topics or "motivation" in topics
        for topics in recent_assistant_topics
    )

    ledger = topic_ledger or TopicLedgerBlock()
    current_topic_entry = ledger.get(literal_topic) if literal_topic else None
    if repeat_question_count > 0:
        freshness = "recent_repeat"
    elif current_topic_entry is not None:
        freshness = "long_gap_reopen"
    else:
        freshness = "new"

    text = _normalize_text(user_message)
    if literal_topic == "rejection":
        user_stance = "rejecting"
    elif _LIGHT_TEASE_RE.search(text):
        user_stance = "playful"
    elif literal_topic and repeat_question_count > 0 and _CHALLENGING_RE.search(text):
        user_stance = "challenging"
    elif literal_topic:
        user_stance = "probing"
    elif len(text) <= 8:
        user_stance = "neutral"
    else:
        user_stance = "neutral"

    if literal_topic == "rejection":
        interaction_move = "repair_needed"
    elif freshness == "new":
        interaction_move = "first_ask"
    elif literal_topic == "help_commitment":
        interaction_move = "anxiety_check"
    elif repeat_question_count >= 3:
        interaction_move = "long_repeat"
    elif user_stance == "challenging":
        interaction_move = "probe"
    elif user_stance == "playful":
        interaction_move = "light_tease"
    elif freshness in {"recent_repeat", "long_gap_reopen"}:
        interaction_move = "reconfirm"
    else:
        interaction_move = "probe"

    if literal_topic:
        reason_tags.append(f"literal_topic:{literal_topic}")
    if interaction_move:
        reason_tags.append(f"interaction_move:{interaction_move}")
    reason_tags.append(f"freshness:{freshness}")
    reason_tags.append(f"user_stance:{user_stance}")
    if last_user_act:
        reason_tags.append(f"last_user_act:{last_user_act}")
    if last_assistant_act:
        reason_tags.append(f"last_assistant_act:{last_assistant_act}")
    if repeat_surface_group:
        reason_tags.append(f"repeat_surface:{repeat_surface_group}")
    if repeat_question_count > 0:
        reason_tags.append(f"repeat_count:{repeat_question_count}")
    if current_topic_entry is not None:
        reason_tags.append("topic_seen_before")
    if recent_identity_answered:
        reason_tags.append("recent_identity_answered")
    if recent_boundary_answered:
        reason_tags.append("recent_boundary_answered")

    return ConversationInterpretation(
        literal_topic=_normalize_topic(literal_topic) if literal_topic else "",
        interaction_move=interaction_move,
        freshness=freshness,
        user_stance=user_stance,
        repeat_question_count=repeat_question_count,
        repeat_surface_group=repeat_surface_group,
        last_user_act=last_user_act,
        last_assistant_act=last_assistant_act,
        answered_topics_recent=tuple(sorted(answered_topics_recent)),
        recent_identity_answered=recent_identity_answered,
        recent_boundary_answered=recent_boundary_answered,
        reason_tags=tuple(reason_tags),
    )


__all__ = [
    "ConversationInterpretation",
    "classify_assistant_answer_style",
    "classify_literal_topic",
    "coerce_conversation_interpretation",
    "interpret_conversation",
    "normalize_surface_group",
]
