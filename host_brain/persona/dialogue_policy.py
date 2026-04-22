from __future__ import annotations

from dataclasses import dataclass, field

from host_brain.app.context.dialogue_state import DialogueStateBlock
from host_brain.app.context.topic_state import TopicStateBlock


@dataclass(frozen=True)
class DialoguePolicyDecision:
    avoid_verbatim_repeat: bool = False
    acknowledge_recent_answer: bool = False
    prefer_meta_response_over_restating: bool = False
    react_to_user_stance: bool = False
    answer_action_before_literal_question: bool = False
    topic_reopen_mode: str = "literal"
    reply_mode: str = ""
    reason_tags: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, object]:
        return {
            "avoid_verbatim_repeat": bool(self.avoid_verbatim_repeat),
            "acknowledge_recent_answer": bool(self.acknowledge_recent_answer),
            "prefer_meta_response_over_restating": bool(self.prefer_meta_response_over_restating),
            "react_to_user_stance": bool(self.react_to_user_stance),
            "answer_action_before_literal_question": bool(self.answer_action_before_literal_question),
            "topic_reopen_mode": self.topic_reopen_mode,
            "reply_mode": self.reply_mode,
            "reason_tags": list(self.reason_tags),
        }


def build_dialogue_policy(
    *,
    dialogue_state: DialogueStateBlock,
    topic_state: TopicStateBlock,
) -> DialoguePolicyDecision:
    reasons: list[str] = []
    avoid_verbatim_repeat = False
    acknowledge_recent_answer = False
    prefer_meta_response_over_restating = False
    react_to_user_stance = False
    answer_action_before_literal_question = False
    topic_reopen_mode = "literal"
    reply_mode = ""

    identity_answered = dialogue_state.recent_identity_answered or "identity" in set(topic_state.active_topics)
    boundary_answered = dialogue_state.recent_boundary_answered or any(
        key in {"relationship_boundary", "motivation"} for key in topic_state.active_topics
    )

    if dialogue_state.current_user_act == "identity_probe" and identity_answered:
        avoid_verbatim_repeat = True
        acknowledge_recent_answer = True
        prefer_meta_response_over_restating = True
        answer_action_before_literal_question = True
        topic_reopen_mode = "identity_meta"
        reply_mode = "identity_repeat_probe"
        reasons.append("identity_already_answered")
    elif dialogue_state.current_user_act == "identity_probe":
        topic_reopen_mode = "identity_direct"
        reply_mode = "identity_first_ask"
        reasons.append("identity_first_ask")

    if dialogue_state.current_user_act in {"boundary_probe", "motivation_probe"} and boundary_answered:
        avoid_verbatim_repeat = True
        acknowledge_recent_answer = True
        prefer_meta_response_over_restating = True
        answer_action_before_literal_question = True
        topic_reopen_mode = "boundary_meta"
        reply_mode = "boundary_repeat_probe"
        reasons.append("boundary_already_answered")
    elif dialogue_state.current_user_act in {"boundary_probe", "motivation_probe"}:
        topic_reopen_mode = "boundary_direct"
        reply_mode = "boundary_first_ask"
        reasons.append("boundary_first_ask")

    if dialogue_state.current_user_act == "help_continuity_probe" and dialogue_state.repeat_question_count >= 1:
        avoid_verbatim_repeat = True
        acknowledge_recent_answer = True
        answer_action_before_literal_question = True
        topic_reopen_mode = "help_meta"
        reply_mode = "help_repeat_probe"
        reasons.append("help_repeated")
    elif dialogue_state.current_user_act == "help_continuity_probe":
        topic_reopen_mode = "help_direct"
        reply_mode = "help_first_ask"
        reasons.append("help_first_ask")

    if dialogue_state.user_stance in {"challenging", "rejecting"}:
        react_to_user_stance = True
        reasons.append(f"user_stance:{dialogue_state.user_stance}")
        if dialogue_state.user_stance == "rejecting":
            reply_mode = reply_mode or "rejection_realign"
    elif dialogue_state.user_stance == "probing" and (
        acknowledge_recent_answer
        or dialogue_state.repeat_question_count >= 1
    ):
        react_to_user_stance = True
        reasons.append(f"user_stance:{dialogue_state.user_stance}")

    return DialoguePolicyDecision(
        avoid_verbatim_repeat=avoid_verbatim_repeat,
        acknowledge_recent_answer=acknowledge_recent_answer,
        prefer_meta_response_over_restating=prefer_meta_response_over_restating,
        react_to_user_stance=react_to_user_stance,
        answer_action_before_literal_question=answer_action_before_literal_question,
        topic_reopen_mode=topic_reopen_mode,
        reply_mode=reply_mode,
        reason_tags=tuple(reasons),
    )


__all__ = ["DialoguePolicyDecision", "build_dialogue_policy"]
