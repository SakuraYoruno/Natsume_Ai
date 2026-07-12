"""Evidence Plane stream_events 写入专项测试（M38-L，T-EVP-12~14）。

验证 EvidencePlane.write_stream_events 的写入正确性和静默降级行为。
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from host_brain.integrations.graph_evidence import EvidencePlane
from host_brain.integrations.graph_contract import (
    STREAM_PHASE_PRESENCE,
    STREAM_EVENT_NODE_START,
    STREAM_EVENT_NODE_END,
)


def _make_evidence_plane() -> EvidencePlane:
    return EvidencePlane(db_path=":memory:")


def _make_presence_events(count: int = 2) -> list[dict[str, Any]]:
    now = int(time.time() * 1000)
    events = []
    for i in range(count):
        events.append({
            "phase": STREAM_PHASE_PRESENCE,
            "event_type": STREAM_EVENT_NODE_START if i % 2 == 0 else STREAM_EVENT_NODE_END,
            "node_id": f"node_{i}",
            "chunk": f"chunk_{i}",
            "ts_ms": now + i,
        })
    return events


# ---------------------------------------------------------------------------
# T-EVP-12：write_stream_events 将事件写入 SQLite stream_events 表
# ---------------------------------------------------------------------------
def test_evp_12_write_stream_events_inserts_rows():
    """T-EVP-12：write_stream_events 将事件写入 SQLite stream_events 表"""
    ep = _make_evidence_plane()
    events = _make_presence_events(count=3)
    ep.write_stream_events(request_id="req-str-001", graph_id="graph-001", events=events)

    rows = ep.query_stream_events(request_id="req-str-001")
    assert len(rows) == 3


# ---------------------------------------------------------------------------
# T-EVP-13：stream_events 表包含正确的字段（phase、event_type、node_id 等）
# ---------------------------------------------------------------------------
def test_evp_13_stream_events_has_correct_fields():
    """T-EVP-13：stream_events 表包含正确的字段（phase、event_type、node_id 等）"""
    ep = _make_evidence_plane()
    events = _make_presence_events(count=1)
    ep.write_stream_events(request_id="req-str-002", graph_id="graph-002", events=events)

    rows = ep.query_stream_events(request_id="req-str-002")
    assert len(rows) == 1
    row = rows[0]
    assert row["request_id"] == "req-str-002"
    assert row["graph_id"] == "graph-002"
    assert row["phase"] == STREAM_PHASE_PRESENCE
    assert row["event_type"] == STREAM_EVENT_NODE_START
    assert row["node_id"] == "node_0"
    assert row["chunk"] == "chunk_0"
    assert isinstance(row["ts_ms"], int)


# ---------------------------------------------------------------------------
# T-EVP-14：write_stream_events 写入失败时静默降级
# ---------------------------------------------------------------------------
def test_evp_14_write_stream_events_silent_degradation(monkeypatch: pytest.MonkeyPatch):
    """T-EVP-14：write_stream_events 写入失败时静默降级（不抛出异常）"""
    ep = _make_evidence_plane()

    def _bad_write(**_kwargs):
        raise RuntimeError("模拟数据库连接失败")

    assert ep._repository is not None
    monkeypatch.setattr(ep._repository, "append_stream_events", _bad_write)

    events = _make_presence_events(count=2)
    # 不应抛出异常
    ep.write_stream_events(request_id="req-str-fail", graph_id="g", events=events)
    # 查询返回空列表
    assert ep.query_stream_events(request_id="req-str-fail") == []
