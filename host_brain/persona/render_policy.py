from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from host_brain.app.context.affect_state import AffectStateBlock, resolve_affect_state
from host_brain.app.context.dialogue_state import DialogueStateBlock, coerce_dialogue_state_block
from host_brain.app.context.drive_state import DriveStateBlock, resolve_drive_state
from host_brain.app.context.relationship_state import RelationshipStateBlock, resolve_relationship_state
from host_brain.app.context.topic_state import TopicStateBlock, coerce_topic_state_block
from host_brain.persona.affect_policy import AffectPolicyDecision, build_affect_policy
from host_brain.persona.dialogue_policy import DialoguePolicyDecision, build_dialogue_policy


def _join_nonempty(parts: list[str]) -> str:
    return " ".join(part for part in parts if str(part or "").strip()).strip()


def _build_dialogue_summary(
    *,
    dialogue_state: DialogueStateBlock,
    dialogue_policy: DialoguePolicyDecision,
    render_target_lang: str,
) -> str:
    if dialogue_policy.topic_reopen_mode == "identity_direct":
        if str(render_target_lang or "").strip().lower() == "ja":
            return "今回は最初の身元確認なので、名前か最低限の立場をはっきり答える。代名詞だけで流さない。"
        return "这轮是首次身份确认，先把名字或最必要身份说清楚，不要只回代词或空泛应付。"
    if not dialogue_policy.acknowledge_recent_answer and not dialogue_policy.react_to_user_stance:
        return ""
    lang = str(render_target_lang or "").strip().lower()
    if lang == "ja":
        parts: list[str] = []
        if dialogue_policy.acknowledge_recent_answer:
            if dialogue_state.current_user_act == "identity_probe":
                parts.append("直前に身元は答えている。")
            elif dialogue_state.current_user_act in {"boundary_probe", "motivation_probe"}:
                parts.append("直前に距離感や理由は答えている。")
            elif dialogue_state.current_user_act == "help_continuity_probe":
                parts.append("直前に支援継続の話題へ触れている。")
        if dialogue_policy.react_to_user_stance:
            if dialogue_state.user_stance == "challenging":
                parts.append("今回は相手の探りや念押しに先に応じる。")
            elif dialogue_state.user_stance == "rejecting":
                parts.append("今回は相手の拒否感に先に応じる。")
            elif dialogue_state.user_stance == "probing":
                parts.append("今回は相手の確認姿勢を先に受ける。")
        return _join_nonempty(parts)
    parts = []
    if dialogue_policy.acknowledge_recent_answer:
        if dialogue_state.current_user_act == "identity_probe":
            parts.append("最近你已经回答过身份。")
        elif dialogue_state.current_user_act in {"boundary_probe", "motivation_probe"}:
            parts.append("最近你已经回应过关系边界或动机。")
        elif dialogue_state.current_user_act == "help_continuity_probe":
            parts.append("最近你已经回应过是否继续帮忙。")
    if dialogue_policy.react_to_user_stance:
        if dialogue_state.user_stance == "challenging":
            parts.append("这轮更像是在试探或顶一下，要先回应这种动作。")
        elif dialogue_state.user_stance == "rejecting":
            parts.append("这轮用户明确在表达不喜欢，要先收住并回应态度本身。")
        elif dialogue_state.user_stance == "probing":
            parts.append("这轮是重复确认或试探，不要把它当成全新问题。")
    return _join_nonempty(parts)


def _build_topic_summary(*, topic_state: TopicStateBlock, render_target_lang: str) -> str:
    active_topics = list(topic_state.active_topics or [])
    if not active_topics:
        return ""
    lang = str(render_target_lang or "").strip().lower()
    topic_text = "、".join(active_topics[:4]) if lang != "ja" else "、".join(active_topics[:4])
    if lang == "ja":
        return f"この会話では既に {topic_text} を答えている。"
    return f"这个 session 里已经明确回答过 {topic_text}。"


