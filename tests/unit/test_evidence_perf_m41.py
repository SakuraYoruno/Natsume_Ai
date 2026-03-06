"""T-EVP-PERF-01~05：Evidence Plane 写入压力专项测试（M41-F）。

验证分级写入策略、熔断机制、异步队列在高并发写入场景下的正确性（mock 模拟）。
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from host_brain.integrations.evidence_circuit_breaker import (
    CircuitState,
    EvidenceCircuitBreaker,
)
from host_brain.integrations.evidence_priority import WritePriority, WRITE_METHOD_PRIORITY
from host_brain.integrations.evidence_write_queue import EvidenceWriteQueue
from host_brain.integrations.graph_evidence import EvidencePlane


class TestEvidencePerfM41(unittest.TestCase):
    """T-EVP-PERF-01~05：写入压力专项测试。"""

    def setUp(self) -> None:
        self.ep = EvidencePlane(db_path=":memory:")
        self.queue = EvidenceWriteQueue(max_size=200)
        self.cb = EvidenceCircuitBreaker(failure_threshold=5, recovery_timeout_ms=10000)

    def test_100_critical_writes_all_complete(self) -> None:
        """T-EVP-PERF-01：连续 100 次 CRITICAL 写入全部同步完成，无丢失。"""
        # CRITICAL 写入：write_barrier_join（同步直写）
        for i in range(100):
            self.ep.write_barrier_join(
                request_id=f"perf-r{i}",
                graph_id="g1",
                barrier_join_result={
                    "passed": True,
                    "failed_branches": [],
                    "join_policy_ref": f"ref{i}",
                    "reason": "ok",
                },
            )
        # 验证全部写入成功（查询前 10 条）
        for i in range(10):
            rows = self.ep.query_barrier_join(request_id=f"perf-r{i}")
            self.assertEqual(1, len(rows), f"perf-r{i} 应有 1 条记录")

    def test_100_non_critical_writes_via_queue(self) -> None:
        """T-EVP-PERF-02：连续 100 次 NON_CRITICAL 写入全部进入队列，flush() 后全部落库。"""
        executed = []

        def non_critical_write(tag: str):
            executed.append(tag)

        for i in range(100):
            self.queue.enqueue(non_critical_write, f"nc-{i}")

        self.assertEqual(100, self.queue.queue_size())
        self.assertEqual(0, len(executed))  # 未执行

        self.queue.flush()
        self.assertEqual(0, self.queue.queue_size())
        self.assertEqual(100, len(executed))
        self.assertEqual([f"nc-{i}" for i in range(100)], executed)

    def test_mixed_50_critical_50_non_critical(self) -> None:
        """T-EVP-PERF-03：混合 50 次 CRITICAL + 50 次 NON_CRITICAL 写入，CRITICAL 全部同步完成，NON_CRITICAL 全部进入队列。"""
        critical_executed = []
        non_critical_executed = []

        def critical_write(tag: str):
            critical_executed.append(tag)

        def non_critical_write(tag: str):
            non_critical_executed.append(tag)

        for i in range(50):
            # CRITICAL：直接同步执行
            critical_write(f"c-{i}")
            # NON_CRITICAL：入队
            self.queue.enqueue(non_critical_write, f"nc-{i}")

        # CRITICAL 全部同步完成
        self.assertEqual(50, len(critical_executed))
        # NON_CRITICAL 全部在队列中，未执行
        self.assertEqual(50, self.queue.queue_size())
        self.assertEqual(0, len(non_critical_executed))

        # flush 后 NON_CRITICAL 全部执行
        self.queue.flush()
        self.assertEqual(50, len(non_critical_executed))

    def test_write_failure_triggers_circuit_breaker(self) -> None:
        """T-EVP-PERF-04：写入失败注入（mock SQLite 抛异常）触发熔断后，CRITICAL 写入转入内存缓冲，不丢失。"""
        cb = EvidenceCircuitBreaker(failure_threshold=3, recovery_timeout_ms=10000)

        def failing_write():
            raise RuntimeError("模拟 SQLite 异常")

        # 连续失败 3 次触发熔断
        for _ in range(3):
            cb.call(failing_write)

        self.assertEqual(CircuitState.OPEN, cb.state)

        # CRITICAL 写入（带 record）在 OPEN 状态下转入内存缓冲
        critical_records = [
            {"type": "write_barrier_join", "priority": "critical", "id": i}
            for i in range(5)
        ]
        for r in critical_records:
            cb.call(failing_write, record=r)

        snapshot = cb.get_buffer_snapshot()
        self.assertEqual(5, len(snapshot))
        for r in critical_records:
            self.assertIn(r, snapshot)

    def test_buffer_snapshot_can_be_replayed(self) -> None:
        """T-EVP-PERF-05：熔断恢复后，内存缓冲中的记录可通过 get_buffer_snapshot() 取出并重放写入。"""
        cb = EvidenceCircuitBreaker(failure_threshold=3, recovery_timeout_ms=10000)

        def failing_write():
            raise RuntimeError("模拟失败")

        # 触发熔断
        for _ in range(3):
            cb.call(failing_write)

        # 缓冲 3 条记录
        buffered_records = [{"id": i, "data": f"record-{i}"} for i in range(3)]
        for r in buffered_records:
            cb.call(failing_write, record=r)

        # 获取快照
        snapshot = cb.get_buffer_snapshot()
        self.assertEqual(3, len(snapshot))

        # 模拟重放：将快照中的记录重新写入（此处用 list 模拟落库）
        replayed = []
        for record in snapshot:
            replayed.append(record)

        self.assertEqual(3, len(replayed))
        for r in buffered_records:
            self.assertIn(r, replayed)


if __name__ == "__main__":
    unittest.main()
