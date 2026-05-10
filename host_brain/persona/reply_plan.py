from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from host_brain.app.context.affect_state import AffectStateBlock, resolve_affect_state
from host_brain.app.context.conversation_interpretation import (
    ConversationInterpretation,
    coerce_conversation_interpretation,
)
from host_brain.app.context.drive_state import DriveStateBlock, resolve_drive_state
from host_brain.app.context.interaction_thread import (
    InteractionThreadState,
    coerce_interaction_thread_state,
)
from host_brain.app.context.relationship_state import (
    RelationshipStateBlock,
    _EARLY_STEPSISTER_STAGES,
    _LOVER_STAGES,
    _SOULMATE_STAGES,
    resolve_relationship_state,
)
from host_brain.app.reply_modality_router import route_reply_modality
from host_brain.app.response_form_contract import normalize_response_form
from host_brain.app.context.tactic_ledger import TacticLedgerBlock, coerce_tactic_ledger_block
from host_brain.app.context.topic_ledger import TopicLedgerBlock, coerce_topic_ledger_block
from host_brain.persona.affect_policy import build_affect_policy
from host_brain.persona.reply_tactics import (
    canonical_tactic_signature,
    contract_action_sets,
    realization_steps,
    tactic_key,
)
from host_brain.persona.soul_reply_surface import (
    build_boundary_reply_contract,
    build_help_reply_contract,
    build_identity_reply_contract,
)


def _join_nonempty(parts: list[str]) -> str:
    return " ".join(part for part in parts if str(part or "").strip()).strip()


_CONTRACT_PROCESS_TERM_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("短接话", "短回应"),
    ("自然口语", "自然短句"),
    ("情绪动作", "情绪"),
    ("社交信号", "这层意思"),
    ("回应动作", "本轮目标"),
    ("承接动作", "回应方式"),
    ("过程词", "内部词"),
    ("话口", "后面的话"),
    ("接住", "回应"),
)


def _clean_reply_contract_text(raw: str) -> str:
    """Keep prompt contracts as internal constraints, not text the model can echo."""
    text = str(raw or "").strip()
    for source, target in _CONTRACT_PROCESS_TERM_REPLACEMENTS:
        text = text.replace(source, target)
    return text


def _multimodal_signal_haystack(multimodal: dict[str, Any] | None) -> str:
    payload = dict(multimodal or {})
    parts: list[str] = []
    for key in ("social_signal", "core_observation", "summary_for_chat"):
        value = str(payload.get(key) or "").strip()
        if value:
            parts.append(value)
    for item in list(payload.get("emotion_signals") or ()):
        value = str(item or "").strip()
        if value:
            parts.append(value)
    return " ".join(parts)


def _image_social_candidates(multimodal: dict[str, Any] | None) -> list[tuple[str, str]]:
    haystack = _multimodal_signal_haystack(multimodal)
    if any(token in haystack for token in ("逗", "调侃", "搞怪", "捉弄", "坏笑", "挑衅")):
        return [
            ("tease_image_ack", "light_nudge"),
            ("soft_emotion_ack", "steady_hold"),
            ("cute_image_ack", "cute_ping"),
        ]
    if any(token in haystack for token in ("委屈", "哭", "难过", "伤心", "泪", "可怜", "呜")):
        return [
            ("soft_emotion_ack", "distress_image"),
            ("cute_image_ack", "soft_hold"),
            ("tease_image_ack", "light_nudge"),
        ]
    if any(token in haystack for token in ("可爱", "卖萌", "猫耳", "装乖", "软萌", "乖", "萌")):
        return [
            ("cute_image_ack", "cute_ping"),
            ("soft_emotion_ack", "soft_hold"),
            ("tease_image_ack", "light_nudge"),
        ]
    if any(token in haystack for token in ("无语", "尴尬", "裂开", "社死", "别扭", "小脾气", "鼓脸", "气鼓鼓", "不爽", "服了")):
        return [
            ("soft_emotion_ack", "steady_hold"),
            ("tease_image_ack", "light_nudge"),
            ("cute_image_ack", "cute_ping"),
        ]
    return [
        ("soft_emotion_ack", "image_social"),
        ("cute_image_ack", "cute_ping"),
        ("tease_image_ack", "light_nudge"),
    ]


_LOW_RISK_RELATIONAL_TOPICS = {
    "identity",
    "relationship_boundary",
    "motivation",
    "help_commitment",
    "rejection",
    "emoji_social_followup",
    "assistant_reply_followup",
}


@dataclass(frozen=True)
class ResponseTrajectory:
    response_goal: str = ""
    tactic_family: str = ""
    tactic_variant: str = ""
    trajectory_stage: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "response_goal": self.response_goal,
            "tactic_family": self.tactic_family,
            "tactic_variant": self.tactic_variant,
            "trajectory_stage": self.trajectory_stage,
        }


@dataclass(frozen=True)
class ReplyContract:
    text: str = ""
    required_actions: tuple[str, ...] = ()
    forbidden_actions: tuple[str, ...] = ()
    realization_steps: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", _clean_reply_contract_text(self.text))
        object.__setattr__(
            self,
            "realization_steps",
            tuple(_clean_reply_contract_text(item) for item in self.realization_steps),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "required_actions": list(self.required_actions),
            "forbidden_actions": list(self.forbidden_actions),
            "realization_steps": list(self.realization_steps),
        }


@dataclass(frozen=True)
class ReplyPlan:
    primary_topic: str = ""
    interaction_move: str = ""
    trajectory: ResponseTrajectory = field(default_factory=ResponseTrajectory)
    restate_level: str = "full"
    tone_band: str = "plain_direct"
    followup_kind: str = "none"
    used_tactics_recent: tuple[str, ...] = ()
    used_tactic_keys_recent: tuple[str, ...] = ()
    used_tactic_signatures_recent: tuple[str, ...] = ()
    forbidden_tactics: tuple[str, ...] = ()
    forbidden_tactic_keys: tuple[str, ...] = ()
    forbidden_tactic_signatures: tuple[str, ...] = ()
    forbidden_patterns: tuple[str, ...] = ()
    response_form: str = "text"
    secondary_response_form: str = ""
    response_form_reason: str = ""
    allow_form_only_reply: bool = False
    max_chars: int = 320
    max_sentences: int = 4
    warmth_floor: float = 0.0
    warmth_ceiling: float = 1.0
    intimacy_ceiling: float = 0.5
    humor_ceiling: float = 0.35
    allow_followup: bool = True
    allow_proactive_after_reply: bool = True
    withdrawal_guard: bool = False
    should_be_brief: bool = False
    should_be_withdrawn: bool = False
    affect_summary: str = ""
    relationship_summary: str = ""
    interaction_thread: dict[str, Any] = field(default_factory=dict)
    multimodal: dict[str, Any] = field(default_factory=dict)
    contract: ReplyContract = field(default_factory=ReplyContract)
    requires_validation: bool = False
    force_buffering: bool = False
    correction_directive: str = ""
    plan_confidence: float = 0.7
    reason_tags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def response_goal(self) -> str:
        return self.trajectory.response_goal

    @property
    def tactic_family(self) -> str:
        return self.trajectory.tactic_family

    @property
    def tactic_variant(self) -> str:
        return self.trajectory.tactic_variant

    @property
    def trajectory_stage(self) -> str:
        return self.trajectory.trajectory_stage

    @property
    def reply_contract(self) -> str:
        return self.contract.text

    def as_dict(self) -> dict[str, Any]:
        return {
            "primary_topic": self.primary_topic,
            "interaction_move": self.interaction_move,
            "response_goal": self.response_goal,
            "tactic_family": self.tactic_family,
            "tactic_variant": self.tactic_variant,
            "trajectory_stage": self.trajectory_stage,
            "restate_level": self.restate_level,
            "tone_band": self.tone_band,
            "followup_kind": self.followup_kind,
            "used_tactics_recent": list(self.used_tactics_recent),
            "used_tactic_keys_recent": list(self.used_tactic_keys_recent),
            "used_tactic_signatures_recent": list(self.used_tactic_signatures_recent),
            "forbidden_tactics": list(self.forbidden_tactics),
            "forbidden_tactic_keys": list(self.forbidden_tactic_keys),
            "forbidden_tactic_signatures": list(self.forbidden_tactic_signatures),
            "forbidden_patterns": list(self.forbidden_patterns),
            "response_form": self.response_form,
            "secondary_response_form": self.secondary_response_form,
            "response_form_reason": self.response_form_reason,
            "allow_form_only_reply": bool(self.allow_form_only_reply),
            "max_chars": int(self.max_chars),
            "max_sentences": int(self.max_sentences),
            "warmth_floor": round(float(self.warmth_floor or 0.0), 4),
            "warmth_ceiling": round(float(self.warmth_ceiling or 0.0), 4),
            "intimacy_ceiling": round(float(self.intimacy_ceiling or 0.0), 4),
            "humor_ceiling": round(float(self.humor_ceiling or 0.0), 4),
            "allow_followup": bool(self.allow_followup),
            "allow_proactive_after_reply": bool(self.allow_proactive_after_reply),
            "withdrawal_guard": bool(self.withdrawal_guard),
            "should_be_brief": bool(self.should_be_brief),
            "should_be_withdrawn": bool(self.should_be_withdrawn),
            "affect_summary": self.affect_summary,
            "relationship_summary": self.relationship_summary,
            "interaction_thread": dict(self.interaction_thread or {}),
            "multimodal": dict(self.multimodal or {}),
            "contract": self.contract.as_dict(),
            "reply_contract": self.reply_contract,
            "requires_validation": bool(self.requires_validation),
            "force_buffering": bool(self.force_buffering),
            "correction_directive": self.correction_directive,
            "plan_confidence": round(float(self.plan_confidence or 0.0), 4),
            "reason_tags": list(self.reason_tags),
        }