def _build_control_line(
    *,
    dialogue_state: DialogueStateBlock,
    dialogue_policy: DialoguePolicyDecision,
    render_target_lang: str,
) -> str:
    reply_mode = str(dialogue_policy.reply_mode or "").strip()
    if not reply_mode:
        return ""
    lang = str(render_target_lang or "").strip().lower()
    if lang == "ja":
        if reply_mode == "identity_repeat_probe":
            return "この問いは初回ではない。相手の念押しに先に一文で応じ、名前を言い直さない。単語だけで返さない。"
        if reply_mode == "identity_first_ask":
            return "初回の身元確認。名前か最小限の立場だけを短く答える。"
        if reply_mode == "boundary_first_ask":
            return "初回の距離感や理由への問い。相手が聞いている理由にそのまま答え、名乗り直さない。"
        if reply_mode == "boundary_repeat_probe":
            return "この問いは既に答えている。理由の言い直しより、疑いに先に一文で応じる。"
        if reply_mode == "help_first_ask":
            return "初回の確認。手伝うかどうかにそのまま短く答え、名乗り直さない。"
        if reply_mode == "help_repeat_probe":
            return "この問いは既に答えている。不安に先に一文で応じ、約束を繰り返さない。"
        if reply_mode == "rejection_realign":
            return "まず語気を引いて、一文で相手の拒否感に応じる。弁解しない。"
        return ""
    if reply_mode == "identity_repeat_probe":
        return "这句不是第一次问身份。先用一整句短话接住对方的重复确认，不要只回一个词，也不要再说“我是夏目蓝”这类完整自报。"
    if reply_mode == "identity_first_ask":
        return "这是第一次问身份，只用一句自然短句说名字或最必要身份。"
    if reply_mode == "boundary_first_ask":
        return "这是第一次问关系或动机，直接回答对方在问的距离感或理由，不要先自我介绍。"
    if reply_mode == "boundary_repeat_probe":
        return "这个问题之前答过。先用一整句短话回应对方的不信任或试探，不要整段重讲理由。"
    if reply_mode == "help_first_ask":
        return "这是第一次确认会不会继续帮，直接回答这件事，不要先自我介绍。"
    if reply_mode == "help_repeat_probe":
        return "这个问题之前答过。先用一整句短话回应对方的不安，不要重新许诺一遍。"
    if reply_mode == "rejection_realign":
        return "先收住语气，用一整句短话回应对方的不喜欢，不要辩解。"
    return ""


@dataclass(frozen=True)
class RenderPolicyDecision:
    max_chars: int = 320
    max_sentences: int = 4
    min_sentences: int = 1
    allow_followup: bool = True
    allow_topic_shift: bool = True
    allow_proactive_after_reply: bool = True
    warmth_floor: float = 0.0
    warmth_ceiling: float = 1.0
    intimacy_ceiling: float = 0.5
    humor_ceiling: float = 0.35
    confession_guard: bool = False
    withdrawal_guard: bool = False
    should_be_brief: bool = False
    should_be_withdrawn: bool = False
    avoid_verbatim_repeat: bool = False
    acknowledge_recent_answer: bool = False
    react_to_user_stance: bool = False
    prefer_meta_response_over_restating: bool = False
    answer_action_before_literal_question: bool = False
    topic_reopen_mode: str = "literal"
    reply_mode: str = ""
    affect_summary: str = ""
    relationship_summary: str = ""
    dialogue_summary: str = ""
    topic_summary: str = ""
    render_policy_summary: str = ""
    control_line: str = ""
    policy_reason_tags: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_chars": int(self.max_chars),
            "max_sentences": int(self.max_sentences),
            "min_sentences": int(self.min_sentences),
            "allow_followup": bool(self.allow_followup),
            "allow_topic_shift": bool(self.allow_topic_shift),
            "allow_proactive_after_reply": bool(self.allow_proactive_after_reply),
            "warmth_floor": round(self.warmth_floor, 4),
            "warmth_ceiling": round(self.warmth_ceiling, 4),
            "intimacy_ceiling": round(self.intimacy_ceiling, 4),
            "humor_ceiling": round(self.humor_ceiling, 4),
            "confession_guard": bool(self.confession_guard),
            "withdrawal_guard": bool(self.withdrawal_guard),
            "should_be_brief": bool(self.should_be_brief),
            "should_be_withdrawn": bool(self.should_be_withdrawn),
            "avoid_verbatim_repeat": bool(self.avoid_verbatim_repeat),
            "acknowledge_recent_answer": bool(self.acknowledge_recent_answer),
            "react_to_user_stance": bool(self.react_to_user_stance),
            "prefer_meta_response_over_restating": bool(self.prefer_meta_response_over_restating),
            "answer_action_before_literal_question": bool(self.answer_action_before_literal_question),
            "topic_reopen_mode": self.topic_reopen_mode,
            "reply_mode": self.reply_mode,
            "affect_summary": self.affect_summary,
            "relationship_summary": self.relationship_summary,
            "dialogue_summary": self.dialogue_summary,
            "topic_summary": self.topic_summary,
            "render_policy_summary": self.render_policy_summary,
            "control_line": self.control_line,
            "policy_reason_tags": list(self.policy_reason_tags),
        }


