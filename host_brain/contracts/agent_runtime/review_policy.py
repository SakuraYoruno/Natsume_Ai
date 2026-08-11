"""ReviewItem contract plus the unchanged Agent runtime v2 policy helper."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from host_brain.contracts.agent_runtime.contract_errors import (
    CONTRACT_FIELD_INVALID,
    REVIEW_ITEM_INVALID,
    REVIEW_TRANSITION_INVALID,
    ContractViolation,
    require_version,
)
from host_brain.contracts.agent_runtime.work_order import AgentWorkOrder, WorkOrderV3


REVIEW_ITEM_SCHEMA_VERSION = "Natsume.agent.review_item.v1"
NODE_REVIEW_POLICY_SCHEMA_VERSION = "Natsume.agent.node_review_policy.v1"
NODE_REVIEW_POLICIES_KEY = "node_review_policies"


class ReviewType(StrEnum):
    PRE_EXECUTION_APPROVAL = "PRE_EXECUTION_APPROVAL"
    POST_EXECUTION_ACCEPTANCE = "POST_EXECUTION_ACCEPTANCE"


class ReviewStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CONSUMED = "CONSUMED"
    CANCELLED = "CANCELLED"


class ReviewResumePhase(StrEnum):
    PRE_EXECUTION = "PRE_EXECUTION"
    POST_EXECUTION_ACCEPTANCE = "POST_EXECUTION_ACCEPTANCE"


_REVIEW_TRANSITIONS: dict[ReviewStatus, frozenset[ReviewStatus]] = {
    ReviewStatus.PENDING: frozenset(
        {
            ReviewStatus.APPROVED,
            ReviewStatus.REJECTED,
            ReviewStatus.EXPIRED,
            ReviewStatus.CANCELLED,
        }
    ),
    ReviewStatus.APPROVED: frozenset(
        {ReviewStatus.CONSUMED, ReviewStatus.EXPIRED, ReviewStatus.CANCELLED}
    ),
    ReviewStatus.REJECTED: frozenset(),
    ReviewStatus.EXPIRED: frozenset(),
    ReviewStatus.CONSUMED: frozenset(),
    ReviewStatus.CANCELLED: frozenset(),
}


def validate_review_transition(
    current: ReviewStatus | str,
    target: ReviewStatus | str,
) -> ReviewStatus:
    try:
        current_status = ReviewStatus(str(current))
        target_status = ReviewStatus(str(target))
    except ValueError as exc:
        raise ContractViolation(REVIEW_TRANSITION_INVALID, "unknown ReviewItem status") from exc
    if target_status not in _REVIEW_TRANSITIONS[current_status]:
        raise ContractViolation(
            REVIEW_TRANSITION_INVALID,
            f"illegal ReviewItem transition: {current_status.value}->{target_status.value}",
        )
    return target_status


@dataclass(frozen=True, slots=True)
class ReviewItem:
    review_id: str
    review_type: ReviewType
    subject_ref: str
    subject_version: int
    requester: str
    reason: str
    risk_level: int
    side_effects: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    status: ReviewStatus
    expires_at: int
    resume_phase: ReviewResumePhase
    diff_ref: str | None = None
    checkpoint_id: str | None = None
    decision_actor: str | None = None
    token_ref: str | None = None
    resume_count: int = 0
    resume_result_ref: str | None = None
    schema_version: str = REVIEW_ITEM_SCHEMA_VERSION

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "ReviewItem":
        data = dict(payload or {})
        require_version(
            data.get("schema_version"),
            expected=REVIEW_ITEM_SCHEMA_VERSION,
            contract_name="ReviewItem",
        )

        def enum_value(enum_type: type[StrEnum], key: str, default: str = "") -> StrEnum:
            raw = str(data.get(key) or default).strip().upper()
            try:
                return enum_type(raw)
            except ValueError as exc:
                raise ContractViolation(REVIEW_ITEM_INVALID, f"unsupported ReviewItem.{key}: {raw}") from exc

        def integer(key: str, *, minimum: int = 0) -> int:
            try:
                value = int(data.get(key))
            except (TypeError, ValueError) as exc:
                raise ContractViolation(CONTRACT_FIELD_INVALID, f"{key} must be an integer") from exc
            if value < minimum:
                raise ContractViolation(CONTRACT_FIELD_INVALID, f"{key} must be at least {minimum}")
            return value

        def optional_text(key: str) -> str | None:
            value = str(data.get(key) or "").strip()
            return value or None

        def text_tuple(key: str) -> tuple[str, ...]:
            value = data.get(key)
            return tuple(
                str(item).strip()
                for item in (value if isinstance(value, list) else [])
                if str(item).strip()
            )

        review = cls(
            review_id=str(data.get("review_id") or "").strip(),
            review_type=enum_value(ReviewType, "review_type"),
            subject_ref=str(data.get("subject_ref") or "").strip(),
            subject_version=integer("subject_version"),
            requester=str(data.get("requester") or "").strip(),
            reason=str(data.get("reason") or "").strip(),
            risk_level=integer("risk_level", minimum=1),
            side_effects=text_tuple("side_effects"),
            evidence_refs=text_tuple("evidence_refs"),
            status=enum_value(ReviewStatus, "status", "PENDING"),
            expires_at=integer("expires_at"),
            resume_phase=enum_value(ReviewResumePhase, "resume_phase"),
            diff_ref=optional_text("diff_ref"),
            checkpoint_id=optional_text("checkpoint_id"),
            decision_actor=optional_text("decision_actor"),
            token_ref=optional_text("token_ref"),
            resume_count=integer("resume_count"),
            resume_result_ref=optional_text("resume_result_ref"),
        )
        review.validate()
        return review

    def validate(self) -> None:
        if self.schema_version != REVIEW_ITEM_SCHEMA_VERSION:
            raise ContractViolation(REVIEW_ITEM_INVALID, "ReviewItem schema version is invalid")
        if not self.review_id or not self.subject_ref or not self.requester or not self.reason:
            raise ContractViolation(
                REVIEW_ITEM_INVALID,
                "review_id, subject_ref, requester, and reason are required",
            )
        if self.risk_level < 1 or self.risk_level > 5:
            raise ContractViolation(REVIEW_ITEM_INVALID, "risk_level must be between 1 and 5")
        expected_phase = (
            ReviewResumePhase.PRE_EXECUTION
            if self.review_type is ReviewType.PRE_EXECUTION_APPROVAL
            else ReviewResumePhase.POST_EXECUTION_ACCEPTANCE
        )
        if self.resume_phase is not expected_phase:
            raise ContractViolation(
                REVIEW_ITEM_INVALID,
                "review_type and resume_phase do not match",
            )
        if self.status in {ReviewStatus.APPROVED, ReviewStatus.CONSUMED}:
            if not self.decision_actor or not self.token_ref:
                raise ContractViolation(
                    REVIEW_ITEM_INVALID,
                    "approved ReviewItem requires decision_actor and token_ref",
                )
        if self.status is ReviewStatus.CONSUMED and self.resume_count < 1:
            raise ContractViolation(
                REVIEW_ITEM_INVALID,
                "consumed ReviewItem requires a positive resume_count",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "review_id": self.review_id,
            "review_type": self.review_type.value,
            "subject_ref": self.subject_ref,
            "subject_version": self.subject_version,
            "requester": self.requester,
            "reason": self.reason,
            "risk_level": self.risk_level,
            "side_effects": list(self.side_effects),
            "evidence_refs": list(self.evidence_refs),
            "diff_ref": self.diff_ref,
            "checkpoint_id": self.checkpoint_id,
            "status": self.status.value,
            "expires_at": self.expires_at,
            "decision_actor": self.decision_actor,
            "token_ref": self.token_ref,
            "resume_phase": self.resume_phase.value,
            "resume_count": self.resume_count,
            "resume_result_ref": self.resume_result_ref,
        }


class ReviewPolicy:
    def durable_review_required(self, work_order: AgentWorkOrder | WorkOrderV3) -> bool:
        if bool(work_order.review_policy.get("durable_review_required")):
            return True
        if bool(work_order.review_policy.get("require_durable_review")):
            return True
        if bool(work_order.approval_policy.get("require_review_token")):
            return True
        if bool(work_order.approval_policy.get("review_id")):
            return True
        return False

    def review_required(
        self,
        work_order: AgentWorkOrder | WorkOrderV3,
        *,
        node_id: str | None = None,
    ) -> bool:
        if isinstance(work_order, WorkOrderV3):
            return pre_execution_review_required(work_order, node_id=node_id)
        if "review_required" in work_order.review_policy:
            return bool(work_order.review_policy.get("review_required"))
        if self.durable_review_required(work_order):
            return True
        return bool(work_order.requires_write)


def pre_execution_review_required(
    work_order: WorkOrderV3,
    *,
    node_id: str | None = None,
) -> bool:
    """Resolve graph-wide and immutable per-node pre-execution Review policy.

    Existing WorkOrders keep their graph-wide semantics.  A planning compiler
    may additionally freeze ``node_review_policies`` for every node in one
    immutable graph revision.  Once that map exists, a missing node entry or
    malformed boolean fails closed instead of silently inheriting a waiver.
    """

    if not isinstance(work_order, WorkOrderV3):
        raise ContractViolation(
            REVIEW_ITEM_INVALID,
            "node-scoped Review policy requires WorkOrderV3",
        )
    policy = dict(work_order.review_policy or {})
    approval = dict(work_order.approval_policy or {})
    graph_required = any(
        _exact_policy_flag(policy, field_name)
        for field_name in (
            "review_required",
            "durable_review_required",
            "require_durable_review",
        )
        if field_name in policy
    ) or any(
        _exact_policy_flag(approval, field_name)
        for field_name in ("require_review_token",)
        if field_name in approval
    )
    if "review_id" in approval:
        review_id = approval.get("review_id")
        if not isinstance(review_id, str) or not review_id.strip():
            raise ContractViolation(
                REVIEW_ITEM_INVALID,
                "review_id must be a non-empty string",
            )
        graph_required = True
    raw_node_policies = policy.get(NODE_REVIEW_POLICIES_KEY)
    if raw_node_policies is None:
        return graph_required
    if not isinstance(raw_node_policies, Mapping) or not raw_node_policies:
        raise ContractViolation(
            REVIEW_ITEM_INVALID,
            f"{NODE_REVIEW_POLICIES_KEY} must be a non-empty object",
        )
    if node_id is None:
        return graph_required or any(
            _node_policy_required(value, node_id=str(key or "").strip())
            for key, value in raw_node_policies.items()
        )
    resolved_node_id = str(node_id or "").strip()
    if not resolved_node_id or resolved_node_id not in raw_node_policies:
        raise ContractViolation(
            REVIEW_ITEM_INVALID,
            "node-scoped Review policy does not cover the requested TaskNode",
        )
    return graph_required or _node_policy_required(
        raw_node_policies[resolved_node_id],
        node_id=resolved_node_id,
    )


def _node_policy_required(value: Any, *, node_id: str) -> bool:
    if not node_id or not isinstance(value, Mapping):
        raise ContractViolation(
            REVIEW_ITEM_INVALID,
            "node Review policy entry is invalid",
        )
    entry = dict(value)
    if entry.get("schema_version") != NODE_REVIEW_POLICY_SCHEMA_VERSION:
        raise ContractViolation(
            REVIEW_ITEM_INVALID,
            f"TaskNode {node_id} has an unsupported Review policy schema",
        )
    required = _exact_policy_flag(entry, "review_required")
    reason_code = str(entry.get("reason_code") or "").strip()
    source_ref = str(entry.get("source_ref") or "").strip()
    if not reason_code or "://" not in source_ref:
        raise ContractViolation(
            REVIEW_ITEM_INVALID,
            f"TaskNode {node_id} Review policy lacks immutable provenance",
        )
    return required


def _exact_policy_flag(policy: Mapping[str, Any], field_name: str) -> bool:
    value = policy.get(field_name)
    if not isinstance(value, bool):
        raise ContractViolation(
            REVIEW_ITEM_INVALID,
            f"{field_name} must be a boolean",
        )
    return value


__all__ = [
    "NODE_REVIEW_POLICIES_KEY",
    "NODE_REVIEW_POLICY_SCHEMA_VERSION",
    "REVIEW_ITEM_SCHEMA_VERSION",
    "ReviewItem",
    "ReviewPolicy",
    "ReviewResumePhase",
    "ReviewStatus",
    "ReviewType",
    "pre_execution_review_required",
    "validate_review_transition",
]
