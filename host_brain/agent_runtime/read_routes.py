"""Deterministic read-only routes outside the governed execution runtime.

``task_progress`` reads the durable Task projection and ``recent_turn`` reads
the durable conversation window.  Neither route creates a Task, an Attempt,
or a second execution authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


READ_ROUTE_UNSUPPORTED = "READ_ROUTE_UNSUPPORTED"
READ_PROJECTION_UNAVAILABLE = "READ_PROJECTION_UNAVAILABLE"


@dataclass(frozen=True)
class ReadProjectionResult:
    route: str
    narrative: str
    reason_code: str
    available: bool = True
    evidence: Mapping[str, Any] = field(default_factory=dict)


def build_read_projection(
    *,
    route: str,
    request_id: str,
    session_id: str,
    person_id: str,
    user_message: str,
    task_status_projection: Any,
    conversation_history: Any,
) -> ReadProjectionResult:
    normalized_route = str(route or "").strip().lower()
    if normalized_route == "task_progress":
        return _build_task_progress_projection(
            request_id=request_id,
            session_id=session_id,
            person_id=person_id,
            projection=task_status_projection,
        )
    if normalized_route == "recent_turn":
        return _build_recent_turn_projection(
            request_id=request_id,
            session_id=session_id,
            user_message=user_message,
            history=conversation_history,
        )
    return ReadProjectionResult(
        route=normalized_route,
        narrative="该读取路由未被支持。",
        reason_code=READ_ROUTE_UNSUPPORTED,
        available=False,
    )


def _build_task_progress_projection(
    *,
    request_id: str,
    session_id: str,
    person_id: str,
    projection: Any,
) -> ReadProjectionResult:
    try:
        if person_id:
            summary = projection.summarize_for_chat(
                person_id=person_id,
                session_id=session_id,
                exclude_request_id=request_id,
            )
        else:
            summary = projection.summarize_for_session(
                session_id=session_id,
                exclude_request_id=request_id,
            )
    except Exception as exc:
        return ReadProjectionResult(
            route="task_progress",
            narrative="暂时无法读取持久任务状态。",
            reason_code=READ_PROJECTION_UNAVAILABLE,
            available=False,
            evidence={"error_type": type(exc).__name__},
        )

    if not isinstance(summary, Mapping):
        return ReadProjectionResult(
            route="task_progress",
            narrative="当前没有可汇报的持久任务。",
            reason_code="TASK_PROGRESS_EMPTY",
            evidence={"projection": "durable_task_status", "found": False},
        )

    row = dict(summary)
    status = _text(row.get("status") or "UNKNOWN", limit=48)
    target = _text(row.get("target_id") or "task", limit=128)
    detail = _text(row.get("progress_message") or row.get("detail"), limit=500)
    result_text = _text(row.get("result_text"), limit=500)
    elapsed_ms = _non_negative_int(row.get("elapsed_ms"))
    narrative = f"当前任务 {target} 状态为 {status}，已运行 {_format_elapsed(elapsed_ms)}。"
    if detail:
        narrative += f" 当前进展：{detail}"
    if result_text and status.upper() in {"COMPLETED", "FAILED", "CANCELLED"}:
        narrative += f" 结果摘要：{result_text}"
    return ReadProjectionResult(
        route="task_progress",
        narrative=narrative,
        reason_code="TASK_PROGRESS_PROJECTED",
        evidence={
            "projection": "durable_task_status",
            "found": True,
            "status": status,
            "graph_id": _text(row.get("graph_id"), limit=255),
            "graph_revision": _non_negative_int(row.get("graph_revision")),
        },
    )


def _build_recent_turn_projection(
    *,
    request_id: str,
    session_id: str,
    user_message: str,
    history: Any,
) -> ReadProjectionResult:
    try:
        window = history.fetch_window_for_messages(
            session_id=session_id,
            max_turns=12,
            exclude_request_id=request_id,
            render_target_lang="zh",
        )
        turns = [
            {
                "role": str(getattr(turn, "role", "") or "").strip().lower(),
                "content": _text(getattr(turn, "content", ""), limit=800),
            }
            for turn in list(getattr(window, "recent_turns", ()) or ())
        ]
        turns = [row for row in turns if row["content"]]
    except Exception as exc:
        return ReadProjectionResult(
            route="recent_turn",
            narrative="暂时无法读取最近对话。",
            reason_code=READ_PROJECTION_UNAVAILABLE,
            available=False,
            evidence={"error_type": type(exc).__name__},
        )

    if not turns:
        return ReadProjectionResult(
            route="recent_turn",
            narrative="当前会话还没有可回顾的上一轮对话。",
            reason_code="RECENT_TURN_EMPTY",
            evidence={"projection": "conversation_history", "turn_count": 0},
        )

    normalized_query = str(user_message or "").strip().lower()
    asks_last_assistant = any(
        marker in normalized_query
        for marker in ("你刚刚说", "你刚才说", "你上一句", "what did you just say")
    )
    selected: list[dict[str, str]]
    if asks_last_assistant:
        last_assistant = next(
            (row for row in reversed(turns) if row["role"] == "assistant"),
            None,
        )
        selected = [last_assistant] if last_assistant is not None else [turns[-1]]
    else:
        selected = turns[-4:]
    lines = [
        f"{'我' if row['role'] == 'assistant' else '你'}：{row['content']}"
        for row in selected
    ]
    return ReadProjectionResult(
        route="recent_turn",
        narrative="\n".join(lines),
        reason_code="RECENT_TURN_PROJECTED",
        evidence={
            "projection": "conversation_history",
            "turn_count": len(selected),
            "roles": [row["role"] for row in selected],
        },
    )


def _text(value: Any, *, limit: int) -> str:
    return " ".join(str(value or "").strip().split())[: max(1, int(limit))]


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _format_elapsed(elapsed_ms: int) -> str:
    seconds = max(0, int(elapsed_ms) // 1_000)
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


__all__ = [
    "READ_PROJECTION_UNAVAILABLE",
    "READ_ROUTE_UNSUPPORTED",
    "ReadProjectionResult",
    "build_read_projection",
]
