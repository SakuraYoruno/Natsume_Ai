from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from host_brain.core.runtime_helpers import _clean_text
from host_brain.persona.surface.data_models import (
    IsolatedSurfaceInput,
    ReplyControlPacket,
)
from host_brain.persona.surface.input_validation import (
    build_isolated_surface_input,
    find_forbidden_input_paths,
)


@dataclass(frozen=True)
class ContinuityTurnSurfaceInput:
    case_id: str
    turn_id: str
    turn_index: int
    request: IsolatedSurfaceInput
    source_turn: Mapping[str, Any]

    def as_trace(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "turn_id": self.turn_id,
            "turn_index": self.turn_index,
            "surface_case_id": self.request.case_id,
        }


@dataclass(frozen=True)
class CompressedContinuitySnapshot:
    turn_count: int = 0
    thread_summary: str = ""
    topic_state: str = ""
    last_turn_summary: str = ""
    open_loop: str = ""
    emotional_state: str = ""
    distance_note: str = "熟人私聊，不升级亲密。"

    def facts(self, *, extra: Sequence[str] | None = None, max_facts: int = 6) -> tuple[str, ...]:
        rows = [
            f"连续摘要：{self.thread_summary}" if self.thread_summary else "",
            f"当前主题：{self.topic_state}" if self.topic_state else "",
            f"上一轮摘要：{self.last_turn_summary}" if self.last_turn_summary else "",
            f"未完成话口：{self.open_loop}" if self.open_loop else "",
            *list(extra or ()),
            f"情绪状态：{self.emotional_state}" if self.emotional_state else "",
            f"关系距离：{self.distance_note}" if self.distance_note else "",
        ]
        return tuple(_clean_list(rows)[: max(1, int(max_facts or 6))])


def _clean_list(values: Sequence[Any] | None) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in list(values or []):
        text = _clean_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned


class ContinuitySummaryCompressor:
    """Deterministic rolling summary for long isolated continuity tests.

    It stores compressed state labels and short summaries, not raw message
    arrays. Runtime can replace this with a model or state-machine compressor,
    as long as the final surface layer still receives only the emitted facts.
    """

    def __init__(
        self,
        *,
        thread_summary: str,
        topic_state: str = "",
        open_loop: str = "",
        emotional_state: str = "平稳",
        distance_note: str = "熟人私聊，不升级亲密。",
    ) -> None:
        self._snapshot = CompressedContinuitySnapshot(
            thread_summary=_clean_text(thread_summary),
            topic_state=_clean_text(topic_state),
            open_loop=_clean_text(open_loop),
            emotional_state=_clean_text(emotional_state),
            distance_note=_clean_text(distance_note),
        )

    @property
    def snapshot(self) -> CompressedContinuitySnapshot:
        return self._snapshot

    def facts_for_next_turn(self, *, extra: Sequence[str] | None = None, max_facts: int = 6) -> tuple[str, ...]:
        return self._snapshot.facts(extra=extra, max_facts=max_facts)

    def observe_turn(
        self,
        *,
        user_summary: str,
        assistant_summary: str,
        topic_state: str | None = None,
        open_loop: str | None = None,
        emotional_state: str | None = None,
        distance_note: str | None = None,
    ) -> CompressedContinuitySnapshot:
        last_turn_summary = "；".join(_clean_list([user_summary, assistant_summary]))
        self._snapshot = CompressedContinuitySnapshot(
            turn_count=self._snapshot.turn_count + 1,
            thread_summary=self._snapshot.thread_summary,
            topic_state=_clean_text(topic_state if topic_state is not None else self._snapshot.topic_state),
            last_turn_summary=last_turn_summary,
            open_loop=_clean_text(open_loop if open_loop is not None else self._snapshot.open_loop),
            emotional_state=_clean_text(emotional_state if emotional_state is not None else self._snapshot.emotional_state),
            distance_note=_clean_text(distance_note if distance_note is not None else self._snapshot.distance_note),
        )
        return self._snapshot


@dataclass(frozen=True)
class RecentDialogueTurn:
    role: str
    text: str


@dataclass(frozen=True)
class ThreeLayerContinuityContext:
    """Raw context allowed only before the surface boundary.

    The compressor may inspect recent raw turns and longer summaries here, but
    the final surface prompt must receive only the resulting ReplyControlPacket.
    """

    current_user_message: str
    recent_turns: Sequence[RecentDialogueTurn | Mapping[str, Any]] = ()
    mid_term_summary: str = ""
    long_term_facts: Sequence[str] = ()
    task_mode_hint: str = "daily_chat"
    response_move_hint: str = ""
    emotional_tone_hint: str = "light"
    relationship_distance: str = "familiar"
    information_budget: str = "short"
    question_budget: int = 0
    must_include: Sequence[str] = ()
    must_not_do: Sequence[str] = ()
    necessary_facts: Sequence[str] = ()
    recall_facts: Sequence[str] = ()
    style_anchor: Sequence[str] = ()


