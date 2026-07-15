"""Durable TaskNode review bindings for the governed runtime.

``ReviewStore`` owns review decisions and one-time resume tokens.  This module
adds the missing Task Runtime semantics: every review is deterministically
bound to one immutable graph revision, node, node status version, and (for
post-execution review) attempt.  It deliberately never executes a worker or
mutates a TaskGraph; the coordinator consumes the returned gate decision and
performs the corresponding state transition through ``TaskStore``.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any
from urllib.parse import quote

from host_brain.contracts.agent_runtime.review_policy import (
    ReviewItem,
    ReviewResumePhase,
    ReviewStatus,
    ReviewType,
)
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.core.ids import now_ms
from host_brain.persistence.review_repository import (
    REVIEW_STORE_NOT_FOUND,
    ReviewStore,
    ReviewStoreError,
)


TASK_NODE_REVIEW_BINDING_CONFLICT = "TASK_NODE_REVIEW_BINDING_CONFLICT"
TASK_NODE_REVIEW_INVALID = "TASK_NODE_REVIEW_INVALID"


class TaskNodeReviewError(RuntimeError):
    """Fail-closed error raised when durable review lineage drifts."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or TASK_NODE_REVIEW_INVALID)
        self.detail = str(detail or "task-node review is invalid")
        super().__init__(f"{self.reason_code}: {self.detail}")


@dataclass(frozen=True, slots=True)
class TaskNodeReviewContext:
    """Frozen Task Runtime identity and public evidence for one review gate."""

    work_order_id: str
    root_work_order_id: str
    graph_id: str
    graph_revision: int
    node_id: str
    node_status_version: int
    attempt_id: str | None
    requester: str
    reason: str
    risk_level: int
    side_effects: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    diff_ref: str | None = None

    def validate(self, *, review_type: ReviewType) -> None:
        required = {
            "work_order_id": self.work_order_id,
            "root_work_order_id": self.root_work_order_id,
            "graph_id": self.graph_id,
            "node_id": self.node_id,
            "requester": self.requester,
            "reason": self.reason,
        }
        missing = sorted(name for name, value in required.items() if not _text(value))
        if missing:
            raise TaskNodeReviewError(
                TASK_NODE_REVIEW_INVALID,
                f"review context is missing required fields: {missing}",
            )
        if int(self.graph_revision) < 1:
            raise TaskNodeReviewError(
                TASK_NODE_REVIEW_INVALID,
                "graph_revision must be at least 1",
            )
        if int(self.node_status_version) < 0:
            raise TaskNodeReviewError(
                TASK_NODE_REVIEW_INVALID,
                "node_status_version must be non-negative",
            )
        if int(self.risk_level) < 1 or int(self.risk_level) > 5:
            raise TaskNodeReviewError(
                TASK_NODE_REVIEW_INVALID,
                "risk_level must be between 1 and 5",
            )
        if review_type is ReviewType.POST_EXECUTION_ACCEPTANCE and not _text(self.attempt_id):
            raise TaskNodeReviewError(
                TASK_NODE_REVIEW_INVALID,
                "post-execution review requires attempt_id",
            )


@dataclass(frozen=True, slots=True)
class TaskNodeReviewGate:
    """Coordinator-facing result; no result ever authorizes side-effect replay."""

    disposition: str
    allowed: bool
    review_id: str | None
    review_status: str | None
    review_type: str | None
    resume_phase: str | None
    subject_ref: str | None
    subject_version: int | None
    checkpoint_id: str | None
    replay_side_effects: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "disposition": self.disposition,
            "allowed": self.allowed,
            "review_id": self.review_id,
            "review_status": self.review_status,
            "review_type": self.review_type,
            "resume_phase": self.resume_phase,
            "subject_ref": self.subject_ref,
            "subject_version": self.subject_version,
            "checkpoint_id": self.checkpoint_id,
            "replay_side_effects": self.replay_side_effects,
        }


