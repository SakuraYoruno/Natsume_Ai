from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

from host_brain.app.context.multimodal_act import coerce_multimodal_act
from host_brain.app.response_form_contract import (
    ResponseFormClass,
    classify_response_form,
    derive_render_profile_for_response_form,
    normalize_response_form,
)


_MEDIA_INPUT_KINDS = {"emoji", "sticker", "image"}
_SOCIAL_MEDIA_KINDS = {"emoji", "sticker"}
_VISUAL_ANSWER_TOPICS = {
    "meme_meaning",
    "literal_description",
    "screenshot_analysis",
    "capability_check",
}
_PHATIC_TEXT_TOPICS = {
    "greeting",
    "presence_ping",
    "light_ack",
    "phatic_punct",
    "playful_ping",
}
_AFFECTIVE_VOICE_TOPICS = {
    "greeting",
    "presence_ping",
    "light_ack",
    "assistant_reply_followup",
    "mood_reflection",
    "rejection",
}
_CLOSE_VOICE_STAGES = {"CONFIDANT", "INTIMATE", "SOULMATE"}


@dataclass(frozen=True)
class ReplyModalityDecision:
    response_form: str = "text"
    secondary_response_form: str = ""
    response_form_reason: str = "default_text"
    allow_form_only_reply: bool = False
    render_profile: str = "zh_text"
    form_class: ResponseFormClass = ResponseFormClass.TEXT
    requires_media_asset: bool = False
    allow_text_model: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "response_form": self.response_form,
            "secondary_response_form": self.secondary_response_form,
            "response_form_reason": self.response_form_reason,
            "allow_form_only_reply": bool(self.allow_form_only_reply),
            "render_profile": self.render_profile,
            "form_class": self.form_class.value,
            "requires_media_asset": bool(self.requires_media_asset),
            "allow_text_model": bool(self.allow_text_model),
        }


def _decision(
    response_form: str,
    *,
    secondary_response_form: str = "",
    reason: str,
    allow_form_only_reply: bool = False,
    requires_media_asset: bool = False,
    allow_text_model: bool = True,
) -> ReplyModalityDecision:
    normalized = normalize_response_form(response_form)
    secondary = _normalize_secondary_response_form(secondary_response_form)
    form_class = classify_response_form(normalized)
    return ReplyModalityDecision(
        response_form=normalized,
        secondary_response_form=secondary,
        response_form_reason=str(reason or "default_text").strip() or "default_text",
        allow_form_only_reply=bool(allow_form_only_reply),
        render_profile=derive_render_profile_for_response_form(normalized),
        form_class=form_class,
        requires_media_asset=bool(requires_media_asset),
        allow_text_model=bool(allow_text_model),
    )


def _normalize_secondary_response_form(raw: str | None) -> str:
    value = str(raw or "").strip().lower()
    if value in {"voice", "sticker", "image", "gif"}:
        return value
    return ""


def _topic_of(interpretation: Any) -> str:
    return str(getattr(interpretation, "literal_topic", "") or "").strip().lower()


def _interaction_move_of(interpretation: Any) -> str:
    return str(getattr(interpretation, "interaction_move", "") or "").strip().lower()


def _reason_tags_of(interpretation: Any) -> tuple[str, ...]:
    return tuple(
        str(item or "").strip().lower()
        for item in list(getattr(interpretation, "reason_tags", ()) or ())
        if str(item or "").strip()
    )


def _relationship_stage(relationship_state: Any) -> str:
    return str(getattr(relationship_state, "bond_stage", "") or "FRIEND").strip().upper() or "FRIEND"


def _is_close_enough_for_voice(relationship_state: Any) -> bool:
    stage = _relationship_stage(relationship_state)
    if stage in _CLOSE_VOICE_STAGES:
        return True
    try:
        attachment_depth = float(getattr(relationship_state, "attachment_depth", 0.0) or 0.0)
        trust = float(getattr(relationship_state, "trust", 0.0) or 0.0)
    except (TypeError, ValueError):
        return False
    return attachment_depth >= 0.72 and trust >= 0.68


