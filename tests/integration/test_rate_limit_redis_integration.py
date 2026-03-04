"""Redis backend integration tests for governor rate-limit store.

Run condition:
- Set `NASTUME_TEST_REDIS_URL` to a reachable Redis endpoint.
- If env var is missing or Redis is unreachable, tests are skipped.
"""

from __future__ import annotations

import os
import time
import uuid

import pytest

from host_brain.governor.rate_limit_store import (
    BACKEND_REDIS,
    DegradingRateLimitStore,
    RateLimitChecker,
    RateLimitConfig,
    create_rate_limit_store,
)


def _build_prefix() -> str:
    return f"Natsume:test:ratelimit:{uuid.uuid4().hex}:"


@pytest.fixture(scope="module")
def redis_client():
    redis_url = os.getenv("NASTUME_TEST_REDIS_URL", "").strip()
    if not redis_url:
        pytest.skip("set NASTUME_TEST_REDIS_URL to run Redis integration tests")

    redis = pytest.importorskip("redis")
    client = redis.from_url(
        redis_url,
        socket_timeout=1.0,
        socket_connect_timeout=1.0,
    )
    try:
        client.ping()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"redis not reachable: {exc}")
    yield client
    try:
        client.close()
    except Exception:
        pass


def _cleanup_keys(redis_client, prefix: str) -> None:
    keys = list(redis_client.scan_iter(match=f"{prefix}*"))
    if keys:
        redis_client.delete(*keys)


def test_redis_backend_checker_end_to_end(redis_client) -> None:
    """check/check_only/reset on real Redis backend should be stable."""
    prefix = _build_prefix()
    config = RateLimitConfig(
        backend=BACKEND_REDIS,
        window_ms=5_000,
        max_requests=2,
        redis_url=os.getenv("NASTUME_TEST_REDIS_URL", "").strip(),
        redis_key_prefix=prefix,
        redis_timeout_ms=1_000,
    )
    store = create_rate_limit_store(config)
    assert isinstance(store, DegradingRateLimitStore)
    assert store.degraded is False

    checker = RateLimitChecker(
        store=store,
        max_requests=config.max_requests,
        window_ms=config.window_ms,
    )
    now = int(time.time() * 1000)
    try:
        r1 = checker.check("global", now_ms=now)
        r2 = checker.check("global", now_ms=now + 1)
        r3 = checker.check_only("global", now_ms=now + 2)

        assert r1.current_count == 1
        assert r2.current_count == 2
        assert r3.current_count == 2
        assert r3.backend == BACKEND_REDIS
        assert r3.degraded is False

        ttl = store.ttl("global", now_ms=now + 3)
        assert ttl > 0

        checker.reset("global")
        r4 = checker.check_only("global", now_ms=now + 4)
        assert r4.current_count == 0
    finally:
        _cleanup_keys(redis_client, prefix)
        checker.close()


def test_redis_backend_shared_count_across_store_instances(redis_client) -> None:
    """Two store instances should share counters through Redis."""
    prefix = _build_prefix()
    config = RateLimitConfig(
        backend=BACKEND_REDIS,
        window_ms=10_000,
        max_requests=5,
        redis_url=os.getenv("NASTUME_TEST_REDIS_URL", "").strip(),
        redis_key_prefix=prefix,
        redis_timeout_ms=1_000,
    )
    store_a = create_rate_limit_store(config)
    store_b = create_rate_limit_store(config)
    assert isinstance(store_a, DegradingRateLimitStore)
    assert isinstance(store_b, DegradingRateLimitStore)
    assert store_a.degraded is False
    assert store_b.degraded is False

    now = int(time.time() * 1000)
    try:
        assert store_a.incr("shared", now_ms=now) == 1
        assert store_b.get_count("shared", now_ms=now + 1) == 1

        assert store_b.incr("shared", now_ms=now + 2) == 2
        assert store_a.get_count("shared", now_ms=now + 3) == 2
    finally:
        _cleanup_keys(redis_client, prefix)
        store_a.close()
        store_b.close()

