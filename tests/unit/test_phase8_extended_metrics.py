"""P8-1 扩展观测单元测试。

验收标准：
  3.1 subgraph_failure_metrics 可跨请求聚合，含失败码分组
  3.2 local_loop_failure_metrics 可跨请求聚合，含失败码分组
  3.3 intent_budget_breach_metrics 可跨请求聚合
  3.4 graph_latency_metrics 按三图 profile 分组，含 p50/p95
"""

from __future__ import annotations

import os
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_ledger(
    *,
    subgraph_failure_rows: list[dict] | None = None,
    graph_run_rows: list[dict] | None = None,
) -> MagicMock:
    """构造一个带 P8 属性的 mock ledger。"""
    ledger = MagicMock()
    ledger.p8_subgraph_failure_log = subgraph_failure_rows or []
    ledger.p8_graph_run_log = graph_run_rows or []
    return ledger


# ---------------------------------------------------------------------------
# 3.1 subgraph_failure_metrics
# ---------------------------------------------------------------------------

class TestSubgraphFailureMetrics:
    def test_empty_returns_zero(self):
        from host_brain.observability.p8_extended_metrics import P8ExtendedMetricsCollector
        collector = P8ExtendedMetricsCollector(ledger=_make_ledger())
        result = collector.collect_subgraph_failure_metrics()
        assert "subgraph_failure_count" in result
        assert "subgraph_failure_by_code" in result
        assert result["subgraph_failure_count"] == 0
        assert result["subgraph_failure_by_code"] == {}

    def test_counts_subgraph_category_only(self):
        from host_brain.observability.p8_extended_metrics import P8ExtendedMetricsCollector
        rows = [
            {"log_category": "subgraph", "error_code": "SUBGRAPH_FAILURE_TIMEOUT"},
            {"log_category": "subgraph", "error_code": "SUBGRAPH_FAILURE_TIMEOUT"},
            {"log_category": "subgraph", "error_code": "SUBGRAPH_FAILURE_INTERNAL"},
            # local_loop 行不应计入 subgraph_failure_metrics
            {"log_category": "local_loop", "error_code": "LOCAL_LOOP_TIMEOUT"},
        ]
        collector = P8ExtendedMetricsCollector(ledger=_make_ledger(subgraph_failure_rows=rows))
        result = collector.collect_subgraph_failure_metrics()
        assert result["subgraph_failure_count"] == 3
        assert result["subgraph_failure_by_code"]["SUBGRAPH_FAILURE_TIMEOUT"] == 2
        assert result["subgraph_failure_by_code"]["SUBGRAPH_FAILURE_INTERNAL"] == 1
        # local_loop 不应出现在 subgraph_failure_by_code
        assert "LOCAL_LOOP_TIMEOUT" not in result["subgraph_failure_by_code"]

    def test_local_loop_not_counted_in_subgraph(self):
        """归类互斥验证：LOCAL_LOOP_* 不计入 subgraph_failure_metrics。"""
        from host_brain.observability.p8_extended_metrics import P8ExtendedMetricsCollector
        rows = [
            {"log_category": "local_loop", "error_code": "LOCAL_LOOP_TIMEOUT"},
            {"log_category": "local_loop", "error_code": "LOCAL_LOOP_MAX_STEPS"},
            {"log_category": "local_loop", "error_code": "LOCAL_LOOP_INTERNAL_ERROR"},
        ]
        collector = P8ExtendedMetricsCollector(ledger=_make_ledger(subgraph_failure_rows=rows))
        result = collector.collect_subgraph_failure_metrics()
        assert result["subgraph_failure_count"] == 0
        assert result["subgraph_failure_by_code"] == {}


# ---------------------------------------------------------------------------
# 3.2 local_loop_failure_metrics
# ---------------------------------------------------------------------------

