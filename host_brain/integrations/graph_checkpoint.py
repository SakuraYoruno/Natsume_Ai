"""Checkpoint / interrupt / resume primitives backed by LangGraph SqliteSaver."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

from host_brain.integrations.graph_contract import (
    DEFAULT_RESUME_DEADLINE_MS,
    INTERRUPT_REASON_HUMAN_REVIEW,
    RESUME_DENIED_DEADLINE_EXCEEDED,
    RESUME_DENIED_POLICY_EXPIRED,
    SYNTHETIC_EVENT_TIMEOUT,
)

_logger = logging.getLogger(__name__)

_ENV_DB_PATH = "HOST_CHECKPOINT_DB"
_DEFAULT_DB_PATH = "host_brain/data/checkpoints.db"


def get_checkpoint_db_path() -> str:
    """Resolve checkpoint DB path from env."""

    return str(os.environ.get(_ENV_DB_PATH, _DEFAULT_DB_PATH)).strip() or _DEFAULT_DB_PATH


@dataclass
class InterruptSignal:
    checkpoint_id: str
    interrupt_reason: str = INTERRUPT_REASON_HUMAN_REVIEW
    interrupted_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    resume_deadline_at: int = field(
        default_factory=lambda: int(time.time() * 1000) + DEFAULT_RESUME_DEADLINE_MS
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "interrupt_reason": self.interrupt_reason,
            "interrupted_at_ms": self.interrupted_at_ms,
            "resume_deadline_at": self.resume_deadline_at,
        }


@dataclass
class SyntheticTimeoutEvent:
    node_result: str = SYNTHETIC_EVENT_TIMEOUT
    error_code: str = RESUME_DENIED_DEADLINE_EXCEEDED
    route_token: str = "GOV_FALLBACK_SECONDARY"
    checkpoint_id: str = ""
    ts_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_result": self.node_result,
            "error_code": self.error_code,
            "route_token": self.route_token,
            "checkpoint_id": self.checkpoint_id,
            "ts_ms": self.ts_ms,
        }


@dataclass
class ResumeDecision:
    allowed: bool
    reason: str = ""
    synthetic_event: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "synthetic_event": self.synthetic_event,
        }


class HostPolicyGuard:
    """Host-side resume gate (Policy Clock)."""

    def check_resume_allowed(
        self,
        *,
        interrupt_signal: InterruptSignal,
        now_ms: int | None = None,
        policy_timeout_ms: int | None = None,
        policy_start_ms: int | None = None,
    ) -> ResumeDecision:
        current_ms = int(now_ms if now_ms is not None else time.time() * 1000)

        if current_ms > interrupt_signal.resume_deadline_at:
            synthetic = SyntheticTimeoutEvent(
                checkpoint_id=interrupt_signal.checkpoint_id,
                error_code=RESUME_DENIED_DEADLINE_EXCEEDED,
                ts_ms=current_ms,
            )
            return ResumeDecision(
                allowed=False,
                reason=(
                    f"resume deadline exceeded "
                    f"(resume_deadline_at={interrupt_signal.resume_deadline_at}, now={current_ms})"
                ),
                synthetic_event=synthetic.to_dict(),
            )

        if policy_timeout_ms is not None and policy_start_ms is not None:
            elapsed = current_ms - int(policy_start_ms)
            if elapsed > int(policy_timeout_ms):
                synthetic = SyntheticTimeoutEvent(
                    checkpoint_id=interrupt_signal.checkpoint_id,
                    error_code=RESUME_DENIED_POLICY_EXPIRED,
                    ts_ms=current_ms,
                )
                return ResumeDecision(
                    allowed=False,
                    reason=f"policy clock timeout (elapsed={elapsed}ms, timeout={policy_timeout_ms}ms)",
                    synthetic_event=synthetic.to_dict(),
                )

        return ResumeDecision(
            allowed=True,
            reason="policy clock check passed",
            synthetic_event=None,
        )


_langgraph_checkpointer: Any = None


def get_langgraph_checkpointer() -> Any:
    """Get lazy singleton SqliteSaver."""

    global _langgraph_checkpointer
    if _langgraph_checkpointer is not None:
        return _langgraph_checkpointer

    import pathlib

    db_path = get_checkpoint_db_path()
    pathlib.Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    from langgraph.checkpoint.sqlite import SqliteSaver

    _langgraph_checkpointer = SqliteSaver.from_conn_string(db_path)
    _logger.info("LangGraph SqliteSaver initialized db_path=%s", db_path)
    return _langgraph_checkpointer


def compile_graph_with_checkpoint(graph: Any) -> Any:
    """Compile StateGraph with LangGraph SqliteSaver."""

    checkpointer = get_langgraph_checkpointer()
    return graph.compile(checkpointer=checkpointer)


def build_interrupt_signal(
    *,
    state: dict[str, Any],
    interrupt_reason: str = INTERRUPT_REASON_HUMAN_REVIEW,
    resume_deadline_ms: int | None = None,
    now_ms: int | None = None,
) -> InterruptSignal:
    """Create interrupt metadata (checkpoint_id aligns with LangGraph thread_id)."""

    thread_id = str(state.get("request_id") or "").strip()
    if not thread_id:
        import uuid

        thread_id = str(uuid.uuid4())
    current_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    deadline_ms = int(resume_deadline_ms if resume_deadline_ms is not None else DEFAULT_RESUME_DEADLINE_MS)
    return InterruptSignal(
        checkpoint_id=thread_id,
        interrupt_reason=str(interrupt_reason),
        interrupted_at_ms=current_ms,
        resume_deadline_at=current_ms + deadline_ms,
    )


def check_resume_allowed(
    *,
    interrupt_signal: InterruptSignal,
    now_ms: int | None = None,
    policy_timeout_ms: int | None = None,
    policy_start_ms: int | None = None,
    policy_guard: HostPolicyGuard | None = None,
) -> ResumeDecision:
    """Evaluate resume permission via policy guard."""

    guard = policy_guard or HostPolicyGuard()
    return guard.check_resume_allowed(
        interrupt_signal=interrupt_signal,
        now_ms=now_ms,
        policy_timeout_ms=policy_timeout_ms,
        policy_start_ms=policy_start_ms,
    )



class CheckpointManager:
    """检查点管理器（占位实现，供测试 monkeypatch 使用）。"""

    def interrupt(self, state: Any, **kwargs: Any) -> str | None:
        """保存检查点并返回 checkpoint_id（None 表示未保存）。"""
        return None

    def resume(self, checkpoint_id: str, **kwargs: Any) -> dict[str, Any] | None:
        """恢复检查点状态（None 表示未找到）。"""
        return None
