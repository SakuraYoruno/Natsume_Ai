from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from host_brain.persona.isolated_surface_harness import (
    IsolatedSurfaceInput,
    ReplyControlPacket,
    build_isolated_surface_input,
)
from host_brain.persona.surface_affect_control import (
    SurfaceAffectControlPacket,
    affect_control_packet_from_mapping,
)
from host_brain.persona.surface_memory_recall import (
    MemoryRecallPacket,
    memory_recall_packet_from_mapping,
)
from host_brain.persona.surface_influence_packet import (
    SurfaceInfluencePacket,
    surface_influence_facts,
    surface_influence_must_not_do,
    surface_influence_packet_from_mapping,
    surface_influence_style_anchor,
    surface_influence_trace,
)
from host_brain.persona.surface_low_info_guidance import SurfaceLowInfoGuidance, low_info_guidance_for_user_message


_FORBIDDEN_RAW_CONTEXT_KEYS = frozenset(
    {
        "agent_advice",
        "assistant_history",
        "candidate",
        "candidates",
        "conversation_history",
        "dialogue_history",
        "history",
        "history_turns",
        "long_term_memory",
        "messages",
        "module_advice",
        "natural_language_advice",
        "organ_outputs",
        "rag_documents",
        "raw_history",
        "raw_long_term_memory",
        "raw_memory",
        "raw_organ_outputs",
        "retrieved_docs",
        "runtime_state",
        "state",
        "tool_candidate_list",
        "tool_candidates",
        "uncompressed_rag_documents",
    }
)
_FORBIDDEN_RAW_CONTEXT_HINTS = (
    "raw_history",
    "raw_memory",
    "raw_organ",
    "organ_output",
    "runtime_state",
    "conversation_history",
    "dialogue_history",
    "long_term_memory",
    "uncompressed_rag",
    "rag_document",
    "retrieved_doc",
    "tool_candidate",
    "natural_language_advice",
    "module_advice",
    "agent_advice",
)


@dataclass(frozen=True)
class SurfaceTaskControl:
    """Task-layer controls that may influence the final surface packet."""

    response_move: str = "reply_current_turn"
    task_mode: str = "daily_chat"
    information_budget: str = "short"
    question_budget: int = 0
    must_include: tuple[str, ...] = ()
    must_not_do: tuple[str, ...] = ()
    facts: tuple[str, ...] = ()
    style_anchor: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "response_move": self.response_move,
            "task_mode": self.task_mode,
            "information_budget": self.information_budget,
            "question_budget": int(self.question_budget),
            "must_include": list(self.must_include),
            "must_not_do": list(self.must_not_do),
            "facts": list(self.facts),
            "style_anchor": list(self.style_anchor),
        }


@dataclass(frozen=True)
class SurfacePacketCompileResult:
    packet: ReplyControlPacket
    trace: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "packet": _packet_as_dict(self.packet),
            "trace": dict(self.trace),
        }


@dataclass(frozen=True)
class CompiledSurfaceInput:
    request: IsolatedSurfaceInput
    packet: ReplyControlPacket
    trace: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "request": _input_as_dict(self.request),
            "packet": _packet_as_dict(self.packet),
            "trace": dict(self.trace),
        }


