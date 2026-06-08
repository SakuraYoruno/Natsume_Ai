from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Deque

from host_brain.core.ids import now_ms

logger = logging.getLogger("Natsume.tribe.event_bus")

# 事件处理器类型：接收 TribeEvent，返回 Awaitable[None]
EventHandler = Callable[["TribeEvent"], Awaitable[None]]


@dataclass(slots=True)
class TribeEvent:
    event_id: str
    event_type: str
    source_agent: str
    target_agent: str | None
    payload: dict[str, Any]
    schema_version: str = "tribe.event.v1"
    ts_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "source_agent": self.source_agent,
            "target_agent": self.target_agent,
            "payload": dict(self.payload),
            "schema_version": self.schema_version,
            "ts_ms": int(self.ts_ms),
        }


class InMemoryTribeEventBus:
    """族间事件总线：支持发布、订阅与异步分发循环。

    - publish()：同步写入环形缓冲区 + 投递到 asyncio.Queue（供 dispatch_loop 消费）
    - subscribe()：注册事件类型 → handler 的映射
    - start() / stop()：启动/停止 asyncio 分发循环（在主事件循环中调用）
    - recent()：查询最近事件（只读，不依赖分发循环）
    """

    def __init__(self, *, max_events: int = 1024) -> None:
        self._max_events = max(1, int(max_events))
        self._events: Deque[TribeEvent] = deque(maxlen=self._max_events)
        self._lock = threading.RLock()
        self._queue_full_last_warn_ms = 0
        self._queue_full_dropped = 0

        # 订阅表：event_type → [handler, ...]
        self._subscribers: dict[str, list[EventHandler]] = {}
        self._sub_lock = threading.RLock()

        # asyncio 分发队列（在 start() 时创建，确保在正确的事件循环中）
        self._queue: asyncio.Queue[TribeEvent] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False
        self._dispatch_task: asyncio.Task[None] | None = None

    # ── 订阅 ──────────────────────────────────────────────────────────────────

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """注册事件处理器。

        可在任意线程调用（线程安全）。
        handler 将在 asyncio 事件循环中被 await 调用。
        """
        key = str(event_type or "").strip().upper() or "UNKNOWN"
        with self._sub_lock:
            self._subscribers.setdefault(key, []).append(handler)
        logger.debug("TribeEventBus.subscribe: event_type=%s handler=%s", key, handler)

    def unsubscribe(self, event_type: str, handler: EventHandler) -> bool:
        """T-56：取消注册事件处理器，支持动态取消订阅。

        可在任意线程调用（线程安全）。
        若 handler 不存在则静默返回 False，不抛出异常。

        Args:
            event_type: 事件类型，大小写不敏感，自动转为大写匹配。
            handler: 要取消的处理器函数，必须与 subscribe() 时传入的对象相同。

        Returns:
            True 表示成功取消，False 表示未找到对应 handler。
        """
        key = str(event_type or "").strip().upper() or "UNKNOWN"
        with self._sub_lock:
            handlers = self._subscribers.get(key)
            if handlers is None:
                return False
            try:
                handlers.remove(handler)
                logger.debug("TribeEventBus.unsubscribe: event_type=%s handler=%s", key, handler)
                # 若列表已空，清理 key 避免内存泄漏
                if not handlers:
                    del self._subscribers[key]
                return True
            except ValueError:
                return False

    def get_subscriber_count(self) -> dict[str, int]:
        """T-66：返回各事件类型的当前订阅数量，供诊断端点使用。

        Returns:
            dict: key 为事件类型（大写），value 为订阅 handler 数量。
                  仅包含当前有订阅的事件类型，空事件类型不出现在结果中。

        Examples:
            >>> bus.get_subscriber_count()
            {"INTERACTION_RECORDED": 3, "BOND_PROGRESS": 4, "LONELINESS_SIGNAL": 5}
        """
        with self._sub_lock:
            return {
                event_type: len(handlers)
                for event_type, handlers in self._subscribers.items()
                if handlers
            }

    # ── 发布 ──────────────────────────────────────────────────────────────────

    def publish(self, event: TribeEvent) -> TribeEvent:
        """同步发布事件：写入环形缓冲区，并尝试投递到分发队列。"""
        with self._lock:
            self._events.append(event)
        # 尝试投递到 asyncio 分发队列（若 dispatch_loop 未启动则静默跳过）
        queue = self._queue
        loop = self._loop
        if queue is not None and self._running:
            def _enqueue() -> None:
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    self._record_queue_full_drop(event=event, batch=False)

            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                running_loop = None
            if loop is not None and running_loop is not loop and loop.is_running():
                loop.call_soon_threadsafe(_enqueue)
            else:
                _enqueue()
        return event

    def publish_batch(self, events: list[TribeEvent]) -> list[TribeEvent]:
        """T-45：批量发布事件，一次性写入环形缓冲区，减少高频场景的锁竞争。

        与逐条调用 publish() 相比，批量写入只需获取一次 _lock，
        适用于同一请求周期内需要连续发布多个事件的场景
        （如 INTERACTION_RECORDED + EMOTION_UPDATED + BOND_PROGRESS 同时触发）。

        Args:
            events: 待发布的事件列表，空列表直接返回。

        Returns:
            已发布的事件列表（与入参相同）。
        """
        if not events:
            return events
        # 一次性写入环形缓冲区
        with self._lock:
            for event in events:
                self._events.append(event)
        # 逐一投递到分发队列（队列满时记录警告并跳过）
        queue = self._queue
        loop = self._loop
        if queue is not None and self._running:
            for event in events:
                def _enqueue_one(item: TribeEvent = event) -> None:
                    try:
                        queue.put_nowait(item)
                    except asyncio.QueueFull:
                        self._record_queue_full_drop(event=item, batch=True)

                try:
                    running_loop = asyncio.get_running_loop()
                except RuntimeError:
                    running_loop = None
                if loop is not None and running_loop is not loop and loop.is_running():
                    loop.call_soon_threadsafe(_enqueue_one)
                else:
                    _enqueue_one()
        return events

    # ── 分发循环 ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """启动异步分发循环（必须在 asyncio 事件循环中调用，如 lifespan）。"""
        if self._running:
            logger.debug("TribeEventBus 分发循环已在运行，跳过重复启动")
            return
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue(maxsize=4096)
        with self._lock:
            self._queue_full_last_warn_ms = 0
            self._queue_full_dropped = 0
        self._running = True
        self._dispatch_task = asyncio.create_task(
            self._dispatch_loop(), name="tribe_event_bus_dispatch"
        )
        logger.info("TribeEventBus 分发循环已启动")

    async def stop(self) -> None:
        """停止分发循环（graceful shutdown）。"""
        self._running = False
        if self._dispatch_task is not None:
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except (asyncio.CancelledError, Exception):
                pass
            self._dispatch_task = None
        self._loop = None
        logger.info("TribeEventBus 分发循环已停止")

    async def _dispatch_loop(self) -> None:
        """异步分发循环：从队列取事件，逐一调用订阅 handler。"""
        while self._running:
            try:
                event = await asyncio.wait_for(
                    self._queue.get(),  # type: ignore[union-attr]
                    timeout=1.0,
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            # 查找订阅 handler（精确匹配 + 通配符 "*"）
            key = str(event.event_type or "").strip().upper() or "UNKNOWN"
            with self._sub_lock:
                handlers = list(self._subscribers.get(key, []))
                handlers += list(self._subscribers.get("*", []))

            for handler in handlers:
                try:
                    await handler(event)
                except Exception as exc:
                    logger.error(
                        "TribeEventBus handler 异常: event_type=%s handler=%s exc=%s",
                        event.event_type,
                        handler,
                        exc,
                        exc_info=True,
                    )

    def _record_queue_full_drop(self, *, event: TribeEvent, batch: bool) -> None:
        """对高频 queue-full 进行节流汇总，避免 warning 日志反向拖慢主链。"""
        queue_size = -1
        if self._queue is not None:
            try:
                queue_size = int(self._queue.qsize())
            except Exception:
                queue_size = -1
        with self._lock:
            self._queue_full_dropped += 1
            dropped = self._queue_full_dropped
            ts = now_ms()
            if ts - int(self._queue_full_last_warn_ms or 0) < 5000:
                return
            self._queue_full_last_warn_ms = ts
            self._queue_full_dropped = 0
        logger.warning(
            "TribeEventBus 分发队列已满，过去5s累计丢弃=%s latest_event_type=%s latest_event_id=%s batch=%s queue_size=%s queue_maxsize=%s",
            dropped,
            event.event_type,
            event.event_id,
            batch,
            queue_size,
            int(getattr(self._queue, "maxsize", 0) or 0),
        )

    # ── 查询 ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _payload_value(payload: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = payload.get(key)
            if value is None:
                continue
            normalized = str(value).strip()
            if normalized:
                return normalized
        return ""

    def recent(
        self,
        *,
        limit: int = 50,
        event_type: str | None = None,
        source_agent: str | None = None,
        target_agent: str | None = None,
        request_id: str | None = None,
        session_id: str | None = None,
        conversation_id: str | None = None,
    ) -> list[TribeEvent]:
        target_type = str(event_type or "").strip().lower()
        target_source_agent = str(source_agent or "").strip().lower()
        target_target_agent = str(target_agent or "").strip().lower()
        target_request_id = str(request_id or "").strip()
        target_session_id = str(session_id or "").strip()
        target_conversation_id = str(conversation_id or "").strip()
        size = max(1, int(limit))
        with self._lock:
            events = list(self._events)
        filtered: list[TribeEvent] = []
        for item in events:
            if target_type and str(item.event_type).strip().lower() != target_type:
                continue
            if target_source_agent and str(item.source_agent).strip().lower() != target_source_agent:
                continue
            if target_target_agent and str(item.target_agent or "").strip().lower() != target_target_agent:
                continue
            payload = item.payload if isinstance(item.payload, dict) else {}
            payload_request_id = self._payload_value(payload, "request_id", "requestId")
            payload_session_id = self._payload_value(payload, "session_id", "sessionId")
            payload_conversation_id = self._payload_value(payload, "conversation_id", "conversationId")
            if target_request_id and payload_request_id != target_request_id:
                continue
            if target_session_id and payload_session_id != target_session_id:
                continue
            if target_conversation_id and payload_conversation_id != target_conversation_id:
                continue
            filtered.append(item)
        return list(reversed(filtered[-size:]))

    # ── 状态查询 ──────────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._running

    def subscriber_count(self, event_type: str | None = None) -> int:
        """返回指定事件类型的订阅数量（不传则返回所有订阅总数）。"""
        with self._sub_lock:
            if event_type is not None:
                key = str(event_type or "").strip().upper() or "UNKNOWN"
                return len(self._subscribers.get(key, []))
            return sum(len(v) for v in self._subscribers.values())


def new_tribe_event(
    *,
    event_type: str,
    source_agent: str,
    target_agent: str | None = None,
    payload: dict[str, Any] | None = None,
    schema_version: str = "tribe.event.v1",
) -> TribeEvent:
    ts_ms = now_ms()
    event_id = f"tribe_evt_{ts_ms}"
    return TribeEvent(
        event_id=event_id,
        event_type=str(event_type).strip() or "UNKNOWN",
        source_agent=str(source_agent).strip() or "unknown_source",
        target_agent=(str(target_agent).strip() or None) if target_agent is not None else None,
        payload=dict(payload or {}),
        schema_version=str(schema_version).strip() or "tribe.event.v1",
        ts_ms=ts_ms,
    )


__all__ = [
    "EventHandler",
    "InMemoryTribeEventBus",
    "TribeEvent",
    "new_tribe_event",
]
