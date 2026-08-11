"""Canonical recovery of OBS Review waits across the outer WAL crash window."""

from __future__ import annotations

import hmac
from typing import Any, Mapping

from natsume_contracts.task import HostCommand, HostCommandSurface

from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.observability.command_outcome_journal import (
    ObsCommandOutcomeRecord,
    ObsCommandReviewWaitRecovery,
)
from host_brain.observability.command_ports import (
    build_governed_review_continuation_ref,
)
from host_brain.persistence.review_continuation_binding import (
    intent_admission_initial_status,
    parse_task_review_subject,
)

from .domain_command_ports import (
    GOVERNED_OPERATOR_COMMAND_IDS,
    build_obs_domain_result_ref,
)


class CanonicalReviewWaitRecoveryResolver:
    """Reopen TaskGraph/Review authority without trusting a transport caller."""

    def __init__(
        self,
        *,
        command_repository: Any,
        task_store: Any,
        review_store: Any,
        review_continuation_store: Any,
        secure_ingress_authority_resolver: Any,
    ) -> None:
        required = (
            (command_repository, "get_by_ingress_claim"),
            (task_store, "get_node"),
            (review_store, "list_reviews"),
            (review_continuation_store, "get_review_continuation"),
            (
                secure_ingress_authority_resolver,
                "resolve_committed_authority_by_audit",
            ),
        )
        if any(not callable(getattr(owner, method, None)) for owner, method in required):
            raise TypeError("canonical OBS Review recovery authority is incomplete")
        self._commands = command_repository
        self._tasks = task_store
        self._reviews = review_store
        self._continuations = review_continuation_store
        self._secure_ingress = secure_ingress_authority_resolver

    def __call__(
        self,
        outcome: ObsCommandOutcomeRecord,
    ) -> ObsCommandReviewWaitRecovery | None:
        if outcome.key.command_id not in GOVERNED_OPERATOR_COMMAND_IDS:
            return None
        claim_ref = str(outcome.claim_ref or "").strip()
        audit_ref = str(outcome.audit_ref or "").strip()
        if not claim_ref or not audit_ref:
            raise RuntimeError("OBS_COMMAND_REVIEW_RECOVERY_OUTER_BINDING_INVALID")
        authority_raw = self._secure_ingress.resolve_committed_authority_by_audit(
            audit_ref=audit_ref,
        )
        authority = dict(authority_raw) if isinstance(authority_raw, Mapping) else {}
        authority_scopes = _scopes(authority.get("required_scopes"))
        authority_body = str(authority.get("body_sha256") or "").strip().lower()
        authority_result = str(authority.get("claim_result_ref") or "").strip()
        if not all(
            (
                authority,
                str(authority.get("claim_ref") or "").strip() == claim_ref,
                str(authority.get("audit_ref") or "").strip() == audit_ref,
                str(authority.get("surface") or "").strip() == "obs.command",
                str(authority.get("action") or "").strip() == outcome.key.action,
                authority_scopes == outcome.key.required_scopes,
                str(authority.get("claim_status") or "").strip().upper()
                == "COMMITTED",
                str(authority.get("decision") or "").strip().upper() == "ALLOW",
                authority.get("side_effects_executed") is False,
                not authority_result,
            )
        ) or not hmac.compare_digest(authority_body, outcome.key.body_sha256):
            raise RuntimeError("OBS_COMMAND_REVIEW_RECOVERY_INGRESS_BINDING_DRIFT")

        record_raw = self._commands.get_by_ingress_claim(claim_ref)
        record = dict(record_raw) if isinstance(record_raw, Mapping) else {}
        command = record.get("command")
        work_order = record.get("work_order")
        if command is None and work_order is None:
            return None
        if not isinstance(command, HostCommand) or not isinstance(work_order, WorkOrderV3):
            raise RuntimeError("OBS_COMMAND_REVIEW_RECOVERY_COMMAND_BINDING_DRIFT")
        metadata = dict(work_order.metadata or {})
        if any(
            (
                command.surface is not HostCommandSurface.OPERATOR,
                str(command.ingress_claim_ref or "").strip() != claim_ref,
                str(command.ingress_audit_ref or "").strip() != audit_ref,
                command.input_sha256 != f"sha256:{outcome.key.body_sha256}",
                str(metadata.get("operator_command_id") or "").strip()
                != outcome.key.command_id,
                command.root_work_order_id != work_order.work_order_id,
                work_order.task_graph_id not in {None, command.graph_id},
                work_order.graph_revision != command.graph_revision,
            )
        ):
            raise RuntimeError("OBS_COMMAND_REVIEW_RECOVERY_COMMAND_BINDING_DRIFT")

        review_records = tuple(
            self._reviews.list_reviews(
                task_graph_id=command.graph_id,
                graph_revision=command.graph_revision,
                work_order_id=work_order.work_order_id,
                limit=10,
            )
            or ()
        )
        reviews: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for raw in review_records:
            durable = dict(raw) if isinstance(raw, Mapping) else {}
            review_raw = durable.get("review")
            review = dict(review_raw) if isinstance(review_raw, Mapping) else {}
            reviews.append((durable, review))
        if not reviews:
            return None
        if len(reviews) != 1:
            raise RuntimeError("OBS_COMMAND_REVIEW_RECOVERY_AUTHORITY_NOT_UNIQUE")
        durable, review = reviews[0]
        node_id = str(durable.get("task_node_id") or "").strip()
        node_raw = self._tasks.get_node(
            command.graph_id,
            command.graph_revision,
            node_id,
        )
        node = dict(node_raw) if isinstance(node_raw, Mapping) else {}
        review_id = str(review.get("review_id") or "").strip()
        review_status = str(review.get("status") or "").strip().upper()
        subject_ref = str(review.get("subject_ref") or "").strip()
        binding = parse_task_review_subject(subject_ref)
        review_subject_version = review.get("subject_version")
        node_status_version = node.get("status_version")
        if any(
            (
                not review_id,
                not node_id,
                str(durable.get("task_graph_id") or "").strip()
                != command.graph_id,
                str(durable.get("work_order_id") or "").strip()
                != work_order.work_order_id,
                binding is None,
                binding is not None and binding.graph_id != command.graph_id,
                binding is not None and binding.graph_revision != command.graph_revision,
                binding is not None and binding.node_id != node_id,
                str(node.get("graph_id") or "").strip() != command.graph_id,
                node.get("graph_revision") != command.graph_revision,
                str(node.get("node_id") or "").strip() != node_id,
                type(review_subject_version) is not int,
                type(node_status_version) is not int,
                type(durable.get("status_version")) is not int,
                str(review.get("resume_phase") or "").strip().upper()
                not in {"PRE_EXECUTION", "POST_EXECUTION_ACCEPTANCE"},
            )
        ):
            raise RuntimeError("OBS_COMMAND_REVIEW_RECOVERY_TASK_BINDING_DRIFT")
        if review_status in {"PENDING", "APPROVED"}:
            _assert_open_review_checkpoint(
                review=review,
                node=node,
                subject_ref=subject_ref,
            )
        elif review_status in {"CONSUMED", "REJECTED", "EXPIRED", "CANCELLED"}:
            continuation_raw = self._continuations.get_review_continuation(review_id)
            continuation = (
                dict(continuation_raw)
                if isinstance(continuation_raw, Mapping)
                else {}
            )
            _assert_terminal_continuation_binding(
                continuation=continuation,
                durable_review=durable,
                review=review,
                node=node,
                command=command,
                work_order=work_order,
            )
        else:
            raise RuntimeError("OBS_COMMAND_REVIEW_RECOVERY_STATUS_INVALID")
        continuation_ref = build_governed_review_continuation_ref(
            graph_id=command.graph_id,
            graph_revision=command.graph_revision,
            review=review,
        )
        return ObsCommandReviewWaitRecovery(
            receipt_ref=outcome.receipt_ref,
            claim_ref=claim_ref,
            audit_ref=audit_ref,
            command_id=outcome.key.command_id,
            action=outcome.key.action,
            required_scopes=outcome.key.required_scopes,
            body_sha256=outcome.key.body_sha256,
            domain_result_ref=build_obs_domain_result_ref(
                outcome.key.command_id,
                claim_ref,
            ),
            continuation_ref=continuation_ref,
        )


