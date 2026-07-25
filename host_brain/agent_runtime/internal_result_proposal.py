"""Governed Internal Agent proposal execution and outer verification.

The canonical Agent executor remains a speculative node worker: it may perform
read/model computation inside the node Lease, but write-capable WorkOrders are
fail-closed by ``AgentRuntimeExecutor``.  This module never publishes an
``agent_execution_result``.  Instead it stages the complete proposal in the
durable worker projection; only the post-Acceptance result-commit handler may
promote that proposal to the Agent result ledger.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
import hashlib
import json
import re
from typing import Any, Protocol

from host_brain.agent_runtime.executor import AgentRuntimeExecutor
from host_brain.contracts.agent_runtime.projection_recovery import (
    ProjectionRecoveryContractError,
    validate_projection_recovery_envelope,
)
from host_brain.core.trace_redaction import sanitize_public_payload
from host_brain.contracts.agent_runtime.task_graph import TaskGraph
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.persistence.worker_projection_repository import (
    WorkerProjectionStoreError,
    validate_worker_projection_payload,
)
from host_brain.task_runtime.governed_workers import FrozenNodeInput
from host_brain.contracts.agent_runtime.worker_budget_usage import (
    build_worker_budget_usage,
    stable_worker_usage_id,
)
from natsume_contracts.task import HostCommand, HostCommandSurface
from natsume_contracts.task.v1 import ExecutorAssignment, TaskExecutor


INTERNAL_AGENT_RESULT_COMMIT_EFFECT_TYPE = "internal.agent.result_commit"
INTERNAL_AGENT_PROPOSAL_SCHEMA_VERSION = (
    "Natsume.internal.agent_result_proposal.v1"
)

INTERNAL_AGENT_PROPOSAL_INVALID = "INTERNAL_AGENT_PROPOSAL_INVALID"
INTERNAL_AGENT_PROPOSAL_PROJECTION_INVALID = (
    "INTERNAL_AGENT_PROPOSAL_PROJECTION_INVALID"
)
_MAX_RESULT_BYTES = 16_777_216
_SENSITIVE_NON_TOKEN = re.compile(
    r"(?:^|_)(?:api_?key|authorization|bearer|cookie|credential|password|"
    r"private_?key|raw_?auth|secret|signature)(?:$|_)",
    re.IGNORECASE,
)
_TOKEN_FIELD = re.compile(r"(?:^|_)tokens?(?:$|_)", re.IGNORECASE)
_SAFE_TOKEN_METADATA = re.compile(
    r"^(?:token_(?:count|budget|limit|usage)|"
    r"(?:prompt|completion|input|output|total|cached|reasoning|actual|estimated|budget|context)_tokens?|"
    r"(?:max|min)(?:_context)?_tokens|"
    r"(?:enforce_)?context_token_budget|context_token_estimator|token_budget_tokens|"
    r"(?:first_token|time_to_first_token|stream_after_first_token)(?:_latency)?_ms)$",
    re.IGNORECASE,
)
class InternalAgentProposalError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or INTERNAL_AGENT_PROPOSAL_INVALID)
        self.detail = str(detail or "Internal Agent proposal is invalid")
        super().__init__(f"{self.reason_code}: {self.detail}")


class CommandAdmissionReaderPort(Protocol):
    def get(self, command_id: str) -> Mapping[str, Any] | None: ...


class WorkerProjectionReaderPort(Protocol):
    def load(self, receipt_ref: str) -> Mapping[str, Any] | None: ...


class InternalAgentProposalExecutor:
    """Run one already-admitted proposal without owning result authority."""

    def __init__(
        self,
        *,
        command_repository: CommandAdmissionReaderPort,
        executor: AgentRuntimeExecutor,
    ) -> None:
        if not callable(getattr(command_repository, "get", None)):
            raise TypeError("command_repository.get is required")
        if not callable(getattr(executor, "execute", None)):
            raise TypeError("executor.execute is required")
        self._commands = command_repository
        self._executor = executor

    async def execute_stream(
        self,
        frozen: FrozenNodeInput,
    ) -> AsyncIterator[Mapping[str, Any]]:
        async for event in self._execute_stream(frozen, recovery_context=None):
            yield event

    async def execute_stream_with_recovery(
        self,
        frozen: FrozenNodeInput,
        *,
        recovery_context: Mapping[str, Any],
    ) -> AsyncIterator[Mapping[str, Any]]:
        async for event in self._execute_stream(
            frozen,
            recovery_context=dict(recovery_context or {}),
        ):
            yield event

    async def _execute_stream(
        self,
        frozen: FrozenNodeInput,
        *,
        recovery_context: dict[str, Any] | None,
    ) -> AsyncIterator[Mapping[str, Any]]:
        command, work_order, _, _ = _reopen_frozen_admission(
            frozen=frozen,
            command_repository=self._commands,
        )
        execute_kwargs: dict[str, Any] = {
            "workspace_execution_authority": _workspace_execution_authority(
                frozen
            )
        }
        # Only the production AgentRuntimeExecutor owns the protected artifact
        # envelope.  Compatibility executors remain usable, but cannot claim
        # seal-based recovery merely because this wrapper supplied metadata.
        if isinstance(self._executor, AgentRuntimeExecutor):
            execute_kwargs["projection_recovery_context"] = recovery_context
        result = await asyncio.to_thread(
            self._executor.execute,
            work_order,
            **execute_kwargs,
        )
        if not isinstance(result, Mapping):
            raise InternalAgentProposalError(
                INTERNAL_AGENT_PROPOSAL_INVALID,
                "canonical Agent executor returned a non-mapping proposal",
            )
        normalized = _json_mapping(result, field_name="Agent result proposal")
        result_status = str(normalized.get("status") or "").strip().upper()
        if result_status not in {"EXECUTED", "FAILED", "BLOCKED", "DEGRADED"}:
            raise InternalAgentProposalError(
                INTERNAL_AGENT_PROPOSAL_INVALID,
                "canonical Agent result status is invalid",
            )
        yield _build_internal_terminal_event(
            frozen=frozen,
            command=command,
            work_order=work_order,
            normalized=normalized,
        )

    def recover_terminal_projection(
        self,
        frozen: FrozenNodeInput,
    ) -> dict[str, Any]:
        """Reopen a seal-bound terminal without invoking Agent/model code."""

        command, work_order, _, _ = _reopen_frozen_admission(
            frozen=frozen,
            command_repository=self._commands,
        )
        workspace_runtime = self._executor.workspace_runtime
        artifact_store = self._executor.artifact_store
        if workspace_runtime is None or artifact_store is None:
            raise InternalAgentProposalError(
                INTERNAL_AGENT_PROPOSAL_PROJECTION_INVALID,
                "sealed projection recovery has no governed Workspace authority",
            )
        authority = _workspace_execution_authority(frozen)
        workspace_id = workspace_runtime.workspace_id_for(
            graph_id=frozen.graph_id,
            graph_revision=frozen.graph_revision,
        )
        seal = artifact_store.inspect_governed_stage_for_authority(
            workspace_id=workspace_id,
            execution_authority=authority,
        )
        if seal.accepted_dir is not None:
            raise InternalAgentProposalError(
                INTERNAL_AGENT_PROPOSAL_PROJECTION_INVALID,
                "accepted artifact cannot rebuild a pre-commit worker projection",
            )
        try:
            envelope = validate_projection_recovery_envelope(
                artifact_store.read_projection_recovery(seal)
            )
        except ProjectionRecoveryContractError as exc:
            raise InternalAgentProposalError(
                INTERNAL_AGENT_PROPOSAL_PROJECTION_INVALID,
                "sealed projection recovery envelope is invalid",
            ) from exc
        worker_timing = envelope["worker_timing"]
        if envelope.get("frozen_node_input") != frozen.to_dict():
            raise InternalAgentProposalError(
                INTERNAL_AGENT_PROPOSAL_PROJECTION_INVALID,
                "sealed projection recovery envelope drifted",
            )
        normalized = artifact_store.read_sealed_json(seal, "result.json")
        normalized["_protected_stage"] = seal.protected_payload()
        terminal = _build_internal_terminal_event(
            frozen=frozen,
            command=command,
            work_order=work_order,
            normalized=normalized,
        )
        return {
            "terminal": terminal,
            "verified_node_grant": dict(envelope["verified_node_grant"]),
            "action_preview": (
                dict(envelope["action_preview"])
                if isinstance(envelope.get("action_preview"), Mapping)
                else None
            ),
            "buffered_pre_acceptance_events": 0,
            "worker_timing": dict(worker_timing),
            "stage_ref": seal.stage_ref,
            "seal_digest": seal.seal_digest,
        }


def _build_internal_terminal_event(
    *,
    frozen: FrozenNodeInput,
    command: HostCommand,
    work_order: WorkOrderV3,
    normalized: Mapping[str, Any],
) -> dict[str, Any]:
    normalized = _json_mapping(
        normalized,
        field_name="Agent result proposal",
    )
    result_status = str(normalized.get("status") or "").strip().upper()
    if result_status not in {"EXECUTED", "FAILED", "BLOCKED", "DEGRADED"}:
        raise InternalAgentProposalError(
            INTERNAL_AGENT_PROPOSAL_INVALID,
            "canonical Agent result status is invalid",
        )
    usage = build_worker_budget_usage(
        usage_id=stable_worker_usage_id(
            graph_id=frozen.graph_id,
            graph_revision=frozen.graph_revision,
            node_id=frozen.node_id,
            attempt_id=frozen.attempt_id,
            route=frozen.route,
        ),
        observations=_trusted_agent_budget_observations(normalized),
        include_agent_tool_calls=True,
    )
    public_result = build_public_internal_agent_result(normalized)
    validate_internal_agent_result_payload(public_result)
    result_digest = _canonical_digest(public_result)
    descriptor = {
        "effect_type": INTERNAL_AGENT_RESULT_COMMIT_EFFECT_TYPE,
        "commit_scope": "NODE",
        "payload": {
            "schema_version": INTERNAL_AGENT_PROPOSAL_SCHEMA_VERSION,
            "work_order_id": frozen.work_order_id,
            "target_id": frozen.target_id,
            "operation_fingerprint": frozen.operation_fingerprint,
            "result_digest": result_digest,
            "result": public_result,
        },
    }
    narrative = str(
        public_result.get("result_text")
        or public_result.get("summary")
        or public_result.get("detail")
        or result_status
    ).strip()
    event: dict[str, Any] = {
        "phase": "COMMIT",
        "done": True,
        "decision": "APPROVE" if result_status == "EXECUTED" else "REJECT",
        "reason_code": (
            None
            if result_status == "EXECUTED"
            else str(
                normalized.get("reason_code")
                or f"AGENT_RESULT_{result_status}"
            )
        ),
        "request_id": command.request_id,
        "narrative": narrative,
        "route_type": frozen.route,
        "target_id": frozen.target_id,
        "acceptance_effect_intents": [descriptor],
        "audit_refs": {
            "path": "internal_agent:leased_result_proposal",
            "result_digest": result_digest,
            "work_order_schema_version": work_order.schema_version,
            "legacy_v2_reprojection": False,
        },
    }
    if usage is not None:
        event["budget_usage"] = usage.to_payload()
    return event


class InternalAgentProposalVerifier:
    """Independently reopen the staged result proposal and its admission."""

    def __init__(
        self,
        *,
        projection_store: WorkerProjectionReaderPort,
    ) -> None:
        self._authority = _ProposalProjectionAuthority(
            projection_store=projection_store,
        )

    def verify(
        self,
        *,
        command: HostCommand,
        work_order: WorkOrderV3,
        execution_context: Mapping[str, Any],
        worker_commit: Mapping[str, Any],
        evidence_refs: Sequence[str],
        **_: Any,
    ) -> dict[str, Any]:
        reason: str | None = None
        proposal: Mapping[str, Any] = {}
        receipt_ref = str(dict(worker_commit or {}).get("receipt_ref") or "").strip()
        try:
            proposal = self._authority.require(
                command=command,
                work_order=work_order,
                execution_context=execution_context,
                worker_commit=worker_commit,
            )
            reason = internal_agent_terminal_result_reason(proposal["result"])
        except InternalAgentProposalError as exc:
            reason = exc.reason_code
        except (WorkerProjectionStoreError, TypeError, ValueError):
            reason = INTERNAL_AGENT_PROPOSAL_PROJECTION_INVALID

        context = dict(execution_context or {})
        passed = reason is None
        refs = _evidence_refs(evidence_refs, receipt_ref)
        return {
            "schema_version": "Natsume.agent.verification_result.v1",
            "verification_id": _stable_id("verify", command.command_id),
            "work_order_id": str(context.get("work_order_id") or ""),
            "task_graph_id": str(context.get("graph_id") or ""),
            "task_node_id": str(context.get("node_id") or ""),
            "attempt_id": str(context.get("attempt_id") or ""),
            "required": True,
            "status": "PASS" if passed else "FAIL",
            "reason_code": reason,
            "evidence_refs": refs,
            "checks": [
                {
                    "check_id": "durable-internal-agent-result-proposal",
                    "status": "PASS" if passed else "FAIL",
                    "reason_code": reason,
                }
            ],
            "started_at_ms": command.created_at_ms,
            "finished_at_ms": command.created_at_ms,
        }


class InternalAgentProposalAcceptance:
    """Judge an exact proposal and preserve durable post-execution Review."""

    def __init__(
        self,
        *,
        projection_store: WorkerProjectionReaderPort,
    ) -> None:
        self._authority = _ProposalProjectionAuthority(
            projection_store=projection_store,
        )

    def judge(
        self,
        *,
        command: HostCommand,
        work_order: WorkOrderV3,
        execution_context: Mapping[str, Any],
        worker_commit: Mapping[str, Any],
        verification_result: Mapping[str, Any],
        evidence_refs: Sequence[str],
        **_: Any,
    ) -> dict[str, Any]:
        reason: str | None = None
        receipt_ref = str(dict(worker_commit or {}).get("receipt_ref") or "").strip()
        try:
            proposal = self._authority.require(
                command=command,
                work_order=work_order,
                execution_context=execution_context,
                worker_commit=worker_commit,
            )
            _require_verification_binding(
                verification=dict(verification_result or {}),
                command=command,
                execution_context=execution_context,
            )
            reason = internal_agent_terminal_result_reason(proposal["result"])
        except InternalAgentProposalError as exc:
            reason = exc.reason_code
        except (WorkerProjectionStoreError, TypeError, ValueError):
            reason = INTERNAL_AGENT_PROPOSAL_PROJECTION_INVALID

        result = proposal.get("result")
        raw_acceptance = (
            result.get("acceptance") if isinstance(result, Mapping) else None
        )
        inner_decision = (
            str(raw_acceptance.get("decision") or "").strip().upper()
            if isinstance(raw_acceptance, Mapping)
            else ""
        )
        review_requested = (
            reason is None
            and (
                inner_decision == "REVIEW"
                or (isinstance(result, Mapping) and result.get("review_required") is True)
            )
        )
        accepted = reason is None and not review_requested
        decision = "ACCEPT" if accepted else "REVIEW" if review_requested else "REJECT"
        reason_code = (
            None
            if accepted
            else "INTERNAL_AGENT_REVIEW_REQUIRED"
            if review_requested
            else reason
        )
        context = dict(execution_context or {})
        refs = _evidence_refs(evidence_refs, receipt_ref)
        criteria_text = tuple(
            work_order.acceptance_criteria
            or ("Internal Agent result proposal is exact and verified",)
        )
        return {
            "schema_version": "Natsume.agent.acceptance_judgement.v1",
            "judgement_id": _stable_id("accept", command.command_id),
            "work_order_id": str(context.get("work_order_id") or ""),
            "task_graph_id": str(context.get("graph_id") or ""),
            "task_node_id": str(context.get("node_id") or ""),
            "attempt_id": str(context.get("attempt_id") or ""),
            "decision": decision,
            "reason_code": reason_code,
            "evidence_refs": refs,
            "criteria": [
                {
                    "criterion_id": _stable_id(
                        "criterion",
                        f"{command.command_id}:{index}:{criterion}",
                    ),
                    "criterion_text": str(criterion),
                    "verdict": "PASS" if accepted else "FAIL",
                    "evidence_refs": refs,
                    "deterministic": True,
                    "reason_code": reason_code,
                    "critic_verdict": None,
                }
                for index, criterion in enumerate(criteria_text, start=1)
            ],
            "critic_candidate": None,
            "started_at_ms": command.created_at_ms,
            "finished_at_ms": command.created_at_ms,
        }


class _ProposalProjectionAuthority:
    def __init__(self, *, projection_store: WorkerProjectionReaderPort) -> None:
        if not callable(getattr(projection_store, "load", None)):
            raise TypeError("projection_store.load is required")
        self._projections = projection_store

    def require(
        self,
        *,
        command: HostCommand,
        work_order: WorkOrderV3,
        execution_context: Mapping[str, Any],
        worker_commit: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(command, HostCommand) or not isinstance(work_order, WorkOrderV3):
            _invalid("HostCommand and WorkOrderV3 are required")
        work_order.validate()
        commit = dict(worker_commit or {})
        receipt_ref = str(commit.get("receipt_ref") or "").strip()
        if not receipt_ref.startswith("worker-projection://"):
            _invalid("worker commit lacks a durable projection receipt")
        raw = self._projections.load(receipt_ref)
        if not isinstance(raw, Mapping):
            _invalid("canonical worker projection is missing")
        projection = validate_worker_projection_payload(
            raw,
            expected_projection_id=receipt_ref,
        )
        _require_public_commit_binding(commit=commit, projection=projection)
        proposal = _proposal_from_projection(projection)
        _require_contract_binding(
            command=command,
            work_order=work_order,
            execution_context=execution_context,
            projection=projection,
            proposal=proposal,
        )
        return proposal


def _reopen_frozen_admission(
    *,
    frozen: FrozenNodeInput,
    command_repository: CommandAdmissionReaderPort,
) -> tuple[HostCommand, WorkOrderV3, TaskGraph, ExecutorAssignment]:
    if not isinstance(frozen, FrozenNodeInput):
        _invalid("Internal Agent executor requires FrozenNodeInput")
    raw = command_repository.get(frozen.command_id)
    if not isinstance(raw, Mapping):
        _invalid("durable Internal command admission is missing")
    command = raw.get("command")
    work_order = raw.get("work_order")
    graph = raw.get("graph")
    assignment = raw.get("assignment")
    if (
        not isinstance(command, HostCommand)
        or not isinstance(work_order, WorkOrderV3)
        or not isinstance(graph, TaskGraph)
        or not isinstance(assignment, ExecutorAssignment)
    ):
        _invalid("durable Internal command admission is incomplete or untyped")
    node = next(
        (candidate for candidate in graph.nodes if candidate.node_id == frozen.node_id),
        None,
    )
    expected_pairs = (
        (command.command_id, frozen.command_id),
        (command.request_id, frozen.request_id),
        (command.session_id, frozen.session_id),
        (command.principal_id, frozen.principal_id),
        (command.root_work_order_id, frozen.root_work_order_id),
        (command.graph_id, frozen.graph_id),
        (command.graph_revision, frozen.graph_revision),
        (command.route_type, frozen.route),
        (command.target_id, frozen.target_id),
        (command.business_scope, frozen.business_scope),
        (command.operation_fingerprint, frozen.operation_fingerprint),
        (work_order.work_order_id, frozen.work_order_id),
        (work_order.root_work_order_id, frozen.root_work_order_id),
        (work_order.task_graph_id, frozen.graph_id),
        (work_order.task_node_id, frozen.node_id),
        (work_order.graph_revision, frozen.graph_revision),
        (work_order.operation_fingerprint, frozen.operation_fingerprint),
        (graph.graph_id, frozen.graph_id),
        (graph.revision, frozen.graph_revision),
        (assignment.work_order_id, frozen.work_order_id),
    )
    if (
        any(actual != expected for actual, expected in expected_pairs)
        or command.surface is not HostCommandSurface.INTERNAL
        or frozen.route != "delegate"
        or frozen.target_id != "agent_runtime"
        or node is None
        or node.route != "delegate:agent_runtime"
        or assignment.executor is not TaskExecutor.CANONICAL
    ):
        _invalid("frozen Internal Agent node differs from durable admission")
    return command, work_order, graph, assignment


def _workspace_execution_authority(
    frozen: FrozenNodeInput,
) -> dict[str, Any]:
    """Project the exact live TaskNode lease into Workspace authority.

    The authority is deliberately rebuilt from the frozen coordinator input;
    neither the caller WorkOrder nor mutable Agent metadata can choose any of
    these fields.
    """

    return {
        "root_work_order_id": frozen.root_work_order_id,
        "graph_id": frozen.graph_id,
        "graph_revision": frozen.graph_revision,
        "node_id": frozen.node_id,
        "attempt_id": frozen.attempt_id,
        "lease_id": frozen.lease_id,
        "command_id": frozen.command_id,
        "principal_id": frozen.principal_id,
        "lease_owner_id": frozen.lease_owner_id,
        "fencing_token": frozen.fencing_token,
    }


def _proposal_from_projection(projection: Mapping[str, Any]) -> dict[str, Any]:
    raw = projection.get("acceptance_effect_intents")
    if not isinstance(raw, list) or len(raw) != 1 or not isinstance(raw[0], Mapping):
        _invalid("Internal Agent projection must contain one result-commit descriptor")
    descriptor = dict(raw[0])
    if set(descriptor) != {"effect_type", "commit_scope", "payload"} or (
        descriptor.get("effect_type") != INTERNAL_AGENT_RESULT_COMMIT_EFFECT_TYPE
        or descriptor.get("commit_scope") != "NODE"
    ):
        _invalid("Internal Agent result-commit descriptor drifted")
    payload = _json_mapping(
        descriptor.get("payload"),
        field_name="Internal Agent result proposal",
    )
    required = {
        "schema_version",
        "work_order_id",
        "target_id",
        "operation_fingerprint",
        "result_digest",
        "result",
    }
    if set(payload) != required or payload.get("schema_version") != (
        INTERNAL_AGENT_PROPOSAL_SCHEMA_VERSION
    ):
        _invalid("Internal Agent result proposal fields drifted")
    result = _json_mapping(payload.get("result"), field_name="Agent result")
    if payload.get("result_digest") != _canonical_digest(result):
        _invalid("Internal Agent result proposal digest drifted")
    return {**payload, "result": result}


def _require_contract_binding(
    *,
    command: HostCommand,
    work_order: WorkOrderV3,
    execution_context: Mapping[str, Any],
    projection: Mapping[str, Any],
    proposal: Mapping[str, Any],
) -> None:
    context = dict(execution_context or {})
    node_id = str(context.get("node_id") or "").strip()
    identity_pairs = (
        (command.route_type, "delegate"),
        (command.target_id, "agent_runtime"),
        (work_order.work_order_id, command.root_work_order_id),
        (work_order.root_work_order_id, command.root_work_order_id),
        (work_order.task_graph_id, command.graph_id),
        (work_order.task_node_id, node_id),
        (work_order.graph_revision, command.graph_revision),
        (work_order.operation_fingerprint, command.operation_fingerprint),
        (context.get("command_id"), command.command_id),
        (context.get("request_id"), command.request_id),
        (context.get("principal_id"), command.principal_id),
        (context.get("work_order_id"), work_order.work_order_id),
        (context.get("graph_id"), command.graph_id),
        (context.get("graph_revision"), command.graph_revision),
        (context.get("worker_route"), "delegate:agent_runtime"),
        (context.get("target_id"), "agent_runtime"),
        (context.get("business_scope"), command.business_scope),
        (context.get("operation_fingerprint"), command.operation_fingerprint),
        (projection.get("command_id"), command.command_id),
        (projection.get("request_id"), command.request_id),
        (projection.get("route_type"), "delegate"),
        (projection.get("target_id"), "agent_runtime"),
        (projection.get("graph_id"), command.graph_id),
        (projection.get("graph_revision"), command.graph_revision),
        (projection.get("node_id"), node_id),
        (projection.get("attempt_id"), context.get("attempt_id")),
        (projection.get("node_grant_ref"), context.get("node_grant_ref")),
        (proposal.get("work_order_id"), work_order.work_order_id),
        (proposal.get("target_id"), "agent_runtime"),
        (proposal.get("operation_fingerprint"), command.operation_fingerprint),
    )
    if any(actual != expected for actual, expected in identity_pairs) or (
        command.surface is not HostCommandSurface.INTERNAL
        or command.graph_revision < 1
    ):
        _invalid("Internal command, WorkOrder, execution and proposal identities differ")


def _require_public_commit_binding(
    *,
    commit: Mapping[str, Any],
    projection: Mapping[str, Any],
) -> None:
    allowed = {
        "phase",
        "done",
        "decision",
        "reason_code",
        "command_id",
        "request_id",
        "narrative",
        "route_type",
        "target_id",
        "graph_id",
        "graph_revision",
        "node_id",
        "attempt_id",
        "node_grant_ref",
        "audit_refs",
        "acceptance_effect_intents",
        "receipt_ref",
    }
    if "budget_usage" in commit:
        allowed.add("budget_usage")
    recovery_checkpoint = commit.get("recovery_checkpoint")
    if recovery_checkpoint is not None:
        allowed.add("recovery_checkpoint")
    persisted_projection_fields = {
        "projection_id",
        "payload_digest",
        "schema_version",
    }
    if persisted_projection_fields.intersection(commit):
        allowed.update(persisted_projection_fields)
    if set(commit) != allowed:
        _invalid("public Internal Agent commit contains missing or domain-result fields")
    if commit.get("receipt_ref") != projection.get("projection_id"):
        _invalid("worker commit receipt differs from canonical projection")
    if persisted_projection_fields.intersection(commit) and any(
        commit.get(key) != projection.get(key)
        for key in persisted_projection_fields
    ):
        _invalid("worker commit persisted projection identity drifted")
    keys = (
        "phase",
        "done",
        "decision",
        "reason_code",
        "command_id",
        "request_id",
        "narrative",
        "route_type",
        "target_id",
        "graph_id",
        "graph_revision",
        "node_id",
        "attempt_id",
        "node_grant_ref",
        "acceptance_effect_intents",
        "budget_usage",
    )
    for key in keys:
        if key in projection or key in commit:
            if commit.get(key) != projection.get(key):
                _invalid(f"public Internal Agent commit drifted from {key}")
    if projection.get("decision") != "APPROVE":
        _invalid("Internal Agent result proposal is not approved")
    if recovery_checkpoint is not None:
        _require_recovery_checkpoint_binding(
            recovery_checkpoint=recovery_checkpoint,
            projection=projection,
        )


def _require_recovery_checkpoint_binding(
    *,
    recovery_checkpoint: Any,
    projection: Mapping[str, Any],
) -> None:
    if not isinstance(recovery_checkpoint, Mapping):
        _invalid("Internal Agent recovery checkpoint is malformed")
    checkpoint = dict(recovery_checkpoint)
    expected_fields = {
        "worker_replayed",
        "payload_digest",
        "payload_digest_source",
        "budget_usage_state",
        "budget_usage_reconstructed",
        "durable_elapsed_ms",
    }
    state = checkpoint.get("budget_usage_state")
    elapsed = checkpoint.get("durable_elapsed_ms")
    reconstructed = checkpoint.get("budget_usage_reconstructed")
    if (
        set(checkpoint) != expected_fields
        or checkpoint.get("worker_replayed") is not False
        or checkpoint.get("payload_digest") != projection.get("payload_digest")
        or checkpoint.get("payload_digest_source") != "receipt_identity_digest"
        or state not in {"EXACT_ACCEPTED", "EXACT_REJECTED", "MISSING"}
        or type(reconstructed) is not bool
        or reconstructed is not (state == "MISSING")
        or (
            state in {"EXACT_ACCEPTED", "EXACT_REJECTED"}
            and elapsed is not None
        )
        or (
            state == "MISSING"
            and (type(elapsed) is not int or elapsed <= 0)
        )
    ):
        _invalid("Internal Agent recovery checkpoint binding drifted")


def _require_verification_binding(
    *,
    verification: Mapping[str, Any],
    command: HostCommand,
    execution_context: Mapping[str, Any],
) -> None:
    context = dict(execution_context or {})
    expected = {
        "status": "PASS",
        "work_order_id": str(context.get("work_order_id") or ""),
        "task_graph_id": str(context.get("graph_id") or ""),
        "task_node_id": str(context.get("node_id") or ""),
        "attempt_id": str(context.get("attempt_id") or ""),
    }
    if any(verification.get(key) != value for key, value in expected.items()) or (
        verification.get("verification_id") != _stable_id("verify", command.command_id)
    ):
        _invalid("Acceptance received no exact passing Internal Agent verification")


def _trusted_agent_budget_observations(value: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = value.get("metadata")
    if not isinstance(metadata, Mapping):
        return {}
    if str(metadata.get("execution_authority") or "").strip() != (
        "canonical_agent_executor"
    ):
        return {}
    return {
        "model_attempts": list(metadata.get("model_attempts") or []),
        "trace": (
            dict(metadata.get("trace") or {})
            if isinstance(metadata.get("trace"), Mapping)
            else {}
        ),
    }


def _json_mapping(value: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _invalid(f"{field_name} must be a mapping")
    try:
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        decoded = json.loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise InternalAgentProposalError(
            INTERNAL_AGENT_PROPOSAL_INVALID,
            f"{field_name} is not finite canonical JSON",
        ) from exc
    if not isinstance(decoded, dict):
        _invalid(f"{field_name} must be a JSON object")
    return decoded


def validate_internal_agent_result_payload(value: Mapping[str, Any]) -> None:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > _MAX_RESULT_BYTES:
        raise InternalAgentProposalError(
            INTERNAL_AGENT_PROPOSAL_INVALID,
            "Internal Agent result proposal exceeds the durable size limit",
        )

    stack: list[tuple[Any, str]] = [(value, "result")]
    visited = 0
    while stack:
        current, path = stack.pop()
        visited += 1
        if visited > 100_000:
            raise InternalAgentProposalError(
                INTERNAL_AGENT_PROPOSAL_INVALID,
                "Internal Agent result proposal structure is too large",
            )
        if isinstance(current, Mapping):
            for raw_key, child in current.items():
                key = str(raw_key or "").strip()
                normalized = re.sub(
                    r"[^a-z0-9]+",
                    "_",
                    key.lower(),
                ).strip("_")
                sensitive = bool(_SENSITIVE_NON_TOKEN.search(normalized)) or (
                    bool(_TOKEN_FIELD.search(normalized))
                    and _SAFE_TOKEN_METADATA.fullmatch(normalized) is None
                )
                if sensitive and not normalized.endswith("_ref"):
                    raise InternalAgentProposalError(
                        INTERNAL_AGENT_PROPOSAL_INVALID,
                        f"secret-like Agent result field is forbidden: {path}.{key}",
                    )
                stack.append((child, f"{path}.{key}"))
        elif isinstance(current, list):
                stack.extend(
                (child, f"{path}[]")
                for child in current
            )


def build_public_internal_agent_result(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the small public proposal persisted before outer Acceptance.

    Full WorkOrder/prompt/result material remains in the sealed governed
    stage.  WorkerProjection carries only terminal gates, bounded narrative
    fields and a protected content reference/digest; local paths, lease
    ownership and arbitrary executor metadata never cross this boundary.
    """

    raw = dict(value or {})
    protected_raw = raw.pop("_protected_stage", None)
    sanitized = sanitize_public_payload(raw)
    if not isinstance(sanitized, Mapping):
        raise InternalAgentProposalError(
            INTERNAL_AGENT_PROPOSAL_INVALID,
            "sanitized Agent result proposal must be a mapping",
        )

    def _bounded_text(field: str, limit: int) -> str:
        return str(sanitized.get(field) or "").strip()[:limit]

    verification_raw = sanitized.get("verification")
    acceptance_raw = sanitized.get("acceptance")
    verification = dict(verification_raw) if isinstance(verification_raw, Mapping) else {}
    acceptance = dict(acceptance_raw) if isinstance(acceptance_raw, Mapping) else {}
    public: dict[str, Any] = {
        "status": _bounded_text("status", 32).upper(),
        "work_order_id": _bounded_text("work_order_id", 240),
        "summary": _bounded_text("summary", 4_000),
        "result_text": _bounded_text("result_text", 24_000),
        "verification": {
            "status": str(verification.get("status") or "NOT_RUN").strip()[:64],
            "reason_code": str(verification.get("reason_code") or "").strip()[:128]
            or None,
        },
        "acceptance": {
            "decision": str(acceptance.get("decision") or "NOT_RUN").strip()[:64],
            "reason_code": str(acceptance.get("reason_code") or "").strip()[:128]
            or None,
        },
        "review_required": sanitized.get("review_required") is True,
        "reason_code": _bounded_text("reason_code", 128) or None,
        "risk_flags": [
            str(item or "").strip()[:128]
            for item in list(sanitized.get("risk_flags") or [])[:64]
            if str(item or "").strip()
        ],
    }
    if protected_raw is not None:
        if not isinstance(protected_raw, Mapping):
            _invalid("governed Agent protected stage is malformed")
        protected = dict(protected_raw)
        if set(protected) != {"schema_version", "ref", "digest"} or (
            protected.get("schema_version")
            != "Natsume.agent.governed_artifact_stage.v1"
            or not str(protected.get("ref") or "").startswith("agent-stage://")
            or not re.fullmatch(
                r"sha256:[0-9a-f]{64}",
                str(protected.get("digest") or ""),
            )
        ):
            _invalid("governed Agent protected stage attribution is invalid")
        public["protected_artifact"] = protected
    return _json_mapping(public, field_name="public Agent result proposal")


