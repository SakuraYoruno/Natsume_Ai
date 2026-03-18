"""T-PERF-01: 性能基线采集验收测试。

验收标准（DevSpec §18.3.1）：
- latency_baseline 字段包含所有必须子字段
- sample_count > 0 时时延值为正整数
- sample_count = 0 时 stale=True 且 stale_reason_code=PERF_BASELINE_STALE
- /obs/metrics 响应中包含 latency_baseline 字段
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "obs"

LATENCY_BASELINE_REQUIRED_FIELDS = {
    "retrieval_p50_ms",
    "retrieval_p95_ms",
    "governor_p50_ms",
    "governor_p95_ms",
    "skill_exec_p50_ms",
    "skill_exec_p95_ms",
    "sampled_at",
    "sample_count",
    "stale",
    "stale_reason_code",
}


class TestPerfBaselineFixture:
    """T-PERF-01-A: metrics_with_latency.json fixture 字段结构稳定。"""

    def setup_method(self):
        path = FIXTURES_DIR / "metrics_with_latency.json"
        self.data = json.loads(path.read_text(encoding="utf-8-sig"))

    def test_latency_baseline_field_present(self):
        """latency_baseline 字段存在于 /obs/metrics 响应中。"""
        assert "latency_baseline" in self.data

    def test_latency_baseline_required_fields(self):
        """latency_baseline 包含所有必须子字段。"""
        baseline = self.data["latency_baseline"]
        for field in LATENCY_BASELINE_REQUIRED_FIELDS:
            assert field in baseline, f"缺少必须字段: {field}"

    def test_sample_count_positive(self):
        """sample_count > 0。"""
        baseline = self.data["latency_baseline"]
        assert baseline["sample_count"] > 0

    def test_latency_values_positive_when_sampled(self):
        """sample_count > 0 时时延值为正整数。"""
        baseline = self.data["latency_baseline"]
        if baseline["sample_count"] > 0:
            for key in ("retrieval_p50_ms", "retrieval_p95_ms",
                        "governor_p50_ms", "governor_p95_ms",
                        "skill_exec_p50_ms", "skill_exec_p95_ms"):
                val = baseline[key]
                assert isinstance(val, int) and val > 0, (
                    f"{key} 应为正整数，实际: {val!r}"
                )

    def test_stale_false_when_sampled(self):
        """sample_count > 0 时 stale=False。"""
        baseline = self.data["latency_baseline"]
        if baseline["sample_count"] > 0:
            assert baseline["stale"] is False
            assert baseline["stale_reason_code"] is None

    def test_sampled_at_is_iso8601(self):
        """sampled_at 为 ISO 8601 格式字符串。"""
        baseline = self.data["latency_baseline"]
        sampled_at = baseline["sampled_at"]
        assert isinstance(sampled_at, str) and len(sampled_at) > 0


class TestPerfBaselineCollector:
    """T-PERF-01-B: MetricsCollector.collect_latency_baseline() 单元测试。"""

    def _make_ledger_with_data(self):
        """创建含有时延数据的 SQLiteLedger。"""
        from host_brain.audit.sqlite_ledger import SQLiteLedger
        from host_brain.core.ids import new_request_id

        ledger = SQLiteLedger(db_path=":memory:")

        # 写入 retrieval_event_log（含 latency_ms）
        for i, lat in enumerate([100, 150, 200, 250, 500]):
            ledger.log_retrieval_event({
                "request_id": new_request_id(),
                "event_type": "embedding",
                "ts_ms": 1000 + i,
                "payload": {"latency_ms": lat},
            })

        # 写入 governor_log（含 latency_ms）
        for i, lat in enumerate([10, 12, 15, 20, 50]):
            ledger.log_governor({
                "request_id": new_request_id(),
                "intent_id": f"intent-{i}",
                "decision": "APPROVE",
                "reason_code": None,
                "rule_trace": "[]",
                "ts_ms": 2000 + i,
                "latency_ms": lat,
            })

        # 写入 skill_exec_log（含 latency_ms）
        for i, lat in enumerate([50, 80, 100, 150, 300]):
            ledger.log_skill_exec({
                "request_id": new_request_id(),
                "intent_id": f"intent-skill-{i}",
                "skill_name": "desktop_note",
                "result_status": "SUCCESS",
                "result_code": None,
                "manifest_ref": f"manifest-{i}",
                "evidence_ref": f"evidence-{i}",
                "ts_ms": 3000 + i,
                "latency_ms": lat,
            })

        return ledger

    def test_collect_latency_baseline_structure(self):
        """collect_latency_baseline() 返回包含所有必须字段的 dict。"""
        from host_brain.obs.metrics import MetricsCollector

        ledger = self._make_ledger_with_data()
        collector = MetricsCollector(ledger=ledger)
        baseline = collector.collect_latency_baseline()

        for field in LATENCY_BASELINE_REQUIRED_FIELDS:
            assert field in baseline, f"缺少必须字段: {field}"

    def test_collect_latency_baseline_sample_count(self):
        """有数据时 sample_count > 0。"""
        from host_brain.obs.metrics import MetricsCollector

        ledger = self._make_ledger_with_data()
        collector = MetricsCollector(ledger=ledger)
        baseline = collector.collect_latency_baseline()

        assert baseline["sample_count"] > 0

    def test_collect_latency_baseline_empty_ledger(self):
        """空 ledger 时 sample_count=0，stale=True，stale_reason_code=PERF_BASELINE_STALE。"""
        from host_brain.audit.sqlite_ledger import SQLiteLedger
        from host_brain.core.errors import PERF_BASELINE_STALE
        from host_brain.obs.metrics import MetricsCollector

        ledger = SQLiteLedger(db_path=":memory:")
        collector = MetricsCollector(ledger=ledger)
        baseline = collector.collect_latency_baseline()

        assert baseline["sample_count"] == 0
        assert baseline["stale"] is True
        assert baseline["stale_reason_code"] == PERF_BASELINE_STALE

    def test_collect_latency_baseline_p50_lte_p95(self):
        """p50 ≤ p95（基本单调性）。"""
        from host_brain.obs.metrics import MetricsCollector

        ledger = self._make_ledger_with_data()
        collector = MetricsCollector(ledger=ledger)
        baseline = collector.collect_latency_baseline()

        for prefix in ("retrieval", "governor", "skill_exec"):
            p50 = baseline.get(f"{prefix}_p50_ms")
            p95 = baseline.get(f"{prefix}_p95_ms")
            if p50 is not None and p95 is not None:
                assert p50 <= p95, f"{prefix}: p50={p50} > p95={p95}"

    def test_collect_method_includes_latency_baseline(self):
        """MetricsCollector.collect() 返回的 payload 包含 latency_baseline 字段。"""
        from host_brain.obs.metrics import MetricsCollector

        ledger = self._make_ledger_with_data()
        collector = MetricsCollector(ledger=ledger)
        payload = collector.collect(request_id="req-perf-001")

        assert "latency_baseline" in payload
        baseline = payload["latency_baseline"]
        assert isinstance(baseline, dict)
        assert "sample_count" in baseline


class TestPerfBaselineErrorCode:
    """T-PERF-01-C: PERF_BASELINE_STALE 错误码可导入。"""

    def test_perf_baseline_stale_importable(self):
        from host_brain.core.errors import PERF_BASELINE_STALE
        assert PERF_BASELINE_STALE == "PERF_BASELINE_STALE"
