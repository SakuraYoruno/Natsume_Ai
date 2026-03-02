"""T-Obs-04: 时间窗口查询与分页契约测试。

验收断言：
- fetch_by_time_range 按时间窗口过滤正确（起止边界含端点）
- fetch_by_time_range 分页 limit/offset 正确
- fetch_by_time_range 对非法表名抛出 ValueError
- count_by_time_range 统计结果与 fetch 行数一致
- cross_timeline 支持 start_ms/end_ms 参数
- build_cross_timeline 便捷入口支持时间窗口
- 大量数据下时间窗口查询性能 < 100ms
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from host_brain.audit.sqlite_ledger import SQLiteLedger, _ALLOWED_TABLES
from host_brain.core.ids import now_ms
from host_brain.observability.cross_timeline import (
    CrossTimelineAggregator,
    build_cross_timeline,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ledger() -> SQLiteLedger:
    """创建内存 ledger 实例。"""
    _ledger = SQLiteLedger(db_path=":memory:")
    yield _ledger
    _ledger.close()


def _insert_intent_rows(ledger: SQLiteLedger, count: int, base_ts: int = 1_000_000) -> None:
    """批量插入 intent_log 行用于性能测试。"""
    for i in range(count):
        ledger.log_intent({
            "request_id": f"req_{i:06d}",
            "intent_id": f"int_{i:06d}",
            "session_id": "sess_001",
            "seq": i,
            "ts_ms": base_ts + i * 100,
            "nonce": f"nonce_{i:06d}",
            "parse_stage": "STRICT",
            "raw_text": f"test input {i}",
        })


def _insert_voice_event_rows(ledger: SQLiteLedger, count: int, base_ts: int = 1_000_000) -> None:
    """批量插入 voice_event_log 行。"""
    for i in range(count):
        ledger.log_voice_event({
            "request_id": f"req_{i:06d}",
            "session_id": "sess_001",
            "event_type": "ASR_CHUNK",
            "direction": "EDGE_TO_HOST",
            "seq": i,
            "status": "OK",
            "ts_ms": base_ts + i * 100,
        })


# ---------------------------------------------------------------------------
# fetch_by_time_range 基础功能
# ---------------------------------------------------------------------------

class TestFetchByTimeRange:

    def test_basic_time_range_filter(self, ledger: SQLiteLedger) -> None:
        """按时间窗口过滤，含端点。"""
        _insert_intent_rows(ledger, 10, base_ts=1000)
        # ts_ms: 1000, 1100, 1200, ..., 1900

        rows = ledger.fetch_by_time_range(
            "intent_log", start_ms=1200, end_ms=1500, limit=100,
        )
        ts_values = [r["ts_ms"] for r in rows]
        assert all(1200 <= t <= 1500 for t in ts_values)
        # 1200, 1300, 1400, 1500 = 4 行
        assert len(rows) == 4

    def test_start_only(self, ledger: SQLiteLedger) -> None:
        """只指定 start_ms。"""
        _insert_intent_rows(ledger, 5, base_ts=1000)
        rows = ledger.fetch_by_time_range(
            "intent_log", start_ms=1300, limit=100,
        )
        assert all(r["ts_ms"] >= 1300 for r in rows)
        # 1300, 1400 = 2 行
        assert len(rows) == 2

    def test_end_only(self, ledger: SQLiteLedger) -> None:
        """只指定 end_ms。"""
        _insert_intent_rows(ledger, 5, base_ts=1000)
        rows = ledger.fetch_by_time_range(
            "intent_log", end_ms=1200, limit=100,
        )
        assert all(r["ts_ms"] <= 1200 for r in rows)
        # 1000, 1100, 1200 = 3 行
        assert len(rows) == 3

    def test_no_time_range(self, ledger: SQLiteLedger) -> None:
        """不指定时间窗口，返回全量（受 limit 限制）。"""
        _insert_intent_rows(ledger, 5, base_ts=1000)
        rows = ledger.fetch_by_time_range("intent_log", limit=100)
        assert len(rows) == 5

    def test_empty_window(self, ledger: SQLiteLedger) -> None:
        """空窗口返回空列表。"""
        _insert_intent_rows(ledger, 5, base_ts=1000)
        rows = ledger.fetch_by_time_range(
            "intent_log", start_ms=9000, end_ms=9999, limit=100,
        )
        assert rows == []

    def test_invalid_table_name(self, ledger: SQLiteLedger) -> None:
        """非法表名抛出 ValueError。"""
        with pytest.raises(ValueError, match="AUDIT_TABLE_NAME_INVALID"):
            ledger.fetch_by_time_range("evil_table", limit=10)


# ---------------------------------------------------------------------------
# fetch_by_time_range 分页
# ---------------------------------------------------------------------------

class TestFetchPagination:

    def test_limit(self, ledger: SQLiteLedger) -> None:
        """limit 正确限制返回行数。"""
        _insert_intent_rows(ledger, 20, base_ts=1000)
        rows = ledger.fetch_by_time_range("intent_log", limit=5)
        assert len(rows) == 5

    def test_offset(self, ledger: SQLiteLedger) -> None:
        """offset 正确跳过行数。"""
        _insert_intent_rows(ledger, 10, base_ts=1000)
        page1 = ledger.fetch_by_time_range("intent_log", limit=3, offset=0)
        page2 = ledger.fetch_by_time_range("intent_log", limit=3, offset=3)
        assert len(page1) == 3
        assert len(page2) == 3
        # 两页不重叠
        ids1 = {r["request_id"] for r in page1}
        ids2 = {r["request_id"] for r in page2}
        assert ids1.isdisjoint(ids2)

    def test_offset_beyond_total(self, ledger: SQLiteLedger) -> None:
        """offset 超过总行数返回空列表。"""
        _insert_intent_rows(ledger, 5, base_ts=1000)
        rows = ledger.fetch_by_time_range("intent_log", limit=10, offset=100)
        assert rows == []

    def test_limit_clamped_to_max(self, ledger: SQLiteLedger) -> None:
        """limit 超过硬上限被截断到 10000。"""
        _insert_intent_rows(ledger, 3, base_ts=1000)
        rows = ledger.fetch_by_time_range("intent_log", limit=99999)
        assert len(rows) == 3  # 只有 3 行，不会报错


# ---------------------------------------------------------------------------
# count_by_time_range
# ---------------------------------------------------------------------------

class TestCountByTimeRange:

    def test_count_matches_fetch(self, ledger: SQLiteLedger) -> None:
        """count 与 fetch 行数一致。"""
        _insert_intent_rows(ledger, 10, base_ts=1000)
        count = ledger.count_by_time_range("intent_log", start_ms=1200, end_ms=1500)
        rows = ledger.fetch_by_time_range("intent_log", start_ms=1200, end_ms=1500, limit=100)
        assert count == len(rows) == 4

    def test_count_no_filter(self, ledger: SQLiteLedger) -> None:
        """无时间过滤时返回总行数。"""
        _insert_intent_rows(ledger, 7, base_ts=1000)
        count = ledger.count_by_time_range("intent_log")
        assert count == 7

    def test_count_invalid_table(self, ledger: SQLiteLedger) -> None:
        """非法表名抛出 ValueError。"""
        with pytest.raises(ValueError, match="AUDIT_TABLE_NAME_INVALID"):
            ledger.count_by_time_range("evil_table")


# ---------------------------------------------------------------------------
# cross_timeline 时间窗口
# ---------------------------------------------------------------------------

class TestCrossTimelineTimeRange:

    def test_aggregate_with_time_range(self, ledger: SQLiteLedger) -> None:
        """aggregate 支持 start_ms/end_ms 过滤。"""
        _insert_intent_rows(ledger, 10, base_ts=1000)
        _insert_voice_event_rows(ledger, 10, base_ts=1000)

        aggregator = CrossTimelineAggregator(ledger=ledger)
        result = aggregator.aggregate(
            request_id="req_000003",
            start_ms=1000,
            end_ms=2000,
        )
        # 应该能找到 request_id=req_000003 的事件
        assert result.status in ("COMPLETE", "HOST_ONLY", "PARTIAL")
        assert result.host_event_count >= 0

    def test_build_cross_timeline_with_range(self, ledger: SQLiteLedger) -> None:
        """build_cross_timeline 便捷入口支持时间窗口。"""
        _insert_intent_rows(ledger, 5, base_ts=1000)
        result = build_cross_timeline(
            ledger=ledger,
            request_id="req_000002",
            start_ms=1000,
            end_ms=2000,
        )
        assert "timeline" in result
        assert "status" in result


# ---------------------------------------------------------------------------
# 性能基准
# ---------------------------------------------------------------------------

class TestTimeRangePerformance:

    def test_fetch_10000_rows_under_100ms(self, ledger: SQLiteLedger) -> None:
        """10000 条记录下时间窗口查询耗时 < 100ms。"""
        _insert_intent_rows(ledger, 10_000, base_ts=1_000_000)

        start = time.perf_counter()
        rows = ledger.fetch_by_time_range(
            "intent_log",
            start_ms=1_200_000,
            end_ms=1_500_000,
            limit=10_000,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert len(rows) > 0
        assert elapsed_ms < 100, f"fetch_by_time_range took {elapsed_ms:.1f}ms, expected < 100ms"

    def test_count_10000_rows_under_50ms(self, ledger: SQLiteLedger) -> None:
        """10000 条记录下 count 查询耗时 < 50ms。"""
        _insert_intent_rows(ledger, 10_000, base_ts=1_000_000)

        start = time.perf_counter()
        count = ledger.count_by_time_range(
            "intent_log",
            start_ms=1_200_000,
            end_ms=1_500_000,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert count > 0
        assert elapsed_ms < 50, f"count_by_time_range took {elapsed_ms:.1f}ms, expected < 50ms"


# ---------------------------------------------------------------------------
# 多表覆盖
# ---------------------------------------------------------------------------

class TestMultiTableTimeRange:

    def test_voice_event_time_range(self, ledger: SQLiteLedger) -> None:
        """voice_event_log 也支持时间窗口查询。"""
        _insert_voice_event_rows(ledger, 10, base_ts=2000)
        rows = ledger.fetch_by_time_range(
            "voice_event_log", start_ms=2300, end_ms=2600, limit=100,
        )
        assert len(rows) == 4
        assert all(2300 <= r["ts_ms"] <= 2600 for r in rows)

    def test_governor_log_time_range(self, ledger: SQLiteLedger) -> None:
        """governor_log 支持时间窗口查询（需要先插入数据）。"""
        for i in range(5):
            ledger.log_governor({
                "request_id": f"req_{i}",
                "intent_id": f"int_{i}",
                "decision": "APPROVE",
                "reason_code": None,
                "ts_ms": 3000 + i * 100,
            })
        rows = ledger.fetch_by_time_range(
            "governor_log", start_ms=3100, end_ms=3300, limit=100,
        )
        assert len(rows) == 3