def internal_agent_terminal_result_reason(
    value: Mapping[str, Any],
) -> str | None:
    """Return only a structural verification failure for an Agent proposal.

    A successful worker may ask for a post-execution review.  That is not a
    verification failure: the Task Runtime must preserve its succeeded Attempt
    and transition the node to ``WAITING_ACCEPTANCE_REVIEW`` so the durable
    Review continuation or cancellation path owns the next step.
    """

    if not isinstance(value, Mapping) or value.get("status") != "EXECUTED":
        return "INTERNAL_AGENT_RESULT_NOT_EXECUTED"
    acceptance = value.get("acceptance")
    decision = (
        str(acceptance.get("decision") or "").strip().upper()
        if isinstance(acceptance, Mapping)
        else ""
    )
    if decision not in {"ACCEPT", "REVIEW"}:
        return "INTERNAL_AGENT_INNER_ACCEPTANCE_NOT_ACCEPTED"
    return None


def _canonical_digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _stable_id(prefix: str, material: str) -> str:
    return f"{prefix}-{hashlib.sha256(str(material).encode('utf-8')).hexdigest()[:40]}"


def _evidence_refs(values: Sequence[str], receipt_ref: str) -> list[str]:
    return list(
        dict.fromkeys(
            str(value or "").strip()
            for value in (*tuple(values or ()), receipt_ref)
            if str(value or "").strip()
        )
    )


def _invalid(detail: str) -> None:
    raise InternalAgentProposalError(
        INTERNAL_AGENT_PROPOSAL_PROJECTION_INVALID,
        detail,
    )


__all__ = [
    "INTERNAL_AGENT_PROPOSAL_INVALID",
    "INTERNAL_AGENT_PROPOSAL_PROJECTION_INVALID",
    "INTERNAL_AGENT_PROPOSAL_SCHEMA_VERSION",
    "INTERNAL_AGENT_RESULT_COMMIT_EFFECT_TYPE",
    "InternalAgentProposalAcceptance",
    "InternalAgentProposalError",
    "InternalAgentProposalExecutor",
    "InternalAgentProposalVerifier",
    "build_public_internal_agent_result",
    "internal_agent_terminal_result_reason",
    "validate_internal_agent_result_payload",
]