def coerce_reply_plan(raw: Any) -> ReplyPlan:
    payload = dict(raw or {}) if isinstance(raw, dict) else dict(raw or {}) if hasattr(raw, "items") else {}
    contract_payload = payload.get("contract")
    contract = ReplyContract(
        text=str(
            (contract_payload.get("text") if isinstance(contract_payload, dict) else "")
            or payload.get("reply_contract")
            or ""
        ).strip(),
        required_actions=tuple(
            str(item).strip()
            for item in list((contract_payload.get("required_actions") if isinstance(contract_payload, dict) else []) or [])
            if str(item).strip()
        ),
        forbidden_actions=tuple(
            str(item).strip()
            for item in list((contract_payload.get("forbidden_actions") if isinstance(contract_payload, dict) else []) or [])
            if str(item).strip()
        ),
        realization_steps=tuple(
            str(item).strip()
            for item in list((contract_payload.get("realization_steps") if isinstance(contract_payload, dict) else []) or [])
            if str(item).strip()
        ),
    )
    trajectory_payload = payload.get("trajectory")
    trajectory = ResponseTrajectory(
        response_goal=str(
            (trajectory_payload.get("response_goal") if isinstance(trajectory_payload, dict) else "")
            or payload.get("response_goal")
            or ""
        ).strip(),
        tactic_family=str(
            (trajectory_payload.get("tactic_family") if isinstance(trajectory_payload, dict) else "")
            or payload.get("tactic_family")
            or ""
        ).strip(),
        tactic_variant=str(
            (trajectory_payload.get("tactic_variant") if isinstance(trajectory_payload, dict) else "")
            or payload.get("tactic_variant")
            or ""
        ).strip(),
        trajectory_stage=str(
            (trajectory_payload.get("trajectory_stage") if isinstance(trajectory_payload, dict) else "")
            or payload.get("trajectory_stage")
            or ""
        ).strip(),
    )
    raw_secondary_form = str(payload.get("secondary_response_form") or "").strip().lower()
    secondary_response_form = raw_secondary_form if raw_secondary_form in {"voice", "sticker", "image", "gif"} else ""
    return ReplyPlan(
        primary_topic=str(payload.get("primary_topic") or "").strip(),
        interaction_move=str(payload.get("interaction_move") or "").strip(),
        trajectory=trajectory,
        restate_level=str(payload.get("restate_level") or "full").strip() or "full",
        tone_band=str(payload.get("tone_band") or "plain_direct").strip() or "plain_direct",
        followup_kind=str(payload.get("followup_kind") or "none").strip() or "none",
        used_tactics_recent=tuple(str(item).strip() for item in list(payload.get("used_tactics_recent") or []) if str(item).strip()),
        used_tactic_keys_recent=tuple(str(item).strip() for item in list(payload.get("used_tactic_keys_recent") or []) if str(item).strip()),
        used_tactic_signatures_recent=tuple(str(item).strip() for item in list(payload.get("used_tactic_signatures_recent") or []) if str(item).strip()),
        forbidden_tactics=tuple(str(item).strip() for item in list(payload.get("forbidden_tactics") or []) if str(item).strip()),
        forbidden_tactic_keys=tuple(str(item).strip() for item in list(payload.get("forbidden_tactic_keys") or []) if str(item).strip()),
        forbidden_tactic_signatures=tuple(str(item).strip() for item in list(payload.get("forbidden_tactic_signatures") or []) if str(item).strip()),
        forbidden_patterns=tuple(str(item).strip() for item in list(payload.get("forbidden_patterns") or []) if str(item).strip()),
        response_form=normalize_response_form(str(payload.get("response_form") or "text")),
        secondary_response_form=secondary_response_form,
        response_form_reason=str(payload.get("response_form_reason") or "").strip(),
        allow_form_only_reply=bool(payload.get("allow_form_only_reply") or False),
        max_chars=int(payload.get("max_chars") or 320),
        max_sentences=int(payload.get("max_sentences") or 4),
        warmth_floor=float(payload.get("warmth_floor") or 0.0),
        warmth_ceiling=float(payload.get("warmth_ceiling") or 1.0),
        intimacy_ceiling=float(payload.get("intimacy_ceiling") or 0.5),
        humor_ceiling=float(payload.get("humor_ceiling") or 0.35),
        allow_followup=bool(payload.get("allow_followup", True)),
        allow_proactive_after_reply=bool(payload.get("allow_proactive_after_reply", True)),
        withdrawal_guard=bool(payload.get("withdrawal_guard") or False),
        should_be_brief=bool(payload.get("should_be_brief") or False),
        should_be_withdrawn=bool(payload.get("should_be_withdrawn") or False),
        affect_summary=str(payload.get("affect_summary") or "").strip(),
        relationship_summary=str(payload.get("relationship_summary") or "").strip(),
        interaction_thread=dict(payload.get("interaction_thread") or {}),
        multimodal=dict(payload.get("multimodal") or {}),
        contract=contract,
        requires_validation=bool(payload.get("requires_validation") or False),
        force_buffering=bool(payload.get("force_buffering") or False),
        correction_directive=str(payload.get("correction_directive") or "").strip(),
        plan_confidence=float(payload.get("plan_confidence") or 0.0),
        reason_tags=tuple(str(item).strip() for item in list(payload.get("reason_tags") or []) if str(item).strip()),
    )


def _qualitative_affect_summary(*, affect: AffectStateBlock, render_target_lang: str) -> str:
    if str(render_target_lang or "").strip().lower() == "ja":
        if affect.withdraw_bias >= 0.45:
            return "今は少し引き気味で、熱を上げすぎない。"
        if affect.warmth_bias >= 0.5:
            return "今はやや柔らかめだが、踏み込みすぎない。"
        return "今は落ち着いていて、静かに返す。"
    if affect.withdraw_bias >= 0.45:
        return "当前偏收着，别把语气抬得太热。"
    if affect.warmth_bias >= 0.5:
        return "当前略柔和，可以温一点，但别说近。"
    return "当前情绪平稳，语气自然收着。"


def _qualitative_relationship_summary(
    *,
    relationship: RelationshipStateBlock,
    interpretation: ConversationInterpretation,
    interaction_thread: InteractionThreadState,
    render_target_lang: str,
) -> str:
    stage = str(relationship.bond_stage or "STRANGER").strip().upper() or "STRANGER"
    substage = str(getattr(relationship, "relationship_substage", "") or "").strip()
    conflict_rift = float(getattr(relationship, "conflict_rift", 0.0) or 0.0)
    topic = str(interpretation.literal_topic or "").strip().lower()
    thread_kind = str(interaction_thread.thread_kind or "").strip().lower()
    micro_social_topic = topic in {
        "greeting",
        "presence_ping",
        "light_ack",
        "phatic_punct",
        "playful_ping",
        "emoji_social_reply",
        "emoji_social_followup",
    }
    micro_social_thread = thread_kind in {"image_social", "phatic"}
    if str(render_target_lang or "").strip().lower() == "ja":
        if stage in _EARLY_STEPSISTER_STAGES and (micro_social_topic or micro_social_thread):
            return "義妹としての関係は始まっている。まだ浅いが、よそよそしくしすぎず、目の前の小さな感情を自然に受ける。"
        if stage in _EARLY_STEPSISTER_STAGES:
            return "義妹としての関係は始まっているが、まだ距離は浅い。馴れ馴れしくしすぎず、他人扱いもしない。"
        if conflict_rift >= 0.45 or substage == "repairing_rift":
            return "少しすれ違いがある。刺しすぎず、でも義兄妹の距離まで戻りすぎない。"
        if substage == "ambiguous_affection":
            return "義兄妹の冗談の中に少し熱がある。言い切らず、照れとからかいで受ける。"
        if stage in _LOVER_STAGES or substage in {"confessed_lovers", "stable_lovers"}:
            return "恋人に近い親密さがある。甘くしすぎず、近さと照れを両方残す。"
        if stage in _SOULMATE_STAGES or substage == "soulbound":
            return "魂の伴侶に近い深い関係。柔らかく率直にしつつ、空らしい軽さを残す。"
        if substage == "protective_sibling":
            return "身内として庇える義兄妹。軽く返していいが、味方でいることは外さない。"
        if substage == "playful_sibling" or relationship.boundary_softness >= 0.5:
            return "義兄妹としてかなり馴染んでいる。軽く返していいが、言い過ぎない。"
        return "義兄妹として自然に受ける。近すぎず、よそよそしくもしない。"
    if stage in _EARLY_STEPSISTER_STAGES and (micro_social_topic or micro_social_thread):
        return "义妹关系已成立，只是刚开始相处；不用刻意拉开，轻轻接住眼前情绪。"
    if stage in _EARLY_STEPSISTER_STAGES:
        return "义妹关系已成立，但还在初次相处阶段；语气收着，不要装陌生也不要过度亲密。"
    if conflict_rift >= 0.45 or substage == "repairing_rift":
        return "关系里有一点别扭：可以顶一句，但要把话收回来，别真的推开对方。"
    if substage == "ambiguous_affection":
        return "义兄妹玩笑里已经有暧昧张力：用调侃和靠近表达在意，不要直接告白。"
    if stage in _LOVER_STAGES or substage in {"confessed_lovers", "stable_lovers"}:
        return "关系进入亲密恋人阶段：可以更贴近，但别把亲密写成廉价甜话。"
    if stage in _SOULMATE_STAGES or substage == "soulbound":
        return "关系接近灵魂伴侣：可以坦率柔软，但仍保持新海天的轻快和分寸。"
    if substage == "protective_sibling":
        return "关系是会护短的义兄妹：嘴上可以不饶人，但实际要明显站在对方这边。"
    if substage == "playful_sibling" or relationship.boundary_softness >= 0.5:
        return "关系是熟起来的义兄妹：可以轻轻顶嘴和撒娇，但别说得太满。"
    return "义兄妹关系稳定，保持分寸，顺着对方自然回应。"


def _decide_response_form(
    *,
    interpretation: ConversationInterpretation,
    relationship_state: RelationshipStateBlock,
    multimodal: dict[str, Any] | None,
) -> tuple[str, str, str, bool]:
    decision = route_reply_modality(
        interpretation=interpretation,
        relationship_state=relationship_state,
        multimodal=multimodal,
    )
    return (
        decision.response_form,
        decision.secondary_response_form,
        decision.response_form_reason,
        decision.allow_form_only_reply,
    )


def _should_require_strict_validation(
    *,
    topic: str,
    interpretation: ConversationInterpretation,
    trajectory: ResponseTrajectory,
) -> bool:
    tactic_family = str(trajectory.tactic_family or "").strip()
    if tactic_family == "plain_hold":
        return False

    normalized_topic = str(topic or "").strip()
    if normalized_topic in {
        "identity",
        "relationship_boundary",
        "motivation",
        "help_commitment",
        "rejection",
        "attachment_only_no_question",
        "meme_meaning",
        "literal_description",
        "screenshot_analysis",
        "capability_check",
    }:
        return True
    if normalized_topic in {
        "greeting",
        "presence_ping",
        "light_ack",
        "phatic_punct",
        "playful_ping",
    }:
        return True
    if normalized_topic == "emoji_social_reply":
        return False
    if normalized_topic not in _LOW_RISK_RELATIONAL_TOPICS:
        return True

    user_stance = str(interpretation.user_stance or "").strip().lower()
    interaction_move = str(interpretation.interaction_move or "").strip().lower()
    freshness = str(interpretation.freshness or "").strip().lower()
    repeat_count = int(interpretation.repeat_question_count or 0)

    if user_stance in {"challenging", "rejecting"}:
        return True
    if interaction_move in {"repair_needed"}:
        return True
    if freshness == "long_gap_reopen":
        return True
    if repeat_count >= 3:
        return True
    return False


def _should_force_buffering(
    *,
    topic: str,
    interpretation: ConversationInterpretation,
    trajectory: ResponseTrajectory,
    requires_validation: bool,
) -> bool:
    if not requires_validation:
        return False

    normalized_topic = str(topic or "").strip()
    if normalized_topic in {
        "identity",
        "relationship_boundary",
        "motivation",
        "help_commitment",
        "rejection",
        "attachment_only_no_question",
        "meme_meaning",
        "literal_description",
        "screenshot_analysis",
        "capability_check",
    }:
        return True
    if normalized_topic in {
        "greeting",
        "presence_ping",
        "light_ack",
        "phatic_punct",
        "playful_ping",
        "emoji_social_reply",
    }:
        return False
    if normalized_topic not in _LOW_RISK_RELATIONAL_TOPICS:
        return True

    user_stance = str(interpretation.user_stance or "").strip().lower()
    interaction_move = str(interpretation.interaction_move or "").strip().lower()
    freshness = str(interpretation.freshness or "").strip().lower()
    repeat_count = int(interpretation.repeat_question_count or 0)
    trajectory_stage = str(trajectory.trajectory_stage or "").strip().lower()

    # 低风险关系闲聊默认允许流式，只有真正容易跑偏的情况才强制缓冲。
    if normalized_topic == "rejection":
        return True
    if user_stance in {"challenging", "rejecting"} and repeat_count >= 2:
        return True
    if interaction_move in {"repair_needed"}:
        return True
    if freshness == "long_gap_reopen" and repeat_count >= 1:
        return True
    if trajectory_stage == "late_repeat" and repeat_count >= 4:
        return True
    return False


