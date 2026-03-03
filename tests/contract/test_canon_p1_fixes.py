"""P1 风险修复验证测试（M10-A 方向 A）。

T-Canon-P1-01：proposal_id UNIQUE 约束 — 重复写入同一 proposal_id 应触发 IntegrityError。
T-Canon-P1-02：canon_routes 无循环导入 — 直接 import canon_routes 不应触发 ImportError。
T-Canon-P1-03：set_ledger_provider 注入生效 — 注入后 _get_ledger_from_app 返回注入的 ledger。
T-Canon-P1-04：rollback_proposal 成功 — APPLIED 提案可回滚，SSOT 版本降回 base_version。
T-Canon-P1-05：rollback_proposal 非 APPLIED 状态 — pending/rejected 提案回滚应抛 PermissionError。
"""

from __future__ import annotations

import importlib
import sqlite3

import pytest

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.memory.canon_world_store import WorldCanonStore


# ---------------------------------------------------------------------------
# 共享 fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def ledger(tmp_path):
    db = SQLiteLedger(db_path=str(tmp_path / "test.sqlite3"))
    return db


@pytest.fixture()
def store(tmp_path, ledger):
    return WorldCanonStore(world_root=str(tmp_path / "world"), ledger=ledger)


# ---------------------------------------------------------------------------
# T-Canon-P1-01：proposal_id UNIQUE 约束
# ---------------------------------------------------------------------------

def test_proposal_id_unique_constraint(ledger):
    """migration 0017 的 UNIQUE INDEX + INSERT OR IGNORE 应保证幂等写入。

    相同 proposal_id 重复写入时，第二次应被静默忽略（不抛异常，不产生重复记录）。
    """
    row = {
        "proposal_id": "p-unique-001",
        "canon_kind": "world",
        "target_id": "doc-001",
        "base_version": 0,
        "proposed_version": 1,
        "proposed_hash": "abc123",
        "proposer_node_id": "test",
        "summary": "first",
        "status": "pending",
        "ts_ms": 1000,
    }
    ledger.log_canon_proposal(row)

    # 第二次写入相同 proposal_id 应被静默忽略（INSERT OR IGNORE）
    ledger.log_canon_proposal({**row, "summary": "duplicate attempt"})

    # 数据库中应只有一条记录（幂等保证）
    items, total = ledger.list_proposals(status="pending")
    matching = [r for r in items if r["proposal_id"] == "p-unique-001"]
    assert len(matching) == 1, f"UNIQUE 约束应保证只有一条记录，实际有 {len(matching)} 条"
    assert matching[0]["summary"] == "first", "第一次写入的 summary 应被保留"


# ---------------------------------------------------------------------------
# T-Canon-P1-02：canon_routes 无循环导入
# ---------------------------------------------------------------------------

def test_canon_routes_no_circular_import():
    """直接 import canon_routes 不应触发 ImportError 或循环导入错误。"""
    try:
        import host_brain.app.canon_routes as cr
        assert hasattr(cr, "router"), "canon_routes 应暴露 router 对象"
        assert hasattr(cr, "set_ledger_provider"), "canon_routes 应暴露 set_ledger_provider"
    except ImportError as exc:
        pytest.fail(f"canon_routes 导入失败（循环导入）: {exc}")


# ---------------------------------------------------------------------------
# T-Canon-P1-03：set_ledger_provider 注入生效
# ---------------------------------------------------------------------------

def test_set_ledger_provider_injection(ledger):
    """注入 ledger provider 后，_get_ledger_from_app 应返回注入的 ledger 实例。"""
    import host_brain.app.canon_routes as cr

    # 保存原始 provider
    original_provider = cr._ledger_provider

    try:
        cr.set_ledger_provider(lambda: ledger)
        result = cr._get_ledger_from_app()
        assert result is ledger, "注入后应返回注入的 ledger 实例"
    finally:
        # 恢复原始 provider，避免污染其他测试
        cr._ledger_provider = original_provider


# ---------------------------------------------------------------------------
# T-Canon-P1-04：rollback_proposal 成功
# ---------------------------------------------------------------------------

def test_rollback_proposal_success(store, ledger):
    """APPLIED 提案可回滚，SSOT 版本降回 base_version，提案 status 变为 ROLLED_BACK。"""
    # 1. 提交提案
    store.submit_proposal(
        proposal_id="p-rollback-001",
        target_doc_id="doc-rollback",
        proposed_title="回滚测试文档",
        proposed_tags=["test"],
        proposed_body="这是一个用于测试回滚的文档内容",
        ts_ms=1000,
    )

    # 2. 审核通过（触发 SSOT 写入）
    doc = store.approve_proposal_with_content(
        "p-rollback-001",
        proposed_title="回滚测试文档",
        proposed_tags=["test"],
        proposed_body="这是一个用于测试回滚的文档内容",
        ts_ms=2000,
    )
    assert doc.version == 1

    # 3. 回滚
    result = store.rollback_proposal(
        "p-rollback-001",
        rollback_reason="测试回滚",
        ts_ms=3000,
    )
    assert result["proposal_id"] == "p-rollback-001"
    assert result["rolled_back_to_version"] == 0  # base_version=0，文件应被删除

    # 4. 验证提案 status 已更新为 ROLLED_BACK
    proposal = ledger.get_proposal("p-rollback-001")
    assert proposal is not None
    assert proposal["status"] == "ROLLED_BACK"

    # 5. 验证 SSOT 文件已删除（base_version=0）
    assert store.get("doc-rollback") is None


# ---------------------------------------------------------------------------
# T-Canon-P1-05：rollback_proposal 非 APPLIED 状态
# ---------------------------------------------------------------------------

def test_rollback_proposal_non_applied_raises(store, ledger):
    """pending / rejected 提案回滚应抛 PermissionError。"""
    # pending 状态
    store.submit_proposal(
        proposal_id="p-rollback-pending",
        target_doc_id="doc-pending",
        proposed_title="待审核文档",
        proposed_tags=[],
        proposed_body="待审核的文档内容，尚未通过审核",
        ts_ms=1000,
    )
    with pytest.raises(PermissionError, match="cannot be rolled back"):
        store.rollback_proposal("p-rollback-pending")

    # rejected 状态
    store.submit_proposal(
        proposal_id="p-rollback-rejected",
        target_doc_id="doc-rejected",
        proposed_title="被拒绝文档",
        proposed_tags=[],
        proposed_body="被拒绝的文档内容，审核未通过",
        ts_ms=1000,
    )
    store.reject_proposal("p-rollback-rejected", reason="测试拒绝")
    with pytest.raises(PermissionError, match="cannot be rolled back"):
        store.rollback_proposal("p-rollback-rejected")
