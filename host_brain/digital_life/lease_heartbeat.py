"""供围栏 Life 投递处理器使用的同步/异步租约心跳。

处理器持有租约期间必须持续续期；心跳或关闭失败均闭合失败，防止过期持有者在新 owner
接管后继续提交关系或设备副作用。
"""

from __future__ import annotations

from collections.abc import Callable
import threading
from types import TracebackType


class LifeLeaseHeartbeatError(RuntimeError):
    """租约心跳失败，处理器不得提交其处理结果。"""


def heartbeat_interval_seconds(lease_ms: int) -> float:
    # 常规租约每秒续期；短测试/修复租约仍保持可实际调度的最小间隔。
    return max(0.01, min(1.0, max(1, int(lease_ms)) / 3_000.0))


class SyncLifeLeaseHeartbeat:
    """在专用线程中续期的同步心跳；退出时必须确认线程停止并传播续期失败。"""
    def __init__(self, renew: Callable[[], None], *, lease_ms: int) -> None:
        self._renew = renew
        self._interval = heartbeat_interval_seconds(lease_ms)
        self._stop = threading.Event()
        self._failure: BaseException | None = None
        self._thread = threading.Thread(
            target=self._run,
            name="life-lease-heartbeat",
            daemon=True,
        )

    def __enter__(self) -> "SyncLifeLeaseHeartbeat":
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        del exc_type, exc, traceback
        self._stop.set()
        self._thread.join(timeout=max(1.0, self._interval * 3.0))
        if self._thread.is_alive():
            raise LifeLeaseHeartbeatError("Life lease heartbeat did not stop")
        if self._failure is not None:
            raise LifeLeaseHeartbeatError("Life lease heartbeat renewal failed") from self._failure
        return False

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self._renew()
            except BaseException as exc:  # propagate on the handler thread
                self._failure = exc
                self._stop.set()
                return


class AsyncLifeLeaseHeartbeat:
    """异步处理器的心跳包装，仍复用专用线程以避免事件循环饥饿造成失租。"""
    def __init__(self, renew: Callable[[], None], *, lease_ms: int) -> None:
        # 续期不能依赖处理器事件循环或共享默认 executor：繁忙时会让 ``asyncio.to_thread``
        # 在租约之外才运行并围栏掉健康处理器。同步心跳已有专用 daemon 线程与闭合关闭语义。
        self._heartbeat = SyncLifeLeaseHeartbeat(renew, lease_ms=lease_ms)

    async def __aenter__(self) -> "AsyncLifeLeaseHeartbeat":
        self._heartbeat.__enter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        return self._heartbeat.__exit__(exc_type, exc, traceback)


__all__ = [
    "AsyncLifeLeaseHeartbeat",
    "LifeLeaseHeartbeatError",
    "SyncLifeLeaseHeartbeat",
    "heartbeat_interval_seconds",
]
