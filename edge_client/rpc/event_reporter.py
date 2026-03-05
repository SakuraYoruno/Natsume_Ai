"""Edge→Host 事件批量上报客户端。

TASK-0079: 实现 Edge 端事件批量上报到 Host 的 RPC 通道。
支持：
- 批量事件上报（HTTP POST /v1/edge/events）
- 网络异常时本地缓存（最多 MAX_CACHE_EVENTS 条或 MAX_CACHE_BYTES 字节）
- 网络恢复后自动批量补发
- 指数退避重试
- 事件 Schema 校验
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from host_brain.core.errors import (
    RPC_EVENT_BATCH_PARTIAL_FAIL,
    RPC_EVENT_NETWORK_UNAVAILABLE,
    RPC_EVENT_SCHEMA_INVALID,
)
from host_brain.core.ids import now_ms

logger = logging.getLogger("Natsume.edge.event_reporter")

# ── 配置常量 ──
MAX_CACHE_EVENTS = 1_000
MAX_CACHE_BYTES = 10 * 1024 * 1024  # 10MB
MAX_BATCH_SIZE = 100
MAX_RETRY_ATTEMPTS = 5
BASE_RETRY_DELAY_S = 1.0
MAX_RETRY_DELAY_S = 30.0

# ── 必需字段 ──
REQUIRED_EVENT_FIELDS = frozenset({"event_type", "ts_ms", "request_id", "session_id"})

# ── 有效事件来源 ──
VALID_SOURCES = frozenset({"edge", "EDGE", "edge_client"})


@dataclass(frozen=True)
class EventReportItem:
    """单条 Edge 事件上报结构。"""

    event_type: str
    ts_ms: int
    request_id: str
    session_id: str
    source: str = "edge"
    payload: dict[str, Any] = field(default_factory=dict)
    direction: str = "EDGE_TO_HOST"
    seq: int | None = None
    status: str | None = None
    reason_code: str | None = None
    stage: str | None = None
    detail: str | None = None
    evidence_ref: str | None = None
    # M29-R（OPT-?）：情感字段（Edge 端感知到的用户情感）
    emotion_tag: str | None = None          # 用户情感标签（如 "SAD"/"HAPPY"）
    emotion_intensity: float | None = None  # 情感强度（0.0~1.0）

    def to_dict(self) -> dict[str, Any]:
        result = {
            "event_type": self.event_type,
            "ts_ms": self.ts_ms,
            "request_id": self.request_id,
            "session_id": self.session_id,
            "source": self.source,
            "payload": dict(self.payload),
            "direction": self.direction,
            "seq": self.seq,
            "status": self.status,
            "reason_code": self.reason_code,
            "stage": self.stage,
            "detail": self.detail,
            "evidence_ref": self.evidence_ref,
        }
        # M29-R：仅在有情感字段时输出
        if self.emotion_tag is not None:
            result["emotion_tag"] = self.emotion_tag
        if self.emotion_intensity is not None:
            result["emotion_intensity"] = self.emotion_intensity
        return result


@dataclass
class BatchReportResult:
    """批量上报结果。"""

    success: bool
    total: int
    accepted: int
    rejected: int
    reason_code: str | None = None
    detail: str | None = None
    rejected_indices: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "total": self.total,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "reason_code": self.reason_code,
            "detail": self.detail,
            "rejected_indices": list(self.rejected_indices),
        }


def validate_event(raw: dict[str, Any]) -> tuple[EventReportItem | None, str | None]:
    """校验单条事件并返回 (parsed, error_message)。

    返回:
        (EventReportItem, None) — 校验通过
        (None, error_str) — 校验失败
    """
    # 必需字段检查
    missing = REQUIRED_EVENT_FIELDS - set(raw.keys())
    if missing:
        return None, f"missing fields: {sorted(missing)}"

    event_type = str(raw.get("event_type") or "").strip()
    if not event_type:
        return None, "event_type is empty"

    ts_ms = raw.get("ts_ms")
    if not isinstance(ts_ms, (int, float)):
        return None, f"ts_ms must be numeric, got {type(ts_ms).__name__}"

    request_id = str(raw.get("request_id") or "").strip()
    if not request_id:
        return None, "request_id is empty"

    session_id = str(raw.get("session_id") or "").strip()
    if not session_id:
        return None, "session_id is empty"

    # 可选字段
    payload = raw.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            payload = {"raw": payload}
    elif not isinstance(payload, dict):
        payload = {}

    return EventReportItem(
        event_type=event_type,
        ts_ms=int(ts_ms),
        request_id=request_id,
        session_id=session_id,
        source=str(raw.get("source") or "edge").strip(),
        payload=dict(payload),
        direction=str(raw.get("direction") or "EDGE_TO_HOST").strip(),
        seq=raw.get("seq") if isinstance(raw.get("seq"), int) else None,
        status=str(raw.get("status") or "").strip() or None,
        reason_code=str(raw.get("reason_code") or "").strip() or None,
        stage=str(raw.get("stage") or "").strip() or None,
        detail=str(raw.get("detail") or "").strip() or None,
        evidence_ref=str(raw.get("evidence_ref") or "").strip() or None,
    ), None


def validate_batch(events: list[dict[str, Any]]) -> tuple[
    list[EventReportItem], list[tuple[int, str]]
]:
    """批量校验事件列表。

    返回:
        (valid_items, [(index, error_message), ...])
    """
    valid: list[EventReportItem] = []
    errors: list[tuple[int, str]] = []

    for idx, raw in enumerate(events):
        if not isinstance(raw, dict):
            errors.append((idx, f"event at index {idx} is not a dict"))
            continue
        item, err = validate_event(raw)
        if item is not None:
            valid.append(item)
        else:
            errors.append((idx, err or "unknown validation error"))

    return valid, errors


class EdgeEventCache:
    """Edge 端本地事件缓存——网络不可用时暂存事件。

    线程安全。最多缓存 MAX_CACHE_EVENTS 条或 MAX_CACHE_BYTES 字节。
    超出时按 FIFO 丢弃最旧事件。
    """

    def __init__(
        self,
        *,
        max_events: int = MAX_CACHE_EVENTS,
        max_bytes: int = MAX_CACHE_BYTES,
    ) -> None:
        self._lock = threading.Lock()
        self._queue: deque[dict[str, Any]] = deque()
        self._current_bytes = 0
        self.max_events = max_events
        self.max_bytes = max_bytes
        self._total_cached = 0
        self._total_evicted = 0

    def push(self, event: dict[str, Any]) -> bool:
        """缓存一条事件。超过上限时丢弃最旧事件。"""
        event_bytes = len(json.dumps(event, default=str).encode("utf-8"))

        with self._lock:
            # 如果单条事件就超过限制，拒绝
            if event_bytes > self.max_bytes:
                logger.warning("Event too large to cache: %d bytes", event_bytes)
                return False

            # 淘汰策略：FIFO 丢弃最旧
            while (
                len(self._queue) >= self.max_events
                or self._current_bytes + event_bytes > self.max_bytes
            ) and self._queue:
                evicted = self._queue.popleft()
                evicted_bytes = len(json.dumps(evicted, default=str).encode("utf-8"))
                self._current_bytes -= evicted_bytes
                self._total_evicted += 1

            self._queue.append(event)
            self._current_bytes += event_bytes
            self._total_cached += 1
            return True

    def drain(self, max_count: int = MAX_BATCH_SIZE) -> list[dict[str, Any]]:
        """取出最多 max_count 条缓存事件（FIFO）。"""
        with self._lock:
            count = min(max_count, len(self._queue))
            batch = []
            for _ in range(count):
                event = self._queue.popleft()
                event_bytes = len(json.dumps(event, default=str).encode("utf-8"))
                self._current_bytes -= event_bytes
                batch.append(event)
            return batch

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._queue)

    @property
    def total_cached(self) -> int:
        return self._total_cached

    @property
    def total_evicted(self) -> int:
        return self._total_evicted

    def health(self) -> dict[str, Any]:
        """缓存健康快照。"""
        with self._lock:
            return {
                "cached_events": len(self._queue),
                "cached_bytes": self._current_bytes,
                "max_events": self.max_events,
                "max_bytes": self.max_bytes,
                "total_cached": self._total_cached,
                "total_evicted": self._total_evicted,
            }


class EdgeEventReporter:
    """Edge→Host 事件批量上报客户端。

    职责：
    1. 校验 Edge 事件 Schema
    2. 通过 HTTP POST /v1/edge/events 批量上报到 Host
    3. 网络异常时缓存到 EdgeEventCache
    4. 网络恢复后自动补发缓存事件
    5. 指数退避重试

    transport_fn: 实际发送函数签名 (events: list[dict]) -> BatchReportResult
                  由调用方注入（支持测试 mock）
    """

    def __init__(
        self,
        *,
        transport_fn: Any | None = None,
        cache: EdgeEventCache | None = None,
        max_retry: int = MAX_RETRY_ATTEMPTS,
        base_delay_s: float = BASE_RETRY_DELAY_S,
        max_delay_s: float = MAX_RETRY_DELAY_S,
    ) -> None:
        self.transport_fn = transport_fn
        self.cache = cache or EdgeEventCache()
        self.max_retry = max_retry
        self.base_delay_s = base_delay_s
        self.max_delay_s = max_delay_s
        self._network_available = True
        self._total_sent = 0
        self._total_failed = 0
        self._total_retried = 0

    def report(self, events: list[dict[str, Any]]) -> BatchReportResult:
        """批量上报事件。

        流程：
        1. 校验所有事件
        2. 尝试发送有效事件
        3. 发送失败时缓存到本地
        4. 返回结果
        """
        if not events:
            return BatchReportResult(
                success=True, total=0, accepted=0, rejected=0,
                detail="empty batch",
            )

        # 校验
        valid_items, validation_errors = validate_batch(events)
        rejected_indices = [idx for idx, _ in validation_errors]

        if not valid_items:
            return BatchReportResult(
                success=False,
                total=len(events),
                accepted=0,
                rejected=len(events),
                reason_code=RPC_EVENT_SCHEMA_INVALID,
                detail=f"all {len(events)} events failed validation",
                rejected_indices=rejected_indices,
            )

        # 尝试发送
        send_result = self._try_send([item.to_dict() for item in valid_items])

        if send_result.success:
            self._total_sent += send_result.accepted
            # 有验证失败的事件
            if validation_errors:
                return BatchReportResult(
                    success=True,
                    total=len(events),
                    accepted=send_result.accepted,
                    rejected=len(validation_errors),
                    reason_code=RPC_EVENT_BATCH_PARTIAL_FAIL if validation_errors else None,
                    detail=f"sent {send_result.accepted}, validation rejected {len(validation_errors)}",
                    rejected_indices=rejected_indices,
                )
            return send_result
        else:
            # 发送失败，缓存所有有效事件
            for item in valid_items:
                self.cache.push(item.to_dict())
            self._total_failed += len(valid_items)

            return BatchReportResult(
                success=False,
                total=len(events),
                accepted=0,
                rejected=len(events),
                reason_code=RPC_EVENT_NETWORK_UNAVAILABLE,
                detail=f"network unavailable, cached {len(valid_items)} events",
                rejected_indices=rejected_indices,
            )

    def flush_cache(self) -> BatchReportResult:
        """尝试将缓存中的事件补发到 Host。"""
        if self.cache.size == 0:
            return BatchReportResult(
                success=True, total=0, accepted=0, rejected=0,
                detail="cache empty",
            )

        total_sent = 0
        total_failed = 0

        while self.cache.size > 0:
            batch = self.cache.drain(MAX_BATCH_SIZE)
            if not batch:
                break

            result = self._try_send(batch)
            if result.success:
                total_sent += result.accepted
            else:
                # 放回缓存
                for event in batch:
                    self.cache.push(event)
                total_failed += len(batch)
                break

        total = total_sent + total_failed
        return BatchReportResult(
            success=(total_failed == 0),
            total=total,
            accepted=total_sent,
            rejected=total_failed,
            reason_code=RPC_EVENT_NETWORK_UNAVAILABLE if total_failed > 0 else None,
            detail=f"flushed {total_sent}, failed {total_failed}",
        )

    def _try_send(self, events: list[dict[str, Any]]) -> BatchReportResult:
        """通过 transport_fn 发送事件，带指数退避重试。"""
        if self.transport_fn is None:
            return BatchReportResult(
                success=False,
                total=len(events),
                accepted=0,
                rejected=len(events),
                reason_code=RPC_EVENT_NETWORK_UNAVAILABLE,
                detail="no transport_fn configured",
            )

        last_error: str | None = None
        delay = self.base_delay_s

        for attempt in range(self.max_retry):
            try:
                result = self.transport_fn(events)
                if isinstance(result, BatchReportResult):
                    self._network_available = True
                    return result
                # transport_fn 返回 dict 兼容
                if isinstance(result, dict):
                    self._network_available = True
                    return BatchReportResult(
                        success=result.get("success", False),
                        total=result.get("total", len(events)),
                        accepted=result.get("accepted", 0),
                        rejected=result.get("rejected", len(events)),
                        reason_code=result.get("reason_code"),
                        detail=result.get("detail"),
                    )
                # 未知返回类型视为成功
                self._network_available = True
                return BatchReportResult(
                    success=True,
                    total=len(events),
                    accepted=len(events),
                    rejected=0,
                )
            except Exception as exc:
                last_error = str(exc)
                self._total_retried += 1
                self._network_available = False
                logger.warning(
                    "Event report attempt %d/%d failed: %s",
                    attempt + 1, self.max_retry, last_error,
                )
                if attempt < self.max_retry - 1:
                    time.sleep(min(delay, self.max_delay_s))
                    delay *= 2

        return BatchReportResult(
            success=False,
            total=len(events),
            accepted=0,
            rejected=len(events),
            reason_code=RPC_EVENT_NETWORK_UNAVAILABLE,
            detail=f"all {self.max_retry} attempts failed: {last_error}",
        )

    @property
    def network_available(self) -> bool:
        return self._network_available

    def health(self) -> dict[str, Any]:
        """上报器健康快照。"""
        return {
            "network_available": self._network_available,
            "total_sent": self._total_sent,
            "total_failed": self._total_failed,
            "total_retried": self._total_retried,
            "cache": self.cache.health(),
        }