def build_render_policy(
    *,
    affect_state: AffectStateBlock,
    relationship_state: RelationshipStateBlock,
    drive_state: DriveStateBlock,
    dialogue_state: DialogueStateBlock,
    topic_state: TopicStateBlock,
    render_target_lang: str = "zh",
) -> RenderPolicyDecision:
    affect_policy = build_affect_policy(
        affect_state=affect_state,
        relationship_state=relationship_state,
        drive_state=drive_state,
    )
    dialogue_policy = build_dialogue_policy(
        dialogue_state=dialogue_state,
        topic_state=topic_state,
    )

    dialogue_summary = _build_dialogue_summary(
        dialogue_state=dialogue_state,
        dialogue_policy=dialogue_policy,
        render_target_lang=render_target_lang,
    )
    topic_summary = _build_topic_summary(
        topic_state=topic_state,
        render_target_lang=render_target_lang,
    )

    render_policy_summary = _join_nonempty(
        [
            "避免原样重复上句。" if dialogue_policy.avoid_verbatim_repeat and render_target_lang != "ja" else "前と同じ言い直しを避ける。" if dialogue_policy.avoid_verbatim_repeat else "",
            "先回应对话动作，再回答题面。" if dialogue_policy.answer_action_before_literal_question and render_target_lang != "ja" else "まずやり取りの動きに応じてから答える。" if dialogue_policy.answer_action_before_literal_question else "",
            "整体保持克制，不把关系说近。" if relationship_state.boundary_softness < 0.45 and render_target_lang != "ja" else "距離を縮めすぎない。" if relationship_state.boundary_softness < 0.45 else "",
        ]
    )
    control_line = _build_control_line(
        dialogue_state=dialogue_state,
        dialogue_policy=dialogue_policy,
        render_target_lang=render_target_lang,
    )

    reasons = list(affect_policy.reason_tags) + list(dialogue_policy.reason_tags)
    return RenderPolicyDecision(
        max_chars=affect_policy.max_chars,
        max_sentences=affect_policy.max_sentences,
        min_sentences=1,
        allow_followup=affect_policy.allow_followup,
        allow_topic_shift=True,
        allow_proactive_after_reply=affect_policy.allow_proactive_after_reply,
        warmth_floor=affect_policy.warmth_floor,
        warmth_ceiling=affect_policy.warmth_ceiling,
        intimacy_ceiling=affect_policy.intimacy_ceiling,
        humor_ceiling=affect_policy.humor_ceiling,
        confession_guard=relationship_state.boundary_softness < 0.25,
        withdrawal_guard=affect_policy.withdrawal_guard,
        should_be_brief=affect_policy.should_be_brief,
        should_be_withdrawn=affect_policy.should_be_withdrawn,
        avoid_verbatim_repeat=dialogue_policy.avoid_verbatim_repeat,
        acknowledge_recent_answer=dialogue_policy.acknowledge_recent_answer,
        react_to_user_stance=dialogue_policy.react_to_user_stance,
        prefer_meta_response_over_restating=dialogue_policy.prefer_meta_response_over_restating,
        answer_action_before_literal_question=dialogue_policy.answer_action_before_literal_question,
        topic_reopen_mode=dialogue_policy.topic_reopen_mode,
        reply_mode=dialogue_policy.reply_mode,
        affect_summary="",
        relationship_summary="",
        dialogue_summary=dialogue_summary,
        topic_summary=topic_summary,
        render_policy_summary=render_policy_summary,
        control_line=control_line,
        policy_reason_tags=tuple(reasons),
    )


__all__ = ["RenderPolicyDecision", "build_render_policy"]


def build_render_policy_from_inputs(
    *,
    emotion_context: dict[str, Any] | None = None,
    body_sense: dict[str, Any] | None = None,
    offline_state: dict[str, Any] | None = None,
    silence_state: dict[str, Any] | None = None,
    bond_summary: dict[str, Any] | None = None,
    social_brain_state: dict[str, Any] | None = None,
    motive_state: dict[str, Any] | None = None,
    dialogue_state: dict[str, Any] | DialogueStateBlock | None = None,
    topic_state: dict[str, Any] | TopicStateBlock | None = None,
    render_target_lang: str = "zh",
) -> RenderPolicyDecision:
    affect_state = resolve_affect_state(
        emotion_context=emotion_context or {},
        body_sense=body_sense or {},
        offline_state=offline_state or {},
        silence_state=silence_state or {},
    )
    relationship_state = resolve_relationship_state(
        bond_summary=bond_summary or {},
        social_brain_state=social_brain_state or {},
    )
    normalized_dialogue = (
        dialogue_state
        if isinstance(dialogue_state, DialogueStateBlock)
        else coerce_dialogue_state_block(dialogue_state)
    )
    normalized_topic = (
        topic_state
        if isinstance(topic_state, TopicStateBlock)
        else coerce_topic_state_block(topic_state)
    )
    drive_state = resolve_drive_state(
        motive_state=motive_state or {},
        affect_state=affect_state,
        relationship_state=relationship_state,
        dialogue_state=normalized_dialogue,
    )
    return build_render_policy(
        affect_state=affect_state,
        relationship_state=relationship_state,
        drive_state=drive_state,
        dialogue_state=normalized_dialogue,
        topic_state=normalized_topic,
        render_target_lang=render_target_lang,
    )


__all__.append("build_render_policy_from_inputs")
