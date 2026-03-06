"""Checkpoint / Interrupt / Resume 核心实现（M39-A）。

实现双时钟模型下的断点续跑（ModelPlane.md §10 Rule 6）：
- CheckpointStore：内存 + SQLite 双层存储
- InterruptSignal：中断信号（checkpoint_id、interrupt_reason、resume_deadline_at）
- HostPolicyGuard：恢复前由 Host 检查 Policy Clock 是否允许恢复
- ResumeDecision：恢复决策（allowed、reason、synthetic_event）
- SyntheticTimeoutEvent：超时合成事件（TIMEOUT_SYNTHETIC）
- CheckpointManager：组合 CheckpointStore + HostPolicyGuard

设计原则（LangGraph_Governed_Runtime_Solution §4.4）：
- LangGraph 负责"能不能恢复"（Execution Clock）
- Host 负责"允不允许恢复"（Policy Clock）
- 超时时写入 SyntheticTimeoutEvent 而非直接恢复
- 两者职责分离后，断点续跑与时间驱动降级不再冲突
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from host_brain.integrations.graph_contract import (
    DEFAULT_RESUME_DEADLINE_MS,
    INTERRUPT_REASON_AIRLOCK,
    INTERRUPT_REASON_HUMAN_REVIEW,
    INTERRUPT_REASON_POLICY_TIMEOUT,
    RESUME_DENIED_DEADLINE_EXCEEDED,
    RESUME_DENIED_POLICY_EXPIRED,
    SYNTHETIC_EVENT_TIMEOUT,
)


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class InterruptSignal:
    """中断信号：保存 checkpoint 后产生，携带恢复所需的元信息。"""
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
class ResumeRequest:
    """恢复请求：由外部（人工或自动）发起，携带 checkpoint_id。"""
    checkpoint_id: str
    requested_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    requester: str = "system"

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "requested_at_ms": self.requested_at_ms,
            "requester": self.requester,
        }


@dataclass
class SyntheticTimeoutEvent:
    """超时合成事件：Policy Clock 超时时写入，替代直接恢复。

    包含 node_result=TIMEOUT_SYNTHETIC、error_code=DEADLINE_EXCEEDED、
    route_token=GOV_FALLBACK_SECONDARY。
    """
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
    """恢复决策：HostPolicyGuard 检查后产生。"""
    allowed: bool
    reason: str = ""
    synthetic_event: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "synthetic_event": self.synthetic_event,
        }


# ---------------------------------------------------------------------------
# CheckpointStore：内存 + SQLite 双层存储
# ---------------------------------------------------------------------------

class CheckpointStore:
    """Checkpoint 存储器（内存 + 可选 SQLite 持久化）。

    内存层：快速读写，进程内有效
    SQLite 层：持久化，跨进程/重启有效（可选）
    """

    def __init__(self, *, db_path: str | None = None) -> None:
        self._store: dict[str, dict[str, Any]] = {}
        self._db_path = db_path
        if db_path:
            self._init_db(db_path)

    def _init_db(self, db_path: str) -> None:
        """初始化 SQLite 数据库（可选）。"""
        try:
            import sqlite3
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS checkpoints (
                        checkpoint_id TEXT PRIMARY KEY,
                        state_json TEXT NOT NULL,
                        created_at_ms INTEGER NOT NULL
                    )"""
                )
        except Exception:
            pass  # 静默降级

    def save(self, state: dict[str, Any]) -> str:
        """保存 GraphState 快照，返回唯一 checkpoint_id。

        Args:
            state: GraphState 字典

        Returns:
            唯一 checkpoint_id（UUID4）
        """
        checkpoint_id = str(uuid.uuid4())
        # 内存层
        self._store[checkpoint_id] = dict(state)
        # SQLite 层（可选）
        if self._db_path:
            try:
                import sqlite3
                state_json = json.dumps(state, ensure_ascii=False, default=str)
                with sqlite3.connect(self._db_path) as conn:
                    conn.execute(
                        "INSERT INTO checkpoints (checkpoint_id, state_json, created_at_ms) VALUES (?, ?, ?)",
                        (checkpoint_id, state_json, int(time.time() * 1000)),
                    )
            except Exception:
                pass  # 静默降级
        return checkpoint_id

    def load(self, checkpoint_id: str) -> dict[str, Any] | None:
        """加载 checkpoint，返回 GraphState 字典。

        Args:
            checkpoint_id: checkpoint ID

        Returns:
            GraphState 字典，若不存在则返回 None
        """
        # 优先从内存层读取
        if checkpoint_id in self._store:
            return dict(self._store[checkpoint_id])
        # 从 SQLite 层读取（可选）
        if self._db_path:
            try:
                import sqlite3
                with sqlite3.connect(self._db_path) as conn:
                    cursor = conn.execute(
                        "SELECT state_json FROM checkpoints WHERE checkpoint_id = ?",
                        (checkpoint_id,),
                    )
                    row = cursor.fetchone()
                    if row:
                        state = json.loads(row[0])
                        # 回填内存层
                        self._store[checkpoint_id] = state
                        return dict(state)
            except Exception:
                pass  # 静默降级
        return None

    def exists(self, checkpoint_id: str) -> bool:
        """检查 checkpoint 是否存在。"""
        if checkpoint_id in self._store:
            return True
        if self._db_path:
            try:
                import sqlite3
                with sqlite3.connect(self._db_path) as conn:
                    cursor = conn.execute(
                        "SELECT 1 FROM checkpoints WHERE checkpoint_id = ?",
                        (checkpoint_id,),
                    )
                    return cursor.fetchone() is not None
            except Exception:
                pass
        return False