class TestLocalLoopFailureMetrics:
    def test_empty_returns_zero(self):
        from host_brain.observability.p8_extended_metrics import P8ExtendedMetricsCollector
        collector = P8ExtendedMetricsCollector(ledger=_make_ledger())
        result = collector.collect_local_loop_failure_metrics()
        assert "local_loop_failure_count" in result
        assert "local_loop_failure_by_code" in result
        assert result["local_loop_failure_count"] == 0

    def test_covers_p6_three_codes(self):
        """覆盖 P6 封板三码：LOCAL_LOOP_TIMEOUT / LOCAL_LOOP_MAX_STEPS / LOCAL_LOOP_INTERNAL_ERROR。"""
        from host_brain.observability.p8_extended_metrics import P8ExtendedMetricsCollector
        rows = [
            {"log_category": "local_loop", "error_code": "LOCAL_LOOP_TIMEOUT"},
            {"log_category": "local_loop", "error_code": "LOCAL_LOOP_MAX_STEPS"},
            {"log_category": "local_loop", "error_code": "LOCAL_LOOP_INTERNAL_ERROR"},
            {"log_category": "local_loop", "error_code": "LOCAL_LOOP_TIMEOUT"},
        ]
        collector = P8ExtendedMetricsCollector(ledger=_make_ledger(subgraph_failure_rows=rows))
        result = collector.collect_local_loop_failure_metrics()
        assert result["local_loop_failure_count"] == 4
        assert result["local_loop_failure_by_code"]["LOCAL_LOOP_TIMEOUT"] == 2
        assert result["local_loop_failure_by_code"]["LOCAL_LOOP_MAX_STEPS"] == 1
        assert result["local_loop_failure_by_code"]["LOCAL_LOOP_INTERNAL_ERROR"] == 1

    def test_subgraph_not_counted_in_local_loop(self):
        """归类互斥验证：SUBGRAPH_FAILURE_* 不计入 local_loop_failure_metrics。"""
        from host_brain.observability.p8_extended_metrics import P8ExtendedMetricsCollector
        rows = [
            {"log_category": "subgraph", "error_code": "SUBGRAPH_FAILURE_TIMEOUT"},
        ]
        collector = P8ExtendedMetricsCollector(ledger=_make_ledger(subgraph_failure_rows=rows))
        result = collector.collect_local_loop_failure_metrics()
        assert result["local_loop_failure_count"] == 0


# ---------------------------------------------------------------------------
# 3.3 intent_budget_breach_metrics
# ---------------------------------------------------------------------------

class TestIntentBudgetBreachMetrics:
    def test_empty_returns_zero(self):
        from host_brain.observability.p8_extended_metrics import P8ExtendedMetricsCollector
        collector = P8ExtendedMetricsCollector(ledger=_make_ledger())
        result = collector.collect_intent_budget_breach_metrics()
        assert "intent_budget_breach_count" in result
        assert "intent_budget_breach_by_code" in result
        assert result["intent_budget_breach_count"] == 0

    def test_counts_budget_breach_codes(self):
        from host_brain.observability.p8_extended_metrics import P8ExtendedMetricsCollector
        # 使用 P7 BUDGET_BREACH_FAILURE_CODES 中的码
        rows = [
            {"execution_profile": "INTENT_EXEC", "status": "FAILED",
             "budget_breach_code": "LOCAL_LOOP_TIMEOUT"},
            {"execution_profile": "INTENT_EXEC", "status": "FAILED",
             "budget_breach_code": "LOCAL_LOOP_MAX_STEPS"},
            {"execution_profile": "INTENT_EXEC", "status": "FAILED",
             "budget_breach_code": "LOCAL_LOOP_TIMEOUT"},
            # 无 budget breach 的行不应计入
            {"execution_profile": "INTENT_EXEC", "status": "OK",
             "budget_breach_code": None},
        ]
        collector = P8ExtendedMetricsCollector(ledger=_make_ledger(graph_run_rows=rows))
        result = collector.collect_intent_budget_breach_metrics()
        assert result["intent_budget_breach_count"] == 3
        assert result["intent_budget_breach_by_code"]["LOCAL_LOOP_TIMEOUT"] == 2
        assert result["intent_budget_breach_by_code"]["LOCAL_LOOP_MAX_STEPS"] == 1

    def test_p7_budget_breach_metrics_not_modified(self):
        """验证 P7 已有的 budget_breach_metrics 不被修改（独立上报）。"""
        from host_brain.observability.metrics import MetricsCollector
        ledger = _make_ledger()
        # 模拟 P7 已有的 supervisor_decision_log
        ledger.supervisor_decision_log = []
        ledger.governor_log = []
        ledger.retrieval_event_log = []
        ledger.intent_log = []
        ledger.skill_exec_log = []
        ledger.config_snapshot_log = []
        ledger.stream_events = []
        ledger.governor_decision_log = []
        collector = MetricsCollector(ledger=ledger)
        result = collector.collect()
        # P7 字段必须存在
        assert "budget_breach_metrics" in result
        # P8 字段在开关关闭时不存在
        assert "subgraph_failure_metrics" not in result


# ---------------------------------------------------------------------------
# 3.4 graph_latency_metrics
# ---------------------------------------------------------------------------

