"""
T-OBS-SESS-01~06 — SessionQueryService 单元测试
TASK-0189 / FR-OBS-009
"""
from __future__ import annotations

import time
import pytest
from unittest.mock import MagicMock

from host_brain.obs.session_query import SessionQueryService


def _make_ledger(rows):
    """构造 mock ledger，fetch_by_time_range 返回指定 rows。"""
    ledger = MagicMock()
    ledger.fetch_by_time_range.return_value = rows
    return ledger


def _make_rows(n=5, decision="APPROVE", id_offset=0):
    now = int(time.time() * 1000)
    return [
        {
            "request_id": f"req-{i + id_offset:04d}",
            "session_id": f"sess-{i + id_offset:04d}",
            "ts_ms":      now - (i + id_offset) * 60_000,
            "decision":   decision,
            "reason_code": None if decision == "APPROVE" else "RATE_LIMIT_EXCEEDED",
            "channel":    "voice" if i % 2 == 0 else "api",
        }
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# T-OBS-SESS-01: 基本分页
# ---------------------------------------------------------------------------
def test_session_query_basic_pagination():
    rows = _make_rows(25)
    svc = SessionQueryService(_make_ledger(rows))
    result = svc.query(page=1, page_size=10)
    assert result["total"] == 25
    assert len(result["items"]) == 10
    assert result["page"] == 1
    assert result["page_size"] == 10


# ---------------------------------------------------------------------------
# T-OBS-SESS-02: 第二页数据正确
# ---------------------------------------------------------------------------
def test_session_query_page2():
    rows = _make_rows(25)
    svc = SessionQueryService(_make_ledger(rows))
    result = svc.query(page=2, page_size=10)
    assert len(result["items"]) == 10
    assert result["page"] == 2


# ---------------------------------------------------------------------------
# T-OBS-SESS-03: status=ok 过滤
# ---------------------------------------------------------------------------
def test_session_query_status_filter_ok():
    rows = _make_rows(5, "APPROVE", id_offset=0) + _make_rows(3, "REJECT", id_offset=100)
    svc = SessionQueryService(_make_ledger(rows))
    result = svc.query(status="ok")
    assert result["total"] == 5
    for item in result["items"]:
        assert item["status"] == "ok"


# ---------------------------------------------------------------------------
# T-OBS-SESS-04: status=rejected 过滤
# ---------------------------------------------------------------------------
def test_session_query_status_filter_rejected():
    rows = _make_rows(5, "APPROVE", id_offset=0) + _make_rows(3, "REJECT", id_offset=100)
    svc = SessionQueryService(_make_ledger(rows))
    result = svc.query(status="rejected")
    assert result["total"] == 3
    for item in result["items"]:
        assert item["status"] == "rejected"


# ---------------------------------------------------------------------------
# T-OBS-SESS-05: keyword 过滤
# ---------------------------------------------------------------------------
def test_session_query_keyword_filter():
    rows = _make_rows(10)
    svc = SessionQueryService(_make_ledger(rows))
    result = svc.query(keyword="req-0001")
    assert result["total"] == 1
    assert result["items"][0]["request_id"] == "req-0001"


# ---------------------------------------------------------------------------
# T-OBS-SESS-06: 空结果时返回 items=[] 而非 404
# ---------------------------------------------------------------------------
def test_session_query_empty_result():
    svc = SessionQueryService(_make_ledger([]))
    result = svc.query()
    assert result["total"] == 0
    assert result["items"] == []
    assert "timestamp" in result


def test_session_query_pages_governor_rows_and_reads_intent_preview():
    first_page = _make_rows(10_000, id_offset=0)
    second_page = _make_rows(1, id_offset=10_000)
    intent_rows = [
        {
            "request_id": "req-10000",
            "session_id": "sess-10000",
            "ts_ms": first_page[0]["ts_ms"],
            "raw_text": "latest intent preview from intent log",
        }
    ]
    ledger = MagicMock()
    ledger.fetch_by_time_range.side_effect = [first_page, second_page, intent_rows]

    svc = SessionQueryService(ledger)
    result = svc.query(page=1, page_size=20)

    assert result["total"] == 10_001
    assert ledger.fetch_by_time_range.call_count == 3

    governor_first = ledger.fetch_by_time_range.call_args_list[0]
    governor_second = ledger.fetch_by_time_range.call_args_list[1]
    intent_call = ledger.fetch_by_time_range.call_args_list[2]

    for call in (governor_first, governor_second, intent_call):
        assert "start_ms" in call.kwargs
        assert "end_ms" in call.kwargs
        assert "limit" in call.kwargs
        assert "offset" in call.kwargs

    assert governor_first.args[0] == "governor_log"
    assert governor_second.args[0] == "governor_log"
    assert intent_call.args[0] == "intent_log"
    assert governor_first.kwargs["offset"] == 0
    assert governor_second.kwargs["offset"] == 10_000
    assert intent_call.kwargs["offset"] == 0