def _coerce_recent_turn(raw: RecentDialogueTurn | Mapping[str, Any]) -> RecentDialogueTurn | None:
    if isinstance(raw, RecentDialogueTurn):
        role = _clean_text(raw.role).lower()
        text = _clean_text(raw.text)
    elif isinstance(raw, Mapping):
        role = _clean_text(raw.get("role") or raw.get("speaker")).lower()
        text = _clean_text(raw.get("text") or raw.get("content"))
    else:
        return None
    if not role or not text:
        return None
    return RecentDialogueTurn(role=role, text=text)


def _recent_turns(raw_turns: Sequence[RecentDialogueTurn | Mapping[str, Any]], *, max_recent_turns: int) -> tuple[RecentDialogueTurn, ...]:
    turns = [turn for item in raw_turns for turn in [_coerce_recent_turn(item)] if turn is not None]
    return tuple(turns[-max(0, int(max_recent_turns or 0)) :])


def _last_turn_with_role(turns: Sequence[RecentDialogueTurn], roles: set[str]) -> RecentDialogueTurn | None:
    for turn in reversed(turns):
        if turn.role in roles:
            return turn
    return None


def _summarize_user_intent(text: str) -> str:
    normalized = _clean_text(text)
    if not normalized:
        return ""
    if normalized in {"嗯", "嗯嗯", "好", "继续", "然后呢"}:
        return "用户用短确认延续当前话题。"
    if any(token in normalized for token in ("报错", "红字", "错误", "Traceback", "traceback")):
        return "用户在继续技术求助。"
    if any(token in normalized for token in ("身份", "名字", "旧角色", "当前人格", "设定")):
        return "用户在追问身份锚点。"
    if any(token in normalized for token in ("累", "困", "头疼", "难受")):
        return "用户表达低能量状态。"
    if any(token in normalized for token in ("怎么", "多少", "为什么", "哪", "吗")):
        return "用户在追问做法或判断。"
    return "用户继续当前话题。"


def _summarize_assistant_move(text: str) -> str:
    normalized = _clean_text(text)
    if not normalized:
        return ""
    if any(token in normalized for token in ("先", "一步", "一处", "看", "贴", "红字")):
        return "助手已给出最小下一步。"
    if any(token in normalized for token in ("旧角色", "当前人格", "名字", "设定")):
        return "助手已收束身份锚点。"
    if any(token in normalized for token in ("压缩", "摘要", "事实", "历史")):
        return "助手已说明压缩边界。"
    if any(token in normalized for token in ("别硬撑", "缓", "歇")):
        return "助手已轻接情绪和体力状态。"
    if any(token in normalized for token in ("继续", "接着", "在听")):
        return "助手已短确认并延续话题。"
    return "助手已回应上一轮。"


def _infer_topic_state(current_user_message: str, task_mode: str, long_term_facts: Sequence[str]) -> str:
    text = _clean_text(current_user_message)
    task = _clean_text(task_mode)
    if any(token in text for token in ("摘要", "压缩", "历史", "facts")):
        return "连续性摘要压缩。"
    if any(token in text for token in ("身份", "名字", "旧角色", "当前人格", "设定")) or task == "identity":
        return "身份锚点压缩。"
    if any(token in text for token in ("报错", "红字", "错误")) or task in {"help_takeover", "write_takeover"}:
        return "技术求助最小入口。"
    facts = _clean_list(long_term_facts)
    if facts:
        return facts[0]
    return "当前话题延续。"


def _infer_open_loop(current_user_message: str, task_mode: str) -> str:
    text = _clean_text(current_user_message)
    task = _clean_text(task_mode)
    if any(token in text for token in ("怎么", "多少", "哪", "为什么")) or task in {"next_step", "advice_continue"}:
        return "下一轮继续围绕当前问题给最小可执行回应。"
    if task == "identity":
        return "后续追问只保留名字和边界，不新增关系设定。"
    if task in {"help_takeover", "write_takeover"}:
        return "等待用户给关键材料，不展开客服话术。"
    return "下一轮继续承接当前话题。"


