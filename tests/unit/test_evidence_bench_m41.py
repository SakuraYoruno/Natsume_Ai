"""T-EVP-BENCH-01~04：EvidenceBench 写入延迟基准测量工具测试（M41-A）。"""

from __future__ import annotations

import unittest

from host_brain.integrations.evidence_bench import EvidenceBench
from host_brain.integrations.graph_evidence import EvidencePlane


class TestEvidenceBenchM41(unittest.TestCase):
    """T-EVP-BENCH-01~04：验证 EvidenceBench.measure_write_latency 返回正确统计字典。"""

    def setUp(self) -> None:
        self.bench = EvidenceBench()
        self.ep = EvidencePlane(db_path=":memory:")

    def _assert_latency_dict(self, result: dict) -> None:
        """断言延迟统计字典包含所有必要字段且值合理。"""
        for key in ("p50", "p95", "p99", "mean", "max"):
            self.assertIn(key, result, f"缺少字段: {key}")
            self.assertIsInstance(result[key], float, f"{key} 应为 float")
            self.assertGreaterEqual(result[key], 0.0, f"{key} 应 >= 0")
        # p50 <= p95 <= p99 <= max
        self.assertLessEqual(result["p50"], result["p95"])
        self.assertLessEqual(result["p95"], result["p99"])
        self.assertLessEqual(result["p99"], result["max"])

    def test_bench_write_route_token(self) -> None:
        """T-EVP-BENCH-01：measure_write_latency 对 write_trace 返回包含 p50/p95/p99 的字典。"""
        # write_route_token 尚未实现，用 write_trace 作为 CRITICAL 代表方法
        result = self.bench.measure_write_latency(
            self.ep.write_trace,
            n=50,
            request_id="bench-r1",
            graph_id="g1",
            trace=[{"node_id": "n1", "status": "OK", "ts_ms": 1000}],
        )
        self._assert_latency_dict(result)

    def test_bench_write_branch_manifest(self) -> None:
        """T-EVP-BENCH-02：measure_write_latency 对 write_branch_manifest 返回包含 p50/p95/p99 的字典。"""
        result = self.bench.measure_write_latency(
            self.ep.write_branch_manifest,
            n=50,
            request_id="bench-r2",
            graph_id="g1",
            manifest={
                "total_count": 2,
                "success_count": 2,
                "fail_count": 0,
                "committed_at_ms": 1000,
                "branches": [],
            },
        )
        self._assert_latency_dict(result)

    def test_bench_write_barrier_join(self) -> None:
        """T-EVP-BENCH-03：measure_write_latency 对 write_barrier_join 返回包含 p50/p95/p99 的字典。"""
        result = self.bench.measure_write_latency(
            self.ep.write_barrier_join,
            n=50,
            request_id="bench-r3",
            graph_id="g1",
            barrier_join_result={
                "passed": True,
                "failed_branches": [],
                "join_policy_ref": "ref1",
                "reason": "ok",
            },
        )
        self._assert_latency_dict(result)

    def test_bench_write_render_event(self) -> None:
        """T-EVP-BENCH-04：measure_write_latency 对 write_stream_events 返回包含 p50/p95/p99 的字典。"""
        # write_render_event 对应 write_stream_events（Render 阶段）
        result = self.bench.measure_write_latency(
            self.ep.write_stream_events,
            n=50,
            request_id="bench-r4",
            graph_id="g1",
            events=[{"phase": "render", "event_type": "chunk", "node_id": "n1", "chunk": "hello", "ts_ms": 1000}],
        )
        self._assert_latency_dict(result)

    def test_bench_n_zero_raises(self) -> None:
        """n=0 时应抛出 ValueError。"""
        with self.assertRaises(ValueError):
            self.bench.measure_write_latency(lambda: None, n=0)


if __name__ == "__main__":
    unittest.main()
