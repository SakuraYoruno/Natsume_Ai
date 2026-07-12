"""Evidence Plane checkpoints 写入专项测试（M39-K，T-EVP-15~17）。

验证 EvidencePlane.write_checkpoint 的写入正确性和静默降级行为。
"""

from __future__ import annotations

import time

import pytest

from host_brain.integrations.graph_evidence import EvidencePlane
from host_brain.integrations.graph_contract import (
    INTERRUPT_REASON_AIRLOCK,
    INTERRUPT_REASON_HUMAN_REVIEW,
)


def _make_evidence_plane() -> EvidencePlane:
    return EvidencePlane(db_path=":memory:")


# ---------------------------------------------------------------------------
# T-EVP-15：write_checkpoint 将 checkpoint 写入 SQLite checkpoints 表
# ---------------------------------------------------------------------------
def test_evp_15_write_checkpoint_inserts_row():
    """T-EVP-15：write_checkpoint 将 checkpoint 写入 SQLite checkpoints 表"""
    ep = _make_evidence_plane()
    now = int(time.time() * 1000)
    ep.write_checkpoint(
        checkpoint_id="ckp-evp-001",
        request_id="req-evp-001",
        graph_id="graph-evp-001",
        interrupt_reason=INTERRUPT_REASON_AIRLOCK,
        resume_deadline_at=now + 300_000,
    )
    row = ep.query_checkpoint(checkpoint_id="ckp-evp-001")
    assert row is not None
    assert row["checkpoint_id"] == "ckp-evp-001"


# ---------------------------------------------------------------------------
# T-EVP-16：checkpoints 表包含正确的字段（checkpoint_id、interrupt_reason、resume_deadline_at 等）
# ---------------------------------------------------------------------------
def test_evp_16_checkpoint_has_correct_fields():
    """T-EVP-16：checkpoints 表包含正确的字段（checkpoint_id、interrupt_reason、resume_deadline_at 等）"""
    ep = _make_evidence_plane()
    now = int(time.time() * 1000)
    deadline = now + 300_000
    ep.write_checkpoint(
        checkpoint_id="ckp-evp-002",
        request_id="req-evp-002",
        graph_id="graph-evp-002",
        interrupt_reason=INTERRUPT_REASON_HUMAN_REVIEW,
        resume_deadline_at=deadline,
    )
    row = ep.query_checkpoint(checkpoint_id="ckp-evp-002")
    assert row is not None
    assert row["checkpoint_id"] == "ckp-evp-002"
    assert row["request_id"] == "req-evp-002"
    assert row["graph_id"] == "graph-evp-002"
    assert row["interrupt_reason"] == INTERRUPT_REASON_HUMAN_REVIEW
    assert row["resume_deadline_at"] == deadline
    assert isinstance(row["created_at_ms"], int)


# ---------------------------------------------------------------------------
# T-EVP-17：write_checkpoint 写入失败时静默降级
# ---------------------------------------------------------------------------
def test_evp_17_write_checkpoint_silent_degradation(monkeypatch: pytest.MonkeyPatch):
    """T-EVP-17：write_checkpoint 写入失败时静默降级（不抛出异常）"""
    ep = _make_evidence_plane()

    def _bad_write(**_kwargs):
        raise RuntimeError("模拟数据库连接失败")

    assert ep._repository is not None
    monkeypatch.setattr(ep._repository, "save_checkpoint", _bad_write)

    # 不应抛出异常
    ep.write_checkpoint(
        checkpoint_id="ckp-evp-fail",
        request_id="req-fail",
        graph_id="g",
        interrupt_reason=INTERRUPT_REASON_AIRLOCK,
        resume_deadline_at=int(time.time() * 1000) + 300_000,
    )
    # 查询返回 None
    assert ep.query_checkpoint(checkpoint_id="ckp-evp-fail") is None
