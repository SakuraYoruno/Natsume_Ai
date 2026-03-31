"""T-EVP-CB-01~08：EvidenceCircuitBreaker 熔断机制测试（M41-D）。"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from host_brain.integrations.evidence_circuit_breaker import (
    CircuitState,
    EvidenceCircuitBreaker,
)


class TestEvidenceCircuitBreakerM41(unittest.TestCase):
    """T-EVP-CB-01~08：验证 EvidenceCircuitBreaker 三态状态机行为。"""

    def setUp(self) -> None:
        self.cb = EvidenceCircuitBreaker(failure_threshold=3, recovery_timeout_ms=500)

    def _make_failing_writer(self):
        def _write():
            raise RuntimeError("模拟写入失败")
        return _write

    def _make_success_writer(self, executed: list):
        def _write():
            executed.append("ok")
        return _write

    def test_initial_state_is_closed(self) -> None:
        """T-EVP-CB-01：初始状态为 CLOSED，写入正常执行。"""
        self.assertEqual(CircuitState.CLOSED, self.cb.state)
        executed = []
        result = self.cb.call(self._make_success_writer(executed))
        self.assertTrue(result)
        self.assertEqual(["ok"], executed)

    def test_consecutive_failures_trigger_open(self) -> None:
        """T-EVP-CB-02：连续失败 failure_threshold 次后状态变为 OPEN。"""
        for _ in range(3):
            self.cb.call(self._make_failing_writer())
        self.assertEqual(CircuitState.OPEN, self.cb.state)

    def test_open_state_buffers_writes(self) -> None:
        """T-EVP-CB-03：OPEN 状态下写入不执行，转入 _memory_buffer。"""
        # 先触发熔断
        for _ in range(3):
            self.cb.call(self._make_failing_writer())
        self.assertEqual(CircuitState.OPEN, self.cb.state)
        # OPEN 状态下写入
        executed = []
        record = {"type": "write_llm_call", "data": "test"}
        result = self.cb.call(self._make_success_writer(executed), record=record)
        self.assertFalse(result)
        self.assertEqual([], executed)  # 未执行
        self.assertIn(record, self.cb.get_buffer_snapshot())

    def test_open_transitions_to_half_open_after_timeout(self) -> None:
        """T-EVP-CB-04：OPEN 状态持续 recovery_timeout_ms 后自动转为 HALF_OPEN。"""
        # 触发熔断
        for _ in range(3):
            self.cb.call(self._make_failing_writer())
        self.assertEqual(CircuitState.OPEN, self.cb.state)
        # 模拟时间流逝超过 recovery_timeout_ms
        import time
        with patch("host_brain.integrations.evidence_circuit_breaker.time") as mock_time:
            # 设置 perf_counter 返回值：open_at + 1s（超过 500ms）
            open_at = self.cb._open_at_mono
            mock_time.perf_counter.return_value = open_at + 1.0
            state = self.cb.state
        self.assertEqual(CircuitState.HALF_OPEN, state)

    def test_half_open_success_recovers_to_closed(self) -> None:
        """T-EVP-CB-05：HALF_OPEN 状态下探测写入成功后状态恢复为 CLOSED。"""
        # 直接设置为 HALF_OPEN
        self.cb._state = CircuitState.HALF_OPEN
        executed = []
        result = self.cb.call(self._make_success_writer(executed))
        self.assertTrue(result)
        self.assertEqual(["ok"], executed)
        self.assertEqual(CircuitState.CLOSED, self.cb._state)

    def test_half_open_failure_reverts_to_open(self) -> None:
        """T-EVP-CB-06：HALF_OPEN 状态下探测写入失败后状态回退为 OPEN。"""
        self.cb._state = CircuitState.HALF_OPEN
        result = self.cb.call(self._make_failing_writer())
        self.assertFalse(result)
        self.assertEqual(CircuitState.OPEN, self.cb._state)

    def test_critical_write_buffered_in_open_state(self) -> None:
        """T-EVP-CB-07：CRITICAL 写入在 OPEN 状态下仍写入 _memory_buffer，不丢失。"""
        # 触发熔断
        for _ in range(3):
            self.cb.call(self._make_failing_writer())
        # CRITICAL 写入（带 record）
        critical_record = {"type": "write_barrier_join", "priority": "critical", "data": "barrier"}
        executed = []
        self.cb.call(self._make_success_writer(executed), record=critical_record)
        # 未执行但已缓冲
        self.assertEqual([], executed)
        snapshot = self.cb.get_buffer_snapshot()
        self.assertIn(critical_record, snapshot)

    def test_get_buffer_snapshot_returns_all_records(self) -> None:
        """T-EVP-CB-08：get_buffer_snapshot() 返回当前内存缓冲中的所有待写入记录。"""
        # 触发熔断
        for _ in range(3):
            self.cb.call(self._make_failing_writer())
        # 写入多条记录
        records = [{"id": i} for i in range(5)]
        for r in records:
            self.cb.call(self._make_failing_writer(), record=r)
        snapshot = self.cb.get_buffer_snapshot()
        self.assertEqual(5, len(snapshot))
        for r in records:
            self.assertIn(r, snapshot)

    def test_reset_clears_state(self) -> None:
        """reset() 后状态恢复为 CLOSED，缓冲清空。"""
        for _ in range(3):
            self.cb.call(self._make_failing_writer())
        self.cb.call(self._make_failing_writer(), record={"x": 1})
        self.cb.reset()
        self.assertEqual(CircuitState.CLOSED, self.cb._state)
        self.assertEqual(0, self.cb._failure_count)
        self.assertEqual([], self.cb.get_buffer_snapshot())


if __name__ == "__main__":
    unittest.main()