def compile_reply_control_packet(
    *,
    user_message: str = "",
    task_control: SurfaceTaskControl | Mapping[str, Any] | None = None,
    memory_packet: MemoryRecallPacket | Mapping[str, Any] | None = None,
    affect_packet: SurfaceAffectControlPacket | Mapping[str, Any] | None = None,
    influence_packet: SurfaceInfluencePacket | Mapping[str, Any] | None = None,
    necessary_facts: Sequence[Any] | None = None,
    must_not_do: Sequence[Any] | None = None,
    style_anchor: Sequence[Any] | None = None,
    max_facts: int = 8,
    max_style_anchors: int = 10,
    max_must_not_do: int = 12,
) -> SurfacePacketCompileResult:
    """Merge compressed task, memory, and affect controls into one surface packet.

    The final surface generator must receive only this packet shape plus
    Soul/user text. Raw memories, raw history, organ outputs, RAG documents, and
    module-authored natural-language suggestions are rejected before merging.
    """

    task = _coerce_task_control(task_control)
    memory = _coerce_memory_packet(memory_packet)
    affect = _coerce_affect_packet(affect_packet)
    influence = _coerce_influence_packet(influence_packet)
    affect = _affect_with_influence(affect, influence)
    low_info_guidance = low_info_guidance_for_user_message(
        user_message,
        task_mode=task.task_mode,
        response_move=task.response_move,
    )
    if _has_temporal_greeting_mismatch(
        user_message=user_message,
        task_facts=task.facts,
        necessary_facts=necessary_facts,
        task_must_not_do=task.must_not_do,
        explicit_must_not_do=must_not_do,
    ):
        low_info_guidance = SurfaceLowInfoGuidance(
            scene_kind="temporal_mismatch_greeting",
            facts=(
                "当前用户用了早安类问候，但必要事实显示现在不是早上；本轮只按时间错位问候处理，不要推断用户睡眠、精神状态或故意程度。",
            ),
            style_anchor=(
                "时间错位问候：先接住关系，再自然点出现在已经不是早上；保持 Soul.md 的说话质感，但不要评价用户状态或动机。",
            ),
            must_not_do=(
                "不要把早安类用户输入直接顺成“早/早啊/早安/早上好”回复。",
                "不要使用早安句法锚；不要编造刚醒、早餐、作息、精神状态、睡迷糊、故意逗你或装傻。",
            ),
        )

    emotional_tone = affect.emotional_tone or "neutral"
    relationship_distance = affect.relationship_distance or "familiar"
    memory_recall_required_terms = _memory_recall_required_terms(
        user_message=user_message,
        task=task,
        memory=memory,
    )
    fact_max_chars = _memory_fact_max_chars(memory)
    facts = _clean_list(
        [
            *task.facts,
            *_coerce_sequence(necessary_facts),
            *low_info_guidance.facts,
            *(_ensure_memory_fact_prefix(fact) for fact in memory.facts),
            *surface_influence_facts(influence),
        ],
        limit=max_facts,
        max_chars=fact_max_chars,
    )
    packet_style_anchor = _clean_list(
        [
            *task.style_anchor,
            *_coerce_sequence(style_anchor),
            *low_info_guidance.style_anchor,
            *_memory_recall_style_anchor(task=task, memory=memory),
            *memory.style_anchor,
            *affect.style_anchor,
            *surface_influence_style_anchor(influence),
        ],
        limit=max_style_anchors,
        max_chars=180,
    )
    packet_must_not_do = _clean_list(
        [
            *task.must_not_do,
            *_coerce_sequence(must_not_do),
            *low_info_guidance.must_not_do,
            *memory.must_not_do,
            *affect.must_not_do,
            *surface_influence_must_not_do(influence),
        ],
        limit=max_must_not_do,
        max_chars=160,
    )
    packet = ReplyControlPacket(
        response_move=task.response_move or "reply_current_turn",
        task_mode=task.task_mode or "daily_chat",
        emotional_tone=emotional_tone,
        relationship_distance=relationship_distance,
        information_budget=task.information_budget or "short",
        question_budget=max(0, int(task.question_budget or 0)),
        must_include=_string_list(_clean_list([*task.must_include, *memory_recall_required_terms], max_chars=80)),
        must_not_do=_string_list(packet_must_not_do),
        facts=_string_list(facts),
        style_anchor=_string_list(packet_style_anchor),
    )
    trace = {
        "task_mode": packet.task_mode,
        "response_move": packet.response_move,
        "affect_applied": bool(affect.emotional_tone or affect.relationship_distance or affect.style_anchor),
        "memory_applied": bool(memory.facts or memory.style_anchor or memory.must_not_do),
        "memory_fact_count": len(memory.facts),
        "memory_relationship_clue_count": len(memory.relationship_clues),
        "influence_applied": _influence_applied(influence),
        "influence": surface_influence_trace(influence),
        "fact_count": len(packet.facts),
        "fact_max_chars": fact_max_chars,
        "style_anchor_count": len(packet.style_anchor),
        "must_not_do_count": len(packet.must_not_do),
        "must_include_count": len(packet.must_include),
        "dropped_memory_relationship_clues": bool(memory.relationship_clues),
        "low_info_scene_guidance_applied": low_info_guidance.applied,
        "low_info_scene_kind": low_info_guidance.scene_kind,
        "surface_boundary": "ReplyControlPacket",
    }
    return SurfacePacketCompileResult(packet=packet, trace=trace)