def compress_three_layer_continuity_context(
    context: ThreeLayerContinuityContext,
    *,
    max_recent_turns: int = 6,
) -> CompressedContinuitySnapshot:
    """Compress recent raw turns plus summaries into a surface-safe snapshot."""

    turns = _recent_turns(context.recent_turns, max_recent_turns=max_recent_turns)
    last_user = _last_turn_with_role(turns, {"user", "human", "对方"})
    last_assistant = _last_turn_with_role(turns, {"assistant", "ai", "旧角色", "当前人格"})
    thread_summary = _clean_text(context.mid_term_summary) or "围绕当前对话继续。"
    last_turn_summary = "；".join(
        _clean_list(
            [
                _summarize_user_intent(last_user.text if last_user else ""),
                _summarize_assistant_move(last_assistant.text if last_assistant else ""),
            ]
        )
    )
    return CompressedContinuitySnapshot(
        turn_count=len(turns),
        thread_summary=thread_summary,
        topic_state=_infer_topic_state(context.current_user_message, context.task_mode_hint, context.long_term_facts),
        last_turn_summary=last_turn_summary,
        open_loop=_infer_open_loop(context.current_user_message, context.task_mode_hint),
        emotional_state=_clean_text(context.emotional_tone_hint) or "light",
        distance_note="熟人私聊，不升级亲密。",
    )


def build_reply_control_packet_from_context(
    context: ThreeLayerContinuityContext,
    *,
    memory_packet: Any | None = None,
    affect_packet: Any | None = None,
    max_facts: int = 6,
    max_recent_turns: int = 6,
) -> ReplyControlPacket:
    """Build the only object allowed to cross into the surface generator."""

    from host_brain.persona.surface_packet_compiler import compile_reply_control_packet  # noqa: PLC0415

    snapshot = compress_three_layer_continuity_context(context, max_recent_turns=max_recent_turns)
    long_facts = [f"长期事实：{fact}" for fact in _clean_list(context.long_term_facts)[:2]]
    facts = snapshot.facts(
        extra=[*_clean_list(context.necessary_facts), *_clean_list(context.recall_facts), *long_facts],
        max_facts=max_facts,
    )
    task_mode = _clean_text(context.task_mode_hint) or "daily_chat"
    base_affect_packet = _affect_packet_with_context_defaults(context, affect_packet)
    compiled = compile_reply_control_packet(
        user_message=context.current_user_message,
        task_control={
            "response_move": _clean_text(context.response_move_hint) or task_mode,
            "task_mode": task_mode,
            "information_budget": _clean_text(context.information_budget) or "short",
            "question_budget": max(0, int(context.question_budget or 0)),
            "must_include": _clean_list(context.must_include),
            "must_not_do": _clean_list(context.must_not_do),
            "facts": list(facts),
            "style_anchor": _clean_list([*context.style_anchor, "压缩连续性"]),
        },
        memory_packet=memory_packet,
        affect_packet=base_affect_packet,
        max_facts=max_facts,
    )
    return compiled.packet


def build_surface_input_from_three_layer_context(
    context: ThreeLayerContinuityContext,
    *,
    soul_md: str,
    case_id: str = "",
    memory_packet: Any | None = None,
    affect_packet: Any | None = None,
    max_facts: int = 6,
    max_recent_turns: int = 6,
    # --- Inner-state leakage fields (life-feel renovation A5) ---
    inner_weather: str = "",
    body_state_hint: str = "",
    care_signal: float = 0.0,
    continuity_signal: float = 0.0,
    tension_hint: str = "",
    liveliness: float = 0.5,
    active_loops: int = 0,
) -> IsolatedSurfaceInput:
    control = build_reply_control_packet_from_context(
        context,
        memory_packet=memory_packet,
        affect_packet=affect_packet,
        max_facts=max_facts,
        max_recent_turns=max_recent_turns,
    )
    return build_isolated_surface_input(
        {
            "case_id": case_id,
            "soul_md": soul_md,
            "user_message": context.current_user_message,
            "reply_control_packet": control,
            # --- Inner-state leakage pass-through ---
            "inner_weather": inner_weather,
            "body_state_hint": body_state_hint,
            "care_signal": care_signal,
            "continuity_signal": continuity_signal,
            "tension_hint": tension_hint,
            "liveliness": liveliness,
            "active_loops": active_loops,
        }
    )


_CASE_KEYS = {
    "case_id",
    "soul_md",
    "turns",
}
_TURN_INPUT_KEYS = {
    "control",
    "facts",
    "must_not_do",
    "reply_control_packet",
    "turn_id",
    "user_message",
}
_TURN_EVAL_KEYS = {
    "candidate_payload",
    "candidates",
    "expected_reference",
    "expected_selected",
    "model_output",
    "notes",
}
_TURN_REQUEST_KEYS = {
    "control",
    "facts",
    "must_not_do",
    "reply_control_packet",
    "user_message",
    # --- Inner-state leakage fields (life-feel renovation A5) ---
    "inner_weather",
    "body_state_hint",
    "care_signal",
    "continuity_signal",
    "tension_hint",
    "liveliness",
    "active_loops",
}