class TestGraphLatencyMetrics:
    def test_empty_returns_null_percentiles(self):
        from host_brain.observability.p8_extended_metrics import P8ExtendedMetricsCollector
        collector = P8ExtendedMetricsCollector(ledger=_make_ledger())
        result = collector.collect_graph_latency_metrics()
        assert "profiles" in result
        for profile in ("CHAT_FAST", "WORKFLOW_SUPERVISED", "INTENT_EXEC"):
            assert profile in result["profiles"]
            assert result["profiles"][profile]["p50_ms"] is None
            assert result["profiles"][profile]["p95_ms"] is None
            assert result["profiles"][profile]["sample_count"] == 0

    def test_sample_count_below_10_returns_null_percentiles(self):
        """样本量 < 10 时 p50/p95 置 null，sample_count 正常记录。"""
        from host_brain.observability.p8_extended_metrics import P8ExtendedMetricsCollector
        rows = [
            {"execution_profile": "CHAT_FAST", "latency_ms": 100 + i}
            for i in range(9)  # 9 条，不足 10
        ]
        collector = P8ExtendedMetricsCollector(ledger=_make_ledger(graph_run_rows=rows))
        result = collector.collect_graph_latency_metrics()
        chat_fast = result["profiles"]["CHAT_FAST"]
        assert chat_fast["sample_count"] == 9
        assert chat_fast["p50_ms"] is None
        assert chat_fast["p95_ms"] is None

    def test_sample_count_10_computes_percentiles(self):
        """样本量 ≥ 10 时正常计算 p50/p95。"""
        from host_brain.observability.p8_extended_metrics import P8ExtendedMetricsCollector
        rows = [
            {"execution_profile": "WORKFLOW_SUPERVISED", "latency_ms": (i + 1) * 10}
            for i in range(10)  # 10, 20, ..., 100
        ]
        collector = P8ExtendedMetricsCollector(ledger=_make_ledger(graph_run_rows=rows))
        result = collector.collect_graph_latency_metrics()
        ws = result["profiles"]["WORKFLOW_SUPERVISED"]
        assert ws["sample_count"] == 10
        assert ws["p50_ms"] is not None
        assert ws["p95_ms"] is not None

    def test_profiles_are_independent(self):
        """三个 profile 独立维护样本序列，互不干扰。"""
        from host_brain.observability.p8_extended_metrics import P8ExtendedMetricsCollector
        rows = (
            [{"execution_profile": "CHAT_FAST", "latency_ms": 50 + i} for i in range(10)]
            + [{"execution_profile": "INTENT_EXEC", "latency_ms": 200 + i} for i in range(5)]
        )
        collector = P8ExtendedMetricsCollector(ledger=_make_ledger(graph_run_rows=rows))
        result = collector.collect_graph_latency_metrics()
        assert result["profiles"]["CHAT_FAST"]["sample_count"] == 10
        assert result["profiles"]["CHAT_FAST"]["p50_ms"] is not None
        assert result["profiles"]["INTENT_EXEC"]["sample_count"] == 5
        assert result["profiles"]["INTENT_EXEC"]["p50_ms"] is None  # 不足 10
        assert result["profiles"]["WORKFLOW_SUPERVISED"]["sample_count"] == 0


# ---------------------------------------------------------------------------
# P8 开关行为
# ---------------------------------------------------------------------------

class TestP8Switch:
    def test_switch_off_no_p8_fields(self):
        """开关关闭时，collect() 不追加 P8 字段，行为与 P7 完全一致。"""
        from host_brain.observability.metrics import MetricsCollector
        ledger = _make_ledger()
        ledger.supervisor_decision_log = []
        ledger.governor_log = []
        ledger.retrieval_event_log = []
        ledger.intent_log = []
        ledger.skill_exec_log = []
        ledger.config_snapshot_log = []
        ledger.stream_events = []
        ledger.governor_decision_log = []
        with patch.dict(os.environ, {"P8_EXTENDED_METRICS_ENABLED": "false"}):
            collector = MetricsCollector(ledger=ledger)
            result = collector.collect()
        for key in ("subgraph_failure_metrics", "local_loop_failure_metrics",
                    "intent_budget_breach_metrics", "graph_latency_metrics"):
            assert key not in result, f"P8 字段 {key!r} 不应在开关关闭时出现"

    def test_switch_on_adds_p8_fields(self):
        """开关开启时，collect() 追加四个 P8 字段。"""
        from host_brain.observability.metrics import MetricsCollector
        ledger = _make_ledger()
        ledger.supervisor_decision_log = []
        ledger.governor_log = []
        ledger.retrieval_event_log = []
        ledger.intent_log = []
        ledger.skill_exec_log = []
        ledger.config_snapshot_log = []
        ledger.stream_events = []
        ledger.governor_decision_log = []
        with patch.dict(os.environ, {"P8_EXTENDED_METRICS_ENABLED": "true"}):
            collector = MetricsCollector(ledger=ledger)
            result = collector.collect()
        for key in ("subgraph_failure_metrics", "local_loop_failure_metrics",
                    "intent_budget_breach_metrics", "graph_latency_metrics"):
            assert key in result, f"P8 字段 {key!r} 应在开关开启时出现"
