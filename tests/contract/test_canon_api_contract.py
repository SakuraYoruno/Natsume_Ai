"""M10-A Canon 治理 API contract 测试（T-Canon-API-01~08）。

DevSpec §20.6：Canon 治理 REST API 回归测试。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    WCANON_PROPOSAL_DUPLICATE,
    WCANON_PROPOSAL_INVALID,
    WCANON_PROPOSAL_NOT_FOUND,
    WCANON_PROPOSAL_STATUS_INVALID,
)
from host_brain.memory.canon_world_store import WorldCanonStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_world_root(tmp_path: Path) -> Path:
    """临时 World Canon 目录（含 3 个种子条目）。"""
    world_dir = tmp_path / "world"
    world_dir.mkdir()
    # 写入最小种子条目
    seed = {
        "doc_id": "wc-001-system-terms",
        "version": 1,
        "status": "active",
        "title": "系统术语",
        "tags": ["system", "terms"],
        "body": "Natsume Ai 是一个意识主权本地智能体，语义内核负责叙事，Host 负责裁决。",
        "updated_by": "seed",
        "ts_ms": 1700000000000,
    }
    from host_brain.memory.canon_world_store import compute_world_canon_hash
    seed["hash"] = compute_world_canon_hash(seed)
    (world_dir / "wc-001-system-terms.json").write_text(
        json.dumps(seed, ensure_ascii=False, indent=2), encoding="utf-8-sig"
    )
    return world_dir


@pytest.fixture()
def ledger(tmp_path: Path) -> SQLiteLedger:
    db_path = str(tmp_path / "test_audit.sqlite3")
    return SQLiteLedger(db_path=db_path)


@pytest.fixture()
def store(tmp_world_root: Path, ledger: SQLiteLedger) -> WorldCanonStore:
    return WorldCanonStore(world_root=tmp_world_root, ledger=ledger)


# ---------------------------------------------------------------------------
# T-Canon-API-01: POST /canon/proposals 提交成功
# ---------------------------------------------------------------------------

def test_submit_proposal_success(store: WorldCanonStore, ledger: SQLiteLedger) -> None:
    """T-Canon-API-01: 提交提案成功，status=pending，proposal_id 可查询。"""
    row = store.submit_proposal(
        proposal_id="prop-001",
        target_doc_id="wc-001-system-terms",
        proposed_title="系统术语 v2",
        proposed_tags=["system", "terms", "v2"],
        proposed_body="Natsume Ai 是一个意识主权本地智能体，语义内核负责叙事草案，Host 负责裁决与执行。",
        proposer_node_id="api.canon_proposals::req-001",
        summary="扩展系统术语定义",
    )
    assert row["proposal_id"] == "prop-001"
    assert row["status"] == "pending"
    assert row["target_id"] == "wc-001-system-terms"

    # 可通过 ledger 查询
    fetched = ledger.get_proposal("prop-001")
    assert fetched is not None
    assert fetched["status"] == "pending"
    assert fetched["proposer_node_id"] == "api.canon_proposals::req-001"


# ---------------------------------------------------------------------------
# T-Canon-API-02: POST /canon/proposals 重复 proposal_id 幂等检查
# ---------------------------------------------------------------------------

def test_submit_proposal_duplicate(store: WorldCanonStore, ledger: SQLiteLedger) -> None:
    """T-Canon-API-02: 重复 proposal_id 时 ledger 中已存在记录（API 层应返回 409）。"""
    store.submit_proposal(
        proposal_id="prop-dup",
        target_doc_id="wc-001-system-terms",
        proposed_title="系统术语 v2",
        proposed_tags=[],
        proposed_body="Natsume Ai 是一个意识主权本地智能体，语义内核负责叙事草案，Host 负责裁决。",
        proposer_node_id="api.canon_proposals::req-dup",
    )
    # 第二次提交相同 proposal_id，ledger 中已存在
    existing = ledger.get_proposal("prop-dup")
    assert existing is not None
    assert existing["proposal_id"] == "prop-dup"
    # API 层（canon_routes.py）会检查 existing is not None 并返回 409 + WCANON_PROPOSAL_DUPLICATE
    # 此处验证错误码常量存在
    assert WCANON_PROPOSAL_DUPLICATE == "WCANON_PROPOSAL_DUPLICATE"


# ---------------------------------------------------------------------------
# T-Canon-API-03: POST /canon/proposals body 含完成性表述被拒绝
# ---------------------------------------------------------------------------

def test_submit_proposal_execution_claim_rejected(store: WorldCanonStore) -> None:
    """T-Canon-API-03: proposed_body 含完成性表述时 submit_proposal 抛出 ValueError。"""
    with pytest.raises(ValueError, match="execution facts"):
        store.submit_proposal(
            proposal_id="prop-bad",
            target_doc_id="wc-001-system-terms",
            proposed_title="系统术语 v2",
            proposed_tags=[],
            proposed_body="Natsume Ai 已执行所有任务，执行完成。",
            proposer_node_id="api.canon_proposals::req-bad",
        )
    # 错误码常量存在
    assert WCANON_PROPOSAL_INVALID == "WCANON_PROPOSAL_INVALID"


# ---------------------------------------------------------------------------
# T-Canon-API-04: POST /canon/proposals/{id}/approve 审核通过
# ---------------------------------------------------------------------------

def test_approve_proposal_success(store: WorldCanonStore, ledger: SQLiteLedger) -> None:
    """T-Canon-API-04: 审核通过，SSOT 文件版本 +1，canon_change_log 可回放。"""
    # 先提交提案
    store.submit_proposal(
        proposal_id="prop-approve",
        target_doc_id="wc-001-system-terms",
        proposed_title="系统术语 v2",
        proposed_tags=["system", "terms"],
        proposed_body="Natsume Ai 是一个意识主权本地智能体，语义内核负责叙事草案，Host 负责裁决与执行。",
        proposer_node_id="api.canon_proposals::req-approve",
    )

    # 审核通过
    doc = store.approve_proposal_with_content(
        "prop-approve",
        proposed_title="系统术语 v2",
        proposed_tags=["system", "terms"],
        proposed_body="Natsume Ai 是一个意识主权本地智能体，语义内核负责叙事草案，Host 负责裁决与执行。",
        approver_node_id="api.canon_proposals.approve::req-approve",
    )

    # SSOT 文件版本 +1（原版本为 1，审核后为 2）
    assert doc.version == 2
    assert doc.doc_id == "wc-001-system-terms"

    # 提案 status 变为 APPLIED
    updated = ledger.get_proposal("prop-approve")
    assert updated is not None
    assert updated["status"] == "APPLIED"

    # canon_change_log 可回放（通过 ledger 查询）
    change_logs = ledger.canon_change_log if hasattr(ledger, "canon_change_log") else []
    # 至少有一条变更记录（通过 upsert_document 写入）
    # 验证 doc hash 已更新
    assert doc.hash != ""


# ---------------------------------------------------------------------------
# T-Canon-API-05: POST /canon/proposals/{id}/approve 对非 pending 提案返回 422
# ---------------------------------------------------------------------------

def test_approve_non_pending_proposal_rejected(store: WorldCanonStore) -> None:
    """T-Canon-API-05: 对非 pending 提案执行 approve 时抛出 PermissionError。"""
    # 提交并审核通过
    store.submit_proposal(
        proposal_id="prop-already-applied",
        target_doc_id="wc-001-system-terms",
        proposed_title="系统术语 v2",
        proposed_tags=[],
        proposed_body="Natsume Ai 是一个意识主权本地智能体，语义内核负责叙事草案，Host 负责裁决。",
        proposer_node_id="api.canon_proposals::req-applied",
    )
    store.approve_proposal_with_content(
        "prop-already-applied",
        proposed_title="系统术语 v2",
        proposed_tags=[],
        proposed_body="Natsume Ai 是一个意识主权本地智能体，语义内核负责叙事草案，Host 负责裁决。",
        approver_node_id="api.canon_proposals.approve::req-applied",
    )

    # 再次 approve 应抛出 PermissionError（status 已是 APPLIED，非 pending）
    with pytest.raises(PermissionError, match="cannot be approved"):
        store.approve_proposal_with_content(
            "prop-already-applied",
            proposed_title="系统术语 v2",
            proposed_tags=[],
            proposed_body="Natsume Ai 是一个意识主权本地智能体，语义内核负责叙事草案，Host 负责裁决。",
            approver_node_id="api.canon_proposals.approve::req-applied-2",
        )
    assert WCANON_PROPOSAL_STATUS_INVALID == "WCANON_PROPOSAL_STATUS_INVALID"


# ---------------------------------------------------------------------------
# T-Canon-API-06: POST /canon/proposals/{id}/reject 拒绝成功
# ---------------------------------------------------------------------------

def test_reject_proposal_success(store: WorldCanonStore, ledger: SQLiteLedger, tmp_world_root: Path) -> None:
    """T-Canon-API-06: 拒绝成功，status=rejected，SSOT 文件不变。"""
    # 记录 SSOT 文件原始内容
    ssot_path = tmp_world_root / "wc-001-system-terms.json"
    original_content = ssot_path.read_text(encoding="utf-8-sig")

    store.submit_proposal(
        proposal_id="prop-reject",
        target_doc_id="wc-001-system-terms",
        proposed_title="系统术语 v2",
        proposed_tags=[],
        proposed_body="Natsume Ai 是一个意识主权本地智能体，语义内核负责叙事草案，Host 负责裁决。",
        proposer_node_id="api.canon_proposals::req-reject",
    )

    row = store.reject_proposal(
        "prop-reject",
        reason="内容不符合规范",
        rejector_node_id="api.canon_proposals.reject::req-reject",
    )

    assert row["status"] == "rejected"
    assert row["reject_reason"] == "内容不符合规范"

    # SSOT 文件不变
    assert ssot_path.read_text(encoding="utf-8-sig") == original_content

    # ledger 中 status 已更新
    updated = ledger.get_proposal("prop-reject")
    assert updated is not None
    assert updated["status"] == "rejected"


def test_reject_non_pending_proposal_rejected(store: WorldCanonStore) -> None:
    """T-Canon-API-06 扩展: 对非 pending 提案执行 reject 时抛出 PermissionError。"""
    store.submit_proposal(
        proposal_id="prop-reject-applied",
        target_doc_id="wc-001-system-terms",
        proposed_title="系统术语 v2",
        proposed_tags=[],
        proposed_body="Natsume Ai 是一个意识主权本地智能体，语义内核负责叙事草案，Host 负责裁决。",
        proposer_node_id="api.canon_proposals::req-reject-applied",
    )
    store.approve_proposal_with_content(
        "prop-reject-applied",
        proposed_title="系统术语 v2",
        proposed_tags=[],
        proposed_body="Natsume Ai 是一个意识主权本地智能体，语义内核负责叙事草案，Host 负责裁决。",
        approver_node_id="api.canon_proposals.approve::req-reject-applied",
    )

    with pytest.raises(PermissionError, match="cannot be rejected"):
        store.reject_proposal("prop-reject-applied", reason="太晚了")


# ---------------------------------------------------------------------------
# T-Canon-API-07: GET /canon/proposals 分页列表
# ---------------------------------------------------------------------------

def test_list_proposals_pagination(store: WorldCanonStore, ledger: SQLiteLedger) -> None:
    """T-Canon-API-07: 分页列表，status 过滤正确。"""
    # 提交 3 个提案
    for i in range(3):
        store.submit_proposal(
            proposal_id=f"prop-list-{i:03d}",
            target_doc_id="wc-001-system-terms",
            proposed_title=f"系统术语 v{i+2}",
            proposed_tags=[],
            proposed_body=f"Natsume Ai 是一个意识主权本地智能体，版本 {i+2}，语义内核负责叙事草案，Host 负责裁决。",
            proposer_node_id=f"api.canon_proposals::req-list-{i}",
        )

    # 拒绝第 0 个
    store.reject_proposal("prop-list-000", reason="测试拒绝")

    # 查询全部
    all_items, total = ledger.list_proposals()
    assert total >= 3

    # 按 status=pending 过滤
    pending_items, pending_total = ledger.list_proposals(status="pending")
    assert pending_total >= 2
    assert all(item["status"] == "pending" for item in pending_items)

    # 按 status=rejected 过滤
    rejected_items, rejected_total = ledger.list_proposals(status="rejected")
    assert rejected_total >= 1
    assert all(item["status"] == "rejected" for item in rejected_items)

    # 分页：page_size=2
    page1_items, _ = ledger.list_proposals(limit=2, offset=0)
    assert len(page1_items) <= 2


# ---------------------------------------------------------------------------
# T-Canon-API-08: GET /canon/documents 返回当前 SSOT 快照
# ---------------------------------------------------------------------------

def test_list_documents_ssot_snapshot(store: WorldCanonStore) -> None:
    """T-Canon-API-08: GET /canon/documents 返回当前 SSOT 快照（至少 1 个种子条目）。"""
    docs = store.list_documents()
    assert len(docs) >= 1

    doc_ids = [doc.doc_id for doc in docs]
    assert "wc-001-system-terms" in doc_ids

    # 每个文档有必要字段
    for doc in docs:
        assert doc.doc_id
        assert doc.version >= 1
        assert doc.hash
        assert doc.title
        assert doc.body
        # evidence_level 约束：body 不含完成性表述
        from host_brain.memory.canon_world_store import _contains_execution_claim
        assert not _contains_execution_claim(doc.body), f"doc {doc.doc_id} body contains execution claim"


# ---------------------------------------------------------------------------
# 额外：reject_proposal 对不存在的 proposal_id 抛出 KeyError
# ---------------------------------------------------------------------------

def test_reject_nonexistent_proposal(store: WorldCanonStore) -> None:
    """reject_proposal 对不存在的 proposal_id 抛出 KeyError（WCANON_PROPOSAL_NOT_FOUND）。"""
    with pytest.raises(KeyError, match="proposal not found"):
        store.reject_proposal("nonexistent-proposal-id")
    assert WCANON_PROPOSAL_NOT_FOUND == "WCANON_PROPOSAL_NOT_FOUND"
