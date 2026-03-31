"""
M26-C LLM 路由配置热更新 + 审计测试
T-LLM-CFG-01~04 / TASK-0224 / FR-LLM-GW-003
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.integrations.llm_gateway import LlmGatewayContract, PROVIDER_OPENAI, PROVIDER_MOCK


class TestLlmRouteConfigM26(unittest.TestCase):
    """T-LLM-CFG-01~04：LLM 路由配置热更新 + 审计"""

    def setUp(self) -> None:
        self.now_ms = 1_800_300_000_000
        self.ledger = SQLiteLedger(db_path=":memory:")
        self.gateway = LlmGatewayContract(
            ledger=self.ledger,
            provider_name=PROVIDER_OPENAI,
            api_key=None,
            available=True,
            timeout_ms=30_000,
        )

    # ------------------------------------------------------------------
    # T-LLM-CFG-01：update_route_config() 热更新后立即生效
    # ------------------------------------------------------------------
    def test_t_llm_cfg_01_update_route_config_takes_effect_immediately(self) -> None:
        """update_route_config() 热更新后，新配置立即生效（不重启）"""
        self.assertEqual(PROVIDER_OPENAI, self.gateway.provider_name)
        self.assertEqual(30_000, self.gateway.timeout_ms)
        self.assertTrue(self.gateway.available)

        self.gateway.update_route_config(
            new_config={
                "provider_name": PROVIDER_MOCK,
                "timeout_ms": 5_000,
                "available": False,
            },
            request_id="cfg_01_req",
            ts_ms=self.now_ms,
        )

        # 热更新立即生效
        self.assertEqual(PROVIDER_MOCK, self.gateway.provider_name)
        self.assertEqual(5_000, self.gateway.timeout_ms)
        self.assertFalse(self.gateway.available)

    # ------------------------------------------------------------------
    # T-LLM-CFG-02：配置变更写 llm_route_config_log（含变更前后 hash）
    # ------------------------------------------------------------------
    def test_t_llm_cfg_02_config_change_written_to_audit_log(self) -> None:
        """update_route_config() 变更写 llm_route_config_log（含 before_hash/after_hash）"""
        self.gateway.update_route_config(
            new_config={"timeout_ms": 10_000},
            request_id="cfg_02_req",
            ts_ms=self.now_ms,
        )

        logs = self.ledger.query_llm_route_config_log(limit=10)
        self.assertEqual(1, len(logs))
        log = logs[0]
        self.assertEqual("cfg_02_req", log["request_id"])
        self.assertIn("before_hash", log)
        self.assertIn("after_hash", log)
        self.assertNotEqual(log["before_hash"], log["after_hash"])
        # changed_fields 包含 timeout_ms
        changed = json.loads(log["changed_fields"])
        self.assertIn("timeout_ms", changed)

    # ------------------------------------------------------------------
    # T-LLM-CFG-03：迁移脚本幂等
    # ------------------------------------------------------------------
    def test_t_llm_cfg_03_migration_script_is_idempotent(self) -> None:
        """迁移脚本幂等：重复执行不报错，表结构不变"""
        migration_path = (
            Path(__file__).resolve().parents[2]
            / "host_brain" / "audit" / "migrations" / "0040_llm_route_config_log.sql"
        )
        self.assertTrue(migration_path.exists(), "迁移脚本文件不存在")
        sql = migration_path.read_text(encoding="utf-8-sig")

        # 重复执行两次，不应报错
        import sqlite3
        conn = sqlite3.connect(":memory:")
        try:
            conn.executescript(sql)
            conn.executescript(sql)  # 第二次执行，幂等
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # T-LLM-CFG-04：配置非法时拒绝更新（INVALID_ROUTE_CONFIG）
    # ------------------------------------------------------------------
    def test_t_llm_cfg_04_invalid_config_raises_value_error(self) -> None:
        """非法配置时 update_route_config() 抛出 ValueError(INVALID_ROUTE_CONFIG)"""
        original_provider = self.gateway.provider_name
        original_timeout = self.gateway.timeout_ms

        # 非 dict 输入
        with self.assertRaises(ValueError) as ctx:
            self.gateway.update_route_config(new_config="invalid")  # type: ignore
        self.assertIn("INVALID_ROUTE_CONFIG", str(ctx.exception))

        # provider_name 非字符串
        with self.assertRaises(ValueError) as ctx:
            self.gateway.update_route_config(new_config={"provider_name": 123})
        self.assertIn("INVALID_ROUTE_CONFIG", str(ctx.exception))

        # available 非布尔值
        with self.assertRaises(ValueError) as ctx:
            self.gateway.update_route_config(new_config={"available": "yes"})
        self.assertIn("INVALID_ROUTE_CONFIG", str(ctx.exception))

        # timeout_ms 非整数
        with self.assertRaises(ValueError) as ctx:
            self.gateway.update_route_config(new_config={"timeout_ms": "30s"})
        self.assertIn("INVALID_ROUTE_CONFIG", str(ctx.exception))

        # 非法配置不应改变现有配置
        self.assertEqual(original_provider, self.gateway.provider_name)
        self.assertEqual(original_timeout, self.gateway.timeout_ms)

    # ------------------------------------------------------------------
    # 额外：无 ledger 时热更新仍生效（不崩溃）
    # ------------------------------------------------------------------
    def test_t_llm_cfg_05_update_without_ledger_does_not_crash(self) -> None:
        """无 ledger 时 update_route_config() 仍正常热更新，不崩溃"""
        gw = LlmGatewayContract(
            ledger=None,
            provider_name=PROVIDER_OPENAI,
            available=True,
        )
        gw.update_route_config(
            new_config={"available": False},
            ts_ms=self.now_ms,
        )
        self.assertFalse(gw.available)

    # ------------------------------------------------------------------
    # 额外：多次变更，每次都写审计日志
    # ------------------------------------------------------------------
    def test_t_llm_cfg_06_multiple_changes_each_written_to_log(self) -> None:
        """多次 update_route_config()，每次都写一条审计日志"""
        for i in range(3):
            self.gateway.update_route_config(
                new_config={"timeout_ms": 10_000 + i * 1_000},
                request_id=f"cfg_06_req_{i}",
                ts_ms=self.now_ms + i * 1_000,
            )

        logs = self.ledger.query_llm_route_config_log(limit=10)
        self.assertEqual(3, len(logs))


if __name__ == "__main__":
    unittest.main()
