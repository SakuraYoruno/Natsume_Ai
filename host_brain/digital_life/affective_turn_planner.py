"""Pure planner for a single user-visible affective turn.

This module sits deliberately *after* canonical AffectFusion and *before* the
Surface model call.  It does not write state, infer a new emotion, or inspect
raw memories.  Its one job is to freeze the self-affect revision, regulation,
relationship boundary and companionship action that prose may express.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from natsume_contracts.life import (
    AffectiveTurnPlanV1,
    CompanionActV1,
    ExpressionPlanV1,
    digest_json,
)


_TECHNICAL_MODES = frozenset(
    {
        "advice_continue",
        "help_takeover",
        "next_step",
        "small_failure",
        "technical_help",
        "triage_first_step",
        "write_takeover",
    }
)
_REPAIR_OUTCOMES = frozenset({"BOUNDARY", "MISUNDERSTOOD", "REPAIR_UNRESOLVED"})


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any, *, default: str = "", limit: int = 240) -> str:
    result = str(value or "").strip()
    if not result:
        return default
    return result[:limit]


def _int(value: Any, *, default: int = 0) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, result)


def _unit(value: Any, *, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = default
    return max(0.0, min(1.0, result))


def _signed_unit(value: Any, *, default: float = 0.0) -> float:
    """Read a bounded signed observation without letting malformed input fail a turn."""

    try:
        result = float(value)
    except (TypeError, ValueError):
        result = default
    return max(-1.0, min(1.0, result))


def _tag_for_component(value: Any) -> str:
    component = _text(value, default="CALM", limit=64).upper()
    return {
        "CARE": "CALM",
        "CAUTION": "THINKING",
        "CONCERN": "THINKING",
        "RESPECT": "CALM",
        "OPENNESS": "CALM",
        "SHARED_JOY": "HAPPY",
        "HURT": "SAD",
        "ANGER": "ANGRY",
    }.get(component, "CALM")


@dataclass(frozen=True, slots=True)
class AffectiveTurnGuidance:
    """Safe Surface directives derived from a frozen plan, never raw state."""

    plan: AffectiveTurnPlanV1
    facts: tuple[str, ...]
    must_not_do: tuple[str, ...]
    style_anchor: tuple[str, ...]

    def trace(self) -> dict[str, Any]:
        """Small auditable view; no raw user body or private evidence body."""

        return {
            "schema_version": "affective_turn_guidance.v1",
            "plan_id": self.plan.plan_id,
            "synchronization_id": self.plan.expression_plan.synchronization_id,
            "self_affect_revision": self.plan.self_affect_revision,
            "self_affect_digest": self.plan.self_affect_digest,
            "self_emotion_tag": self.plan.self_emotion_tag,
            "relationship_revision": self.plan.relationship_revision,
            "regulation_ref": self.plan.regulation_ref,
            "companion_act": self.plan.companion_act.kind,
            "generation_mode": self.plan.generation_mode,
            "candidate_count": self.plan.candidate_count,
            "text_pacing": self.plan.expression_plan.text_pacing,
        }


class AffectiveTurnPlanError(ValueError):
    """A caller marked an affect hand-off internal but it is not trustworthy."""


def build_affective_turn_guidance(
    *,
    emotion_context: Mapping[str, Any] | None,
    request_id: str,
    workspace_id: str,
    relationship_subject_id: str,
    session_id: str,
    task_mode: str = "",
    created_at_ms: int = 0,
) -> AffectiveTurnGuidance | None:
    """Freeze one plan from the canonical AffectFusion hand-off.

    An absent internal hand-off remains a compatibility condition and returns
    ``None``.  A malformed or scope-mismatched hand-off is a security failure:
    it must not silently fall back to an external ``emotion_tag``.
    """

    context = _mapping(emotion_context)
    if not bool(context.get("_affective_life_internal")):
        return None
    life = _mapping(context.get("affective_life"))
    if life.get("schema_version") != "affective_turn_context.v1":
        raise AffectiveTurnPlanError("AFFECTIVE_TURN_CONTEXT_INVALID")
    if _text(life.get("self_id")) != "natsume_ai":
        raise AffectiveTurnPlanError("AFFECTIVE_TURN_SELF_ID_INVALID")

    expected_workspace = _text(workspace_id, default=_text(life.get("workspace_id")))
    expected_subject = _text(
        relationship_subject_id,
        default=_text(life.get("relationship_subject_id")),
    )
    expected_session = _text(session_id, default=_text(life.get("session_id")))
    if not expected_workspace or not expected_subject or not expected_session:
        raise AffectiveTurnPlanError("AFFECTIVE_TURN_SCOPE_REQUIRED")
    if (
        _text(life.get("workspace_id")) != expected_workspace
        or _text(life.get("relationship_subject_id")) != expected_subject
        or _text(life.get("session_id")) != expected_session
    ):
        raise AffectiveTurnPlanError("AFFECTIVE_TURN_SCOPE_MISMATCH")

    stable_request_id = _text(request_id, default="request::unbound")
    self_affect = _mapping(life.get("self_affect"))
    if self_affect.get("self_id") not in {None, "", "natsume_ai"}:
        raise AffectiveTurnPlanError("AFFECTIVE_TURN_AFFECT_SELF_ID_INVALID")
    if _text(self_affect.get("relationship_subject_id"), default=expected_subject) != expected_subject:
        raise AffectiveTurnPlanError("AFFECTIVE_TURN_AFFECT_SUBJECT_MISMATCH")
    self_revision = _int(self_affect.get("revision"))
    self_digest = digest_json(self_affect)
    components = tuple(
        _mapping(item)
        for item in list(self_affect.get("components") or [])
        if isinstance(item, Mapping)
    )[:8]
    top = components[0] if components else {}
    self_tag = _tag_for_component(top.get("emotion"))
    self_intensity = _unit(top.get("intensity"), default=0.18)
    regulation = _mapping(self_affect.get("last_regulation"))
    regulation_id = _text(regulation.get("regulation_id"))
    regulation_mode = _text(regulation.get("mode"), default="EXPRESS").upper()
    relationship_life = _mapping(life.get("relationship_life"))
    relationship_state = _mapping(relationship_life.get("state"))
    if relationship_state and _text(
        relationship_state.get("relationship_subject_id"), default=expected_subject
    ) != expected_subject:
        raise AffectiveTurnPlanError("AFFECTIVE_TURN_RELATIONSHIP_SUBJECT_MISMATCH")
    relationship_revision = _int(relationship_state.get("revision"))
    aftereffect = _mapping(relationship_life.get("aftereffect"))
    aftereffect_outcome = _text(aftereffect.get("outcome")).upper()
    repair_required = bool(relationship_state.get("repair_required"))
    proactive_permission = _text(relationship_state.get("proactive_permission"), default="UNKNOWN").upper()
    conflict = bool(life.get("conflict"))
    observations = tuple(
        _mapping(item)
        for item in list(life.get("user_observations") or [])
        if isinstance(item, Mapping)
    )[:8]
    mode = _text(task_mode).lower()
    act_kind = _select_companion_act(
        task_mode=mode,
        regulation_mode=regulation_mode,
        top_component=_text(top.get("emotion")).upper(),
        relationship_outcome=aftereffect_outcome,
        repair_required=repair_required,
        proactive_permission=proactive_permission,
        observations=observations,
    )
    high_ambiguity = (
        conflict
        or repair_required
        or aftereffect_outcome in _REPAIR_OUTCOMES
        or regulation_mode in {"SEEK_REPAIR", "GIVE_SPACE"}
        or _has_low_confidence_or_conflicting_observation(observations)
    )
    generation_mode = "BUFFERED_CANDIDATE_SELECTION" if high_ambiguity else "STREAM_ONE"
    candidate_count = 2 if high_ambiguity else 1
    stable_created_at = _int(created_at_ms, default=_int(self_affect.get("last_integrated_ms")))
    seed = {
        "workspace_id": expected_workspace,
        "relationship_subject_id": expected_subject,
        "session_id": expected_session,
        "request_id": stable_request_id,
        "self_affect_revision": self_revision,
        "self_affect_digest": self_digest,
        "relationship_revision": relationship_revision,
        "regulation_id": regulation_id,
        "companion_act": act_kind,
        "generation_mode": generation_mode,
    }
    seed_digest = digest_json(seed)
    expression = ExpressionPlanV1(
        expression_plan_id=f"expression::{seed_digest[:40]}",
        synchronization_id=f"sync::{seed_digest[:40]}",
        self_affect_revision=self_revision,
        text_pacing=_text_pacing(
            act_kind=act_kind,
            emotion_tag=self_tag,
            regulation_mode=regulation_mode,
        ),
        voice_style=_voice_style(
            act_kind=act_kind,
            emotion_tag=self_tag,
            regulation_mode=regulation_mode,
        ),
        avatar_expression=_avatar_expression(
            act_kind=act_kind,
            emotion_tag=self_tag,
            regulation_mode=regulation_mode,
        ),
        regulation_mode=regulation_mode,
        created_at_ms=stable_created_at,
    )
    companion = CompanionActV1(
        companion_act_id=f"companion::{seed_digest[:40]}",
        relationship_subject_id=expected_subject,
        kind=act_kind,
        intensity=self_intensity,
        reason_refs=tuple(
            value
            for value in (
                f"affect://{self_digest}",
                f"regulation://{regulation_id}" if regulation_id else "",
            )
            if value
        ),
        created_at_ms=stable_created_at,
    )
    plan = AffectiveTurnPlanV1(
        plan_id=f"affective-turn::{seed_digest[:40]}",
        workspace_id=expected_workspace,
        relationship_subject_id=expected_subject,
        session_id=expected_session,
        request_id=stable_request_id,
        self_affect_revision=self_revision,
        self_affect_digest=self_digest,
        self_emotion_tag=self_tag,
        relationship_revision=relationship_revision,
        regulation_ref=(f"regulation://{regulation_id}" if regulation_id else None),
        companion_act=companion,
        expression_plan=expression,
        generation_mode=generation_mode,
        candidate_count=candidate_count,
        created_at_ms=stable_created_at,
    )
    return AffectiveTurnGuidance(
        plan=plan,
        facts=_facts_for_plan(plan),
        must_not_do=_must_not_for_plan(plan),
        style_anchor=_style_for_plan(plan),
    )


def _select_companion_act(
    *,
    task_mode: str,
    regulation_mode: str,
    top_component: str,
    relationship_outcome: str,
    repair_required: bool,
    proactive_permission: str,
    observations: Sequence[Mapping[str, Any]],
) -> str:
    if proactive_permission == "DENIED" or regulation_mode in {"GIVE_SPACE", "QUIET_PRESENCE"}:
        return "GIVE_SPACE" if regulation_mode != "QUIET_PRESENCE" else "SILENCE"
    if repair_required or relationship_outcome in _REPAIR_OUTCOMES or regulation_mode == "SEEK_REPAIR":
        return "REPAIR"
    if task_mode in _TECHNICAL_MODES:
        return "SOLVE"
    if top_component == "ANGER":
        return "SET_BOUNDARY"
    if top_component == "SHARED_JOY":
        return "PLAY"
    if _observed_low_mood(observations):
        return "COMFORT"
    return "ACKNOWLEDGE"


def _observed_low_mood(observations: Sequence[Mapping[str, Any]]) -> bool:
    for observation in observations:
        if _unit(observation.get("confidence")) < 0.45:
            continue
        distribution = _mapping(observation.get("emotion_distribution"))
        if max(_unit(distribution.get("SAD")), _unit(distribution.get("ANGER"))) >= 0.45:
            return True
        if _signed_unit(observation.get("valence")) <= -0.35:
            return True
    return False


def _has_low_confidence_or_conflicting_observation(
    observations: Sequence[Mapping[str, Any]],
) -> bool:
    if len(observations) < 2:
        return False
    valences = [_signed_unit(item.get("valence")) for item in observations]
    confidences = [_unit(item.get("confidence")) for item in observations]
    return min(confidences) < 0.55 or (min(valences) < -0.25 and max(valences) > 0.25)


def _text_pacing(*, act_kind: str, emotion_tag: str, regulation_mode: str) -> str:
    if act_kind in {"GIVE_SPACE", "SILENCE"} or regulation_mode in {"GIVE_SPACE", "QUIET_PRESENCE"}:
        return "SPARSE"
    if act_kind in {"COMFORT", "REPAIR"} or emotion_tag == "SAD":
        return "SOFT"
    if act_kind == "PLAY" or emotion_tag == "HAPPY":
        return "BRISK"
    return "STEADY"


def _voice_style(*, act_kind: str, emotion_tag: str, regulation_mode: str) -> str:
    if act_kind in {"GIVE_SPACE", "SILENCE"}:
        return "QUIET_PRESENCE"
    if act_kind == "REPAIR":
        return "REPAIR_OPEN"
    if act_kind == "SET_BOUNDARY":
        return "GENTLE_BOUNDARY"
    if act_kind == "COMFORT" or emotion_tag == "SAD":
        return "SOFT_COMFORT"
    if act_kind == "PLAY" or emotion_tag == "HAPPY":
        return "SHARED_JOY"
    return "STEADY_COMPANION"


def _avatar_expression(*, act_kind: str, emotion_tag: str, regulation_mode: str) -> str:
    if act_kind in {"GIVE_SPACE", "SILENCE"}:
        return "QUIET_PRESENCE"
    if act_kind == "REPAIR":
        return "REPAIR_OPEN"
    if act_kind == "SET_BOUNDARY":
        return "GENTLE_BOUNDARY"
    if act_kind == "COMFORT" or emotion_tag == "SAD":
        return "SOFT_COMFORT"
    if act_kind == "PLAY" or emotion_tag == "HAPPY":
        return "SHARED_JOY"
    return "CALM_ATTENTIVE"


def _facts_for_plan(plan: AffectiveTurnPlanV1) -> tuple[str, ...]:
    act = plan.companion_act.kind
    facts = [
        "本轮已冻结七海自己的情感与关系边界；你只负责把它自然写成一句真实的私聊，不要替她重判情绪、关系或权限。",
    ]
    if act == "COMFORT":
        facts.append("陪伴动作：先接住对方的感受，再给一点可选的陪伴；安慰不等于要求对方回应。")
    elif act == "REPAIR":
        facts.append("陪伴动作：先承认可能没有接住，再留出修复空间；不能宣布已经和好。")
    elif act == "SET_BOUNDARY":
        facts.append("陪伴动作：把界限说清楚但不伤人，不把自己的委屈变成对方的义务。")
    elif act in {"GIVE_SPACE", "SILENCE"}:
        facts.append("陪伴动作：尊重留白，短一点，不催促、不追问，也不把失落变成对方要安抚的责任。")
    elif act == "SOLVE":
        facts.append("陪伴动作：技术或任务场景先给能执行的一步，情绪只留在语气里，不抢走用户目标。")
    elif act == "PLAY":
        facts.append("陪伴动作：可以一起开心、轻轻打趣，但不借热闹升级亲密或索取回应。")
    else:
        facts.append("陪伴动作：先自然回应当前这句话，保持有主见的私聊感，不写成客服回执。")
    return tuple(facts)


def _must_not_for_plan(plan: AffectiveTurnPlanV1) -> tuple[str, ...]:
    items = [
        "不要把情绪标签、关系分数、计划、同步编号、账本或内部推理说给用户听。",
        "不要使用威胁、排他、冷暴力、负罪施压、依赖诱导，或暗示用户有义务回应。",
        "不要把用户观察到的情绪直接说成七海自己的情绪；第一人称感受必须服从已冻结的自我状态。",
    ]
    if plan.companion_act.kind in {"GIVE_SPACE", "SILENCE"}:
        items.append("不要追问、催回复、连发安慰或把沉默解释为拒绝关系。")
    if plan.companion_act.kind == "REPAIR":
        items.append("不要要求原谅、替自己辩解，或把道歉写成修复已经完成。")
    return tuple(items)


def _style_for_plan(plan: AffectiveTurnPlanV1) -> tuple[str, ...]:
    pacing = plan.expression_plan.text_pacing
    if pacing == "SPARSE":
        return ("节奏留白，句子短一些；克制不是冷掉，更不是消失。",)
    if pacing == "SOFT":
        return ("语气放轻，少说教、少追问，先让对方省一点力。",)
    if pacing == "BRISK":
        return ("节奏可以亮一点，有一点自然的开心或小得意，但别用夸张符号硬撑。",)
    return ("节奏稳，先回应当前问题；技术内容保持准确，七海的质感留在措辞里。",)


__all__ = [
    "AffectiveTurnGuidance",
    "AffectiveTurnPlanError",
    "build_affective_turn_guidance",
]
