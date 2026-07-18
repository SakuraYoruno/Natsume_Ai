"""T-CURSOR-01~05：Canon 提案 cursor-based 分页测试（M16-B TASK-0179）。

覆盖：
  T-CURSOR-01  after_id=None 时使用 OFFSET 分页（向后兼容）
  T-CURSOR-02  after_id 指定时使用 cursor 分页（id < after_id）
  T-CURSOR-03  cursor 分页返回 row_id 字段（供下次翻页使用）
  T-CURSOR-04  after_id=0 时返回空列表（边界条件）
  T-CURSOR-05  list_proposals REST 端点支持 after_id 查询参数
"""
from __future__ import annotations

import time

import pytest

from host_brain.app.host_route_ports import HostRoutePorts
from tests.fakes.canon_runtime import compose_canon_test_runtime


@pytest.fixture
def canon_service(tmp_path):
    import host_brain.app.canon_routes as cr

    with compose_canon_test_runtime(
        tmp_path,
        request_id="canon-route-pagination",
    ) as runtime:
        yield HostRoutePorts(
            canon_service_provider=lambda: runtime.container.application,
        )


# ---------------------------------------------------------------------------
# 辅助：构造内存 ledger
# ---------------------------------------------------------------------------

def _make_in_memory_ledger():
    from host_brain.audit.sqlite_ledger import SQLiteLedger
    return SQLiteLedger(db_path=":memory:")


def _insert_proposals(ledger, count: int = 10) -> list[str]:
    """向 ledger 插入 count 个 pending 提案，返回 proposal_id 列表。"""
    ids = []
    for i in range(count):
        pid = f"cursor-test-{i:04d}"
        ledger._conn.execute(
            """
            INSERT INTO canon_proposal_log
              (proposal_id, target_id, status, proposer_node_id, summary, ts_ms)
            VALUES (?, ?, 'pending', 'test', 'test', ?)
            """,
            (pid, f"doc-{i}", int(time.time() * 1000) + i),
        )
        ids.append(pid)
    ledger._conn.commit()
    return ids


# ---------------------------------------------------------------------------
# T-CURSOR-01  after_id=None 时使用 OFFSET 分页（向后兼容）
# ---------------------------------------------------------------------------

class TestOffsetPaginationBackwardCompat:
    def test_offset_pagination_default(self):
        """T-CURSOR-01：after_id=None 时使用 OFFSET 分页，向后兼容。"""
        ledger = _make_in_memory_ledger()
        _insert_proposals(ledger, 10)

        items, total = ledger.list_proposals(limit=5, offset=0)
        assert total == 10
        assert len(items) == 5

        items2, _ = ledger.list_proposals(limit=5, offset=5)
        assert len(items2) == 5

        # 两页不重叠
        ids1 = {r["proposal_id"] for r in items}
        ids2 = {r["proposal_id"] for r in items2}
        assert ids1.isdisjoint(ids2)

    def test_offset_pagination_last_page(self):
        """T-CURSOR-01b：OFFSET 分页最后一页返回剩余条目。"""
        ledger = _make_in_memory_ledger()
        _insert_proposals(ledger, 7)

        items, total = ledger.list_proposals(limit=5, offset=5)
        assert total == 7
        assert len(items) == 2


# ---------------------------------------------------------------------------
# T-CURSOR-02  after_id 指定时使用 cursor 分页（id < after_id）
# ---------------------------------------------------------------------------

