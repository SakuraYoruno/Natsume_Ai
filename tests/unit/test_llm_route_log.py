"""T-AUDIT-LLM-01~05：SQLiteLedger.log_llm_route() 正式方法测试（TASK-0217 M25-A）。"""

from __future__ import annotations

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.ids import now_ms


# ---------------------------------------------------------------------------
# 辅助工厂
# ---------------------------------------------------------------------------

def _make_ledger() -> SQLiteLedger:
    return SQLiteLedger(db_path=":memory:", check_same_thread=False)


# ---------------------------------------------------------------------------
# T-AUDIT-LLM-01：log_llm_route() 写入成功，llm_route_log 表有记录
# ---------------------------------------------------------------------------

class TestLogLlmRouteWrite:
    """T-AUDIT-LLM-01：log_llm_route() 写入成功，llm_route_log 表有记录。"""

    def test_write_and_read_back(self) -> None:
        ledger = _make_ledger()
        ts = now_ms()
        ledger.log_llm_route({
            "request_id": "req-001",
            "model_id": "gpt-4o",
            "reason": "primary_model_ok",
            "latency_ms": 120,
            "fallback_used": False,
            "ts_ms": ts,
        })
        rows = ledger.query_llm_route_log()
        assert len(rows) == 1
        row = rows[0]
        assert row["request_id"] == "req-001"
        assert row["model_id"] == "gpt-4o"
        assert row["reason"] == "primary_model_ok"
        assert row["latency_ms"] == 120
        assert row["fallback_used"] is False
        assert row["ts_ms"] == ts


# ---------------------------------------------------------------------------
# T-AUDIT-LLM-02：fallback_used=True 时记录正确
# ---------------------------------------------------------------------------

class TestLogLlmRouteFallback:
    """T-AUDIT-LLM-02：fallback_used=True 时记录正确。"""

    def test_fallback_used_true(self) -> None:
        ledger = _make_ledger()
        ledger.log_llm_route({
            "request_id": "req-002",
            "model_id": "deepseek-v4-flash",
            "reason": "fallback_1_ok (primary=gpt-4o failed: SERVICE_UNAVAILABLE_DEPENDENCY)",
            "latency_ms": 350,
            "fallback_used": True,
            "ts_ms": now_ms(),
        })
        rows = ledger.query_llm_route_log()
        assert len(rows) == 1
        assert rows[0]["fallback_used"] is True
        assert rows[0]["model_id"] == "deepseek-v4-flash"

    def test_fallback_used_false(self) -> None:
        ledger = _make_ledger()
        ledger.log_llm_route({
            "request_id": "req-002b",
            "model_id": "gpt-4o",
            "reason": "primary_model_ok",
            "latency_ms": 80,
            "fallback_used": False,
            "ts_ms": now_ms(),
        })
        rows = ledger.query_llm_route_log()
        assert rows[0]["fallback_used"] is False


# ---------------------------------------------------------------------------
# T-AUDIT-LLM-03：迁移脚本幂等（重复执行不报错）
# ---------------------------------------------------------------------------

class TestMigrationIdempotent:
    """T-AUDIT-LLM-03：迁移脚本幂等（重复执行不报错）。"""

    def test_migration_idempotent(self) -> None:
        import sqlite3
        from pathlib import Path

        migration_path = (
            Path(__file__).parent.parent.parent
            / "host_brain" / "audit" / "migrations" / "0039_llm_route_log.sql"
        )
        assert migration_path.exists(), f"迁移脚本不存在: {migration_path}"

        sql = migration_path.read_text(encoding="utf-8-sig")
        conn = sqlite3.connect(":memory:")
        # 执行两次，验证幂等
        conn.executescript(sql)
        conn.executescript(sql)  # 不应抛出异常
        conn.close()


# ---------------------------------------------------------------------------
# T-AUDIT-LLM-04：时间窗口查询返回正确记录数
# ---------------------------------------------------------------------------

class TestQueryLlmRouteLogTimeWindow:
    """T-AUDIT-LLM-04：时间窗口查询返回正确记录数。"""

    def test_time_window_query(self) -> None:
        ledger = _make_ledger()
        base_ts = 1_700_000_000_000  # 固定基准时间戳

        # 写入 5 条记录，时间间隔 1000ms
        for i in range(5):
            ledger.log_llm_route({
                "request_id": f"req-{i:03d}",
                "model_id": "gpt-4o",
                "reason": "primary_model_ok",
                "latency_ms": 100 + i * 10,
                "fallback_used": False,
                "ts_ms": base_ts + i * 1000,
            })

        # 查询全部
        all_rows = ledger.query_llm_route_log()
        assert len(all_rows) == 5

        # 查询时间窗口 [base_ts+1000, base_ts+3000]（应返回 3 条）
        windowed = ledger.query_llm_route_log(
            start_ms=base_ts + 1000,
            end_ms=base_ts + 3000,
        )
        assert len(windowed) == 3
        assert windowed[0]["request_id"] == "req-001"
        assert windowed[-1]["request_id"] == "req-003"

    def test_limit_respected(self) -> None:
        ledger = _make_ledger()
        base_ts = 1_700_000_000_000
        for i in range(10):
            ledger.log_llm_route({
                "request_id": f"req-lim-{i:03d}",
                "model_id": "gpt-4o",
                "reason": "primary_model_ok",
                "latency_ms": 50,
                "fallback_used": False,
                "ts_ms": base_ts + i * 100,
            })
        rows = ledger.query_llm_route_log(limit=3)
        assert len(rows) == 3
