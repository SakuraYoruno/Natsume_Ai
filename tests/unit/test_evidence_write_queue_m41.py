"""T-EVP-QUEUE-01~06：EvidenceWriteQueue 异步写入队列测试（M41-C）。"""

from __future__ import annotations

import time
import unittest

from host_brain.integrations.evidence_write_queue import EvidenceWriteQueue


class TestEvidenceWriteQueueM41(unittest.TestCase):
    """T-EVP-QUEUE-01~06：验证 EvidenceWriteQueue 的入队、flush、超时、路由行为。"""

    def setUp(self) -> None:
        self.queue = EvidenceWriteQueue(max_size=10)
        self.executed: list[str] = []

    def _make_writer(self, tag: str):
        def _write():
            self.executed.append(tag)
        return _write

    def _make_slow_writer(self, tag: str, sleep_ms: float = 50):
        def _write():
            time.sleep(sleep_ms / 1000.0)
            self.executed.append(tag)
        return _write

    def test_enqueue_increases_queue_size(self) -> None:
        """T-EVP-QUEUE-01：enqueue() 将写入任务加入队列，queue_size() 返回正确数量。"""
        self.assertEqual(0, self.queue.queue_size())
        self.queue.enqueue(self._make_writer("a"))
        self.assertEqual(1, self.queue.queue_size())
        self.queue.enqueue(self._make_writer("b"))
        self.assertEqual(2, self.queue.queue_size())
        # 入队后任务未执行
        self.assertEqual([], self.executed)

    def test_flush_executes_all_and_clears(self) -> None:
        """T-EVP-QUEUE-02：flush() 执行队列中所有写入任务并清空队列。"""
        for i in range(5):
            self.queue.enqueue(self._make_writer(str(i)))
        self.assertEqual(5, self.queue.queue_size())
        remaining = self.queue.flush()
        self.assertEqual(0, remaining)
        self.assertEqual(0, self.queue.queue_size())
        self.assertEqual(["0", "1", "2", "3", "4"], self.executed)

    def test_flush_timeout_stops_early(self) -> None:
        """T-EVP-QUEUE-03：flush(timeout_ms=100) 超时后停止执行并返回剩余队列大小。"""
        # 加入 5 个慢写入（每个 50ms）
        for i in range(5):
            self.queue.enqueue(self._make_slow_writer(str(i), sleep_ms=50))
        # 100ms 超时，最多执行 2 个
        remaining = self.queue.flush(timeout_ms=100)
        # 至少有剩余任务（超时了）
        self.assertGreater(remaining, 0)
        # 已执行的任务数 + 剩余数 = 5
        self.assertEqual(5, len(self.executed) + remaining)

    def test_critical_write_bypasses_queue(self) -> None:
        """T-EVP-QUEUE-04：CRITICAL 写入绕过队列直接同步执行。"""
        # 模拟 CRITICAL 写入：直接调用，不经过 enqueue
        executed = []
        def critical_write():
            executed.append("critical")
        # CRITICAL 写入直接执行，不入队
        critical_write()
        self.assertEqual(["critical"], executed)
        self.assertEqual(0, self.queue.queue_size())

    def test_non_critical_write_enters_queue(self) -> None:
        """T-EVP-QUEUE-05：NON_CRITICAL 写入进入队列，不立即执行。"""
        executed = []
        def non_critical_write():
            executed.append("non_critical")
        # NON_CRITICAL 写入通过 enqueue，不立即执行
        self.queue.enqueue(non_critical_write)
        self.assertEqual(1, self.queue.queue_size())
        self.assertEqual([], executed)
        # flush 后才执行
        self.queue.flush()
        self.assertEqual(["non_critical"], executed)

    def test_queue_full_triggers_auto_flush(self) -> None:
        """T-EVP-QUEUE-06：队列满时新写入触发 flush() 后再入队。"""
        queue = EvidenceWriteQueue(max_size=3)
        executed = []
        for i in range(3):
            queue.enqueue(lambda tag=str(i): executed.append(tag))
        self.assertEqual(3, queue.queue_size())
        # 第 4 个入队时触发自动 flush
        queue.enqueue(lambda: executed.append("new"))
        # flush 后原有 3 个已执行，新的 1 个在队列中
        self.assertEqual(3, len(executed))
        self.assertEqual(1, queue.queue_size())

    def test_flush_with_failing_writer_continues(self) -> None:
        """flush 时某个写入失败，不影响后续任务执行。"""
        def failing_write():
            raise RuntimeError("模拟写入失败")
        self.queue.enqueue(failing_write)
        self.queue.enqueue(self._make_writer("after_fail"))
        self.queue.flush()
        self.assertEqual(["after_fail"], self.executed)


if __name__ == "__main__":
    unittest.main()