def _has_temporal_greeting_mismatch(
    *,
    user_message: Any,
    task_facts: Sequence[Any] | None,
    necessary_facts: Sequence[Any] | None,
    task_must_not_do: Sequence[Any] | None,
    explicit_must_not_do: Sequence[Any] | None,
) -> bool:
    compact_user = re.sub(r"\s+", "", str(user_message or "").strip())
    if not re.search(r"^(早|早安|早上好)", compact_user):
        return False
    context = "\n".join(
        str(item or "")
        for seq in (task_facts, necessary_facts, task_must_not_do, explicit_must_not_do)
        for item in _coerce_sequence(seq)
    )
    compact_context = re.sub(r"\s+", "", context)
    return any(
        marker in compact_context
        for marker in (
            "时间错位问候",
            "当前不是早上",
            "当前阶段是afternoon",
            "阶段是afternoon",
            "not_morning",
            "不要回复“早”",
            "不要直接按早晨回复",
        )
    )


def compile_surface_input(
    *,
    soul_md: str,
    user_message: str,
    case_id: str = "",
    task_control: SurfaceTaskControl | Mapping[str, Any] | None = None,
    memory_packet: MemoryRecallPacket | Mapping[str, Any] | None = None,
    affect_packet: SurfaceAffectControlPacket | Mapping[str, Any] | None = None,
    influence_packet: SurfaceInfluencePacket | Mapping[str, Any] | None = None,
    necessary_facts: Sequence[Any] | None = None,
    must_not_do: Sequence[Any] | None = None,
    style_anchor: Sequence[Any] | None = None,
    max_facts: int = 8,
    max_style_anchors: int = 10,
    max_must_not_do: int = 12,
) -> CompiledSurfaceInput:
    compiled = compile_reply_control_packet(
        user_message=user_message,
        task_control=task_control,
        memory_packet=memory_packet,
        affect_packet=affect_packet,
        influence_packet=influence_packet,
        necessary_facts=necessary_facts,
        must_not_do=must_not_do,
        style_anchor=style_anchor,
        max_facts=max_facts,
        max_style_anchors=max_style_anchors,
        max_must_not_do=max_must_not_do,
    )
    request = build_isolated_surface_input(
        {
            "case_id": case_id,
            "soul_md": soul_md,
            "user_message": user_message,
            "reply_control_packet": compiled.packet,
        }
    )
    return CompiledSurfaceInput(
        request=request,
        packet=compiled.packet,
        trace=compiled.trace,
    )


def compile_surface_shadow_inputs(
    *,
    soul_md: str,
    user_message: str,
    case_id: str = "",
    task_control: SurfaceTaskControl | Mapping[str, Any] | None = None,
    memory_packet: MemoryRecallPacket | Mapping[str, Any] | None = None,
    affect_packet: SurfaceAffectControlPacket | Mapping[str, Any] | None = None,
    influence_packet: SurfaceInfluencePacket | Mapping[str, Any] | None = None,
    necessary_facts: Sequence[Any] | None = None,
    must_not_do: Sequence[Any] | None = None,
    style_anchor: Sequence[Any] | None = None,
    max_facts: int = 8,
    max_style_anchors: int = 10,
    max_must_not_do: int = 12,
) -> dict[str, CompiledSurfaceInput]:
    """Build baseline, memory-only, affect-only, and combined shadow requests."""

    common = {
        "soul_md": soul_md,
        "user_message": user_message,
        "task_control": task_control,
        "necessary_facts": necessary_facts,
        "must_not_do": must_not_do,
        "style_anchor": style_anchor,
        "influence_packet": influence_packet,
        "max_facts": max_facts,
        "max_style_anchors": max_style_anchors,
        "max_must_not_do": max_must_not_do,
    }
    return {
        "baseline": compile_surface_input(case_id=_variant_case_id(case_id, "baseline"), **common),
        "memory": compile_surface_input(
            case_id=_variant_case_id(case_id, "memory"),
            memory_packet=memory_packet,
            **common,
        ),
        "affect": compile_surface_input(
            case_id=_variant_case_id(case_id, "affect"),
            affect_packet=affect_packet,
            **common,
        ),
        "memory_affect": compile_surface_input(
            case_id=_variant_case_id(case_id, "memory_affect"),
            memory_packet=memory_packet,
            affect_packet=affect_packet,
            **common,
        ),
    }


def _string_list(values: Sequence[str]) -> list[str]:
    return [str(value) for value in values]