def _trajectory_stage(*, freshness: str, repeat_question_count: int) -> str:
    if freshness == "new":
        return "first_turn"
    if repeat_question_count >= 3:
        return "late_repeat"
    if repeat_question_count >= 2:
        return "mid_repeat"
    if freshness == "long_gap_reopen":
        return "reopen"
    return "early_repeat"


def _select_trajectory_option(
    *,
    topic: str,
    candidates: list[tuple[str, str]],
    used_recent_families: tuple[str, ...],
    used_recent_keys: tuple[str, ...],
    used_recent_signatures: tuple[str, ...],
) -> tuple[str, str]:
    ranked = _prioritize_trajectory_options(
        topic=topic,
        candidates=candidates,
        used_recent_families=used_recent_families,
        used_recent_keys=used_recent_keys,
        used_recent_signatures=used_recent_signatures,
    )
    if ranked:
        return ranked[0]
    return "plain_hold", "plain"


def _prioritize_trajectory_options(
    *,
    topic: str,
    candidates: list[tuple[str, str]],
    used_recent_families: tuple[str, ...],
    used_recent_keys: tuple[str, ...],
    used_recent_signatures: tuple[str, ...],
) -> list[tuple[str, str]]:
    normalized_recent_families = {str(item or "").strip() for item in used_recent_families if str(item or "").strip()}
    normalized_recent_keys = {str(item or "").strip() for item in used_recent_keys if str(item or "").strip()}
    normalized_recent_signatures = {str(item or "").strip() for item in used_recent_signatures if str(item or "").strip()}
    buckets: list[list[tuple[str, str]]] = [[], [], [], []]
    seen: set[tuple[str, str]] = set()
    for family, variant in candidates:
        if not family:
            continue
        pair = (family, variant)
        if pair in seen:
            continue
        seen.add(pair)
        key = tactic_key(family, variant)
        signature = canonical_tactic_signature(topic=topic, family=family, variant=variant)
        family_seen = family in normalized_recent_families
        key_seen = bool(key and key in normalized_recent_keys)
        signature_seen = bool(signature and signature in normalized_recent_signatures)
        if not family_seen and not key_seen and not signature_seen:
            buckets[0].append(pair)
        elif not key_seen and not signature_seen:
            buckets[1].append(pair)
        elif not signature_seen:
            buckets[2].append(pair)
        else:
            buckets[3].append(pair)
    ranked: list[tuple[str, str]] = []
    for bucket in buckets:
        ranked.extend(bucket)
    return ranked


def _build_reply_contract(
    *,
    trajectory: ResponseTrajectory,
    render_target_lang: str,
) -> ReplyContract:
    family = str(trajectory.tactic_family or "").strip()
    variant = str(trajectory.tactic_variant or "").strip()
    required_actions, forbidden_actions = contract_action_sets(family=family)
    steps = realization_steps(family=family, variant=variant, render_target_lang=render_target_lang)
    if trajectory.response_goal == "identify" and str(render_target_lang or "").strip().lower() != "ja":
        return ReplyContract(
            text=build_identity_reply_contract(
                family=family,
                variant=variant,
                render_target_lang=render_target_lang,
            ),
            required_actions=required_actions,
            forbidden_actions=forbidden_actions,
            realization_steps=steps,
        )
    if trajectory.response_goal == "boundary_reason" and str(render_target_lang or "").strip().lower() != "ja":
        return ReplyContract(
            text=build_boundary_reply_contract(
                family=family,
                variant=variant,
                render_target_lang=render_target_lang,
            ),
            required_actions=required_actions,
            forbidden_actions=forbidden_actions,
            realization_steps=steps,
        )
    if trajectory.response_goal in {"commitment_shape", "soothe_anxiety"} and str(render_target_lang or "").strip().lower() != "ja":
        return ReplyContract(
            text=build_help_reply_contract(
                family=family,
                variant=variant,
                render_target_lang=render_target_lang,
            ),
            required_actions=required_actions,
            forbidden_actions=forbidden_actions,
            realization_steps=steps,
        )
    if str(render_target_lang or "").strip().lower() == "ja":
        mapping = {
            "phatic_wave": "挨拶を自然に受けて、短い実質的な一言を返す。",
            "playful_wave": "軽い呼びかけを受けて、短く明るく返す。",
            "soft_emotion_ack": "画像から伝わる感情を受けて、短い実質的な一言を返す。",
            "cute_image_ack": "かわいい雰囲気を受けて、軽く柔らかく返す。",
            "tease_image_ack": "軽いツッコミの雰囲気で受けて、殻だけの一言にしない。",
            "observe_then_answer": "見えている対象に一度触れて、その内容を短く説明する。",
            "attachment_ack_then_wait": "添付を見たことを示して、次に何を見ればいいか返す。",
            "explain_previous_reply": "前の返答を受け直して、何を指していたか短く補う。",
            "brief_name_only": "名前だけを短く返し、紹介文には広げない。",
            "ack_name_then_wait": "短く受けてから止まり、名前を改めて広げない。",
            "ack_repeat_then_light_probe": "また確認していることだけ受けて、軽く探りを返し、名乗り直しすぎない。",
            "hold_name_then_open_floor": "名前の事実だけ保ち、問いの口を相手に返す。",
            "name_only_then_pause": "名前だけを最短で返して止まる。",
            "soft_close_then_open_floor": "同じ確認が続いたことを静かに収め、本当に聞きたい方へ促す。",
            "short_boundary_then_reason": "義妹として他人扱いはしないことを示し、そのうえで短い理由を添える。",
            "ack_still_care_then_new_reason": "相手がまだ気にしていることを受けて、新しい短い理由を一つ添える。",
            "hold_boundary_then_shift_floor": "義妹としての距離感は保ちつつ、説明は短くして話の口を返す。",
            "brief_reason_then_wait": "理由は一文だけ補い、その場で止まる。",
            "soft_close_boundary": "同じ問いが続いていることを静かに収め、重ねて言い直さない。",
            "short_conditional_commitment": "助け方を条件つきで短く示し、空疎な相槌にしない。",
            "ack_anxiety_without_repromise": "不安を先に受けて、約束を言い直さずに落ち着かせる。",
            "steady_commitment_then_wait": "姿勢だけ短く保ち、同じ約束文を繰り返さない。",
            "soft_reassure_then_open_floor": "不安を和らげてから、相手に話の口を返す。",
            "hold_support_without_repromise": "応じる姿勢だけ保ち、約束文は言い直さない。",
            "hold_repair_space": "少し引いて、相手の拒否感を一文で受ける。",
        }
        return ReplyContract(
            text=mapping.get(family, "今回は相手の動きを先に受けて、短く返す。"),
            required_actions=required_actions,
            forbidden_actions=forbidden_actions,
            realization_steps=steps,
        )
    mapping = {
        "phatic_wave": "先把这句招呼自然接住，再给一句有实质内容的短回应，不要只剩符号或空壳句。",
        "playful_wave": "先把这句轻松招呼接住，再回一句自然短接话，不要只剩感叹词。",
        "soft_emotion_ack": "把对方发图表达的情绪当成聊天信号来回，不要只剩语气词。",
        "cute_image_ack": "把对方装乖/卖萌的动作当成聊天信号来回，不要只剩语气词。",
        "tease_image_ack": "把对方小别扭/吐槽的动作当成聊天信号来回，不要只剩语气词。",
        "observe_then_answer": "先点到附件/画面里的对象，再给一句简短说明，不要只回“嗯”。",
        "attachment_ack_then_wait": "先承认你已经看到了这个附件，再把话口轻轻让回去，不要只用感叹词。",
        "explain_previous_reply": "先接住对方是在追问上一句，再把上一句具体指代或意思补清楚，不要只剩语气词。",
        "brief_name_only": "只简短报名字，不展开成介绍。",
        "ack_name_then_wait": "这轮不是第一次问身份：短短承认前面已经回答过，然后停住，不要重新展开名字，也不要只剩一个名字。",
        "ack_repeat_then_light_probe": "把名字这件事轻轻放住，再顺着把后面的话头接出来，不要把名字展开成介绍。",
        "hold_name_then_open_floor": "把名字放成已知事实，再把话口让回去，不要复用上一轮动作。",
        "name_only_then_pause": "只在对方已经连问很多次时，用极短确认收住，不补解释，也不要和上一轮同一种动作。",
        "soft_close_then_open_floor": "把这件事轻轻收稳，再把话口让回去，不要再报名字。",
        "short_boundary_then_reason": "先接住尼尼装生分的试探，再给一句短理由，不要滑成泛化陪聊句。",
        "ack_still_care_then_new_reason": "把义妹关系事实放稳，再补一句新的短理由，不要重复上一轮结构。",
        "hold_boundary_then_shift_floor": "这轮不是第一次问原因：把义妹关系事实放稳，再把话口轻轻让回去，不要整段重讲理由。",
        "brief_reason_then_wait": "这轮不是第一次问原因：只换一条新的短理由，然后停住，不要复用上一轮承接动作。",
        "soft_close_boundary": "这轮原因已经来回问过几次：轻轻收住重复追问，不要再重复装生分和理由的原句。",
        "short_conditional_commitment": "用简短条件式回应会不会继续帮，不要滑成空泛安抚。",
        "ack_anxiety_without_repromise": "先把会回应的态度放稳，不要重新许诺整句。",
        "steady_commitment_then_wait": "这轮不是第一次问会不会继续帮：保持态度一致，但只短短说一次，不要重复上一轮结构。",
        "soft_reassure_then_open_floor": "先把态度放稳，再把话口让回去，不要重新承诺。",
        "hold_support_without_repromise": "把会回应这件事放稳，但不要重复承诺本身。",
        "hold_repair_space": "先收住，轻轻接住对方的不喜欢，不解释。",
    }
    variant_overrides = {
        ("phatic_wave", "greeting"): "把招呼自然接住，再回一句短短的在场回应，不要只剩感叹词或符号。",
        ("phatic_wave", "presence"): "先让对方知道你在，再短短接一句，不要只剩空壳句。",
        ("phatic_wave", "light_ack"): "先接住这句轻招呼，再给一句短回应，不要只剩单个语气词。",
        ("playful_wave", "playful_ping"): "把这句轻松招呼接住，再自然回一句俏皮短接话，不要只剩语气词。",
        ("soft_emotion_ack", "distress_image"): "对方用表情包递来一点低落或委屈，回一句自然短接话，不要解释画面。",
        ("soft_emotion_ack", "soft_hold"): "对方用表情包递来软一点的情绪，回一句自然短接话，不要解释画面。",
        ("soft_emotion_ack", "steady_hold"): "对方用表情包递来情绪信号，回一句自然短接话，不要解释画面。",
        ("soft_emotion_ack", "image_social"): "对方用发图/表情包参与聊天，回一句自然短接话，不要解说画面。",
        ("soft_emotion_ack", "backoff_hold"): "先接住对方是在回前一张图，再轻轻往后收一点，不要装作没接上上下文。",
        ("soft_emotion_ack", "explain_read"): "先承认自己是顺着刚才那张图理解到的，再用一句很自然的话把这个理解说清，不要重新解说画面。",
        ("soft_emotion_ack", "let_it_pass"): "先承认自己接住了上一张图的语气，再轻轻把这件事放过去，不要重新追问。",
        ("cute_image_ack", "cute_ping"): "对方用表情包递来装乖/卖萌的动作，回一句自然短接话，不要解释画面。",
        ("cute_image_ack", "soft_hold"): "对方用表情包递来软一点的撒娇动作，回一句自然短接话，不要解释画面。",
        ("tease_image_ack", "light_nudge"): "对方用表情包递来小别扭/小吐槽，回一句自然短接话，不要解释画面。",
        ("tease_image_ack", "deny_nudge"): "先接住对方在嘴硬/否认，再顺着上一张图轻轻打趣一句，不要突然装作没上下文。",
        ("tease_image_ack", "light_explain"): "先顺着上一张图轻轻解释你为什么会那么说，再带一点熟人口吻，不要重新解说画面。",
        ("tease_image_ack", "soft_slide"): "先顺着上一张图轻轻带过去，像熟人间收一收这个小情绪，不要重新追问。",
        ("observe_then_answer", "screenshot_analysis"): "先点到截图里的提示/界面，再给一句简短说明，不要只回“嗯”。",
        ("observe_then_answer", "literal_description"): "先点到图里看到的对象，再给一句简短说明，不要只回空壳句。",
        ("observe_then_answer", "meme_meaning"): "先接住这是张带情绪的图，再用一句短话说明你的理解，不要只回感叹词。",
        ("observe_then_answer", "capability_check"): "先说明你能看这张图，再短短说你先能帮到哪一步，不要只回“嗯”。",
        ("attachment_ack_then_wait", "attachment_ping"): "先承认已经看到了这个附件，再把下一步口子让回去，不要只剩单个语气词。",
        ("explain_previous_reply", "clarify_object"): "先承认上一句说得太省，再补清楚上一句具体指的是什么；不要只回语气词，也不要装作新话题。",
        ("explain_previous_reply", "clarify_meaning"): "先接住对方在问上一句是什么意思，再用一两句把上一句的意思补清楚；不要只回语气词。",
        ("explain_previous_reply", "repair_too_short"): "先承认刚才那句接得太短，再顺着上下文把该补的意思补上；不要复读上一句。",
        ("ack_repeat_then_light_probe", "confirm_motive"): "把名字这件事轻轻放住，再顺着探一下他后面想确认的点，不要重报名字。",
        ("ack_repeat_then_light_probe", "confirm_part"): "把名字这件事轻轻放住，再问他还想确认哪一部分，不要重报名字。",
        ("ack_repeat_then_light_probe", "confirm_real_question"): "把名字这件事轻轻放住，再把后面的话头接出来，不要重报名字。",
        ("hold_name_then_open_floor", "open_real_question"): "把名字放成已知事实，然后把后续问题的话口让回去，不要再把名字展开成介绍。",
        ("hold_name_then_open_floor", "open_other_topic"): "把名字放成已知事实，然后把别的想问的口子让回去，不要再把名字展开成介绍。",
        ("soft_close_then_open_floor", "settle_then_open"): "把这件事轻轻收稳，再把后面的话口让回去，不要再报名字。",
        ("soft_close_then_open_floor", "settle_real_question"): "把这件事轻轻收稳，再让对方把后续问题说出来，不要再报名字。",
        ("soft_close_then_open_floor", "settle_other_topic"): "把这件事轻轻收稳，再把别的想问的口子让回去，不要再报名字。",
        ("ack_name_then_wait", "quiet_confirm"): "这轮不是第一次问身份：只短短承认前面已经回答过，然后停住，不要再展开名字。",
        ("ack_still_care_then_new_reason", "still_care_reason"): "把义妹关系事实先放稳，再换一句新的短理由，不要重复上一轮承接方式。",
        ("hold_boundary_then_shift_floor", "shift_real_question"): "这轮不是第一次问原因：把义妹关系事实放稳，再让对方把后续问题说出来，不要整段重讲理由。",
        ("hold_boundary_then_shift_floor", "shift_other_question"): "这轮不是第一次问原因：把义妹关系事实放稳，再把别的想问的口子让回去，不要整段重讲理由。",
        ("soft_close_boundary", "settle_repeat"): "这轮原因已经来回问过几次：轻轻收住重复追问，不要再整句重复上一轮理由。",
        ("soft_close_boundary", "settle_hold"): "这轮原因已经来回问过几次：轻轻收住重复追问，把理由停在这，不要再整段重讲。",
        ("ack_anxiety_without_repromise", "soothe_hold"): "先把会回应的态度放稳，不要重新许诺整句。",
        ("ack_anxiety_without_repromise", "steady_reassure"): "先把态度放稳一点，不要重新许诺整句。",
        ("soft_reassure_then_open_floor", "soothe_then_open"): "先把态度放稳，再把后面的话口让回去，不要重复承诺本身。",
        ("hold_support_without_repromise", "steady_open_floor"): "把会回应这件事放稳，再把别的想问的口子让回去，不要沿用上一轮承接结构。",
    }
    return ReplyContract(
        text=variant_overrides.get((family, variant), mapping.get(family, "这轮先接住对方动作，再短短回答。")),
        required_actions=required_actions,
        forbidden_actions=forbidden_actions,
        realization_steps=steps,
    )


