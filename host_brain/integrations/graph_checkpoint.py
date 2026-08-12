"""图运行时的中断、检查点与恢复原语。

本模块把 LangGraph 的保存器封装在宿主策略边界之后：图可以保存执行进度，
但是否允许从该进度继续，仍必须由策略时钟重新裁决，不能把“存在检查点”
误当成“拥有恢复授权”。
"""

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
    """解析检查点数据库位置，并为未配置环境保留稳定的本地默认值。"""

    return str(os.environ.get(_ENV_DB_PATH, _DEFAULT_DB_PATH)).strip() or _DEFAULT_DB_PATH


@dataclass
class InterruptSignal:
    """写入状态的中断事实；``checkpoint_id`` 与 LangGraph 的线程身份对齐。"""
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
    """恢复被策略拒绝时生成的合成节点结果，供后续路由和审计统一处理。"""
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
    """策略守卫的唯一恢复判定；拒绝时可携带可追溯的合成超时事件。"""
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
    """宿主侧恢复闸门。

    检查点只保存执行位置，不授予继续执行的权力。本守卫同时检查人工处理
    截止时间和整图策略时钟，任一耗尽均 fail closed，避免陈旧状态绕过当前
    治理规则后重新进入图。
    """

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
            # 截止期超限后不尝试“宽容恢复”：下游应按常规失败路径消费该事件。
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
                # 策略预算覆盖暂停时长，避免等待人工审核成为绕开全局超时的通道。
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
    """按进程惰性创建唯一的 LangGraph 检查点保存器。

    优先选择 SQLite，使进程重启后仍可恢复；可选后端缺失时才退回内存保存器。
    该降级保持图可运行，却明确失去跨进程恢复能力，日志会留下该运行边界。
    """

    global _langgraph_checkpointer
    if _langgraph_checkpointer is not None:
        return _langgraph_checkpointer

    import pathlib

    db_path = get_checkpoint_db_path()
    pathlib.Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ModuleNotFoundError as exc:
        from langgraph.checkpoint.memory import MemorySaver

        _langgraph_checkpointer = MemorySaver()
        _logger.warning(
            "LangGraph SqliteSaver unavailable, falling back to MemorySaver: %s",
            exc,
        )
        return _langgraph_checkpointer

    _langgraph_checkpointer = SqliteSaver.from_conn_string(db_path)
    _logger.info("LangGraph SqliteSaver initialized db_path=%s", db_path)
    return _langgraph_checkpointer


def compile_graph_with_checkpoint(graph: Any) -> Any:
    """为图注入统一保存器后再编译，避免各调用方私自选择恢复介质。"""

    checkpointer = get_langgraph_checkpointer()
    return graph.compile(checkpointer=checkpointer)


def build_interrupt_signal(
    *,
    state: dict[str, Any],
    interrupt_reason: str = INTERRUPT_REASON_HUMAN_REVIEW,
    resume_deadline_ms: int | None = None,
    now_ms: int | None = None,
) -> InterruptSignal:
    """构造中断元数据，并让检查点身份与请求线程身份一致。

    没有请求 ID 时生成新的标识，以防无关执行共享可恢复状态；调用方传入的
    时钟值则用于重放或测试，使截止计算保持确定性。
    """

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
    """通过策略守卫重新裁决恢复许可，而非直接信任调用方或检查点。"""

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
