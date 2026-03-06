"""T-EVP-PRI-01~06：WritePriority 枚举分级测试（M41-B）。"""

from __future__ import annotations

import unittest

from host_brain.integrations.evidence_priority import WritePriority, WRITE_METHOD_PRIORITY


class TestEvidencePriorityM41(unittest.TestCase):
    """T-EVP-PRI-01~06：验证 WritePriority 枚举和各方法默认优先级。"""

    def test_priority_enum_has_critical(self) -> None:
        """T-EVP-PRI-01：WritePriority 枚举包含 CRITICAL 和 NON_CRITICAL 两个值。"""
        self.assertIn(WritePriority.CRITICAL, WritePriority)
        self.assertIn(WritePriority.NON_CRITICAL, WritePriority)
        self.assertEqual(len(WritePriority), 2)

    def test_write_route_token_is_critical(self) -> None:
        """T-EVP-PRI-02：write_route_token 默认优先级为 CRITICAL。"""
        self.assertEqual(
            WritePriority.CRITICAL,
            WRITE_METHOD_PRIORITY["write_route_token"],
        )

    def test_write_branch_manifest_is_critical(self) -> None:
        """T-EVP-PRI-03：write_branch_manifest 默认优先级为 CRITICAL。"""
        self.assertEqual(
            WritePriority.CRITICAL,
            WRITE_METHOD_PRIORITY["write_branch_manifest"],
        )

    def test_write_barrier_join_is_critical(self) -> None:
        """T-EVP-PRI-04：write_barrier_join 默认优先级为 CRITICAL。"""
        self.assertEqual(
            WritePriority.CRITICAL,
            WRITE_METHOD_PRIORITY["write_barrier_join"],
        )

    def test_write_llm_call_is_non_critical(self) -> None:
        """T-EVP-PRI-05：write_llm_call 默认优先级为 NON_CRITICAL。"""
        self.assertEqual(
            WritePriority.NON_CRITICAL,
            WRITE_METHOD_PRIORITY["write_llm_call"],
        )

    def test_write_render_event_is_non_critical(self) -> None:
        """T-EVP-PRI-06：write_render_event 默认优先级为 NON_CRITICAL。"""
        self.assertEqual(
            WritePriority.NON_CRITICAL,
            WRITE_METHOD_PRIORITY["write_render_event"],
        )

    def test_critical_value(self) -> None:
        """CRITICAL 枚举值为 'critical'。"""
        self.assertEqual("critical", WritePriority.CRITICAL.value)

    def test_non_critical_value(self) -> None:
        """NON_CRITICAL 枚举值为 'non_critical'。"""
        self.assertEqual("non_critical", WritePriority.NON_CRITICAL.value)


if __name__ == "__main__":
    unittest.main()