class TaskNodeReviewRuntime:
    """Idempotently create and inspect reviews bound to durable TaskNodes."""

    def __init__(
        self,
        review_store: ReviewStore,
        *,
        clock: Callable[[], int] = now_ms,
        review_ttl_ms: int = 24 * 60 * 60 * 1_000,
    ) -> None:
        if int(review_ttl_ms) <= 0:
            raise ValueError("review_ttl_ms must be positive")
        self._store = review_store
        self._clock = clock
        self._review_ttl_ms = int(review_ttl_ms)

    def pre_execution_gate(
        self,
        *,
        work_order: WorkOrderV3 | Mapping[str, Any],
        context: TaskNodeReviewContext,
    ) -> TaskNodeReviewGate:
        order = (
            work_order
            if isinstance(work_order, WorkOrderV3)
            else WorkOrderV3.from_mapping(work_order)
        )
        order.validate()
        required = _pre_execution_review_required(order)
        if not required:
            return TaskNodeReviewGate(
                disposition="NOT_REQUIRED",
                allowed=True,
                review_id=None,
                review_status=None,
                review_type=None,
                resume_phase=None,
                subject_ref=None,
                subject_version=None,
                checkpoint_id=None,
            )
        return self._ensure_gate(
            context=context,
            review_type=ReviewType.PRE_EXECUTION_APPROVAL,
        )

    @staticmethod
    def pre_execution_required(
        work_order: WorkOrderV3 | Mapping[str, Any],
    ) -> bool:
        order = (
            work_order
            if isinstance(work_order, WorkOrderV3)
            else WorkOrderV3.from_mapping(work_order)
        )
        order.validate()
        return _pre_execution_review_required(order)

    def post_execution_acceptance_gate(
        self,
        *,
        context: TaskNodeReviewContext,
    ) -> TaskNodeReviewGate:
        return self._ensure_gate(
            context=context,
            review_type=ReviewType.POST_EXECUTION_ACCEPTANCE,
        )

    def lookup_pre_execution_gate(
        self,
        *,
        context: TaskNodeReviewContext,
    ) -> TaskNodeReviewGate | None:
        """Read a prior pre-execution gate without creating a new review."""

        review_type = ReviewType.PRE_EXECUTION_APPROVAL
        context.validate(review_type=review_type)
        binding = _binding(context, review_type=review_type)
        try:
            record = self._store.get_review(str(binding["review_id"]))
        except ReviewStoreError as exc:
            if exc.reason_code == REVIEW_STORE_NOT_FOUND:
                return None
            raise
        self._assert_record_binding(
            record,
            context=context,
            binding=binding,
            review_type=review_type,
        )
        return _gate_from_record(record)

    def _ensure_gate(
        self,
        *,
        context: TaskNodeReviewContext,
        review_type: ReviewType,
    ) -> TaskNodeReviewGate:
        context.validate(review_type=review_type)
        binding = _binding(context, review_type=review_type)
        try:
            record = self._store.get_review(binding["review_id"])
        except ReviewStoreError as exc:
            if exc.reason_code != REVIEW_STORE_NOT_FOUND:
                raise
            record = self._store.create_review(
                _new_review_item(
                    context,
                    binding=binding,
                    review_type=review_type,
                    expires_at=int(self._clock()) + self._review_ttl_ms,
                ),
                work_order_id=context.work_order_id,
                task_graph_id=context.graph_id,
                task_node_id=context.node_id,
            )
        self._assert_record_binding(
            record,
            context=context,
            binding=binding,
            review_type=review_type,
        )
        return _gate_from_record(record)

    @staticmethod
    def review_id(
        context: TaskNodeReviewContext,
        *,
        review_type: ReviewType,
    ) -> str:
        context.validate(review_type=review_type)
        return str(_binding(context, review_type=review_type)["review_id"])

    @staticmethod
    def _assert_record_binding(
        record: Mapping[str, Any],
        *,
        context: TaskNodeReviewContext,
        binding: Mapping[str, Any],
        review_type: ReviewType,
    ) -> None:
        raw_review = record.get("review")
        review = dict(raw_review) if isinstance(raw_review, Mapping) else {}
        expected = {
            "review_id": binding["review_id"],
            "review_type": review_type.value,
            "subject_ref": binding["subject_ref"],
            "subject_version": int(context.node_status_version),
            "resume_phase": binding["resume_phase"],
            "checkpoint_id": binding["checkpoint_id"],
            "requester": context.requester,
            "reason": context.reason,
            "risk_level": int(context.risk_level),
            "side_effects": list(_stable_texts(context.side_effects)),
            "evidence_refs": list(_stable_texts(context.evidence_refs)),
            "diff_ref": _text(context.diff_ref) or None,
        }
        actual = {key: review.get(key) for key in expected}
        outer_expected = {
            "work_order_id": context.work_order_id,
            "task_graph_id": context.graph_id,
            "task_node_id": context.node_id,
        }
        outer_actual = {key: record.get(key) for key in outer_expected}
        if actual != expected or outer_actual != outer_expected:
            raise TaskNodeReviewError(
                TASK_NODE_REVIEW_BINDING_CONFLICT,
                "durable ReviewItem does not exactly match its TaskNode lineage",
            )