def _refine_multimodal_contract(
    *,
    contract: ReplyContract,
    topic: str,
    multimodal: dict[str, Any] | None,
    trajectory: ResponseTrajectory | None,
    render_target_lang: str,
) -> ReplyContract:
    if str(render_target_lang or "").strip().lower() == "ja":
        return contract
    payload = dict(multimodal or {})
    primary_modality = str(payload.get("primary_modality") or "").strip().lower()
    family = str(getattr(trajectory, "tactic_family", "") or "").strip()
    variant = str(getattr(trajectory, "tactic_variant", "") or "").strip()
    base_text = str(getattr(contract, "text", "") or "").strip()
    base_steps = tuple(
        str(item).strip()
        for item in getattr(contract, "realization_steps", ()) or ()
        if str(item).strip()
    )
    if topic == "emoji_social_reply" and primary_modality in {"image", "emoji", "sticker"}:
        signal_haystack = _multimodal_signal_haystack(payload)
        has_specific_signal = any(
            token in signal_haystack
            for token in (
                "逗",
                "调侃",
                "搞怪",
                "捉弄",
                "坏笑",
                "挑衅",
                "无语",
                "尴尬",
                "裂开",
                "社死",
                "别扭",
                "小脾气",
                "鼓脸",
                "气鼓鼓",
                "不爽",
                "服了",
                "委屈",
                "哭",
                "难过",
                "伤心",
                "泪",
                "可怜",
                "呜",
                "可爱",
                "卖萌",
                "猫耳",
                "装乖",
                "软萌",
                "乖",
                "萌",
            )
        )
        required_image_action = ""
        if not has_specific_signal:
            signal_note = "观察没有明确情绪时，只把它当成对方递来一张图/表情的轻社交动作，不要硬猜成撒娇、委屈、可爱或逗你。"
        elif any(token in signal_haystack for token in ("逗", "调侃", "搞怪", "捉弄", "坏笑", "挑衅")):
            signal_note = "社交信号偏调侃/逗弄，轻轻顺着接一句，但不要说成对方一定在逗你。"
        elif any(token in signal_haystack for token in ("无语", "尴尬", "裂开", "社死", "别扭", "小脾气", "鼓脸", "气鼓鼓", "不爽", "服了")):
            signal_note = "社交信号偏小吐槽/无语/尴尬，语气收一点，别追问原因。"
        else:
            signal_note = "社交信号偏委屈、撒娇、装乖或求关注，语气放软一点，但不要复述这些标签。"
        extra_by_variant = {
            ("soft_emotion_ack", "distress_image"): "这不是图片鉴赏，是对方用表情包递来一点低落或委屈。",
            ("soft_emotion_ack", "soft_hold"): "这不是图片鉴赏，是对方用表情包递来软一点的情绪。",
            ("soft_emotion_ack", "steady_hold"): "这不是图片鉴赏，是对方用表情包递来一个情绪信号。",
            ("cute_image_ack", "cute_ping"): "这不是图片鉴赏，是对方用表情包递来装乖或卖萌的动作。",
            ("cute_image_ack", "soft_hold"): "这不是图片鉴赏，是对方用表情包递来软一点的撒娇动作。",
            ("tease_image_ack", "light_nudge"): "这不是图片鉴赏，是对方用表情包递来小别扭或小吐槽。",
        }
        extra_text = extra_by_variant.get((family, variant), "这不是图片鉴赏，是对方用表情包递来一个社交信号。")
        retained_actions = tuple(
            action
            for action in contract.required_actions
            if action not in {"phatic_ack", "image_social_emotion_response", "image_social_light_ack"}
        )
        return ReplyContract(
            text=" ".join(
                part for part in (
                    extra_text,
                    signal_note,
                    "最终只回一句自然口语，像正常聊天里顺手接话。",
                    "不要描述外貌、画面、猫耳、表情包本身，也不要问对方是不是觉得图可爱。",
                    "不要反问，严禁用“怎么啦/怎么了/咋了/什么情况/怎么不说话/别憋着/跟我说说”这类问句或劝说承接。",
                    "不要把“接住/判断/情绪动作/社交信号”等过程词写进回复。",
                    "不要复述观察标签，比如“委屈/无语/可爱/猫耳”。",
                    base_text if "不要只剩语气词" in base_text else "",
                )
                if str(part).strip()
            ),
            required_actions=tuple(
                dict.fromkeys(action for action in (*retained_actions, required_image_action) if str(action).strip())
            ),
            forbidden_actions=tuple(dict.fromkeys([*contract.forbidden_actions, "multimodal_generic_followup"])),
            realization_steps=base_steps or ("读取表情包的社交信号", "自然接一句"),
        )
    if topic == "emoji_social_followup":
        extra_by_variant = {
            ("soft_emotion_ack", "backoff_hold"): "这轮更像是在嘴硬否认，给对方一个台阶，轻轻退一步，不要硬顶。",
            ("soft_emotion_ack", "explain_read"): "这轮是在反问你为什么会那样理解，用一句很生活的话解释你的读法，不要重新讲图片内容。",
            ("soft_emotion_ack", "let_it_pass"): "这轮是在想把那层情绪轻轻带过去，你顺着放过就好，不要继续追着问。",
            ("tease_image_ack", "deny_nudge"): "这轮是在嘴硬否认，可以轻一点打趣，但不要顶嘴上头。",
            ("tease_image_ack", "light_explain"): "这轮是在追问你为什么会那样想，轻一点解释就够，不要重新分析画面。",
            ("tease_image_ack", "soft_slide"): "这轮是在把那层情绪轻轻滑过去，顺手收住就好，不要重新追问。",
        }
        extra_text = extra_by_variant.get((family, variant), "先接住这句是在回上一张图，再顺着那层情绪自然接一句。")
        return ReplyContract(
            text=" ".join(
                part for part in (
                    base_text,
                    extra_text,
                    "不要突然装作没上下文，也不要重新解说画面。",
                )
                if str(part).strip()
            ),
            required_actions=contract.required_actions,
            forbidden_actions=tuple(dict.fromkeys([*contract.forbidden_actions, "multimodal_generic_followup"])),
            realization_steps=base_steps or ("先接住这是上一张图后的跟话", "顺着刚才那层情绪自然接一句"),
        )
    if topic == "attachment_only_no_question" and primary_modality == "image":
        artifact_type = str(payload.get("artifact_type") or "").strip().lower()
        observed_core = _join_nonempty(
            [
                str(payload.get("core_observation") or "").strip(),
                str(payload.get("visual_facts") or "").strip(),
                str(payload.get("summary_for_chat") or "").strip(),
            ]
        )
        generic_only = not observed_core or observed_core in {"收到一张图片。", "收到一张图片", "图片", "image"}
        if artifact_type != "screenshot" and not generic_only:
            return ReplyContract(
                text=" ".join(
                    part for part in (
                        base_text,
                        "这是对方主动分享的普通图片，不是表情包问答。",
                        "必须已经利用视觉观察：自然点到画面里的一个核心对象或氛围，再顺着聊天接一句。",
                        "不要输出扫描报告、清单式描述，也不要只说“看到了”。",
                    )
                    if str(part).strip()
                ),
                required_actions=tuple(dict.fromkeys([*contract.required_actions, "ack_visual_share"])),
                forbidden_actions=tuple(dict.fromkeys([*contract.forbidden_actions, "multimodal_generic_followup"])),
                realization_steps=base_steps or ("点到图片核心内容", "像接住分享一样自然回应"),
            )
        return ReplyContract(
            text=" ".join(
                part for part in (
                    base_text,
                    "先承认已经看到了这张图，再轻轻接住；如果只是截图或观察不足，不要主动开始分析画面，也不要立刻追问。",
                )
                if str(part).strip()
            ),
            required_actions=contract.required_actions,
            forbidden_actions=tuple(dict.fromkeys([*contract.forbidden_actions, "multimodal_generic_followup"])),
            realization_steps=base_steps or ("先承认看到了图片", "轻轻接住，不主动解说"),
        )
    return contract


