"""Promote one verified Internal Agent proposal after Task Acceptance.

The handler owns the only write to ``agent_execution_result``.  It never calls
an Agent/model executor.  Exact replay first reopens the immutable command and
worker projection; a reclaimed unknown effect uses the read-only ``reconcile``
path and can therefore never duplicate Agent computation or result promotion.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import inspect
import json
import re
import secrets
from typing import Any

from host_brain.agent_runtime.internal_result_proposal import (
    INTERNAL_AGENT_PROPOSAL_SCHEMA_VERSION,
    INTERNAL_AGENT_RESULT_COMMIT_EFFECT_TYPE,
    internal_agent_terminal_result_reason,
    validate_internal_agent_result_payload,
)
from host_brain.contracts.agent_runtime.task_graph import TaskGraph
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.persistence.worker_projection_repository import (
    validate_worker_projection_payload,
)
from host_brain.task_runtime.acceptance_effect_dispatcher import (
    AcceptanceEffectOutcomeUnknown,
)
from natsume_contracts.task import HostCommand, HostCommandSurface
from natsume_contracts.task.v1 import ExecutorAssignment, TaskExecutor


INTERNAL_ACCEPTED_RESULT_SCHEMA_VERSION = (
    "Natsume.internal.accepted_agent_result.v1"
)
INTERNAL_ACTION_PREVIEW_CONTEXT_SCHEMA = (
    "Natsume.internal.action_preview_context.v2"
)
INTERNAL_ACCEPTED_RESULT_INVALID = "INTERNAL_ACCEPTED_RESULT_INVALID"

_CANONICAL_FIELDS = frozenset(
    {
        "schema_version",
        "command_id",
        "request_id",
        "work_order_id",
        "graph_id",
        "graph_revision",
        "node_id",
        "attempt_id",
        "target_id",
        "operation_fingerprint",
        "worker_projection_ref",
        "result_digest",
    }
)
_PRIVATE_PREVIEW_FIELD = "_action_preview_context"
_PRIVATE_TERMINAL_PROOF_FIELD = "_action_preview_terminal_proof"
_PREVIEW_FIELDS = frozenset(
    {
        "schema_version",
        "origin_authority_kind",
        "origin_event_id",
        "origin_receipt_ref",
        "origin_boundary",
    }
)
_BOUNDARY_FIELDS = frozenset(
    {
        "action_kind",
        "action_scope",
        "risk_level",
        "action_args",
        "context_pack_digest",
        "scene_identity",
        "task_graph_id",
        "graph_revision",
        "task_node_id",
        "parent_governor_grant_ref",
        "node_grant_ref",
        "node_grant",
        "authority_context",
    }
)
_AUTHORITY_FIELDS = frozenset(
    {"schema_version", "effect_id", "lease_owner_id", "fencing_token"}
)
_NODE_GRANT_FIELDS = frozenset({"grant_ref", "binding_digest"})
_DURABLE_REF = re.compile(r"^[a-z][a-z0-9+.-]{1,31}://\S+$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class InternalAcceptedResultError(RuntimeError):
    def __init__(self, detail: str) -> None:
        self.reason_code = INTERNAL_ACCEPTED_RESULT_INVALID
        self.detail = str(detail or "accepted Internal Agent result is invalid")
        super().__init__(f"{self.reason_code}: {self.detail}")


class InternalAcceptedResultHandler:
    """Commit one exact worker proposal to the Agent result ledger."""

    def __init__(
        self,
        *,
        command_repository: Any,
        projection_store: Any,
        result_repository: Any,
    ) -> None:
        _require_method(command_repository, "get")
        _require_method(projection_store, "load")
        _require_method(result_repository, "get_by_command")
        _require_method(result_repository, "append")
        self._commands = command_repository
        self._projections = projection_store
        self._results = result_repository

    async def __call__(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        canonical, preview = _accepted_payload(payload)
        result = await self._reopen(canonical, preview)
        existing = await _resolve(
            self._results.get_by_command(canonical["command_id"])
        )
        if existing is not None:
            record = _exact_result_record(
                existing,
                canonical=canonical,
                expected_result=result,
            )
            return _handler_outcome(record, replayed=True)

        try:
            await _resolve(
                self._results.append(
                    command_id=canonical["command_id"],
                    work_order_id=canonical["work_order_id"],
                    result=result,
                )
            )
            durable = await _resolve(
                self._results.get_by_command(canonical["command_id"])
            )
            record = _exact_result_record(
                durable,
                canonical=canonical,
                expected_result=result,
            )
        except AcceptanceEffectOutcomeUnknown:
            raise
        except Exception as exc:
            replay = await self._reconcile_after_commit(
                canonical,
                expected_result=result,
            )
            if replay is not None:
                return _handler_outcome(replay, replayed=True)
            raise AcceptanceEffectOutcomeUnknown(
                "Internal Agent result commit has no exact durable attribution "
                f"for command {canonical['command_id']}: "
                f"{_opaque_exception(exc)}"
            ) from exc
        return _handler_outcome(record, replayed=False)

    async def reconcile(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        """Read exact state only; never append or rerun Agent computation."""

        canonical, preview = _accepted_payload(payload)
        expected_result = await self._reopen(canonical, preview)
        try:
            existing = await _resolve(
                self._results.get_by_command(canonical["command_id"])
            )
            if existing is None:
                raise LookupError("exact Internal Agent result is absent")
            record = _exact_result_record(
                existing,
                canonical=canonical,
                expected_result=expected_result,
            )
        except AcceptanceEffectOutcomeUnknown:
            raise
        except Exception as exc:
            raise AcceptanceEffectOutcomeUnknown(
                "Internal Agent result reconciliation found no exact durable "
                f"record for command {canonical['command_id']}: "
                f"{_opaque_exception(exc)}"
            ) from exc
        return _handler_outcome(record, replayed=True)

    async def _reopen(
        self,
        canonical: Mapping[str, Any],
        preview: Mapping[str, Any],
    ) -> dict[str, Any]:
        raw = await _resolve(self._commands.get(canonical["command_id"]))
        if not isinstance(raw, Mapping):
            _invalid("accepted Internal Agent command is missing")
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
            _invalid("accepted Internal Agent admission is incomplete or untyped")
        node = next(
            (
                candidate
                for candidate in graph.nodes
                if candidate.node_id == canonical["node_id"]
            ),
            None,
        )
        if node is None:
            _invalid("accepted Internal Agent TaskNode is missing")

        projection_raw = await _resolve(
            self._projections.load(canonical["worker_projection_ref"])
        )
        if not isinstance(projection_raw, Mapping):
            _invalid("accepted Internal Agent worker projection is missing")
        projection = validate_worker_projection_payload(
            projection_raw,
            expected_projection_id=canonical["worker_projection_ref"],
        )
        result = _proposal_result(
            projection,
            canonical=canonical,
        )
        _validate_reopened_identity(
            canonical=canonical,
            preview=preview,
            command=command,
            work_order=work_order,
            graph=graph,
            node=node,
            assignment=assignment,
            projection=projection,
        )
        return result

    async def _reconcile_after_commit(
        self,
        canonical: Mapping[str, Any],
        *,
        expected_result: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        try:
            durable = await _resolve(
                self._results.get_by_command(canonical["command_id"])
            )
            if durable is None:
                return None
            return _exact_result_record(
                durable,
                canonical=canonical,
                expected_result=expected_result,
            )
        except Exception:
            return None


def _accepted_payload(
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(payload, Mapping):
        _invalid("accepted Internal Agent payload must be a mapping")
    value = dict(payload)
    preview_raw = value.pop(_PRIVATE_PREVIEW_FIELD, None)
    terminal_raw = value.pop(_PRIVATE_TERMINAL_PROOF_FIELD, None)
    if frozenset(value) != _CANONICAL_FIELDS:
        _invalid("accepted Internal Agent payload fields drifted")
    if value.get("schema_version") != INTERNAL_ACCEPTED_RESULT_SCHEMA_VERSION:
        _invalid("accepted Internal Agent result schema drifted")
    canonical: dict[str, Any] = {}
    for field in _CANONICAL_FIELDS - {"graph_revision"}:
        text = str(value.get(field) or "").strip()
        if not text:
            _invalid(f"accepted Internal Agent {field} is required")
        canonical[field] = text
    revision = value.get("graph_revision")
    if isinstance(revision, bool) or type(revision) is not int or revision < 1:
        _invalid("accepted Internal Agent graph_revision is invalid")
    canonical["graph_revision"] = revision
    if canonical["target_id"] != "agent_runtime":
        _invalid("accepted Internal Agent target is unsupported")
    if not _SHA256.fullmatch(canonical["operation_fingerprint"]):
        _invalid("accepted Internal Agent operation fingerprint is invalid")
    if not _SHA256.fullmatch(canonical["result_digest"]):
        _invalid("accepted Internal Agent result digest is invalid")
    if not canonical["worker_projection_ref"].startswith("worker-projection://"):
        _invalid("accepted Internal Agent worker projection ref is invalid")
    preview = _private_preview_context(
        preview_raw,
        canonical=canonical,
        terminal_proof=terminal_raw,
    )
    return canonical, preview


def _private_preview_context(
    value: Any,
    *,
    canonical: Mapping[str, Any],
    terminal_proof: Any,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _invalid("Internal Agent result commit lacks Dispatcher Preview context")
    context = dict(value)
    if frozenset(context) != _PREVIEW_FIELDS or context.get("schema_version") != (
        INTERNAL_ACTION_PREVIEW_CONTEXT_SCHEMA
    ):
        _invalid("Internal Agent private Preview context fields drifted")
    if str(context.get("origin_authority_kind") or "").strip().upper() != (
        "ACCEPTANCE_EFFECT"
    ):
        _invalid("Internal Agent private Preview origin is invalid")
    for field in ("origin_event_id", "origin_receipt_ref"):
        if not _DURABLE_REF.fullmatch(str(context.get(field) or "").strip()):
            _invalid(f"Internal Agent private Preview {field} is invalid")
    boundary_raw = context.get("origin_boundary")
    if not isinstance(boundary_raw, Mapping):
        _invalid("Internal Agent private Preview boundary is missing")
    boundary = dict(boundary_raw)
    if frozenset(boundary) != _BOUNDARY_FIELDS:
        _invalid("Internal Agent private Preview boundary fields drifted")
    if (
        boundary.get("action_kind")
        != f"acceptance-effect.{INTERNAL_AGENT_RESULT_COMMIT_EFFECT_TYPE}"
        or boundary.get("action_args") != dict(canonical)
        or boundary.get("task_graph_id") != canonical["graph_id"]
        or boundary.get("graph_revision") != canonical["graph_revision"]
        or boundary.get("task_node_id") != canonical["node_id"]
    ):
        _invalid("Internal Agent private Preview boundary drifted")
    risk = boundary.get("risk_level")
    if isinstance(risk, bool) or type(risk) is not int or not 1 <= risk <= 5:
        _invalid("Internal Agent private Preview risk is invalid")
    if not _SHA256.fullmatch(str(boundary.get("context_pack_digest") or "")):
        _invalid("Internal Agent private Preview context digest is invalid")
    if not isinstance(boundary.get("scene_identity"), Mapping):
        _invalid("Internal Agent private Preview Scene identity is invalid")
    authority_raw = boundary.get("authority_context")
    node_grant_raw = boundary.get("node_grant")
    if not isinstance(authority_raw, Mapping) or not isinstance(node_grant_raw, Mapping):
        _invalid("Internal Agent private Preview authority binding is invalid")
    authority = dict(authority_raw)
    node_grant = dict(node_grant_raw)
    if frozenset(authority) != _AUTHORITY_FIELDS or authority.get(
        "schema_version"
    ) != "Natsume.action_preview.acceptance_effect_authority_request.v1":
        _invalid("Internal Agent private Preview effect authority drifted")
    fence = authority.get("fencing_token")
    if (
        authority.get("effect_id") != boundary.get("action_scope")
        or not _DURABLE_REF.fullmatch(str(authority.get("effect_id") or ""))
        or not str(authority.get("lease_owner_id") or "").strip()
        or isinstance(fence, bool)
        or type(fence) is not int
        or fence < 1
    ):
        _invalid("Internal Agent private Preview effect fence drifted")
    if (
        frozenset(node_grant) != _NODE_GRANT_FIELDS
        or node_grant.get("grant_ref") != boundary.get("node_grant_ref")
        or not _DURABLE_REF.fullmatch(str(node_grant.get("grant_ref") or ""))
        or not _SHA256.fullmatch(str(node_grant.get("binding_digest") or ""))
        or not _DURABLE_REF.fullmatch(
            str(boundary.get("parent_governor_grant_ref") or "")
        )
    ):
        _invalid("Internal Agent private Preview node authority drifted")
    if terminal_proof is not None:
        if not isinstance(terminal_proof, Mapping):
            _invalid("Internal Agent terminal Preview proof is invalid")
        proof = dict(terminal_proof)
        if frozenset(proof) != {"service", "event_id", "boundary"} or (
            proof.get("event_id") != context["origin_event_id"]
            or proof.get("boundary") != boundary
        ):
            _invalid("Internal Agent terminal Preview proof differs")
    return {
        **context,
        "origin_boundary": deepcopy(boundary),
    }


def _proposal_result(
    projection: Mapping[str, Any],
    *,
    canonical: Mapping[str, Any],
) -> dict[str, Any]:
    raw = projection.get("acceptance_effect_intents")
    if not isinstance(raw, list) or len(raw) != 1 or not isinstance(raw[0], Mapping):
        _invalid("Internal Agent worker projection has no exact result proposal")
    descriptor = dict(raw[0])
    if set(descriptor) != {"effect_type", "commit_scope", "payload"} or (
        descriptor.get("effect_type") != INTERNAL_AGENT_RESULT_COMMIT_EFFECT_TYPE
        or descriptor.get("commit_scope") != "NODE"
    ):
        _invalid("Internal Agent result proposal descriptor drifted")
    payload = descriptor.get("payload")
    if not isinstance(payload, Mapping):
        _invalid("Internal Agent result proposal payload is invalid")
    proposal = dict(payload)
    required = {
        "schema_version",
        "work_order_id",
        "target_id",
        "operation_fingerprint",
        "result_digest",
        "result",
    }
    if set(proposal) != required or proposal.get("schema_version") != (
        INTERNAL_AGENT_PROPOSAL_SCHEMA_VERSION
    ):
        _invalid("Internal Agent result proposal fields drifted")
    result = proposal.get("result")
    if not isinstance(result, Mapping):
        _invalid("Internal Agent result proposal result is invalid")
    normalized = _json_mapping(result)
    validate_internal_agent_result_payload(normalized)
    result_digest = _canonical_digest(normalized)
    terminal_reason = internal_agent_terminal_result_reason(normalized)
    if (
        proposal.get("work_order_id") != canonical["work_order_id"]
        or proposal.get("target_id") != canonical["target_id"]
        or proposal.get("operation_fingerprint")
        != canonical["operation_fingerprint"]
        or proposal.get("result_digest") != canonical["result_digest"]
        or result_digest != canonical["result_digest"]
        or terminal_reason is not None
    ):
        _invalid(
            "Internal Agent accepted result proposal is not exact terminal "
            f"success: {terminal_reason or 'identity drift'}"
        )
    return normalized


def _validate_reopened_identity(
    *,
    canonical: Mapping[str, Any],
    preview: Mapping[str, Any],
    command: HostCommand,
    work_order: WorkOrderV3,
    graph: TaskGraph,
    node: Any,
    assignment: ExecutorAssignment,
    projection: Mapping[str, Any],
) -> None:
    expected_pairs = (
        (command.command_id, canonical["command_id"]),
        (command.request_id, canonical["request_id"]),
        (command.root_work_order_id, canonical["work_order_id"]),
        (command.graph_id, canonical["graph_id"]),
        (command.graph_revision, canonical["graph_revision"]),
        (command.route_type, "delegate"),
        (command.target_id, canonical["target_id"]),
        (command.operation_fingerprint, canonical["operation_fingerprint"]),
        (work_order.work_order_id, canonical["work_order_id"]),
        (work_order.root_work_order_id, canonical["work_order_id"]),
        (work_order.task_graph_id, canonical["graph_id"]),
        (work_order.task_node_id, canonical["node_id"]),
        (work_order.graph_revision, canonical["graph_revision"]),
        (work_order.operation_fingerprint, canonical["operation_fingerprint"]),
        (graph.graph_id, canonical["graph_id"]),
        (graph.revision, canonical["graph_revision"]),
        (graph.result_node_id, canonical["node_id"]),
        (node.node_id, canonical["node_id"]),
        (node.route, "delegate:agent_runtime"),
        (assignment.work_order_id, canonical["work_order_id"]),
        (projection.get("projection_id"), canonical["worker_projection_ref"]),
        (projection.get("command_id"), canonical["command_id"]),
        (projection.get("request_id"), canonical["request_id"]),
        (projection.get("graph_id"), canonical["graph_id"]),
        (projection.get("graph_revision"), canonical["graph_revision"]),
        (projection.get("node_id"), canonical["node_id"]),
        (projection.get("attempt_id"), canonical["attempt_id"]),
        (projection.get("route_type"), "delegate"),
        (projection.get("target_id"), canonical["target_id"]),
    )
    if (
        any(actual != expected for actual, expected in expected_pairs)
        or command.surface is not HostCommandSurface.INTERNAL
        or assignment.executor is not TaskExecutor.CANONICAL
        or projection.get("decision") != "APPROVE"
        or preview["origin_boundary"].get("action_args") != dict(canonical)
    ):
        _invalid("accepted Internal Agent durable identities drifted")


def _exact_result_record(
    value: Any,
    *,
    canonical: Mapping[str, Any],
    expected_result: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _invalid("durable Internal Agent result is missing")
    record = dict(value)
    result = record.get("result")
    if not isinstance(result, Mapping):
        _invalid("durable Internal Agent result payload is missing")
    normalized = _json_mapping(result)
    if (
        record.get("command_id") != canonical["command_id"]
        or record.get("work_order_id") != canonical["work_order_id"]
        or record.get("result_status") != "EXECUTED"
        or record.get("result_digest") != canonical["result_digest"]
        or normalized != _json_mapping(expected_result)
        or not _DURABLE_REF.fullmatch(str(record.get("receipt_ref") or ""))
    ):
        _invalid("durable Internal Agent result attribution drifted")
    return {**record, "result": normalized}


def _handler_outcome(record: Mapping[str, Any], *, replayed: bool) -> dict[str, Any]:
    return {
        "receipt_ref": str(record.get("receipt_ref") or ""),
        "command_id": str(record.get("command_id") or ""),
        "work_order_id": str(record.get("work_order_id") or ""),
        "result_digest": str(record.get("result_digest") or ""),
        "result_status": str(record.get("result_status") or ""),
        "replayed": bool(replayed),
    }


def _canonical_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _opaque_exception(exc: BaseException) -> str:
    error_type = f"{type(exc).__module__}.{type(exc).__qualname__}"
    return (
        f"error_type={error_type} "
        f"error_digest=sha256:{secrets.token_hex(32)}"
    )


def _json_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
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
        raise InternalAcceptedResultError(
            "Internal Agent result is not finite canonical JSON"
        ) from exc
    if not isinstance(decoded, dict):
        _invalid("Internal Agent result must be a JSON object")
    return decoded


async def _resolve(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _require_method(value: Any, name: str) -> None:
    if not callable(getattr(value, name, None)):
        raise TypeError(f"{name} is required")


def _invalid(detail: str) -> None:
    raise InternalAcceptedResultError(detail)


__all__ = [
    "INTERNAL_ACCEPTED_RESULT_INVALID",
    "INTERNAL_ACCEPTED_RESULT_SCHEMA_VERSION",
    "INTERNAL_ACTION_PREVIEW_CONTEXT_SCHEMA",
    "InternalAcceptedResultError",
    "InternalAcceptedResultHandler",
]