def _pre_execution_review_required(order: WorkOrderV3) -> bool:
    review_policy = dict(order.review_policy or {})
    approval_policy = dict(order.approval_policy or {})
    explicit = (
        bool(review_policy.get("review_required"))
        or bool(review_policy.get("durable_review_required"))
        or bool(review_policy.get("require_durable_review"))
        or bool(approval_policy.get("require_review_token"))
        or bool(approval_policy.get("review_id"))
    )
    waiver_mode = _text(review_policy.get("mode")) == "governor_grant_review_waiver"
    if waiver_mode and explicit:
        raise TaskNodeReviewError(
            TASK_NODE_REVIEW_INVALID,
            "review policy cannot require durable review and claim a governor-grant waiver",
        )
    return explicit


def _binding(
    context: TaskNodeReviewContext,
    *,
    review_type: ReviewType,
) -> dict[str, Any]:
    phase = (
        ReviewResumePhase.PRE_EXECUTION
        if review_type is ReviewType.PRE_EXECUTION_APPROVAL
        else ReviewResumePhase.POST_EXECUTION_ACCEPTANCE
    )
    attempt = _text(context.attempt_id) or "not-started"
    identity = {
        "work_order_id": context.work_order_id,
        "root_work_order_id": context.root_work_order_id,
        "graph_id": context.graph_id,
        "graph_revision": int(context.graph_revision),
        "node_id": context.node_id,
        "node_status_version": int(context.node_status_version),
        "attempt_id": attempt,
        "review_type": review_type.value,
        "resume_phase": phase.value,
    }
    digest = hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()
    graph = quote(context.graph_id, safe="")
    node = quote(context.node_id, safe="")
    root = quote(context.root_work_order_id, safe="")
    attempt_ref = quote(attempt, safe="")
    subject_ref = (
        f"task-node://{graph}/{int(context.graph_revision)}/{node}"
        f"?root={root}&attempt={attempt_ref}"
    )
    checkpoint_id = (
        f"task-checkpoint://{graph}/{int(context.graph_revision)}/{node}/"
        f"{phase.value.lower()}?root={root}&attempt={attempt_ref}"
    )
    return {
        "review_id": f"task_review_{digest[:40]}",
        "subject_ref": subject_ref,
        "resume_phase": phase.value,
        "checkpoint_id": checkpoint_id,
    }


def _new_review_item(
    context: TaskNodeReviewContext,
    *,
    binding: Mapping[str, Any],
    review_type: ReviewType,
    expires_at: int,
) -> ReviewItem:
    return ReviewItem(
        review_id=str(binding["review_id"]),
        review_type=review_type,
        subject_ref=str(binding["subject_ref"]),
        subject_version=int(context.node_status_version),
        requester=context.requester,
        reason=context.reason,
        risk_level=int(context.risk_level),
        side_effects=_stable_texts(context.side_effects),
        evidence_refs=_stable_texts(context.evidence_refs),
        status=ReviewStatus.PENDING,
        expires_at=int(expires_at),
        resume_phase=ReviewResumePhase(str(binding["resume_phase"])),
        diff_ref=_text(context.diff_ref) or None,
        checkpoint_id=str(binding["checkpoint_id"]),
    )


def _gate_from_record(record: Mapping[str, Any]) -> TaskNodeReviewGate:
    raw_review = record.get("review")
    review = dict(raw_review) if isinstance(raw_review, Mapping) else {}
    status = _text(review.get("status")).upper()
    if status == ReviewStatus.CONSUMED.value:
        disposition = "RESUME_FROM_CHECKPOINT"
        allowed = True
    elif status in {ReviewStatus.PENDING.value, ReviewStatus.APPROVED.value}:
        disposition = "WAITING_REVIEW"
        allowed = False
    elif status in {
        ReviewStatus.REJECTED.value,
        ReviewStatus.EXPIRED.value,
        ReviewStatus.CANCELLED.value,
    }:
        disposition = "REVIEW_DENIED"
        allowed = False
    else:
        raise TaskNodeReviewError(
            TASK_NODE_REVIEW_INVALID,
            f"unsupported durable review status: {status or 'missing'}",
        )
    return TaskNodeReviewGate(
        disposition=disposition,
        allowed=allowed,
        review_id=_text(review.get("review_id")) or None,
        review_status=status,
        review_type=_text(review.get("review_type")) or None,
        resume_phase=_text(review.get("resume_phase")) or None,
        subject_ref=_text(review.get("subject_ref")) or None,
        subject_version=int(review.get("subject_version", 0)),
        checkpoint_id=_text(review.get("checkpoint_id")) or None,
        replay_side_effects=False,
    )


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _stable_texts(values: Sequence[Any]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_text(value) for value in values if _text(value)))


def _text(value: Any) -> str:
    return str(value or "").strip()


__all__ = [
    "TASK_NODE_REVIEW_BINDING_CONFLICT",
    "TASK_NODE_REVIEW_INVALID",
    "TaskNodeReviewContext",
    "TaskNodeReviewError",
    "TaskNodeReviewGate",
    "TaskNodeReviewRuntime",
]
