"""Rate Limit 检查器。

从 rate_limit_store.py 拆分，提供 RateLimitChecker 和工厂函数。
"""

from __future__ import annotations

from typing import Any

from host_brain.governor.rate_limit_store import (
    BACKEND_REDIS,
    DEFAULT_MAX_REQUESTS,
    DEFAULT_WINDOW_MS,
    DegradingRateLimitStore,
    RateLimitConfig,
    RateLimitResult,
    RateLimitStore,
    create_rate_limit_store,
    load_rate_limit_config,
)

class RateLimitChecker:
    """Rate Limit 检查器：封装存储层 + 窗口配置 + 检查逻辑。

    提供统一的 check() 方法供 R4 Gate 调用。``check`` 会消耗一个请求槽，适合真实准入；
    ``check_only`` 只用于展示或预检，不能替代真实准入，否则并发调用会绕过计数。
    """

    def __init__(
        self,
        *,
        store: RateLimitStore,
        max_requests: int = DEFAULT_MAX_REQUESTS,
        window_ms: int = DEFAULT_WINDOW_MS,
    ) -> None:
        self._store = store
        self._max_requests = int(max_requests)
        self._window_ms = int(window_ms)

    def check(self, key: str = "global", *, now_ms: int | None = None) -> RateLimitResult:
        """原子地计数并判断是否超过配额，将存储降级状态显式传给上层门控。"""
        count = self._store.incr(key, now_ms=now_ms)
        hit = count > self._max_requests
        remaining = max(0, self._max_requests - count)

        degraded = False
        degraded_from: str | None = None
        reason_code: str | None = None

        if isinstance(self._store, DegradingRateLimitStore) and self._store.degraded:
            degraded = True
            degraded_from = BACKEND_REDIS  # 降级前是外部后端
            reason_code = self._store.degrade_reason

        return RateLimitResult(
            hit=hit,
            current_count=count,
            max_requests=self._max_requests,
            window_ms=self._window_ms,
            remaining=remaining,
            backend=self._store.backend_name(),
            degraded=degraded,
            degraded_from=degraded_from,
            reason_code=reason_code,
        )

    def check_only(self, key: str = "global", *, now_ms: int | None = None) -> RateLimitResult:
        """只查询当前计数，不预留或消耗配额，结果仅适合非授权性观察。"""
        count = self._store.get_count(key, now_ms=now_ms)
        hit = count > self._max_requests
        remaining = max(0, self._max_requests - count)

        degraded = False
        degraded_from: str | None = None
        reason_code: str | None = None

        if isinstance(self._store, DegradingRateLimitStore) and self._store.degraded:
            degraded = True
            degraded_from = BACKEND_REDIS
            reason_code = self._store.degrade_reason

        return RateLimitResult(
            hit=hit,
            current_count=count,
            max_requests=self._max_requests,
            window_ms=self._window_ms,
            remaining=remaining,
            backend=self._store.backend_name(),
            degraded=degraded,
            degraded_from=degraded_from,
            reason_code=reason_code,
        )

    def reset(self, key: str = "global") -> None:
        """重置指定键；调用方必须把它限制在受控维护/测试路径，不能暴露给普通请求。"""
        self._store.reset(key)

    def close(self) -> None:
        """关闭底层存储。"""
        self._store.close()


def create_rate_limit_checker(config: RateLimitConfig | None = None) -> RateLimitChecker:
    """便捷入口：按配置创建 Rate Limit 检查器。"""
    cfg = config or load_rate_limit_config()
    store = create_rate_limit_store(cfg)
    return RateLimitChecker(
        store=store,
        max_requests=cfg.max_requests,
        window_ms=cfg.window_ms,
    )


def _parse_int(value: Any, *, default: int, min_value: int, max_value: int) -> int:
    """解析配置整数并夹紧范围，格式错误回退到明确默认值而非抛给准入热路径。"""
    if value is None or str(value).strip() == "":
        return int(default)
    try:
        parsed = int(str(value).strip())
    except ValueError:
        return int(default)
    return min(max(parsed, min_value), max_value)
