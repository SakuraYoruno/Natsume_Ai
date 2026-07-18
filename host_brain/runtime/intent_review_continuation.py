"""Private Review continuation behavior for durable Intent admission.

The mixin has no constructor and owns no port.  The one production
``DurableIntentAdmissionVerifier`` supplies the durable ReviewStore, clock,
and canonical RuntimeEvent append/reopen boundary.  Keeping those authorities
on the verifier prevents this focused split from creating a second admission
or continuation service.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from host_brain.contracts.agent_runtime.review_policy import (
    ReviewItem,
    ReviewResumePhase,
    ReviewStatus,
    ReviewType,
)
from host_brain.contracts.agent_runtime.task_graph import TaskGraph, TaskNode
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.integrations.runtime_events import RuntimeEvent
from natsume_contracts.task import HostCommand

from .context_contracts import SceneDecisionV2
from .intent_admission_contracts import (
    INTENT_ADMISSION_BLOCKED,
    INTENT_ADMISSION_INVALID,
    IntentAdmissionError,
    _CONTINUATION_AGGREGATE_TYPE,
    _CONTINUATION_EVENT_TYPE,
    _CONTINUATION_SCHEMA,
    _contract_digest,
    _digest,
    _integer,
    _mapping,
    _payload_digest_without,
)


class _IntentReviewContinuationMixin:
    """ReviewItem materialization and restrict-only continuation receipts."""

    @staticmethod
    def _review_id(receipt: Mapping[str, Any]) -> str:
        return "intent-review-" + _digest(
            {
                "task_graph_id": receipt.get("task_graph_id"),
                "graph_revision": receipt.get("graph_revision"),
                "task_node_id": receipt.get("task_node_id"),
                "receipt_digest": receipt.get("receipt_digest"),
            }
        ).split(":", 1)[1]

    def _review_item(
        self,
        *,
        command: HostCommand,
        work_order: WorkOrderV3,
        graph: TaskGraph,
        node: TaskNode,
        scene: SceneDecisionV2,
        receipt: Mapping[str, Any],
        receipt_event_id: str,
    ) -> ReviewItem:
        if receipt.get("decision") != "REVIEW":
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "only REVIEW arbitration may create an Intent ReviewItem",
            )
        graph_id = quote(graph.graph_id, safe="")
        node_id = quote(node.node_id, safe="")
        initial_status = str(getattr(node.status, "value", node.status)).strip().upper()
        if initial_status not in {"READY", "BLOCKED"}:
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "Intent ReviewItem requires the exact initial READY/BLOCKED TaskNode",
            )
        subject_ref = (
            f"task-node://{graph_id}/{graph.revision}/{node_id}"
            "?attempt=not-started&authority=intent-admission"
            f"&initial_status={initial_status}"
        )
        checkpoint_digest = _digest(
            {
                "subject_ref": subject_ref,
                "receipt_digest": receipt["receipt_digest"],
            }
        ).split(":", 1)[1]
        expires_at = (
            max(int(command.created_at_ms), int(graph.created_at), 1)
            + self._review_ttl_ms
        )
        return ReviewItem(
            review_id=self._review_id(receipt),
            review_type=ReviewType.PRE_EXECUTION_APPROVAL,
            subject_ref=subject_ref,
            # TaskGraph revision is already frozen in the subject path.  The
            # Review's subject version is the independent durable TaskNode CAS
            # version checked by ReviewStore in the same SQLite transaction.
            subject_version=int(node.status_version),
            requester=str(receipt["policy_ref"]),
            reason="Intent arbitration requires explicit durable approval",
            risk_level=max(1, min(5, int(node.risk_level))),
            side_effects=(f"route:{node.route}",),
            evidence_refs=(
                receipt_event_id,
                "intentframe://sha256/"
                + str(receipt["intent_frame_digest"]).removeprefix("sha256:"),
                "intentarbitration://sha256/"
                + str(receipt["arbitration_digest"]).removeprefix("sha256:"),
                "scene://sha256/"
                + str(scene.decision_digest).removeprefix("sha256:"),
            ),
            status=ReviewStatus.PENDING,
            expires_at=expires_at,
            resume_phase=ReviewResumePhase.PRE_EXECUTION,
            diff_ref=(
                "intent-constraints://sha256/"
                + str(receipt["constraint_set_digest"]).split(":", 1)[1]
            ),
            checkpoint_id=f"intent-checkpoint://sha256/{checkpoint_digest}",
        )

    def _ensure_review(
        self,
        *,
        command: HostCommand,
        work_order: WorkOrderV3,
        graph: TaskGraph,
        node: TaskNode,
        scene: SceneDecisionV2,
        receipt: Mapping[str, Any],
        receipt_event_id: str,
    ) -> dict[str, Any]:
        store = self._reviews
        if store is None:
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "Intent REVIEW requires the production durable ReviewStore",
            )
        item = self._review_item(
            command=command,
            work_order=work_order,
            graph=graph,
            node=node,
            scene=scene,
            receipt=receipt,
            receipt_event_id=receipt_event_id,
        )
        try:
            record = store.get_review(item.review_id)
        except Exception:
            if item.expires_at <= int(self._clock()):
                raise IntentAdmissionError(
                    INTENT_ADMISSION_INVALID,
                    "Intent ReviewItem deterministic expiry is already elapsed",
                )
            try:
                record = store.create_review(
                    item,
                    work_order_id=work_order.work_order_id,
                    task_graph_id=graph.graph_id,
                    task_node_id=node.node_id,
                )
            except Exception as create_exc:
                # Reconcile a committed response loss or concurrent identical
                # creator before surfacing a store error.
                try:
                    record = store.get_review(item.review_id)
                except Exception as lookup_exc:
                    raise IntentAdmissionError(
                        INTENT_ADMISSION_INVALID,
                        "durable Intent ReviewItem creation failed",
                    ) from create_exc
                if record is None:
                    raise IntentAdmissionError(
                        INTENT_ADMISSION_INVALID,
                        "durable Intent ReviewItem creation was not observable",
                    ) from lookup_exc
        return self._validate_review_record(
            record,
            expected=item,
            work_order=work_order,
            graph=graph,
            node=node,
        )

    def _validate_review_record(
        self,
        record: Mapping[str, Any],
        *,
        expected: ReviewItem,
        work_order: WorkOrderV3,
        graph: TaskGraph,
        node: TaskNode,
    ) -> dict[str, Any]:
        outer = _mapping(record, field="durable Intent ReviewItem record")
        review = _mapping(outer.get("review"), field="durable Intent ReviewItem")
        immutable_fields = (
            "schema_version",
            "review_id",
            "review_type",
            "subject_ref",
            "subject_version",
            "requester",
            "reason",
            "risk_level",
            "side_effects",
            "evidence_refs",
            "diff_ref",
            "checkpoint_id",
            "expires_at",
            "resume_phase",
        )
        expected_dict = expected.to_dict()
        if any(
            review.get(field) != expected_dict.get(field)
            for field in immutable_fields
        ):
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "durable Intent ReviewItem identity or constraints drifted",
            )
        if (
            outer.get("work_order_id") != work_order.work_order_id
            or outer.get("task_graph_id") != graph.graph_id
            or outer.get("task_node_id") != node.node_id
            or outer.get("side_effects") != list(expected.side_effects)
            or outer.get("evidence_refs") != list(expected.evidence_refs)
        ):
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "durable Intent ReviewItem outer lineage drifted",
            )
        try:
            status = ReviewStatus(str(review.get("status") or "").strip().upper())
        except ValueError as exc:
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "durable Intent ReviewItem status is invalid",
            ) from exc
        if status in {ReviewStatus.PENDING, ReviewStatus.APPROVED} and int(
            review.get("expires_at") or 0
        ) <= int(self._clock()):
            raise IntentAdmissionError(
                INTENT_ADMISSION_BLOCKED,
                "durable Intent ReviewItem is expired",
            )
        if status is ReviewStatus.CONSUMED and int(
            review.get("resume_count") or 0
        ) != 1:
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "durable Intent ReviewItem one-time resume count drifted",
            )
        return outer

    def _continuation_event(
        self,
        *,
        command: HostCommand,
        graph: TaskGraph,
        node: TaskNode,
        scene: SceneDecisionV2,
        receipt: Mapping[str, Any],
        receipt_event_id: str,
        review_record: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> RuntimeEvent:
        review = _mapping(
            review_record.get("review"),
            field="consumed Intent ReviewItem",
        )
        review_ref = f"review://{review['review_id']}"
        scene_ref = (
            "scene://sha256/"
            + str(scene.decision_digest).removeprefix("sha256:")
        )
        aggregate_id = _digest(
            {
                "graph_id": graph.graph_id,
                "graph_revision": graph.revision,
                "node_id": node.node_id,
                "review_id": review["review_id"],
            }
        )
        payload_ref = "intentcontinuation://sha256/" + aggregate_id.split(":", 1)[1]
        return RuntimeEvent.create(
            event_type=_CONTINUATION_EVENT_TYPE,
            source_kind="REVIEW",
            source_ref=review_ref,
            aggregate_type=_CONTINUATION_AGGREGATE_TYPE,
            aggregate_id=aggregate_id,
            aggregate_version=int(review_record.get("status_version") or 0),
            payload_ref=payload_ref,
            payload=dict(payload),
            evidence_refs=(
                review_ref,
                receipt_event_id,
                scene_ref,
                str(review["checkpoint_id"]),
                str(review["diff_ref"]),
                str(review["resume_result_ref"]),
            ),
            redaction_level="INTERNAL_REDACTED",
            ts_ms=max(
                1,
                _integer(command.created_at_ms, field="HostCommand created_at_ms"),
            ),
        )

    def _append_review_continuation(
        self,
        *,
        command: HostCommand,
        graph: TaskGraph,
        node: TaskNode,
        scene: SceneDecisionV2,
        receipt: Mapping[str, Any],
        receipt_event_id: str,
        review_record: Mapping[str, Any],
    ) -> str:
        review = _mapping(
            review_record.get("review"),
            field="consumed Intent ReviewItem",
        )
        if (
            review.get("status") != ReviewStatus.CONSUMED.value
            or int(review.get("resume_count") or 0) != 1
            or not str(review.get("resume_result_ref") or "").strip()
            or receipt.get("decision") != "REVIEW"
            or "REVIEW" not in receipt.get("constraint_dispositions", [])
            or "BLOCK" in receipt.get("constraint_dispositions", [])
        ):
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "Intent Review continuation would loosen or bypass constraints",
            )
        payload: dict[str, Any] = {
            "schema": _CONTINUATION_SCHEMA,
            "task_graph_id": graph.graph_id,
            "graph_revision": graph.revision,
            "task_graph_digest": _contract_digest(graph, field="TaskGraph"),
            "task_node_id": node.node_id,
            "task_node_digest": _contract_digest(node, field="TaskNode"),
            "scene_id": scene.scene_id,
            "scene_revision": scene.revision,
            "scene_decision_digest": scene.decision_digest,
            "intent_frame_digest": receipt["intent_frame_digest"],
            "arbitration_digest": receipt["arbitration_digest"],
            "original_receipt_digest": receipt["receipt_digest"],
            "constraint_set_digest": receipt["constraint_set_digest"],
            "constraint_dispositions": list(receipt["constraint_dispositions"]),
            "permission_effect": "RESTRICT_ONLY",
            "review_id": review["review_id"],
            "review_status_version": int(review_record.get("status_version") or 0),
            "review_state_digest": _digest(review),
            "resume_result_ref": review["resume_result_ref"],
            "continuation_allowed": True,
        }
        payload["continuation_digest"] = _payload_digest_without(
            payload,
            "continuation_digest",
        )
        event = self._continuation_event(
            command=command,
            graph=graph,
            node=node,
            scene=scene,
            receipt=receipt,
            receipt_event_id=receipt_event_id,
            review_record=review_record,
            payload=payload,
        )
        record = self._append_or_reopen(
            event,
            detail="Intent Review continuation receipt append failed",
        )
        stored = self._validate_event_identity(
            record,
            expected_event=event,
            detail="Intent Review continuation RuntimeEvent",
        )
        actual = _mapping(
            stored.get("payload"),
            field="Intent Review continuation receipt",
        )
        if actual != payload or actual.get(
            "continuation_digest"
        ) != _payload_digest_without(actual, "continuation_digest"):
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "Intent Review continuation receipt drifted",
            )
        return str(event.event_id)
