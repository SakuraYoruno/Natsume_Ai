"""Redis connection and sorted-set operations for distributed rate limits."""

from __future__ import annotations

import time
from typing import Any


def _now_ms() -> int:
    return int(time.time() * 1000)


class RedisRateLimitRepository:
    """Own the Redis client lifecycle and atomic-window storage primitives."""

    def __init__(
        self,
        *,
        window_ms: int,
        redis_url: str,
        key_prefix: str,
        timeout_ms: int,
    ) -> None:
        self._window_ms = int(window_ms)
        self._key_prefix = str(key_prefix)
        self._client: Any = None
        try:
            import redis  # type: ignore[import-untyped]

            self._client = redis.from_url(
                str(redis_url),
                socket_timeout=int(timeout_ms) / 1000.0,
                socket_connect_timeout=int(timeout_ms) / 1000.0,
            )
            self._client.ping()
        except Exception:
            self._client = None
            raise

    def _require_client(self) -> Any:
        if self._client is None:
            raise ConnectionError("Redis client not connected")
        return self._client

    def _full_key(self, key: str) -> str:
        return f"{self._key_prefix}{key}"

    def increment(self, key: str, *, ts_ms: int) -> int:
        client = self._require_client()
        cutoff = int(ts_ms) - self._window_ms
        full_key = self._full_key(key)
        pipe = client.pipeline()
        pipe.zremrangebyscore(full_key, 0, cutoff)
        pipe.zadd(full_key, {f"{ts_ms}:{id(pipe)}:{_now_ms()}": int(ts_ms)})
        pipe.zcard(full_key)
        pipe.pexpire(full_key, self._window_ms * 2)
        results = pipe.execute()
        return int(results[2])

    def count(self, key: str, *, ts_ms: int) -> int:
        client = self._require_client()
        cutoff = int(ts_ms) - self._window_ms
        full_key = self._full_key(key)
        client.zremrangebyscore(full_key, 0, cutoff)
        return int(client.zcard(full_key))

    def reset(self, key: str) -> None:
        self._require_client().delete(self._full_key(key))

    def ttl(self, key: str, *, ts_ms: int) -> int:
        client = self._require_client()
        cutoff = int(ts_ms) - self._window_ms
        rows = client.zrangebyscore(
            self._full_key(key),
            cutoff + 1,
            "+inf",
            start=0,
            num=1,
            withscores=True,
        )
        if not rows:
            return 0
        return max(0, int(rows[0][1]) + self._window_ms - int(ts_ms))

    def close(self) -> None:
        if self._client is None:
            return
        try:
            self._client.close()
        except Exception:
            pass
