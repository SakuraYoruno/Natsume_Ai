from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Mapping


_IDENTITY_SHORT_RE = re.compile(r"^[、，,\s]*(?:你是(?:谁)?|你谁|你是谁)[?？!！。…~]*$")
_BOUNDARY_RE = re.compile(r"(我们.*不熟|不熟吧|很熟吗|我们很熟吗|我们熟吗)")
_MOTIVATION_RE = re.compile(r"(为什么要帮我|为什么帮我|为什么要管我|为什么管我)")
_HELP_CONTINUITY_RE = re.compile(r"(还会帮我吗|还会理我吗|还会管我吗)")
_GREETING_RE = re.compile(r"^(?:你好|您好|嗨|哈喽|hello|hi|hey|早安|早上好|中午好|晚上好|晚安)[!！。…~\s]*$", re.IGNORECASE)
_PRESENCE_PING_RE = re.compile(r"^(?:在吗|在不在|还在吗|忙吗|在嘛|有人吗)[?？!！。…~\s]*$")
_AUDITORY_CHECK_RE = re.compile(
    r"^(?:"
    r"(?:你)?(?:能不能|能|可以|可不可以|有没有)?(?:听到|听见|听得到|听得见|听清|听清楚)"
    r"(?:我|我说话|我讲话|声音)?(?:吗|么)?"
    r"|(?:我)?(?:说话|讲话|发声|声音)(?:你)?(?:能不能|能|可以|可不可以)?"
    r"(?:听到|听见|听得到|听得见|听清|听清楚)(?:吗|么)?"
    r"|(?:有|有没有)(?:听到|听见)?声音(?:吗|么)?"
    r"|(?:刚才|刚刚)?(?:我)?(?:说了|说话|讲话)(?:什么)?(?:你)?(?:听到|听见|听得到|听得见)(?:吗|么)?"
    r")[?？!！。…~\s]*$",
    re.IGNORECASE,
)
_LIGHT_ACK_RE = re.compile(r"^(?:嗯|嗯嗯|哦|哦哦|好的?|行|行吧|收到|ok|okay|好哦|好呀)[!！。…~\s]*$", re.IGNORECASE)
_PLAYFUL_PING_RE = re.compile(r"^(?:戳戳|喂喂|嘿嘿|冒泡|路过|ping|戳一戳)[!！。…~\s]*$")
_IMAGE_MESSAGE_RE = re.compile(
    r"^\[(?:图片|图像|表情|表情包|贴图|贴纸|截图)(?:消息)?\]$|"
    r"^\[(?:image|emoji|sticker|photo|picture|screenshot)(?: message)?\]$|"
    r"^\[[?\uff1f]{2,}\]$",
    re.IGNORECASE,
)
_IMAGE_SOCIAL_ASSISTANT_RE = re.compile(
    r"(委屈成这样|突然这么可爱|想让我哄你|软乎乎的表情|小表情|拿这个表情|拿这种|撒娇吗|闹别扭呀|情绪这么低呀|一下子软成这样|可爱成这样|突然这么乖|想让我抱抱你吗|丢这个给我看呀|丢这个给我看|发这个给我看呀|发这个给我看|拿来给我看一下|看错啦|当我看错啦|多想了一点|会错意)"
)
_SELF_IDENTIFY_RE = re.compile(r"(我是|我叫|我的名字是)")
_BOUNDARY_DISTANCE_ANSWER_RE = re.compile(r"(我们并?不熟|我们不熟|不熟|不算熟|没熟到那种地步|慢慢熟|非要多熟|搭把手|顺路嘛|正好路过)")
_STEPSISTER_BOUNDARY_ANSWER_RE = re.compile(
    r"(义妹|妹妹|一家人|家里人|外人|见外|别装(?:生分|陌生|不熟)|装(?:生分|陌生|不熟)|划清界限|往外推|不当外人)"
)
_MOTIVATION_ANSWER_RE = re.compile(
    r"(会管|我管|要管|会帮|我帮|帮你|插手|接手|放着不管|不可能不管|不能不管|不当外人|别装(?:生分|陌生|不熟)|装(?:生分|陌生|不熟)|见外)"
)
_HELP_ANSWER_RE = re.compile(r"(会帮|会理|会回|会答|不会不理|多半不会不理|不会装作没听见)")
_IMAGE_SOCIAL_DENIAL_RE = re.compile(
    r"(我不委屈|不委屈|我没委屈|我不难过|我没难过|才没有|哪有|(?:没有|没|不是|不).{0,4}(?:闹别扭|委屈|难过|生气|不高兴|撒娇|伤心|emo|哭))"
)
_IMAGE_SOCIAL_WHY_RE = re.compile(r"(怎么了啊|怎么啦啊|怎么了呀|怎么啦呀|怎么了嘛|怎么啦嘛)")
_IMAGE_SOCIAL_NOTHING_RE = re.compile(r"(没什么是怎么了|没什么啦|没什么呀|没什么啊|没事啊|没事啦)")
_IMAGE_SOCIAL_SHORT_DENIAL_RE = re.compile(r"^(?:没有|没|不是|不|才没有|哪有)[啊呀啦嘛么哇呢哦]*$")
_IMAGE_SOCIAL_NEGATION_RE = re.compile(r"(没有|没|不是|不|才没有|哪有)")
_IMAGE_SOCIAL_AFFECT_TARGET_RE = re.compile(r"(闹别扭|委屈|难过|生气|不高兴|撒娇|伤心|emo|哭)")
_ASSISTANT_REPLY_MEANING_RE = re.compile(
    r"^(?:什么意思|啥意思|什么意思啊|什么意思呀|啥意思啊|啥意思呀|这句什么意思|这句话什么意思|这个什么意思|这是什么意思|是什么意思|意思是啥)[?？!！。…~\s]*$",
    re.IGNORECASE,
)
_ASSISTANT_REPLY_SURPRISE_RE = re.compile(r"^(?:啊|哈|诶|嗯|唔|啥|什么)[?？！？!。…~\s]*$", re.IGNORECASE)
_ASSISTANT_REPLY_PUNCT_QUESTION_RE = re.compile(r"^[?？]{1,4}$")
_ASSISTANT_REPLY_PUNCT_SURPRISE_RE = re.compile(r"^[!！?？]{1,4}$")
_ASSISTANT_REPLY_OBJECT_RE = re.compile(
    r"^(?:知道|懂|明白|清楚|看见|看到|说|回|回复|讲|问|指|是|算|觉得|以为).{0,6}(?:什么|啥)[?？!！。…~\s]*$",
    re.IGNORECASE,
)