def _scopes(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(sorted(str(item).strip() for item in value if str(item).strip()))


def _assert_open_review_checkpoint(
    *,
    review: Mapping[str, Any],
    node: Mapping[str, Any],
    subject_ref: str,
) -> None:
    resume_phase = str(review.get("resume_phase") or "").strip().upper()
    expected_status = intent_admission_initial_status(subject_ref) or (
        "WAITING_APPROVAL"
        if resume_phase == "PRE_EXECUTION"
        else "WAITING_ACCEPTANCE_REVIEW"
    )
    if (
        str(node.get("status") or "").strip().upper() != expected_status
        or node.get("status_version") != review.get("subject_version")
    ):
        raise RuntimeError("OBS_COMMAND_REVIEW_RECOVERY_TASK_BINDING_DRIFT")


def _assert_terminal_continuation_binding(
    *,
    continuation: Mapping[str, Any],
    durable_review: Mapping[str, Any],
    review: Mapping[str, Any],
    node: Mapping[str, Any],
    command: HostCommand,
    work_order: WorkOrderV3,
) -> None:
    continuation_review_raw = continuation.get("review")
    continuation_review = (
        dict(continuation_review_raw)
        if isinstance(continuation_review_raw, Mapping)
        else {}
    )
    continuation_node_raw = continuation.get("node")
    continuation_node = (
        dict(continuation_node_raw)
        if isinstance(continuation_node_raw, Mapping)
        else {}
    )
    expected = (
        str(continuation.get("review_id") or "").strip()
        == str(review.get("review_id") or "").strip(),
        str(continuation.get("graph_id") or "").strip() == command.graph_id,
        continuation.get("graph_revision") == command.graph_revision,
        str(continuation.get("node_id") or "").strip()
        == str(durable_review.get("task_node_id") or "").strip(),
        continuation.get("subject_version") == review.get("subject_version"),
        str(continuation.get("resume_phase") or "").strip().upper()
        == str(review.get("resume_phase") or "").strip().upper(),
        continuation.get("review_status_version")
        == durable_review.get("status_version"),
        continuation_review == dict(review),
        continuation_node == _node_identity(node),
        type(continuation.get("checkpoint_crossed")) is bool,
        str(durable_review.get("work_order_id") or "").strip()
        == work_order.work_order_id,
    )
    if not continuation or not all(expected):
        raise RuntimeError("OBS_COMMAND_REVIEW_RECOVERY_CONTINUATION_BINDING_DRIFT")


def _node_identity(node: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "graph_id": node.get("graph_id"),
        "graph_revision": node.get("graph_revision"),
        "node_id": node.get("node_id"),
        "status": node.get("status"),
        "status_version": node.get("status_version"),
    }


__all__ = ["CanonicalReviewWaitRecoveryResolver"]
