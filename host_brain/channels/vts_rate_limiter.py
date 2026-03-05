"""VTS 表情触发频率限制（TASK-0214 M24-B）。

令牌桶算法，每秒最多 N 次（可配置）。
超限时写 vts_rate_limit_log（reason_code=VTS_RATE_LIMIT_EXCEEDED）。
支持热更新频率配置（不重启）。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

from host_brain.core.errors import VTS_RATE_LIMIT_EXCEEDED
from host_brain.core.ids import now_ms


@dataclass
class VTSRateLimitResult:
    """频率限制检查结果。"""
    allowed: bool
    reason_code: str | None
    detail: str
    current_tokens: float
    max_tokens: float


class VTSRateLimiter:
    """VTS 表情触发频率限制（令牌桶算法，TASK-0214 M24-B）。

    每秒最多 N 次（可配置），支持热更新频率配置（不重启）。
    超限时写 vts_rate_limit_log（reason_code=VTS_RATE_LIMIT_EXCEEDED）。
    """

    def __init__(
        self,
        *,
        max_per_second: float = 2.0,
        burst_size: float | None = None,
    ) -> None:
        """初始化频率限制器。

        Args:
            max_per_second: 每秒最多触发次数（可热更新）。
            burst_size: 突发容量（默认等于 max_per_second）。
        """
        self._lock = threading.Lock()
        self._max_per_second = float(max_per_second)
        self._burst_size = float(burst_size if burst_size is not None else max_per_second)
        self._tokens = self._burst_size
        self._last_refill_time = time.monotonic()

    @property
    def max_per_second(self) -> float:
        return self._max_per_second

    def update_rate(self, *, max_per_second: float, burst_size: float | None = None) -> None:
        """热更新频率配置（不重启，立即生效）。

        Args:
            max_per_second: 新的每秒最多触发次数。
            burst_size: 新的突发容量（默认等于 max_per_second）。
        """
        with self._lock:
            self._max_per_second = float(max_per_second)
            new_burst = float(burst_size if burst_size is not None else max_per_second)
            self._burst_size = new_burst
            # 热更新时将令牌重置为新的 burst_size（允许立即使用新配额）
            self._tokens = new_burst

    def check(self) -> VTSRateLimitResult:
        """检查是否允许触发（消耗一个令牌）。

        Returns:
            VTSRateLimitResult：检查结果（allowed/reason_code/current_tokens）。
        """
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill_time
            self._last_refill_time = now

            # 补充令牌
            self._tokens = min(
                self._burst_size,
                self._tokens + elapsed * self._max_per_second,
            )

            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return VTSRateLimitResult(
                    allowed=True,
                    reason_code=None,
                    detail="rate limit ok",
                    current_tokens=round(self._tokens, 3),
                    max_tokens=self._burst_size,
                )
            else:
                return VTSRateLimitResult(
                    allowed=False,
                    reason_code=VTS_RATE_LIMIT_EXCEEDED,
                    detail=f"vts rate limit exceeded (max={self._max_per_second}/s, tokens={self._tokens:.3f})",
                    current_tokens=round(self._tokens, 3),
                    max_tokens=self._burst_size,
                )

    def log_rate_limit_event(
        self,
        *,
        ledger: Any,
        request_id: str,
        emotion_tag: str | None,
        ts_ms: int | None = None,
    ) -> None:
        """写入 vts_rate_limit_log（通过 ledger.log_avatar_action）。

        Args:
            ledger: SQLiteLedger 实例。
            request_id: 请求 ID。
            emotion_tag: 被限流的情绪标签。
            ts_ms: 时间戳（毫秒）。
        """
        if ledger is None:
            return
        now_value = int(ts_ms if ts_ms is not None else now_ms())
        try:
            ledger.log_avatar_action({
                "request_id": request_id,
                "trigger": "RATE_LIMITER",
                "emotion_tag": emotion_tag,
                "expression_id": None,
                "status": "RATE_LIMITED",
                "reason_code": VTS_RATE_LIMIT_EXCEEDED,
                "stage": "S_VTS_RATE_LIMIT",
                "detail": f"vts rate limit exceeded for emotion_tag={emotion_tag!r}",
                "ts_ms": now_value,
            })
        except Exception:
            pass  # 日志写入失败不影响主流程
