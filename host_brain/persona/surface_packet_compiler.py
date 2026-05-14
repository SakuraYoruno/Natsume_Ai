from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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
        "memory",
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
    task_control: SurfaceTaskControl | Mapping[str, Any] | None = None,
    memory_packet: MemoryRecallPacket | Mapping[str, Any] | None = None,
    affect_packet: SurfaceAffectControlPacket | Mapping[str, Any] | None = None,
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

    emotional_tone = affect.emotional_tone or "neutral"
    relationship_distance = affect.relationship_distance or "familiar"
    facts = _clean_list(
        [
            *task.facts,
            *_coerce_sequence(necessary_facts),
            *(_ensure_memory_fact_prefix(fact) for fact in memory.facts),
        ],
        limit=max_facts,
        max_chars=180,
    )
    packet_style_anchor = _clean_list(
        [
            *task.style_anchor,
            *_coerce_sequence(style_anchor),
            *memory.style_anchor,
            *affect.style_anchor,
        ],
        limit=max_style_anchors,
        max_chars=180,
    )
    packet_must_not_do = _clean_list(
        [
            *task.must_not_do,
            *_coerce_sequence(must_not_do),
            *memory.must_not_do,
            *affect.must_not_do,
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
        must_include=list(task.must_include),
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
        "fact_count": len(packet.facts),
        "style_anchor_count": len(packet.style_anchor),
        "must_not_do_count": len(packet.must_not_do),
        "dropped_memory_relationship_clues": bool(memory.relationship_clues),
        "surface_boundary": "ReplyControlPacket",
    }
    return SurfacePacketCompileResult(packet=packet, trace=trace)


def compile_surface_input(
    *,
    soul_md: str,
    user_message: str,
    case_id: str = "",
    task_control: SurfaceTaskControl | Mapping[str, Any] | None = None,
    memory_packet: MemoryRecallPacket | Mapping[str, Any] | None = None,
    affect_packet: SurfaceAffectControlPacket | Mapping[str, Any] | None = None,
    necessary_facts: Sequence[Any] | None = None,
    must_not_do: Sequence[Any] | None = None,
    style_anchor: Sequence[Any] | None = None,
    max_facts: int = 8,
    max_style_anchors: int = 10,
    max_must_not_do: int = 12,
) -> CompiledSurfaceInput:
    compiled = compile_reply_control_packet(
        task_control=task_control,
        memory_packet=memory_packet,
        affect_packet=affect_packet,
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
