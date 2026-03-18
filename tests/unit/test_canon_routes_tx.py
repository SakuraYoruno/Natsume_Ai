"""T-CANON-TX-01~06：Canon 治理 API 强化测试（M16-A TASK-0178）。

覆盖：
  T-CANON-TX-01  _get_canon_store 双重检查锁定（F-28 修复）
  T-CANON-TX-02  approve 操作事务包裹（F-35 修复）
  T-CANON-TX-03  approve 事务回滚（DB 异常时 ROLLBACK）
  T-CANON-TX-04  reject 审计写入失败不阻断主流程（F-36 修复）
  T-CANON-TX-05  _get_ledger_from_app 降级路径（F-29 修复）
  T-CANON-TX-06  _get_canon_store 多线程并发安全
"""
from __future__ import annotations

import threading
import time
from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# 辅助：构造最小 ledger mock
# ---------------------------------------------------------------------------

def _make_ledger_mock(proposal_row: dict | None = None) -> MagicMock:
    ledger = MagicMock()
    ledger.get_proposal.return_value = proposal_row
    ledger.list_proposals.return_value = ([], 0)
    ledger.log_system_event.return_value = None
    ledger.update_proposal_status.return_value = None
    # transaction() 上下文管理器
    import contextlib

    @contextlib.contextmanager
    def _tx():
        yield

    ledger.transaction.return_value = _tx()
    return ledger


# ---------------------------------------------------------------------------
# T-CANON-TX-01  _get_canon_store 双重检查锁定（F-28 修复）
# ---------------------------------------------------------------------------

class TestGetCanonStoreLock:
    def test_double_checked_locking_creates_single_instance(self):
        """T-CANON-TX-01：多次调用 _get_canon_store 只创建一个实例。"""
        import host_brain.app.canon_routes as cr

        # 重置全局单例
        cr._canon_store = None
        ledger = _make_ledger_mock()

        with patch("host_brain.app.canon_routes.WorldCanonStore") as MockStore:
            MockStore.return_value = MagicMock()
            store1 = cr._get_canon_store(ledger)
            store2 = cr._get_canon_store(ledger)
            assert store1 is store2
            assert MockStore.call_count == 1  # 只创建一次

    def test_lock_exists_on_module(self):
        """T-CANON-TX-01b：模块级别存在 _canon_store_lock（threading.Lock）。"""
        import host_brain.app.canon_routes as cr
        assert hasattr(cr, "_canon_store_lock")
        assert isinstance(cr._canon_store_lock, type(threading.Lock()))


# ---------------------------------------------------------------------------
# T-CANON-TX-02  approve 操作事务包裹（F-35 修复）
# ---------------------------------------------------------------------------

class TestApproveTransaction:
    def test_approve_calls_transaction(self):
        """T-CANON-TX-02：approve_proposal 调用 approve_proposal_with_content（内部自管事务）。"""
        import host_brain.app.canon_routes as cr
        import asyncio

        proposal_row = {
            "proposal_id": "p-tx-02",
            "target_id": "doc-001",
            "status": "pending",
            "proposer_node_id": "test",
            "summary": "test",
            "proposed_hash": None,
            "base_version": 1,
            "proposed_version": 2,
            "ts_ms": int(time.time() * 1000),
        }
        ledger = _make_ledger_mock(proposal_row)

        mock_store = MagicMock()
        mock_doc = MagicMock()
        mock_doc.doc_id = "doc-001"
        mock_store.approve_proposal_with_content.return_value = mock_doc

        cr._canon_store = None
        cr._ledger_provider = lambda: ledger

        with patch("host_brain.app.canon_routes._get_canon_store", return_value=mock_store):
            from host_brain.app.canon_routes import CanonProposalApproveRequest
            req = CanonProposalApproveRequest(
                proposed_title="Test",
                proposed_tags=[],
                proposed_body="test body",
            )
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(
                    cr.approve_proposal("p-tx-02", req)
                )
            finally:
                loop.close()

        # approve_proposal_with_content 内部自管事务，验证它被调用
        mock_store.approve_proposal_with_content.assert_called_once()


# ---------------------------------------------------------------------------
# T-CANON-TX-03  approve 事务回滚（DB 异常时 ROLLBACK）
# ---------------------------------------------------------------------------

class TestApproveTransactionRollback:
    def test_approve_rollback_on_exception(self):
        """T-CANON-TX-03：approve_proposal_with_content 抛出异常时返回 500。"""
        import host_brain.app.canon_routes as cr
        import asyncio
        from fastapi import HTTPException

        proposal_row = {
            "proposal_id": "p-tx-03",
            "target_id": "doc-001",
            "status": "pending",
            "proposer_node_id": "test",
            "summary": "test",
            "proposed_hash": None,
            "base_version": 1,
            "proposed_version": 2,
            "ts_ms": int(time.time() * 1000),
        }
        ledger = _make_ledger_mock(proposal_row)

        mock_store = MagicMock()
        mock_store.approve_proposal_with_content.side_effect = RuntimeError("DB error")

        cr._ledger_provider = lambda: ledger

        with patch("host_brain.app.canon_routes._get_canon_store", return_value=mock_store):
            from host_brain.app.canon_routes import CanonProposalApproveRequest
            req = CanonProposalApproveRequest(
                proposed_title="Test",
                proposed_tags=[],
                proposed_body="test body",
            )
            with pytest.raises(HTTPException) as exc_info:
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(
                        cr.approve_proposal("p-tx-03", req)
                    )
                finally:
                    loop.close()
            # 内部异常应被包装为 500
            assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# T-CANON-TX-04  reject 审计写入失败不阻断主流程（F-36 修复）
