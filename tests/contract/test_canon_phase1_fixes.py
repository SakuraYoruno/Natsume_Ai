"""P1 风险修复验证测试（M10-A 方向 A）。

T-Canon-P1-01：proposal_id UNIQUE 约束 — 重复写入同一 proposal_id 应触发 IntegrityError。
T-Canon-P1-02：canon_routes 无循环导入 — 直接 import canon_routes 不应触发 ImportError。
T-Canon-P1-03：set_ledger_provider 注入生效 — 注入后 _get_ledger_from_app 返回注入的 ledger。
T-Canon-P1-04：rollback_proposal 成功 — APPLIED 提案可回滚，SSOT 版本降回 base_version。
T-Canon-P1-05：rollback_proposal 非 APPLIED 状态 — pending/rejected 提案回滚应抛 PermissionError。
"""

from __future__ import annotations

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.memory.canon_world_store import WorldCanonStore
from tests.fakes.canon_runtime import compose_canon_test_runtime


# ---------------------------------------------------------------------------
# 共享 fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def ledger(tmp_path):
    db_path = tmp_path / "test.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="canon-phase1-contract",
    )
    assert migrated.success, migrated.as_dict()
    db = SQLiteLedger(db_path=db_path, initialize_schema=False)
    try:
        yield db
    finally:
        db.close()


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
        assert hasattr(cr, "create_canon_router")
        assert not hasattr(cr, "set_canon_service_provider")
        assert not hasattr(cr, "set_ledger_provider")
    except ImportError as exc:
        pytest.fail(f"canon_routes 导入失败（循环导入）: {exc}")


# ---------------------------------------------------------------------------
# T-Canon-P1-03：set_ledger_provider 注入生效
# ---------------------------------------------------------------------------

def test_set_canon_service_provider_injection(tmp_path):
    """Route injection owns one explicit Canon application boundary."""
    import host_brain.app.canon_routes as cr
    from host_brain.app.host_route_ports import HostRoutePorts

    with compose_canon_test_runtime(
        tmp_path,
        request_id="canon-phase1-provider",
    ) as runtime:
        route_ports = HostRoutePorts(
            canon_service_provider=lambda: runtime.container.application,
        )
        assert cr._get_canon_service(route_ports) is runtime.container.application


# ---------------------------------------------------------------------------
# T-Canon-P1-04：rollback_proposal 成功
# ---------------------------------------------------------------------------

def test_rollback_proposal_success(store, ledger):
    """旧 approve/rollback 写入口均 fail-closed。"""
    # 1. 提交提案
    store.submit_proposal(
        proposal_id="p-rollback-001",
        target_doc_id="doc-rollback",
        proposed_title="回滚测试文档",
        proposed_tags=["test"],
        proposed_body="这是一个用于测试回滚的文档内容",
        ts_ms=1000,
    )

    with pytest.raises(PermissionError, match="CANON_DIRECT_WRITE_FORBIDDEN"):
        store.approve_proposal_with_content(
            "p-rollback-001",
            proposed_title="回滚测试文档",
            proposed_tags=["test"],
            proposed_body="这是一个用于测试回滚的文档内容",
            ts_ms=2000,
        )
    with pytest.raises(PermissionError, match="CANON_DIRECT_WRITE_FORBIDDEN"):
        store.rollback_proposal("p-rollback-001", rollback_reason="测试回滚")

    proposal = ledger.get_proposal("p-rollback-001")
    assert proposal is not None
    assert proposal["status"] == "pending"
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
    with pytest.raises(PermissionError, match="CANON_DIRECT_WRITE_FORBIDDEN"):
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
    with pytest.raises(PermissionError, match="CANON_DIRECT_WRITE_FORBIDDEN"):
        store.rollback_proposal("p-rollback-rejected")