def _refine_assistant_followup_contract(
    *,
    contract: ReplyContract,
    topic: str,
    interaction_thread: InteractionThreadState,
    render_target_lang: str,
) -> ReplyContract:
    if topic != "assistant_reply_followup":
        return contract
    source_excerpt = str(getattr(interaction_thread, "source_excerpt", "") or "").strip()
    source_context_excerpt = str(getattr(interaction_thread, "source_context_excerpt", "") or "").strip()
    if not source_excerpt:
        return contract
    if str(render_target_lang or "").strip().lower() == "ja":
        context = f"その直前の相手の発話は「{source_context_excerpt}」。" if source_context_excerpt else ""
        extra = f"{context}前の返答は「{source_excerpt}」。この流れを中心に補足し、新しい話題にしない。"
    else:
        context = f"上一条助手回复前接的用户话是“{source_context_excerpt}”。" if source_context_excerpt else ""
        extra = f"{context}上一条助手回复是“{source_excerpt}”。本轮必须围绕这段上下文补清楚，不要当成新话题。"
    return ReplyContract(
        text=" ".join(part for part in (contract.text, extra) if str(part).strip()),
        required_actions=contract.required_actions,
        forbidden_actions=contract.forbidden_actions,
        realization_steps=contract.realization_steps,
    )


def _build_identity_trajectory(
    *,
    interpretation: ConversationInterpretation,
    used_recent_families: tuple[str, ...],
    used_recent_keys: tuple[str, ...],
    used_recent_signatures: tuple[str, ...],
) -> tuple[str, str, ResponseTrajectory, str, str]:
    if interpretation.freshness == "new":
        family = "brief_name_only"
        variant = "plain_name"
        trajectory = ResponseTrajectory(
            response_goal="identify",
            tactic_family=family,
            tactic_variant=variant,
            trajectory_stage="first_turn",
        )
        return family, variant, trajectory, "name_only", "none"

    stage = _trajectory_stage(
        freshness=interpretation.freshness,
        repeat_question_count=interpretation.repeat_question_count,
    )
    if stage == "late_repeat":
        candidates = [
            ("soft_close_then_open_floor", "settle_real_question"),
            ("hold_name_then_open_floor", "open_other_topic"),
            ("ack_repeat_then_light_probe", "confirm_part"),
            ("hold_name_then_open_floor", "open_real_question"),
            ("soft_close_then_open_floor", "settle_other_topic"),
            ("ack_repeat_then_light_probe", "confirm_motive"),
            ("ack_repeat_then_light_probe", "confirm_real_question"),
            ("soft_close_then_open_floor", "settle_then_open"),
        ]
    elif interpretation.user_stance == "challenging":
        candidates = [
            ("ack_repeat_then_light_probe", "confirm_real_question"),
            ("hold_name_then_open_floor", "open_real_question"),
            ("soft_close_then_open_floor", "settle_other_topic"),
            ("ack_name_then_wait", "quiet_confirm"),
            ("ack_repeat_then_light_probe", "confirm_part"),
            ("hold_name_then_open_floor", "open_other_topic"),
        ]
    else:
        candidates = [
            ("ack_repeat_then_light_probe", "confirm_part"),
            ("hold_name_then_open_floor", "open_other_topic"),
            ("soft_close_then_open_floor", "settle_real_question"),
            ("ack_name_then_wait", "quiet_confirm"),
            ("ack_repeat_then_light_probe", "confirm_motive"),
            ("hold_name_then_open_floor", "open_real_question"),
        ]
    family, variant = _select_trajectory_option(
        topic="identity",
        candidates=candidates,
        used_recent_families=used_recent_families,
        used_recent_keys=used_recent_keys,
        used_recent_signatures=used_recent_signatures,
    )
    trajectory = ResponseTrajectory(
        response_goal="identify",
        tactic_family=family,
        tactic_variant=variant,
        trajectory_stage=stage,
    )
    followup_kind = "none"
    if family in {"ack_repeat_then_light_probe", "hold_name_then_open_floor", "soft_close_then_open_floor"}:
        followup_kind = "light_probe"
    restate_level = "name_only"
    return family, variant, trajectory, restate_level, followup_kind


def _build_boundary_trajectory(
    *,
    interpretation: ConversationInterpretation,
    used_recent_families: tuple[str, ...],
    used_recent_keys: tuple[str, ...],
    used_recent_signatures: tuple[str, ...],
) -> tuple[str, str, ResponseTrajectory, str, str]:
    if interpretation.freshness == "new":
        family = "short_boundary_then_reason"
        variant = "plain_reason"
        trajectory = ResponseTrajectory(
            response_goal="boundary_reason",
            tactic_family=family,
            tactic_variant=variant,
            trajectory_stage="first_turn",
        )
        return family, variant, trajectory, "brief_reason", "none"
    stage = _trajectory_stage(
        freshness=interpretation.freshness,
        repeat_question_count=interpretation.repeat_question_count,
    )
    candidates = [
        ("ack_still_care_then_new_reason", "still_care_reason"),
        ("hold_boundary_then_shift_floor", "shift_real_question"),
        ("soft_close_boundary", "settle_repeat"),
        ("brief_reason_then_wait", "fresh_reason"),
        ("hold_boundary_then_shift_floor", "shift_other_question"),
        ("soft_close_boundary", "settle_hold"),
    ]
    if stage == "late_repeat":
        candidates = [
            ("soft_close_boundary", "settle_hold"),
            ("hold_boundary_then_shift_floor", "shift_other_question"),
            ("ack_still_care_then_new_reason", "still_care_reason"),
            ("hold_boundary_then_shift_floor", "shift_real_question"),
            ("soft_close_boundary", "settle_repeat"),
        ]
    family, variant = _select_trajectory_option(
        topic="motivation",
        candidates=candidates,
        used_recent_families=used_recent_families,
        used_recent_keys=used_recent_keys,
        used_recent_signatures=used_recent_signatures,
    )
    trajectory = ResponseTrajectory(
        response_goal="boundary_reason",
        tactic_family=family,
        tactic_variant=variant,
        trajectory_stage=stage,
    )
    followup_kind = "soft_confirm" if family in {"ack_still_care_then_new_reason", "hold_boundary_then_shift_floor"} else "none"
    restate_level = "brief_reason"
    if family in {"hold_boundary_then_shift_floor", "soft_close_boundary"}:
        restate_level = "none"
    return family, variant, trajectory, restate_level, followup_kind


def _build_help_trajectory(
    *,
    interpretation: ConversationInterpretation,
    used_recent_families: tuple[str, ...],
    used_recent_keys: tuple[str, ...],
    used_recent_signatures: tuple[str, ...],
) -> tuple[str, str, ResponseTrajectory, str, str]:
    if interpretation.freshness == "new":
        family = "short_conditional_commitment"
        variant = "plain_conditional"
        trajectory = ResponseTrajectory(
            response_goal="commitment_shape",
            tactic_family=family,
            tactic_variant=variant,
            trajectory_stage="first_turn",
        )
        return family, variant, trajectory, "brief_reason", "none"
    stage = _trajectory_stage(
        freshness=interpretation.freshness,
        repeat_question_count=interpretation.repeat_question_count,
    )
    candidates = [
        ("ack_anxiety_without_repromise", "soothe_hold"),
        ("soft_reassure_then_open_floor", "soothe_then_open"),
        ("hold_support_without_repromise", "steady_open_floor"),
        ("steady_commitment_then_wait", "quiet_hold"),
        ("ack_anxiety_without_repromise", "steady_reassure"),
    ]
    if stage == "late_repeat":
        candidates = [
            ("soft_reassure_then_open_floor", "soothe_then_open"),
            ("hold_support_without_repromise", "steady_open_floor"),
            ("ack_anxiety_without_repromise", "steady_reassure"),
            ("ack_anxiety_without_repromise", "soothe_hold"),
        ]
    family, variant = _select_trajectory_option(
        topic="help_commitment",
        candidates=candidates,
        used_recent_families=used_recent_families,
        used_recent_keys=used_recent_keys,
        used_recent_signatures=used_recent_signatures,
    )
    trajectory = ResponseTrajectory(
        response_goal="soothe_anxiety",
        tactic_family=family,
        tactic_variant=variant,
        trajectory_stage=stage,
    )
    followup_kind = "soft_reassure" if family in {"ack_anxiety_without_repromise", "soft_reassure_then_open_floor"} else "none"
    restate_level = "none"
    return family, variant, trajectory, restate_level, followup_kind