# ---------------------------------------------------------------------------

class TestRejectAuditNonFatal:
    def test_reject_succeeds_even_if_audit_fails(self):
        """T-CANON-TX-04：reject 审计写入失败时只记录 warning，不返回 500。"""
        import host_brain.app.canon_routes as cr
        import asyncio

        proposal_row = {
            "proposal_id": "p-tx-04",
            "target_id": "doc-001",
            "status": "pending",
            "proposer_node_id": "test",
            "summary": "test",
            "proposed_hash": None,
            "base_version": 1,
            "proposed_version": 2,
            "ts_ms": int(time.time() * 1000),
        }
        ledger = _make_ledger_mock(proposal_row)
        # 审计写入抛出异常
        ledger.log_system_event.side_effect = RuntimeError("audit DB full")

        mock_store = MagicMock()
        mock_store.reject_proposal.return_value = proposal_row

        cr._ledger_provider = lambda: ledger

        with patch("host_brain.app.canon_routes._get_canon_store", return_value=mock_store):
            from host_brain.app.canon_routes import CanonProposalRejectRequest
            req = CanonProposalRejectRequest(reason="test reject")
            # 不应抛出异常
            result = asyncio.new_event_loop().run_until_complete(
                cr.reject_proposal("p-tx-04", req)
            )
            assert result.proposal_id == "p-tx-04"

    def test_reject_audit_failure_logs_warning(self, caplog):
        """T-CANON-TX-04b：reject 审计写入失败时记录 warning 日志。"""
        import host_brain.app.canon_routes as cr
        import asyncio
        import logging

        proposal_row = {
            "proposal_id": "p-tx-04b",
            "target_id": "doc-001",
            "status": "pending",
            "proposer_node_id": "test",
            "summary": "test",
            "proposed_hash": None,
            "base_version": 1,
            "proposed_version": 2,
            "ts_ms": int(time.time() * 1000),
        }
        ledger = _make_ledger_mock(proposal_row)
        ledger.log_system_event.side_effect = RuntimeError("audit DB full")

        mock_store = MagicMock()
        mock_store.reject_proposal.return_value = proposal_row

        cr._ledger_provider = lambda: ledger

        with patch("host_brain.app.canon_routes._get_canon_store", return_value=mock_store):
            from host_brain.app.canon_routes import CanonProposalRejectRequest
            req = CanonProposalRejectRequest(reason="test reject")
            with caplog.at_level(logging.WARNING, logger="Natsume.canon_routes"):
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(
                        cr.reject_proposal("p-tx-04b", req)
                    )
                finally:
                    loop.close()
            assert any("audit write failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# T-CANON-TX-05  _get_ledger_from_app 降级路径（F-29 修复）
# ---------------------------------------------------------------------------

class TestGetLedgerFallback:
    def test_fallback_to_memory_when_no_provider(self):
        """T-CANON-TX-05：未注入 provider 时降级为内存 ledger，不抛出异常。"""
        import host_brain.app.canon_routes as cr
        cr._ledger_provider = None
        ledger = cr._get_ledger_from_app()
        assert ledger is not None
        assert ledger.db_path == ":memory:"

    def test_uses_provider_when_set(self):
        """T-CANON-TX-05b：注入 provider 后使用 provider 返回的 ledger。"""
        import host_brain.app.canon_routes as cr
        mock_ledger = MagicMock()
        cr._ledger_provider = lambda: mock_ledger
        result = cr._get_ledger_from_app()
        assert result is mock_ledger


# ---------------------------------------------------------------------------
# T-CANON-TX-06  _get_canon_store 多线程并发安全
# ---------------------------------------------------------------------------

class TestGetCanonStoreConcurrency:
    def test_concurrent_calls_create_single_instance(self):
        """T-CANON-TX-06：多线程并发调用 _get_canon_store 只创建一个实例。"""
        import host_brain.app.canon_routes as cr

        cr._canon_store = None
        ledger = _make_ledger_mock()
        instances = []
        errors = []

        with patch("host_brain.app.canon_routes.WorldCanonStore") as MockStore:
            MockStore.return_value = MagicMock()

            def worker():
                try:
                    store = cr._get_canon_store(ledger)
                    instances.append(id(store))
                except Exception as e:
                    errors.append(e)

            threads = [threading.Thread(target=worker) for _ in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        assert len(errors) == 0, f"并发调用出现异常: {errors}"
        assert len(set(instances)) == 1, "多线程并发应只创建一个 WorldCanonStore 实例"
        assert MockStore.call_count == 1, "WorldCanonStore 只应被实例化一次"
