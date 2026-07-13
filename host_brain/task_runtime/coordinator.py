"""Durable one-node Task Runtime coordinator.

The coordinator is the execution authority for an admitted Host command.  It
does not create commands or graphs: admission must already have atomically
persisted one graph with one READY node.  Every subsequent state mutation is
outbox-bound, the frozen node route selects exactly one worker, and a worker's
terminal COMMIT is withheld until verification and acceptance both pass.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from natsume_contracts.task.v1 import ExecutorAssignment, HostCommand, TaskExecutor

from .ports import (
    AcceptancePort,
    ExecutionProjectionReaderPort,
    GovernedWorkerResolverPort,
    RuntimeOutboxPublisherPort,
    TaskOutboxFactoryPort,
    TaskStorePort,
    VerificationPort,
)


GOVERNED_ADMISSION_INVALID = "GOVERNED_ADMISSION_INVALID"
GOVERNED_ASSIGNMENT_CONFLICT = "GOVERNED_ASSIGNMENT_CONFLICT"
GOVERNED_DURABLE_STATE_CONFLICT = "GOVERNED_DURABLE_STATE_CONFLICT"
GOVERNED_EXECUTION_IN_PROGRESS = "GOVERNED_EXECUTION_IN_PROGRESS"
GOVERNED_OPERATION_RECOVERY_REQUIRED = "GOVERNED_OPERATION_RECOVERY_REQUIRED"
GOVERNED_RECEIPT_COMMIT_FAILED = "GOVERNED_RECEIPT_COMMIT_FAILED"
GOVERNED_WORKER_COMMIT_INVALID = "GOVERNED_WORKER_COMMIT_INVALID"
GOVERNED_WORKER_FAILED = "GOVERNED_WORKER_FAILED"
GOVERNED_VERIFICATION_FAILED = "GOVERNED_VERIFICATION_FAILED"
GOVERNED_VERIFICATION_EVIDENCE_MISSING = "GOVERNED_VERIFICATION_EVIDENCE_MISSING"
GOVERNED_ACCEPTANCE_FAILED = "GOVERNED_ACCEPTANCE_FAILED"


class GovernedRuntimeError(RuntimeError):
    """Fail-closed runtime error with a stable public reason code."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or GOVERNED_DURABLE_STATE_CONFLICT)
        self.detail = str(detail or "governed runtime operation failed")
        super().__init__(f"{self.reason_code}: {self.detail}")

    def to_dict(self) -> dict[str, str]:
        return {"reason_code": self.reason_code, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class AdmittedExecution:
    """Normalized, immutable view of one atomically admitted command."""

    command_id: str
    work_order_id: str
    assignment_id: str
    executor: str
    graph_id: str
    graph_revision: int
    node_id: str
    worker_route: str
    business_scope: str
    operation_fingerprint: str
    reservation_id: str
    replayed: bool = False

    @classmethod
    def from_contracts(
        cls,
        *,
        command: HostCommand,
        assignment: ExecutorAssignment,
        graph: Any,
        replayed: bool = False,
    ) -> "AdmittedExecution":
        if not isinstance(command, HostCommand):
            raise GovernedRuntimeError(GOVERNED_ADMISSION_INVALID, "command must be HostCommand")
        if not isinstance(assignment, ExecutorAssignment):
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "assignment must be ExecutorAssignment",
            )
        graph_id = _text(getattr(graph, "graph_id", None))
        graph_revision = _exact_positive_int(getattr(graph, "revision", None), "graph.revision")
        graph_root = _text(getattr(graph, "root_work_order_id", None))
        nodes = tuple(getattr(graph, "nodes", ()) or ())
        if len(nodes) != 1:
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "governed command admission requires exactly one frozen TaskNode",
            )
        node = nodes[0]
        node_id = _text(getattr(node, "node_id", None))
        worker_route = _text(getattr(node, "route", None))
        if not graph_id or not node_id or not worker_route:
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "graph identity, node identity, and frozen worker route are required",
            )
        if (
            graph_id != command.graph_id
            or graph_revision != command.graph_revision
            or graph_root != command.root_work_order_id
        ):
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "HostCommand and TaskGraph revision identity differ",
            )
        if assignment.work_order_id != command.root_work_order_id:
            raise GovernedRuntimeError(
                GOVERNED_ASSIGNMENT_CONFLICT,
                "ExecutorAssignment belongs to a different WorkOrder",
            )
        executor = _enum_text(assignment.executor)
        if executor != TaskExecutor.CANONICAL.value:
            raise GovernedRuntimeError(GOVERNED_ASSIGNMENT_CONFLICT, "unsupported executor")
        reservation_seed = "\n".join(
            (
                command.command_id,
                assignment.assignment_id,
                command.business_scope,
                command.operation_fingerprint,
            )
        )
        reservation_id = "reservation::governed::" + hashlib.sha256(
            reservation_seed.encode("utf-8")
        ).hexdigest()
        admitted = cls(
            command_id=command.command_id,
            work_order_id=command.root_work_order_id,
            assignment_id=assignment.assignment_id,
            executor=executor,
            graph_id=graph_id,
            graph_revision=graph_revision,
            node_id=node_id,
            worker_route=worker_route,
            business_scope=command.business_scope,
            operation_fingerprint=command.operation_fingerprint,
            reservation_id=reservation_id,
            replayed=bool(replayed),
        )
        admitted.validate()
        return admitted

    def validate(self) -> None:
        required = (
            self.command_id,
            self.work_order_id,
            self.assignment_id,
            self.executor,
            self.graph_id,
            self.node_id,
            self.worker_route,
            self.business_scope,
            self.operation_fingerprint,
            self.reservation_id,
        )
        if not all(_text(item) for item in required) or self.graph_revision < 1:
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "admitted execution identity is incomplete",
            )
        if self.executor != TaskExecutor.CANONICAL.value:
            raise GovernedRuntimeError(GOVERNED_ASSIGNMENT_CONFLICT, "executor is not frozen")