def _auto_voice_enabled() -> bool:
    raw = str(os.environ.get("HOST_REPLY_MODALITY_AUTO_VOICE_ENABLED") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _is_social_reaction_act(multimodal_act: Any) -> bool:
    return (
        str(getattr(multimodal_act, "user_intent", "") or "").strip().lower() == "social_reaction"
        and not bool(getattr(multimodal_act, "requires_text_answer", True))
    )


class ReplyModalityRouter:
    """Decide the high-level output class before generation or transport.

    The router owns only the *shape* of the reply. It does not write final text,
    does not select actual sticker assets, and does not add fallback wording.
    """

    def route(
        self,
        *,
        interpretation: Any,
        relationship_state: Any,
        multimodal: Mapping[str, Any] | None = None,
        requested_response_form: str | None = None,
    ) -> ReplyModalityDecision:
        requested = normalize_response_form(requested_response_form, default="") if requested_response_form else ""
        if requested == "voice":
            return _decision("voice", reason="requested_voice", allow_text_model=True)
        if requested in {"sticker", "image", "gif"}:
            return _decision(
                requested,
                reason="requested_media",
                allow_form_only_reply=True,
                requires_media_asset=True,
                allow_text_model=False,
            )

        payload = dict(multimodal or {})
        primary_modality = str(payload.get("primary_modality") or "").strip().lower()
        multimodal_act = coerce_multimodal_act(payload.get("multimodal_act") or payload)
        act_input_kind = str(getattr(multimodal_act, "input_kind", "") or "").strip().lower()
        input_kind = primary_modality or act_input_kind
        topic = _topic_of(interpretation)
        interaction_move = _interaction_move_of(interpretation)
        reason_tags = _reason_tags_of(interpretation)
        social_reaction_act = _is_social_reaction_act(multimodal_act)

        if topic in _VISUAL_ANSWER_TOPICS:
            return _decision("text", reason="visual_answer_text")

        if topic == "emoji_social_followup":
            return _decision("text", reason="image_social_followup")

        if topic == "emoji_social_reply":
            if input_kind in _SOCIAL_MEDIA_KINDS or social_reaction_act:
                return _decision(
                    "sticker",
                    reason="social_reaction",
                    allow_form_only_reply=True,
                    requires_media_asset=True,
                    allow_text_model=False,
                )
            if input_kind == "image":
                return _decision("text", reason="image_social_reply")
            return _decision("text", reason="emoji_social_text")

        if topic == "attachment_only_no_question":
            if input_kind in _SOCIAL_MEDIA_KINDS or social_reaction_act:
                return _decision(
                    "sticker",
                    reason="social_reaction",
                    allow_form_only_reply=True,
                    requires_media_asset=True,
                    allow_text_model=False,
                )
            if input_kind == "image":
                return _decision("text", reason="visual_share_text")
            return _decision("text", reason="attachment_text")

        if input_kind == "voice" and topic in _AFFECTIVE_VOICE_TOPICS and _is_close_enough_for_voice(relationship_state):
            return _decision("voice", reason="voice_echo_close")

        if (
            _auto_voice_enabled()
            and topic in _AFFECTIVE_VOICE_TOPICS
            and _is_close_enough_for_voice(relationship_state)
            and interaction_move not in {"repair_needed", "task_request"}
            and "recent_repeat" not in reason_tags
        ):
            return _decision("voice", reason="close_affective_voice")

        if topic in _PHATIC_TEXT_TOPICS:
            return _decision("text", reason="phatic_text")

        if input_kind in _MEDIA_INPUT_KINDS and social_reaction_act:
            return _decision(
                "sticker",
                reason="social_reaction",
                allow_form_only_reply=True,
                requires_media_asset=True,
                allow_text_model=False,
            )

        return _decision("text", reason="default_text")


_router: ReplyModalityRouter | None = None


def get_reply_modality_router() -> ReplyModalityRouter:
    global _router
    if _router is None:
        _router = ReplyModalityRouter()
    return _router


def route_reply_modality(
    *,
    interpretation: Any,
    relationship_state: Any,
    multimodal: Mapping[str, Any] | None = None,
    requested_response_form: str | None = None,
) -> ReplyModalityDecision:
    return get_reply_modality_router().route(
        interpretation=interpretation,
        relationship_state=relationship_state,
        multimodal=multimodal,
        requested_response_form=requested_response_form,
    )


__all__ = [
    "ReplyModalityDecision",
    "ReplyModalityRouter",
    "get_reply_modality_router",
    "route_reply_modality",
]
