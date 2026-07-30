"""Frozen chat/delegate workers for the canonical governed Task Runtime.

These wrappers consume one already-planned TaskNode.  They cannot select a
route, inspect search alternatives, mutate a graph, or invoke a planner.  A
terminal public projection must be durably staged before the coordinator can
observe the single COMMIT event.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Any, Protocol
from urllib.parse import unquote

from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.contracts.agent_runtime.projection_recovery import (
    ProjectionRecoveryContractError,
    validate_worker_timing,
)
from host_brain.contracts.agent_runtime.node_artifact import (
    artifact_digest,
    is_task_result_artifact_ref,
)
from host_brain.task_runtime.worker_registry import GovernedWorkerRoute
from host_brain.contracts.agent_runtime.worker_budget_usage import (
    WorkerBudgetUsage,
    WorkerBudgetUsageError,
)
from host_brain.runtime.action_preview_authority import (
    ActionPreviewAuthorityError,
    context_pack_digest_from_work_order,
)
from host_brain.runtime.action_preview_resolver import (
    action_preview_scene_identity,
    build_node_authority_context,
)
from natsume_contracts.task import HostCommand


GOVERNED_NODE_INPUT_INVALID = "GOVERNED_NODE_INPUT_INVALID"
GOVERNED_NODE_GRANT_INVALID = "GOVERNED_NODE_GRANT_INVALID"
GOVERNED_NODE_COMMIT_INVALID = "GOVERNED_NODE_COMMIT_INVALID"
GOVERNED_NODE_PROJECTION_FAILED = "GOVERNED_NODE_PROJECTION_FAILED"


class GovernedNodeWorkerError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or GOVERNED_NODE_INPUT_INVALID)
        self.detail = str(detail or "governed node worker failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


@dataclass(frozen=True, slots=True)
class FrozenNodeInput:
    command_id: str
    request_id: str
    session_id: str
    principal_id: str
    work_order_id: str
    root_work_order_id: str
    graph_id: str
    graph_revision: int
    node_id: str
    attempt_id: str
    lease_id: str
    lease_owner_id: str
    fencing_token: int
    node_risk_level: int
    route: str
    target_id: str
    objective: str
    input_refs: tuple[str, ...]
    _input_pack_json: str = field(repr=False)
    _input_artifacts_json: str = field(repr=False)
    node_grant_ref: str
    parent_grant_ref: str
    _budget_json: str = field(default="{}", repr=False)
    reservation_id: str = ""
    business_scope: str = ""
    operation_fingerprint: str = ""

    @property
    def input_pack(self) -> Mapping[str, Any]:
        """Return a fresh projection so an executor cannot mutate frozen input."""

        loaded = json.loads(self._input_pack_json)
        return dict(loaded) if isinstance(loaded, Mapping) else {}

    @property
    def input_artifacts(self) -> tuple[Mapping[str, Any], ...]:
        """Return fresh immutable-by-copy predecessor output projections."""

        loaded = json.loads(self._input_artifacts_json)
        return tuple(dict(item) for item in loaded if isinstance(item, Mapping))

    @property
    def budget(self) -> Mapping[str, Any]:
        """Return the exact durable budget projection frozen for this node."""

        loaded = json.loads(self._budget_json)
        return dict(loaded) if isinstance(loaded, Mapping) else {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "request_id": self.request_id,
            "session_id": self.session_id,
            "principal_id": self.principal_id,
            "work_order_id": self.work_order_id,
            "root_work_order_id": self.root_work_order_id,
            "graph_id": self.graph_id,
            "graph_revision": self.graph_revision,
            "node_id": self.node_id,
            "attempt_id": self.attempt_id,
            "lease_id": self.lease_id,
            "lease_owner_id": self.lease_owner_id,
            "fencing_token": self.fencing_token,
            "node_risk_level": self.node_risk_level,
            "route": self.route,
            "target_id": self.target_id,
            "objective": self.objective,
            "input_refs": list(self.input_refs),
            "input_pack": dict(self.input_pack),
            "input_artifacts": [dict(item) for item in self.input_artifacts],
            "node_grant_ref": self.node_grant_ref,
            "parent_grant_ref": self.parent_grant_ref,
            "budget": dict(self.budget),
            "reservation_id": self.reservation_id,
            "business_scope": self.business_scope,
            "operation_fingerprint": self.operation_fingerprint,
        }


class FrozenRouteExecutorPort(Protocol):
    def execute_stream(self, frozen: FrozenNodeInput) -> AsyncIterator[Mapping[str, Any]]: ...


class NodeGrantVerifierPort(Protocol):
    def verify(
        self,
        *,
        grant_ref: str,
        parent_grant_ref: str,
        request_id: str,
        session_id: str,
        principal_id: str,
        graph_id: str,
        graph_revision: int,
        root_work_order_id: str,
        node_id: str,
        route: str,
        target_id: str,
        attempt_id: str | None = None,
        lease_id: str | None = None,
        lease_owner_id: str | None = None,
        fencing_token: int | None = None,
    ) -> Any: ...


class WorkerProjectionWriterPort(Protocol):
    def stage(self, *, projection_id: str, projection: Mapping[str, Any]) -> Mapping[str, Any]: ...


class GovernedFrozenNodeWorker:
    """Execute one frozen node and durably stage its only terminal projection."""

    def __init__(
        self,
        *,
        route_kind: str,
        executor: FrozenRouteExecutorPort,
        node_grants: NodeGrantVerifierPort,
        projections: WorkerProjectionWriterPort,
        action_preview_service: Any | None = None,
        allowed_acceptance_effect_types: frozenset[str] | None = None,
        preview_purpose: str | None = None,
    ) -> None:
        kind = str(route_kind or "").strip().lower()
        if kind not in {"chat", "delegate", "tool"}:
            raise ValueError("route_kind must be chat, delegate or tool")
        if not callable(getattr(executor, "execute_stream", None)):
            raise ValueError("executor.execute_stream is required")
        if not callable(getattr(node_grants, "verify", None)):
            raise ValueError("node_grants.verify is required")
        if not callable(getattr(projections, "stage", None)):
            raise ValueError("projections.stage is required")
        self._route_kind = kind
        self._executor = executor
        self._node_grants = node_grants
        self._projections = projections
        self._action_preview_service = action_preview_service
        purpose = str(preview_purpose or "").strip().lower()
        if purpose and purpose not in {"delegate.dispatch", "scheduler.execute"}:
            raise ValueError("preview_purpose must be delegate.dispatch or scheduler.execute")
        if purpose == "scheduler.execute" and kind != "delegate":
            raise ValueError("scheduler.execute Preview requires a delegate route")
        self._preview_purpose = purpose or None
        allowed_effects = (
            frozenset({"life.foreground_completed"})
            if allowed_acceptance_effect_types is None
            else frozenset(
                str(value or "").strip()
                for value in allowed_acceptance_effect_types
                if str(value or "").strip()
            )
        )
        if not allowed_effects:
            raise ValueError("allowed_acceptance_effect_types must not be empty")
        self._allowed_acceptance_effect_types = allowed_effects

    async def execute_stream(
        self,
        *,
        command: HostCommand,
        work_order: WorkOrderV3,
        execution_context: Mapping[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        frozen = _freeze_node_input(
            command=command,
            work_order=work_order,
            execution_context=execution_context,
            expected_route_kind=self._route_kind,
        )
        verified = self._node_grants.verify(
            grant_ref=frozen.node_grant_ref,
            parent_grant_ref=frozen.parent_grant_ref,
            request_id=frozen.request_id,
            session_id=frozen.session_id,
            principal_id=frozen.principal_id,
            graph_id=frozen.graph_id,
            graph_revision=frozen.graph_revision,
            root_work_order_id=frozen.root_work_order_id,
            node_id=frozen.node_id,
            route=frozen.route,
            target_id=frozen.target_id,
            attempt_id=frozen.attempt_id,
            lease_id=frozen.lease_id,
            lease_owner_id=frozen.lease_owner_id,
            fencing_token=frozen.fencing_token,
        )
        _assert_verified_grant(verified, frozen=frozen)
        preview = self._preview_action(
            command=command,
            work_order=work_order,
            frozen=frozen,
            verified_grant=_projection(verified),
        )

        terminal: dict[str, Any] | None = None
        terminal_count = 0
        buffered_event_count = 0
        visible_event_count = 0
        execute_with_recovery = getattr(
            self._executor,
            "execute_stream_with_recovery",
            None,
        )
        stream = (
            execute_with_recovery(
                frozen,
                recovery_context={
                    "schema_version": "Natsume.internal.projection_recovery.v1",
                    "frozen_node_input": frozen.to_dict(),
                    "verified_node_grant": _projection(verified),
                    "action_preview": (
                        dict(preview) if preview is not None else None
                    ),
                    "buffered_pre_acceptance_events": 0,
                },
            )
            if callable(execute_with_recovery)
            else self._executor.execute_stream(frozen)
        )
        async for raw in stream:
            event = dict(raw) if isinstance(raw, Mapping) else {}
            if bool(event.get("done")) or str(event.get("phase") or "").strip().upper() == "COMMIT":
                terminal_count += 1
                terminal = event
                continue
            if _task_bound_chat_visibility_event(
                event,
                frozen=frozen,
                route_kind=self._route_kind,
                expected_index=visible_event_count,
            ):
                visible_event_count += 1
                # The frozen executor has already verified that this text was
                # written into the immutable Task delivery ledger.  It is the
                # only pre-Acceptance event permitted past a governed worker.
                yield event
                continue
            # A worker is still speculative until Verification and Acceptance
            # commit.  All other deltas remain non-public until the accepted
            # terminal projection is available.
            buffered_event_count += 1
        if terminal_count != 1 or terminal is None:
            raise GovernedNodeWorkerError(
                GOVERNED_NODE_COMMIT_INVALID,
                f"frozen executor must emit exactly one terminal event, got {terminal_count}",
            )
        yield self._stage_terminal_projection(
            frozen=frozen,
            verified_grant=_projection(verified),
            preview=preview,
            terminal=terminal,
            buffered_event_count=buffered_event_count,
            visible_event_count=visible_event_count,
        )

    def recover_sealed_checkpoint(
        self,
        *,
        command: HostCommand,
        work_order: WorkOrderV3,
        execution_context: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Rebuild one projection from a protected seal without rerunning work."""

        recover = getattr(self._executor, "recover_terminal_projection", None)
        if not callable(recover):
            return None
        frozen = _freeze_node_input(
            command=command,
            work_order=work_order,
            execution_context=execution_context,
            expected_route_kind=self._route_kind,
        )
        raw = recover(frozen)
        material = dict(raw) if isinstance(raw, Mapping) else {}
        terminal = material.get("terminal")
        verified = material.get("verified_node_grant")
        preview = material.get("action_preview")
        buffered = material.get("buffered_pre_acceptance_events")
        worker_timing = material.get("worker_timing")
        try:
            worker_timing = validate_worker_timing(worker_timing)
        except ProjectionRecoveryContractError as exc:
            raise GovernedNodeWorkerError(
                GOVERNED_NODE_PROJECTION_FAILED,
                "sealed projection recovery worker timing is invalid",
            ) from exc
        if (
            not isinstance(terminal, Mapping)
            or not isinstance(verified, Mapping)
            or (preview is not None and not isinstance(preview, Mapping))
            or type(buffered) is not int
            or buffered < 0
        ):
            raise GovernedNodeWorkerError(
                GOVERNED_NODE_PROJECTION_FAILED,
                "sealed projection recovery material is incomplete",
            )
        _assert_verified_grant(verified, frozen=frozen)
        self._reopen_recovery_preview(
            frozen=frozen,
            verified_grant=dict(verified),
            preview=(dict(preview) if isinstance(preview, Mapping) else None),
        )
        projection, worker_commit = self._build_terminal_projection(
            frozen=frozen,
            verified_grant=dict(verified),
            preview=(dict(preview) if isinstance(preview, Mapping) else None),
            terminal=dict(terminal),
            buffered_event_count=buffered,
        )
        return {
            "projection": projection,
            "worker_commit": worker_commit,
            "stage_ref": str(material.get("stage_ref") or ""),
            "seal_digest": str(material.get("seal_digest") or ""),
            "worker_timing": dict(worker_timing),
        }

    def _reopen_recovery_preview(
        self,
        *,
        frozen: FrozenNodeInput,
        verified_grant: Mapping[str, Any],
        preview: Mapping[str, Any] | None,
    ) -> None:
        """Reopen the immutable Preview receipt without renewing authority."""

        if self._route_kind != "delegate":
            if preview is not None:
                raise GovernedNodeWorkerError(
                    GOVERNED_NODE_GRANT_INVALID,
                    "sealed recovery contains an unexpected ActionPreview",
                )
            return
        if preview is None or self._action_preview_service is None:
            raise GovernedNodeWorkerError(
                GOVERNED_NODE_GRANT_INVALID,
                "sealed Delegate recovery has no durable ActionPreview authority",
            )
        reopen = getattr(self._action_preview_service, "reopen_receipt", None)
        if not callable(reopen):
            raise GovernedNodeWorkerError(
                GOVERNED_NODE_GRANT_INVALID,
                "ActionPreview authority cannot reopen historical receipts",
            )
        event_id = str(preview.get("event_id") or "").strip()
        reopened_raw = reopen(event_id=event_id)
        reopened = dict(reopened_raw) if isinstance(reopened_raw, Mapping) else {}
        durable_identity = dict(reopened)
        sealed_identity = dict(preview)
        durable_replayed = durable_identity.pop("replayed", None)
        sealed_identity.pop("replayed", None)
        if durable_replayed is not True or durable_identity != sealed_identity:
            raise GovernedNodeWorkerError(
                GOVERNED_NODE_GRANT_INVALID,
                "sealed ActionPreview differs from its durable RuntimeEvent",
            )
        receipt = reopened.get("receipt")
        expected_grant_digest = "sha256:" + _sha256_json(
            dict(verified_grant)
        )
        if (
            not isinstance(receipt, Mapping)
            or str(reopened.get("receipt_ref") or "").strip()
            != str(preview.get("receipt_ref") or "").strip()
            or str(receipt.get("node_grant_ref") or "")
            != frozen.node_grant_ref
            or str(receipt.get("node_grant_digest") or "").lower()
            != expected_grant_digest
        ):
            raise GovernedNodeWorkerError(
                GOVERNED_NODE_GRANT_INVALID,
                "sealed ActionPreview does not bind the verified node grant",
            )

    def _stage_terminal_projection(
        self,
        *,
        frozen: FrozenNodeInput,
        verified_grant: Mapping[str, Any],
        preview: Mapping[str, Any] | None,
        terminal: Mapping[str, Any],
        buffered_event_count: int,
        visible_event_count: int = 0,
    ) -> dict[str, Any]:
        projection, worker_commit = self._build_terminal_projection(
            frozen=frozen,
            verified_grant=verified_grant,
            preview=preview,
            terminal=terminal,
            buffered_event_count=buffered_event_count,
            visible_event_count=visible_event_count,
        )
        projection_id = str(projection["projection_id"])
        payload_digest = str(projection["payload_digest"])
        record = self._projections.stage(
            projection_id=projection_id,
            projection=projection,
        )
        durable = dict(record) if isinstance(record, Mapping) else {}
        if (
            str(durable.get("projection_id") or "") != projection_id
            or str(durable.get("payload_digest") or "") != payload_digest
        ):
            raise GovernedNodeWorkerError(
                GOVERNED_NODE_PROJECTION_FAILED,
                "projection writer did not return the exact durable projection",
            )
        return worker_commit

    def _build_terminal_projection(
        self,
        *,
        frozen: FrozenNodeInput,
        verified_grant: Mapping[str, Any],
        preview: Mapping[str, Any] | None,
        terminal: Mapping[str, Any],
        buffered_event_count: int,
        visible_event_count: int = 0,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        _assert_terminal(terminal, frozen=frozen)
        normalized = {
            "phase": "COMMIT",
            "done": True,
            "decision": str(terminal.get("decision") or "").strip().upper(),
            "reason_code": str(terminal.get("reason_code") or "").strip() or None,
            "command_id": frozen.command_id,
            "request_id": frozen.request_id,
            "narrative": str(terminal.get("narrative") or "").strip(),
            "route_type": frozen.route,
            "target_id": frozen.target_id,
            "graph_id": frozen.graph_id,
            "graph_revision": frozen.graph_revision,
            "node_id": frozen.node_id,
            "attempt_id": frozen.attempt_id,
            "node_grant_ref": frozen.node_grant_ref,
            "audit_refs": _safe_audit_refs(terminal.get("audit_refs")),
        }
        acceptance_effect_intents = _safe_acceptance_effect_intents(
            terminal.get("acceptance_effect_intents"),
            allowed_effect_types=self._allowed_acceptance_effect_types,
        )
        if acceptance_effect_intents:
            normalized["acceptance_effect_intents"] = acceptance_effect_intents
        raw_budget_usage = terminal.get("budget_usage")
        if raw_budget_usage is not None:
            try:
                budget_usage = WorkerBudgetUsage.from_payload(
                    raw_budget_usage if isinstance(raw_budget_usage, Mapping) else {}
                )
            except WorkerBudgetUsageError as exc:
                raise GovernedNodeWorkerError(
                    GOVERNED_NODE_COMMIT_INVALID,
                    f"frozen executor returned invalid budget usage: {exc}",
                ) from exc
            normalized["budget_usage"] = budget_usage.to_payload()
        normalized["audit_refs"]["buffered_pre_acceptance_events"] = int(
            buffered_event_count
        )
        normalized["audit_refs"]["task_bound_visible_pre_acceptance_events"] = int(
            visible_event_count
        )
        normalized["audit_refs"].update(
            {
                "node_grant_ref": frozen.node_grant_ref,
                "node_grant_digest": "sha256:"
                + _sha256_json(dict(verified_grant)),
            }
        )
        if preview is not None:
            normalized["audit_refs"].update(
                {
                    "action_preview_receipt_ref": preview["receipt_ref"],
                    "action_preview_event_id": preview["event_id"],
                }
            )
        payload_digest = _sha256_json(normalized)
        projection_id = (
            f"worker-projection://{_ref(frozen.graph_id)}/{frozen.graph_revision}/"
            f"{_ref(frozen.node_id)}/{_ref(frozen.attempt_id)}/{payload_digest}"
        )
        projection = {
            **normalized,
            "projection_id": projection_id,
            "payload_digest": f"sha256:{payload_digest}",
            "schema_version": "Natsume.task_runtime.worker_projection.v1",
        }
        commit_audit_refs = {
            **normalized["audit_refs"],
            "worker_projection_ref": projection_id,
            "node_grant_ref": frozen.node_grant_ref,
        }
        # Preserve an executor-owned source path when it is part of the
        # immutable projection.  Overwriting it only in the transient COMMIT
        # makes independent Verification correctly report commit drift.
        commit_audit_refs.setdefault(
            "path",
            "governed_task_runtime:frozen_node",
        )
        worker_commit = {
            **normalized,
            "receipt_ref": projection_id,
            "audit_refs": commit_audit_refs,
        }
        return projection, worker_commit

    def _preview_action(
        self,
        *,
        command: HostCommand,
        work_order: WorkOrderV3,
        frozen: FrozenNodeInput,
        verified_grant: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        if self._route_kind not in {"delegate", "tool"}:
            return None
        if self._route_kind == "tool" and frozen.node_risk_level < 3:
            return None
        pack = dict(work_order.input_pack or {})
        if pack.get("runtime_context_schema") != "natsume.runtime-context.request.v1":
            if self._action_preview_service is not None:
                raise GovernedNodeWorkerError(
                    GOVERNED_NODE_GRANT_INVALID,
                    "legacy Delegate WorkOrder is quarantined without Runtime Context",
                )
            return None
        service = self._action_preview_service
        if service is None:
            raise GovernedNodeWorkerError(
                GOVERNED_NODE_GRANT_INVALID,
                "governed Delegate has no app-local ActionPreview authority",
            )
        try:
            scheduler_preview = self._preview_purpose == "scheduler.execute"
            action_args = {
                "objective_digest": "sha256:"
                + hashlib.sha256(frozen.objective.encode("utf-8")).hexdigest(),
                "input_refs": list(frozen.input_refs),
            }
            if scheduler_preview:
                occurrence_id = str(pack.get("occurrence_id") or "").strip()
                definition_ref = str(pack.get("definition_ref") or "").strip()
                if not occurrence_id or not definition_ref:
                    raise GovernedNodeWorkerError(
                        GOVERNED_NODE_GRANT_INVALID,
                        "governed Scheduler Preview requires frozen occurrence and definition refs",
                    )
                action_args = {
                    "occurrence_id": occurrence_id,
                    "definition_ref": definition_ref,
                    "objective_digest": action_args["objective_digest"],
                }
            if self._route_kind == "tool":
                intent = pack.get("intent_draft")
                if not isinstance(intent, Mapping):
                    raise GovernedNodeWorkerError(
                        GOVERNED_NODE_GRANT_INVALID,
                        "governed Tool Preview requires its frozen intent projection",
                    )
                if (
                    str(intent.get("route") or "").strip().lower() != "tool"
                    or str(intent.get("target_id") or "").strip()
                    != frozen.target_id
                    or not isinstance(intent.get("tool_binding"), Mapping)
                    or not intent.get("tool_binding")
                ):
                    raise GovernedNodeWorkerError(
                        GOVERNED_NODE_GRANT_INVALID,
                        "governed Tool Preview route/target differs from the TaskNode",
                    )
                # Bind the actual immutable execution payload, not merely the
                # objective.  The WorkOrder freezer already rejected
                # authority-bearing/sensitive fields before this point.
                action_args = {
                    "tool_id": frozen.target_id,
                    "args": _json_copy(dict(intent.get("args") or {})),
                    "context": _json_copy(dict(intent.get("context") or {})),
                    "tool_binding": _json_copy(
                        dict(intent.get("tool_binding") or {})
                    ),
                }
            kwargs = {
                "action_kind": (
                    f"scheduler.{frozen.target_id}"
                    if scheduler_preview
                    else "delegate.dispatch"
                    if self._route_kind == "delegate"
                    else "tool.execute"
                ),
                "action_scope": frozen.target_id,
                # The initial pentest adapter is read-only, but the domain is
                # security-sensitive and therefore keeps the high-risk gate.
                "risk_level": frozen.node_risk_level,
                "action_args": action_args,
                "context_pack_digest": context_pack_digest_from_work_order(work_order),
                "scene_identity": action_preview_scene_identity(
                    dict(pack.get("scene_identity_v2") or {})
                ),
                "task_graph_id": frozen.graph_id,
                "graph_revision": frozen.graph_revision,
                "task_node_id": frozen.node_id,
                "parent_governor_grant_ref": frozen.parent_grant_ref,
                "node_grant_ref": frozen.node_grant_ref,
                "node_grant": dict(verified_grant),
                "guardian_capability_id": (
                    f"scheduler_{frozen.target_id}"
                    if scheduler_preview
                    else f"delegate_{frozen.target_id}"
                    if self._route_kind == "delegate"
                    else frozen.target_id
                ).lower(),
                "authority_context": build_node_authority_context(
                    command=command,
                    execution_context={
                        "attempt_id": frozen.attempt_id,
                        "lease_id": frozen.lease_id,
                        "lease_owner_id": frozen.lease_owner_id,
                        "fencing_token": frozen.fencing_token,
                    },
                    purpose=(
                        "scheduler.execute"
                        if scheduler_preview
                        else "delegate.dispatch"
                        if self._route_kind == "delegate"
                        else "tool.execute"
                    ),
                ),
            }
            issued = service.issue_receipt(**kwargs)
            service.require_execution(
                event_id=issued["event_id"],
                **{
                    key: value
                    for key, value in kwargs.items()
                    if key != "guardian_capability_id"
                },
            )
        except ActionPreviewAuthorityError as exc:
            raise GovernedNodeWorkerError(exc.reason_code, exc.detail) from exc
        return dict(issued)


def _freeze_node_input(
    *,
    command: HostCommand,
    work_order: WorkOrderV3,
    execution_context: Mapping[str, Any],
    expected_route_kind: str,
) -> FrozenNodeInput:
    if not isinstance(command, HostCommand) or not isinstance(work_order, WorkOrderV3):
        raise GovernedNodeWorkerError(
            GOVERNED_NODE_INPUT_INVALID,
            "HostCommand and explicit WorkOrderV3 are required",
        )
    work_order.validate()
    context = dict(execution_context or {})
    graph_id = _required(context.get("graph_id"), "graph_id")
    node_id = _required(context.get("node_id"), "node_id")
    attempt_id = _required(context.get("attempt_id"), "attempt_id")
    lease_id = _required(context.get("lease_id"), "lease_id")
    lease_owner_id = _required(
        context.get("lease_owner_id"),
        "lease_owner_id",
    )
    try:
        fencing_token = int(context.get("fencing_token"))
        node_risk_level = int(context.get("node_risk_level"))
    except (TypeError, ValueError) as exc:
        raise GovernedNodeWorkerError(
            GOVERNED_NODE_INPUT_INVALID,
            "fencing_token must be a positive integer",
        ) from exc
    if fencing_token < 1 or not 1 <= node_risk_level <= 5:
        raise GovernedNodeWorkerError(
            GOVERNED_NODE_INPUT_INVALID,
            "fencing_token and node_risk_level must be valid",
        )
    route_value = _required(context.get("worker_route"), "worker_route")
    route = GovernedWorkerRoute.parse(route_value)
    if route.kind != expected_route_kind or not route.target_id:
        raise GovernedNodeWorkerError(
            GOVERNED_NODE_INPUT_INVALID,
            f"worker expected {expected_route_kind}:target route, got {route_value}",
        )
    try:
        revision = int(context.get("graph_revision"))
    except (TypeError, ValueError) as exc:
        raise GovernedNodeWorkerError(
            GOVERNED_NODE_INPUT_INVALID,
            "graph_revision must be an integer",
        ) from exc
    if (
        revision < 1
        or graph_id != work_order.task_graph_id
        or revision != work_order.graph_revision
        or _required(context.get("work_order_id"), "work_order_id") != work_order.work_order_id
        or _required(context.get("command_id"), "command_id") != command.command_id
        or command.graph_id != graph_id
        or command.graph_revision != revision
        or command.root_work_order_id != work_order.root_work_order_id
    ):
        raise GovernedNodeWorkerError(
            GOVERNED_NODE_INPUT_INVALID,
            "command/work-order/execution graph lineage differs",
        )
    binding = _node_binding(work_order, node_id=node_id)
    if (
        str(binding.get("worker_route") or "").strip().lower() != route.kind
        or str(binding.get("target_id") or "").strip() != route.target_id
    ):
        raise GovernedNodeWorkerError(
            GOVERNED_NODE_INPUT_INVALID,
            "frozen node route differs from WorkOrder planning lineage",
        )
    grant_ref = _required(context.get("node_grant_ref"), "node_grant_ref")
    parent_ref = _required(work_order.approval_ref, "work_order.approval_ref")
    if grant_ref == parent_ref:
        raise GovernedNodeWorkerError(
            GOVERNED_NODE_GRANT_INVALID,
            "node execution cannot reuse the parent governor grant",
        )
    raw_refs = context.get("node_input_refs")
    input_refs = tuple(
        dict.fromkeys(
            str(value or "").strip()
            for value in (raw_refs if isinstance(raw_refs, Sequence) and not isinstance(raw_refs, str) else ())
            if str(value or "").strip()
        )
    )
    raw_artifacts = context.get("node_input_artifacts", ())
    if not isinstance(raw_artifacts, Sequence) or isinstance(
        raw_artifacts,
        (str, bytes, bytearray),
    ):
        raise GovernedNodeWorkerError(
            GOVERNED_NODE_INPUT_INVALID,
            "node_input_artifacts must be an array",
        )
    input_artifacts = [
        _json_copy(item)
        for item in raw_artifacts
        if isinstance(item, Mapping)
    ]
    if len(input_artifacts) != len(raw_artifacts):
        raise GovernedNodeWorkerError(
            GOVERNED_NODE_INPUT_INVALID,
            "node_input_artifacts must contain only objects",
        )
    expected_artifact_refs = {
        ref for ref in input_refs if is_task_result_artifact_ref(ref)
    }
    actual_artifact_refs = {
        str(item.get("artifact_ref") or "").strip()
        for item in input_artifacts
    }
    if (
        len(actual_artifact_refs) != len(input_artifacts)
        or actual_artifact_refs != expected_artifact_refs
    ):
        raise GovernedNodeWorkerError(
            GOVERNED_NODE_INPUT_INVALID,
            "resolved node artifacts differ from frozen input_refs",
        )
    for artifact in input_artifacts:
        payload = artifact.get("payload")
        if not isinstance(payload, Mapping) or str(
            artifact.get("payload_sha256") or ""
        ) != artifact_digest(payload):
            raise GovernedNodeWorkerError(
                GOVERNED_NODE_INPUT_INVALID,
                "resolved node artifact payload digest is invalid",
            )
    budget = _freeze_budget_payload(context.get("budget"))
    reservation_id = _required(context.get("reservation_id"), "reservation_id")
    business_scope = _required(context.get("business_scope"), "business_scope")
    operation_fingerprint = _required(
        context.get("operation_fingerprint"),
        "operation_fingerprint",
    )
    return FrozenNodeInput(
        command_id=command.command_id,
        request_id=command.request_id,
        session_id=command.session_id,
        principal_id=command.principal_id,
        work_order_id=work_order.work_order_id,
        root_work_order_id=work_order.root_work_order_id,
        graph_id=graph_id,
        graph_revision=revision,
        node_id=node_id,
        attempt_id=attempt_id,
        lease_id=lease_id,
        lease_owner_id=lease_owner_id,
        fencing_token=fencing_token,
        node_risk_level=node_risk_level,
        route=route.kind,
        target_id=route.target_id,
        objective=_required(binding.get("objective"), "node binding objective"),
        input_refs=input_refs,
        _input_pack_json=json.dumps(
            _json_copy(work_order.input_pack),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
        _input_artifacts_json=json.dumps(
            input_artifacts,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
        node_grant_ref=grant_ref,
        parent_grant_ref=parent_ref,
        _budget_json=json.dumps(
            budget,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
        reservation_id=reservation_id,
        business_scope=business_scope,
        operation_fingerprint=operation_fingerprint,
    )


def _node_binding(work_order: WorkOrderV3, *, node_id: str) -> dict[str, Any]:
    lineage = work_order.metadata.get("planning_lineage")
    lineage_map = dict(lineage) if isinstance(lineage, Mapping) else {}
    raw = lineage_map.get("node_bindings")
    rows = list(raw) if isinstance(raw, list) else []
    matches = [dict(row) for row in rows if isinstance(row, Mapping) and str(row.get("node_id") or "") == node_id]
    if len(matches) != 1:
        raise GovernedNodeWorkerError(
            GOVERNED_NODE_INPUT_INVALID,
            f"WorkOrder requires exactly one frozen binding for node {node_id}",
        )
    return matches[0]


def _freeze_budget_payload(value: Any) -> dict[str, Any]:
    """Freeze only budget dimensions that can constrain a model call.

    ``execution_context`` is built by ``NodeExecutionRuntime`` after durable
    precharge.  Requiring this exact projection prevents a worker input pack
    from inventing a larger deadline, retry allowance, or output allowance.
    """

    raw = dict(value) if isinstance(value, Mapping) else {}
    budget_id = _required(raw.get("budget_id"), "budget.budget_id")
    status = str(raw.get("status") or "").strip().upper()
    if status != "ACTIVE":
        raise GovernedNodeWorkerError(
            GOVERNED_NODE_INPUT_INVALID,
            "budget.status must be ACTIVE before worker execution",
        )
    try:
        status_version = int(raw.get("status_version"))
    except (TypeError, ValueError) as exc:
        raise GovernedNodeWorkerError(
            GOVERNED_NODE_INPUT_INVALID,
            "budget.status_version must be a non-negative integer",
        ) from exc
    if status_version < 0:
        raise GovernedNodeWorkerError(
            GOVERNED_NODE_INPUT_INVALID,
            "budget.status_version must be a non-negative integer",
        )
    remaining_raw = raw.get("remaining")
    if not isinstance(remaining_raw, Mapping):
        raise GovernedNodeWorkerError(
            GOVERNED_NODE_INPUT_INVALID,
            "budget.remaining must be an object",
        )
    remaining: dict[str, int] = {}
    for dimension in (
        "token_count",
        "time_ms",
        "retry_count",
        "model_cost_micros",
    ):
        value = remaining_raw.get(dimension)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise GovernedNodeWorkerError(
                GOVERNED_NODE_INPUT_INVALID,
                f"budget.remaining.{dimension} must be a non-negative integer",
            )
        remaining[dimension] = int(value)
    return {
        "budget_id": budget_id,
        "status": status,
        "status_version": status_version,
        "remaining": remaining,
    }


def _assert_verified_grant(value: Any, *, frozen: FrozenNodeInput) -> None:
    verified = _projection(value)
    expected = {
        "grant_ref": frozen.node_grant_ref,
        "parent_grant_ref": frozen.parent_grant_ref,
        "graph_id": frozen.graph_id,
        "graph_revision": frozen.graph_revision,
        "root_work_order_id": frozen.root_work_order_id,
        "node_id": frozen.node_id,
        "route": frozen.route,
        "target_id": frozen.target_id,
    }
    actual = {key: verified.get(key) for key in expected}
    if actual != expected:
        raise GovernedNodeWorkerError(
            GOVERNED_NODE_GRANT_INVALID,
            "node grant verifier did not return the exact frozen authority binding",
        )


def _projection(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    projector = getattr(value, "to_dict", None)
    projected = projector() if callable(projector) else None
    return dict(projected) if isinstance(projected, Mapping) else {}


def _task_bound_chat_visibility_event(
    event: Mapping[str, Any],
    *,
    frozen: FrozenNodeInput,
    route_kind: str,
    expected_index: int,
) -> bool:
    """Prove that a nonterminal event is one durable Chat sentence receipt."""

    markers = {
        "delivery_id",
        "delivery_status",
        "graph_id",
        "attempt_id",
        "lease_id",
        "node_grant_ref",
        "relationship_subject_id",
    }
    claims_visibility = bool(markers.intersection(event))
    if not claims_visibility:
        return False
    if route_kind != "chat" or str(event.get("phase") or "").strip().upper() != "SENTENCE":
        raise GovernedNodeWorkerError(
            GOVERNED_NODE_COMMIT_INVALID,
            "only a frozen Chat sentence may carry pre-acceptance visibility authority",
        )
    delivery_id = str(event.get("delivery_id") or "").strip()
    if (
        not re.fullmatch(r"taskchat://[0-9a-f]{64}", delivery_id)
        or str(event.get("delivery_status") or "").strip()
        != "TASK_NODE_SENTENCE_STAGED"
    ):
        raise GovernedNodeWorkerError(
            GOVERNED_NODE_COMMIT_INVALID,
            "pre-acceptance Chat sentence does not have a canonical durable receipt",
        )
    sentence = str(event.get("sentence") or "").strip()
    if not sentence or len(sentence) > 2_000:
        raise GovernedNodeWorkerError(
            GOVERNED_NODE_COMMIT_INVALID,
            "pre-acceptance Chat sentence content is invalid",
        )
    try:
        sentence_index = int(event.get("sentence_index"))
        revision = int(event.get("graph_revision"))
        affect_revision = int(event.get("self_affect_revision"))
    except (TypeError, ValueError) as exc:
        raise GovernedNodeWorkerError(
            GOVERNED_NODE_COMMIT_INVALID,
            "pre-acceptance Chat sentence has invalid numeric identity",
        ) from exc
    relationship_subject_id = _frozen_relationship_subject_id(frozen)
    if (
        sentence_index != expected_index
        or revision != frozen.graph_revision
        or affect_revision < 0
        or {
            "request_id": str(event.get("request_id") or "").strip(),
            "graph_id": str(event.get("graph_id") or "").strip(),
            "node_id": str(event.get("node_id") or "").strip(),
            "attempt_id": str(event.get("attempt_id") or "").strip(),
            "lease_id": str(event.get("lease_id") or "").strip(),
            "lease_owner_id": str(event.get("lease_owner_id") or "").strip(),
            "fencing_token": _positive_event_int(
                event.get("fencing_token"), field_name="fencing_token"
            ),
            "node_grant_ref": str(event.get("node_grant_ref") or "").strip(),
            "self_id": str(event.get("self_id") or "").strip(),
            "relationship_subject_id": str(
                event.get("relationship_subject_id") or ""
            ).strip(),
        }
        != {
            "request_id": frozen.request_id,
            "graph_id": frozen.graph_id,
            "node_id": frozen.node_id,
            "attempt_id": frozen.attempt_id,
            "lease_id": frozen.lease_id,
            "lease_owner_id": frozen.lease_owner_id,
            "fencing_token": frozen.fencing_token,
            "node_grant_ref": frozen.node_grant_ref,
            "self_id": "natsume_ai",
            "relationship_subject_id": relationship_subject_id,
        }
        or not str(event.get("workspace_id") or "").strip()
        or not str(event.get("affective_turn_plan_id") or "").strip()
        or not str(event.get("synchronization_id") or "").strip()
    ):
        raise GovernedNodeWorkerError(
            GOVERNED_NODE_COMMIT_INVALID,
            "pre-acceptance Chat sentence differs from frozen Task identity",
        )
    return True


def _positive_event_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool):
        raise GovernedNodeWorkerError(
            GOVERNED_NODE_COMMIT_INVALID,
            f"pre-acceptance Chat sentence has invalid {field_name}",
        )
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise GovernedNodeWorkerError(
            GOVERNED_NODE_COMMIT_INVALID,
            f"pre-acceptance Chat sentence has invalid {field_name}",
        ) from exc
    if parsed < 1:
        raise GovernedNodeWorkerError(
            GOVERNED_NODE_COMMIT_INVALID,
            f"pre-acceptance Chat sentence has invalid {field_name}",
        )
    return parsed


def _is_zero_visible_sentence_count(value: Any) -> bool:
    """Recognize only a well-formed zero without leaking parser failures."""

    if isinstance(value, bool):
        return False
    try:
        return int(value) == 0
    except (TypeError, ValueError):
        return False


def _frozen_relationship_subject_id(frozen: FrozenNodeInput) -> str:
    input_pack = frozen.input_pack
    request_body = input_pack.get("request_body")
    request_body = request_body if isinstance(request_body, Mapping) else {}
    context_schema = str(input_pack.get("runtime_context_schema") or "").strip()
    if context_schema:
        # The runtime context is the immutable ingress authority for new Host
        # graphs.  It intentionally keeps the relationship subject distinct
        # from the authenticated transport principal; do not fall back to a
        # caller-controlled request field once it exists.
        if context_schema != "natsume.runtime-context.request.v1":
            raise GovernedNodeWorkerError(
                GOVERNED_NODE_COMMIT_INVALID,
                "frozen Chat runtime context schema is invalid",
            )
        subject = str(input_pack.get("subject_id") or "").strip()
    else:
        # Compatibility only for already accepted graphs from before typed
        # Runtime Context was introduced.
        subject = str(
            input_pack.get("relationship_subject_id")
            or request_body.get("relationship_subject_id")
            or ""
        ).strip()
    if not subject:
        raise GovernedNodeWorkerError(
            GOVERNED_NODE_COMMIT_INVALID,
            "frozen Chat context lacks canonical relationship subject",
        )
    return subject


def _assert_terminal(event: Mapping[str, Any], *, frozen: FrozenNodeInput) -> None:
    decision = str(event.get("decision") or "").strip().upper()
    narrative = str(event.get("narrative") or "").strip()
    supplied_route = str(event.get("route_type") or frozen.route).strip().lower()
    supplied_target = str(event.get("target_id") or frozen.target_id).strip()
    audit_refs = event.get("audit_refs")
    task_delivery = (
        audit_refs.get("task_bound_sentence_delivery")
        if isinstance(audit_refs, Mapping)
        else None
    )
    no_visible_chat_reply = (
        frozen.route == "chat"
        and decision == "REJECT"
        and not narrative
        and isinstance(task_delivery, Mapping)
        and str(task_delivery.get("status") or "") == "NO_VISIBLE_REPLY"
        and _is_zero_visible_sentence_count(
            task_delivery.get("visible_sentence_count")
        )
    )
    if decision not in {"APPROVE", "REJECT"} or (
        not narrative and not no_visible_chat_reply
    ):
        raise GovernedNodeWorkerError(
            GOVERNED_NODE_COMMIT_INVALID,
            "terminal event requires APPROVE/REJECT and a narrative unless a Chat failure proved no visible reply",
        )
    if supplied_route != frozen.route or supplied_target != frozen.target_id:
        raise GovernedNodeWorkerError(
            GOVERNED_NODE_COMMIT_INVALID,
            "frozen executor attempted to change node route or target",
        )


def _safe_audit_refs(value: Any) -> dict[str, Any]:
    raw = dict(value) if isinstance(value, Mapping) else {}
    public = {
        str(key): _json_copy(child)
        for key, child in raw.items()
        if str(key).strip() and "token" not in str(key).lower() and "secret" not in str(key).lower()
    }
    trace = public.get("trace_stage_meta")
    if isinstance(trace, Mapping) and "governed_model_budget" in trace:
        # FrozenChatRouteExecutor has already compared this exact guard with
        # the durable node budget before it emits a terminal.  The public
        # worker projection carries the resulting typed budget_usage instead;
        # it must not republish the consumed execution constraint (whose
        # max_*_tokens fields intentionally fail the projection secret gate).
        public_trace = dict(trace)
        public_trace.pop("governed_model_budget", None)
        public["trace_stage_meta"] = public_trace
    return public


def _safe_acceptance_effect_intents(
    value: Any,
    *,
    allowed_effect_types: frozenset[str],
) -> list[dict[str, Any]]:
    if value in (None, (), []):
        return []
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise GovernedNodeWorkerError(
            GOVERNED_NODE_COMMIT_INVALID,
            "acceptance_effect_intents must be a sequence",
        )
    intents: list[dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            raise GovernedNodeWorkerError(
                GOVERNED_NODE_COMMIT_INVALID,
                "acceptance effect intent must be a mapping",
            )
        effect_type = str(raw.get("effect_type") or "").strip()
        commit_scope = str(raw.get("commit_scope") or "").strip().upper()
        payload = raw.get("payload")
        if (
            effect_type not in allowed_effect_types
            or commit_scope != "NODE"
            or not isinstance(payload, Mapping)
        ):
            raise GovernedNodeWorkerError(
                GOVERNED_NODE_COMMIT_INVALID,
                "frozen route emitted an unsupported acceptance effect",
            )
        intents.append(
            {
                "effect_type": effect_type,
                "commit_scope": commit_scope,
                "payload": _json_copy(payload),
            }
        )
    return intents


def _required(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise GovernedNodeWorkerError(
            GOVERNED_NODE_INPUT_INVALID,
            f"{field_name} is required",
        )
    return text


def _sha256_json(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_copy(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise GovernedNodeWorkerError(
            GOVERNED_NODE_INPUT_INVALID,
            "frozen worker input/projection must be JSON-safe",
        ) from exc


def _ref(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:24]


__all__ = [
    "FrozenNodeInput",
    "GOVERNED_NODE_COMMIT_INVALID",
    "GOVERNED_NODE_GRANT_INVALID",
    "GOVERNED_NODE_INPUT_INVALID",
    "GOVERNED_NODE_PROJECTION_FAILED",
    "GovernedFrozenNodeWorker",
    "GovernedNodeWorkerError",
]