@dataclass(frozen=True, slots=True)
class GovernedExecutionReceipt:
    command_id: str
    work_order_id: str
    assignment_id: str
    executor: str
    worker_route: str
    graph_id: str
    graph_revision: int
    node_id: str
    attempt_id: str
    reservation_id: str
    receipt_ref: str | None
    payload_digest: str | None
    status: str
    verification_status: str
    accepted: bool
    replayed: bool
    reason_code: str | None = None
    evidence_refs: tuple[str, ...] = ()
    projection: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "work_order_id": self.work_order_id,
            "assignment_id": self.assignment_id,
            "executor": self.executor,
            "worker_route": self.worker_route,
            "graph_id": self.graph_id,
            "graph_revision": self.graph_revision,
            "node_id": self.node_id,
            "attempt_id": self.attempt_id,
            "reservation_id": self.reservation_id,
            "receipt_ref": self.receipt_ref,
            "payload_digest": self.payload_digest,
            "status": self.status,
            "verification_status": self.verification_status,
            "accepted": self.accepted,
            "replayed": self.replayed,
            "reason_code": self.reason_code,
            "evidence_refs": list(self.evidence_refs),
            "projection": dict(self.projection),
        }


class GovernedTaskCoordinator:
    """Apply the one-node durable state machine around exactly one worker."""

    def __init__(
        self,
        *,
        task_store: TaskStorePort,
        outbox_publisher: RuntimeOutboxPublisherPort,
        outbox_factory: TaskOutboxFactoryPort,
        worker_resolver: GovernedWorkerResolverPort,
        verifier: VerificationPort,
        acceptance: AcceptancePort,
        projection_reader: ExecutionProjectionReaderPort | None = None,
        owner_id: str,
        lease_ttl_ms: int = 120_000,
        operation_ttl_ms: int = 120_000,
        publish_limit: int = 200,
    ) -> None:
        owner = _text(owner_id)
        if not owner:
            raise ValueError("owner_id is required")
        if int(lease_ttl_ms) <= 0 or int(operation_ttl_ms) <= 0:
            raise ValueError("lease and operation TTLs must be positive")
        self._store = task_store
        self._publisher = outbox_publisher
        self._outbox_factory = outbox_factory
        self._workers = worker_resolver
        self._verifier = verifier
        self._acceptance = acceptance
        self._projection_reader = projection_reader
        self._owner_id = owner
        self._lease_ttl_ms = int(lease_ttl_ms)
        self._operation_ttl_ms = int(operation_ttl_ms)
        self._publish_limit = max(1, int(publish_limit))

    async def execute(
        self,
        *,
        admitted: AdmittedExecution,
        command: HostCommand,
        work_order: Any,
    ) -> dict[str, Any]:
        terminal: dict[str, Any] | None = None
        async for event in self.execute_stream(
            admitted=admitted,
            command=command,
            work_order=work_order,
        ):
            if bool(event.get("done")):
                terminal = dict(event)
        if terminal is None:
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "governed execution produced no durable terminal projection",
            )
        return terminal

    async def execute_stream(
        self,
        *,
        admitted: AdmittedExecution,
        command: HostCommand,
        work_order: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        admitted.validate()
        if not isinstance(command, HostCommand) or command.command_id != admitted.command_id:
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "runtime command differs from admitted command",
            )

        graph = await _maybe_await(self._store.get_graph(admitted.graph_id, admitted.graph_revision))
        node = await _maybe_await(
            self._store.get_node(admitted.graph_id, admitted.graph_revision, admitted.node_id)
        )
        self._assert_durable_identity(admitted, graph=graph, node=node)

        replay_receipt = await self._terminal_replay(admitted, graph=graph, node=node)
        if replay_receipt is not None:
            await self._publish_outbox()
            yield self._terminal_event(replay_receipt)
            return

        graph_status = _status(graph)
        node_status = _status(node)
        if graph_status not in {"CREATED", "ACTIVE"} or node_status != "READY":
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                f"admission must expose CREATED/ACTIVE graph and READY node, got {graph_status}/{node_status}",
            )

        attempts = await self._attempts_for_node(admitted)
        if attempts:
            raise GovernedRuntimeError(
                GOVERNED_EXECUTION_IN_PROGRESS,
                "an admitted graph revision already owns an attempt; automatic duplicate execution is forbidden",
            )
        existing_reservation = await self._try_operation(admitted.reservation_id)
        if existing_reservation is not None:
            raise GovernedRuntimeError(
                GOVERNED_OPERATION_RECOVERY_REQUIRED,
                "an existing operation reservation has no complete durable terminal projection",
            )

        if graph_status == "CREATED":
            graph = await self._transition_graph(admitted, graph, "ACTIVE")

        attempt_id = _attempt_id(admitted, attempt_no=1)
        attempt = await _maybe_await(
            self._store.create_attempt(
                attempt_id=attempt_id,
                graph_id=admitted.graph_id,
                graph_revision=admitted.graph_revision,
                node_id=admitted.node_id,
                attempt_no=1,
                expected_node_version=_version(node),
                operation_scope=admitted.business_scope,
                operation_fingerprint=admitted.operation_fingerprint,
                outbox=self._outbox(
                    admitted,
                    aggregate_type="TASK_ATTEMPT",
                    aggregate_id=attempt_id,
                    aggregate_version=0,
                    event_type="TASK_ATTEMPT_CREATED",
                    attempt_id=attempt_id,
                ),
            )
        )
        lease = await _maybe_await(
            self._store.acquire_lease(
                attempt_id,
                owner_id=self._owner_id,
                ttl_ms=self._lease_ttl_ms,
                expected_attempt_version=_version(attempt),
                outbox=self._outbox(
                    admitted,
                    aggregate_type="TASK_ATTEMPT",
                    aggregate_id=attempt_id,
                    aggregate_version=_version(attempt) + 1,
                    event_type="TASK_ATTEMPT_LEASED",
                    attempt_id=attempt_id,
                ),
            )
        )
        attempt = await _maybe_await(self._store.get_attempt(attempt_id))
        node = await _maybe_await(
            self._store.get_node(admitted.graph_id, admitted.graph_revision, admitted.node_id)
        )
        lease_id = _required_record_text(lease, "lease_id")
        fencing_token = _record_positive_int(lease, "fencing_token")
        reserved_raw = await _maybe_await(
            self._store.reserve_operation(
                reservation_id=admitted.reservation_id,
                business_scope=admitted.business_scope,
                operation_fingerprint=admitted.operation_fingerprint,
                attempt_id=attempt_id,
                lease_id=lease_id,
                owner_id=self._owner_id,
                fencing_token=fencing_token,
                ttl_ms=self._operation_ttl_ms,
                outbox=self._outbox(
                    admitted,
                    aggregate_type="OPERATION_RESERVATION",
                    aggregate_id=admitted.reservation_id,
                    aggregate_version=0,
                    event_type="OPERATION_RESERVED",
                    attempt_id=attempt_id,
                ),
            )
        )
        reservation_acquired, reservation = _reservation_result(reserved_raw)
        if not reservation_acquired:
            raise GovernedRuntimeError(
                GOVERNED_OPERATION_RECOVERY_REQUIRED,
                f"operation reservation is already {_status(reservation) or 'owned'}",
            )

        node = await self._transition_node(admitted, node, "RUNNING")
        attempt = await self._transition_attempt(
            admitted,
            attempt,
            lease_id=lease_id,
            fencing_token=fencing_token,
            target_status="RUNNING",
        )
        execution_context = {
            "execution_mode": "governed_task_runtime",
            "command_id": admitted.command_id,
            "request_id": command.request_id,
            "principal_id": command.principal_id,
            "target_id": command.target_id,
            "work_order_id": admitted.work_order_id,
            "assignment_id": admitted.assignment_id,
            "executor": admitted.executor,
            "worker_route": admitted.worker_route,
            "graph_id": admitted.graph_id,
            "graph_revision": admitted.graph_revision,
            "node_id": admitted.node_id,
            "attempt_id": attempt_id,
            "lease_id": lease_id,
            "fencing_token": fencing_token,
            "reservation_id": admitted.reservation_id,
        }

        try:
            worker_commit: dict[str, Any] | None = None
            commit_count = 0
            worker = self._workers.resolve(
                executor=admitted.executor,
                worker_route=admitted.worker_route,
            )
            async for raw_event in _worker_event_stream(
                worker,
                command=command,
                work_order=work_order,
                execution_context=execution_context,
            ):
                event = _mapping(raw_event, field_name="worker event")
                if _is_terminal_commit(event):
                    commit_count += 1
                    worker_commit = event
                    continue
                yield event
            if commit_count != 1 or worker_commit is None:
                raise GovernedRuntimeError(
                    GOVERNED_WORKER_COMMIT_INVALID,
                    f"worker must produce exactly one final COMMIT, got {commit_count}",
                )
        except BaseException as exc:
            await self._mark_worker_failure(
                admitted,
                attempt_id=attempt_id,
                fencing_token=fencing_token,
                reason_code=_exception_reason(exc, GOVERNED_WORKER_FAILED),
            )
            receipt = self._receipt(
                admitted,
                attempt_id=attempt_id,
                status="FAILED",
                verification_status="NOT_RUN",
                accepted=False,
                reason_code=_exception_reason(exc, GOVERNED_WORKER_FAILED),
            )
            yield self._terminal_event(receipt)
            return

        payload_digest = _payload_digest(worker_commit)
        digest_ref = f"sha256://execution/{payload_digest.split(':', 1)[1]}"
        receipt_ref = _worker_receipt_ref(worker_commit) or (
            "taskreceipt://"
            f"{_ref_token(admitted.graph_id)}/{admitted.graph_revision}/"
            f"{_ref_token(admitted.node_id)}/{_ref_token(attempt_id)}/"
            f"{payload_digest.split(':', 1)[1]}"
        )
        evidence_refs = (receipt_ref, digest_ref)
        try:
            reservation = await _maybe_await(
                self._store.commit_operation(
                    admitted.reservation_id,
                    attempt_id=attempt_id,
                    lease_id=lease_id,
                    owner_id=self._owner_id,
                    fencing_token=fencing_token,
                    expected_version=_version(reservation),
                    receipt_ref=receipt_ref,
                    outbox=self._outbox(
                        admitted,
                        aggregate_type="OPERATION_RESERVATION",
                        aggregate_id=admitted.reservation_id,
                        aggregate_version=_version(reservation) + 1,
                        event_type="OPERATION_COMMITTED",
                        attempt_id=attempt_id,
                        evidence_refs=evidence_refs,
                    ),
                )
            )
        except BaseException as exc:
            await self._publish_outbox(best_effort=True)
            raise GovernedRuntimeError(
                GOVERNED_RECEIPT_COMMIT_FAILED,
                f"worker receipt could not be committed before terminal state: {_exception_reason(exc, 'UNKNOWN')}",
            ) from exc

        attempt = await self._transition_attempt(
            admitted,
            attempt,
            lease_id=lease_id,
            fencing_token=fencing_token,
            target_status="SUCCEEDED",
            evidence_refs=evidence_refs,
        )
        node = await self._transition_node(
            admitted,
            node,
            "EXECUTED",
            evidence_refs=evidence_refs,
        )
        node = await self._transition_node(
            admitted,
            node,
            "VERIFYING",
            evidence_refs=evidence_refs,
        )

        verification_raw = await _maybe_await(
            self._verifier.verify(
                command=command,
                work_order=work_order,
                execution_context=execution_context,
                worker_commit=worker_commit,
                evidence_refs=evidence_refs,
            )
        )
        verification = _mapping(verification_raw, field_name="verification result")
        verification_refs = _evidence_refs(verification)
        verification_status = _text(verification.get("status")).upper()
        missing_verification_refs = tuple(ref for ref in evidence_refs if ref not in verification_refs)
        if verification_status == "PASS" and missing_verification_refs:
            verification = {
                **verification,
                "status": "FAIL",
                "reason_code": GOVERNED_VERIFICATION_EVIDENCE_MISSING,
                "evidence_refs": list(dict.fromkeys((*verification_refs, *evidence_refs))),
            }
            verification_refs = _evidence_refs(verification)
            verification_status = "FAIL"

        if verification_status != "PASS":
            node = await self._transition_node(
                admitted,
                node,
                "VERIFICATION_FAILED",
                evidence_refs=verification_refs or evidence_refs,
                verification_result=verification,
            )
            graph = await self._transition_graph(admitted, graph, "FAILED")
            await self._publish_outbox()
            receipt = self._receipt(
                admitted,
                attempt_id=attempt_id,
                receipt_ref=receipt_ref,
                payload_digest=payload_digest,
                status="VERIFICATION_FAILED",
                verification_status=verification_status or "ERROR",
                accepted=False,
                reason_code=_text(verification.get("reason_code")) or GOVERNED_VERIFICATION_FAILED,
                evidence_refs=verification_refs or evidence_refs,
                projection=worker_commit,
            )
            yield self._terminal_event(receipt)
            return

        node = await self._transition_node(
            admitted,
            node,
            "VERIFIED",
            evidence_refs=verification_refs,
            verification_result=verification,
        )
        acceptance_raw = await _maybe_await(
            self._acceptance.judge(
                command=command,
                work_order=work_order,
                execution_context=execution_context,
                worker_commit=worker_commit,
                verification_result=verification,
                evidence_refs=verification_refs,
            )
        )
        acceptance = _mapping(acceptance_raw, field_name="acceptance judgement")
        acceptance_refs = _evidence_refs(acceptance)
        accepted = _text(acceptance.get("decision")).upper() == "ACCEPT"
        missing_acceptance_refs = tuple(ref for ref in evidence_refs if ref not in acceptance_refs)
        if accepted and missing_acceptance_refs:
            accepted = False
            acceptance = {
                **acceptance,
                "decision": "REVIEW",
                "reason_code": GOVERNED_ACCEPTANCE_FAILED,
                "evidence_refs": list(dict.fromkeys((*acceptance_refs, *evidence_refs))),
            }
            acceptance_refs = _evidence_refs(acceptance)

        if not accepted:
            node = await self._transition_node(
                admitted,
                node,
                "WAITING_ACCEPTANCE_REVIEW",
                evidence_refs=acceptance_refs or verification_refs,
            )
            graph = await self._transition_graph(admitted, graph, "BLOCKED")
            await self._publish_outbox()
            receipt = self._receipt(
                admitted,
                attempt_id=attempt_id,
                receipt_ref=receipt_ref,
                payload_digest=payload_digest,
                status="WAITING_ACCEPTANCE_REVIEW",
                verification_status="PASS",
                accepted=False,
                reason_code=_text(acceptance.get("reason_code")) or GOVERNED_ACCEPTANCE_FAILED,
                evidence_refs=acceptance_refs or verification_refs,
                projection=worker_commit,
            )
            yield self._terminal_event(receipt)
            return

        node = await self._transition_node(
            admitted,
            node,
            "ACCEPTED",
            evidence_refs=acceptance_refs,
            acceptance_judgement=acceptance,
        )
        graph = await self._transition_graph(admitted, graph, "COMPLETED")
        await self._publish_outbox()
        receipt = self._receipt(
            admitted,
            attempt_id=attempt_id,
            receipt_ref=receipt_ref,
            payload_digest=payload_digest,
            status="ACCEPTED",
            verification_status="PASS",
            accepted=True,
            evidence_refs=acceptance_refs,
            projection=worker_commit,
        )
        yield self._approved_commit_event(worker_commit, receipt)

    def _assert_durable_identity(
        self,
        admitted: AdmittedExecution,
        *,
        graph: Mapping[str, Any],
        node: Mapping[str, Any],
    ) -> None:
        graph_payload = _nested_mapping(graph, "graph")
        durable_graph_id = _text(graph.get("graph_id") or graph_payload.get("graph_id"))
        durable_revision = int(graph.get("revision") or graph_payload.get("revision") or 0)
        durable_root = _text(graph.get("root_work_order_id") or graph_payload.get("root_work_order_id"))
        node_payload = _nested_mapping(node, "node")
        durable_node_id = _text(node.get("node_id") or node_payload.get("node_id"))
        durable_route = _text(node.get("route") or node_payload.get("route"))
        if (
            durable_graph_id != admitted.graph_id
            or durable_revision != admitted.graph_revision
            or durable_root != admitted.work_order_id
            or durable_node_id != admitted.node_id
        ):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "durable graph/node identity differs from admitted command",
            )
        if durable_route != admitted.worker_route:
            raise GovernedRuntimeError(
                GOVERNED_ASSIGNMENT_CONFLICT,
                "durable TaskNode.route differs from the frozen worker route",
            )

    async def _terminal_replay(
        self,
        admitted: AdmittedExecution,
        *,
        graph: Mapping[str, Any],
        node: Mapping[str, Any],
    ) -> GovernedExecutionReceipt | None:
        graph_status = _status(graph)
        node_status = _status(node)
        waiting_acceptance_review = (
            graph_status == "BLOCKED"
            and node_status == "WAITING_ACCEPTANCE_REVIEW"
        )
        terminal = (
            graph_status in {"COMPLETED", "FAILED", "CANCELLED"}
            or node_status in {
                "ACCEPTED",
                "FAILED",
                "VERIFICATION_FAILED",
                "CANCELLED",
            }
            or waiting_acceptance_review
        )
        if not terminal:
            return None
        reservation = await self._try_operation(admitted.reservation_id)
        attempts = await self._attempts_for_node(admitted)
        latest = attempts[-1] if attempts else {}
        attempt_id = _text(latest.get("attempt_id")) or _attempt_id(admitted, attempt_no=1)
        reservation_status = _status(reservation or {})
        receipt_ref = _text((reservation or {}).get("receipt_ref")) or None
        accepted = graph_status == "COMPLETED" and node_status == "ACCEPTED"
        if accepted and (reservation_status != "COMMITTED" or not receipt_ref):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "accepted graph lacks its committed operation receipt",
            )
        if waiting_acceptance_review and (
            reservation_status != "COMMITTED" or not receipt_ref
        ):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "acceptance-review graph lacks its committed operation receipt",
            )
        projection: Mapping[str, Any] | None = None
        if accepted and receipt_ref and self._projection_reader is not None:
            loaded = await _maybe_await(self._projection_reader.load(receipt_ref))
            if not isinstance(loaded, Mapping):
                raise GovernedRuntimeError(
                    GOVERNED_DURABLE_STATE_CONFLICT,
                    "accepted receipt has no durable public projection",
                )
            projection = dict(loaded)
        return self._receipt(
            admitted,
            attempt_id=attempt_id,
            receipt_ref=receipt_ref,
            payload_digest=_digest_from_receipt_ref(receipt_ref),
            status=node_status or graph_status,
            verification_status=(
                "PASS"
                if accepted or waiting_acceptance_review
                else "FAIL"
                if node_status == "VERIFICATION_FAILED"
                else "UNKNOWN"
            ),
            accepted=accepted,
            replayed=True,
            reason_code=None if accepted else f"GOVERNED_REPLAY_{node_status or graph_status}",
            evidence_refs=(receipt_ref,) if receipt_ref else (),
            projection=projection,
        )

    async def _attempts_for_node(self, admitted: AdmittedExecution) -> list[dict[str, Any]]:
        snapshot = await _maybe_await(
            self._store.read_scheduler_snapshot(admitted.graph_id, admitted.graph_revision)
        )
        attempts = snapshot.get("attempts") if isinstance(snapshot, Mapping) else None
        if not isinstance(attempts, list):
            raise GovernedRuntimeError(
                GOVERNED_DURABLE_STATE_CONFLICT,
                "durable scheduler snapshot does not expose attempts",
            )
        rows = [
            dict(item)
            for item in attempts
            if isinstance(item, Mapping) and _text(item.get("node_id")) == admitted.node_id
        ]
        rows.sort(key=lambda item: (int(item.get("attempt_no") or 0), _text(item.get("attempt_id"))))
        return rows

    async def _try_operation(self, reservation_id: str) -> dict[str, Any] | None:
        try:
            value = await _maybe_await(self._store.get_operation_reservation(reservation_id))
        except Exception as exc:
            reason = _exception_reason(exc, "")
            if reason in {"TASK_STORE_NOT_FOUND", "NOT_FOUND", "OPERATION_NOT_FOUND"}:
                return None
            raise
        return dict(value) if isinstance(value, Mapping) else None

    async def _transition_graph(
        self,
        admitted: AdmittedExecution,
        graph: Mapping[str, Any],
        target_status: str,
    ) -> dict[str, Any]:
        version = _version(graph)
        return dict(
            await _maybe_await(
                self._store.transition_graph(
                    admitted.graph_id,
                    admitted.graph_revision,
                    expected_version=version,
                    target_status=target_status,
                    outbox=self._outbox(
                        admitted,
                        aggregate_type="TASK_GRAPH",
                        aggregate_id=admitted.graph_id,
                        aggregate_version=version + 1,
                        event_type=f"TASK_GRAPH_{target_status}",
                    ),
                )
            )
        )

    async def _transition_node(
        self,
        admitted: AdmittedExecution,
        node: Mapping[str, Any],
        target_status: str,
        *,
        evidence_refs: Sequence[str] = (),
        verification_result: Mapping[str, Any] | None = None,
        acceptance_judgement: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        version = _version(node)
        return dict(
            await _maybe_await(
                self._store.transition_node(
                    admitted.graph_id,
                    admitted.graph_revision,
                    admitted.node_id,
                    expected_version=version,
                    target_status=target_status,
                    outbox=self._outbox(
                        admitted,
                        aggregate_type="TASK_NODE",
                        aggregate_id=(
                            f"{admitted.graph_id}@{admitted.graph_revision}/{admitted.node_id}"
                        ),
                        aggregate_version=version + 1,
                        event_type=f"TASK_NODE_{target_status}",
                        evidence_refs=evidence_refs,
                    ),
                    verification_result=verification_result,
                    acceptance_judgement=acceptance_judgement,
                )
            )
        )

    async def _transition_attempt(
        self,
        admitted: AdmittedExecution,
        attempt: Mapping[str, Any],
        *,
        lease_id: str,
        fencing_token: int,
        target_status: str,
        evidence_refs: Sequence[str] = (),
        error_ref: str | None = None,
    ) -> dict[str, Any]:
        del lease_id  # binding is checked by the durable store through the active lease
        attempt_id = _required_record_text(attempt, "attempt_id")
        version = _version(attempt)
        return dict(
            await _maybe_await(
                self._store.transition_attempt(
                    attempt_id,
                    owner_id=self._owner_id,
                    fencing_token=fencing_token,
                    expected_version=version,
                    target_status=target_status,
                    error_ref=error_ref,
                    outbox=self._outbox(
                        admitted,
                        aggregate_type="TASK_ATTEMPT",
                        aggregate_id=attempt_id,
                        aggregate_version=version + 1,
                        event_type=f"TASK_ATTEMPT_{target_status}",
                        attempt_id=attempt_id,
                        evidence_refs=evidence_refs,
                    ),
                )
            )
        )

    async def _mark_worker_failure(
        self,
        admitted: AdmittedExecution,
        *,
        attempt_id: str,
        fencing_token: int,
        reason_code: str,
    ) -> None:
        error_ref = "taskerror://" + hashlib.sha256(
            f"{attempt_id}\n{reason_code}".encode("utf-8")
        ).hexdigest()
        try:
            attempt = await _maybe_await(self._store.get_attempt(attempt_id))
            if _status(attempt) == "RUNNING":
                await self._transition_attempt(
                    admitted,
                    attempt,
                    lease_id="",
                    fencing_token=fencing_token,
                    target_status="FAILED",
                    error_ref=error_ref,
                )
            node = await _maybe_await(
                self._store.get_node(admitted.graph_id, admitted.graph_revision, admitted.node_id)
            )
            if _status(node) == "RUNNING":
                await self._transition_node(admitted, node, "FAILED", evidence_refs=(error_ref,))
            graph = await _maybe_await(self._store.get_graph(admitted.graph_id, admitted.graph_revision))
            if _status(graph) == "ACTIVE":
                await self._transition_graph(admitted, graph, "FAILED")
        finally:
            await self._publish_outbox(best_effort=True)

    def _outbox(
        self,
        admitted: AdmittedExecution,
        *,
        aggregate_type: str,
        aggregate_id: str,
        aggregate_version: int,
        event_type: str,
        attempt_id: str | None = None,
        evidence_refs: Sequence[str] = (),
    ) -> Any:
        material = "\n".join(
            (
                admitted.command_id,
                aggregate_type,
                aggregate_id,
                str(aggregate_version),
                event_type,
            )
        )
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        return self._outbox_factory.create(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=int(aggregate_version),
            event_type=event_type,
            payload_ref=f"taskstore://event/{aggregate_type}/{aggregate_id}/{int(aggregate_version)}",
            idempotency_key=f"governed:{digest}",
            attempt_id=attempt_id,
            evidence_refs=tuple(dict.fromkeys(_text(item) for item in evidence_refs if _text(item))),
            outbox_id=f"outbox::governed::{digest}",
        )

    async def _publish_outbox(self, *, best_effort: bool = False) -> dict[str, Any]:
        try:
            result = await _maybe_await(self._publisher.publish_pending(limit=self._publish_limit))
        except Exception:
            if best_effort:
                return {"status": "DEGRADED"}
            raise
        return dict(result) if isinstance(result, Mapping) else {"status": "OK"}

    def _receipt(
        self,
        admitted: AdmittedExecution,
        *,
        attempt_id: str,
        status: str,
        verification_status: str,
        accepted: bool,
        receipt_ref: str | None = None,
        payload_digest: str | None = None,
        replayed: bool | None = None,
        reason_code: str | None = None,
        evidence_refs: Sequence[str] = (),
        projection: Mapping[str, Any] | None = None,
    ) -> GovernedExecutionReceipt:
        return GovernedExecutionReceipt(
            command_id=admitted.command_id,
            work_order_id=admitted.work_order_id,
            assignment_id=admitted.assignment_id,
            executor=admitted.executor,
            worker_route=admitted.worker_route,
            graph_id=admitted.graph_id,
            graph_revision=admitted.graph_revision,
            node_id=admitted.node_id,
            attempt_id=attempt_id,
            reservation_id=admitted.reservation_id,
            receipt_ref=receipt_ref,
            payload_digest=payload_digest,
            status=str(status),
            verification_status=str(verification_status),
            accepted=bool(accepted),
            replayed=admitted.replayed if replayed is None else bool(replayed),
            reason_code=str(reason_code or "").strip() or None,
            evidence_refs=tuple(dict.fromkeys(_text(item) for item in evidence_refs if _text(item))),
            projection=dict(projection or {}),
        )

    @staticmethod
    def _approved_commit_event(
        worker_commit: Mapping[str, Any],
        receipt: GovernedExecutionReceipt,
    ) -> dict[str, Any]:
        event = dict(worker_commit)
        event.update(
            {
                "phase": "COMMIT",
                "done": True,
                "decision": "APPROVE",
                "reason_code": None,
                "governed": True,
                "execution_receipt": receipt.to_dict(),
            }
        )
        return event

    @staticmethod
    def _terminal_event(receipt: GovernedExecutionReceipt) -> dict[str, Any]:
        if receipt.accepted:
            event = dict(receipt.projection)
            event.update({
                "phase": "COMMIT",
                "done": True,
                "decision": "APPROVE",
                "reason_code": None,
                "governed": True,
                "replayed": receipt.replayed,
                "execution_receipt": receipt.to_dict(),
            })
            return event
        return {
            "phase": "TERMINAL",
            "done": True,
            "decision": "REJECT",
            "reason_code": receipt.reason_code or GOVERNED_DURABLE_STATE_CONFLICT,
            "governed": True,
            "replayed": receipt.replayed,
            "execution_receipt": receipt.to_dict(),
        }


async def _worker_event_stream(
    worker: Any,
    *,
    command: HostCommand,
    work_order: Any,
    execution_context: Mapping[str, Any],
) -> AsyncIterator[Mapping[str, Any]]:
    stream_method = getattr(worker, "execute_stream", None)
    if callable(stream_method):
        stream = stream_method(
            command=command,
            work_order=work_order,
            execution_context=execution_context,
        )
        if inspect.isawaitable(stream):
            stream = await stream
        if hasattr(stream, "__aiter__"):
            async for event in stream:
                yield event
            return
        if isinstance(stream, Sequence) and not isinstance(stream, (str, bytes, bytearray)):
            for event in stream:
                yield event
            return
        raise GovernedRuntimeError(GOVERNED_WORKER_FAILED, "worker stream is not iterable")

    execute_method = getattr(worker, "execute", None)
    if not callable(execute_method):
        raise GovernedRuntimeError(
            GOVERNED_WORKER_FAILED,
            "selected worker exposes neither execute_stream nor execute",
        )
    result = execute_method(
        command=command,
        work_order=work_order,
        execution_context=execution_context,
    )
    result = await _maybe_await(result)
    payload = _mapping(result, field_name="worker result")
    if not _is_terminal_commit(payload):
        payload = {**payload, "phase": "COMMIT", "done": True}
    yield payload


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _mapping(value: Any, *, field_name: str) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    serializer = getattr(value, "to_dict", None)
    if callable(serializer):
        payload = serializer()
        if isinstance(payload, Mapping):
            return dict(payload)
    raise GovernedRuntimeError(GOVERNED_DURABLE_STATE_CONFLICT, f"{field_name} must be a mapping")


def _nested_mapping(value: Mapping[str, Any], key: str) -> dict[str, Any]:
    nested = value.get(key)
    return dict(nested) if isinstance(nested, Mapping) else {}


def _text(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip()


def _enum_text(value: Any) -> str:
    return _text(value).lower()


def _exact_positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise GovernedRuntimeError(GOVERNED_ADMISSION_INVALID, f"{field_name} must be an integer >= 1")
    return value


def _status(record: Mapping[str, Any]) -> str:
    if not isinstance(record, Mapping):
        return ""
    nested = record.get("node") if isinstance(record.get("node"), Mapping) else record.get("graph")
    return _text(record.get("status") or (nested or {}).get("status")).upper()


def _version(record: Mapping[str, Any]) -> int:
    value = record.get("status_version") if isinstance(record, Mapping) else None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "durable record lacks an exact non-negative status_version",
        )
    return value


def _required_record_text(record: Mapping[str, Any], key: str) -> str:
    value = _text(record.get(key)) if isinstance(record, Mapping) else ""
    if not value:
        raise GovernedRuntimeError(GOVERNED_DURABLE_STATE_CONFLICT, f"durable {key} is required")
    return value


def _record_positive_int(record: Mapping[str, Any], key: str) -> int:
    value = record.get(key) if isinstance(record, Mapping) else None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise GovernedRuntimeError(GOVERNED_DURABLE_STATE_CONFLICT, f"durable {key} must be positive")
    return value


def _reservation_result(value: Any) -> tuple[bool, dict[str, Any]]:
    if isinstance(value, Mapping):
        acquired = value.get("acquired")
        reservation = value.get("reservation")
    else:
        acquired = getattr(value, "acquired", None)
        reservation = getattr(value, "reservation", None)
    if not isinstance(acquired, bool) or not isinstance(reservation, Mapping):
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "reserve_operation returned an invalid result",
        )
    return acquired, dict(reservation)