def _affect_packet_with_context_defaults(
    context: ThreeLayerContinuityContext,
    affect_packet: Any | None,
) -> Any:
    base = {
        "emotional_tone": _clean_text(context.emotional_tone_hint) or "light",
        "relationship_distance": _clean_text(context.relationship_distance) or "familiar",
    }
    if affect_packet is None:
        return base
    if isinstance(affect_packet, Mapping):
        payload = dict(affect_packet)
    elif hasattr(affect_packet, "as_dict"):
        raw = affect_packet.as_dict()  # type: ignore[attr-defined]
        payload = dict(raw) if isinstance(raw, Mapping) else {}
    else:
        return affect_packet
    if not _clean_text(payload.get("emotional_tone")):
        payload["emotional_tone"] = base["emotional_tone"]
    if not _clean_text(payload.get("relationship_distance")):
        payload["relationship_distance"] = base["relationship_distance"]
    return payload


def _without_eval_only(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _without_eval_only(item)
            for key, item in value.items()
            if str(key) not in _TURN_EVAL_KEYS
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_without_eval_only(item) for item in value]
    return value


def find_forbidden_continuity_input_paths(payload: Any) -> tuple[str, ...]:
    """Find raw runtime context in continuity cases, ignoring eval-only outputs."""

    return find_forbidden_input_paths(_without_eval_only(payload))


def _validate_case_payload(payload: Mapping[str, Any]) -> None:
    unexpected = sorted(str(key) for key in payload.keys() if str(key) not in _CASE_KEYS)
    if unexpected:
        raise ValueError("continuity case contains unexpected fields: " + ", ".join(unexpected[:8]))
    forbidden = find_forbidden_continuity_input_paths(payload)
    if forbidden:
        raise ValueError("continuity case contains forbidden raw context: " + ", ".join(forbidden[:8]))
    turns = payload.get("turns")
    if isinstance(turns, (str, bytes, bytearray)) or not isinstance(turns, Sequence) or not turns:
        raise ValueError("continuity case requires a non-empty turns list")


def _validate_turn_payload(turn: Mapping[str, Any], *, index: int) -> None:
    allowed = _TURN_INPUT_KEYS | _TURN_EVAL_KEYS
    unexpected = sorted(str(key) for key in turn.keys() if str(key) not in allowed)
    if unexpected:
        raise ValueError(f"continuity turn {index} contains unexpected fields: " + ", ".join(unexpected[:8]))
    if "control" not in turn and "reply_control_packet" not in turn:
        raise ValueError(f"continuity turn {index} requires ReplyControlPacket")
    if not _clean_text(turn.get("user_message")):
        raise ValueError(f"continuity turn {index} requires user_message")


def build_continuity_surface_inputs(
    payload: Mapping[str, Any],
    *,
    soul_md: str | None = None,
) -> tuple[ContinuityTurnSurfaceInput, ...]:
    """Build per-turn isolated surface inputs from a compressed continuity case.

    The final surface generator still sees only the current turn's user input,
    ReplyControlPacket, facts, and must_not_do. Previous-turn continuity must be
    represented upstream as compressed facts, never as raw message history.
    """

    _validate_case_payload(payload)
    case_id = _clean_text(payload.get("case_id")) or "continuity_case"
    soul = _clean_text(soul_md if soul_md is not None else payload.get("soul_md"))
    rows: list[ContinuityTurnSurfaceInput] = []
    for index, raw_turn in enumerate(payload["turns"], start=1):  # type: ignore[index]
        if not isinstance(raw_turn, Mapping):
            raise ValueError(f"continuity turn {index} must be a JSON object")
        _validate_turn_payload(raw_turn, index=index)
        turn_id = _clean_text(raw_turn.get("turn_id")) or f"t{index:02d}"
        request_payload = {key: raw_turn[key] for key in _TURN_REQUEST_KEYS if key in raw_turn}
        request_payload["soul_md"] = soul
        request_payload["case_id"] = f"{case_id}/{turn_id}"
        request = build_isolated_surface_input(request_payload)
        rows.append(
            ContinuityTurnSurfaceInput(
                case_id=case_id,
                turn_id=turn_id,
                turn_index=index,
                request=request,
                source_turn=raw_turn,
            )
        )
    return tuple(rows)


__all__ = [
    "CompressedContinuitySnapshot",
    "ContinuitySummaryCompressor",
    "ContinuityTurnSurfaceInput",
    "RecentDialogueTurn",
    "ThreeLayerContinuityContext",
    "build_continuity_surface_inputs",
    "build_reply_control_packet_from_context",
    "build_surface_input_from_three_layer_context",
    "compress_three_layer_continuity_context",
    "find_forbidden_continuity_input_paths",
]