# ---------------------------------------------------------------------------
# HostPolicyGuard：恢复前置检查
# ---------------------------------------------------------------------------

class HostPolicyGuard:
    """Host 策略守卫：恢复前由 Host 检查 Policy Clock 是否允许恢复。

    职责：
    - 检查 resume_deadline_at 是否已超时
    - 检查 policy_timeout_ms 是否已超时
    - 超时时生成 SyntheticTimeoutEvent
    """

    def check_resume_allowed(
        self,
        *,
        interrupt_signal: InterruptSignal,
        now_ms: int | None = None,
        policy_timeout_ms: int | None = None,
        policy_start_ms: int | None = None,
    ) -> ResumeDecision:
        """检查是否允许恢复。

        Args:
            interrupt_signal: 中断信号（含 resume_deadline_at）
            now_ms: 当前时间戳（毫秒），默认为 time.time() * 1000
            policy_timeout_ms: 全局策略超时（毫秒），可选
            policy_start_ms: 策略时钟起点（毫秒），可选

        Returns:
            ResumeDecision（allowed=True 或 allowed=False + synthetic_event）
        """
        current_ms = int(now_ms if now_ms is not None else time.time() * 1000)

        # 检查 resume_deadline_at 是否已超时
        if current_ms > interrupt_signal.resume_deadline_at:
            synthetic = SyntheticTimeoutEvent(
                checkpoint_id=interrupt_signal.checkpoint_id,
                error_code=RESUME_DENIED_DEADLINE_EXCEEDED,
                ts_ms=current_ms,
            )
            return ResumeDecision(
                allowed=False,
                reason=f"恢复截止时间已过（resume_deadline_at={interrupt_signal.resume_deadline_at}，now={current_ms}）",
                synthetic_event=synthetic.to_dict(),
            )

        # 检查 Policy Clock 是否已超时（可选）
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
                    reason=f"Policy Clock 已超时（elapsed={elapsed}ms，policy_timeout={policy_timeout_ms}ms）",
                    synthetic_event=synthetic.to_dict(),
                )

        return ResumeDecision(
            allowed=True,
            reason="Policy Clock 检查通过，允许恢复",
            synthetic_event=None,
        )


# ---------------------------------------------------------------------------
# CheckpointManager：组合 CheckpointStore + HostPolicyGuard
# ---------------------------------------------------------------------------

class CheckpointManager:
    """Checkpoint 管理器：组合 CheckpointStore + HostPolicyGuard。

    提供 interrupt() 和 resume() 两个核心操作：
    - interrupt(state) -> InterruptSignal：保存 checkpoint，返回中断信号
    - resume(interrupt_signal, ...) -> ResumeDecision：检查后决定是否允许恢复
    """

    def __init__(
        self,
        *,
        store: CheckpointStore | None = None,
        policy_guard: HostPolicyGuard | None = None,
        default_resume_deadline_ms: int = DEFAULT_RESUME_DEADLINE_MS,
    ) -> None:
        self._store = store or CheckpointStore()
        self._policy_guard = policy_guard or HostPolicyGuard()
        self._default_resume_deadline_ms = int(default_resume_deadline_ms)

    def interrupt(
        self,
        state: dict[str, Any],
        *,
        interrupt_reason: str = INTERRUPT_REASON_HUMAN_REVIEW,
        resume_deadline_ms: int | None = None,
    ) -> InterruptSignal:
        """保存 checkpoint 并返回中断信号。

        Args:
            state: 当前 GraphState 字典
            interrupt_reason: 中断原因（HUMAN_REVIEW / POLICY_TIMEOUT / AIRLOCK）
            resume_deadline_ms: 恢复截止时间（毫秒，从现在起），默认 DEFAULT_RESUME_DEADLINE_MS

        Returns:
            InterruptSignal（含 checkpoint_id、interrupt_reason、resume_deadline_at）
        """
        checkpoint_id = self._store.save(state)
        now = int(time.time() * 1000)
        deadline_ms = int(resume_deadline_ms if resume_deadline_ms is not None else self._default_resume_deadline_ms)
        return InterruptSignal(
            checkpoint_id=checkpoint_id,
            interrupt_reason=str(interrupt_reason),
            interrupted_at_ms=now,
            resume_deadline_at=now + deadline_ms,
        )

    def resume(
        self,
        interrupt_signal: InterruptSignal,
        *,
        now_ms: int | None = None,
        policy_timeout_ms: int | None = None,
        policy_start_ms: int | None = None,
    ) -> ResumeDecision:
        """检查是否允许恢复，返回恢复决策。

        Args:
            interrupt_signal: 中断信号
            now_ms: 当前时间戳（毫秒），默认为 time.time() * 1000
            policy_timeout_ms: 全局策略超时（毫秒），可选
            policy_start_ms: 策略时钟起点（毫秒），可选

        Returns:
            ResumeDecision（allowed=True 或 allowed=False + synthetic_event）
        """
        return self._policy_guard.check_resume_allowed(
            interrupt_signal=interrupt_signal,
            now_ms=now_ms,
            policy_timeout_ms=policy_timeout_ms,
            policy_start_ms=policy_start_ms,
        )

    def load_state(self, checkpoint_id: str) -> dict[str, Any] | None:
        """从 checkpoint 加载 GraphState。

        Args:
            checkpoint_id: checkpoint ID

        Returns:
            GraphState 字典，若不存在则返回 None
        """
        return self._store.load(checkpoint_id)