def _attempt_id(admitted: AdmittedExecution, *, attempt_no: int) -> str:
    material = (
        f"{admitted.graph_id}@{admitted.graph_revision}/{admitted.node_id}/"
        f"{admitted.assignment_id}/{int(attempt_no)}"
    )
    return "attempt::governed::" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _payload_digest(value: Mapping[str, Any]) -> str:
    try:
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise GovernedRuntimeError(
            GOVERNED_WORKER_COMMIT_INVALID,
            "worker COMMIT is not canonical finite JSON",
        ) from exc
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _is_terminal_commit(event: Mapping[str, Any]) -> bool:
    return _text(event.get("phase")).upper() == "COMMIT" and bool(event.get("done"))


def _worker_receipt_ref(event: Mapping[str, Any]) -> str | None:
    direct = _text(event.get("receipt_ref"))
    if direct:
        return direct
    nested = event.get("execution_receipt")
    if isinstance(nested, Mapping):
        value = _text(nested.get("receipt_ref"))
        return value or None
    return None


def _evidence_refs(value: Mapping[str, Any]) -> tuple[str, ...]:
    raw = value.get("evidence_refs") if isinstance(value, Mapping) else None
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(dict.fromkeys(_text(item) for item in raw if _text(item)))


def _exception_reason(exc: BaseException, default: str) -> str:
    return _text(getattr(exc, "reason_code", None)) or _text(default) or exc.__class__.__name__.upper()


