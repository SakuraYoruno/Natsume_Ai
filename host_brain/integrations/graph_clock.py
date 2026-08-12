"""Dual-Clock Supervision for Governed Runtime Contract (M36-A).

实现双时钟监督机制（ModelPlane.md §10 Rule 2）：
- ExecutionClock：节点级执行时钟（记录节点耗时，检测 EXEC_TIMEOUT）
- PolicyClock：图级策略时钟（检测全局 POLICY_TIMEOUT，强制终止图执行）
- DualClockSupervisor：双时钟监督器，组合 ExecutionClock + PolicyClock

设计原则：
- 所有时钟操作纯内存，无 I/O 依赖
- Policy Clock 超时 → 强制终止（status = STATUS_FAILED）
- Execution Clock 超时 → 仅记录事件，不强制终止
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Generator

from host_brain.integrations.graph_contract import (
    CLOCK_EVENT_EXEC_TIMEOUT,
    CLOCK_EVENT_POLICY_TIMEOUT,
    DEFAULT_POLICY_TIMEOUT_MS,
    build_clock_event,
)


# ---------------------------------------------------------------------------
# ClockEvent 数据类
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
# 时钟事件是不可变观测记录：执行器将其写回图状态，而非在时钟层直接改写路由。
class ClockEvent:
    """时钟事件（EXEC_TIMEOUT / POLICY_TIMEOUT）。"""
    event_type: str
    node_id: str
    elapsed_ms: int
    threshold_ms: int
    ts_ms: int

    def to_dict(self) -> dict[str, Any]:
        """转换为字典，供 GraphState.clock_events 追加。"""
        return {
            "event_type": self.event_type,
            "node_id": self.node_id,
            "elapsed_ms": self.elapsed_ms,
            "threshold_ms": self.threshold_ms,
            "ts_ms": self.ts_ms,
        }


# ---------------------------------------------------------------------------
# ExecutionClock：节点级执行时钟
# ---------------------------------------------------------------------------

class ExecutionClock:
# 节点时钟只度量单次节点工作量；超限会留下证据，具体降级由图执行器决定。
    """节点级执行时钟，记录节点耗时，检测 EXEC_TIMEOUT。

    用法：
        clock = ExecutionClock(node_timeout_ms=3000)
        clock.start()
        # ... 节点执行 ...
        clock.stop()
        if clock.is_timeout:
            # 追加 EXEC_TIMEOUT 事件
    """

    def __init__(self, *, node_timeout_ms: int = 5_000) -> None:
        self._node_timeout_ms = int(node_timeout_ms)
        self._start_ms: int | None = None
        self._end_ms: int | None = None

    def start(self) -> None:
        # 每次开始都清空旧终点，避免重用实例时把上一次节点耗时混入当前测量。
        """记录节点执行开始时间戳（毫秒）。"""
        self._start_ms = int(time.time() * 1000)
        self._end_ms = None

    def stop(self) -> None:
        """记录节点执行结束时间戳（毫秒）。"""
        self._end_ms = int(time.time() * 1000)

    @property
    def exec_start_ms(self) -> int | None:
        """节点执行开始时间戳（毫秒）。"""
        return self._start_ms

    @property
    def exec_end_ms(self) -> int | None:
        """节点执行结束时间戳（毫秒）。"""
        return self._end_ms

    @property
    def elapsed_ms(self) -> int:
        # 未停止时以当前时间计算，令上下文管理器在异常退出也能得到真实耗时。
        """节点执行耗时（毫秒）。未 stop 时返回从 start 到当前的耗时。"""
        if self._start_ms is None:
            return 0
        end = self._end_ms if self._end_ms is not None else int(time.time() * 1000)
        return max(0, end - self._start_ms)

    @property
    def is_timeout(self) -> bool:
        # 使用严格大于：恰好等于预算仍视为在契约边界内。
        """节点执行是否超时（elapsed_ms > node_timeout_ms）。"""
        return self.elapsed_ms > self._node_timeout_ms

    def to_exec_stats(self) -> dict[str, Any]:
        """转换为节点执行统计字典，供 GraphState.node_exec_stats 写入。"""
        return {
            "exec_start_ms": self._start_ms,
            "exec_end_ms": self._end_ms,
            "exec_duration_ms": self.elapsed_ms,
            "node_timeout_ms": self._node_timeout_ms,
            "is_timeout": self.is_timeout,
        }

    def make_timeout_event(self, *, node_id: str) -> ClockEvent:
        """生成 EXEC_TIMEOUT 事件。"""
        return ClockEvent(
            event_type=CLOCK_EVENT_EXEC_TIMEOUT,
            node_id=node_id,
            elapsed_ms=self.elapsed_ms,
            threshold_ms=self._node_timeout_ms,
            ts_ms=int(time.time() * 1000),
        )


# ---------------------------------------------------------------------------
# PolicyClock：图级策略时钟
# ---------------------------------------------------------------------------

class PolicyClock:
# 策略时钟覆盖整张图和暂停时长；一旦超限，调用方必须停止继续调度新节点。
    """图级策略时钟，检测全局 POLICY_TIMEOUT，超时时强制终止图执行。

    用法：
        clock = PolicyClock(policy_start_ms=..., policy_timeout_ms=30000)
        event = clock.check_policy_timeout(node_id="node_a")
        if event is not None:
            # Policy Clock 超时，强制终止
    """

    def __init__(
        self,
        *,
        policy_start_ms: int | None = None,
        policy_timeout_ms: int = DEFAULT_POLICY_TIMEOUT_MS,
    ) -> None:
        self._policy_start_ms = policy_start_ms if policy_start_ms is not None else int(time.time() * 1000)
        self._policy_timeout_ms = int(policy_timeout_ms)

    def start(self) -> None:
        """重置策略时钟起点为当前时间戳。"""
        self._policy_start_ms = int(time.time() * 1000)

    @property
    def elapsed_ms(self) -> int:
        """从策略时钟起点到当前的耗时（毫秒）。"""
        return max(0, int(time.time() * 1000) - self._policy_start_ms)

    def check_policy_timeout(self, *, node_id: str) -> ClockEvent | None:
        # 返回事件而非抛异常，使执行器能够连同失败状态一起持久化和审计。
        """检查是否超过全局策略超时。

        Args:
            node_id: 当前准备执行的节点 ID

        Returns:
            ClockEvent(event_type=POLICY_TIMEOUT) 若超时，否则 None
        """
        elapsed = self.elapsed_ms
        if elapsed > self._policy_timeout_ms:
            return ClockEvent(
                event_type=CLOCK_EVENT_POLICY_TIMEOUT,
                node_id=node_id,
                elapsed_ms=elapsed,
                threshold_ms=self._policy_timeout_ms,
                ts_ms=int(time.time() * 1000),
            )
        return None


# ---------------------------------------------------------------------------
# DualClockSupervisor：双时钟监督器
# ---------------------------------------------------------------------------

class DualClockSupervisor:
# 把“节点是否慢”和“图是否已失去继续授权”分离，避免节点预算掩盖全局治理预算。
    """双时钟监督器，组合 ExecutionClock + PolicyClock。

    在节点执行前检查 Policy Clock，节点执行后记录 Execution Clock。

    用法：
        supervisor = DualClockSupervisor(
            policy_start_ms=state["policy_start_ms"],
            policy_timeout_ms=state["policy_timeout_ms"],
            node_timeout_ms=5000,
        )
        with supervisor.supervise_node(node_id="node_a") as ctx:
            if ctx.policy_timeout_event:
                # Policy Clock 超时，强制终止
                return fail_hard(...)
            # 执行节点...
        # 节点执行完成后
        clock_events = supervisor.collect_clock_events()
        node_exec_stats = supervisor.collect_exec_stats()
    """

    def __init__(
        self,
        *,
        policy_start_ms: int | None = None,
        policy_timeout_ms: int = DEFAULT_POLICY_TIMEOUT_MS,
        node_timeout_ms: int = 5_000,
    ) -> None:
        # PolicyClock 在监督器生命周期内保持同一原点；ExecutionClock 则为每个节点新建。
        self._policy_clock = PolicyClock(
            policy_start_ms=policy_start_ms,
            policy_timeout_ms=policy_timeout_ms,
        )
        self._node_timeout_ms = int(node_timeout_ms)
        self._pending_events: list[ClockEvent] = []
        self._exec_stats: dict[str, dict[str, Any]] = {}

    @contextmanager
    def supervise_node(self, *, node_id: str) -> Generator[_SupervisionContext, None, None]:
        # 先检查策略预算再启动节点测量，保证超时的图不会因 finally 语义而继续执行业务逻辑。
        """节点执行监督上下文管理器。

        进入时：检查 Policy Clock 是否超时
        退出时：记录 Execution Clock 统计，若超时追加 EXEC_TIMEOUT 事件

        Args:
            node_id: 当前节点 ID

        Yields:
            _SupervisionContext（含 policy_timeout_event）
        """
        # 节点执行前：检查 Policy Clock
        policy_event = self._policy_clock.check_policy_timeout(node_id=node_id)
        ctx = _SupervisionContext(policy_timeout_event=policy_event)

        exec_clock = ExecutionClock(node_timeout_ms=self._node_timeout_ms)
        exec_clock.start()

        try:
            yield ctx
        finally:
            # finally 保证异常、提前返回同样留下耗时统计；事件收集不吞掉原始节点异常。
            exec_clock.stop()
            # 记录节点执行统计
            self._exec_stats[node_id] = exec_clock.to_exec_stats()
            # 若节点超时，追加 EXEC_TIMEOUT 事件
            if exec_clock.is_timeout:
                self._pending_events.append(exec_clock.make_timeout_event(node_id=node_id))
            # 若 Policy Clock 超时，追加 POLICY_TIMEOUT 事件
            if policy_event is not None:
                self._pending_events.append(policy_event)

    def collect_clock_events(self) -> list[dict[str, Any]]:
        # 返回副本化字典，调用方可以安全并入 GraphState 而不持有内部事件对象。
        """收集所有时钟事件（转换为字典列表）。"""
        return [e.to_dict() for e in self._pending_events]

    def collect_exec_stats(self) -> dict[str, dict[str, Any]]:
        # 复制最外层映射，防止状态合并反向替换监督器自己的统计索引。
        """收集所有节点执行统计。"""
        return dict(self._exec_stats)


@dataclass
class _SupervisionContext:
    """supervise_node 上下文，携带 Policy Clock 检查结果。"""
    policy_timeout_event: ClockEvent | None = field(default=None)
