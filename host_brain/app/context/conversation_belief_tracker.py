"""Conversation belief tracking for frontstage understanding.

The belief state is a compact, explainable estimate of what the user is really
talking about now. It is intentionally evidence-based and reversible: when the
user corrects Natsume, the next turn can repair the belief instead of pretending
the previous guess was correct.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from host_brain.core.runtime_helpers import _coerce_list as _list
from host_brain.core.runtime_helpers import _coerce_mapping as _mapping
from host_brain.app.context.intent_hypothesis_engine import build_intent_hypotheses
from host_brain.app.context.reference_resolver import best_reference, resolve_references


@dataclass(slots=True)
class ConversationBelief:
    active_topic: str
    referenced_object: dict[str, Any]
    user_goal: str
    user_emotion: str
    confidence: float
    open_questions: list[str]
    repair_requested: bool
    selected_intent: str
    update_reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "active_topic": self.active_topic,
            "referenced_object": dict(self.referenced_object or {}),
            "user_goal": self.user_goal,
            "user_emotion": self.user_emotion,
            "confidence": round(float(self.confidence), 4),
            "open_questions": list(self.open_questions or []),
            "repair_requested": bool(self.repair_requested),
            "selected_intent": self.selected_intent,
            "update_reason": self.update_reason,
        }


def _text(value: Any, *, limit: int = 220) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        return text[:limit].rstrip() + "..."
    return text


def _top_hypothesis(hypotheses: Sequence[Mapping[str, Any]] | None) -> dict[str, Any]:
    items = [_mapping(item) for item in _list(hypotheses) if isinstance(item, Mapping)]
    items.sort(key=lambda item: float(item.get("confidence") or 0.0), reverse=True)
    return items[0] if items else {}


def _emotion_from_pragmatics(pragmatic_frame: Mapping[str, Any] | None, selected_intent: str) -> str:
    pragmatic = _mapping(pragmatic_frame)
    emotion = _text(pragmatic.get("likely_emotion"), limit=80)
    if emotion:
        return emotion
    if selected_intent in {"repair_misunderstanding", "criticize_reasoning_quality", "challenge_value_or_direction"}:
        return "dissatisfied_but_collaborating"
    if selected_intent in {"ask_recommendation_item_judgement", "recommendation_followup_query"}:
        return "curious_expecting_continuity"
    if selected_intent in {"ask_user_preference_profile", "user_preference_query"}:
        return "checking_memory_and_understanding"
    return "neutral"


def _should_carry_previous_belief(text: str, selected_intent: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    if selected_intent in {"request_action_or_continue", "conversation_repair", "repair_misunderstanding"}:
        return True
    carry_markers = (
        "这个",
        "那个",
        "它",
        "这本",
        "那本",
        "这部",
        "那部",
        "第",
        "继续",
        "接着",
        "上一",
        "刚才",
        "刚刚",
        "前面",
        "上面",
    )
    return any(marker in raw for marker in carry_markers)


def _build_experience_guidance(
    *,
    text: str,
    selected_intent: str,
    references: Sequence[Mapping[str, Any]] | None,
    experience_lessons: Sequence[Mapping[str, Any]] | None,
    recommended_action: str,
) -> dict[str, Any]:
    raw_lessons = [_mapping(item) for item in _list(experience_lessons) if isinstance(item, Mapping)]
    lessons = []
    for item in raw_lessons:
        success_count = int(item.get("success_count") or 0)
        failure_count = int(item.get("failure_count") or 0)
        confidence = float(item.get("confidence") or 0.0)
        if failure_count > success_count and confidence < 0.85:
            continue
        lessons.append(item)
    refs = [_mapping(item) for item in _list(references) if isinstance(item, Mapping)]
    if not lessons:
        return {
            "applied": False,
            "action": recommended_action or "answer",
            "reason": "",
            "lessons_used": [],
            "do_not": [],
            "confidence": 0.0,
        }

    top_ref = refs[0] if refs else {}
    ref_kind = _text(top_ref.get("target_kind"), limit=100)
    ref_label = _text(top_ref.get("target_label"), limit=180)
    ref_confidence = float(top_ref.get("confidence") or 0.0) if top_ref else 0.0
    lesson_text = " ".join(_text(item.get("lesson"), limit=500) for item in lessons[:3])
    lesson_patterns = {_text(item.get("pattern"), limit=140) for item in lessons[:5]}
    best_lesson = lessons[0]
    relevance = float(best_lesson.get("relevance") or best_lesson.get("confidence") or 0.0)
    raw = str(text or "")

    action = recommended_action or "answer"
    reason = ""
    do_not: list[str] = []

    is_recommendation_reference_lesson = (
        "distilled_recommendation_reference_repair" in lesson_patterns
        or "第几本" in lesson_text
        or "最近推荐集合" in lesson_text
    )
    has_recommendation_reference_phrase = any(
        marker in raw
        for marker in ("这本", "那本", "哪本", "第", "小说", "轻小说", "书", "推荐")
    )
    has_recommendation_ref = ref_kind in {"recommendation_item", "recommendation_set", "ordinal_reference"}
    if is_recommendation_reference_lesson and (has_recommendation_reference_phrase or has_recommendation_ref):
        do_not.extend(["不要泛化成长期偏好", "不要切到其他媒介", "不要忽略第几本/这本的引用"])
        if ref_kind == "recommendation_item" and ref_confidence >= 0.75:
            action = "bind_reference_then_answer"
            reason = f"经验提示：本轮应先绑定最近推荐作品“{ref_label}”，再回答真实问题。"
        elif ref_kind == "recommendation_set" or ref_confidence < 0.6:
            action = "clarify_reference"
            reason = "经验提示：用户在推荐上下文里用模糊指代，但无法唯一绑定作品，应先自然澄清是哪一本。"
        elif "第三本" in raw or "第3本" in raw:
            action = "bind_reference_then_answer"
            reason = "经验提示：用户用第几本追问时，应优先查最近推荐集合并点名作品。"

    if selected_intent in {"conversation_repair", "repair_misunderstanding"}:
        do_not.extend(["不要空泛道歉", "不要沿用上一轮错误理解"])
        if action == "answer":
            action = "repair_then_answer"
        if not reason:
            reason = "经验提示：用户正在纠正理解错位，应先重建被纠正对象，再继续。"

    if selected_intent in {"criticize_reasoning_quality", "meta_quality_feedback"}:
        do_not.extend(["不要只加分类", "不要像客服工单", "不要为系统辩解"])
        action = "diagnose_and_improve_reasoning"
        if not reason:
            reason = "经验提示：用户批评不像人在思考时，要解释推理缺口并做结构性修复。"

    seen: set[str] = set()
    clean_do_not = []
    for item in do_not:
        if item and item not in seen:
            seen.add(item)
            clean_do_not.append(item)

    applied = bool(reason or action != (recommended_action or "answer"))
    return {
        "applied": applied,
        "action": action,
        "reason": reason,
        "lessons_used": [
            {
                "lesson_id": _text(item.get("lesson_id"), limit=180),
                "pattern": _text(item.get("pattern"), limit=140),
                "relevance": item.get("relevance"),
                "confidence": item.get("confidence"),
            }
            for item in lessons[:3]
        ] if applied else [],
        "do_not": clean_do_not,
        "confidence": round(max(relevance, ref_confidence), 4) if applied else 0.0,
    }


def build_belief_state(
    *,
    text: str,
    references: Sequence[Mapping[str, Any]] | None,
    hypotheses: Sequence[Mapping[str, Any]] | None,
    semantic_intent: Mapping[str, Any] | None = None,
    pragmatic_frame: Mapping[str, Any] | None = None,
    previous_belief: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    semantic = _mapping(semantic_intent)
    pragmatic = _mapping(pragmatic_frame)
    previous = _mapping(previous_belief)
    ref = best_reference(references)
    top = _top_hypothesis(hypotheses)
    selected_intent = _text(top.get("intent"), limit=100) or _text(semantic.get("type"), limit=100) or "open_chat"
    carry_previous = _should_carry_previous_belief(text, selected_intent)
    referenced_object = {
        "target_kind": _text(ref.get("target_kind"), limit=80),
        "target_id": _text(ref.get("target_id"), limit=180),
        "target_label": _text(ref.get("target_label"), limit=180),
        "confidence": float(ref.get("confidence") or 0.0) if ref else 0.0,
    } if ref else (_mapping(previous.get("referenced_object")) if carry_previous else {})

    active_topic = (
        _text(referenced_object.get("target_label"), limit=180)
        or _text(top.get("target_label"), limit=180)
        or (_text(previous.get("active_topic"), limit=180) if carry_previous else "")
        or _text(text, limit=120)
    )
    user_goal = (
        _text(top.get("user_goal"), limit=220)
        or _text(pragmatic.get("implied_need"), limit=220)
        or _text(semantic.get("answer_strategy"), limit=180)
        or "自然回应当前话题"
    )
    confidence = max(
        float(top.get("confidence") or 0.0),
        float(ref.get("confidence") or 0.0) if ref else 0.0,
        float(semantic.get("confidence") or 0.0),
    )
    open_questions: list[str] = []
    if ref and float(ref.get("confidence") or 0.0) < 0.6:
        open_questions.append("引用对象未能唯一绑定")
    if selected_intent == "open_chat" and confidence < 0.55:
        open_questions.append("用户真实目标需要结合更多上下文")
    repair_requested = bool(pragmatic.get("repair_mode")) or selected_intent in {
        "repair_misunderstanding",
        "criticize_reasoning_quality",
        "challenge_value_or_direction",
    }

    belief = ConversationBelief(
        active_topic=active_topic,
        referenced_object=referenced_object,
        user_goal=user_goal,
        user_emotion=_emotion_from_pragmatics(pragmatic, selected_intent),
        confidence=confidence or 0.48,
        open_questions=open_questions,
        repair_requested=repair_requested,
        selected_intent=selected_intent,
        update_reason="reference+hypothesis+pragmatic_frame",
    )
    return belief.as_dict()


def build_conversation_understanding_pack(
    *,
    text: str,
    semantic_intent: Mapping[str, Any] | None = None,
    pragmatic_frame: Mapping[str, Any] | None = None,
    recent_recommendation_context: Mapping[str, Any] | None = None,
    recent_turns: Sequence[Mapping[str, Any]] | None = None,
    previous_belief: Mapping[str, Any] | None = None,
    experience_lessons: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    references = resolve_references(
        text=text,
        recent_recommendation_context=recent_recommendation_context,
        previous_belief=previous_belief,
        recent_turns=recent_turns,
    )
    hypotheses = build_intent_hypotheses(
        text=text,
        references=references,
        semantic_intent=semantic_intent,
        pragmatic_frame=pragmatic_frame,
    )
    dialogue_frame: dict[str, Any] = {}
    try:
        from host_brain.app.context.dialogue_frame import build_dialogue_frame  # noqa: PLC0415

        dialogue_frame = build_dialogue_frame(
            current_user_message=text,
            recent_turns=recent_turns,
            recent_recommendation_context=recent_recommendation_context,
            previous_belief=previous_belief,
        )
    except Exception:
        dialogue_frame = {}
    belief = build_belief_state(
        text=text,
        references=references,
        hypotheses=hypotheses,
        semantic_intent=semantic_intent,
        pragmatic_frame=pragmatic_frame,
        previous_belief=previous_belief,
    )
    if str(dialogue_frame.get("status") or "") == "RESOLVED":
        target = _mapping(dialogue_frame.get("target"))
        belief = dict(belief)
        belief["active_topic"] = _text(target.get("target_label"), limit=180) or belief.get("active_topic")
        belief["referenced_object"] = {
            "target_kind": _text(target.get("target_kind"), limit=80),
            "target_id": _text(target.get("detail_url") or target.get("source_turn_id"), limit=180),
            "target_label": _text(target.get("target_label"), limit=180),
            "confidence": float(dialogue_frame.get("confidence") or 0.0),
        }
        belief["user_goal"] = _text(dialogue_frame.get("user_goal"), limit=220) or belief.get("user_goal")
        belief["selected_intent"] = _text(dialogue_frame.get("intent"), limit=100) or belief.get("selected_intent")
        belief["confidence"] = max(float(belief.get("confidence") or 0.0), float(dialogue_frame.get("confidence") or 0.0))
        belief["open_questions"] = []
        belief["update_reason"] = "dialogue_frame+reference+hypothesis+pragmatic_frame"
    open_questions = list(belief.get("open_questions") or [])
    confidence = float(belief.get("confidence") or 0.0)
    low_reference_confidence = any(
        float(_mapping(item).get("confidence") or 0.0) < 0.6
        for item in _list(references)
        if isinstance(item, Mapping)
    )
    recommended_action = "answer"
    if open_questions and low_reference_confidence:
        recommended_action = "clarify_reference"
    elif open_questions and confidence < 0.7:
        recommended_action = "clarify_reference"
    elif open_questions:
        recommended_action = "answer_with_uncertainty"
    lesson_items = [
        _mapping(item)
        for item in _list(experience_lessons)[:5]
        if isinstance(item, Mapping)
    ]
    experience_guidance = _build_experience_guidance(
        text=text,
        selected_intent=str(belief.get("selected_intent") or ""),
        references=references,
        experience_lessons=lesson_items,
        recommended_action=recommended_action,
    )
    if str(experience_guidance.get("action") or "") == "clarify_reference":
        recommended_action = "clarify_reference"
    return {
        "schema_version": "Natsume.conversation_understanding.v1",
        "domain": _text(
            _mapping(semantic_intent).get("domain")
            or _mapping(pragmatic_frame).get("domain")
            or "general",
            limit=120,
        ),
        "belief_state": belief,
        "dialogue_frame": dialogue_frame,
        "resolved_references": references,
        "intent_hypotheses": hypotheses,
        "selected_intent": belief.get("selected_intent"),
        "confidence": belief.get("confidence"),
        "experience_lessons": lesson_items,
        "experience_guidance": experience_guidance,
        "recommended_action": recommended_action,
        "ambiguity": {
            "has_open_questions": bool(belief.get("open_questions")),
            "open_questions": open_questions,
            "recommended_action": recommended_action,
        },
    }


__all__ = ["ConversationBelief", "build_belief_state", "build_conversation_understanding_pack"]
