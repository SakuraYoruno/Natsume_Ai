"""T-EVP-IDX-01~06：Evidence Plane 分区索引优化测试（M41-E）。"""

from __future__ import annotations

import sqlite3
import unittest

from host_brain.integrations.graph_evidence import EvidencePlane


def _get_indexes(conn: sqlite3.Connection, table: str) -> set[str]:
    """获取指定表的所有索引名称。"""
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?",
        (table,),
    )
    return {row[0] for row in cursor.fetchall()}


class TestEvidenceIndexM41(unittest.TestCase):
    """T-EVP-IDX-01~06：验证分区索引正确创建且幂等。"""

    def setUp(self) -> None:
        self.ep = EvidencePlane(db_path=":memory:")
        self.conn = self.ep._connect()

    def tearDown(self) -> None:
        # :memory: 模式下连接由 EvidencePlane 管理，不在此关闭
        pass

    def test_barrier_join_log_run_id_index(self) -> None:
        """T-EVP-IDX-01：barrier_join_log 表存在 idx_bjl_run_id 索引。"""
        indexes = _get_indexes(self.conn, "barrier_join_log")
        self.assertIn("idx_bjl_run_id", indexes)

    def test_barrier_join_log_ts_index(self) -> None:
        """T-EVP-IDX-02：barrier_join_log 表存在 idx_bjl_ts 索引。"""
        indexes = _get_indexes(self.conn, "barrier_join_log")
        self.assertIn("idx_bjl_ts", indexes)

    def test_subgraph_trace_run_id_index(self) -> None:
        """T-EVP-IDX-03：subgraph_trace 表存在 idx_sgt_run_id 索引。"""
        indexes = _get_indexes(self.conn, "subgraph_trace")
        self.assertIn("idx_sgt_run_id", indexes)

    def test_subgraph_trace_parent_path_index(self) -> None:
        """T-EVP-IDX-04：subgraph_trace 表存在 idx_sgt_parent_path 索引。"""
        indexes = _get_indexes(self.conn, "subgraph_trace")
        self.assertIn("idx_sgt_parent_path", indexes)

    def test_llm_call_log_run_id_index(self) -> None:
        """T-EVP-IDX-05：llm_call_log 表存在 idx_lcl_run_id 索引。"""
        indexes = _get_indexes(self.conn, "llm_call_log")
        self.assertIn("idx_lcl_run_id", indexes)

    def test_llm_call_log_node_id_index(self) -> None:
        """T-EVP-IDX-06：llm_call_log 表存在 idx_lcl_node_id 索引。"""
        indexes = _get_indexes(self.conn, "llm_call_log")
        self.assertIn("idx_lcl_node_id", indexes)

    def test_ensure_tables_idempotent(self) -> None:
        """T-EVP-IDX-06b：_init_db() 重复调用时索引创建幂等（IF NOT EXISTS 不报错）。"""
        # 重复初始化不应抛出异常
        try:
            ep2 = EvidencePlane(db_path=":memory:")
            ep2._init_db()
        except Exception as exc:
            self.fail(f"重复调用 _init_db() 抛出异常: {exc}")


if __name__ == "__main__":
    unittest.main()
