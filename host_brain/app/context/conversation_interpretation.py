from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Mapping

from .interaction_thread import (
    InteractionThreadState,
    detect_assistant_reply_followup_kind,
    detect_image_social_followup_kind,
    resolve_interaction_thread,
)
from .topic_ledger import TopicLedgerBlock


_IDENTITY_SHORT_RE = re.compile(r"^[、，,\s]*(?:你是(?:谁)?|你谁|你是谁)[?？!！。…~]*$")
_BOUNDARY_RE = re.compile(r"(我们.*不熟|不熟吧|很熟吗|我们很熟吗|我们熟吗)")
_MOTIVATION_RE = re.compile(r"(为什么要帮我|为什么帮我|为什么要管我|为什么管我)")
_HELP_CONTINUITY_RE = re.compile(r"(还会帮我吗|还会理我吗|还会管我吗)")
_REJECTION_RE = re.compile(r"(我不喜欢这样|我不喜欢你这样|别这么说|别这样说|别这么热心|别这样)")
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
_CONNECTIVITY_CHECK_RE = re.compile(
    r"^(?:测试(?:一下|下)?|test|ping|"
    r"(?:测试(?:一下|下)?(?:现在)?(?:能不能|能|会不会|会|可以)?(?:回|回复|收到)(?:吗|么)?)|"
    r"(?:你)?(?:能不能|能|会不会|会|可以)(?:回|回复)(?:我)?(?:吗|么)?|"
    r"(?:你)?(?:能不能|能|可以)?收到(?:吗|么)?|"
    r"有反应(?:吗|么)?|回我一下|回复一下|在线(?:吗|么)?)"
    r"[?？!！。…~\s]*$",
    re.IGNORECASE,
)
_LIGHT_ACK_RE = re.compile(r"^(?:嗯|嗯嗯|哦|哦哦|好的?|行|行吧|收到|ok|okay|好哦|好呀)[!！。…~\s]*$", re.IGNORECASE)
_PLAYFUL_PING_RE = re.compile(r"^(?:戳戳|喂喂|嘿嘿|冒泡|路过|ping|戳一戳)[!！。…~\s]*$", re.IGNORECASE)
_PHATIC_PUNCT_RE = re.compile(r"^[?？!！。…~、,.，]{1,4}$")
_LIGHT_TEASE_RE = re.compile(r"(记不住|想听我多说|还想听|逗我|考我|又来问)")
_CHALLENGING_RE = re.compile(r"(吧|吗|？|\?|就|还|又|不是|没|到底)")
_CAPABILITY_CHECK_RE = re.compile(r"(能|可以|可不可以|是否).{0,8}(看到|看见|看清|看懂|识别)|你.{0,4}(看到|看见|看懂)|看得到", re.IGNORECASE)
_MEANING_QUERY_RE = re.compile(r"(什么意思|啥意思|什么梗|表达什么|想表达什么|在说什么|什么语气|什么感觉)", re.IGNORECASE)
_SCREENSHOT_QUERY_RE = re.compile(r"(报错|错误|怎么|哪里|哪儿|按钮|界面|问题|啥情况|什么意思|怎么操作|点哪里)", re.IGNORECASE)
_LITERAL_QUERY_RE = re.compile(r"(图里有什么|图片里有什么|画的什么|是什么|写了什么|看到什么|内容是什么)", re.IGNORECASE)
_IMAGE_LOOK_REQUEST_RE = re.compile(
    r"(?:帮我)?(?:看一下|看下|看看|看一眼|你看)(?:这|这个|下)?(?:张)?(?:图|图片|照片|截图)?|"
    r"(?:这|这个)(?:张)?(?:图|图片|照片|截图)(?:你)?(?:看|看看|看一下|看下)",
    re.IGNORECASE,
)
_IMAGE_SOCIAL_SIGNAL_TOKENS = (
    "表情包",
    "表情图",
    "贴纸",
    "贴图",
    "反应图",
    "meme",
    "委屈",
    "哭",
    "难过",
    "伤心",
    "可怜",
    "可爱",
    "卖萌",
    "猫耳",
    "装乖",
    "乖",
    "萌",
    "无语",
    "尴尬",
    "炸毛",
    "抱抱",
    "撒娇",
    "吐槽",
    "不爽",
    "气鼓鼓",
    "打趣",
    "逗",
)
_MEDIA_ONLY_PLACEHOLDER_RE = re.compile(
    r"^\[(?:图片|图像|表情|表情包|贴图|贴纸|截图|文件|语音|视频)(?:消息)?\]$|"
    r"^\[(?:image|emoji|sticker|photo|picture|screenshot|file|voice|video)(?: message)?\]$|"
    r"^\[[?\uff1f]{2,}\]$|"
    r"^<\s*(?:image|emoji|sticker|photo|picture)\s*>$",
    re.IGNORECASE,
)
_IMAGE_SOCIAL_ASSISTANT_RE = re.compile(
    r"(委屈成这样|突然这么可爱|想让我哄你|软乎乎的表情|小表情|拿这个表情|拿这种|撒娇吗|闹别扭呀|情绪这么低呀|一下子软成这样)"
)
_BOUNDARY_DISTANCE_ANSWER_RE = re.compile(r"(我们并?不熟|我们不熟|不熟|不算熟|没熟到那种地步|慢慢熟|非要多熟|搭把手|顺路嘛|正好路过)")
_STEPSISTER_BOUNDARY_ANSWER_RE = re.compile(
    r"(义妹|妹妹|一家人|家里人|外人|见外|别装(?:生分|陌生|不熟)|装(?:生分|陌生|不熟)|划清界限|往外推|不当外人)"
)
_MOTIVATION_ANSWER_RE = re.compile(
    r"(会管|我管|要管|会帮|我帮|帮你|插手|接手|放着不管|不可能不管|不能不管|不当外人|别装(?:生分|陌生|不熟)|装(?:生分|陌生|不熟)|见外)"
)
_HELP_COMMITMENT_ANSWER_RE = re.compile(r"(会帮|会理|会回|会答|不会不理|多半不会不理|不会装作没听见)")
_GENERIC_ASSISTIVE_RE = re.compile(r"(有什么事吗|有什么事|需要什么帮助|需要我|我可以帮)")


