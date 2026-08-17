"""由生命周期拥有的精确耐久续接扫描监督器。

监督器只拥有调度节奏与关闭，刻意接收已经组合好的 continuation callable，因此不能
自行发明 admission、execution 或 recovery authority。
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any


ContinuationRunner = Callable[
    [],
    Awaitable[Sequence[Mapping[str, Any]]],
]
_REASON_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,119}")
_LOGGER = logging.getLogger(__name__)


class PeriodicDurableContinuation:
    """在事件 wakeup 与定时 tick 间串行化一次有界 continuation scan。

    同一时间仅允许一个 runner 调用，避免两个触发源对同一耐久 survivor 竞态重入；
    runner 的报告会被复制为健康快照，外部调用者不能篡改监督器保存的观测结果。
    """

    def __init__(
        self,
        *,
        runner: ContinuationRunner,
        task_name: str,
        failure_reason_code: str,
        interval_ms: int = 5_000,
    ) -> None:
        if not callable(runner):
            raise TypeError("periodic durable continuation runner must be callable")
        normalized_name = str(task_name or "").strip()
        normalized_failure = str(failure_reason_code or "").strip()
        if not normalized_name or not _REASON_CODE.fullmatch(normalized_failure):
            raise ValueError(
                "periodic durable continuation task name and stable failure code "
                "are required"
            )
        if int(interval_ms) < 10:
            raise ValueError(
                "periodic durable continuation interval must be at least 10ms"
            )
        self._runner = runner
        self._task_name = normalized_name
        self._failure_reason_code = normalized_failure
        self._interval_s = int(interval_ms) / 1_000.0
        self._cycle_lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None
        self._wake_event: asyncio.Event | None = None
        self._last_report: tuple[dict[str, Any], ...] = ()
        self._last_failure: dict[str, Any] | None = None
        self._last_logged_failure: tuple[str, str] | None = None
        self._completed_cycles = 0

    async def run_once(self) -> tuple[dict[str, Any], ...]:
        """执行一次精确扫描，并把失败暴露给启动调用者。

        失败只保存稳定 reason 与异常类型，不泄露 runner 的敏感上下文；相同失败去重日志，
        成功后才清除失败状态并计入完成周期。
        """

        async with self._cycle_lock:
            try:
                raw_report = await self._runner()
                report = _normalize_report(raw_report)
            except Exception as exc:
                self._last_failure = {
                    "reason_code": _safe_reason_code(
                        getattr(exc, "reason_code", None),
                        fallback=self._failure_reason_code,
                    ),
                    "error_type": type(exc).__name__,
                    "ts_ms": time.time_ns() // 1_000_000,
                }
                failure_key = (
                    str(self._last_failure["reason_code"]),
                    str(self._last_failure["error_type"]),
                )
                if failure_key != self._last_logged_failure:
                    _LOGGER.error(
                        "%s: durable continuation failed (%s)",
                        failure_key[0],
                        failure_key[1],
                    )
                    self._last_logged_failure = failure_key
                raise
            self._last_report = report
            if self._last_logged_failure is not None:
                _LOGGER.info(
                    "%s: durable continuation recovered",
                    self._last_logged_failure[0],
                )
                self._last_logged_failure = None
            self._last_failure = None
            self._completed_cycles += 1
            return tuple(dict(item) for item in report)

    async def start(self) -> None:
        """在调用方完成强制启动扫描后启动轮询；重复调用不创建第二个后台任务。"""

        task = self._task
        if task is not None and not task.done():
            return
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._task = asyncio.create_task(
            self._run_loop(
                stop_event=self._stop_event,
                wake_event=self._wake_event,
            ),
            name=self._task_name,
        )

    def wake(self) -> None:
        """把任意数量事件驱动 wakeup 合并为下一次扫描，避免唤醒风暴。"""

        wake_event = self._wake_event
        task = self._task
        if wake_event is not None and task is not None and not task.done():
            wake_event.set()

    def request_stop(self) -> None:
        """阻止新的扫描，同时允许已开始的耐久事务完成而不被粗暴取消。"""

        stop_event = self._stop_event
        wake_event = self._wake_event
        if stop_event is not None:
            stop_event.set()
        if wake_event is not None:
            wake_event.set()

    async def join(self, *, timeout_s: float) -> None:
        """等待静默而不取消进行中的事务；超时以稳定 reason 交给上层关闭重试。"""

        task = self._task
        if task is None:
            return
        try:
            await asyncio.wait_for(
                asyncio.shield(task),
                timeout=max(0.1, float(timeout_s)),
            )
        except asyncio.CancelledError:
            # Uvicorn may cancel lifespan-owned background tasks while an
            # incomplete candidate is being stopped.  Once this supervisor's
            # own stop event is set, an already-cancelled child is quiescent;
            # propagating its cancellation would falsely turn orderly Host
            # teardown into a retry-required runtime failure.  Never swallow
            # cancellation of the caller itself.
            current = asyncio.current_task()
            caller_cancelling = bool(
                current is not None
                and callable(getattr(current, "cancelling", None))
                and current.cancelling()
            )
            if (
                caller_cancelling
                or not task.cancelled()
                or self._stop_event is None
                or not self._stop_event.is_set()
            ):
                raise
        except TimeoutError as exc:
            raise RuntimeError(
                f"{self._failure_reason_code}_STOP_TIMEOUT"
            ) from exc
        if self._task is task:
            self._task = None
            self._stop_event = None
            self._wake_event = None

    def health_snapshot(self) -> dict[str, Any]:
        """返回只读健康投影，包含最近报告与失败但不触发新的扫描。"""
        task = self._task
        stop_event = self._stop_event
        return {
            "running": bool(task is not None and not task.done()),
            "stop_requested": bool(stop_event is not None and stop_event.is_set()),
            "completed_cycles": int(self._completed_cycles),
            "last_report": tuple(dict(item) for item in self._last_report),
            "last_failure": (
                dict(self._last_failure)
                if self._last_failure is not None
                else None
            ),
        }

    async def _run_loop(
        self,
        *,
        stop_event: asyncio.Event,
        wake_event: asyncio.Event,
    ) -> None:
        """按 wakeup 或间隔运行；失败由下一次有界 tick 重试同一 canonical runner。"""
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(
                    wake_event.wait(),
                    timeout=self._interval_s,
                )
            except TimeoutError:
                pass
            wake_event.clear()
            if stop_event.is_set():
                break
            try:
                await self.run_once()
            except Exception:
                # ``run_once`` retained an opaque health record.  The next
                # bounded timer tick retries the same canonical authority.
                continue


def _normalize_report(
    value: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """复制并验证 runner 报告，阻止可变/非映射结果污染健康状态。"""
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        raise TypeError("durable continuation report must be a sequence")
    report: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise TypeError("durable continuation report item must be a mapping")
        report.append(dict(item))
    return tuple(report)


def _safe_reason_code(value: Any, *, fallback: str) -> str:
    """只保留格式受限的稳定 reason code，其他异常信息回退到调用方指定代码。"""
    candidate = str(value or "").strip()
    if _REASON_CODE.fullmatch(candidate):
        return candidate
    return str(fallback)


__all__ = ["PeriodicDurableContinuation"]
