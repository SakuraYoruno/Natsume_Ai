"""Rate Limit 可插拔存储层：InMemory / SQLite / Redis 三种后端。

提供：
- RateLimitStore（抽象接口）：incr / get_count / reset / ttl
- InMemoryRateLimitStore（默认）：基于 dict 的滑动窗口，零外部依赖
- SQLiteRateLimitStore：单机持久化，适合单节点重启保持计数
- RedisRateLimitStore：集群模式共享限流，不可用时降级到 InMemory
- create_rate_limit_store：工厂函数，按 backend 配置创建对应实例
"""

from __future__ import annotations

import abc
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from host_brain.core.errors import (
    RATE_LIMIT_BACKEND_DEGRADED,
    RATE_LIMIT_BACKEND_UNAVAILABLE,
)

# ── 后端类型常量 ──
BACKEND_MEMORY = "memory"
BACKEND_SQLITE = "sqlite"
BACKEND_REDIS = "redis"
ALLOWED_BACKENDS = frozenset({BACKEND_MEMORY, BACKEND_SQLITE, BACKEND_REDIS})

# ── 默认配置 ──
DEFAULT_WINDOW_MS = 60_000  # 60秒滑动窗口
DEFAULT_MAX_REQUESTS = 30   # 窗口内最大请求数


@dataclass(frozen=True)
class RateLimitConfig:
    """Rate Limit 配置。"""
    backend: str = BACKEND_MEMORY
    window_ms: int = DEFAULT_WINDOW_MS
    max_requests: int = DEFAULT_MAX_REQUESTS
    # SQLite 配置
    sqlite_db_path: str = ":memory:"
    # Redis 配置
    redis_url: str = "redis://localhost:6379/0"
    redis_key_prefix: str = "natsume:ratelimit:"
    redis_timeout_ms: int = 1_000


@dataclass
class RateLimitResult:
    """Rate Limit 检查结果。"""
    hit: bool                     # 是否触发限流
    current_count: int            # 当前窗口内计数
    max_requests: int             # 窗口最大允许数
    window_ms: int                # 窗口大小（毫秒）
    remaining: int                # 窗口内剩余可用
    backend: str                  # 当前使用的后端
    degraded: bool = False        # 是否降级
    degraded_from: str | None = None  # 降级前的后端
    reason_code: str | None = None    # 降级原因码

    def to_dict(self) -> dict[str, Any]:
        return {
            "hit": self.hit,
            "current_count": self.current_count,
            "max_requests": self.max_requests,
            "window_ms": self.window_ms,
            "remaining": self.remaining,
            "backend": self.backend,
            "degraded": self.degraded,
            "degraded_from": self.degraded_from,
            "reason_code": self.reason_code,
        }


class RateLimitStore(abc.ABC):
    """Rate Limit 存储抽象接口。"""

    @abc.abstractmethod
    def incr(self, key: str, *, now_ms: int | None = None) -> int:
        """递增 key 的计数并返回当前窗口内计数。"""

    @abc.abstractmethod
    def get_count(self, key: str, *, now_ms: int | None = None) -> int:
        """获取 key 当前窗口内的计数（不递增）。"""

    @abc.abstractmethod
    def reset(self, key: str) -> None:
        """重置 key 的计数。"""

    @abc.abstractmethod
    def ttl(self, key: str, *, now_ms: int | None = None) -> int:
        """返回 key 当前窗口剩余存活时间（毫秒），无窗口返回 0。"""

    @abc.abstractmethod
    def backend_name(self) -> str:
        """返回后端名称标识。"""

    def close(self) -> None:
        """关闭资源（子类可覆写）。"""


# ── InMemory 实现 ──