def _normalize_text(raw: Any) -> str:
    return str(raw or "").strip()


def _normalize_topic(raw: str) -> str:
    value = str(raw or "").strip()
    return value or "identity"


def _current_identity_aliases() -> tuple[str, ...]:
    try:
        from host_brain.app.context.soul_runtime import get_compiled_soul  # noqa: PLC0415

        soul = get_compiled_soul()
        aliases = tuple(str(item).strip() for item in getattr(soul, "identity_aliases", ()) if str(item).strip())
        if aliases:
            return aliases
        display_name = str(getattr(soul, "display_name", "") or "").strip()
        if display_name:
            return (display_name, display_name.replace(" ", ""))
    except Exception:
        pass
    return ()


def _contains_identity_name(text: str) -> bool:
    normalized = _normalize_text(text)
    if not normalized:
        return False
    return any(alias and alias in normalized for alias in _current_identity_aliases())


def _infer_literal_topic_from_perception(message: str, multimodal_payload: Mapping[str, Any] | None) -> str:
    payload = dict(multimodal_payload or {})
    if not payload:
        return ""

    text = _normalize_text(message)
    normalized = text.lower()
    primary_modality = str(payload.get("primary_modality") or "").strip().lower()
    observed_types = {
        str(item).strip().lower()
        for item in list(payload.get("observed_types") or [])
        if str(item).strip()
    }
    artifact_type = str(payload.get("artifact_type") or "").strip().lower()
    core_observation = _normalize_text(payload.get("core_observation"))
    visible_text = _normalize_text(payload.get("visible_text"))
    social_signal = _normalize_text(payload.get("social_signal"))
    emotion_signals = [
        str(item).strip()
        for item in list(payload.get("emotion_signals") or [])
        if str(item).strip()
    ]
    haystack = " ".join([core_observation, visible_text, social_signal, *emotion_signals]).lower()
    has_visual_payload = bool(primary_modality or observed_types or core_observation or visible_text or emotion_signals)
    if not has_visual_payload:
        return ""

    visual_modalities = {"image", "emoji", "sticker"}
    is_screenshot = artifact_type == "screenshot" or "screenshot" in observed_types
    is_visual_payload = primary_modality in visual_modalities or bool(observed_types.intersection(visual_modalities))
    is_emoji_payload = primary_modality in {"emoji", "sticker"} or bool(observed_types.intersection({"emoji", "sticker"}))
    is_plain_image_payload = primary_modality == "image" or "image" in observed_types
    has_specific_social_signal = bool(social_signal or emotion_signals) or any(
        token in haystack for token in _IMAGE_SOCIAL_SIGNAL_TOKENS
    )
    is_social_image = is_visual_payload and not is_screenshot and (
        is_emoji_payload
        or (is_plain_image_payload and has_specific_social_signal)
    )
    media_only = not normalized or bool(_MEDIA_ONLY_PLACEHOLDER_RE.match(normalized))

    if text and _CAPABILITY_CHECK_RE.search(text):
        return "capability_check"
    if text and _MEANING_QUERY_RE.search(text):
        return "meme_meaning" if is_social_image else "literal_description"
    if is_screenshot and text and _SCREENSHOT_QUERY_RE.search(text):
        return "screenshot_analysis"
    if text and _LITERAL_QUERY_RE.search(text):
        return "literal_description"
    if text and _IMAGE_LOOK_REQUEST_RE.search(text):
        return "emoji_social_reply" if is_social_image else "literal_description"
    if media_only:
        if is_social_image:
            return "emoji_social_reply"
        if is_plain_image_payload or is_screenshot:
            return "attachment_only_no_question"
        return "attachment_only_no_question"
    if is_social_image:
        return "emoji_social_reply"
    if is_screenshot:
        return "attachment_only_no_question"
    return ""