def _normalize_text(raw: Any) -> str:
    return str(raw or "").strip()


def _compact_text(raw: Any) -> str:
    return re.sub(r"[\s\u3000，,。.!！?？、~～…]+", "", str(raw or "").strip().lower())


def _turn_role(turn: Any) -> str:
    if isinstance(turn, Mapping):
        return _normalize_text(turn.get("role")).lower()
    return ""


def _turn_content(turn: Any) -> str:
    if isinstance(turn, Mapping):
        return _normalize_text(turn.get("content"))
    return ""


def _turn_metadata(turn: Any) -> dict[str, Any]:
    if isinstance(turn, Mapping):
        raw = turn.get("metadata")
        return dict(raw) if isinstance(raw, Mapping) else {}
    return {}


def _turn_multimodal_observations(turn: Any) -> dict[str, Any]:
    metadata = _turn_metadata(turn)
    raw = metadata.get("multimodal_observations")
    return dict(raw) if isinstance(raw, Mapping) else {}


def _turn_has_image_social_metadata(turn: Any) -> bool:
    metadata = _turn_metadata(turn)
    observations = _turn_multimodal_observations(turn)
    attachments = list(metadata.get("attachments") or [])
    primary_modality = _normalize_text(observations.get("primary_modality")).lower()
    observed_types = {
        _normalize_text(item).lower()
        for item in list(observations.get("observed_types") or observations.get("modalities") or [])
        if _normalize_text(item)
    }
    has_image_attachment = any(
        _normalize_text(item.get("type")).lower() in {"image", "emoji", "sticker"}
        for item in attachments
        if isinstance(item, Mapping)
    )
    social_signal = _normalize_text(observations.get("social_signal"))
    emotion_signals = [
        _normalize_text(item)
        for item in list(observations.get("emotion_signals") or [])
        if _normalize_text(item)
    ]
    return bool(
        primary_modality in {"image", "emoji", "sticker"}
        or observed_types.intersection({"image", "emoji", "sticker"})
        or has_image_attachment
        or social_signal
        or emotion_signals
    )