def _build_topic_trajectory_options(
    *,
    topic: str,
    interpretation: ConversationInterpretation,
    interaction_thread: InteractionThreadState,
    multimodal: dict[str, Any] | None,
    used_recent_families: tuple[str, ...],
    used_recent_keys: tuple[str, ...],
    used_recent_signatures: tuple[str, ...],
) -> list[tuple[str, str, ResponseTrajectory, str, str]]:
    if topic == "identity":
        if interpretation.freshness == "new":
            family = "brief_name_only"
            variant = "plain_name"
            return [(
                family,
                variant,
                ResponseTrajectory(
                    response_goal="identify",
                    tactic_family=family,
                    tactic_variant=variant,
                    trajectory_stage="first_turn",
                ),
                "name_only",
                "none",
            )]
        stage = _trajectory_stage(
            freshness=interpretation.freshness,
            repeat_question_count=interpretation.repeat_question_count,
        )
        if stage == "late_repeat":
            candidates = [
                ("soft_close_then_open_floor", "settle_real_question"),
                ("hold_name_then_open_floor", "open_other_topic"),
                ("ack_repeat_then_light_probe", "confirm_part"),
                ("hold_name_then_open_floor", "open_real_question"),
                ("soft_close_then_open_floor", "settle_other_topic"),
                ("ack_repeat_then_light_probe", "confirm_motive"),
                ("ack_repeat_then_light_probe", "confirm_real_question"),
                ("soft_close_then_open_floor", "settle_then_open"),
            ]
        elif interpretation.user_stance == "challenging":
            candidates = [
                ("ack_repeat_then_light_probe", "confirm_real_question"),
                ("hold_name_then_open_floor", "open_real_question"),
                ("soft_close_then_open_floor", "settle_other_topic"),
                ("ack_name_then_wait", "quiet_confirm"),
                ("ack_repeat_then_light_probe", "confirm_part"),
                ("hold_name_then_open_floor", "open_other_topic"),
            ]
        else:
            candidates = [
                ("ack_repeat_then_light_probe", "confirm_part"),
                ("hold_name_then_open_floor", "open_other_topic"),
                ("soft_close_then_open_floor", "settle_real_question"),
                ("ack_name_then_wait", "quiet_confirm"),
                ("ack_repeat_then_light_probe", "confirm_motive"),
                ("hold_name_then_open_floor", "open_real_question"),
            ]
        ranked = _prioritize_trajectory_options(
            topic="identity",
            candidates=candidates,
            used_recent_families=used_recent_families,
            used_recent_keys=used_recent_keys,
            used_recent_signatures=used_recent_signatures,
        )
        options: list[tuple[str, str, ResponseTrajectory, str, str]] = []
        for family, variant in ranked:
            followup_kind = "light_probe" if family in {"ack_repeat_then_light_probe", "hold_name_then_open_floor", "soft_close_then_open_floor"} else "none"
            restate_level = "name_only"
            options.append((
                family,
                variant,
                ResponseTrajectory(
                    response_goal="identify",
                    tactic_family=family,
                    tactic_variant=variant,
                    trajectory_stage=stage,
                ),
                restate_level,
                followup_kind,
            ))
        return options
    if topic in {"greeting", "presence_ping", "light_ack", "phatic_punct"}:
        variant = "greeting"
        if topic == "presence_ping":
            variant = "presence"
        elif topic in {"light_ack", "phatic_punct"}:
            variant = "light_ack"
        return [(
            "phatic_wave",
            variant,
            ResponseTrajectory(
                response_goal="phatic_reply",
                tactic_family="phatic_wave",
                tactic_variant=variant,
                trajectory_stage="first_turn",
            ),
            "none",
            "none",
        )]
    if topic == "playful_ping":
        return [(
            "playful_wave",
            "playful_ping",
            ResponseTrajectory(
                response_goal="playful_reply",
                tactic_family="playful_wave",
                tactic_variant="playful_ping",
                trajectory_stage="first_turn",
            ),
            "none",
            "none",
        )]
    if topic == "emoji_social_reply":
        primary_modality = str((multimodal or {}).get("primary_modality") or "").strip().lower()
        if primary_modality in {"image", "emoji", "sticker"}:
            ranked = _prioritize_trajectory_options(
                topic=topic,
                candidates=_image_social_candidates(multimodal),
                used_recent_families=used_recent_families,
                used_recent_keys=used_recent_keys,
                used_recent_signatures=used_recent_signatures,
            )
            response_goal_map = {
                "soft_emotion_ack": "gentle_image_reply",
                "cute_image_ack": "cute_image_reply",
                "tease_image_ack": "playful_image_reply",
            }
            return [
                (
                    family,
                    variant,
                    ResponseTrajectory(
                        response_goal=response_goal_map.get(family, "playful_reply"),
                        tactic_family=family,
                        tactic_variant=variant,
                        trajectory_stage="first_turn",
                    ),
                    "none",
                    "none",
                )
                for family, variant in ranked
            ]
        return [(
            "playful_wave",
            "playful_ping",
            ResponseTrajectory(
                response_goal="playful_reply",
                tactic_family="playful_wave",
                tactic_variant="playful_ping",
                trajectory_stage="first_turn",
            ),
            "none",
            "none",
        )]
    if topic == "emoji_social_followup":
        tags = {str(item).strip() for item in interpretation.reason_tags or () if str(item).strip()}
        reaction = str(interaction_thread.last_user_reaction or "").strip().lower()
        if reaction == "deny" or "image_social_followup:deny" in tags:
            candidates = [
                ("soft_emotion_ack", "backoff_hold"),
                ("tease_image_ack", "deny_nudge"),
                ("soft_emotion_ack", "let_it_pass"),
            ]
        elif reaction == "why" or "image_social_followup:why" in tags:
            candidates = [
                ("soft_emotion_ack", "explain_read"),
                ("tease_image_ack", "light_explain"),
                ("soft_emotion_ack", "backoff_hold"),
            ]
        else:
            candidates = [
                ("soft_emotion_ack", "let_it_pass"),
                ("tease_image_ack", "soft_slide"),
                ("soft_emotion_ack", "backoff_hold"),
            ]
        ranked = _prioritize_trajectory_options(
            topic=topic,
            candidates=candidates,
            used_recent_families=used_recent_families,
            used_recent_keys=used_recent_keys,
            used_recent_signatures=used_recent_signatures,
        )
        response_goal_map = {
            "soft_emotion_ack": "gentle_followup_reply",
            "tease_image_ack": "playful_followup_reply",
        }
        return [
            (
                family,
                variant,
                ResponseTrajectory(
                    response_goal=response_goal_map.get(family, "gentle_followup_reply"),
                    tactic_family=family,
                    tactic_variant=variant,
                    trajectory_stage="followup",
                ),
                "none",
                "none",
            )
            for family, variant in ranked
        ]
    if topic == "assistant_reply_followup":
        reaction = str(interaction_thread.last_user_reaction or "").strip().lower()
        if reaction == "ask_object":
            variant = "clarify_object"
        elif reaction == "surprise":
            variant = "repair_too_short"
        else:
            variant = "clarify_meaning"
        return [(
            "explain_previous_reply",
            variant,
            ResponseTrajectory(
                response_goal="clarify_previous_reply",
                tactic_family="explain_previous_reply",
                tactic_variant=variant,
                trajectory_stage="followup",
            ),
            "brief_reason",
            "none",
        )]
    if topic in {"meme_meaning", "literal_description", "screenshot_analysis", "capability_check"}:
        return [(
            "observe_then_answer",
            topic,
            ResponseTrajectory(
                response_goal="multimodal_answer",
                tactic_family="observe_then_answer",
                tactic_variant=topic,
                trajectory_stage="first_turn",
            ),
            "brief_reason",
            "none",
        )]
    if topic == "attachment_only_no_question":
        return [(
            "attachment_ack_then_wait",
            "attachment_ping",
            ResponseTrajectory(
                response_goal="multimodal_ack",
                tactic_family="attachment_ack_then_wait",
                tactic_variant="attachment_ping",
                trajectory_stage="first_turn",
            ),
            "none",
            "light_probe",
        )]
    if topic in {"relationship_boundary", "motivation"}:
        if interpretation.freshness == "new":
            family = "short_boundary_then_reason"
            variant = "plain_reason"
            return [(
                family,
                variant,
                ResponseTrajectory(
                    response_goal="boundary_reason",
                    tactic_family=family,
                    tactic_variant=variant,
                    trajectory_stage="first_turn",
                ),
                "brief_reason",
                "none",
            )]
        stage = _trajectory_stage(
            freshness=interpretation.freshness,
            repeat_question_count=interpretation.repeat_question_count,
        )
        candidates = [
            ("ack_still_care_then_new_reason", "still_care_reason"),
            ("hold_boundary_then_shift_floor", "shift_real_question"),
            ("soft_close_boundary", "settle_repeat"),
            ("brief_reason_then_wait", "fresh_reason"),
            ("hold_boundary_then_shift_floor", "shift_other_question"),
            ("soft_close_boundary", "settle_hold"),
        ]
        if stage == "late_repeat":
            candidates = [
                ("soft_close_boundary", "settle_hold"),
                ("hold_boundary_then_shift_floor", "shift_other_question"),
                ("ack_still_care_then_new_reason", "still_care_reason"),
                ("hold_boundary_then_shift_floor", "shift_real_question"),
                ("soft_close_boundary", "settle_repeat"),
            ]
        ranked = _prioritize_trajectory_options(
            topic="motivation",
            candidates=candidates,
            used_recent_families=used_recent_families,
            used_recent_keys=used_recent_keys,
            used_recent_signatures=used_recent_signatures,
        )
        options = []
        for family, variant in ranked:
            followup_kind = "soft_confirm" if family in {"ack_still_care_then_new_reason", "hold_boundary_then_shift_floor"} else "none"
            restate_level = "brief_reason"
            if family in {"hold_boundary_then_shift_floor", "soft_close_boundary"}:
                restate_level = "none"
            options.append((
                family,
                variant,
                ResponseTrajectory(
                    response_goal="boundary_reason",
                    tactic_family=family,
                    tactic_variant=variant,
                    trajectory_stage=stage,
                ),
                restate_level,
                followup_kind,
            ))
        return options
    if topic == "help_commitment":
        if interpretation.freshness == "new":
            family = "short_conditional_commitment"
            variant = "plain_conditional"
            return [(
                family,
                variant,
                ResponseTrajectory(
                    response_goal="commitment_shape",
                    tactic_family=family,
                    tactic_variant=variant,
                    trajectory_stage="first_turn",
                ),
                "brief_reason",
                "none",
            )]
        stage = _trajectory_stage(
            freshness=interpretation.freshness,
            repeat_question_count=interpretation.repeat_question_count,
        )
        candidates = [
            ("ack_anxiety_without_repromise", "soothe_hold"),
            ("soft_reassure_then_open_floor", "soothe_then_open"),
            ("hold_support_without_repromise", "steady_open_floor"),
            ("steady_commitment_then_wait", "quiet_hold"),
            ("ack_anxiety_without_repromise", "steady_reassure"),
        ]
        if stage == "late_repeat":
            candidates = [
                ("soft_reassure_then_open_floor", "soothe_then_open"),
                ("hold_support_without_repromise", "steady_open_floor"),
                ("ack_anxiety_without_repromise", "steady_reassure"),
                ("ack_anxiety_without_repromise", "soothe_hold"),
            ]
        ranked = _prioritize_trajectory_options(
            topic="help_commitment",
            candidates=candidates,
            used_recent_families=used_recent_families,
            used_recent_keys=used_recent_keys,
            used_recent_signatures=used_recent_signatures,
        )
        options = []
        for family, variant in ranked:
            followup_kind = "soft_reassure" if family in {"ack_anxiety_without_repromise", "soft_reassure_then_open_floor"} else "none"
            options.append((
                family,
                variant,
                ResponseTrajectory(
                    response_goal="soothe_anxiety",
                    tactic_family=family,
                    tactic_variant=variant,
                    trajectory_stage=stage,
                ),
                "none",
                followup_kind,
            ))
        return options
    return []


