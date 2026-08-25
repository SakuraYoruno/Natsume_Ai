"""Durable, replay-safe Intent arbitration at TaskGraph admission.

The first admission re-opens the canonical HostCommand, Scene and Governor
owners through ``ProductionIntentAuthorityPort`` and persists one immutable
RuntimeEvent receipt per TaskNode.  Later duplicate delivery and process
restart validate those receipts against the exact durable contracts and Scene;
they never need to re-authorize execution with an expired parent grant.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import logging
import re
import time
from typing import Any

from host_brain.contracts.agent_runtime.review_policy import ReviewStatus
from host_brain.contracts.agent_runtime.task_graph import TaskGraph, TaskNode
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.integrations.runtime_events import RuntimeEvent
from natsume_contracts.task import HostCommand

from host_brain.contracts.runtime_context import IntentArbitrationV1, IntentFrameV1, SceneDecisionV2
from .intent_admission_contracts import (
    INTENT_ADMISSION_BLOCKED,
    INTENT_ADMISSION_INVALID,
    INTENT_ADMISSION_REVIEW_REQUIRED,
    IntentAdmissionError,
    _AGGREGATE_TYPE,
    _ALLOWED_DECISIONS,
    _COMPLETE_SET_AGGREGATE_TYPE,
    _COMPLETE_SET_EVENT_TYPE,
    _COMPLETE_SET_SCHEMA,
    _CONSTRAINT_DISPOSITIONS,
    _DEFAULT_REVIEW_TTL_MS,
    _EVENT_TYPE,
    _PREPARED_NODE_AGGREGATE_TYPE,
    _PREPARED_NODE_EVENT_TYPE,
    _PREPARED_SET_AGGREGATE_TYPE,
    _PREPARED_SET_EVENT_TYPE,
    _PREPARED_SET_SCHEMA,
    _PROCEED_DECISIONS,
    _RECEIPT_SCHEMA,
    _REVIEW_CONTINUATION_AUTHORITY_FIELDS,
    _contract_digest,
    _digest,
    _integer,
    _mapping,
    _receipt_payload_digest,
    _scene_query,
    _text,
)
from .intent_authority import IntentAuthorityError
from .intent_review_continuation import _IntentReviewContinuationMixin
from .production_intent_authority import ProductionIntentAuthorityPort


_logger = logging.getLogger("Natsume.intent_admission")


class DurableIntentAdmissionVerifier(_IntentReviewContinuationMixin):
    """Two-phase, replay-safe Intent admission for an immutable TaskGraph.

    The verifier deliberately separates *preparation* from executable
    admission.  Every node is first arbitrated while the parent grant is live;
    those full results are persisted as node-preparation events and only then
    sealed by one graph-level prepared marker.  Materialized node receipts can
    therefore be repaired after a process crash without reopening an expired
    parent grant, while a mere matching HostCommand can never bypass preflight.

    A REVIEW result creates one deterministic, durable ``ReviewItem``.  It can
    proceed only after ``ReviewStore`` proves the exact item is ``CONSUMED``;
    the continuation is then sealed by its own immutable event and the final
    graph-level complete marker.  The original arbitration receipt is never
    rewritten or promoted into execution authority.
    """

    def __init__(
        self,
        *,
        authority_port: ProductionIntentAuthorityPort,
        intent_arbiter: Any,
        event_store: Any,
        review_store: Any | None = None,
        review_ttl_ms: int = _DEFAULT_REVIEW_TTL_MS,
        clock: Callable[[], int] | None = None,
    ) -> None:
        if not callable(getattr(authority_port, "resolve_canonical_intent", None)):
            raise TypeError("authority_port must expose resolve_canonical_intent")
        if not callable(getattr(intent_arbiter, "arbitrate", None)):
            raise TypeError("intent_arbiter must expose arbitrate")
        if not callable(getattr(event_store, "append_event", None)) or not callable(
            getattr(event_store, "get_event_by_id", None)
        ):
            raise TypeError("event_store must expose append_event and get_event_by_id")
        if review_store is not None and (
            not callable(getattr(review_store, "create_review", None))
            or not callable(getattr(review_store, "get_review", None))
        ):
            raise TypeError("review_store must expose create_review and get_review")
        if isinstance(review_ttl_ms, bool) or int(review_ttl_ms) < 60_000:
            raise ValueError("review_ttl_ms must be at least 60000")
        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable")
        repository = getattr(authority_port, "command_repository", None)
        if not callable(getattr(repository, "get_by_graph", None)):
            raise TypeError("authority_port must use a canonical command repository")
        self._authority = authority_port
        self._arbiter = intent_arbiter
        self._events = event_store
        self._reviews = review_store
        self._review_ttl_ms = int(review_ttl_ms)
        self._clock = clock or (lambda: int(time.time() * 1000))
        self._commands = repository

    def has_prepared_intent_receipt_set(
        self,
        *,
        command: HostCommand,
        work_order: WorkOrderV3,
        graph: TaskGraph,
    ) -> bool:
        """Prove the exact complete preparation set before parent preflight.

        ``GovernedCommandApplication`` calls this method only after it has
        matched all four durable admission contracts, including assignment.
        Missing, partial, or drifting preparation returns ``False``/raises and
        therefore cannot turn command equality into an authorization bypass.
        """

        durable_command, durable_order, durable_graph = self._reopen_admission(
            command=command,
            work_order=work_order,
            graph=graph,
        )
        scene = self._require_scene(_scene_query(durable_order))
        marker = self._get_event(
            self._graph_event(
                stage="PREPARED",
                command=durable_command,
                work_order=durable_order,
                graph=durable_graph,
                scene=scene,
                payload={},
            ),
            detail="Intent prepared-set marker lookup failed",
        )
        if marker is None:
            return False
        self._load_prepared_set(
            command=durable_command,
            work_order=durable_order,
            graph=durable_graph,
            scene=scene,
            marker_record=marker,
        )
        return True

    def verify_intent_admission(
        self,
        *,
        command: HostCommand,
        work_order: WorkOrderV3,
        graph: TaskGraph,
        replayed: bool,
    ) -> tuple[dict[str, Any], ...]:
        """Return one exact original arbitration receipt per graph node."""

        if not isinstance(replayed, bool):
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "admission replay marker must be boolean",
            )
        durable_command, durable_order, durable_graph = self._reopen_admission(
            command=command,
            work_order=work_order,
            graph=graph,
        )
        scene = self._require_scene(_scene_query(durable_order))
        prepared_probe = self._graph_event(
            stage="PREPARED",
            command=durable_command,
            work_order=durable_order,
            graph=durable_graph,
            scene=scene,
            payload={},
        )
        marker = self._get_event(
            prepared_probe,
            detail="Intent prepared-set marker lookup failed",
        )
        if marker is None:
            prepared = self._prepare_receipt_set(
                command=durable_command,
                work_order=durable_order,
                graph=durable_graph,
                scene=scene,
            )
        else:
            prepared = self._load_prepared_set(
                command=durable_command,
                work_order=durable_order,
                graph=durable_graph,
                scene=scene,
                marker_record=marker,
            )

        receipts: list[dict[str, Any]] = []
        final_event_ids: dict[str, str] = {}
        continuation_event_ids: dict[str, str] = {}
        review_ids: list[str] = []
        blocked_nodes: list[str] = []
        waiting_nodes: list[str] = []
        for node in tuple(durable_graph.nodes):
            payload = prepared[node.node_id]
            final_event = self._node_event(
                stage="FINAL",
                command=durable_command,
                graph=durable_graph,
                node=node,
                scene=scene,
                payload=payload,
            )
            final_record = self._append_or_reopen(
                final_event,
                detail="Intent arbitration receipt append failed",
            )
            receipt = self._validate_receipt(
                record=final_record,
                command=durable_command,
                work_order=durable_order,
                graph=durable_graph,
                node=node,
                scene=scene,
                expected_event=final_event,
                expected_payload=payload,
            )
            receipts.append(receipt)
            final_event_ids[node.node_id] = str(final_event.event_id)
            decision = str(receipt["decision"])
            if decision == "BLOCK":
                blocked_nodes.append(node.node_id)
                continue
            if decision != "REVIEW":
                continue
            review_id = self._review_id(receipt)
            review_ids.append(review_id)
            review = self._ensure_review(
                command=durable_command,
                work_order=durable_order,
                graph=durable_graph,
                node=node,
                scene=scene,
                receipt=receipt,
                receipt_event_id=str(final_event.event_id),
            )
            status = str(review["review"]["status"] or "").strip().upper()
            if status in {ReviewStatus.PENDING.value, ReviewStatus.APPROVED.value}:
                waiting_nodes.append(node.node_id)
                continue
            if status in {
                ReviewStatus.REJECTED.value,
                ReviewStatus.EXPIRED.value,
                ReviewStatus.CANCELLED.value,
            }:
                blocked_nodes.append(node.node_id)
                continue
            if status != ReviewStatus.CONSUMED.value:
                raise IntentAdmissionError(
                    INTENT_ADMISSION_INVALID,
                    f"durable Intent ReviewItem has unsupported status {status}",
                )
            continuation = self._append_review_continuation(
                command=durable_command,
                graph=durable_graph,
                node=node,
                scene=scene,
                receipt=receipt,
                receipt_event_id=str(final_event.event_id),
                review_record=review,
            )
            continuation_event_ids[node.node_id] = continuation

        # BLOCK can never be converted into a Review continuation.  In a mixed
        # graph it also dominates any waiting reviews.
        if blocked_nodes:
            raise IntentAdmissionError(
                INTENT_ADMISSION_BLOCKED,
                "Intent admission permanently blocked TaskGraph node(s): "
                + ",".join(blocked_nodes),
            )
        if waiting_nodes:
            raise IntentAdmissionError(
                INTENT_ADMISSION_REVIEW_REQUIRED,
                "Intent arbitration requires consumed durable ReviewItem(s): "
                + ",".join(
                    f"{node_id}:{self._review_id(prepared[node_id])}"
                    for node_id in waiting_nodes
                ),
            )

        self._seal_complete_set(
            command=durable_command,
            work_order=durable_order,
            graph=durable_graph,
            scene=scene,
            prepared=prepared,
            final_event_ids=final_event_ids,
            continuation_event_ids=continuation_event_ids,
            review_ids=tuple(sorted(review_ids)),
        )
        return tuple(receipts)

    def resolve_terminal_intent_block(
        self,
        *,
        command: HostCommand,
        work_order: WorkOrderV3,
        graph: TaskGraph,
    ) -> dict[str, Any] | None:
        """Return cancellation authority for an immutable direct BLOCK.

        Review rejection deliberately shares ``INTENT_ADMISSION_BLOCKED`` at
        the execution boundary, but it has a different terminal authority.
        This resolver therefore reopens every exact final receipt and returns
        a SYSTEM cancellation identity only when at least one node's original
        arbitration decision was ``BLOCK``.  No policy is re-evaluated here.
        """

        durable_command, durable_order, durable_graph = self._reopen_admission(
            command=command,
            work_order=work_order,
            graph=graph,
        )
        scene = self._require_scene(_scene_query(durable_order))
        prepared_event = self._graph_event(
            stage="PREPARED",
            command=durable_command,
            work_order=durable_order,
            graph=durable_graph,
            scene=scene,
            payload={},
        )
        prepared_marker = self._get_event(
            prepared_event,
            detail="terminal Intent BLOCK prepared-set lookup failed",
        )
        if prepared_marker is None:
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "terminal Intent BLOCK lacks a complete prepared-set marker",
            )
        prepared = self._load_prepared_set(
            command=durable_command,
            work_order=durable_order,
            graph=durable_graph,
            scene=scene,
            marker_record=prepared_marker,
        )
        blocked: list[dict[str, str]] = []
        for node in tuple(durable_graph.nodes):
            payload = prepared[node.node_id]
            final_event = self._node_event(
                stage="FINAL",
                command=durable_command,
                graph=durable_graph,
                node=node,
                scene=scene,
                payload=payload,
            )
            final_record = self._get_event(
                final_event,
                detail="terminal Intent BLOCK final receipt lookup failed",
            )
            if final_record is None:
                raise IntentAdmissionError(
                    INTENT_ADMISSION_INVALID,
                    "terminal Intent BLOCK final receipt is missing",
                )
            receipt = self._validate_receipt(
                record=final_record,
                command=durable_command,
                work_order=durable_order,
                graph=durable_graph,
                node=node,
                scene=scene,
                expected_event=final_event,
                expected_payload=payload,
            )
            if receipt.get("decision") == "BLOCK":
                blocked.append(
                    {
                        "task_node_id": node.node_id,
                        "receipt_digest": str(receipt["receipt_digest"]),
                        "receipt_event_id": str(final_event.event_id),
                    }
                )
        if not blocked:
            return None
        blocked.sort(key=lambda item: item["task_node_id"])
        identity = {
            "schema_version": "natsume.intent-block-terminal.v1",
            "command_id": durable_command.command_id,
            "task_graph_id": durable_graph.graph_id,
            "graph_revision": durable_graph.revision,
            "blocked_receipts": blocked,
        }
        cancellation_digest = _digest(identity).split(":", 1)[1]
        return {
            **identity,
            "blocked_node_ids": [item["task_node_id"] for item in blocked],
            "request_id": f"intent-block-terminal-{cancellation_digest[:40]}",
            "request_identity": (
                f"intent-block://sha256/{cancellation_digest}"
            ),
            "reason_code": INTENT_ADMISSION_BLOCKED,
        }

    def verify_terminal_intent_review_claim(
        self,
        *,
        command: HostCommand,
        work_order: WorkOrderV3,
        graph: TaskGraph,
        continuation_claim: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Prove a denied Intent Review before graph cancellation.

        The ordinary verifier correctly raises ``INTENT_ADMISSION_BLOCKED``
        for a rejected/expired Review.  The 0096 worker still needs a finite
        terminal action, so this method reopens the immutable prepared/final
        receipt and the exact durable ReviewItem without converting either
        into execution permission.  Its only result is a SYSTEM cancellation
        identity bound to the Review's terminal status version.
        """

        claim = _mapping(
            continuation_claim,
            field="Intent Review continuation authority",
        )
        if (
            set(claim) != _REVIEW_CONTINUATION_AUTHORITY_FIELDS
            or claim.get("schema_version")
            != "Natsume.task.review_continuation_authority.v1"
        ):
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "terminal Intent Review continuation authority schema drifted",
            )
        status = str(claim.get("review_status") or "").strip().upper()
        if status not in {
            ReviewStatus.REJECTED.value,
            ReviewStatus.EXPIRED.value,
            ReviewStatus.CANCELLED.value,
        }:
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "terminal Intent Review claim is not denied",
            )
        durable_command, durable_order, durable_graph = self._reopen_admission(
            command=command,
            work_order=work_order,
            graph=graph,
        )
        scene = self._require_scene(_scene_query(durable_order))
        prepared_event = self._graph_event(
            stage="PREPARED",
            command=durable_command,
            work_order=durable_order,
            graph=durable_graph,
            scene=scene,
            payload={},
        )
        prepared_marker = self._get_event(
            prepared_event,
            detail="terminal Intent Review prepared-set lookup failed",
        )
        if prepared_marker is None:
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "terminal Intent Review lacks a complete prepared-set marker",
            )
        prepared = self._load_prepared_set(
            command=durable_command,
            work_order=durable_order,
            graph=durable_graph,
            scene=scene,
            marker_record=prepared_marker,
        )
        graph_id = _text(claim.get("graph_id"), field="claim graph_id")
        graph_revision = _integer(
            claim.get("graph_revision"),
            field="claim graph_revision",
            minimum=1,
        )
        node_id = _text(claim.get("node_id"), field="claim node_id")
        if graph_id != durable_graph.graph_id or graph_revision != durable_graph.revision:
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "terminal Intent Review claim Graph binding drifted",
            )
        node = next(
            (candidate for candidate in durable_graph.nodes if candidate.node_id == node_id),
            None,
        )
        if node is None:
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "terminal Intent Review claim TaskNode is absent",
            )
        payload = prepared[node.node_id]
        final_event = self._node_event(
            stage="FINAL",
            command=durable_command,
            graph=durable_graph,
            node=node,
            scene=scene,
            payload=payload,
        )
        final_record = self._get_event(
            final_event,
            detail="terminal Intent Review final receipt lookup failed",
        )
        if final_record is None:
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "terminal Intent Review final receipt is missing",
            )
        receipt = self._validate_receipt(
            record=final_record,
            command=durable_command,
            work_order=durable_order,
            graph=durable_graph,
            node=node,
            scene=scene,
            expected_event=final_event,
            expected_payload=payload,
        )
        if receipt.get("decision") != "REVIEW":
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "terminal continuation is not backed by an Intent REVIEW receipt",
            )
        store = self._reviews
        if store is None:
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "terminal Intent Review requires the production durable ReviewStore",
            )
        expected = self._review_item(
            command=durable_command,
            work_order=durable_order,
            graph=durable_graph,
            node=node,
            scene=scene,
            receipt=receipt,
            receipt_event_id=str(final_event.event_id),
        )
        try:
            raw_review = store.get_review(expected.review_id)
        except Exception as exc:
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "terminal Intent ReviewItem is not durable",
            ) from exc
        review_record = self._validate_review_record(
            raw_review,
            expected=expected,
            work_order=durable_order,
            graph=durable_graph,
            node=node,
        )
        review = _mapping(review_record.get("review"), field="terminal Intent ReviewItem")
        exact_claim = (
            claim.get("review_id") == review.get("review_id")
            and claim.get("graph_id") == durable_graph.graph_id
            and claim.get("graph_revision") == durable_graph.revision
            and claim.get("node_id") == node.node_id
            and claim.get("subject_version") == review.get("subject_version")
            and claim.get("resume_phase") == review.get("resume_phase")
            and claim.get("review_status") == review.get("status")
            and claim.get("review_status_version")
            == int(review_record.get("status_version") or -1)
            and isinstance(claim.get("claim_fencing_token"), int)
            and int(claim.get("claim_fencing_token") or 0) > 0
            and isinstance(claim.get("claim_expires_ts_ms"), int)
            and int(claim.get("claim_expires_ts_ms") or -1) >= 0
            and isinstance(claim.get("status_version"), int)
            and int(claim.get("status_version") or -1) >= 0
            and bool(str(claim.get("claim_owner") or "").strip())
            and bool(
                re.fullmatch(
                    r"sha256:[0-9a-f]{64}",
                    str(claim.get("claim_token_sha256") or ""),
                )
            )
        )
        if not exact_claim:
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "terminal Intent Review claim differs from durable Review authority",
            )
        identity = {
            "review_id": review["review_id"],
            "review_status": status,
            "review_status_version": int(review_record["status_version"]),
            "task_graph_id": durable_graph.graph_id,
            "graph_revision": durable_graph.revision,
            "task_node_id": node.node_id,
            "receipt_digest": receipt["receipt_digest"],
        }
        cancellation_digest = _digest(identity).split(":", 1)[1]
        return {
            **identity,
            "request_id": f"intent-review-terminal-{cancellation_digest[:40]}",
            "request_identity": f"review://{review['review_id']}",
            "reason_code": f"INTENT_REVIEW_{status}",
            "receipt_event_id": str(final_event.event_id),
        }

    def _reopen_admission(
        self,
        *,
        command: HostCommand,
        work_order: WorkOrderV3,
        graph: TaskGraph,
    ) -> tuple[HostCommand, WorkOrderV3, TaskGraph]:
        if not isinstance(command, HostCommand):
            raise IntentAdmissionError(INTENT_ADMISSION_INVALID, "HostCommand is untyped")
        if not isinstance(work_order, WorkOrderV3) or not isinstance(graph, TaskGraph):
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "WorkOrderV3 or TaskGraph is untyped",
            )
        try:
            raw = self._commands.get_by_graph(
                graph_id=graph.graph_id,
                graph_revision=graph.revision,
            )
        except Exception as exc:
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "canonical HostCommand admission lookup failed",
            ) from exc
        record = _mapping(raw, field="canonical HostCommand admission")
        durable_command = record.get("command")
        durable_order = record.get("work_order")
        durable_graph = record.get("graph")
        if not isinstance(durable_command, HostCommand):
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "canonical HostCommand admission is untyped",
            )
        if not isinstance(durable_order, WorkOrderV3) or not isinstance(
            durable_graph,
            TaskGraph,
        ):
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "canonical WorkOrderV3 or TaskGraph admission is untyped",
            )
        if (
            durable_command.to_dict() != command.to_dict()
            or durable_order.to_dict() != work_order.to_dict()
            or durable_graph.to_dict() != graph.to_dict()
        ):
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "admission arguments differ from canonical durable contracts",
            )
        return durable_command, durable_order, durable_graph

    def _require_scene(self, query: Mapping[str, Any]) -> SceneDecisionV2:
        resolver = getattr(self._authority, "scene_resolver", None)
        if not callable(getattr(resolver, "require_decision", None)):
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "canonical SceneResolver is unavailable",
            )
        try:
            scene = resolver.require_decision(
                workspace_id=query["workspace_id"],
                subject_id=query["subject_id"],
                scene_id=query["scene_id"],
                revision=query["revision"],
                decision_digest=query["decision_digest"],
            )
        except Exception as exc:
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "canonical Scene reopening failed",
            ) from exc
        if type(scene) is not SceneDecisionV2:
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "canonical SceneResolver returned an untyped decision",
            )
        return scene

    def _prepare_receipt_set(
        self,
        *,
        command: HostCommand,
        work_order: WorkOrderV3,
        graph: TaskGraph,
        scene: SceneDecisionV2,
    ) -> dict[str, dict[str, Any]]:
        """Arbitrate all nodes live, persist them, then seal the exact set."""

        payloads: dict[str, dict[str, Any]] = {}
        for node in tuple(graph.nodes):
            if not isinstance(node, TaskNode):
                raise IntentAdmissionError(
                    INTENT_ADMISSION_INVALID,
                    "TaskGraph contains an untyped TaskNode",
                )
            payloads[node.node_id] = self._arbitrate_payload(
                command=command,
                work_order=work_order,
                graph=graph,
                node=node,
                scene=scene,
            )

        # Preparation rows are written before the graph marker.  A crash while
        # writing them does not permit parent-preflight bypass.  Once the
        # marker exists, every preparation row is already durable and missing
        # final receipts can be materialized without the parent grant.
        for node in tuple(graph.nodes):
            payload = payloads[node.node_id]
            event = self._node_event(
                stage="PREPARED",
                command=command,
                graph=graph,
                node=node,
                scene=scene,
                payload=payload,
            )
            record = self._append_or_reopen(
                event,
                detail="Intent node preparation append failed",
            )
            self._validate_receipt(
                record=record,
                command=command,
                work_order=work_order,
                graph=graph,
                node=node,
                scene=scene,
                expected_event=event,
                expected_payload=payload,
            )

        marker_payload = self._prepared_marker_payload(
            command=command,
            work_order=work_order,
            graph=graph,
            scene=scene,
            prepared=payloads,
        )
        marker_event = self._graph_event(
            stage="PREPARED",
            command=command,
            work_order=work_order,
            graph=graph,
            scene=scene,
            payload=marker_payload,
        )
        marker_record = self._append_or_reopen(
            marker_event,
            detail="Intent prepared-set marker append failed",
        )
        return self._load_prepared_set(
            command=command,
            work_order=work_order,
            graph=graph,
            scene=scene,
            marker_record=marker_record,
        )

    def _arbitrate_payload(
        self,
        *,
        command: HostCommand,
        work_order: WorkOrderV3,
        graph: TaskGraph,
        node: TaskNode,
        scene: SceneDecisionV2,
    ) -> dict[str, Any]:
        try:
            frame, reopened_scene = self._authority.resolve_canonical_intent(
                workspace_id=scene.workspace_id,
                subject_id=scene.subject_id,
                task_graph_id=graph.graph_id,
                graph_revision=graph.revision,
                task_node_id=node.node_id,
                scene_id=scene.scene_id,
                scene_revision=scene.revision,
                scene_decision_digest=scene.decision_digest,
                grant_ref=_text(
                    command.governor_grant_ref,
                    field="HostCommand governor_grant_ref",
                ),
            )
            if type(frame) is not IntentFrameV1 or reopened_scene != scene:
                raise IntentAuthorityError(
                    "canonical Intent input differs from admission Scene"
                )
            arbitration = self._arbiter.arbitrate(frame=frame, scene=reopened_scene)
        except IntentAdmissionError:
            raise
        except Exception as exc:
            diagnostic = (
                str(exc)[:240]
                if isinstance(exc, (IntentAuthorityError, ValueError, TypeError))
                else type(exc).__name__
            )
            _logger.warning(
                "canonical Intent arbitration failed request_id=%s graph_id=%s "
                "node_id=%s error_type=%s diagnostic=%s",
                command.request_id,
                graph.graph_id,
                node.node_id,
                type(exc).__name__,
                diagnostic,
            )
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "canonical Intent arbitration failed",
            ) from exc
        if type(arbitration) is not IntentArbitrationV1:
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "IntentArbiter returned an untyped result",
            )
        return self._build_payload(
            command=command,
            work_order=work_order,
            graph=graph,
            node=node,
            scene=scene,
            frame=frame,
            arbitration=arbitration,
        )

    def _build_payload(
        self,
        *,
        command: HostCommand,
        work_order: WorkOrderV3,
        graph: TaskGraph,
        node: TaskNode,
        scene: SceneDecisionV2,
        frame: IntentFrameV1,
        arbitration: IntentArbitrationV1,
    ) -> dict[str, Any]:
        decision = str(arbitration.decision or "").strip().upper()
        if decision not in _ALLOWED_DECISIONS:
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "Intent arbitration decision is invalid",
            )
        dispositions = tuple(
            sorted({str(item.disposition) for item in arbitration.constraints})
        )
        if any(item not in _CONSTRAINT_DISPOSITIONS for item in dispositions):
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "Intent arbitration contains an invalid constraint disposition",
            )
        payload: dict[str, Any] = {
            "schema": _RECEIPT_SCHEMA,
            "command_id": command.command_id,
            "command_digest": _contract_digest(command, field="HostCommand"),
            "work_order_id": work_order.work_order_id,
            "work_order_digest": _contract_digest(work_order, field="WorkOrderV3"),
            "task_graph_id": graph.graph_id,
            "graph_revision": graph.revision,
            "task_graph_digest": _contract_digest(graph, field="TaskGraph"),
            "task_node_id": node.node_id,
            "task_node_digest": _contract_digest(node, field="TaskNode"),
            "workspace_id": scene.workspace_id,
            "subject_id": scene.subject_id,
            "scene_id": scene.scene_id,
            "scene_revision": scene.revision,
            "scene_decision_digest": scene.decision_digest,
            "context_pack_digest": frame.context_pack_digest,
            "intent_frame_digest": frame.frame_digest,
            "arbitration_digest": arbitration.arbitration_digest,
            "decision": decision,
            "goal_digest": frame.goal_digest,
            "requested_action_digest": frame.requested_action_digest,
            "grant_ref": frame.grant_ref,
            "grant_digest": frame.grant_digest,
            "policy_ref": frame.arbiter_policy_ref,
            "policy_digest": frame.arbiter_policy_digest,
            "constraint_set_digest": _digest(
                [item.to_dict() for item in arbitration.constraints]
            ),
            "constraint_dispositions": list(dispositions),
            "proceed_allowed": decision in _PROCEED_DECISIONS,
        }
        payload["receipt_digest"] = _receipt_payload_digest(payload)
        return payload

    def _node_event(
        self,
        *,
        stage: str,
        command: HostCommand,
        graph: TaskGraph,
        node: TaskNode,
        scene: SceneDecisionV2,
        payload: Mapping[str, Any],
    ) -> RuntimeEvent:
        normalized_stage = str(stage or "").strip().upper()
        if normalized_stage == "PREPARED":
            event_type = _PREPARED_NODE_EVENT_TYPE
            aggregate_type = _PREPARED_NODE_AGGREGATE_TYPE
            payload_scheme = "intentpreparation"
        elif normalized_stage == "FINAL":
            event_type = _EVENT_TYPE
            aggregate_type = _AGGREGATE_TYPE
            payload_scheme = "intentadmission"
        else:
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "Intent node receipt stage is invalid",
            )
        command_ref = (
            "hostcommand://sha256/"
            + _digest(command.to_dict()).split(":", 1)[1]
        )
        graph_ref = (
            "taskgraph://sha256/"
            + _digest(graph.to_dict()).split(":", 1)[1]
        )
        scene_ref = (
            "scene://sha256/"
            + _digest(
                {
                    "workspace_id": scene.workspace_id,
                    "subject_id": scene.subject_id,
                    "scene_id": scene.scene_id,
                    "revision": scene.revision,
                    "decision_digest": scene.decision_digest,
                }
            ).split(":", 1)[1]
        )
        aggregate_id = _digest(
            {
                "graph_id": graph.graph_id,
                "graph_revision": graph.revision,
                "node_id": node.node_id,
            }
        )
        payload_ref = (
            f"{payload_scheme}://sha256/"
            + _digest(
                {
                    "graph_id": graph.graph_id,
                    "graph_revision": graph.revision,
                    "node_id": node.node_id,
                }
            ).split(":", 1)[1]
        )
        return RuntimeEvent.create(
            event_type=event_type,
            source_kind="TASK",
            source_ref=command_ref,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=graph.revision,
            payload_ref=payload_ref,
            payload=dict(payload),
            evidence_refs=(
                command_ref,
                graph_ref,
                scene_ref,
                _text(command.governor_grant_ref, field="governor_grant_ref"),
            ),
            redaction_level="INTERNAL_REDACTED",
            # RuntimeEventStore replaces zero with its wall clock.  Pinning a
            # deterministic positive value keeps concurrent/restart replay
            # byte-identical even for imported commands created at epoch zero.
            ts_ms=max(
                1,
                _integer(
                    command.created_at_ms,
                    field="HostCommand created_at_ms",
                ),
            ),
        )

    def _graph_event(
        self,
        *,
        stage: str,
        command: HostCommand,
        work_order: WorkOrderV3,
        graph: TaskGraph,
        scene: SceneDecisionV2,
        payload: Mapping[str, Any],
    ) -> RuntimeEvent:
        normalized_stage = str(stage or "").strip().upper()
        if normalized_stage == "PREPARED":
            event_type = _PREPARED_SET_EVENT_TYPE
            aggregate_type = _PREPARED_SET_AGGREGATE_TYPE
            payload_scheme = "intentpreparedset"
        elif normalized_stage == "COMPLETE":
            event_type = _COMPLETE_SET_EVENT_TYPE
            aggregate_type = _COMPLETE_SET_AGGREGATE_TYPE
            payload_scheme = "intentcompleteset"
        else:
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "Intent graph receipt stage is invalid",
            )
        command_ref, graph_ref, scene_ref = self._authority_refs(
            command=command,
            graph=graph,
            scene=scene,
        )
        aggregate_id = _digest(
            {"graph_id": graph.graph_id, "graph_revision": graph.revision}
        )
        payload_ref = (
            f"{payload_scheme}://sha256/"
            + _digest(
                {"graph_id": graph.graph_id, "graph_revision": graph.revision}
            ).split(":", 1)[1]
        )
        return RuntimeEvent.create(
            event_type=event_type,
            source_kind="TASK",
            source_ref=command_ref,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=graph.revision,
            payload_ref=payload_ref,
            payload=dict(payload),
            evidence_refs=(
                command_ref,
                graph_ref,
                scene_ref,
                _text(command.governor_grant_ref, field="governor_grant_ref"),
            ),
            redaction_level="INTERNAL_REDACTED",
            ts_ms=max(
                1,
                _integer(command.created_at_ms, field="HostCommand created_at_ms"),
            ),
        )

    @staticmethod
    def _authority_refs(
        *,
        command: HostCommand,
        graph: TaskGraph,
        scene: SceneDecisionV2,
    ) -> tuple[str, str, str]:
        command_ref = (
            "hostcommand://sha256/"
            + _digest(command.to_dict()).split(":", 1)[1]
        )
        graph_ref = (
            "taskgraph://sha256/" + _digest(graph.to_dict()).split(":", 1)[1]
        )
        scene_ref = (
            "scene://sha256/"
            + _digest(
                {
                    "workspace_id": scene.workspace_id,
                    "subject_id": scene.subject_id,
                    "scene_id": scene.scene_id,
                    "revision": scene.revision,
                    "decision_digest": scene.decision_digest,
                }
            ).split(":", 1)[1]
        )
        return command_ref, graph_ref, scene_ref

    def _get_event(
        self,
        event: RuntimeEvent,
        *,
        detail: str,
    ) -> Mapping[str, Any] | None:
        try:
            return self._events.get_event_by_id(event_id=event.event_id)
        except Exception as exc:
            raise IntentAdmissionError(INTENT_ADMISSION_INVALID, detail) from exc

    def _append_or_reopen(
        self,
        event: RuntimeEvent,
        *,
        detail: str,
    ) -> Mapping[str, Any]:
        try:
            return self._events.append_event(event)
        except Exception as exc:
            # A committed response can be lost.  Re-open the exact deterministic
            # event before deciding the append failed.
            existing = self._get_event(event, detail=detail)
            if existing is not None:
                return existing
            raise IntentAdmissionError(INTENT_ADMISSION_INVALID, detail) from exc

    @staticmethod
    def _event_identity(event: RuntimeEvent) -> dict[str, Any]:
        return {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "source_kind": event.source_kind,
            "source_ref": event.source_ref,
            "aggregate_type": event.aggregate_type,
            "aggregate_id": event.aggregate_id,
            "aggregate_version": event.aggregate_version,
            "payload_ref": event.payload_ref,
            "evidence_refs": list(event.evidence_refs),
            "dedupe_key": event.dedupe_key,
            "redaction_level": event.redaction_level,
            "ts_ms": event.ts_ms,
        }

    @staticmethod
    def _validate_event_identity(
        record: Mapping[str, Any],
        *,
        expected_event: RuntimeEvent,
        detail: str,
    ) -> dict[str, Any]:
        event = _mapping(record, field=detail)
        for field, value in DurableIntentAdmissionVerifier._event_identity(
            expected_event
        ).items():
            if event.get(field) != value:
                raise IntentAdmissionError(
                    INTENT_ADMISSION_INVALID,
                    f"{detail} {field} drifted",
                )
        return event

    def _validate_receipt(
        self,
        *,
        record: Mapping[str, Any],
        command: HostCommand,
        work_order: WorkOrderV3,
        graph: TaskGraph,
        node: TaskNode,
        scene: SceneDecisionV2,
        expected_event: RuntimeEvent,
        expected_payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        event = self._validate_event_identity(
            record,
            expected_event=expected_event,
            detail="Intent admission RuntimeEvent",
        )
        payload = _mapping(event.get("payload"), field="Intent admission receipt")
        expected_static = {
            "schema": _RECEIPT_SCHEMA,
            "command_id": command.command_id,
            "command_digest": _contract_digest(command, field="HostCommand"),
            "work_order_id": work_order.work_order_id,
            "work_order_digest": _contract_digest(work_order, field="WorkOrderV3"),
            "task_graph_id": graph.graph_id,
            "graph_revision": graph.revision,
            "task_graph_digest": _contract_digest(graph, field="TaskGraph"),
            "task_node_id": node.node_id,
            "task_node_digest": _contract_digest(node, field="TaskNode"),
            "workspace_id": scene.workspace_id,
            "subject_id": scene.subject_id,
            "scene_id": scene.scene_id,
            "scene_revision": scene.revision,
            "scene_decision_digest": scene.decision_digest,
            "grant_ref": _text(
                command.governor_grant_ref,
                field="HostCommand governor_grant_ref",
            ),
        }
        if any(payload.get(field) != value for field, value in expected_static.items()):
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "Intent admission receipt differs from durable command, graph or Scene",
            )
        variable_fields = {
            "context_pack_digest",
            "intent_frame_digest",
            "arbitration_digest",
            "decision",
            "goal_digest",
            "requested_action_digest",
            "grant_digest",
            "policy_ref",
            "policy_digest",
            "constraint_set_digest",
            "constraint_dispositions",
            "proceed_allowed",
            "receipt_digest",
        }
        if set(payload) != set(expected_static) | variable_fields:
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "Intent admission receipt schema contains missing or extra fields",
            )
        decision = str(payload.get("decision") or "").strip().upper()
        if decision not in _ALLOWED_DECISIONS or payload.get("decision") != decision:
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "Intent admission receipt decision is invalid",
            )
        if payload.get("proceed_allowed") is not (decision in _PROCEED_DECISIONS):
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "Intent admission receipt proceed boundary drifted",
            )
        dispositions = payload.get("constraint_dispositions")
        if (
            not isinstance(dispositions, list)
            or dispositions != sorted(set(dispositions))
            or any(item not in _CONSTRAINT_DISPOSITIONS for item in dispositions)
        ):
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "Intent admission constraint disposition set drifted",
            )
        expected_decision = (
            "BLOCK"
            if "BLOCK" in dispositions
            else "REVIEW"
            if "REVIEW" in dispositions
            else "RESTRICT"
            if "RESTRICT" in dispositions
            else "NO_ADDITIONAL_RESTRICTION"
        )
        if decision != expected_decision:
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "Intent admission decision no longer matches its constraints",
            )
        digest_fields = (
            "context_pack_digest",
            "intent_frame_digest",
            "arbitration_digest",
            "goal_digest",
            "requested_action_digest",
            "grant_digest",
            "policy_digest",
            "constraint_set_digest",
            "receipt_digest",
        )
        for field in digest_fields:
            value = _text(payload.get(field), field=field)
            raw = value.removeprefix("sha256:")
            if len(raw) != 64 or any(char not in "0123456789abcdef" for char in raw):
                raise IntentAdmissionError(
                    INTENT_ADMISSION_INVALID,
                    f"Intent admission receipt {field} is invalid",
                )
        _text(payload.get("policy_ref"), field="policy_ref")
        if payload["receipt_digest"] != _receipt_payload_digest(payload):
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "Intent admission receipt digest drifted",
            )
        if expected_payload is not None and payload != dict(expected_payload):
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "Intent admission receipt differs from its prepared result",
            )
        return payload

    @staticmethod
    def _receipt_set_digest(prepared: Mapping[str, Mapping[str, Any]]) -> str:
        return _digest(
            [
                {
                    "task_node_id": node_id,
                    "receipt_digest": payload.get("receipt_digest"),
                }
                for node_id, payload in sorted(prepared.items())
            ]
        )

    def _prepared_marker_payload(
        self,
        *,
        command: HostCommand,
        work_order: WorkOrderV3,
        graph: TaskGraph,
        scene: SceneDecisionV2,
        prepared: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": _PREPARED_SET_SCHEMA,
            "command_id": command.command_id,
            "command_digest": _contract_digest(command, field="HostCommand"),
            "work_order_id": work_order.work_order_id,
            "work_order_digest": _contract_digest(work_order, field="WorkOrderV3"),
            "task_graph_id": graph.graph_id,
            "graph_revision": graph.revision,
            "task_graph_digest": _contract_digest(graph, field="TaskGraph"),
            "workspace_id": scene.workspace_id,
            "subject_id": scene.subject_id,
            "scene_id": scene.scene_id,
            "scene_revision": scene.revision,
            "scene_decision_digest": scene.decision_digest,
            "node_count": len(graph.nodes),
            "node_receipt_set_digest": self._receipt_set_digest(prepared),
            "review_expires_at_ms": max(
                int(command.created_at_ms), int(graph.created_at), 1
            )
            + self._review_ttl_ms,
        }
        payload["prepared_set_digest"] = _receipt_payload_digest(payload)
        return payload

    def _load_prepared_set(
        self,
        *,
        command: HostCommand,
        work_order: WorkOrderV3,
        graph: TaskGraph,
        scene: SceneDecisionV2,
        marker_record: Mapping[str, Any],
    ) -> dict[str, dict[str, Any]]:
        prepared: dict[str, dict[str, Any]] = {}
        for node in tuple(graph.nodes):
            event = self._node_event(
                stage="PREPARED",
                command=command,
                graph=graph,
                node=node,
                scene=scene,
                payload={},
            )
            record = self._get_event(
                event,
                detail="Intent node preparation lookup failed",
            )
            if record is None:
                raise IntentAdmissionError(
                    INTENT_ADMISSION_INVALID,
                    "Intent prepared-set marker references a missing node preparation",
                )
            prepared[node.node_id] = self._validate_receipt(
                record=record,
                command=command,
                work_order=work_order,
                graph=graph,
                node=node,
                scene=scene,
                expected_event=event,
            )
        expected_payload = self._prepared_marker_payload(
            command=command,
            work_order=work_order,
            graph=graph,
            scene=scene,
            prepared=prepared,
        )
        expected_event = self._graph_event(
            stage="PREPARED",
            command=command,
            work_order=work_order,
            graph=graph,
            scene=scene,
            payload=expected_payload,
        )
        event = self._validate_event_identity(
            marker_record,
            expected_event=expected_event,
            detail="Intent prepared-set RuntimeEvent",
        )
        if _mapping(event.get("payload"), field="Intent prepared-set payload") != expected_payload:
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "Intent prepared-set payload drifted",
            )
        return prepared

    def _seal_complete_set(
        self,
        *,
        command: HostCommand,
        work_order: WorkOrderV3,
        graph: TaskGraph,
        scene: SceneDecisionV2,
        prepared: Mapping[str, Mapping[str, Any]],
        final_event_ids: Mapping[str, str],
        continuation_event_ids: Mapping[str, str],
        review_ids: tuple[str, ...],
    ) -> None:
        prepared_event = self._graph_event(
            stage="PREPARED",
            command=command,
            work_order=work_order,
            graph=graph,
            scene=scene,
            payload={},
        )
        payload: dict[str, Any] = {
            "schema": _COMPLETE_SET_SCHEMA,
            "command_id": command.command_id,
            "command_digest": _contract_digest(command, field="HostCommand"),
            "work_order_id": work_order.work_order_id,
            "work_order_digest": _contract_digest(work_order, field="WorkOrderV3"),
            "task_graph_id": graph.graph_id,
            "graph_revision": graph.revision,
            "task_graph_digest": _contract_digest(graph, field="TaskGraph"),
            "scene_id": scene.scene_id,
            "scene_revision": scene.revision,
            "scene_decision_digest": scene.decision_digest,
            "node_count": len(graph.nodes),
            "prepared_marker_event_id": prepared_event.event_id,
            "prepared_receipt_set_digest": self._receipt_set_digest(prepared),
            "final_event_set_digest": _digest(dict(sorted(final_event_ids.items()))),
            "continuation_event_set_digest": _digest(
                dict(sorted(continuation_event_ids.items()))
            ),
            "review_ids_digest": _digest(list(review_ids)),
            "review_count": len(review_ids),
        }
        payload["complete_set_digest"] = _receipt_payload_digest(payload)
        event = self._graph_event(
            stage="COMPLETE",
            command=command,
            work_order=work_order,
            graph=graph,
            scene=scene,
            payload=payload,
        )
        record = self._append_or_reopen(
            event,
            detail="Intent complete-set marker append failed",
        )
        stored = self._validate_event_identity(
            record,
            expected_event=event,
            detail="Intent complete-set RuntimeEvent",
        )
        if _mapping(stored.get("payload"), field="Intent complete-set payload") != payload:
            raise IntentAdmissionError(
                INTENT_ADMISSION_INVALID,
                "Intent complete-set payload drifted",
            )


__all__ = [
    "DurableIntentAdmissionVerifier",
    "INTENT_ADMISSION_BLOCKED",
    "INTENT_ADMISSION_INVALID",
    "INTENT_ADMISSION_REVIEW_REQUIRED",
    "IntentAdmissionError",
]