def _coerce_task_control(raw: SurfaceTaskControl | Mapping[str, Any] | None) -> SurfaceTaskControl:
    if raw is None:
        return SurfaceTaskControl()
    if isinstance(raw, SurfaceTaskControl):
        _reject_forbidden_raw_context(raw.as_dict(), source="task_control")
        return raw
    if not isinstance(raw, Mapping):
        raise ValueError("task control must be a mapping")
    _reject_forbidden_raw_context(raw, source="task_control")
    return SurfaceTaskControl(
        response_move=_clean_text(raw.get("response_move")) or "reply_current_turn",
        task_mode=_clean_text(raw.get("task_mode")) or "daily_chat",
        information_budget=_clean_text(raw.get("information_budget")) or "short",
        question_budget=max(0, _coerce_int(raw.get("question_budget"), default=0)),
        must_include=tuple(_clean_list(_coerce_sequence(raw.get("must_include")), max_chars=120)),
        must_not_do=tuple(_clean_list(_coerce_sequence(raw.get("must_not_do")), max_chars=160)),
        facts=tuple(_clean_list(_coerce_sequence(raw.get("facts")), max_chars=180)),
        style_anchor=tuple(_clean_list(_coerce_sequence(raw.get("style_anchor")), max_chars=180)),
    )


def _memory_recall_style_anchor(*, task: SurfaceTaskControl, memory: MemoryRecallPacket) -> tuple[str, ...]:
    haystack = " ".join(
        [
            str(task.task_mode or ""),
            str(task.response_move or ""),
        ]
    ).lower()
    if not memory.facts or not any(token in haystack for token in ("recall", "memory")):
        return ()
    return ("记忆追问：必须直接答出记忆事实本身；不要只说记得、没忘或让用户接着说。",)


def _memory_recall_required_terms(
    *,
    user_message: str,
    task: SurfaceTaskControl,
    memory: MemoryRecallPacket,
) -> tuple[str, ...]:
    haystack = " ".join(
        [
            str(task.task_mode or ""),
            str(task.response_move or ""),
        ]
    ).lower()
    if not memory.facts or not any(token in haystack for token in ("recall", "memory")):
        return ()
    user_text = _clean_text(user_message)[:240]
    if not _looks_like_explicit_memory_question(user_text):
        return ()
    memory_text = "\n".join(_clean_text(fact)[:240] for fact in memory.facts)
    return tuple(_clean_list(_salient_memory_answer_terms(memory_text, user_text=user_text), limit=2, max_chars=32))


def _looks_like_explicit_memory_question(user_message: str) -> bool:
    text = str(user_message or "")
    if not text:
        return False
    return any(token in text for token in ("记得", "还记", "之前", "上次", "哪里", "哪块", "什么", "哪个", "哪本", "哪部"))


def _salient_memory_answer_terms(memory_text: str, *, user_text: str) -> list[str]:
    priority_terms = (
        "生硬",
        "不通顺",
        "输入输出",
        "模板感",
        "文本质量",
        "台词召回",
        "原台词",
        "记忆召回",
        "情绪层",
        "关系层",
        "主链",
    )
    result: list[str] = []
    for term in priority_terms:
        if term in memory_text and term not in result:
            result.append(term)
    if result:
        return result
    quoted = re.findall(r"[「“\"]([^」”\"]{2,60})[」”\"]", memory_text)
    stop_terms = (
        "用户",
        "之前",
        "上次",
        "当前",
        "回合",
        "摘要",
        "回复",
        "记忆",
        "线索",
        "长期",
        "事实",
        "主链",
        "哪里",
        "什么",
        "哪个",
    )
    for segment in quoted:
        cleaned = _clean_text(segment)[:80]
        if not cleaned or cleaned in user_text:
            continue
        for token in re.findall(r"[\u4e00-\u9fffA-Za-z0-9_]{2,12}", cleaned):
            if token in user_text or any(stop in token for stop in stop_terms):
                continue
            result.append(token)
            if len(result) >= 2:
                return result
    return result


def _memory_fact_max_chars(memory: MemoryRecallPacket) -> int:
    memory_text = "\n".join(_clean_text(fact) for fact in memory.facts)
    if re.search(r"可比较证据|看点[:：]|简介[:：]|题材[:：]|更新[:：]|热度[:：]", memory_text):
        return 900
    return 320


def _coerce_memory_packet(raw: MemoryRecallPacket | Mapping[str, Any] | None) -> MemoryRecallPacket:
    if raw is None:
        return MemoryRecallPacket()
    if isinstance(raw, Mapping):
        _reject_forbidden_raw_context(raw, source="memory_packet")
    packet = memory_recall_packet_from_mapping(raw)
    _reject_forbidden_raw_context(packet.as_dict(), source="memory_packet")
    return packet


def _coerce_affect_packet(raw: SurfaceAffectControlPacket | Mapping[str, Any] | None) -> SurfaceAffectControlPacket:
    if raw is None:
        return SurfaceAffectControlPacket()
    if isinstance(raw, Mapping):
        _reject_forbidden_raw_context(raw, source="affect_packet")
    packet = affect_control_packet_from_mapping(raw)
    _reject_forbidden_raw_context(packet.as_dict(), source="affect_packet")
    return packet


