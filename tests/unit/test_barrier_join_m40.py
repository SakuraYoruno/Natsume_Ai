"""barrier_join() 核心专项测试（M40-J，T-BJ-01~10）。

验证并行分支 fan-in 校验的正确性。
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from host_brain.integrations.graph_branch import (
    AuditTicket,
    BarrierJoinResult,
    barrier_join,
    build_audit_ticket,
)
from host_brain.integrations.graph_contract import (
    BARRIER_JOIN_POLICY_PARTIAL,
    BARRIER_JOIN_POLICY_STRICT,
    BARRIER_JOIN_STATUS_PASSED,
    BARRIER_JOIN_STATUS_FAILED,
)


def _make_manifest(branches: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "branches": branches,
        "total_count": len(branches),
        "success_count": sum(1 for b in branches if b.get("status") == "OK"),
        "fail_count": sum(1 for b in branches if b.get("status") != "OK"),
        "committed_at_ms": int(time.time() * 1000),
    }


def _branch(branch_id: str, audit_commit: str = "", status: str = "OK") -> dict[str, Any]:
    output: dict[str, Any] = {}
    if audit_commit:
        output["audit_commit"] = audit_commit
    return {
        "branch_id": branch_id,
        "status": status,
        "output": output,
        "exec_duration_ms": 10,
        "error": None,
    }


# ---------------------------------------------------------------------------
# T-BJ-01：所有分支有 audit_commit 时 passed=True
# ---------------------------------------------------------------------------
def test_bj_01_all_branches_with_audit_commit_passed():
    """T-BJ-01：所有分支有 audit_commit 时 passed=True"""
    manifest = _make_manifest([
        _branch("b1", audit_commit="commit-hash-001"),
        _branch("b2", audit_commit="commit-hash-002"),
    ])
    result = barrier_join(branch_manifest=manifest)
    assert result.passed is True
    assert result.failed_branches == []


# ---------------------------------------------------------------------------
# T-BJ-02：任一分支缺 audit_commit 时 passed=False
# ---------------------------------------------------------------------------
def test_bj_02_any_branch_missing_audit_commit_failed():
    """T-BJ-02：任一分支缺 audit_commit 时 passed=False"""
    manifest = _make_manifest([
        _branch("b1", audit_commit="commit-hash-001"),
        _branch("b2", audit_commit=""),  # 缺 audit_commit
    ])
    result = barrier_join(branch_manifest=manifest)
    assert result.passed is False


# ---------------------------------------------------------------------------
# T-BJ-03：failed_branches 包含所有缺证据的分支 ID
# ---------------------------------------------------------------------------
def test_bj_03_failed_branches_contains_missing_ids():
    """T-BJ-03：failed_branches 包含所有缺证据的分支 ID"""
    manifest = _make_manifest([
        _branch("b1", audit_commit=""),
        _branch("b2", audit_commit="commit-hash-002"),
        _branch("b3", audit_commit=""),
    ])
    result = barrier_join(branch_manifest=manifest)
    assert result.passed is False
    assert "b1" in result.failed_branches
    assert "b3" in result.failed_branches
    assert "b2" not in result.failed_branches


# ---------------------------------------------------------------------------
# T-BJ-04：空 branch_manifest 时 passed=True（无分支无需校验）
# ---------------------------------------------------------------------------
def test_bj_04_empty_manifest_passed():
    """T-BJ-04：空 branch_manifest 时 passed=True（无分支无需校验）"""
    result = barrier_join(branch_manifest=None)
    assert result.passed is True

    result2 = barrier_join(branch_manifest={})
    assert result2.passed is True

    result3 = barrier_join(branch_manifest={"branches": []})
    assert result3.passed is True


# ---------------------------------------------------------------------------
# T-BJ-05：join_policy=strict 时任一失败则 passed=False
# ---------------------------------------------------------------------------
def test_bj_05_strict_policy_any_failure_fails():
    """T-BJ-05：join_policy=strict 时任一失败则 passed=False"""
    manifest = _make_manifest([
        _branch("b1", audit_commit="commit-001"),
        _branch("b2", audit_commit=""),  # 缺 audit_commit
    ])
    result = barrier_join(branch_manifest=manifest, join_policy=BARRIER_JOIN_POLICY_STRICT)
    assert result.passed is False
    assert result.join_policy_ref == ""  # strict 模式不记录 join_policy_ref


# ---------------------------------------------------------------------------
# T-BJ-06：join_policy=partial 时记录 join_policy_ref
# ---------------------------------------------------------------------------
def test_bj_06_partial_policy_records_join_policy_ref():
    """T-BJ-06：join_policy=partial 时记录 join_policy_ref"""
    manifest = _make_manifest([
        _branch("b1", audit_commit="commit-001"),
        _branch("b2", audit_commit=""),  # 缺 audit_commit
    ])
    result = barrier_join(branch_manifest=manifest, join_policy=BARRIER_JOIN_POLICY_PARTIAL)
    assert result.passed is False
    assert result.join_policy_ref != ""  # partial 模式记录 join_policy_ref
    assert "partial" in result.join_policy_ref


# ---------------------------------------------------------------------------
# T-BJ-07：build_audit_ticket 返回有效 AuditTicket
# ---------------------------------------------------------------------------
def test_bj_07_build_audit_ticket_returns_valid_ticket():
    """T-BJ-07：build_audit_ticket 返回有效 AuditTicket"""
    ticket = build_audit_ticket(branch_id="b1", input_payload={"key": "value"})
    assert isinstance(ticket, AuditTicket)
    assert ticket.branch_id == "b1"
    assert ticket.input_digest != ""
    assert ticket.issued_at_ms > 0


# ---------------------------------------------------------------------------
# T-BJ-08：AuditTicket.audit_commit 非空
# ---------------------------------------------------------------------------
def test_bj_08_audit_ticket_has_nonempty_audit_commit():
    """T-BJ-08：AuditTicket.audit_commit 非空"""
    ticket = build_audit_ticket(branch_id="b2", input_payload={"x": 1})
    assert ticket.audit_commit != ""
    assert len(ticket.audit_commit) == 64  # SHA-256 hex


# ---------------------------------------------------------------------------
# T-BJ-09：output_digest 不一致时 passed=False（若提供 audit_tickets）
# ---------------------------------------------------------------------------
def test_bj_09_output_digest_mismatch_fails():
    """T-BJ-09：output_digest 不一致时 passed=False（若提供 audit_tickets）"""
    ticket = AuditTicket(
        branch_id="b1",
        audit_commit="commit-001",
        input_digest="input-digest-001",
        output_digest="expected-output-digest-001",  # 期望的 output_digest
        issued_at_ms=int(time.time() * 1000),
    )
    # 分支输出与期望的 output_digest 不一致
    manifest = _make_manifest([
        _branch("b1", audit_commit="commit-001"),
    ])
    result = barrier_join(branch_manifest=manifest, audit_tickets=[ticket])
    # output_digest 不一致（实际计算的 digest 与 ticket.output_digest 不同）
    assert result.passed is False
    assert "b1" in result.failed_branches


# ---------------------------------------------------------------------------
# T-BJ-10：output_digest 一致时 passed=True（若提供 audit_tickets）
# ---------------------------------------------------------------------------
def test_bj_10_output_digest_match_passed():
    """T-BJ-10：output_digest 一致时 passed=True（若提供 audit_tickets）"""
    import hashlib
    import json

    branch_output = {"audit_commit": "commit-001", "result": "ok"}
    output_json = json.dumps(branch_output, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    expected_digest = hashlib.sha256(output_json.encode("utf-8")).hexdigest()

    ticket = AuditTicket(
        branch_id="b1",
        audit_commit="commit-001",
        input_digest="input-digest-001",
        output_digest=expected_digest,  # 与实际输出一致
        issued_at_ms=int(time.time() * 1000),
    )
    manifest = _make_manifest([
        {
            "branch_id": "b1",
            "status": "OK",
            "output": branch_output,
            "exec_duration_ms": 10,
            "error": None,
        }
    ])
    result = barrier_join(branch_manifest=manifest, audit_tickets=[ticket])
    assert result.passed is True
