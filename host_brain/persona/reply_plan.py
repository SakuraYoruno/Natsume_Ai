from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from host_brain.app.context.affect_state import AffectStateBlock, resolve_affect_state
from host_brain.app.context.conversation_interpretation import (
    ConversationInterpretation,
    coerce_conversation_interpretation,
)
from host_brain.app.context.drive_state import DriveStateBlock, resolve_drive_state
from host_brain.app.context.relationship_state import RelationshipStateBlock, resolve_relationship_state
from host_brain.app.context.tactic_ledger import TacticLedgerBlock, coerce_tactic_ledger_block
from host_brain.app.context.topic_ledger import TopicLedgerBlock, coerce_topic_ledger_block
from host_brain.persona.affect_policy import build_affect_policy
from host_brain.persona.reply_tactics import (
    canonical_tactic_signature,
    contract_action_sets,
    realization_steps,
    tactic_key,
)


def _join_nonempty(parts: list[str]) -> str:
    return " ".join(part for part in parts if str(part or "").strip()).strip()


_LOW_RISK_RELATIONAL_TOPICS = {
    "identity",
    "relationship_boundary",
    "motivation",
    "help_commitment",
    "rejection",
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


def _qualitative_relationship_summary(*, relationship: RelationshipStateBlock, render_target_lang: str) -> str:
    stage = str(relationship.bond_stage or "STRANGER").strip().upper() or "STRANGER"
    if str(render_target_lang or "").strip().lower() == "ja":
        if stage in {"STRANGER", "ACQUAINTANCE"}:
            return "相手との距離はまだ遠めで、馴れ馴れしくしない。"
        if relationship.boundary_softness >= 0.5:
            return "関係はやや柔らかいが、言い過ぎない。"
        return "距離は保ちつつ、自然に受ける。"
    if stage in {"STRANGER", "ACQUAINTANCE"}:
        return "你和对方还不熟，不要过度亲近。"
    if relationship.boundary_softness >= 0.5:
        return "关系稍微软一点，但别说得太近。"
    return "保持分寸，顺着对方自然回应。"


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
    if normalized_topic not in _LOW_RISK_RELATIONAL_TOPICS:
        return True

    user_stance = str(interpretation.user_stance or "").strip().lower()
    interaction_move = str(interpretation.interaction_move or "").strip().lower()
    freshness = str(interpretation.freshness or "").strip().lower()
    repeat_count = int(interpretation.repeat_question_count or 0)

    if user_stance in {"challenging", "rejecting"}:
        return True
    if interaction_move in {"probe", "repair_needed", "long_repeat"}:
        return True
    if freshness == "long_gap_reopen":
        return True
    if repeat_count >= 2:
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
    if str(render_target_lang or "").strip().lower() == "ja":
        mapping = {
            "brief_name_only": "名前だけを短く返し、紹介文には広げない。",
            "ack_name_then_wait": "短く受けてから止まり、名前を改めて広げない。",
            "ack_repeat_then_light_probe": "また確認していることだけ受けて、軽く探りを返し、名乗り直しすぎない。",
            "hold_name_then_open_floor": "名前の事実だけ保ち、問いの口を相手に返す。",
            "name_only_then_pause": "名前だけを最短で返して止まる。",
            "soft_close_then_open_floor": "同じ確認が続いたことを静かに収め、本当に聞きたい方へ促す。",
            "short_boundary_then_reason": "親しくないことを先に示し、そのうえで短い理由を添える。",
            "ack_still_care_then_new_reason": "相手がまだ気にしていることを受けて、新しい短い理由を一つ添える。",
            "hold_boundary_then_shift_floor": "距離感は保ちつつ、説明は短くして話の口を返す。",
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
        "brief_name_only": "只简短报名字，不展开成介绍。",
        "ack_name_then_wait": "这轮不是第一次问身份：短短承认前面已经回答过，然后停住，不要重新展开名字，也不要只剩一个名字。",
        "ack_repeat_then_light_probe": "把名字这件事轻轻放住，再顺着把真正想问的东西引出来，不要把名字展开成介绍。",
        "hold_name_then_open_floor": "把名字放成已知事实，再把话口让回去，不要复用上一轮动作。",
        "name_only_then_pause": "只在对方已经连问很多次时，用极短确认收住，不补解释，也不要和上一轮同一种动作。",
        "soft_close_then_open_floor": "把这件事轻轻收稳，再把话口让回去，不要再报名字。",
        "short_boundary_then_reason": "先明确不熟，再给一句短理由，不要滑成泛化陪聊句。",
        "ack_still_care_then_new_reason": "把距离先放稳，再补一句新的短理由，不要重复上一轮结构。",
        "hold_boundary_then_shift_floor": "这轮不是第一次问原因：把距离放稳，再把话口轻轻让回去，不要整段重讲理由。",
        "brief_reason_then_wait": "这轮不是第一次问原因：只换一条新的短理由，然后停住，不要复用上一轮承接动作。",
        "soft_close_boundary": "这轮原因已经来回问过几次：轻轻收住重复追问，不要再重复不熟和理由的原句。",
        "short_conditional_commitment": "用简短条件式回应会不会继续帮，不要滑成空泛安抚。",
        "ack_anxiety_without_repromise": "先把会回应的态度放稳，不要重新许诺整句。",
        "steady_commitment_then_wait": "这轮不是第一次问会不会继续帮：保持态度一致，但只短短说一次，不要重复上一轮结构。",
        "soft_reassure_then_open_floor": "先把态度放稳，再把话口让回去，不要重新承诺。",
        "hold_support_without_repromise": "把会回应这件事放稳，但不要重复承诺本身。",
        "hold_repair_space": "先收住，轻轻接住对方的不喜欢，不解释。",
    }
    variant_overrides = {
        ("ack_repeat_then_light_probe", "confirm_motive"): "把名字这件事轻轻放住，再顺着探一下他真正想确认的是什么，不要重报名字。",
        ("ack_repeat_then_light_probe", "confirm_part"): "把名字这件事轻轻放住，再问他还想确认哪一部分，不要重报名字。",
        ("ack_repeat_then_light_probe", "confirm_real_question"): "把名字这件事轻轻放住，再把真正想问的是什么引出来，不要重报名字。",
        ("hold_name_then_open_floor", "open_real_question"): "把名字放成已知事实，然后把真正想问的机会让回去，不要再把名字展开成介绍。",
        ("hold_name_then_open_floor", "open_other_topic"): "把名字放成已知事实，然后把别的想问的口子让回去，不要再把名字展开成介绍。",
        ("soft_close_then_open_floor", "settle_then_open"): "把这件事轻轻收稳，再把真正想问的口子让回去，不要再报名字。",
        ("soft_close_then_open_floor", "settle_real_question"): "把这件事轻轻收稳，再把真正想问的是什么让对方说出来，不要再报名字。",
        ("soft_close_then_open_floor", "settle_other_topic"): "把这件事轻轻收稳，再把别的想问的口子让回去，不要再报名字。",
        ("ack_name_then_wait", "quiet_confirm"): "这轮不是第一次问身份：只短短承认前面已经回答过，然后停住，不要再展开名字。",
        ("ack_still_care_then_new_reason", "still_care_reason"): "把距离先放稳，再换一句新的短理由，不要重复上一轮承接方式。",
        ("hold_boundary_then_shift_floor", "shift_real_question"): "这轮不是第一次问原因：把距离放稳，再把真正想问的是什么让对方说出来，不要整段重讲理由。",
        ("hold_boundary_then_shift_floor", "shift_other_question"): "这轮不是第一次问原因：把距离放稳，再把别的想问的口子让回去，不要整段重讲理由。",
        ("soft_close_boundary", "settle_repeat"): "这轮原因已经来回问过几次：轻轻收住重复追问，不要再整句重复上一轮理由。",
        ("soft_close_boundary", "settle_hold"): "这轮原因已经来回问过几次：轻轻收住重复追问，把理由停在这，不要再整段重讲。",
        ("ack_anxiety_without_repromise", "soothe_hold"): "先把会回应的态度放稳，不要重新许诺整句。",
        ("ack_anxiety_without_repromise", "steady_reassure"): "先把态度放稳一点，不要重新许诺整句。",
        ("soft_reassure_then_open_floor", "soothe_then_open"): "先把态度放稳，再把真正想问的口子让回去，不要重复承诺本身。",
        ("hold_support_without_repromise", "steady_open_floor"): "把会回应这件事放稳，再把别的想问的口子让回去，不要沿用上一轮承接结构。",
    }
    return ReplyContract(
        text=variant_overrides.get((family, variant), mapping.get(family, "这轮先接住对方动作，再短短回答。")),
        required_actions=required_actions,
        forbidden_actions=forbidden_actions,
        realization_steps=steps,
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
    restate_level = "none" if family not in {"brief_name_only", "ack_name_then_wait", "name_only_then_pause"} else "name_only"
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
            restate_level = "none" if family not in {"brief_name_only", "ack_name_then_wait", "name_only_then_pause"} else "name_only"
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


def _assemble_reply_plan(
    *,
    affect_policy: Any,
    affect_state: AffectStateBlock,
    relationship_state: RelationshipStateBlock,
    interpretation: ConversationInterpretation,
    topic: str,
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

    contract = _build_reply_contract(
        trajectory=trajectory,
        render_target_lang=render_target_lang,
    )
    requires_validation = _should_require_strict_validation(
        topic=topic,
        interpretation=interpretation,
        trajectory=trajectory,
    )

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
        max_chars=affect_policy.max_chars,
        max_sentences=affect_policy.max_sentences,
        warmth_floor=affect_policy.warmth_floor,
        warmth_ceiling=affect_policy.warmth_ceiling,
        intimacy_ceiling=affect_policy.intimacy_ceiling,
        humor_ceiling=affect_policy.humor_ceiling,
        allow_followup=affect_policy.allow_followup,
        allow_proactive_after_reply=affect_policy.allow_proactive_after_reply,
        withdrawal_guard=affect_policy.withdrawal_guard,
        should_be_brief=affect_policy.should_be_brief,
        should_be_withdrawn=affect_policy.should_be_withdrawn,
        affect_summary=_qualitative_affect_summary(affect=affect_state, render_target_lang=render_target_lang),
        relationship_summary=_qualitative_relationship_summary(
            relationship=relationship_state,
            render_target_lang=render_target_lang,
        ),
        contract=contract,
        requires_validation=requires_validation,
        force_buffering=requires_validation,
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
    topic_ledger: TopicLedgerBlock,
    tactic_ledger: TacticLedgerBlock,
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
            topic=topic,
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
        topic=topic,
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
    topic_ledger: TopicLedgerBlock,
    tactic_ledger: TacticLedgerBlock,
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
        used_recent_families=used_recent,
        used_recent_keys=used_recent_keys,
        used_recent_signatures=used_recent_signatures,
    )
    if not options:
        return [
            build_reply_plan(
                affect_state=affect_state,
                relationship_state=relationship_state,
                drive_state=drive_state,
                interpretation=interpretation,
                topic_ledger=topic_ledger,
                tactic_ledger=tactic_ledger,
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
                topic=topic,
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
    render_target_lang: str = "zh"

    def build(self) -> ReplyPlan:
        return build_reply_plan(
            affect_state=self.affect_state,
            relationship_state=self.relationship_state,
            drive_state=self.drive_state,
            interpretation=self.interpretation,
            topic_ledger=self.topic_ledger,
            tactic_ledger=self.tactic_ledger,
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
    topic_ledger: dict[str, Any] | TopicLedgerBlock | None = None,
    tactic_ledger: dict[str, Any] | TacticLedgerBlock | None = None,
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
        topic_ledger=ledger,
        tactic_ledger=tactics,
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
    topic_ledger: dict[str, Any] | TopicLedgerBlock | None = None,
    tactic_ledger: dict[str, Any] | TacticLedgerBlock | None = None,
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
        topic_ledger=ledger,
        tactic_ledger=tactics,
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