def classify_literal_topic(message: str, multimodal_payload: Mapping[str, Any] | None = None) -> str:
    text = _normalize_text(message)
    perception_topic = _infer_literal_topic_from_perception(message, multimodal_payload)
    if perception_topic:
        return perception_topic
    if not text:
        return ""
    if _GREETING_RE.match(text):
        return "greeting"
    if _AUDITORY_CHECK_RE.match(text):
        return "auditory_check"
    if _PRESENCE_PING_RE.match(text):
        return "presence_ping"
    if _CONNECTIVITY_CHECK_RE.match(text):
        return "presence_ping"
    if _LIGHT_ACK_RE.match(text):
        return "light_ack"
    if _PLAYFUL_PING_RE.match(text):
        return "playful_ping"
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
    if _contains_identity_name(text) and re.search(r"(我是|我叫|我的名字是|^(?:我[？?]?[，,\s]*)?)", text):
        return "self_identify"
    if _BOUNDARY_DISTANCE_ANSWER_RE.search(text):
        return "boundary_distance_answer"
    if _STEPSISTER_BOUNDARY_ANSWER_RE.search(text):
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
    if _contains_identity_name(text):
        topics.add("identity")
    if _STEPSISTER_BOUNDARY_ANSWER_RE.search(text):
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
    ongoing_thread: str = ""
    followup_target: str = ""
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
            "ongoing_thread": self.ongoing_thread,
            "followup_target": self.followup_target,
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
        ongoing_thread=_normalize_text(payload.get("ongoing_thread")),
        followup_target=_normalize_text(payload.get("followup_target")),
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
    multimodal_observations: Mapping[str, Any] | None = None,
    interaction_thread: InteractionThreadState | None = None,
) -> ConversationInterpretation:
    turns = [dict(turn) for turn in list(recent_turns or []) if isinstance(turn, Mapping)][-6:]
    multimodal_payload = dict(multimodal_observations or {})
    literal_topic = classify_literal_topic(
        user_message,
        multimodal_payload=multimodal_payload,
    )
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
    thread_state = interaction_thread or resolve_interaction_thread(
        recent_turns=turns,
        current_literal_topic=literal_topic,
        current_user_message=user_message,
        multimodal_observations=multimodal_payload,
    )
    recent_image_social_context = bool(thread_state.open and thread_state.thread_kind == "image_social")
    recent_assistant_reply_context = bool(thread_state.open and thread_state.thread_kind == "assistant_reply_followup")

    image_followup_kind = ""
    text = _normalize_text(user_message)
    if not literal_topic and recent_image_social_context and text:
        image_followup_kind = detect_image_social_followup_kind(text)
        if image_followup_kind:
            literal_topic = "emoji_social_followup"
    assistant_followup_kind = ""
    if not literal_topic and recent_assistant_reply_context and text:
        assistant_followup_kind = (
            str(thread_state.last_user_reaction or "").strip()
            or detect_assistant_reply_followup_kind(text, str(thread_state.source_excerpt or ""))
        )
        if assistant_followup_kind:
            literal_topic = "assistant_reply_followup"
    if not literal_topic and text and _PHATIC_PUNCT_RE.match(text):
        literal_topic = "phatic_punct"

    ledger = topic_ledger or TopicLedgerBlock()
    current_topic_entry = ledger.get(literal_topic) if literal_topic else None
    if repeat_question_count > 0:
        freshness = "recent_repeat"
    elif current_topic_entry is not None:
        freshness = "long_gap_reopen"
    else:
        freshness = "new"

    if literal_topic == "rejection":
        user_stance = "rejecting"
    elif literal_topic == "assistant_reply_followup":
        if assistant_followup_kind in {"surprise"}:
            user_stance = "challenging"
        else:
            user_stance = "probing"
    elif literal_topic == "emoji_social_followup":
        if image_followup_kind == "deny":
            user_stance = "challenging"
        elif image_followup_kind == "nothing":
            user_stance = "playful"
        else:
            user_stance = "probing"
    elif literal_topic == "emoji_social_reply":
        user_stance = "playful"
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
    elif literal_topic == "assistant_reply_followup":
        if assistant_followup_kind == "ask_object":
            interaction_move = "clarify_reference"
        elif assistant_followup_kind == "surprise":
            interaction_move = "repair_previous_reply"
        else:
            interaction_move = "clarify_previous_reply"
    elif literal_topic == "emoji_social_followup":
        if image_followup_kind == "deny":
            interaction_move = "repair_needed"
        elif image_followup_kind == "nothing":
            interaction_move = "light_tease"
        else:
            interaction_move = "reconfirm"
    elif literal_topic == "auditory_check":
        interaction_move = "auditory_probe"
    elif literal_topic in {"greeting", "presence_ping", "light_ack", "phatic_punct"}:
        interaction_move = "light_ack"
    elif literal_topic in {"playful_ping", "emoji_social_reply"}:
        interaction_move = "playful_ping"
    elif literal_topic in {"attachment_only_no_question"}:
        interaction_move = "light_ack"
    elif literal_topic in {"meme_meaning", "literal_description", "screenshot_analysis", "capability_check"}:
        interaction_move = "probe"
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
    if image_followup_kind:
        reason_tags.append(f"image_social_followup:{image_followup_kind}")
    if assistant_followup_kind:
        reason_tags.append(f"assistant_reply_followup:{assistant_followup_kind}")
    if thread_state.thread_kind:
        reason_tags.append(f"ongoing_thread:{thread_state.thread_kind}")
    if thread_state.source_topic:
        reason_tags.append(f"followup_target:{thread_state.source_topic}")
    if recent_image_social_context:
        reason_tags.append("recent_image_social_context")
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
        ongoing_thread=(thread_state.thread_kind if thread_state.open else ""),
        followup_target=thread_state.source_topic,
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