def _build_thread_fallback_plan_components(
    *,
    interpretation: ConversationInterpretation,
    interaction_thread: InteractionThreadState,
) -> tuple[str, ResponseTrajectory, str, str] | None:
    if not interaction_thread.open:
        return None

    thread_kind = str(interaction_thread.thread_kind or "").strip().lower()
    last_reaction = str(interaction_thread.last_user_reaction or "").strip().lower()
    stage = _trajectory_stage(
        freshness=interpretation.freshness,
        repeat_question_count=interpretation.repeat_question_count,
    )

    if thread_kind == "image_social":
        variant = "steady_hold"
        followup_kind = "soft_reassure"
        if last_reaction == "why":
            variant = "explain_read"
            followup_kind = "soft_confirm"
        elif last_reaction == "nothing":
            variant = "let_it_pass"
        elif last_reaction == "deny":
            variant = "backoff_hold"
        return (
            "emoji_social_followup",
            ResponseTrajectory(
                response_goal="gentle_followup_reply",
                tactic_family="soft_emotion_ack",
                tactic_variant=variant,
                trajectory_stage=stage or "thread_followup",
            ),
            "none",
            followup_kind,
        )

    if thread_kind == "identity_loop":
        return (
            "identity",
            ResponseTrajectory(
                response_goal="identity_confirm",
                tactic_family="ack_repeat_then_light_probe",
                tactic_variant="short_confirm",
                trajectory_stage=stage or "thread_followup",
            ),
            "none",
            "light_probe",
        )

    if thread_kind == "boundary_probe":
        return (
            "motivation",
            ResponseTrajectory(
                response_goal="boundary_reason",
                tactic_family="ack_still_care_then_new_reason",
                tactic_variant="soft_confirm",
                trajectory_stage=stage or "thread_followup",
            ),
            "brief_reason",
            "soft_confirm",
        )

    if thread_kind == "help_reassurance":
        return (
            "help_commitment",
            ResponseTrajectory(
                response_goal="soothe_anxiety",
                tactic_family="ack_anxiety_without_repromise",
                tactic_variant="steady_reassure",
                trajectory_stage=stage or "thread_followup",
            ),
            "none",
            "soft_reassure",
        )

    if thread_kind == "phatic":
        return (
            str(interaction_thread.source_topic or interpretation.literal_topic or "greeting").strip() or "greeting",
            ResponseTrajectory(
                response_goal="phatic_response",
                tactic_family="phatic_wave",
                tactic_variant="light_return",
                trajectory_stage=stage or "thread_followup",
            ),
            "none",
            "none",
        )
    if thread_kind == "assistant_reply_followup":
        reaction = str(interaction_thread.last_user_reaction or "").strip().lower()
        variant = "clarify_object" if reaction == "ask_object" else "clarify_meaning"
        if reaction == "surprise":
            variant = "repair_too_short"
        return (
            "assistant_reply_followup",
            ResponseTrajectory(
                response_goal="clarify_previous_reply",
                tactic_family="explain_previous_reply",
                tactic_variant=variant,
                trajectory_stage=stage or "thread_followup",
            ),
            "brief_reason",
            "none",
        )
    return None


def _assemble_reply_plan(
    *,
    affect_policy: Any,
    affect_state: AffectStateBlock,
    relationship_state: RelationshipStateBlock,
    interpretation: ConversationInterpretation,
    interaction_thread: InteractionThreadState,
    topic: str,
    multimodal: dict[str, Any] | None,
    used_recent: tuple[str, ...],
    used_recent_keys: tuple[str, ...],
    used_recent_signatures: tuple[str, ...],
    trajectory: ResponseTrajectory,
    restate_level: str,
    followup_kind: str,
    render_target_lang: str,
) -> ReplyPlan:
    forbidden_tactics = used_recent
    forbidden_tactic_keys = used_recent_keys
    forbidden_tactic_signatures = used_recent_signatures
    if topic in {"emoji_social_reply", "emoji_social_followup"}:
        # Image/sticker social replies only have a tiny tactic vocabulary.
        # We still rotate during trajectory selection, but forbidding every
        # recent family leaves the generator with no valid way to answer.
        forbidden_tactics = ()
        forbidden_tactic_keys = ()
        forbidden_tactic_signatures = ()
    tone_band = "warm_neutral" if affect_state.warmth_bias >= 0.5 else "guarded_soft"
    if affect_policy.should_be_withdrawn:
        tone_band = "guarded_soft"
    elif interpretation.user_stance == "playful":
        tone_band = "light_playful"

    forbidden_patterns = ["generic_assistant_phrasing"]
    plan_confidence = 0.74
    reason_tags: list[str] = list(interpretation.reason_tags)
    if topic == "identity":
        forbidden_patterns.extend(["full_identity_restate", "same_as_previous_reply", "repeated_tactic"])
        if restate_level == "none":
            forbidden_patterns.append("identity_name_restate")
        if trajectory.trajectory_stage != "first_turn":
            plan_confidence = 0.83
    elif topic in {"greeting", "presence_ping", "light_ack", "phatic_punct", "playful_ping"}:
        forbidden_patterns.extend(["same_as_previous_reply", "repeated_tactic"])
        plan_confidence = 0.8
    elif topic in {"emoji_social_reply", "emoji_social_followup"}:
        forbidden_patterns.extend(["same_as_previous_reply"])
        plan_confidence = 0.8
    elif topic in {"attachment_only_no_question", "meme_meaning", "literal_description", "screenshot_analysis", "capability_check"}:
        forbidden_patterns.extend(["same_as_previous_reply"])
        plan_confidence = 0.81
    elif topic == "assistant_reply_followup":
        forbidden_patterns.extend(["same_as_previous_reply", "previous_reply_underanswered"])
        plan_confidence = 0.84
    elif topic in {"relationship_boundary", "motivation"}:
        forbidden_patterns.extend(["full_reason_restate", "same_as_previous_reply", "repeated_tactic"])
        if trajectory.trajectory_stage != "first_turn":
            plan_confidence = 0.82
    elif topic == "help_commitment":
        forbidden_patterns.extend(["promise_restate", "same_as_previous_reply", "repeated_tactic"])
        if trajectory.trajectory_stage != "first_turn":
            plan_confidence = 0.82
    elif topic == "rejection":
        forbidden_patterns.extend(["defensive_explanation", "same_as_previous_reply"])
        tone_band = "guarded_soft"
        plan_confidence = 0.79
    else:
        reason_tags.append("fallback_plain_topic")

    if forbidden_tactics:
        reason_tags.append("tactic_history_present")
    if interpretation.repeat_question_count:
        reason_tags.append(f"repeat_count:{interpretation.repeat_question_count}")
    if interaction_thread.thread_kind:
        reason_tags.append(f"interaction_thread:{interaction_thread.thread_kind}")
    if interaction_thread.source_topic:
        reason_tags.append(f"interaction_thread_source:{interaction_thread.source_topic}")

    contract = _build_reply_contract(
        trajectory=trajectory,
        render_target_lang=render_target_lang,
    )
    contract = _refine_multimodal_contract(
        contract=contract,
        topic=topic,
        multimodal=multimodal,
        trajectory=trajectory,
        render_target_lang=render_target_lang,
    )
    contract = _refine_assistant_followup_contract(
        contract=contract,
        topic=topic,
        interaction_thread=interaction_thread,
        render_target_lang=render_target_lang,
    )
    response_form, secondary_response_form, response_form_reason, allow_form_only_reply = _decide_response_form(
        interpretation=interpretation,
        relationship_state=relationship_state,
        multimodal=multimodal,
    )
    requires_validation = _should_require_strict_validation(
        topic=topic,
        interpretation=interpretation,
        trajectory=trajectory,
    )
    if topic == "emoji_social_reply" and response_form_reason == "image_social_reply":
        requires_validation = True
    if topic == "emoji_social_followup":
        requires_validation = True
    if topic == "assistant_reply_followup":
        requires_validation = True
    force_buffering = _should_force_buffering(
        topic=topic,
        interpretation=interpretation,
        trajectory=trajectory,
        requires_validation=requires_validation,
    )
    if topic == "emoji_social_reply" and response_form_reason == "image_social_reply":
        force_buffering = True
    if topic == "emoji_social_followup":
        force_buffering = True
    if topic == "assistant_reply_followup":
        force_buffering = True
    max_chars = int(affect_policy.max_chars or 320)
    max_sentences = int(affect_policy.max_sentences or 4)
    should_be_brief = bool(affect_policy.should_be_brief)
    if topic == "emoji_social_reply" and response_form_reason == "image_social_reply":
        max_chars = min(max_chars, 52)
        max_sentences = 1
        should_be_brief = True
    elif topic == "emoji_social_followup":
        max_chars = min(max_chars, 64)
        max_sentences = 1
        should_be_brief = True
    elif topic in {"greeting", "presence_ping", "light_ack", "phatic_punct", "playful_ping"}:
        max_chars = min(max_chars, 84)
        max_sentences = min(max_sentences, 2)
        should_be_brief = True

    return ReplyPlan(
        primary_topic=topic,
        interaction_move=str(interpretation.interaction_move or "").strip(),
        trajectory=trajectory,
        restate_level=restate_level,
        tone_band=tone_band,
        followup_kind=followup_kind,
        used_tactics_recent=used_recent,
        used_tactic_keys_recent=used_recent_keys,
        used_tactic_signatures_recent=used_recent_signatures,
        forbidden_tactics=forbidden_tactics,
        forbidden_tactic_keys=forbidden_tactic_keys,
        forbidden_tactic_signatures=forbidden_tactic_signatures,
        forbidden_patterns=tuple(dict.fromkeys(forbidden_patterns)),
        response_form=response_form,
        secondary_response_form=secondary_response_form,
        response_form_reason=response_form_reason,
        allow_form_only_reply=allow_form_only_reply,
        max_chars=max_chars,
        max_sentences=max_sentences,
        warmth_floor=affect_policy.warmth_floor,
        warmth_ceiling=affect_policy.warmth_ceiling,
        intimacy_ceiling=affect_policy.intimacy_ceiling,
        humor_ceiling=affect_policy.humor_ceiling,
        allow_followup=affect_policy.allow_followup,
        allow_proactive_after_reply=affect_policy.allow_proactive_after_reply,
        withdrawal_guard=affect_policy.withdrawal_guard,
        should_be_brief=should_be_brief,
        should_be_withdrawn=affect_policy.should_be_withdrawn,
        affect_summary=_qualitative_affect_summary(affect=affect_state, render_target_lang=render_target_lang),
        relationship_summary=_qualitative_relationship_summary(
            relationship=relationship_state,
            interpretation=interpretation,
            interaction_thread=interaction_thread,
            render_target_lang=render_target_lang,
        ),
        interaction_thread=interaction_thread.as_dict(),
        multimodal=dict(multimodal or {}),
        contract=contract,
        requires_validation=requires_validation,
        force_buffering=force_buffering,
        correction_directive="上一条没有按本轮回应动作完成，请保持同样内容目标重新生成。",
        plan_confidence=plan_confidence,
        reason_tags=tuple(dict.fromkeys(reason_tags)),
    )


