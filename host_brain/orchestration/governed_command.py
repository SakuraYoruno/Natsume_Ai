"""Application service for atomic command admission and governed execution."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Mapping
from typing import Any, Protocol

from natsume_contracts.task.v1 import ExecutorAssignment, HostCommand

from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraphCompletionPolicy,
    validate_task_graph_admission_state,
)
from host_brain.task_runtime.coordinator import (
    AdmittedExecution,
    GOVERNED_ADMISSION_INVALID,
    GOVERNED_ASSIGNMENT_CONFLICT,
    GovernedRuntimeError,
    GovernedTaskCoordinator,
)
from host_brain.task_runtime.errors import (
    GOVERNED_CANCELLATION_AUTHORITY_INVALID,
    GOVERNED_CANCELLATION_AUTHORITY_REQUIRED,
)
from host_brain.task_runtime.ports import CommandRepositoryPort


class GraphCancellationAuthorityVerifierPort(Protocol):
    """Resolve one server-owned authority projection to a durable reference."""

    def verify_graph_cancellation(
        self,
        *,
        authority: Mapping[str, Any],
        source: str,
        reason_code: str,
        graph_id: str,
        graph_revision: int,
    ) -> Mapping[str, Any]: ...


class IntentAdmissionVerifierPort(Protocol):
    """Verify one durable Graph against the canonical Intent authorities."""

    def verify_intent_admission(
        self,
        *,
        command: HostCommand,
        work_order: Any,
        graph: Any,
        replayed: bool,
    ) -> Any: ...


class GovernedCommandApplication:
    """Bind command admission to the only allowed Task Runtime entrypoint."""

    def __init__(
        self,
        *,
        command_repository: CommandRepositoryPort,
        coordinator: GovernedTaskCoordinator,
        node_grant_service: Any | None = None,
        cancellation_authority_verifier: GraphCancellationAuthorityVerifierPort | None = None,
        intent_admission_verifier: IntentAdmissionVerifierPort | None = None,
    ) -> None:
        self._commands = command_repository
        self._coordinator = coordinator
        self._node_grants = node_grant_service
        self._cancellation_authority = cancellation_authority_verifier
        self._intent_admission = intent_admission_verifier

    async def execute(
        self,
        *,
        command: HostCommand,
        work_order: Any,
        assignment: ExecutorAssignment,
        graph: Any,
    ) -> dict[str, Any]:
        if not await self._is_intent_receipt_replay(
            command=command,
            work_order=work_order,
            assignment=assignment,
            graph=graph,
        ):
            await self.preflight_parent_governor_binding(
                parent_grant_ref=command.governor_grant_ref,
                request_id=command.request_id,
                session_id=command.session_id,
                principal_id=command.principal_id,
            )
        admitted, persisted_command, persisted_work_order, persisted_graph = await self._admit(
            command=command,
            work_order=work_order,
            assignment=assignment,
            graph=graph,
        )
        await self._verify_intent_admission(
            admitted=admitted,
            command=persisted_command,
            work_order=persisted_work_order,
            graph=persisted_graph,
        )
        if self._node_grants is None:
            return await self._coordinator.execute(
                admitted=admitted,
                command=persisted_command,
                work_order=persisted_work_order,
            )
        node_grant_refs = await self._derive_node_grant_refs(
            admitted=admitted,
            command=persisted_command,
            work_order=persisted_work_order,
            graph=persisted_graph,
        )
        return await self._coordinator.execute(
            admitted=admitted,
            command=persisted_command,
            work_order=persisted_work_order,
            node_grant_refs=node_grant_refs,
        )

    async def execute_stream(
        self,
        *,
        command: HostCommand,
        work_order: Any,
        assignment: ExecutorAssignment,
        graph: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        if not await self._is_intent_receipt_replay(
            command=command,
            work_order=work_order,
            assignment=assignment,
            graph=graph,
        ):
            await self.preflight_parent_governor_binding(
                parent_grant_ref=command.governor_grant_ref,
                request_id=command.request_id,
                session_id=command.session_id,
                principal_id=command.principal_id,
            )
        admitted, persisted_command, persisted_work_order, persisted_graph = await self._admit(
            command=command,
            work_order=work_order,
            assignment=assignment,
            graph=graph,
        )
        await self._verify_intent_admission(
            admitted=admitted,
            command=persisted_command,
            work_order=persisted_work_order,
            graph=persisted_graph,
        )
        execution_kwargs: dict[str, Any] = {
            "admitted": admitted,
            "command": persisted_command,
            "work_order": persisted_work_order,
        }
        if self._node_grants is not None:
            execution_kwargs["node_grant_refs"] = await self._derive_node_grant_refs(
                admitted=admitted,
                command=persisted_command,
                work_order=persisted_work_order,
                graph=persisted_graph,
            )
        async for event in self._coordinator.execute_stream(**execution_kwargs):
            yield event

    async def execute_review_continuation(
        self,
        *,
        command: HostCommand,
        work_order: Any,
        assignment: ExecutorAssignment,
        graph: Any,
        continuation_claim: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Resume one durable Review checkpoint without reopening admission.

        The Review continuation claim is coordination, not business authority.
        ``ReviewStore`` must still prove the exact consumed/denied Review and
        waiting TaskNode.  A consumed Review may mint a short-lived,
        claim-fenced node-grant renewal so a human pause longer than the
        original Governor TTL does not make the durable graph impossible to
        resume.  Denied/expired Reviews need no worker grant: the coordinator
        only reconciles their already-durable terminal decision.
        """

        claim = dict(continuation_claim or {})
        admitted, persisted_command, persisted_work_order, persisted_graph = (
            await self._admit(
                command=command,
                work_order=work_order,
                assignment=assignment,
                graph=graph,
            )
        )
        if (
            str(claim.get("graph_id") or "").strip() != admitted.graph_id
            or int(claim.get("graph_revision") or 0) != admitted.graph_revision
            or str(claim.get("node_id") or "").strip()
            not in {binding.node_id for binding in admitted.nodes}
        ):
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "Review continuation claim differs from the durable Graph admission",
            )
        review_status = str(claim.get("review_status") or "").strip().upper()
        try:
            await self._verify_intent_admission(
                admitted=admitted,
                command=persisted_command,
                work_order=persisted_work_order,
                graph=persisted_graph,
            )
        except GovernedRuntimeError as exc:
            # A denied Intent Review is intentionally non-executable.  The
            # ordinary verifier must keep returning BLOCKED, but the durable
            # 0096 continuation worker still needs a finite checkpoint action
            # instead of deferring forever.  Only the exact terminal Review
            # proof may enter the existing graph-cancellation state machine.
            if (
                review_status not in {"REJECTED", "EXPIRED", "CANCELLED"}
                or exc.reason_code != "INTENT_ADMISSION_BLOCKED"
            ):
                raise
            verifier = self._intent_admission
            prove_terminal = getattr(
                verifier,
                "verify_terminal_intent_review_claim",
                None,
            )
            if not callable(prove_terminal):
                raise GovernedRuntimeError(
                    GOVERNED_ADMISSION_INVALID,
                    "terminal Intent Review verifier is unavailable",
                ) from exc
            try:
                proof = prove_terminal(
                    command=persisted_command,
                    work_order=persisted_work_order,
                    graph=persisted_graph,
                    continuation_claim=claim,
                )
                if inspect.isawaitable(proof):
                    proof = await proof
            except BaseException as proof_exc:
                if isinstance(proof_exc, asyncio.CancelledError):
                    raise
                reason_code = str(
                    getattr(proof_exc, "reason_code", None)
                    or GOVERNED_ADMISSION_INVALID
                ).strip()
                detail = str(
                    getattr(proof_exc, "detail", None)
                    or "terminal Intent Review proof failed"
                ).strip()
                raise GovernedRuntimeError(reason_code, detail) from proof_exc
            if (
                not isinstance(proof, Mapping)
                or proof.get("task_graph_id") != admitted.graph_id
                or proof.get("graph_revision") != admitted.graph_revision
                or proof.get("task_node_id") != claim.get("node_id")
                or proof.get("review_id") != claim.get("review_id")
                or proof.get("review_status") != review_status
            ):
                raise GovernedRuntimeError(
                    GOVERNED_ADMISSION_INVALID,
                    "terminal Intent Review proof differs from continuation claim",
                )
            cancellation = getattr(
                self._coordinator,
                "request_graph_cancellation",
                None,
            )
            if not callable(cancellation):
                raise GovernedRuntimeError(
                    GOVERNED_ADMISSION_INVALID,
                    "governed coordinator cannot terminalize denied Intent Review",
                )
            result = cancellation(
                admitted=admitted,
                command=persisted_command,
                work_order=persisted_work_order,
                request_id=str(proof.get("request_id") or "").strip(),
                request_identity=str(proof.get("request_identity") or "").strip(),
                source="SYSTEM",
                reason_code=str(proof.get("reason_code") or "").strip(),
            )
            if inspect.isawaitable(result):
                result = await result
            if not isinstance(result, Mapping):
                raise GovernedRuntimeError(
                    GOVERNED_ADMISSION_INVALID,
                    "denied Intent Review cancellation returned no durable result",
                )
            return dict(result)

        execution_kwargs: dict[str, Any] = {
            "admitted": admitted,
            "command": persisted_command,
            "work_order": persisted_work_order,
        }
        if self._node_grants is not None and review_status == "CONSUMED":
            derive = getattr(
                self._node_grants,
                "derive_review_continuation",
                None,
            )
            if not callable(derive):
                raise GovernedRuntimeError(
                    GOVERNED_ADMISSION_INVALID,
                    "node grant service cannot renew a durable Review continuation",
                )
            renewed = derive(
                parent_grant_ref=str(persisted_command.governor_grant_ref or "").strip(),
                task_graph=persisted_graph,
                work_order=persisted_work_order,
                request_id=persisted_command.request_id,
                session_id=persisted_command.session_id,
                principal_id=persisted_command.principal_id,
                continuation_claim=claim,
            )
            if inspect.isawaitable(renewed):
                renewed = await renewed
            raw_refs = getattr(renewed, "grant_refs", None)
            refs = dict(raw_refs) if isinstance(raw_refs, Mapping) else {}
            expected_nodes = {binding.node_id for binding in admitted.nodes}
            if set(refs) != expected_nodes or any(
                not str(value or "").strip() for value in refs.values()
            ):
                raise GovernedRuntimeError(
                    GOVERNED_ASSIGNMENT_CONFLICT,
                    "Review continuation renewal does not cover the admitted TaskGraph",
                )
            execution_kwargs["node_grant_refs"] = {
                str(node_id): str(ref) for node_id, ref in refs.items()
            }
        elif review_status not in {"REJECTED", "EXPIRED", "CANCELLED"}:
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "Review continuation requires a canonical terminal Review status",
            )
        return await self._coordinator.execute(**execution_kwargs)

    async def _is_intent_receipt_replay(
        self,
        *,
        command: HostCommand,
        work_order: Any,
        assignment: ExecutorAssignment,
        graph: Any,
    ) -> bool:
        """Recognize only an exact durable replay before parent-grant preflight.

        A composed Intent verifier owns an immutable per-node receipt.  Once
        that receipt exists, replay must be able to validate it after the
        parent grant expires.  New or drifting input still performs the normal
        live parent preflight before any immutable admission is created.
        """

        if self._intent_admission is None or not isinstance(command, HostCommand):
            return False
        getter = getattr(self._commands, "get", None)
        if not callable(getter):
            return False
        try:
            raw = getter(command.command_id)
            if inspect.isawaitable(raw):
                raw = await raw
        except Exception:
            return False
        if not isinstance(raw, Mapping):
            return False
        record = dict(raw)
        durable_command = record.get("command")
        durable_work_order = record.get("work_order")
        durable_assignment = record.get("assignment")
        durable_graph = record.get("graph")
        serializers = (
            (durable_command, command),
            (durable_work_order, work_order),
            (durable_assignment, assignment),
            (durable_graph, graph),
        )
        try:
            exact_contracts = all(
                callable(getattr(durable, "to_dict", None))
                and callable(getattr(candidate, "to_dict", None))
                and durable.to_dict() == candidate.to_dict()
                for durable, candidate in serializers
            )
        except Exception:
            return False
        if not exact_contracts:
            return False
        # A matching durable command is only admission identity, never proof
        # that Intent arbitration finished.  Parent-grant preflight may be
        # skipped only after the verifier reopens the graph-level prepared
        # marker *and* every exact per-node preparation receipt.  Partial
        # writes therefore fail closed instead of inheriting replay authority.
        prove = getattr(
            self._intent_admission,
            "has_prepared_intent_receipt_set",
            None,
        )
        if not callable(prove):
            return False
        try:
            proof = prove(
                command=durable_command,
                work_order=durable_work_order,
                graph=durable_graph,
            )
            if inspect.isawaitable(proof):
                proof = await proof
            return proof is True
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                raise
            return False

    async def _verify_intent_admission(
        self,
        *,
        admitted: AdmittedExecution,
        command: HostCommand,
        work_order: Any,
        graph: Any,
    ) -> None:
        verifier = self._intent_admission
        if verifier is None:
            return
        verify = getattr(verifier, "verify_intent_admission", None)
        if not callable(verify):
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "composed Intent admission verifier is unavailable",
            )
        try:
            result = verify(
                command=command,
                work_order=work_order,
                graph=graph,
                replayed=bool(admitted.replayed),
            )
            if inspect.isawaitable(result):
                result = await result
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                raise
            reason_code = str(
                getattr(exc, "reason_code", None) or GOVERNED_ADMISSION_INVALID
            ).strip()
            detail = str(
                getattr(exc, "detail", None)
                or "canonical Intent admission verification failed"
            ).strip()
            raise GovernedRuntimeError(reason_code, detail) from exc
        expected_nodes = {binding.node_id for binding in admitted.nodes}
        returned_nodes = (
            {
                str(item.get("task_node_id") or "").strip()
                for item in result
                if isinstance(item, Mapping)
            }
            if isinstance(result, (tuple, list))
            else set()
        )
        if (
            not isinstance(result, (tuple, list))
            or len(result) != len(admitted.nodes)
            or len(returned_nodes) != len(result)
            or returned_nodes != expected_nodes
        ):
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "Intent admission verifier did not cover the exact TaskGraph",
            )

    async def request_graph_cancellation(
        self,
        *,
        command_id: str,
        graph_id: str,
        graph_revision: int,
        authority: Mapping[str, Any],
        source: str,
        reason_code: str,
    ) -> dict[str, Any]:
        """Request a graph-wide cancellation through a verified internal boundary.

        The caller may pass only a server-owned, secret-free authority
        projection.  ``request_id`` and ``request_identity`` are issued by the
        verifier after it re-resolves the durable authority; they can never be
        supplied by an HTTP payload or another application caller.
        """

        command_ref = str(command_id or "").strip()
        if not command_ref:
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "graph cancellation requires an existing durable command_id",
            )
        getter = getattr(self._commands, "get", None)
        if not callable(getter):
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "command repository cannot resolve an existing cancellation target",
            )
        record_raw = getter(command_ref)
        if inspect.isawaitable(record_raw):
            record_raw = await record_raw
        if not isinstance(record_raw, Mapping):
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "graph cancellation target was not durably admitted",
            )
        record = dict(record_raw)
        persisted_command = record.get("command")
        persisted_work_order = record.get("work_order")
        persisted_assignment = record.get("assignment")
        persisted_graph = record.get("graph")
        if (
            not isinstance(persisted_command, HostCommand)
            or persisted_command.command_id != command_ref
            or not isinstance(persisted_assignment, ExecutorAssignment)
            or persisted_graph is None
        ):
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "durable cancellation target is incomplete",
            )
        self._require_graph(persisted_graph, require_admission_state=False)
        durable_graph_id = str(getattr(persisted_graph, "graph_id", "") or "").strip()
        durable_revision = getattr(persisted_graph, "revision", None)
        if (
            not isinstance(graph_revision, int)
            or isinstance(graph_revision, bool)
            or not isinstance(durable_revision, int)
            or isinstance(durable_revision, bool)
            or str(graph_id or "").strip() != durable_graph_id
            or graph_revision != durable_revision
        ):
            raise GovernedRuntimeError(
                GOVERNED_CANCELLATION_AUTHORITY_INVALID,
                "graph cancellation target differs from durable admission",
            )

        verifier = self._cancellation_authority
        if verifier is None:
            raise GovernedRuntimeError(
                GOVERNED_CANCELLATION_AUTHORITY_REQUIRED,
                "graph cancellation has no composed authority verifier",
            )
        authority_projection = (
            dict(authority or {}) if isinstance(authority, Mapping) else {}
        )
        authority_request_id = str(
            authority_projection.get("request_id") or ""
        ).strip()
        authority_audit_ref = str(
            authority_projection.get("audit_ref") or ""
        ).strip()
        authority_principal_id = str(
            authority_projection.get("principal_id") or ""
        ).strip()
        requested_binding = {
            "source": str(source or "").strip().upper(),
            "reason_code": str(reason_code or "").strip().upper(),
            "graph_id": durable_graph_id,
            "graph_revision": graph_revision,
        }
        if (
            not isinstance(authority, Mapping)
            or not authority
            or not authority_request_id
            or not authority_audit_ref
            or not authority_principal_id
            or not requested_binding["source"]
            or not requested_binding["reason_code"]
            or not durable_graph_id
            or graph_revision < 1
        ):
            raise GovernedRuntimeError(
                GOVERNED_CANCELLATION_AUTHORITY_INVALID,
                "graph cancellation authority input is incomplete",
            )
        try:
            verified_raw = verifier.verify_graph_cancellation(
                authority=authority_projection,
                **requested_binding,
            )
            if inspect.isawaitable(verified_raw):
                verified_raw = await verified_raw
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise GovernedRuntimeError(
                GOVERNED_CANCELLATION_AUTHORITY_INVALID,
                "graph cancellation authority verification failed",
            ) from exc
        if not isinstance(verified_raw, Mapping):
            raise GovernedRuntimeError(
                GOVERNED_CANCELLATION_AUTHORITY_INVALID,
                "graph cancellation verifier returned no durable binding",
            )
        verified = dict(verified_raw)
        verified_binding: dict[str, Any] = {
            "request_id": str(verified.get("request_id") or "").strip(),
            "request_identity": str(verified.get("request_identity") or "").strip(),
            "source": str(verified.get("source") or "").strip().upper(),
            "reason_code": str(verified.get("reason_code") or "").strip().upper(),
            "graph_id": str(verified.get("graph_id") or "").strip(),
            "graph_revision": verified.get("graph_revision"),
        }
        principal_id = str(verified.get("principal_id") or "").strip()
        if (
            not verified_binding["request_id"]
            or not verified_binding["request_identity"]
            or not principal_id
            or verified_binding["request_id"] != authority_request_id
            or verified_binding["request_identity"] != authority_audit_ref
            or principal_id != authority_principal_id
            or {
                key: verified_binding[key]
                for key in (
                    "source",
                    "reason_code",
                    "graph_id",
                    "graph_revision",
                )
            }
            != requested_binding
        ):
            raise GovernedRuntimeError(
                GOVERNED_CANCELLATION_AUTHORITY_INVALID,
                "verified cancellation authority drifted from its Graph binding",
            )

        admitted = AdmittedExecution.from_contracts(
            command=persisted_command,
            assignment=persisted_assignment,
            graph=persisted_graph,
            replayed=True,
        )
        return await self._coordinator.request_graph_cancellation(
            admitted=admitted,
            command=persisted_command,
            work_order=persisted_work_order,
            request_id=verified_binding["request_id"],
            request_identity=verified_binding["request_identity"],
            source=verified_binding["source"],
            reason_code=verified_binding["reason_code"],
        )

    async def _admit(
        self,
        *,
        command: HostCommand,
        work_order: Any,
        assignment: ExecutorAssignment,
        graph: Any,
    ) -> tuple[AdmittedExecution, HostCommand, Any, Any]:
        if not isinstance(command, HostCommand):
            raise GovernedRuntimeError(GOVERNED_ADMISSION_INVALID, "command must be HostCommand")
        if not isinstance(assignment, ExecutorAssignment):
            raise GovernedRuntimeError(
                GOVERNED_ASSIGNMENT_CONFLICT,
                "assignment must be ExecutorAssignment",
            )
        self._require_ready_graph(graph)
        completion_policy = str(
            getattr(getattr(graph, "completion_policy", None), "value", None)
            or getattr(graph, "completion_policy", None)
            or ""
        ).strip()
        if completion_policy == TaskGraphCompletionPolicy.ATOMIC_GRAPH_SUCCESS_V1.value:
            if self._node_grants is None:
                raise GovernedRuntimeError(
                    GOVERNED_ADMISSION_INVALID,
                    "atomic Graph success requires a node grant service before admission",
                )
            if not str(command.governor_grant_ref or "").strip():
                raise GovernedRuntimeError(
                    GOVERNED_ADMISSION_INVALID,
                    "atomic Graph success requires a parent Governor grant before admission",
                )
            if not self._coordinator.supports_completion_policy(completion_policy):
                raise GovernedRuntimeError(
                    GOVERNED_ADMISSION_INVALID,
                    "runtime cannot admit the TaskGraph's frozen completion policy",
                )
        record_raw = self._commands.admit(command, work_order, assignment, graph)
        if inspect.isawaitable(record_raw):
            record_raw = await record_raw
        if not isinstance(record_raw, Mapping):
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "command repository admission must return a durable record",
            )
        record = dict(record_raw)
        persisted_command = record.get("command", command)
        persisted_assignment = record.get("assignment", assignment)
        persisted_graph = record.get("graph", graph)
        persisted_work_order = record.get("work_order", work_order)
        if not isinstance(persisted_command, HostCommand):
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "durable admission did not return a typed HostCommand",
            )
        if not isinstance(persisted_assignment, ExecutorAssignment):
            raise GovernedRuntimeError(
                GOVERNED_ASSIGNMENT_CONFLICT,
                "durable admission did not return a typed ExecutorAssignment",
            )
        if persisted_command.to_dict() != command.to_dict():
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "durable HostCommand replay differs from the requested command",
            )
        if persisted_assignment.to_dict() != assignment.to_dict():
            raise GovernedRuntimeError(
                GOVERNED_ASSIGNMENT_CONFLICT,
                "durable admission attempted to change the frozen executor assignment",
            )
        self._require_graph(
            persisted_graph,
            require_admission_state=not bool(record.get("replayed")),
        )
        admitted = AdmittedExecution.from_contracts(
            command=persisted_command,
            assignment=persisted_assignment,
            graph=persisted_graph,
            replayed=bool(record.get("replayed")),
        )
        return admitted, persisted_command, persisted_work_order, persisted_graph

    async def _derive_node_grant_refs(
        self,
        *,
        admitted: AdmittedExecution,
        command: HostCommand,
        work_order: Any,
        graph: Any,
    ) -> dict[str, str]:
        parent_ref = str(command.governor_grant_ref or "").strip()
        if not parent_ref:
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "governed TaskGraph has no parent Governor grant",
            )
        derived = self._node_grants.derive(
            parent_grant_ref=parent_ref,
            task_graph=graph,
            work_order=work_order,
            request_id=command.request_id,
            session_id=command.session_id,
            principal_id=command.principal_id,
        )
        if inspect.isawaitable(derived):
            derived = await derived
        raw_refs = getattr(derived, "grant_refs", None)
        refs = dict(raw_refs) if isinstance(raw_refs, Mapping) else {}
        expected_nodes = {binding.node_id for binding in admitted.nodes}
        if (
            set(refs) != expected_nodes
            or any(not str(value or "").strip() for value in refs.values())
            or str(getattr(derived, "parent_grant_ref", "") or "") != parent_ref
            or str(getattr(derived, "task_graph_id", "") or "") != admitted.graph_id
            or int(getattr(derived, "graph_revision", 0) or 0) != admitted.graph_revision
        ):
            raise GovernedRuntimeError(
                GOVERNED_ASSIGNMENT_CONFLICT,
                "node grant batch does not exactly cover the admitted TaskGraph",
            )
        return {str(node_id): str(ref) for node_id, ref in refs.items()}

    async def preflight_parent_governor_binding(
        self,
        *,
        parent_grant_ref: str,
        request_id: str,
        session_id: str,
        principal_id: str,
    ) -> None:
        """Reject an impossible parent grant before immutable admission.

        ``NodeBoundGrantService`` resolves this read-only check from the
        durable Governor ledger. It is deliberately repeated by grant
        derivation after admission so an expiry or takeover race cannot expand
        authority. A configured grant service without this capability is an
        invalid authority boundary, never a compatibility fallback.
        """

        if self._node_grants is None:
            return
        parent_ref = str(parent_grant_ref or "").strip()
        if not parent_ref:
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "governed TaskGraph has no parent Governor grant",
            )
        validate_parent = getattr(
            self._node_grants,
            "validate_parent_binding",
            None,
        )
        if not callable(validate_parent):
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "node grant service lacks parent Governor binding preflight",
            )
        validated = validate_parent(
            parent_grant_ref=parent_ref,
            request_id=request_id,
            session_id=session_id,
            principal_id=principal_id,
        )
        if inspect.isawaitable(validated):
            validated = await validated
        if validated is False:
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "node grant service rejected the parent Governor grant",
            )

    @staticmethod
    def _require_ready_graph(graph: Any) -> None:
        GovernedCommandApplication._require_graph(
            graph,
            require_admission_state=True,
        )

    @staticmethod
    def _require_graph(graph: Any, *, require_admission_state: bool) -> None:
        validator = getattr(graph, "validate", None)
        if not callable(validator):
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "Host command admission requires a validated TaskGraph",
            )
        try:
            validator()
        except Exception as exc:
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                f"TaskGraph dependency topology is invalid: {exc}",
            ) from exc
        nodes = tuple(getattr(graph, "nodes", ()) or ())
        if not nodes:
            raise GovernedRuntimeError(
                GOVERNED_ADMISSION_INVALID,
                "Host command admission requires at least one TaskNode",
            )
        if require_admission_state:
            try:
                validate_task_graph_admission_state(graph)
            except Exception as exc:
                raise GovernedRuntimeError(
                    GOVERNED_ADMISSION_INVALID,
                    f"TaskGraph admission state is invalid: {exc}",
                ) from exc


__all__ = [
    "GovernedCommandApplication",
    "GraphCancellationAuthorityVerifierPort",
    "IntentAdmissionVerifierPort",
]
