"""历史路由配置迁移的存储兼容性测试。"""

from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path


class TestLlmRouteConfigMigration(unittest.TestCase):
    """保留迁移脚本幂等性，旧的网关热更新 API 已退休。"""

    def test_migration_script_is_idempotent(self) -> None:
        migration_path = (
            Path(__file__).resolve().parents[2]
            / "host_brain"
            / "audit"
            / "migrations"
            / "0040_llm_route_config_log.sql"
        )
        self.assertTrue(migration_path.exists(), "迁移脚本文件不存在")
        sql = migration_path.read_text(encoding="utf-8-sig")

        conn = sqlite3.connect(":memory:")
        try:
            conn.executescript(sql)
            conn.executescript(sql)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
