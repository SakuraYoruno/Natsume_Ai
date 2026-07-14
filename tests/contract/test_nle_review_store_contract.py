from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3

import pytest

from host_brain.contracts.agent_runtime.review_policy import (
    ReviewItem,
    ReviewResumePhase,
    ReviewStatus,
    ReviewType,
)
from host_brain.persistence.review_repository import (
    REVIEW_RESUME_ALREADY_CONSUMED,
    REVIEW_STORE_SCHEMA_NOT_READY,
    REVIEW_TOKEN_EXPIRED,
    REVIEW_TOKEN_SUBJECT_MISMATCH,
    ReviewStore,
    ReviewStoreError,
)
from host_brain.contracts.agent_runtime.work_order import AgentWorkOrder
from host_brain.agent_runtime.workspace_policy import WorkspacePolicy, WorkspaceResolution


ROOT = Path(__file__).resolve().parents[2]
REVIEW_STORE_MIGRATION = ROOT / "host_brain" / "audit" / "migrations" / "0055_agent_runtime_review_store.sql"


@dataclass
class MutableClock:
    value: int = 1_000

    def __call__(self) -> int:
        return self.value

    def set(self, value: int) -> None:
        self.value = int(value)


def _apply_review_migration(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(REVIEW_STORE_MIGRATION.read_text(encoding="utf-8"))


def _store(tmp_path: Path, clock: MutableClock | None = None) -> ReviewStore:
    db_path = tmp_path / "review-store.sqlite3"
    _apply_review_migration(db_path)
    return ReviewStore(db_path, clock=clock or MutableClock())


def _review(
    review_id: str,
    *,
    review_type: ReviewType = ReviewType.PRE_EXECUTION_APPROVAL,
    checkpoint_id: str = "checkpoint://pre/graph-1/node-1/v0",
    expires_at: int = 60_000,
) -> ReviewItem:
    resume_phase = (
        ReviewResumePhase.PRE_EXECUTION
        if review_type is ReviewType.PRE_EXECUTION_APPROVAL
        else ReviewResumePhase.POST_EXECUTION_ACCEPTANCE
    )
    return ReviewItem(
        review_id=review_id,
        review_type=review_type,
        subject_ref="work-order://graph-1/node-1",
        subject_version=3,
        requester="agent-runtime",
        reason="write side effect requires durable owner review",
        risk_level=4,
        side_effects=("write_files",),
        evidence_refs=("evidence://review/input/1",),
        status=ReviewStatus.PENDING,
        expires_at=expires_at,
        resume_phase=resume_phase,
        diff_ref="diff://workspace/pre/1",
        checkpoint_id=checkpoint_id,
    )


def test_t_nle_10_review_store_requires_migration_and_persists_after_restart(tmp_path: Path) -> None:
    empty_db = tmp_path / "empty.sqlite3"
    sqlite3.connect(str(empty_db)).close()
    with pytest.raises(ReviewStoreError) as missing:
        ReviewStore(empty_db)
    assert missing.value.reason_code == REVIEW_STORE_SCHEMA_NOT_READY

    clock = MutableClock(1_000)
    store = _store(tmp_path, clock)
    created = store.create_review(_review("review-pre-persist"))
    assert created["review"]["status"] == "PENDING"

    reopened = ReviewStore(store.db_path, clock=MutableClock(1_500))
    persisted = reopened.get_review("review-pre-persist")

    assert persisted["review"]["review_id"] == "review-pre-persist"
    assert persisted["review"]["checkpoint_id"] == "checkpoint://pre/graph-1/node-1/v0"
    assert persisted["status_version"] == 0


def test_t_nle_10_pre_resume_is_token_bound_and_one_time(tmp_path: Path) -> None:
    clock = MutableClock(1_000)
    store = _store(tmp_path, clock)
    store.create_review(_review("review-pre-once"))

    approved = store.approve_review(
        "review-pre-once",
        actor_id="owner",
        expected_subject_ref="work-order://graph-1/node-1",
        expected_subject_version=3,
        expected_resume_phase=ReviewResumePhase.PRE_EXECUTION,
        expected_checkpoint_id="checkpoint://pre/graph-1/node-1/v0",
    )
    token = approved["resume_token_ref"]
    reopened = ReviewStore(store.db_path, clock=clock)
    resumed = reopened.resume_review(
        "review-pre-once",
        resume_token_ref=token,
        subject_ref="work-order://graph-1/node-1",
        subject_version=3,
        resume_phase=ReviewResumePhase.PRE_EXECUTION,
        checkpoint_id="checkpoint://pre/graph-1/node-1/v0",
        resume_result_ref="review-resume://review-pre-once/pre/1",
    )

    assert resumed["resume_action"] == "START_EXECUTION"
    assert resumed["replay_side_effects"] is False
    assert resumed["review"]["review"]["status"] == "CONSUMED"
    assert resumed["review"]["review"]["resume_count"] == 1

    with pytest.raises(ReviewStoreError) as duplicate:
        reopened.resume_review(
            "review-pre-once",
            resume_token_ref=token,
            subject_ref="work-order://graph-1/node-1",
            subject_version=3,
            resume_phase=ReviewResumePhase.PRE_EXECUTION,
            checkpoint_id="checkpoint://pre/graph-1/node-1/v0",
            resume_result_ref="review-resume://review-pre-once/pre/duplicate",
        )
    assert duplicate.value.reason_code == REVIEW_RESUME_ALREADY_CONSUMED


def test_t_nle_10_expired_and_cross_subject_resume_are_rejected(tmp_path: Path) -> None:
    clock = MutableClock(1_000)
    store = _store(tmp_path, clock)
    store.create_review(_review("review-expiring", expires_at=2_000))
    expiring_token = store.approve_review("review-expiring", actor_id="owner")["resume_token_ref"]

    clock.set(2_000)
    with pytest.raises(ReviewStoreError) as expired:
        store.resume_review(
            "review-expiring",
            resume_token_ref=expiring_token,
            subject_ref="work-order://graph-1/node-1",
            subject_version=3,
            resume_phase=ReviewResumePhase.PRE_EXECUTION,
            checkpoint_id="checkpoint://pre/graph-1/node-1/v0",
        )
    assert expired.value.reason_code == REVIEW_TOKEN_EXPIRED

    clock.set(3_000)
    store.create_review(_review("review-a", expires_at=60_000))
    store.create_review(_review("review-b", expires_at=60_000))
    token_a = store.approve_review("review-a", actor_id="owner")["resume_token_ref"]
    with pytest.raises(ReviewStoreError) as mismatch:
        store.resume_review(
            "review-b",
            resume_token_ref=token_a,
            subject_ref="work-order://graph-1/node-1",
            subject_version=3,
            resume_phase=ReviewResumePhase.PRE_EXECUTION,
            checkpoint_id="checkpoint://pre/graph-1/node-1/v0",
        )
    assert mismatch.value.reason_code == REVIEW_TOKEN_SUBJECT_MISMATCH


def test_t_nle_10_post_resume_returns_checkpoint_without_side_effect_replay(tmp_path: Path) -> None:
    store = _store(tmp_path, MutableClock(1_000))
    store.create_review(
        _review(
            "review-post",
            review_type=ReviewType.POST_EXECUTION_ACCEPTANCE,
            checkpoint_id="checkpoint://post/graph-1/node-1/v4",
        )
    )
    token = store.approve_review(
        "review-post",
        actor_id="owner",
        expected_resume_phase=ReviewResumePhase.POST_EXECUTION_ACCEPTANCE,
        expected_checkpoint_id="checkpoint://post/graph-1/node-1/v4",
    )["resume_token_ref"]

    resumed = store.resume_review(
        "review-post",
        resume_token_ref=token,
        subject_ref="work-order://graph-1/node-1",
        subject_version=3,
        resume_phase=ReviewResumePhase.POST_EXECUTION_ACCEPTANCE,
        checkpoint_id="checkpoint://post/graph-1/node-1/v4",
        resume_result_ref="review-resume://review-post/post/1",
    )

    assert resumed["resume_action"] == "RETURN_POST_EXECUTION_CHECKPOINT"
    assert resumed["checkpoint_id"] == "checkpoint://post/graph-1/node-1/v4"
    assert resumed["replay_side_effects"] is False


def test_t_nle_10_confirmed_true_cannot_bypass_declared_durable_review(tmp_path: Path) -> None:
    work_order = AgentWorkOrder.from_mapping(
        {
            "work_order_id": "wo-durable-review",
            "domain": "code",
            "run_mode": "worker",
            "objective": "write after durable review",
            "workspace": {"path": str(tmp_path)},
            "allowed_actions": ["write_files"],
            "forbidden_actions": ["modify_secret_files"],
            "approval_policy": {
                "require_confirm": True,
                "confirmed": True,
                "require_review_token": True,
            },
            "review_policy": {
                "review_required": True,
                "durable_review_required": True,
            },
        }
    )

    allowed, reason = WorkspacePolicy().enforce_write_policy(
        work_order,
        WorkspaceResolution(path=tmp_path, source="path"),
    )

    assert allowed is False
    assert reason is not None
    assert "confirmed=true is not sufficient" in reason
