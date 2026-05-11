from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from host_brain.persona.isolated_surface_harness import (
    IsolatedSurfaceInput,
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
}


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


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
    "build_continuity_surface_inputs",
    "find_forbidden_continuity_input_paths",
]