def _coerce_influence_packet(raw: SurfaceInfluencePacket | Mapping[str, Any] | None) -> SurfaceInfluencePacket:
    if raw is None:
        return SurfaceInfluencePacket()
    if isinstance(raw, Mapping):
        _reject_forbidden_raw_context(raw, source="influence_packet")
    packet = surface_influence_packet_from_mapping(raw)
    _reject_forbidden_raw_context(packet.as_dict(), source="influence_packet")
    return packet


def _affect_with_influence(
    affect: SurfaceAffectControlPacket,
    influence: SurfaceInfluencePacket,
) -> SurfaceAffectControlPacket:
    if not _influence_applied(influence):
        return affect
    emotional_tone = affect.emotional_tone or influence.affect.emotional_tone
    relationship_distance = affect.relationship_distance or influence.relationship.relationship_distance
    expression_intensity = affect.expression_intensity or influence.affect.expression_intensity
    return replace(
        affect,
        emotional_tone=emotional_tone,
        relationship_distance=relationship_distance,
        expression_intensity=expression_intensity,
    )


def _influence_applied(influence: SurfaceInfluencePacket) -> bool:
    return bool(
        influence.task.task_mode
        or influence.task.response_move
        or influence.affect.emotional_tone
        or influence.relationship.relationship_distance
        or influence.memory.facts
        or influence.continuity.recent_turns_injected
        or influence.safety.must_not_do
    )


def _reject_forbidden_raw_context(value: Any, *, source: str, prefix: str = "") -> None:
    paths: list[str] = []
    _collect_forbidden_paths(value, paths=paths, prefix=prefix)
    if paths:
        shown = ", ".join(f"{source}.{path}" for path in paths[:8])
        raise ValueError(f"surface packet compiler received forbidden raw context: {shown}")


def _collect_forbidden_paths(value: Any, *, paths: list[str], prefix: str = "") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            path = f"{prefix}.{key_text}" if prefix else key_text
            if _is_forbidden_raw_context_key(key_text):
                paths.append(path)
            _collect_forbidden_paths(child, paths=paths, prefix=path)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, child in enumerate(value):
            _collect_forbidden_paths(child, paths=paths, prefix=f"{prefix}[{index}]")


def _is_forbidden_raw_context_key(key: str) -> bool:
    normalized = str(key or "").strip().lower()
    if normalized in _FORBIDDEN_RAW_CONTEXT_KEYS:
        return True
    return any(hint in normalized for hint in _FORBIDDEN_RAW_CONTEXT_HINTS)


def _ensure_memory_fact_prefix(text: Any) -> str:
    value = _clean_text(text)
    if not value:
        return ""
    if any(value.startswith(prefix) for prefix in ("记忆线索：", "长期事实：", "召回线索：")):
        return value
    return f"记忆线索：{value}"


def _variant_case_id(case_id: str, variant: str) -> str:
    root = _clean_text(case_id) or "surface_shadow"
    return f"{root}:{variant}"


def _clean_text(value: Any) -> str:
    return str(value or "").replace("\u3000", " ").strip()


def _clean_list(
    values: Sequence[Any] | None,
    *,
    limit: int | None = None,
    max_chars: int = 180,
) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in list(values or []):
        text = _clean_text(value)
        if max_chars > 0:
            text = text[:max_chars].strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
        if limit is not None and len(cleaned) >= limit:
            break
    return cleaned


def _coerce_sequence(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes, bytearray)):
        text = _clean_text(value)
        return [text] if text else []
    if isinstance(value, Sequence):
        return list(value)
    return [value]


def _coerce_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _packet_as_dict(packet: ReplyControlPacket) -> dict[str, Any]:
    if hasattr(packet, "model_dump"):
        return packet.model_dump()  # type: ignore[no-any-return]
    return packet.dict()  # type: ignore[no-any-return]


def _input_as_dict(request: IsolatedSurfaceInput) -> dict[str, Any]:
    if hasattr(request, "model_dump"):
        return request.model_dump()  # type: ignore[no-any-return]
    return request.dict()  # type: ignore[no-any-return]


__all__ = [
    "CompiledSurfaceInput",
    "SurfacePacketCompileResult",
    "SurfaceTaskControl",
    "compile_reply_control_packet",
    "compile_surface_input",
    "compile_surface_shadow_inputs",
]
