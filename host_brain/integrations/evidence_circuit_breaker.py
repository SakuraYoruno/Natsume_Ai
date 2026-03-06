"""Evidence Plane 写入失败熔断机制（M41-D）。

提供 EvidenceCircuitBreaker 类，当 Evidence Plane 写入连续失败超过阈值时触发熔断，
降级为内存缓冲 + 批量落库，防止写入故障传播到主流程。

状态机：
  CLOSED（正常）→ OPEN（熔断）→ HALF_OPEN（探测恢复）→ CLOSED
"""

from __future__ import annotations

import logging
import time
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """熔断器状态枚举。"""
    CLOSED = "closed"       # 正常，写入直接执行
    OPEN = "open"           # 熔断，写入转入内存缓冲
    HALF_OPEN = "half_open" # 探测恢复，允许单次探测写入


class EvidenceCircuitBreaker:
    """Evidence Plane 写入失败熔断器。

    - CLOSED：正常状态，写入直接执行；连续失败达到阈值后转为 OPEN。
    - OPEN：熔断状态，写入不执行，转入 _memory_buffer；
            持续 recovery_timeout_ms 后自动转为 HALF_OPEN。
    - HALF_OPEN：探测恢复状态，允许单次探测写入；
                 成功则恢复为 CLOSED，失败则回退为 OPEN。
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout_ms: int = 10000,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_timeout_ms = recovery_timeout_ms
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._open_at_mono: float | None = None
        self._memory_buffer: list[dict[str, Any]] = []

    @property
    def state(self) -> CircuitState:
        """当前熔断器状态。"""
        self._check_recovery()
        return self._state

    def _check_recovery(self) -> None:
        """检查是否应从 OPEN 转为 HALF_OPEN。"""
        if self._state == CircuitState.OPEN and self._open_at_mono is not None:
            elapsed_ms = (time.perf_counter() - self._open_at_mono) * 1000.0
            if elapsed_ms >= self._recovery_timeout_ms:
                logger.debug("EvidenceCircuitBreaker 进入 HALF_OPEN 探测状态")
                self._state = CircuitState.HALF_OPEN

    def call(
        self,
        write_fn: Callable[..., Any],
        record: dict[str, Any] | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> bool:
        """通过熔断器执行写入操作。

        Args:
            write_fn: 写入方法
            record: 写入记录（用于内存缓冲，OPEN 状态时保存）
            *args: 传递给 write_fn 的位置参数
            **kwargs: 传递给 write_fn 的关键字参数

        Returns:
            True 表示写入成功执行，False 表示被熔断（转入内存缓冲）
        """
        self._check_recovery()

        if self._state == CircuitState.OPEN:
            # 熔断状态：写入转入内存缓冲
            if record is not None:
                self._memory_buffer.append(record)
            logger.debug("EvidenceCircuitBreaker OPEN，写入转入内存缓冲")
            return False

        if self._state == CircuitState.HALF_OPEN:
            # 探测状态：允许单次探测写入
            try:
                write_fn(*args, **kwargs)
                # 探测成功，恢复为 CLOSED
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                self._open_at_mono = None
                logger.debug("EvidenceCircuitBreaker 探测成功，恢复为 CLOSED")
                return True
            except Exception as exc:
                # 探测失败，回退为 OPEN
                self._state = CircuitState.OPEN
                self._open_at_mono = time.perf_counter()
                if record is not None:
                    self._memory_buffer.append(record)
                logger.warning("EvidenceCircuitBreaker 探测失败，回退为 OPEN: %s", exc)
                return False

        # CLOSED 状态：正常执行
        try:
            write_fn(*args, **kwargs)
            self._failure_count = 0
            return True
        except Exception as exc:
            self._failure_count += 1
            logger.warning(
                "EvidenceCircuitBreaker 写入失败（%d/%d）: %s",
                self._failure_count,
                self._failure_threshold,
                exc,
            )
            if self._failure_count >= self._failure_threshold:
                self._state = CircuitState.OPEN
                self._open_at_mono = time.perf_counter()
                if record is not None:
                    self._memory_buffer.append(record)
                logger.warning(
                    "EvidenceCircuitBreaker 触发熔断（连续失败 %d 次）",
                    self._failure_count,
                )
            return False

    def get_buffer_snapshot(self) -> list[dict[str, Any]]:
        """返回当前内存缓冲中的所有待写入记录（副本）。"""
        return list(self._memory_buffer)

    def clear_buffer(self) -> None:
        """清空内存缓冲。"""
        self._memory_buffer.clear()

    def reset(self) -> None:
        """重置熔断器到 CLOSED 状态（测试用）。"""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._open_at_mono = None
        self._memory_buffer.clear()
