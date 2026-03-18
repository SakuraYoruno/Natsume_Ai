"""T-Migration-02: World Canon 迁移文件 contract 测试（TASK-0112）。

验收标准：
- 0015_world_canon_doc.sql 存在且可被 SQLite 执行（幂等）；
- 0016_canon_proposal_log.sql 存在且可被 SQLite 执行（幂等）；
- 迁移后 world_canon_doc / canon_proposal_log / canon_change_log 表结构与 DevSpec §19.1.2/19.1.4 一致；
- sync_result_expected.json fixture 存在且字段完整。
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = ROOT / "host_brain" / "audit" / "migrations"
FIXTURE_WORLD = ROOT / "tests" / "fixtures" / "canon" / "world"


def _apply_migration(conn: sqlite3.Connection, sql_path: Path) -> None:
    sql = sql_path.read_text(encoding="utf-8-sig")
    conn.executescript(sql)


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


class TestWorldCanonMigrationContract(unittest.TestCase):
    """T-Migration-02: 迁移文件存在性 + 可执行性 + 表结构合规。"""

    def test_t_migration_02a_migration_files_exist(self) -> None:
        """0015 和 0016 迁移文件存在。"""
        for fname in ("0015_world_canon_doc.sql", "0016_canon_proposal_log.sql"):
            path = MIGRATIONS_DIR / fname
            self.assertTrue(path.exists(), msg=f"迁移文件不存在: {path}")
            self.assertGreater(path.stat().st_size, 0, msg=f"迁移文件为空: {path}")

    def test_t_migration_02b_0015_idempotent_execution(self) -> None:
        """0015_world_canon_doc.sql 可被 SQLite 执行，且幂等（重复执行不报错）。"""
        sql_path = MIGRATIONS_DIR / "0015_world_canon_doc.sql"
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.sqlite3"
            conn = sqlite3.connect(str(db))
            try:
                # 第一次执行
                _apply_migration(conn, sql_path)
                # 第二次执行（幂等）
                _apply_migration(conn, sql_path)
                # 表存在
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                self.assertIn("world_canon_doc", tables)
            finally:
                conn.close()

    def test_t_migration_02c_0016_idempotent_execution(self) -> None:
        """0016_canon_proposal_log.sql 可被 SQLite 执行，且幂等（重复执行不报错）。"""
        sql_path = MIGRATIONS_DIR / "0016_canon_proposal_log.sql"
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.sqlite3"
            conn = sqlite3.connect(str(db))
            try:
                _apply_migration(conn, sql_path)
                _apply_migration(conn, sql_path)
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                self.assertIn("canon_proposal_log", tables)
                self.assertIn("canon_change_log", tables)
            finally:
                conn.close()

    def test_t_migration_02d_world_canon_doc_schema(self) -> None:
        """world_canon_doc 表字段与 DevSpec §19.1.2 一致。"""
        sql_path = MIGRATIONS_DIR / "0015_world_canon_doc.sql"
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.sqlite3"
            conn = sqlite3.connect(str(db))
            try:
                _apply_migration(conn, sql_path)
                cols = _table_columns(conn, "world_canon_doc")
                required = {"doc_id", "version", "hash", "status", "source_path", "updated_ts"}
                self.assertTrue(
                    required.issubset(cols),
                    msg=f"world_canon_doc 缺少字段: {required - cols}",
                )
            finally:
                conn.close()

    def test_t_migration_02e_canon_proposal_log_schema(self) -> None:
        """canon_proposal_log 表字段与 DevSpec §19.1.4 一致。"""
        sql_path = MIGRATIONS_DIR / "0016_canon_proposal_log.sql"
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.sqlite3"
            conn = sqlite3.connect(str(db))
            try:
                _apply_migration(conn, sql_path)
                cols = _table_columns(conn, "canon_proposal_log")
                required = {
                    "proposal_id", "canon_kind", "target_id",
                    "base_version", "proposed_version", "proposed_hash",
                    "proposer_node_id", "summary", "status", "ts_ms",
                }
                self.assertTrue(
                    required.issubset(cols),
                    msg=f"canon_proposal_log 缺少字段: {required - cols}",
                )
            finally:
                conn.close()

    def test_t_migration_02f_canon_change_log_schema(self) -> None:
        """canon_change_log 表字段与 DevSpec §19.1.4 一致（含 before/after hash）。"""
        sql_path = MIGRATIONS_DIR / "0016_canon_proposal_log.sql"
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.sqlite3"
            conn = sqlite3.connect(str(db))
            try:
                _apply_migration(conn, sql_path)
                cols = _table_columns(conn, "canon_change_log")
                required = {
                    "change_id", "proposal_id", "canon_kind", "target_id",
                    "before_version", "after_version",
                    "before_hash", "after_hash",
                    "source_path", "ts_ms",
                }
                self.assertTrue(
                    required.issubset(cols),
                    msg=f"canon_change_log 缺少字段: {required - cols}",
                )
            finally:
                conn.close()

    def test_t_migration_02g_sync_result_fixture_exists_and_valid(self) -> None:
        """sync_result_expected.json fixture 存在且字段完整。"""
        fixture_path = FIXTURE_WORLD / "sync_result_expected.json"
        self.assertTrue(fixture_path.exists(), msg=f"fixture 不存在: {fixture_path}")
        data = json.loads(fixture_path.read_text(encoding="utf-8-sig"))
        # 顶层字段
        for field in ("sync_ts_ms", "records", "total"):
            self.assertIn(field, data, msg=f"sync_result_expected.json 缺少字段: {field}")
        self.assertEqual(data["total"], len(data["records"]))
        # 每条记录字段
        for rec in data["records"]:
            for field in ("doc_id", "version", "hash", "source_path", "changed"):
                self.assertIn(field, rec, msg=f"records 条目缺少字段: {field}")
        # 3 个种子条目
        self.assertEqual(3, data["total"], msg="sync_result_expected.json 应包含 3 个种子条目")

    def test_t_migration_02h_indexes_created_by_0015(self) -> None:
        """0015 迁移后 world_canon_doc 索引已创建。"""
        sql_path = MIGRATIONS_DIR / "0015_world_canon_doc.sql"
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.sqlite3"
            conn = sqlite3.connect(str(db))
            try:
                _apply_migration(conn, sql_path)
                indexes = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='world_canon_doc'"
                    ).fetchall()
                }
                self.assertIn("idx_world_canon_doc_status", indexes)
                self.assertIn("idx_world_canon_doc_updated_ts", indexes)
            finally:
                conn.close()

    def test_t_migration_02i_indexes_created_by_0016(self) -> None:
        """0016 迁移后 canon_proposal_log / canon_change_log 索引已创建。"""
        sql_path = MIGRATIONS_DIR / "0016_canon_proposal_log.sql"
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.sqlite3"
            conn = sqlite3.connect(str(db))
            try:
                _apply_migration(conn, sql_path)
                all_indexes = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='index'"
                    ).fetchall()
                }
                for idx in (
                    "idx_canon_proposal_target",
                    "idx_canon_proposal_status",
                    "idx_canon_change_target",
                    "idx_canon_change_ts",
                ):
                    self.assertIn(idx, all_indexes, msg=f"索引未创建: {idx}")
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
