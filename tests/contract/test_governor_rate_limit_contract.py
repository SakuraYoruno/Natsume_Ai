"""T-Gov-R4-01: Rate Limit 多 backend 一致性与降级稳定性契约测试。

覆盖：
1. InMemory 后端：滑动窗口计数、过期清理、TTL、reset
2. SQLite 后端：同 InMemory 行为一致性
3. 多 backend 切换后同输入同结果
4. Redis 不可用时降级到 InMemory 且 rule_trace 可标注 backend 信息
5. RateLimitChecker 封装：check() / check_only() / reset() 行为
6. R4 Gate 集成：evaluate_governor_gates 输出含 backend 标注
7. 配置加载：HOST_RATE_LIMIT_BACKEND 环境变量解析
8. Fixture 可加载且结构完整

断言点：
- 同输入同结果（InMemory vs SQLite）
- 窗口过期后计数归零
- 多 key 独立计数互不干扰
- 降级后 backend_name 包含 degraded 标记
- R4 Gate threshold_summary 包含 backend 字段
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from host_brain.core.errors import (
    RATE_LIMITED,
    RATE_LIMIT_BACKEND_DEGRADED,
    RATE_LIMIT_BACKEND_UNAVAILABLE,
)
from host_brain.governor.rate_limit_store import (
    BACKEND_MEMORY,
    BACKEND_REDIS,
    BACKEND_SQLITE,
    DegradingRateLimitStore,
    InMemoryRateLimitStore,
    RateLimitChecker,
    RateLimitConfig,
    RateLimitResult,
    SQLiteRateLimitStore,
    create_rate_limit_checker,
    create_rate_limit_store,
)
from host_brain.governor.rules import evaluate_governor_gates


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "governor_rate_limit"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TestInMemoryRateLimitStore(unittest.TestCase):
    """T-Gov-R4-01.InMemory: InMemory 后端滑动窗口行为。"""

    def setUp(self) -> None:
        self.store = InMemoryRateLimitStore(window_ms=10_000)

    def test_01_incr_counts_correctly(self) -> None:
        """T-Gov-R4-01.1: incr 递增计数。"""
        self.assertEqual(1, self.store.incr("k1", now_ms=100_000))
        self.assertEqual(2, self.store.incr("k1", now_ms=102_000))
        self.assertEqual(3, self.store.incr("k1", now_ms=104_000))

    def test_02_get_count_no_increment(self) -> None:
        """T-Gov-R4-01.2: get_count 不递增。"""
        self.store.incr("k1", now_ms=100_000)
        self.assertEqual(1, self.store.get_count("k1", now_ms=100_000))
        self.assertEqual(1, self.store.get_count("k1", now_ms=100_000))

    def test_03_window_expiry(self) -> None:
        """T-Gov-R4-01.3: 窗口过期后计数归零。"""
        self.store.incr("k1", now_ms=100_000)
        self.store.incr("k1", now_ms=102_000)
        # 窗口过期后
        self.assertEqual(0, self.store.get_count("k1", now_ms=120_000))

    def test_04_partial_expiry(self) -> None:
        """T-Gov-R4-01.4: 部分过期——仅清理超出窗口的记录。"""
        self.store.incr("k1", now_ms=100_000)
        self.store.incr("k1", now_ms=105_000)
        # 100000 过期，105000 仍在窗口内
        self.assertEqual(1, self.store.get_count("k1", now_ms=111_000))

    def test_05_reset(self) -> None:
        """T-Gov-R4-01.5: reset 清空计数。"""
        self.store.incr("k1", now_ms=100_000)
        self.store.incr("k1", now_ms=102_000)
        self.store.reset("k1")
        self.assertEqual(0, self.store.get_count("k1", now_ms=103_000))

    def test_06_ttl(self) -> None:
        """T-Gov-R4-01.6: ttl 返回窗口剩余时间。"""
        self.store.incr("k1", now_ms=100_000)
        ttl = self.store.ttl("k1", now_ms=105_000)
        self.assertGreater(ttl, 0)
        self.assertLessEqual(ttl, 10_000)

    def test_07_ttl_empty(self) -> None:
        """T-Gov-R4-01.7: 无记录时 ttl 返回 0。"""
        self.assertEqual(0, self.store.ttl("k_none", now_ms=100_000))

    def test_08_backend_name(self) -> None:
        """T-Gov-R4-01.8: backend_name 返回 memory。"""
        self.assertEqual(BACKEND_MEMORY, self.store.backend_name())

    def test_09_multi_key_isolation(self) -> None:
        """T-Gov-R4-01.9: 多 key 独立计数。"""
        self.store.incr("A", now_ms=100_000)
        self.store.incr("B", now_ms=100_000)
        self.store.incr("A", now_ms=101_000)
        self.assertEqual(2, self.store.get_count("A", now_ms=101_000))
        self.assertEqual(1, self.store.get_count("B", now_ms=101_000))


class TestSQLiteRateLimitStore(unittest.TestCase):
    """T-Gov-R4-01.SQLite: SQLite 后端与 InMemory 行为一致性。"""

    def setUp(self) -> None:
        self.store = SQLiteRateLimitStore(window_ms=10_000, db_path=":memory:")

    def tearDown(self) -> None:
        self.store.close()

    def test_10_incr_counts_correctly(self) -> None:
        """T-Gov-R4-01.10: SQLite incr 递增计数。"""
        self.assertEqual(1, self.store.incr("k1", now_ms=100_000))
        self.assertEqual(2, self.store.incr("k1", now_ms=102_000))

    def test_11_window_expiry(self) -> None:
        """T-Gov-R4-01.11: SQLite 窗口过期后计数归零。"""
        self.store.incr("k1", now_ms=100_000)
        self.assertEqual(0, self.store.get_count("k1", now_ms=120_000))

    def test_12_reset(self) -> None:
        """T-Gov-R4-01.12: SQLite reset 清空计数。"""
        self.store.incr("k1", now_ms=100_000)
        self.store.reset("k1")
        self.assertEqual(0, self.store.get_count("k1", now_ms=101_000))

    def test_13_ttl(self) -> None:
        """T-Gov-R4-01.13: SQLite ttl 返回窗口剩余时间。"""
        self.store.incr("k1", now_ms=100_000)
        ttl = self.store.ttl("k1", now_ms=105_000)
        self.assertGreater(ttl, 0)

    def test_14_backend_name(self) -> None:
        """T-Gov-R4-01.14: backend_name 返回 sqlite。"""
        self.assertEqual(BACKEND_SQLITE, self.store.backend_name())

    def test_15_multi_key_isolation(self) -> None:
        """T-Gov-R4-01.15: SQLite 多 key 独立计数。"""
        self.store.incr("A", now_ms=100_000)
        self.store.incr("B", now_ms=100_000)
        self.store.incr("A", now_ms=101_000)
        self.assertEqual(2, self.store.get_count("A", now_ms=101_000))
        self.assertEqual(1, self.store.get_count("B", now_ms=101_000))


class TestBackendConsistency(unittest.TestCase):
    """T-Gov-R4-01.Consistency: 多 backend 同输入同结果。"""

    def test_16_memory_sqlite_same_result(self) -> None:
        """T-Gov-R4-01.16: InMemory 与 SQLite 同输入同结果。"""
        mem = InMemoryRateLimitStore(window_ms=10_000)
        sql = SQLiteRateLimitStore(window_ms=10_000, db_path=":memory:")

        events = [
            ("k1", 100_000),
            ("k1", 102_000),
            ("k1", 104_000),
            ("k1", 105_000),
        ]

        mem_counts = []
        sql_counts = []
        for key, ts in events:
            mem_counts.append(mem.incr(key, now_ms=ts))
            sql_counts.append(sql.incr(key, now_ms=ts))

        self.assertEqual(mem_counts, sql_counts, "InMemory 与 SQLite 计数序列必须一致")
        sql.close()

    def test_17_memory_sqlite_after_expiry(self) -> None:
        """T-Gov-R4-01.17: 过期后两种后端行为一致。"""
        mem = InMemoryRateLimitStore(window_ms=5_000)
        sql = SQLiteRateLimitStore(window_ms=5_000, db_path=":memory:")

        mem.incr("k1", now_ms=100_000)
        sql.incr("k1", now_ms=100_000)

        mem_count = mem.get_count("k1", now_ms=110_000)
        sql_count = sql.get_count("k1", now_ms=110_000)

        self.assertEqual(mem_count, sql_count)
        self.assertEqual(0, mem_count)
        sql.close()


class TestDegradingStore(unittest.TestCase):
    """T-Gov-R4-01.Degrading: Redis 不可用时降级行为。"""

    def test_18_redis_unavailable_degrades_to_memory(self) -> None:
        """T-Gov-R4-01.18: Redis 不可用时降级到 InMemory。"""
        config = RateLimitConfig(
            backend=BACKEND_REDIS,
            redis_url="redis://nonexistent:9999/0",
            redis_timeout_ms=100,
        )
        store = create_rate_limit_store(config)
        self.assertIsInstance(store, DegradingRateLimitStore)
        self.assertTrue(store.degraded)
        self.assertIn("degraded", store.backend_name())

    def test_19_degraded_store_still_functional(self) -> None:
        """T-Gov-R4-01.19: 降级后存储仍可正常工作。"""
        config = RateLimitConfig(
            backend=BACKEND_REDIS,
            window_ms=10_000,
            max_requests=3,
            redis_url="redis://nonexistent:9999/0",
            redis_timeout_ms=100,
        )
        store = create_rate_limit_store(config)
        # 降级后仍可 incr
        count = store.incr("test", now_ms=100_000)
        self.assertEqual(1, count)

    def test_20_degraded_backend_name_contains_degraded_from(self) -> None:
        """T-Gov-R4-01.20: 降级后 backend_name 包含原始后端信息。"""
        config = RateLimitConfig(
            backend=BACKEND_REDIS,
            redis_url="redis://nonexistent:9999/0",
            redis_timeout_ms=100,
        )
        store = create_rate_limit_store(config)
        name = store.backend_name()
        self.assertIn("degraded_from", name)
        self.assertIn("redis", name)

    def test_21_degraded_degrade_reason(self) -> None:
        """T-Gov-R4-01.21: 降级原因码为 RATE_LIMIT_BACKEND_UNAVAILABLE。"""
        config = RateLimitConfig(
            backend=BACKEND_REDIS,
            redis_url="redis://nonexistent:9999/0",
            redis_timeout_ms=100,
        )
        store = create_rate_limit_store(config)
        self.assertIsInstance(store, DegradingRateLimitStore)
        self.assertEqual(RATE_LIMIT_BACKEND_UNAVAILABLE, store.degrade_reason)


class TestRateLimitChecker(unittest.TestCase):
    """T-Gov-R4-01.Checker: RateLimitChecker 封装行为。"""

    def test_22_check_increments_and_returns_result(self) -> None:
        """T-Gov-R4-01.22: check() 递增并返回结果。"""
        checker = create_rate_limit_checker(RateLimitConfig(
            backend=BACKEND_MEMORY, window_ms=10_000, max_requests=2,
        ))
        r1 = checker.check("test", now_ms=100_000)
        self.assertFalse(r1.hit)
        self.assertEqual(1, r1.current_count)
        self.assertEqual(2, r1.max_requests)
        self.assertEqual(1, r1.remaining)

        r2 = checker.check("test", now_ms=101_000)
        self.assertFalse(r2.hit)

        r3 = checker.check("test", now_ms=102_000)
        self.assertTrue(r3.hit)
        self.assertEqual(0, r3.remaining)

    def test_23_check_only_no_increment(self) -> None:
        """T-Gov-R4-01.23: check_only() 不递增。"""
        checker = create_rate_limit_checker(RateLimitConfig(
            backend=BACKEND_MEMORY, window_ms=10_000, max_requests=5,
        ))
        checker.check("test", now_ms=100_000)
        r = checker.check_only("test", now_ms=100_000)
        self.assertEqual(1, r.current_count)

    def test_24_checker_reset(self) -> None:
        """T-Gov-R4-01.24: checker.reset() 清空计数。"""
        checker = create_rate_limit_checker(RateLimitConfig(
            backend=BACKEND_MEMORY, window_ms=10_000, max_requests=5,
        ))
        checker.check("test", now_ms=100_000)
        checker.check("test", now_ms=101_000)
        checker.reset("test")
        r = checker.check_only("test", now_ms=102_000)
        self.assertEqual(0, r.current_count)

    def test_25_checker_result_to_dict(self) -> None:
        """T-Gov-R4-01.25: RateLimitResult.to_dict() 结构完整。"""
        checker = create_rate_limit_checker(RateLimitConfig(
            backend=BACKEND_MEMORY, window_ms=10_000, max_requests=3,
        ))
        r = checker.check("test", now_ms=100_000)
        d = r.to_dict()
        self.assertIn("hit", d)
        self.assertIn("current_count", d)
        self.assertIn("backend", d)
        self.assertIn("remaining", d)
        self.assertEqual(BACKEND_MEMORY, d["backend"])

    def test_26_checker_degraded_result(self) -> None:
        """T-Gov-R4-01.26: 降级后 checker 结果含 degraded 信息。"""
        checker = create_rate_limit_checker(RateLimitConfig(
            backend=BACKEND_REDIS,
            redis_url="redis://nonexistent:9999/0",
            redis_timeout_ms=100,
            window_ms=10_000,
            max_requests=5,
        ))
        r = checker.check("test", now_ms=100_000)
        self.assertTrue(r.degraded)
        self.assertIsNotNone(r.reason_code)


class TestR4GateIntegration(unittest.TestCase):
    """T-Gov-R4-01.Gate: R4 Gate 集成 backend 标注。"""

    def test_27_r4_gate_with_backend_info(self) -> None:
        """T-Gov-R4-01.27: R4 Gate threshold_summary 包含 backend。"""
        result = evaluate_governor_gates({
            "rate_limit_hit": True,
            "rate_limit_backend": "memory",
        })
        r4 = [g for g in result["rule_trace"] if g["gate_id"] == "R4_RATE_LIMIT"][0]
        self.assertTrue(r4["hit"])
        self.assertEqual("memory", r4["threshold_summary"]["backend"])
        self.assertEqual(RATE_LIMITED, r4["reason_hint"])

    def test_28_r4_gate_with_degraded_info(self) -> None:
        """T-Gov-R4-01.28: R4 Gate 降级时 threshold_summary 包含 degraded 标记。"""
        result = evaluate_governor_gates({
            "rate_limit_hit": False,
            "rate_limit_backend": "memory(degraded_from:redis)",
            "rate_limit_degraded": True,
            "rate_limit_degraded_from": "redis",
            "rate_limit_reason_code": RATE_LIMIT_BACKEND_UNAVAILABLE,
        })
        r4 = [g for g in result["rule_trace"] if g["gate_id"] == "R4_RATE_LIMIT"][0]
        self.assertFalse(r4["hit"])
        self.assertTrue(r4["threshold_summary"]["degraded"])
        self.assertEqual("redis", r4["threshold_summary"]["degraded_from"])
        self.assertEqual(
            RATE_LIMIT_BACKEND_UNAVAILABLE,
            r4["snapshot_summary"]["degraded_reason_code"],
        )

    def test_29_r4_gate_backward_compatible(self) -> None:
        """T-Gov-R4-01.29: 不传 backend 字段时 R4 Gate 仍正常（向后兼容）。"""
        result = evaluate_governor_gates({
            "rate_limit_hit": False,
        })
        r4 = [g for g in result["rule_trace"] if g["gate_id"] == "R4_RATE_LIMIT"][0]
        self.assertFalse(r4["hit"])
        self.assertIn("window", r4["threshold_summary"])
        self.assertEqual("PASS", r4["reason_hint"])

    def test_30_r4_gate_decision_reject_on_hit(self) -> None:
        """T-Gov-R4-01.30: rate_limit_hit=True 时 decision=REJECT。"""
        result = evaluate_governor_gates({
            "rate_limit_hit": True,
            "rate_limit_backend": "sqlite",
        })
        self.assertEqual("REJECT", result["decision"])
        self.assertEqual(RATE_LIMITED, result["reason_code"])


class TestConfigLoading(unittest.TestCase):
    """T-Gov-R4-01.Config: Rate Limit 配置加载。"""

    def test_31_default_config_memory(self) -> None:
        """T-Gov-R4-01.31: 默认 backend=memory。"""
        from host_brain.app.config import load_host_config
        result = load_host_config(env={
            "Nastume_ENV": "dev",
            "HOST_LLM_PROVIDER": "mock",
            "HOST_COLD_START_ALLOWED": "true",
        })
        self.assertTrue(result.success)
        self.assertEqual("memory", result.config.rate_limit_backend)

    def test_32_config_sqlite_backend(self) -> None:
        """T-Gov-R4-01.32: HOST_RATE_LIMIT_BACKEND=sqlite 解析正确。"""
        from host_brain.app.config import load_host_config
        result = load_host_config(env={
            "Nastume_ENV": "dev",
            "HOST_LLM_PROVIDER": "mock",
            "HOST_COLD_START_ALLOWED": "true",
            "HOST_RATE_LIMIT_BACKEND": "sqlite",
        })
        self.assertTrue(result.success)
        self.assertEqual("sqlite", result.config.rate_limit_backend)

    def test_33_config_redis_backend(self) -> None:
        """T-Gov-R4-01.33: HOST_RATE_LIMIT_BACKEND=redis 解析正确。"""
        from host_brain.app.config import load_host_config
        result = load_host_config(env={
            "Nastume_ENV": "dev",
            "HOST_LLM_PROVIDER": "mock",
            "HOST_COLD_START_ALLOWED": "true",
            "HOST_RATE_LIMIT_BACKEND": "redis",
        })
        self.assertTrue(result.success)
        self.assertEqual("redis", result.config.rate_limit_backend)

    def test_34_config_invalid_backend_fallback(self) -> None:
        """T-Gov-R4-01.34: 无效 backend 值降级到 memory（带警告）。"""
        from host_brain.app.config import load_host_config
        result = load_host_config(env={
            "Nastume_ENV": "dev",
            "HOST_LLM_PROVIDER": "mock",
            "HOST_COLD_START_ALLOWED": "true",
            "HOST_RATE_LIMIT_BACKEND": "unknown",
        })
        # 无效值被纠正为 memory 但可能产生 issue
        self.assertTrue(result.success)
        self.assertEqual("memory", result.config.rate_limit_backend)

    def test_35_config_window_and_max(self) -> None:
        """T-Gov-R4-01.35: window_ms 和 max_requests 可配置。"""
        from host_brain.app.config import load_host_config
        result = load_host_config(env={
            "Nastume_ENV": "dev",
            "HOST_LLM_PROVIDER": "mock",
            "HOST_COLD_START_ALLOWED": "true",
            "HOST_RATE_LIMIT_WINDOW_MS": "30000",
            "HOST_RATE_LIMIT_MAX_REQUESTS": "100",
        })
        self.assertTrue(result.success)
        self.assertEqual(30_000, result.config.rate_limit_window_ms)
        self.assertEqual(100, result.config.rate_limit_max_requests)


class TestFactoryFunction(unittest.TestCase):
    """T-Gov-R4-01.Factory: 工厂函数行为。"""

    def test_36_create_memory_store(self) -> None:
        """T-Gov-R4-01.36: 创建 memory 存储。"""
        store = create_rate_limit_store(RateLimitConfig(backend=BACKEND_MEMORY))
        self.assertIsInstance(store, InMemoryRateLimitStore)

    def test_37_create_sqlite_store(self) -> None:
        """T-Gov-R4-01.37: 创建 sqlite 存储。"""
        store = create_rate_limit_store(RateLimitConfig(backend=BACKEND_SQLITE))
        self.assertIsInstance(store, SQLiteRateLimitStore)
        store.close()

    def test_38_create_redis_store_degrades(self) -> None:
        """T-Gov-R4-01.38: Redis 不可用时工厂返回降级存储。"""
        store = create_rate_limit_store(RateLimitConfig(
            backend=BACKEND_REDIS,
            redis_url="redis://nonexistent:9999/0",
            redis_timeout_ms=100,
        ))
        self.assertIsInstance(store, DegradingRateLimitStore)


class TestFixtures(unittest.TestCase):
    """T-Gov-R4-01.Fixtures: Fixture 结构验证。"""

    def test_39_window_sliding_fixture(self) -> None:
        """T-Gov-R4-01.39: window_sliding fixture 可加载。"""
        fix = _load_json(FIXTURE_DIR / "window_sliding.json")
        self.assertEqual("rate_limit_window_sliding", fix["scenario"])
        self.assertIn("events", fix)
        self.assertGreaterEqual(len(fix["events"]), 1)

    def test_40_window_expired_fixture(self) -> None:
        """T-Gov-R4-01.40: window_expired fixture 可加载。"""
        fix = _load_json(FIXTURE_DIR / "window_expired.json")
        self.assertEqual("rate_limit_window_expired", fix["scenario"])
        self.assertIn("events_phase1", fix)
        self.assertIn("events_phase2_after_expiry", fix)

    def test_41_concurrent_keys_fixture(self) -> None:
        """T-Gov-R4-01.41: concurrent_keys fixture 可加载。"""
        fix = _load_json(FIXTURE_DIR / "concurrent_keys.json")
        self.assertEqual("rate_limit_concurrent_keys", fix["scenario"])
        self.assertTrue(fix["expected"]["keys_isolated"])

    def test_42_fixture_driven_sliding_window(self) -> None:
        """T-Gov-R4-01.42: fixture 驱动的滑动窗口测试。"""
        fix = _load_json(FIXTURE_DIR / "window_sliding.json")
        cfg = fix["config"]
        checker = create_rate_limit_checker(RateLimitConfig(
            backend=cfg["backend"],
            window_ms=cfg["window_ms"],
            max_requests=cfg["max_requests"],
        ))
        for event in fix["events"]:
            r = checker.check(event["key"], now_ms=event["ts_ms"])
            self.assertEqual(
                event["expected_count"], r.current_count,
                f"count mismatch at ts_ms={event['ts_ms']}",
            )
            self.assertEqual(
                event["expected_hit"], r.hit,
                f"hit mismatch at ts_ms={event['ts_ms']}",
            )

    def test_43_fixture_driven_window_expiry(self) -> None:
        """T-Gov-R4-01.43: fixture 驱动的窗口过期测试。"""
        fix = _load_json(FIXTURE_DIR / "window_expired.json")
        cfg = fix["config"]
        checker = create_rate_limit_checker(RateLimitConfig(
            backend=cfg["backend"],
            window_ms=cfg["window_ms"],
            max_requests=cfg["max_requests"],
        ))
        # Phase 1
        for event in fix["events_phase1"]:
            r = checker.check(event["key"], now_ms=event["ts_ms"])
            self.assertEqual(event["expected_count"], r.current_count)
            self.assertEqual(event["expected_hit"], r.hit)

        # Phase 2（窗口过期后）
        for event in fix["events_phase2_after_expiry"]:
            r = checker.check(event["key"], now_ms=event["ts_ms"])
            self.assertEqual(event["expected_count"], r.current_count)
            self.assertEqual(event["expected_hit"], r.hit)

    def test_44_fixture_driven_concurrent_keys(self) -> None:
        """T-Gov-R4-01.44: fixture 驱动的多 key 独立计数测试。"""
        fix = _load_json(FIXTURE_DIR / "concurrent_keys.json")
        cfg = fix["config"]
        checker = create_rate_limit_checker(RateLimitConfig(
            backend=cfg["backend"],
            window_ms=cfg["window_ms"],
            max_requests=cfg["max_requests"],
        ))
        for event in fix["events"]:
            r = checker.check(event["key"], now_ms=event["ts_ms"])
            self.assertEqual(
                event["expected_count"], r.current_count,
                f"count mismatch for key={event['key']} at ts_ms={event['ts_ms']}",
            )
            self.assertEqual(
                event["expected_hit"], r.hit,
                f"hit mismatch for key={event['key']} at ts_ms={event['ts_ms']}",
            )


class TestGovernorRulesRegression(unittest.TestCase):
    """T-Gov-R4-01.Regression: 现有 T-Gov-01 回归。"""

    def test_45_existing_r1_r7_trace_structure(self) -> None:
        """T-Gov-R4-01.45: R1~R7 trace 结构不变。"""
        result = evaluate_governor_gates({"trust_level": 50})
        self.assertEqual("APPROVE", result["decision"])
        self.assertEqual(7, len(result["rule_trace"]))
        for row in result["rule_trace"]:
            self.assertIn("gate_id", row)
            self.assertIn("hit", row)
            self.assertIn("threshold_summary", row)
            self.assertIn("snapshot_summary", row)
            self.assertIn("reason_hint", row)

    def test_46_existing_reject_reason_code(self) -> None:
        """T-Gov-R4-01.46: REJECT 仍带 reason_code。"""
        result = evaluate_governor_gates({"trust_level": 10})
        self.assertEqual("REJECT", result["decision"])
        self.assertIsNotNone(result["reason_code"])

    def test_47_stable_output_for_same_input(self) -> None:
        """T-Gov-R4-01.47: 同输入稳定输出。"""
        payload = {"trust_level": 80, "rate_limit_hit": False}
        r1 = evaluate_governor_gates(payload)
        r2 = evaluate_governor_gates(payload)
        self.assertEqual(r1, r2)


if __name__ == "__main__":
    unittest.main()