def _ref_token(value: Any) -> str:
    text = _text(value)
    return "".join(char if char.isalnum() or char in "._-" else "-" for char in text).strip("-") or "unknown"


def _digest_from_receipt_ref(receipt_ref: str | None) -> str | None:
    if not receipt_ref:
        return None
    tail = receipt_ref.rsplit("/", 1)[-1]
    if len(tail) == 64 and all(char in "0123456789abcdef" for char in tail.lower()):
        return "sha256:" + tail.lower()
    return None


__all__ = [
    "AdmittedExecution",
    "GOVERNED_ACCEPTANCE_FAILED",
    "GOVERNED_ADMISSION_INVALID",
    "GOVERNED_ASSIGNMENT_CONFLICT",
    "GOVERNED_DURABLE_STATE_CONFLICT",
    "GOVERNED_EXECUTION_IN_PROGRESS",
    "GOVERNED_OPERATION_RECOVERY_REQUIRED",
    "GOVERNED_RECEIPT_COMMIT_FAILED",
    "GOVERNED_VERIFICATION_FAILED",
    "GOVERNED_WORKER_COMMIT_INVALID",
    "GOVERNED_WORKER_FAILED",
    "GovernedExecutionReceipt",
    "GovernedRuntimeError",
    "GovernedTaskCoordinator",
]
