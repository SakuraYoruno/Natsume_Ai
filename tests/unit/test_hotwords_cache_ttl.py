"""M10-E F-42：_CANON_HOTWORDS_CACHE TTL 自动失效专项测试。

T-HW-TTL-01：TTL 未到期时 get() 返回完整热词列表。
T-HW-TTL-02：TTL 到期后 get() 返回空列表（模拟时间推进）。
T-HW-TTL-03：is_valid() 在有效期内返回 True，到期后返回 False。
T-HW-TTL-04：clear() 立即使缓存失效，is_valid() 返回 False。
T-HW-TTL-05：并发写入安全——多线程同时 set() 不引发竞态，最终缓存非空。
"""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest

from edge_client.senses.asr import _HotwordsCache, _CANON_HOTWORDS_CACHE


# ---------------------------------------------------------------------------
# T-HW-TTL-01：TTL 未到期时 get() 返回完整热词列表
# ---------------------------------------------------------------------------

def test_cache_get_within_ttl():
    """set() 后立即 get()，TTL 未到期，应返回完整热词列表。"""
    cache = _HotwordsCache(ttl_s=60.0)
    words = ["夏目贵志", "猫咪老师", "妖怪"]
    cache.set(words)

    result = cache.get()
    assert result == words, f"期望 {words}，实际 {result}"


# ---------------------------------------------------------------------------
# T-HW-TTL-02：TTL 到期后 get() 返回空列表
# ---------------------------------------------------------------------------

def test_cache_get_after_ttl_expired():
    """通过 mock time.monotonic 模拟 TTL 到期，get() 应返回空列表。"""
    cache = _HotwordsCache(ttl_s=10.0)
    words = ["夏目贵志", "猫咪老师"]

    base_time = 1000.0
    # set() 时记录 base_time
    with patch("time.monotonic", return_value=base_time):
        cache.set(words)

    # 模拟 11 秒后（超过 TTL=10s）
    with patch("time.monotonic", return_value=base_time + 11.0):
        result = cache.get()

    assert result == [], f"TTL 到期后应返回空列表，实际 {result}"


# ---------------------------------------------------------------------------
# T-HW-TTL-03：is_valid() 在有效期内返回 True，到期后返回 False
# ---------------------------------------------------------------------------

def test_cache_is_valid_transitions():
    """is_valid() 应在 TTL 内返回 True，到期后返回 False。"""
    cache = _HotwordsCache(ttl_s=5.0)
    words = ["妖怪", "人类"]

    base_time = 2000.0
    with patch("time.monotonic", return_value=base_time):
        cache.set(words)

    # TTL 内（4 秒后）
    with patch("time.monotonic", return_value=base_time + 4.0):
        assert cache.is_valid() is True, "TTL 内 is_valid() 应为 True"

    # TTL 到期（6 秒后）
    with patch("time.monotonic", return_value=base_time + 6.0):
        assert cache.is_valid() is False, "TTL 到期后 is_valid() 应为 False"


# ---------------------------------------------------------------------------
# T-HW-TTL-04：clear() 立即使缓存失效
# ---------------------------------------------------------------------------

def test_cache_clear_invalidates_immediately():
    """clear() 后 is_valid() 应立即返回 False，get() 返回空列表。"""
    cache = _HotwordsCache(ttl_s=300.0)
    cache.set(["夏目贵志"])

    assert cache.is_valid() is True, "clear() 前 is_valid() 应为 True"

    cache.clear()

    assert cache.is_valid() is False, "clear() 后 is_valid() 应为 False"
    assert cache.get() == [], "clear() 后 get() 应返回空列表"
    assert cache.age_s() == 0.0, "clear() 后 age_s() 应为 0.0"


# ---------------------------------------------------------------------------
# T-HW-TTL-05：并发写入安全——多线程同时 set() 不引发竞态
# ---------------------------------------------------------------------------

def test_cache_concurrent_set_thread_safe():
    """多线程同时调用 set() 不应引发异常，最终缓存应为非空列表。"""
    cache = _HotwordsCache(ttl_s=300.0)
    errors: list[Exception] = []

    def worker(idx: int) -> None:
        try:
            cache.set([f"词汇_{idx}_{i}" for i in range(10)])
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"并发 set() 引发异常：{errors}"
    result = cache.get()
    assert isinstance(result, list), "get() 应返回 list"
    assert len(result) > 0, "并发写入后缓存应非空"
