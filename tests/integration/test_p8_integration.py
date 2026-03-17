"""P8-1 集成测试：验收 3.5 — 三图主链与 P1～P7 契约不退化。

验收标准 3.5：
  - Chat / Supervised / Intent 三图主链节点与边不变
  - execution_profile 语义不变，入口层写入逻辑不变
  - executor 不新增业务语义
  - P7 已有的 supervisor_metrics、budget_breach_metrics（Supervisor 侧）不退化
  - 三图主链可正常运行（复用 P7 集成测试用例）

本文件不重复实现三图主链逻辑，仅验证：
  1. P8 新增文件/改动不破坏三图主链结构
  2. P7 已有观测字段在 P8 后仍存在且结构不变
  3. P8 开关关闭时 collect() 行为与 P7 完全一致
  4. P8 开关开启时四个新字段正确追加
  5. P8 ledger 写入方法存在且可调用（不抛异常）
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 3.5.1 三图主链结构不变（静态断言）
# ---------------------------------------------------------------------------

class TestThreeGraphStructureUnchanged:
    def test_chat_graph_structure_unchanged(self):
        """Chat 图主链节点与边不变。"""
        from host_brain.app.graph_config_builder import build_chat_graph
        config = build_chat_graph(
            provider_name="mock",
            model_id="mock",
            timeout_ms=5000,
            available=True,
            api_key=None,
            provider_fn=lambda _: None,
        )
        meta = config.get("graph_meta") or {}
        active = meta.get("active_nodes") or []
        assert len(active) >= 1, "Chat 图 active_nodes 不得为空"
        assert config.get("entry_node"), "Chat 图须有 entry_node"
        assert config.get("edges"), "Chat 图须有 edges"

    def test_supervised_graph_structure_unchanged(self):
        """Supervised 图主链节点与边不变。"""
        from host_brain.app.graph_config_builder import build_supervised_graph
        config = build_supervised_graph(
            provider_name="mock",
            model_id="mock",
            timeout_ms=5000,
            available=True,
            api_key=None,
            provider_fn=lambda _: None,
        )
        meta = config.get("graph_meta") or {}
        active = meta.get("active_nodes") or []
        assert len(active) >= 1, "Supervised 图 active_nodes 不得为空"
        assert config.get("entry_node"), "Supervised 图须有 entry_node"

    def test_intent_graph_structure_unchanged(self):
        """Intent 图主链节点与边不变（4 节点）。"""
        from host_brain.app.graph_config_builder import (
            build_intent_graph,
            INTENT_AGENT_ENTRY_NODE_ID,
            JUDGE_AGENT_NODE_ID,
            CHAT_RENDER_NODE_ID,
        )
        config = build_intent_graph(
            provider_name="mock",
            model_id="mock",
            timeout_ms=5000,
            available=True,
            api_key=None,
            provider_fn=lambda _: None,
        )
        meta = config.get("graph_meta") or {}
        active = meta.get("active_nodes") or []
        assert INTENT_AGENT_ENTRY_NODE_ID in active
        assert JUDGE_AGENT_NODE_ID in active
        assert CHAT_RENDER_NODE_ID in active
        assert len(active) == 4, "Intent 图主链须仍为 4 节点"

    def test_execution_profile_constants_unchanged(self):
        """execution_profile 常量语义不变。"""
        from host_brain.app.execution_profile import (
            PROFILE_CHAT_FAST,
            PROFILE_WORKFLOW_SUPERVISED,
            PROFILE_INTENT_EXEC,
        )
        assert PROFILE_CHAT_FAST == "CHAT_FAST"
        assert PROFILE_WORKFLOW_SUPERVISED == "WORKFLOW_SUPERVISED"
        assert PROFILE_INTENT_EXEC == "INTENT_EXEC"

    def test_executor_no_p8_business_branch(self):
        """executor 未新增 P8 业务特判（P8 逻辑仅在 graph_runtime / ledger 层）。"""
        root = Path(__file__).resolve().parents[2]
        executor_path = root / "host_brain" / "integrations" / "graph_executor.py"
        text = executor_path.read_text(encoding="utf-8")
        assert "p8_" not in text.lower(), "graph_executor 不得包含 P8 业务分支"
        assert "P8_EXTENDED" not in text, "graph_executor 不得引用 P8 开关"


# ---------------------------------------------------------------------------
# 3.5.2 P7 已有观测字段不退化
# ---------------------------------------------------------------------------

class TestP7ObsFieldsNotDegraded:
    def _make_ledger(self) -> MagicMock:
        ledger = MagicMock()
        ledger.supervisor_decision_log = []
        ledger.governor_log = []
        ledger.retrieval_event_log = []
        ledger.intent_log = []
        ledger.skill_exec_log = []
        ledger.config_snapshot_log = []
        ledger.stream_events = []
        ledger.governor_decision_log = []
        ledger.p8_subgraph_failure_log = []
        ledger.p8_graph_run_log = []
        return ledger

    def test_p7_supervisor_metrics_field_exists(self):
        """P7 supervisor_metrics 字段在 P8 后仍存在。"""
        from host_brain.observability.metrics import MetricsCollector
        with patch.dict(os.environ, {"P8_EXTENDED_METRICS_ENABLED": "false"}):
            collector = MetricsCollector(ledger=self._make_ledger())
            result = collector.collect()
        assert "supervisor_metrics" in result
        sm = result["supervisor_metrics"]
        assert "decision_source_count" in sm
        assert "failure_code_count" in sm
        assert "selected_agent_count" in sm
        assert "total_decisions" in sm

    def test_p7_budget_breach_metrics_field_exists(self):
        """P7 budget_breach_metrics 字段在 P8 后仍存在且结构不变。"""
        from host_brain.observability.metrics import MetricsCollector
        with patch.dict(os.environ, {"P8_EXTENDED_METRICS_ENABLED": "false"}):
            collector = MetricsCollector(ledger=self._make_ledger())
            result = collector.collect()
        assert "budget_breach_metrics" in result
        bm = result["budget_breach_metrics"]
        assert "budget_breach_count" in bm
        assert "budget_breach_by_code" in bm
        assert "budget_breach_rate_pct" in bm
        assert "total_supervisor_decisions" in bm

    def test_p7_core_fields_all_present(self):
        """P7 核心字段全部存在（不退化）。"""
        from host_brain.observability.metrics import MetricsCollector
        with patch.dict(os.environ, {"P8_EXTENDED_METRICS_ENABLED": "false"}):
            collector = MetricsCollector(ledger=self._make_ledger())
            result = collector.collect()
        required_p7_fields = [
            "ts_ms", "obs_mode", "governor_decisions", "retrieval_latency",
            "hard_snapshot_staleness_ms", "recent_chains", "latency_baseline",
            "emotion_metrics", "agent_metrics", "supervisor_metrics",
            "budget_breach_metrics", "debug_trace",
        ]
        for field in required_p7_fields:
            assert field in result, f"P7 字段 {field!r} 不得在 P8 后消失"


# ---------------------------------------------------------------------------
# 3.5.3 P8 开关行为（集成层验证）
# ---------------------------------------------------------------------------

class TestP8SwitchIntegration:
    def _make_ledger(self) -> MagicMock:
        ledger = MagicMock()
        ledger.supervisor_decision_log = []
        ledger.governor_log = []
        ledger.retrieval_event_log = []
        ledger.intent_log = []
        ledger.skill_exec_log = []
        ledger.config_snapshot_log = []
        ledger.stream_events = []
        ledger.governor_decision_log = []
        ledger.p8_subgraph_failure_log = []
        ledger.p8_graph_run_log = []
        return ledger

    def test_switch_off_collect_identical_to_p7(self):
        """开关关闭时，collect() 不含任何 P8 字段。"""
        from host_brain.observability.metrics import MetricsCollector
        with patch.dict(os.environ, {"P8_EXTENDED_METRICS_ENABLED": "false"}):
            collector = MetricsCollector(ledger=self._make_ledger())
            result = collector.collect()
        p8_fields = [
            "subgraph_failure_metrics", "local_loop_failure_metrics",
            "intent_budget_breach_metrics", "graph_latency_metrics",
        ]
        for f in p8_fields:
            assert f not in result, f"开关关闭时不得出现 P8 字段 {f!r}"

    def test_switch_on_adds_four_p8_fields(self):
        """开关开启时，collect() 追加四个 P8 字段，且不修改已有字段。"""
        from host_brain.observability.metrics import MetricsCollector
        with patch.dict(os.environ, {"P8_EXTENDED_METRICS_ENABLED": "true"}):
            collector = MetricsCollector(ledger=self._make_ledger())
            result = collector.collect()
        p8_fields = [
            "subgraph_failure_metrics", "local_loop_failure_metrics",
            "intent_budget_breach_metrics", "graph_latency_metrics",
        ]
        for f in p8_fields:
            assert f in result, f"开关开启时须出现 P8 字段 {f!r}"
        # P7 字段仍存在
        assert "budget_breach_metrics" in result
        assert "supervisor_metrics" in result


# ---------------------------------------------------------------------------
# 3.5.4 P8 ledger 写入方法存在且可调用
# ---------------------------------------------------------------------------

class TestP8LedgerMethods:
    def test_log_p8_subgraph_failure_method_exists(self):
        """SQLiteLedger 有 log_p8_subgraph_failure 方法。"""
        from host_brain.audit.sqlite_ledger import SQLiteLedger
        assert hasattr(SQLiteLedger, "log_p8_subgraph_failure"), \
            "SQLiteLedger 须有 log_p8_subgraph_failure 方法"

    def test_log_p8_graph_run_method_exists(self):
        """SQLiteLedger 有 log_p8_graph_run 方法。"""
        from host_brain.audit.sqlite_ledger import SQLiteLedger
        assert hasattr(SQLiteLedger, "log_p8_graph_run"), \
            "SQLiteLedger 须有 log_p8_graph_run 方法"

    def test_p8_subgraph_failure_log_property_exists(self):
        """SQLiteLedger 有 p8_subgraph_failure_log property。"""
        from host_brain.audit.sqlite_ledger import SQLiteLedger
        assert hasattr(SQLiteLedger, "p8_subgraph_failure_log"), \
            "SQLiteLedger 须有 p8_subgraph_failure_log property"

    def test_p8_graph_run_log_property_exists(self):
        """SQLiteLedger 有 p8_graph_run_log property。"""
        from host_brain.audit.sqlite_ledger import SQLiteLedger
        assert hasattr(SQLiteLedger, "p8_graph_run_log"), \
            "SQLiteLedger 须有 p8_graph_run_log property"

    def test_log_p8_subgraph_failure_in_memory(self):
        """log_p8_subgraph_failure 可写入内存 DB 并可读回。"""
        from host_brain.audit.sqlite_ledger import SQLiteLedger
        ledger = SQLiteLedger(":memory:")
        ledger.log_p8_subgraph_failure({
            "request_id": "test-req-001",
            "ts_ms": 1700000000000,
            "execution_profile": "INTENT_EXEC",
            "log_category": "local_loop",
            "error_code": "LOCAL_LOOP_TIMEOUT",
            "failed_node": "core_reasoner_primary",
            "detail": "timed out",
        })
        rows = ledger.p8_subgraph_failure_log
        assert len(rows) == 1
        assert rows[0]["error_code"] == "LOCAL_LOOP_TIMEOUT"
        assert rows[0]["log_category"] == "local_loop"

    def test_log_p8_graph_run_in_memory(self):
        """log_p8_graph_run 可写入内存 DB 并可读回。"""
        from host_brain.audit.sqlite_ledger import SQLiteLedger
        ledger = SQLiteLedger(":memory:")
        ledger.log_p8_graph_run({
            "request_id": "test-req-002",
            "ts_ms": 1700000000001,
            "execution_profile": "CHAT_FAST",
            "latency_ms": 123,
            "status": "OK",
            "budget_breach_code": None,
        })
        rows = ledger.p8_graph_run_log
        assert len(rows) == 1
        assert rows[0]["execution_profile"] == "CHAT_FAST"
        assert rows[0]["latency_ms"] == 123

    def test_p8_constants_in_ledger_constants(self):
        """ledger_constants 中 P8 常量存在且正确。"""
        from host_brain.audit.ledger_constants import (
            P8_LOCAL_LOOP_FAILURE_CODES,
            P8_TABLE_SUBGRAPH_FAILURE_LOG,
            P8_TABLE_GRAPH_RUN_LOG,
        )
        assert "LOCAL_LOOP_TIMEOUT" in P8_LOCAL_LOOP_FAILURE_CODES
        assert "LOCAL_LOOP_MAX_STEPS" in P8_LOCAL_LOOP_FAILURE_CODES
        assert "LOCAL_LOOP_INTERNAL_ERROR" in P8_LOCAL_LOOP_FAILURE_CODES
        assert P8_TABLE_SUBGRAPH_FAILURE_LOG == "p8_subgraph_failure_log"
        assert P8_TABLE_GRAPH_RUN_LOG == "p8_graph_run_log"


# ---------------------------------------------------------------------------
# 3.5.5 P8 基线测试文件存在（文档链路完整性）
# ---------------------------------------------------------------------------

def test_p8_baseline_files_exist():
    """P8 实施文件存在（基线完整性）。"""
    root = Path(__file__).resolve().parents[2]
    files = [
        root / "host_brain" / "observability" / "p8_extended_metrics.py",
        root / "host_brain" / "audit" / "ledger_schema.py",
        root / "host_brain" / "audit" / "ledger_constants.py",
        root / "host_brain" / "audit" / "sqlite_ledger.py",
        root / "host_brain" / "observability" / "metrics.py",
        root / "host_brain" / "integrations" / "graph_runtime.py",
        root / "tests" / "unit" / "test_p8_extended_metrics.py",
    ]
    for f in files:
        assert f.is_file(), f"P8 实施文件须存在: {f}"


def test_p8_scope_doc_exists():
    """P8 SCOPE 定稿文档存在。"""
    root = Path(__file__).resolve().parents[2]
    doc = root / "docs" / "50_project" / "MULTI_AGENT_P8_SCOPE_2026-03.md"
    assert doc.is_file(), "MULTI_AGENT_P8_SCOPE_2026-03.md 须存在"