def _classify_user_topic(text: str) -> str:
    if not text:
        return ""
    if _IDENTITY_SHORT_RE.match(text):
        return "identity"
    if _HELP_CONTINUITY_RE.search(text):
        return "help_commitment"
    if _MOTIVATION_RE.search(text):
        return "motivation"
    if _BOUNDARY_RE.search(text):
        return "relationship_boundary"
    if _GREETING_RE.match(text):
        return "greeting"
    if _AUDITORY_CHECK_RE.match(text):
        return "auditory_check"
    if _PRESENCE_PING_RE.match(text):
        return "presence_ping"
    if _LIGHT_ACK_RE.match(text):
        return "light_ack"
    if _PLAYFUL_PING_RE.match(text):
        return "playful_ping"
    return ""


def _classify_assistant_topic(text: str) -> str:
    if not text:
        return ""
    if _IMAGE_SOCIAL_ASSISTANT_RE.search(text):
        return "image_social"
    if _SELF_IDENTIFY_RE.search(text):
        return "identity"
    if _BOUNDARY_DISTANCE_ANSWER_RE.search(text):
        return ""
    if _STEPSISTER_BOUNDARY_ANSWER_RE.search(text) or _MOTIVATION_ANSWER_RE.search(text):
        return "relationship_boundary"
    if _HELP_ANSWER_RE.search(text):
        return "help_commitment"
    return ""


def detect_image_social_followup_kind(text: str) -> str:
    if not text:
        return ""
    normalized = _normalize_text(text)
    if _IMAGE_SOCIAL_NOTHING_RE.search(normalized):
        return "nothing"
    if _IMAGE_SOCIAL_WHY_RE.search(normalized):
        return "why"
    if _IMAGE_SOCIAL_DENIAL_RE.search(normalized):
        return "deny"
    if _IMAGE_SOCIAL_SHORT_DENIAL_RE.match(normalized):
        return "deny"
    if _IMAGE_SOCIAL_NEGATION_RE.search(normalized) and _IMAGE_SOCIAL_AFFECT_TARGET_RE.search(normalized):
        return "deny"
    return ""


def detect_assistant_reply_followup_kind(text: str, latest_assistant_text: str = "") -> str:
    """Detect short user turns that ask about the previous assistant reply.

    This is intentionally a semantic thread detector, not a response author.
    It only says "the user is asking us to clarify what we just said".
    """
    normalized = _normalize_text(text)
    if latest_assistant_text and _ASSISTANT_REPLY_PUNCT_QUESTION_RE.match(normalized):
        return "clarify_meaning"
    if latest_assistant_text and _ASSISTANT_REPLY_PUNCT_SURPRISE_RE.match(normalized):
        return "surprise"
    compact = _compact_text(normalized)
    if not compact or len(compact) > 18:
        return ""
    if _ASSISTANT_REPLY_MEANING_RE.match(normalized):
        return "clarify_meaning"
    if _ASSISTANT_REPLY_SURPRISE_RE.match(normalized):
        return "surprise"
    if _ASSISTANT_REPLY_OBJECT_RE.match(normalized):
        return "ask_object"

    if ("什么" in compact or "啥" in compact) and len(compact) <= 10:
        previous = _compact_text(latest_assistant_text)
        if previous:
            previous_core = previous[: min(8, max(2, len(previous)))]
            current_stem = compact.replace("什么", "").replace("啥", "")
            if current_stem and (
                current_stem in previous
                or previous_core.startswith(current_stem)
                or compact.startswith(previous_core)
            ):
                return "ask_object"
        if compact in {"什么", "啥", "说什么", "回什么", "问什么"}:
            return "ask_object"
    return ""


def _derive_emotion_anchor(text: str) -> str:
    haystack = _normalize_text(text)
    if any(token in haystack for token in ("委屈", "哭", "难过", "伤心", "泪")):
        return "distress"
    if any(token in haystack for token in ("可爱", "卖萌", "猫耳", "乖", "萌")):
        return "cute"
    if any(token in haystack for token in ("别扭", "无语", "不爽", "小脾气", "吐槽")):
        return "tease"
    return ""


@dataclass(frozen=True)
class InteractionThreadState:
    thread_kind: str = ""
    source_topic: str = ""
    source_modality: str = ""
    source_excerpt: str = ""
    source_context_excerpt: str = ""
    emotion_anchor: str = ""
    open: bool = False
    source_turn_index: int | None = None
    last_assistant_move: str = ""
    last_user_reaction: str = ""
    turn_count: int = 0
    reason_tags: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "thread_kind": self.thread_kind,
            "source_topic": self.source_topic,
            "source_modality": self.source_modality,
            "source_excerpt": self.source_excerpt,
            "source_context_excerpt": self.source_context_excerpt,
            "emotion_anchor": self.emotion_anchor,
            "open": bool(self.open),
            "source_turn_index": self.source_turn_index,
            "last_assistant_move": self.last_assistant_move,
            "last_user_reaction": self.last_user_reaction,
            "turn_count": int(self.turn_count or 0),
            "reason_tags": list(self.reason_tags),
        }