class InMemoryRateLimitStore(RateLimitStore):
    """基于内存 dict 的滑动窗口 Rate Limit 存储。

    每个 key 维护一个时间戳列表，incr 时清理过期时间戳。
    线程安全（使用 threading.Lock）。
    """

    def __init__(self, *, window_ms: int = DEFAULT_WINDOW_MS) -> None:
        self._window_ms = int(window_ms)
        self._data: dict[str, list[int]] = {}
        self._lock = threading.Lock()

    def incr(self, key: str, *, now_ms: int | None = None) -> int:
        ts = now_ms if now_ms is not None else _now_ms()
        cutoff = ts - self._window_ms
        with self._lock:
            timestamps = self._data.get(key, [])
            timestamps = [t for t in timestamps if t > cutoff]
            timestamps.append(ts)
            self._data[key] = timestamps
            return len(timestamps)

    def get_count(self, key: str, *, now_ms: int | None = None) -> int:
        ts = now_ms if now_ms is not None else _now_ms()
        cutoff = ts - self._window_ms
        with self._lock:
            timestamps = self._data.get(key, [])
            return sum(1 for t in timestamps if t > cutoff)

    def reset(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def ttl(self, key: str, *, now_ms: int | None = None) -> int:
        ts = now_ms if now_ms is not None else _now_ms()
        cutoff = ts - self._window_ms
        with self._lock:
            timestamps = self._data.get(key, [])
            valid = [t for t in timestamps if t > cutoff]
            if not valid:
                return 0
            oldest = min(valid)
            return max(0, oldest + self._window_ms - ts)

    def backend_name(self) -> str:
        return BACKEND_MEMORY


# ── SQLite 实现 ──

class SQLiteRateLimitStore(RateLimitStore):
    """基于 SQLite 的滑动窗口 Rate Limit 存储。

    适合单机部署，重启后计数持久化。
    使用 WAL 模式优化并发读写。
    """

    def __init__(
        self,
        *,
        window_ms: int = DEFAULT_WINDOW_MS,
        db_path: str = ":memory:",
    ) -> None:
        self._window_ms = int(window_ms)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=3000")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS rate_limit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT NOT NULL,
                    ts_ms INTEGER NOT NULL
                )
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_rate_limit_key_ts
                ON rate_limit_events (key, ts_ms)
            """)
            self._conn.commit()

    def incr(self, key: str, *, now_ms: int | None = None) -> int:
        ts = now_ms if now_ms is not None else _now_ms()
        cutoff = ts - self._window_ms
        with self._lock:
            # 清理过期记录
            self._conn.execute(
                "DELETE FROM rate_limit_events WHERE key = ? AND ts_ms <= ?",
                (key, cutoff),
            )
            # 插入新记录
            self._conn.execute(
                "INSERT INTO rate_limit_events (key, ts_ms) VALUES (?, ?)",
                (key, ts),
            )
            self._conn.commit()
            # 返回当前计数
            row = self._conn.execute(
                "SELECT COUNT(*) FROM rate_limit_events WHERE key = ? AND ts_ms > ?",
                (key, cutoff),
            ).fetchone()
            return int(row[0]) if row else 1

    def get_count(self, key: str, *, now_ms: int | None = None) -> int:
        ts = now_ms if now_ms is not None else _now_ms()
        cutoff = ts - self._window_ms
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM rate_limit_events WHERE key = ? AND ts_ms > ?",
                (key, cutoff),
            ).fetchone()
            return int(row[0]) if row else 0

    def reset(self, key: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM rate_limit_events WHERE key = ?",
                (key,),
            )
            self._conn.commit()

    def ttl(self, key: str, *, now_ms: int | None = None) -> int:
        ts = now_ms if now_ms is not None else _now_ms()
        cutoff = ts - self._window_ms
        with self._lock:
            row = self._conn.execute(
                "SELECT MIN(ts_ms) FROM rate_limit_events WHERE key = ? AND ts_ms > ?",
                (key, cutoff),
            ).fetchone()
            if not row or row[0] is None:
                return 0
            oldest = int(row[0])
            return max(0, oldest + self._window_ms - ts)

    def backend_name(self) -> str:
        return BACKEND_SQLITE

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# ── Redis 实现 ──

class RedisRateLimitStore(RateLimitStore):
    """基于 Redis Sorted Set 的滑动窗口 Rate Limit 存储。

    适合集群部署，多节点共享限流。
    Redis 不可用时抛出异常（由外层降级处理）。
    """

    def __init__(
        self,
        *,
        window_ms: int = DEFAULT_WINDOW_MS,
        redis_url: str = "redis://localhost:6379/0",
        key_prefix: str = "natsume:ratelimit:",
        timeout_ms: int = 1_000,
    ) -> None:
        self._window_ms = int(window_ms)
        self._key_prefix = str(key_prefix)
        self._timeout_ms = int(timeout_ms)
        self._redis_url = str(redis_url)
        self._client: Any = None
        self._connect()

    def _connect(self) -> None:
        """尝试连接 Redis。失败时 self._client 保持 None。"""
        try:
            import redis  # type: ignore[import-untyped]
            self._client = redis.from_url(
                self._redis_url,
                socket_timeout=self._timeout_ms / 1000.0,
                socket_connect_timeout=self._timeout_ms / 1000.0,
            )
            # 验证连接
            self._client.ping()
        except Exception:
            self._client = None
            raise

    def _full_key(self, key: str) -> str:
        return f"{self._key_prefix}{key}"

    def incr(self, key: str, *, now_ms: int | None = None) -> int:
        if self._client is None:
            raise ConnectionError("Redis client not connected")
        ts = now_ms if now_ms is not None else _now_ms()
        cutoff = ts - self._window_ms
        fk = self._full_key(key)
        pipe = self._client.pipeline()
        # 清理过期
        pipe.zremrangebyscore(fk, 0, cutoff)
        # 添加新事件（member 用 ts+随机后缀防重复）
        pipe.zadd(fk, {f"{ts}:{id(pipe)}:{_now_ms()}": ts})
        # 计数
        pipe.zcard(fk)
        # 设置过期（窗口大小的 2 倍作为 TTL）
        pipe.pexpire(fk, self._window_ms * 2)
        results = pipe.execute()
        return int(results[2])

    def get_count(self, key: str, *, now_ms: int | None = None) -> int:
        if self._client is None:
            raise ConnectionError("Redis client not connected")
        ts = now_ms if now_ms is not None else _now_ms()
        cutoff = ts - self._window_ms
        fk = self._full_key(key)
        # 先清理再计数
        self._client.zremrangebyscore(fk, 0, cutoff)
        return int(self._client.zcard(fk))

    def reset(self, key: str) -> None:
        if self._client is None:
            raise ConnectionError("Redis client not connected")
        self._client.delete(self._full_key(key))

    def ttl(self, key: str, *, now_ms: int | None = None) -> int:
        if self._client is None:
            raise ConnectionError("Redis client not connected")
        ts = now_ms if now_ms is not None else _now_ms()
        cutoff = ts - self._window_ms
        fk = self._full_key(key)
        # 获取最早的有效事件
        results = self._client.zrangebyscore(fk, cutoff + 1, "+inf", start=0, num=1, withscores=True)
        if not results:
            return 0
        oldest_ts = int(results[0][1])
        return max(0, oldest_ts + self._window_ms - ts)

    def backend_name(self) -> str:
        return BACKEND_REDIS

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass


# ── 降级包装器 ──

class DegradingRateLimitStore(RateLimitStore):
    """降级包装器：主后端不可用时自动降级到 InMemory。

    降级发生时记录 reason_code 供 rule_trace 使用。
    """

    def __init__(
        self,
        *,
        primary: RateLimitStore,
        fallback: InMemoryRateLimitStore,
        primary_backend_name: str,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._primary_name = str(primary_backend_name)
        self._degraded = False
        self._degrade_reason: str | None = None

    @property
    def degraded(self) -> bool:
        return self._degraded

    @property
    def degrade_reason(self) -> str | None:
        return self._degrade_reason

    def incr(self, key: str, *, now_ms: int | None = None) -> int:
        if self._degraded:
            return self._fallback.incr(key, now_ms=now_ms)
        try:
            return self._primary.incr(key, now_ms=now_ms)
        except Exception:
            self._mark_degraded()
            return self._fallback.incr(key, now_ms=now_ms)

    def get_count(self, key: str, *, now_ms: int | None = None) -> int:
        if self._degraded:
            return self._fallback.get_count(key, now_ms=now_ms)
        try:
            return self._primary.get_count(key, now_ms=now_ms)
        except Exception:
            self._mark_degraded()
            return self._fallback.get_count(key, now_ms=now_ms)

    def reset(self, key: str) -> None:
        if self._degraded:
            self._fallback.reset(key)
            return
        try:
            self._primary.reset(key)
        except Exception:
            self._mark_degraded()
            self._fallback.reset(key)

    def ttl(self, key: str, *, now_ms: int | None = None) -> int:
        if self._degraded:
            return self._fallback.ttl(key, now_ms=now_ms)
        try:
            return self._primary.ttl(key, now_ms=now_ms)
        except Exception:
            self._mark_degraded()
            return self._fallback.ttl(key, now_ms=now_ms)

    def backend_name(self) -> str:
        if self._degraded:
            return f"{BACKEND_MEMORY}(degraded_from:{self._primary_name})"
        return self._primary_name

    def close(self) -> None:
        self._primary.close()
        self._fallback.close()

    def _mark_degraded(self) -> None:
        self._degraded = True
        self._degrade_reason = RATE_LIMIT_BACKEND_DEGRADED


# ── 工厂函数 ──

def create_rate_limit_store(config: RateLimitConfig | None = None) -> RateLimitStore:
    """按配置创建 Rate Limit 存储实例。

    - memory：直接返回 InMemoryRateLimitStore
    - sqlite：返回 SQLiteRateLimitStore
    - redis：尝试创建 RedisRateLimitStore，失败时降级到 InMemory
    """
    cfg = config or RateLimitConfig()
    backend = str(cfg.backend).strip().lower()

    if backend == BACKEND_MEMORY:
        return InMemoryRateLimitStore(window_ms=cfg.window_ms)

    if backend == BACKEND_SQLITE:
        return SQLiteRateLimitStore(
            window_ms=cfg.window_ms,
            db_path=cfg.sqlite_db_path,
        )

    if backend == BACKEND_REDIS:
        fallback = InMemoryRateLimitStore(window_ms=cfg.window_ms)
        try:
            primary = RedisRateLimitStore(
                window_ms=cfg.window_ms,
                redis_url=cfg.redis_url,
                key_prefix=cfg.redis_key_prefix,
                timeout_ms=cfg.redis_timeout_ms,
            )
            return DegradingRateLimitStore(
                primary=primary,
                fallback=fallback,
                primary_backend_name=BACKEND_REDIS,
            )
        except Exception:
            # Redis 不可用，降级到 InMemory
            return _make_pre_degraded_store(fallback, BACKEND_REDIS)

    # 未知 backend，降级到 memory
    return InMemoryRateLimitStore(window_ms=cfg.window_ms)


def _make_pre_degraded_store(
    fallback: InMemoryRateLimitStore,
    original_backend: str,
) -> DegradingRateLimitStore:
    """创建一个预降级的存储实例（主后端创建时已失败）。"""

    class _FailedStore(RateLimitStore):
        def incr(self, key: str, *, now_ms: int | None = None) -> int:
            raise ConnectionError(f"{original_backend} unavailable")

        def get_count(self, key: str, *, now_ms: int | None = None) -> int:
            raise ConnectionError(f"{original_backend} unavailable")

        def reset(self, key: str) -> None:
            raise ConnectionError(f"{original_backend} unavailable")

        def ttl(self, key: str, *, now_ms: int | None = None) -> int:
            raise ConnectionError(f"{original_backend} unavailable")

        def backend_name(self) -> str:
            return original_backend

    store = DegradingRateLimitStore(
        primary=_FailedStore(),
        fallback=fallback,
        primary_backend_name=original_backend,
    )
    store._degraded = True
    store._degrade_reason = RATE_LIMIT_BACKEND_UNAVAILABLE
    return store


# ── Rate Limit 检查器 ──

class RateLimitChecker:
    """Rate Limit 检查器：封装存储层 + 窗口配置 + 检查逻辑。

    提供统一的 check() 方法供 R4 Gate 调用。
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
        """检查 key 是否触发限流。会自动递增计数。"""
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
        """仅查询不递增。"""
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
        """重置 key 的计数。"""
        self._store.reset(key)

    def close(self) -> None:
        """关闭底层存储。"""
        self._store.close()


def create_rate_limit_checker(config: RateLimitConfig | None = None) -> RateLimitChecker:
    """便捷入口：按配置创建 Rate Limit 检查器。"""
    cfg = config or RateLimitConfig()
    store = create_rate_limit_store(cfg)
    return RateLimitChecker(
        store=store,
        max_requests=cfg.max_requests,
        window_ms=cfg.window_ms,
    )


def _now_ms() -> int:
    return int(time.time() * 1000)
