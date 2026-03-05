"""微信通道健康状态管理（TASK-0215 M24-C）。

维护微信通道健康状态（last_recv_ts/last_send_ts/status/error_count）。
连续失败 N 次后自动降级（WECHAT_CHANNEL_DEGRADED）。
降级时仍接受 inbound，停止 outbound。
恢复时写 wechat_channel_recovery_log。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from host_brain.core.errors import (
    RATE_LIMIT_EXCEEDED,
    WECHAT_CHANNEL_DEGRADED,
    WECHAT_RATE_LIMIT_EXCEEDED,
    WECHAT_SEND_FAILED,
)
from host_brain.core.ids import now_ms


WECHAT_STATUS_UP = "UP"
WECHAT_STATUS_DEGRADED = "DEGRADED"
WECHAT_STATUS_DOWN = "DOWN"

# 默认连续失败阈值（超过此次数后自动降级）
DEFAULT_FAILURE_THRESHOLD = 3
# 默认每个 thread_key 每分钟最多推送条数
DEFAULT_OUTBOUND_RATE_PER_MINUTE = 10
# 健康状态刷新间隔（秒）
HEALTH_REFRESH_INTERVAL_S = 30


@dataclass
class WeChatChannelStatus:
    """微信通道健康状态快照。"""
    status: str                          # UP / DEGRADED / DOWN
    last_recv_ts_ms: int | None          # 最后收到消息时间戳
    last_send_ts_ms: int | None          # 最后发送消息时间戳
    error_count: int                     # 连续失败次数
    degraded_since_ts_ms: int | None     # 降级开始时间戳
    outbound_blocked: bool               # outbound 是否被阻断
    reason_code: str | None              # 降级原因码


@dataclass
class WeChatRateLimitResult:
    """频率限制检查结果。"""
    allowed: bool
    reason_code: str | None
    detail: str
    current_count: int
    max_count: int


class WeChatChannelHealth:
    """微信通道健康状态管理（TASK-0215 M24-C）。

    维护健康状态（last_recv_ts/last_send_ts/status/error_count）。
    连续失败 N 次后自动降级（WECHAT_CHANNEL_DEGRADED）。
    降级时仍接受 inbound，停止 outbound。
    恢复时写 wechat_channel_recovery_log。
    """

    def __init__(
        self,
        *,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
        outbound_rate_per_minute: int = DEFAULT_OUTBOUND_RATE_PER_MINUTE,
        ledger: Any = None,
    ) -> None:
        """初始化通道健康管理器。

        Args:
            failure_threshold: 连续失败阈值（超过后自动降级）。
            outbound_rate_per_minute: 每个 thread_key 每分钟最多推送条数。
            ledger: SQLiteLedger 实例（用于写入恢复日志）。
        """
        self._lock = threading.Lock()
        self._failure_threshold = int(failure_threshold)
        self._outbound_rate_per_minute = int(outbound_rate_per_minute)
        self._ledger = ledger

        self._status = WECHAT_STATUS_UP
        self._last_recv_ts_ms: int | None = None
        self._last_send_ts_ms: int | None = None
        self._error_count = 0
        self._degraded_since_ts_ms: int | None = None

        # 频率限制：{thread_key: [(ts_ms, count), ...]}
        self._outbound_counts: dict[str, list[int]] = {}

    def record_recv(self, *, ts_ms: int | None = None) -> None:
        """记录收到 inbound 消息（更新 last_recv_ts）。"""
        with self._lock:
            self._last_recv_ts_ms = int(ts_ms if ts_ms is not None else now_ms())

    def record_send_success(self, *, ts_ms: int | None = None) -> None:
        """记录 outbound 发送成功（重置错误计数，可能触发恢复）。"""
        with self._lock:
            now_value = int(ts_ms if ts_ms is not None else now_ms())
            self._last_send_ts_ms = now_value
            prev_error_count = self._error_count
            self._error_count = 0

            # 如果之前是降级状态，现在恢复
            if self._status == WECHAT_STATUS_DEGRADED and prev_error_count > 0:
                self._status = WECHAT_STATUS_UP
                self._degraded_since_ts_ms = None
                self._write_recovery_log(ts_ms=now_value)

    def record_send_failure(self, *, ts_ms: int | None = None) -> None:
        """记录 outbound 发送失败（累计错误计数，可能触发降级）。"""
        with self._lock:
            now_value = int(ts_ms if ts_ms is not None else now_ms())
            self._error_count += 1

            if self._error_count >= self._failure_threshold and self._status == WECHAT_STATUS_UP:
                self._status = WECHAT_STATUS_DEGRADED
                self._degraded_since_ts_ms = now_value

    def check_outbound_allowed(self, *, thread_key: str, ts_ms: int | None = None) -> WeChatRateLimitResult:
        """检查 outbound 是否允许推送（频率限制 + 降级检查）。

        Args:
            thread_key: 微信会话 thread_key。
            ts_ms: 当前时间戳（毫秒）。

        Returns:
            WeChatRateLimitResult：检查结果。
        """
        with self._lock:
            now_value = int(ts_ms if ts_ms is not None else now_ms())

            # 降级时阻断 outbound
            if self._status == WECHAT_STATUS_DEGRADED:
                return WeChatRateLimitResult(
                    allowed=False,
                    reason_code=WECHAT_CHANNEL_DEGRADED,
                    detail=f"wechat channel degraded (error_count={self._error_count})",
                    current_count=self._error_count,
                    max_count=self._failure_threshold,
                )

            # 频率限制：每个 thread_key 每分钟最多 N 条
            window_start_ms = now_value - 60_000  # 1 分钟窗口
            counts = self._outbound_counts.get(thread_key, [])
            # 清理过期记录
            counts = [ts for ts in counts if ts > window_start_ms]
            current_count = len(counts)

            if current_count >= self._outbound_rate_per_minute:
                self._outbound_counts[thread_key] = counts
                return WeChatRateLimitResult(
                    allowed=False,
                    reason_code=WECHAT_RATE_LIMIT_EXCEEDED,
                    detail=(
                        f"wechat outbound rate limit exceeded for thread_key={thread_key!r} "
                        f"({current_count}/{self._outbound_rate_per_minute} per minute)"
                    ),
                    current_count=current_count,
                    max_count=self._outbound_rate_per_minute,
                )

            # 允许推送，记录本次时间戳
            counts.append(now_value)
            self._outbound_counts[thread_key] = counts
            return WeChatRateLimitResult(
                allowed=True,
                reason_code=None,
                detail="outbound allowed",
                current_count=current_count + 1,
                max_count=self._outbound_rate_per_minute,
            )

    def get_status(self) -> WeChatChannelStatus:
        """获取当前通道健康状态快照（30 秒自动刷新由调用方负责）。"""
        with self._lock:
            return WeChatChannelStatus(
                status=self._status,
                last_recv_ts_ms=self._last_recv_ts_ms,
                last_send_ts_ms=self._last_send_ts_ms,
                error_count=self._error_count,
                degraded_since_ts_ms=self._degraded_since_ts_ms,
                outbound_blocked=(self._status == WECHAT_STATUS_DEGRADED),
                reason_code=(WECHAT_CHANNEL_DEGRADED if self._status == WECHAT_STATUS_DEGRADED else None),
            )

    def reset_for_recovery(self, *, ts_ms: int | None = None) -> None:
        """手动重置降级状态（用于测试或运维干预）。"""
        with self._lock:
            now_value = int(ts_ms if ts_ms is not None else now_ms())
            if self._status == WECHAT_STATUS_DEGRADED:
                self._status = WECHAT_STATUS_UP
                self._error_count = 0
                self._degraded_since_ts_ms = None
                self._write_recovery_log(ts_ms=now_value)

    def _write_recovery_log(self, *, ts_ms: int) -> None:
        """写入 wechat_channel_recovery_log（通过 ledger.log_channel_io）。"""
        if self._ledger is None:
            return
        try:
            from host_brain.core.ids import new_request_id
            self._ledger.log_channel_io({
                "request_id": new_request_id(),
                "channel": "wechat",
                "direction": "RECOVERY",
                "thread_key": "system",
                "message_id": f"recovery_{ts_ms}",
                "user_id": None,
                "status": "RECOVERED",
                "reason_code": None,
                "stage": "S_CHANNEL_RECOVERY",
                "detail": "wechat channel recovered from degraded state",
                "payload_digest": None,
                "ts_ms": ts_ms,
            })
        except Exception:
            pass  # 恢复日志写入失败不影响主流程