class TestCursorPagination:
    def test_cursor_pagination_basic(self):
        """T-CURSOR-02：after_id 指定时返回 id < after_id 的记录。"""
        ledger = _make_in_memory_ledger()
        _insert_proposals(ledger, 10)

        # 先获取第一页（无 after_id）
        page1, total = ledger.list_proposals(limit=5, after_id=None)
        assert total == 10
        assert len(page1) == 5

        # 用第一页最后一条的 row_id 作为 after_id
        last_row_id = page1[-1]["row_id"]
        page2, _ = ledger.list_proposals(limit=5, after_id=last_row_id)
        assert len(page2) == 5

        # 两页不重叠
        ids1 = {r["proposal_id"] for r in page1}
        ids2 = {r["proposal_id"] for r in page2}
        assert ids1.isdisjoint(ids2)

    def test_cursor_pagination_no_overlap(self):
        """T-CURSOR-02b：cursor 分页不产生重复记录。"""
        ledger = _make_in_memory_ledger()
        _insert_proposals(ledger, 15)

        all_ids = set()
        after_id = None
        page_count = 0

        while True:
            items, _ = ledger.list_proposals(limit=5, after_id=after_id)
            if not items:
                break
            for item in items:
                assert item["proposal_id"] not in all_ids, "cursor 分页不应有重复记录"
                all_ids.add(item["proposal_id"])
            after_id = items[-1]["row_id"]
            page_count += 1
            if page_count > 10:
                break

        assert len(all_ids) == 15


# ---------------------------------------------------------------------------
# T-CURSOR-03  cursor 分页返回 row_id 字段（供下次翻页使用）
# ---------------------------------------------------------------------------

class TestCursorRowId:
    def test_row_id_present_in_results(self):
        """T-CURSOR-03：list_proposals 返回的每条记录包含 row_id 字段。"""
        ledger = _make_in_memory_ledger()
        _insert_proposals(ledger, 3)

        items, _ = ledger.list_proposals(limit=10)
        for item in items:
            assert "row_id" in item, "每条记录应包含 row_id 字段"
            assert isinstance(item["row_id"], int)
            assert item["row_id"] > 0

    def test_row_id_monotonically_decreasing(self):
        """T-CURSOR-03b：cursor 分页结果按 id DESC 排序，row_id 单调递减。"""
        ledger = _make_in_memory_ledger()
        _insert_proposals(ledger, 5)

        items, _ = ledger.list_proposals(limit=10)
        row_ids = [item["row_id"] for item in items]
        assert row_ids == sorted(row_ids, reverse=True), "row_id 应按降序排列"


# ---------------------------------------------------------------------------
# T-CURSOR-04  after_id=0 时返回空列表（边界条件）
# ---------------------------------------------------------------------------

class TestCursorEdgeCases:
    def test_after_id_zero_returns_empty(self):
        """T-CURSOR-04：after_id=0 时返回空列表（id < 0 无记录）。"""
        ledger = _make_in_memory_ledger()
        _insert_proposals(ledger, 5)

        items, total = ledger.list_proposals(limit=10, after_id=0)
        assert len(items) == 0

    def test_after_id_beyond_max_returns_all(self):
        """T-CURSOR-04b：after_id 超过最大 id 时返回所有记录。"""
        ledger = _make_in_memory_ledger()
        _insert_proposals(ledger, 5)

        items, total = ledger.list_proposals(limit=10, after_id=999999)
        assert len(items) == 5


# ---------------------------------------------------------------------------
# T-CURSOR-05  list_proposals REST 端点支持 after_id 查询参数
# ---------------------------------------------------------------------------

class TestListProposalsEndpointCursorParam:
    def test_endpoint_accepts_retired_after_id_param(self, canon_service):
        """The legacy integer cursor is accepted but cannot steer the stable projection."""
        import asyncio
        import host_brain.app.canon_routes as cr

        loop = asyncio.new_event_loop()
        try:
            response = loop.run_until_complete(cr.list_proposals(
                after_id=42,
                _route_ports=canon_service,
            ))
        finally:
            loop.close()

        assert response.total == 0
        assert response.page == 1
        assert response.items == []

    def test_endpoint_after_id_none_by_default(self, canon_service):
        """Default pagination resolves through the same explicit application service."""
        import asyncio
        import host_brain.app.canon_routes as cr

        loop = asyncio.new_event_loop()
        try:
            response = loop.run_until_complete(cr.list_proposals(
                _route_ports=canon_service,
            ))
        finally:
            loop.close()

        assert response.total == 0
        assert response.page_size == 20