def coerce_interaction_thread_state(raw: Mapping[str, Any] | None) -> InteractionThreadState:
    payload = dict(raw or {}) if isinstance(raw, Mapping) else {}
    return InteractionThreadState(
        thread_kind=_normalize_text(payload.get("thread_kind")),
        source_topic=_normalize_text(payload.get("source_topic")),
        source_modality=_normalize_text(payload.get("source_modality")),
        source_excerpt=_normalize_text(payload.get("source_excerpt")),
        source_context_excerpt=_normalize_text(payload.get("source_context_excerpt")),
        emotion_anchor=_normalize_text(payload.get("emotion_anchor")),
        open=bool(payload.get("open") or False),
        source_turn_index=payload.get("source_turn_index"),
        last_assistant_move=_normalize_text(payload.get("last_assistant_move")),
        last_user_reaction=_normalize_text(payload.get("last_user_reaction")),
        turn_count=int(payload.get("turn_count") or 0),
        reason_tags=tuple(str(item).strip() for item in list(payload.get("reason_tags") or []) if str(item).strip()),
    )


def resolve_interaction_thread(
    *,
    recent_turns: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...] | None,
    current_literal_topic: str = "",
    current_user_message: str = "",
    multimodal_observations: Mapping[str, Any] | None = None,
) -> InteractionThreadState:
    turns = [dict(turn) for turn in list(recent_turns or []) if isinstance(turn, Mapping)][-8:]
    reason_tags: list[str] = []
    multimodal = dict(multimodal_observations or {})
    current_topic = _normalize_text(current_literal_topic)

    if multimodal:
        primary_modality = _normalize_text(multimodal.get("primary_modality")).lower()
        if current_topic == "emoji_social_reply" and primary_modality in {"image", "emoji", "sticker"}:
            return InteractionThreadState(
                thread_kind="image_social",
                source_topic="emoji_social_reply",
                source_modality=primary_modality,
                emotion_anchor=_derive_emotion_anchor(
                    " ".join(
                        [
                            _normalize_text(multimodal.get("core_observation")),
                            _normalize_text(multimodal.get("summary_for_chat")),
                            " ".join(str(item).strip() for item in list(multimodal.get("emotion_signals") or []) if str(item).strip()),
                        ]
                    )
                ),
                open=True,
                source_turn_index=len(turns),
                turn_count=1,
                reason_tags=("current_multimodal_image_social",),
            )

    image_source_index: int | None = None
    image_emotion_anchor = ""
    last_assistant_move = ""
    latest_assistant_text = ""
    latest_assistant_context_text = ""
    last_user_reaction = ""
    last_user_turn_index: int | None = None
    last_user_text = ""
    for idx, turn in enumerate(turns):
        role = _turn_role(turn)
        content = _turn_content(turn)
        if role == "user" and (_IMAGE_MESSAGE_RE.match(content) or _turn_has_image_social_metadata(turn)):
            image_source_index = idx
            reason_tags.append("recent_image_message")
            observations = _turn_multimodal_observations(turn)
            metadata_emotion = " ".join(
                [
                    _normalize_text(observations.get("social_signal")),
                    _normalize_text(observations.get("core_observation")),
                    _normalize_text(observations.get("summary_for_chat")),
                    " ".join(
                        _normalize_text(item)
                        for item in list(observations.get("emotion_signals") or [])
                        if _normalize_text(item)
                    ),
                ]
            )
            if metadata_emotion and not image_emotion_anchor:
                image_emotion_anchor = _derive_emotion_anchor(metadata_emotion)
        if role == "user":
            last_user_turn_index = idx
            if content:
                last_user_text = content
        if role == "assistant":
            if content:
                latest_assistant_text = content
                latest_assistant_context_text = last_user_text
            assistant_topic = _classify_assistant_topic(content)
            if assistant_topic == "image_social":
                if image_source_index is None:
                    image_source_index = last_user_turn_index
                last_assistant_move = "image_social_ack"
                image_emotion_anchor = _derive_emotion_anchor(content)
                reason_tags.append("recent_image_social_assistant")
        elif role == "user":
            reaction = detect_image_social_followup_kind(content)
            if reaction:
                last_user_reaction = reaction

    if last_assistant_move:
        return InteractionThreadState(
            thread_kind="image_social",
            source_topic="emoji_social_reply",
            source_modality="image",
            emotion_anchor=image_emotion_anchor,
            open=True,
            source_turn_index=image_source_index,
            last_assistant_move=last_assistant_move,
            last_user_reaction=last_user_reaction,
            turn_count=max(1, len(turns) - image_source_index),
            reason_tags=tuple(dict.fromkeys(reason_tags)),
        )

    if image_source_index is not None and detect_image_social_followup_kind(current_user_message):
        return InteractionThreadState(
            thread_kind="image_social",
            source_topic="emoji_social_reply",
            source_modality="image",
            emotion_anchor=image_emotion_anchor,
            open=True,
            source_turn_index=image_source_index,
            last_assistant_move="image_social_ack",
            last_user_reaction=detect_image_social_followup_kind(current_user_message),
            turn_count=max(1, len(turns) - image_source_index),
            reason_tags=tuple(dict.fromkeys([*reason_tags, "recent_image_social_metadata"])),
        )

    assistant_followup_kind = detect_assistant_reply_followup_kind(
        current_user_message,
        latest_assistant_text=latest_assistant_text,
    )
    if assistant_followup_kind and latest_assistant_text and current_topic not in {
        "identity",
        "relationship_boundary",
        "motivation",
        "help_commitment",
        "emoji_social_reply",
        "emoji_social_followup",
        "meme_meaning",
        "literal_description",
        "screenshot_analysis",
        "capability_check",
    }:
        return InteractionThreadState(
            thread_kind="assistant_reply_followup",
            source_topic="assistant_reply",
            source_modality="text",
            source_excerpt=latest_assistant_text[:160],
            source_context_excerpt=latest_assistant_context_text[:160],
            open=True,
            source_turn_index=max(0, len(turns) - 1),
            last_assistant_move=_classify_assistant_topic(latest_assistant_text) or "previous_reply",
            last_user_reaction=assistant_followup_kind,
            turn_count=1,
            reason_tags=("recent_assistant_reply_followup", f"assistant_reply_followup:{assistant_followup_kind}"),
        )

    user_topics = [_classify_user_topic(_turn_content(turn)) for turn in turns if _turn_role(turn) == "user"]
    assistant_topics = [_classify_assistant_topic(_turn_content(turn)) for turn in turns if _turn_role(turn) == "assistant"]

    if current_topic == "identity" or "identity" in user_topics[-3:]:
        if "identity" in assistant_topics[-2:]:
            return InteractionThreadState(
                thread_kind="identity_loop",
                source_topic="identity",
                open=True,
                turn_count=max(1, user_topics[-3:].count("identity")),
                reason_tags=("recent_identity_loop",),
            )
    if current_topic in {"relationship_boundary", "motivation"} or any(topic in {"relationship_boundary", "motivation"} for topic in user_topics[-3:]):
        if "relationship_boundary" in assistant_topics[-2:]:
            return InteractionThreadState(
                thread_kind="boundary_probe",
                source_topic="motivation",
                open=True,
                turn_count=max(1, sum(1 for topic in user_topics[-3:] if topic in {"relationship_boundary", "motivation"})),
                reason_tags=("recent_boundary_probe",),
            )
    if current_topic == "help_commitment" or "help_commitment" in user_topics[-3:]:
        if "help_commitment" in assistant_topics[-2:]:
            return InteractionThreadState(
                thread_kind="help_reassurance",
                source_topic="help_commitment",
                open=True,
                turn_count=max(1, user_topics[-3:].count("help_commitment")),
                reason_tags=("recent_help_reassurance",),
            )
    if current_topic in {"greeting", "presence_ping", "auditory_check", "light_ack", "playful_ping"}:
        return InteractionThreadState(
            thread_kind="phatic",
            source_topic=current_topic,
            open=True,
            turn_count=1,
            reason_tags=("current_phatic",),
        )
    return InteractionThreadState()


__all__ = [
    "detect_assistant_reply_followup_kind",
    "detect_image_social_followup_kind",
    "InteractionThreadState",
    "coerce_interaction_thread_state",
    "resolve_interaction_thread",
]
