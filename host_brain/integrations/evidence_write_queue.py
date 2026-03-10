"""Evidence Plane 异步写入队列（M41-C）。

提供 EvidenceWriteQueue 类，为 NON_CRITICAL 级写入提供异步队列缓冲，
支持批量落库，避免非关键写入阻塞主流程。
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any, Callable

logger = logging.getLogger(__name__)

_DEFAULT_MAX_SIZE = 500


class EvidenceWriteQueue:
    """Evidence Plane 异步写入队列。

    NON_CRITICAL 写入通过 enqueue() 加入队列，由 flush() 批量执行。
    队列满时自动触发 flush() 后再入队。
    """

    def __init__(self, max_size: int = _DEFAULT_MAX_SIZE) -> None:
        self._queue: deque[tuple[Callable[..., Any], tuple[Any, ...], dict[str, Any]]] = deque()
        self._max_size = max_size

    def enqueue(
        self,
        write_fn: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """将写入任务加入队列。

        若队列已满，先执行 flush() 再入队。

        Args:
            write_fn: 写入方法
            *args: 位置参数
            **kwargs: 关键字参数
        """
        if len(self._queue) >= self._max_size:
            logger.debug("EvidenceWriteQueue 队列已满（%d），触发自动 flush", self._max_size)
            self.flush()
        self._queue.append((write_fn, args, kwargs))

    def flush(self, timeout_ms: float | None = None) -> int:
        """批量执行队列中的所有写入任务并清空队列。

        Args:
            timeout_ms: 超时限制（毫秒）。若指定，超时后停止执行并返回剩余数量。
                        若为 None，则执行全部任务。

        Returns:
            剩余未执行的任务数量（超时时 > 0，正常完成时为 0）
        """
        deadline = None
        if timeout_ms is not None:
            deadline = time.perf_counter() + timeout_ms / 1000.0

        executed = 0
        retry_queue: list[tuple] = []  # 失败任务暂存，flush 结束后重入队一次

        while self._queue:
            if deadline is not None and time.perf_counter() >= deadline:
                logger.debug("EvidenceWriteQueue flush 超时，剩余 %d 个任务", len(self._queue))
                break
            write_fn, args, kwargs = self._queue.popleft()
            try:
                write_fn(*args, **kwargs)
            except Exception as exc:
                logger.warning("EvidenceWriteQueue 写入失败，将重试一次: %s", exc)
                retry_queue.append((write_fn, args, kwargs))
            executed += 1

        # 将失败任务重新放回队列末尾（最多重试 1 次，避免无限循环）
        for item in retry_queue:
            if len(self._queue) < self._max_size:
                self._queue.append(item)
            else:
                logger.warning("EvidenceWriteQueue 队列已满，丢弃重试任务")

        return len(self._queue)

    def queue_size(self) -> int:
        """返回当前队列中待执行的任务数量。"""
        return len(self._queue)

    def clear(self) -> None:
        """清空队列（丢弃所有待执行任务）。"""
        self._queue.clear()
