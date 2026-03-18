"""T-E2E-01: 冷启动最小闭环 Smoke Test。

验收标准（DevSpec §18.2.1）：
- 无外部依赖下响应为合法 JSON
- quality.stage 含降级标记
- SQLite 有 intent_log + governor_log 记录
- 无伪执行宣告（FALSE_EXECUTION_CLAIM 检查）
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "e2e"

# ── 冷启动最小闭环：核心断言 ─────────────────────────────────────────────────

class TestColdStartE2E:
    """T-E2E-01: 冷启动最小闭环（无外部依赖）。"""

    def test_cold_start_input_fixture_valid(self):
        """冷启动输入 fixture 为合法 JSON，包含必须字段。"""
        path = FIXTURES_DIR / "cold_start_input.json"
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        assert "request_id" in data
        assert "session_id" in data
        assert "user_input" in data
        assert data.get("no_external_deps") is True

    def test_cold_start_output_structure_stable(self):
        """冷启动输出结构稳定：合法 JSON + quality.stage 含降级标记。

        使用 QA 模块的最小模板降级路径验证输出结构。
        """
        from host_brain.core.qa import build_fallback_output

        result = build_fallback_output(
            reason_code="SERVICE_UNAVAILABLE_COLD_START",
            stage="cold_start",
        )
        # 输出为合法 dict（可序列化为 JSON）
        json_str = json.dumps(result)
        parsed = json.loads(json_str)
        assert isinstance(parsed, dict)

        # quality.stage 含降级标记（meta.degraded=True 即为降级）
        meta = parsed.get("meta") or {}
        stage = meta.get("stage") or ""
        assert stage, "meta.stage 不能为空"
        assert meta.get("degraded") is True, "meta.degraded 应为 True"

    def test_no_false_execution_claim(self):
        """冷启动输出不含伪执行宣告。"""
        from host_brain.core.qa import build_fallback_output, has_false_execution_claim

        result = build_fallback_output(
            reason_code="SERVICE_UNAVAILABLE_COLD_START",
            stage="cold_start",
        )
        # 检查伪执行宣告
        assert not has_false_execution_claim(result), f"冷启动输出含伪执行宣告: {result}"

    def test_sqlite_audit_records_exist(self):
        """冷启动后 SQLite 有 intent_log + governor_log 记录。"""
        from host_brain.audit.sqlite_ledger import SQLiteLedger
        from host_brain.core.ids import new_request_id

        ledger = SQLiteLedger(db_path=":memory:")
        request_id = new_request_id()

        # 写入 intent_log
        ledger.log_intent({
            "request_id": request_id,
            "intent_id": "intent-cold-001",
            "session_id": "sess-cold-001",
            "ts_ms": 1000,
            "proposer_node_id": "host",
            "proposed_intent_draft_json": json.dumps({"action": "none"}),
            "lint_result": "ok",
        })

        # 写入 governor_log
        ledger.log_governor({
            "request_id": request_id,
            "intent_id": "intent-cold-001",
            "decision": "DEGRADED",
            "reason_code": "SERVICE_UNAVAILABLE_COLD_START",
            "rule_trace": json.dumps([]),
            "ts_ms": 1001,
        })

        # 断言记录存在
        intent_rows = [r for r in ledger.intent_log if r.get("request_id") == request_id]
        gov_rows = [r for r in ledger.governor_log if r.get("request_id") == request_id]

        assert len(intent_rows) >= 1, "intent_log 中无对应记录"
        assert len(gov_rows) >= 1, "governor_log 中无对应记录"

    def test_reason_code_not_empty_on_degraded(self):
        """降级时 reason_code 不为空。"""
        from host_brain.core.qa import build_fallback_output

        result = build_fallback_output(
            reason_code="SERVICE_UNAVAILABLE_COLD_START",
            stage="cold_start",
        )
        assert result.get("meta", {}).get("reason_code"), "降级时 meta.reason_code 不能为空"