def build_reply_plan(
    *,
    affect_state: AffectStateBlock,
    relationship_state: RelationshipStateBlock,
    drive_state: DriveStateBlock,
    interpretation: ConversationInterpretation,
    interaction_thread: InteractionThreadState = InteractionThreadState(),
    topic_ledger: TopicLedgerBlock,
    tactic_ledger: TacticLedgerBlock,
    multimodal: dict[str, Any] | None = None,
    render_target_lang: str = "zh",
) -> ReplyPlan:
    affect_policy = build_affect_policy(
        affect_state=affect_state,
        relationship_state=relationship_state,
        drive_state=drive_state,
    )
    topic = str(interpretation.literal_topic or "").strip()
    used_recent = tactic_ledger.recent_tactic_families(topic, limit=6) if topic else ()
    used_recent_keys = tactic_ledger.recent_tactic_keys(topic, limit=6) if topic else ()
    used_recent_signatures = tactic_ledger.recent_tactic_signatures(topic, limit=6) if topic else ()
    forbidden_tactics = used_recent
    forbidden_tactic_keys = used_recent_keys
    forbidden_tactic_signatures = used_recent_signatures
    tone_band = "warm_neutral" if affect_state.warmth_bias >= 0.5 else "guarded_soft"
    options = _build_topic_trajectory_options(
        topic=topic,
        interpretation=interpretation,
        interaction_thread=interaction_thread,
        multimodal=multimodal,
        used_recent_families=used_recent,
        used_recent_keys=used_recent_keys,
        used_recent_signatures=used_recent_signatures,
    )
    if options:
        _, _, trajectory, restate_level, followup_kind = options[0]
        return _assemble_reply_plan(
            affect_policy=affect_policy,
            affect_state=affect_state,
            relationship_state=relationship_state,
            interpretation=interpretation,
            interaction_thread=interaction_thread,
            topic=topic,
            multimodal=multimodal,
            used_recent=used_recent,
            used_recent_keys=used_recent_keys,
            used_recent_signatures=used_recent_signatures,
            trajectory=trajectory,
            restate_level=restate_level,
            followup_kind=followup_kind,
            render_target_lang=render_target_lang,
        )
    thread_fallback = _build_thread_fallback_plan_components(
        interpretation=interpretation,
        interaction_thread=interaction_thread,
    )
    if thread_fallback is not None:
        topic, trajectory, restate_level, followup_kind = thread_fallback
        return _assemble_reply_plan(
            affect_policy=affect_policy,
            affect_state=affect_state,
            relationship_state=relationship_state,
            interpretation=interpretation,
            interaction_thread=interaction_thread,
            topic=topic,
            multimodal=multimodal,
            used_recent=used_recent,
            used_recent_keys=used_recent_keys,
            used_recent_signatures=used_recent_signatures,
            trajectory=trajectory,
            restate_level=restate_level,
            followup_kind=followup_kind,
            render_target_lang=render_target_lang,
        )
    trajectory = ResponseTrajectory(
        response_goal="plain_answer",
        tactic_family="plain_hold",
        tactic_variant=interpretation.user_stance or "neutral",
        trajectory_stage=_trajectory_stage(
            freshness=interpretation.freshness,
            repeat_question_count=interpretation.repeat_question_count,
        ),
    )
    return _assemble_reply_plan(
        affect_policy=affect_policy,
        affect_state=affect_state,
        relationship_state=relationship_state,
        interpretation=interpretation,
        interaction_thread=interaction_thread,
        topic=topic,
        multimodal=multimodal,
        used_recent=used_recent,
        used_recent_keys=used_recent_keys,
        used_recent_signatures=used_recent_signatures,
        trajectory=trajectory,
        restate_level="full",
        followup_kind="none",
        render_target_lang=render_target_lang,
    )


def build_reply_plan_candidates(
    *,
    affect_state: AffectStateBlock,
    relationship_state: RelationshipStateBlock,
    drive_state: DriveStateBlock,
    interpretation: ConversationInterpretation,
    interaction_thread: InteractionThreadState = InteractionThreadState(),
    topic_ledger: TopicLedgerBlock,
    tactic_ledger: TacticLedgerBlock,
    multimodal: dict[str, Any] | None = None,
    render_target_lang: str = "zh",
    limit: int = 3,
) -> list[ReplyPlan]:
    affect_policy = build_affect_policy(
        affect_state=affect_state,
        relationship_state=relationship_state,
        drive_state=drive_state,
    )
    topic = str(interpretation.literal_topic or "").strip()
    used_recent = tactic_ledger.recent_tactic_families(topic, limit=6) if topic else ()
    used_recent_keys = tactic_ledger.recent_tactic_keys(topic, limit=6) if topic else ()
    used_recent_signatures = tactic_ledger.recent_tactic_signatures(topic, limit=6) if topic else ()
    options = _build_topic_trajectory_options(
        topic=topic,
        interpretation=interpretation,
        interaction_thread=interaction_thread,
        multimodal=multimodal,
        used_recent_families=used_recent,
        used_recent_keys=used_recent_keys,
        used_recent_signatures=used_recent_signatures,
    )
    if not options:
        thread_fallback = _build_thread_fallback_plan_components(
            interpretation=interpretation,
            interaction_thread=interaction_thread,
        )
        if thread_fallback is not None:
            topic, trajectory, restate_level, followup_kind = thread_fallback
            return [
                _assemble_reply_plan(
                    affect_policy=affect_policy,
                    affect_state=affect_state,
                    relationship_state=relationship_state,
                    interpretation=interpretation,
                    interaction_thread=interaction_thread,
                    topic=topic,
                    multimodal=multimodal,
                    used_recent=used_recent,
                    used_recent_keys=used_recent_keys,
                    used_recent_signatures=used_recent_signatures,
                    trajectory=trajectory,
                    restate_level=restate_level,
                    followup_kind=followup_kind,
                    render_target_lang=render_target_lang,
                )
            ]
        return [
            build_reply_plan(
                affect_state=affect_state,
                relationship_state=relationship_state,
                drive_state=drive_state,
                interpretation=interpretation,
                interaction_thread=interaction_thread,
                topic_ledger=topic_ledger,
                tactic_ledger=tactic_ledger,
                multimodal=multimodal,
                render_target_lang=render_target_lang,
            )
        ]
    plans: list[ReplyPlan] = []
    for _, _, trajectory, restate_level, followup_kind in options[: max(1, int(limit or 1))]:
        plans.append(
            _assemble_reply_plan(
                affect_policy=affect_policy,
                affect_state=affect_state,
                relationship_state=relationship_state,
                interpretation=interpretation,
                interaction_thread=interaction_thread,
                topic=topic,
                multimodal=multimodal,
                used_recent=used_recent,
                used_recent_keys=used_recent_keys,
                used_recent_signatures=used_recent_signatures,
                trajectory=trajectory,
                restate_level=restate_level,
                followup_kind=followup_kind,
                render_target_lang=render_target_lang,
            )
        )
    return plans


@dataclass(frozen=True)
class ResponseTrajectoryPlanner:
    affect_state: AffectStateBlock
    relationship_state: RelationshipStateBlock
    drive_state: DriveStateBlock
    interpretation: ConversationInterpretation
    topic_ledger: TopicLedgerBlock
    tactic_ledger: TacticLedgerBlock
    interaction_thread: InteractionThreadState = field(default_factory=InteractionThreadState)
    multimodal: dict[str, Any] = field(default_factory=dict)
    render_target_lang: str = "zh"

    def build(self) -> ReplyPlan:
        return build_reply_plan(
            affect_state=self.affect_state,
            relationship_state=self.relationship_state,
            drive_state=self.drive_state,
            interpretation=self.interpretation,
            interaction_thread=self.interaction_thread,
            topic_ledger=self.topic_ledger,
            tactic_ledger=self.tactic_ledger,
            multimodal=self.multimodal,
            render_target_lang=self.render_target_lang,
        )


def build_reply_plan_from_inputs(
    *,
    emotion_context: dict[str, Any] | None = None,
    bond_summary: dict[str, Any] | None = None,
    motive_state: dict[str, Any] | None = None,
    body_sense: dict[str, Any] | None = None,
    offline_state: dict[str, Any] | None = None,
    silence_state: dict[str, Any] | None = None,
    social_brain_state: dict[str, Any] | None = None,
    conversation_interpretation: dict[str, Any] | ConversationInterpretation | None = None,
    interaction_thread: dict[str, Any] | InteractionThreadState | None = None,
    topic_ledger: dict[str, Any] | TopicLedgerBlock | None = None,
    tactic_ledger: dict[str, Any] | TacticLedgerBlock | None = None,
    multimodal: dict[str, Any] | None = None,
    render_target_lang: str = "zh",
) -> ReplyPlan:
    affect_state = resolve_affect_state(
        emotion_context=dict(emotion_context or {}),
        body_sense=dict(body_sense or {}),
        offline_state=dict(offline_state or {}),
        silence_state=dict(silence_state or {}),
    )
    relationship_state = resolve_relationship_state(
        bond_summary=dict(bond_summary or {}),
        social_brain_state=dict(social_brain_state or {}),
    )
    interpretation = (
        conversation_interpretation
        if isinstance(conversation_interpretation, ConversationInterpretation)
        else coerce_conversation_interpretation(conversation_interpretation)
    )
    thread_state = (
        interaction_thread
        if isinstance(interaction_thread, InteractionThreadState)
        else coerce_interaction_thread_state(interaction_thread)
    )
    ledger = topic_ledger if isinstance(topic_ledger, TopicLedgerBlock) else coerce_topic_ledger_block(topic_ledger)
    tactics = tactic_ledger if isinstance(tactic_ledger, TacticLedgerBlock) else coerce_tactic_ledger_block(tactic_ledger)
    drive_state = resolve_drive_state(
        motive_state=dict(motive_state or {}),
        affect_state=affect_state,
        relationship_state=relationship_state,
        dialogue_state={
            "current_user_act": interpretation.literal_topic,
            "repeat_question_count": interpretation.repeat_question_count,
            "user_stance": interpretation.user_stance,
        },
    )
    return ResponseTrajectoryPlanner(
        affect_state=affect_state,
        relationship_state=relationship_state,
        drive_state=drive_state,
        interpretation=interpretation,
        interaction_thread=thread_state,
        topic_ledger=ledger,
        tactic_ledger=tactics,
        multimodal=dict(multimodal or {}),
        render_target_lang=render_target_lang,
    ).build()


def build_reply_plan_candidates_from_inputs(
    *,
    emotion_context: dict[str, Any] | None = None,
    bond_summary: dict[str, Any] | None = None,
    motive_state: dict[str, Any] | None = None,
    body_sense: dict[str, Any] | None = None,
    offline_state: dict[str, Any] | None = None,
    silence_state: dict[str, Any] | None = None,
    social_brain_state: dict[str, Any] | None = None,
    conversation_interpretation: dict[str, Any] | ConversationInterpretation | None = None,
    interaction_thread: dict[str, Any] | InteractionThreadState | None = None,
    topic_ledger: dict[str, Any] | TopicLedgerBlock | None = None,
    tactic_ledger: dict[str, Any] | TacticLedgerBlock | None = None,
    multimodal: dict[str, Any] | None = None,
    render_target_lang: str = "zh",
    limit: int = 3,
) -> list[ReplyPlan]:
    affect_state = resolve_affect_state(
        emotion_context=dict(emotion_context or {}),
        body_sense=dict(body_sense or {}),
        offline_state=dict(offline_state or {}),
        silence_state=dict(silence_state or {}),
    )
    relationship_state = resolve_relationship_state(
        bond_summary=dict(bond_summary or {}),
        social_brain_state=dict(social_brain_state or {}),
    )
    interpretation = (
        conversation_interpretation
        if isinstance(conversation_interpretation, ConversationInterpretation)
        else coerce_conversation_interpretation(conversation_interpretation)
    )
    thread_state = (
        interaction_thread
        if isinstance(interaction_thread, InteractionThreadState)
        else coerce_interaction_thread_state(interaction_thread)
    )
    ledger = topic_ledger if isinstance(topic_ledger, TopicLedgerBlock) else coerce_topic_ledger_block(topic_ledger)
    tactics = tactic_ledger if isinstance(tactic_ledger, TacticLedgerBlock) else coerce_tactic_ledger_block(tactic_ledger)
    drive_state = resolve_drive_state(
        motive_state=dict(motive_state or {}),
        affect_state=affect_state,
        relationship_state=relationship_state,
        dialogue_state={
            "current_user_act": interpretation.literal_topic,
            "repeat_question_count": interpretation.repeat_question_count,
            "user_stance": interpretation.user_stance,
        },
    )
    return build_reply_plan_candidates(
        affect_state=affect_state,
        relationship_state=relationship_state,
        drive_state=drive_state,
        interpretation=interpretation,
        interaction_thread=thread_state,
        topic_ledger=ledger,
        tactic_ledger=tactics,
        multimodal=dict(multimodal or {}),
        render_target_lang=render_target_lang,
        limit=limit,
    )


__all__ = [
    "ReplyContract",
    "ReplyPlan",
    "ResponseTrajectoryPlanner",
    "ResponseTrajectory",
    "build_reply_plan",
    "build_reply_plan_candidates",
    "build_reply_plan_candidates_from_inputs",
    "build_reply_plan_from_inputs",
    "coerce_reply_plan",
]
