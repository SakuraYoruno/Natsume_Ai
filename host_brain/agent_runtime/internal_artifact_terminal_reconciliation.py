"""Durable continuation for governed Internal Agent terminal artifacts.

The worker owns no execution authority.  It may only abandon a fully sealed
staging artifact after the canonical Task graph is already terminal, or start
workspace cleanup after reopening immutable PROMOTED/ABANDONED evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import threading
import time
from typing import Any

from host_brain.agent_runtime.artifact_store import governed_attempt_segment
from host_brain.contracts.agent_runtime.projection_recovery import (
    ProjectionRecoveryContractError,
    validate_projection_recovery_envelope,
)
from host_brain.contracts.agent_runtime.internal_budget_claim_result import (
    build_internal_budget_rejection_result_ref,
)
from host_brain.contracts.agent_runtime.internal_nonacceptance_claim_result import (
    INTERNAL_NONACCEPTANCE_TERMINAL_REASONS,
    build_internal_nonacceptance_result_ref,
)
from host_brain.contracts.agent_runtime.workspace_lifecycle import (
    WorkspaceLifecycleState,
)


INTERNAL_ARTIFACT_TERMINAL_INVALID = "INTERNAL_ARTIFACT_TERMINAL_INVALID"
_INTERNAL_ROUTE = "delegate:agent_runtime"


class InternalArtifactTerminalReconciliationError(RuntimeError):
    def __init__(self, detail: str) -> None:
        self.reason_code = INTERNAL_ARTIFACT_TERMINAL_INVALID
        self.detail = str(detail or "Internal artifact terminal state is invalid")
        super().__init__(f"{self.reason_code}: {self.detail}")


@dataclass(frozen=True, slots=True)
class InternalArtifactTerminalReport:
    workspace_id: str
    attempt_id: str
    action: str
    reason_code: str | None = None
    receipt_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": self.workspace_id,
            "attempt_id": self.attempt_id,
            "action": self.action,
            "reason_code": self.reason_code,
            "receipt_ref": self.receipt_ref,
        }


class InternalArtifactTerminalReconciler:
    """Reconcile exact sealed/tombstoned artifacts without rerunning Agent."""

    def __init__(
        self,
        *,
        artifact_store: Any,
        workspace_runtime: Any,
        task_store: Any,
        command_repository: Any,
        projection_store: Any,
        claim_result_store: Any | None = None,
    ) -> None:
        required = (
            (artifact_store, "inventory_governed_seals"),
            (artifact_store, "inventory_unsealed_governed_stages"),
            (artifact_store, "discard_unsealed_governed_stage"),
            (artifact_store, "inspect_governed_stage_for_authority"),
            (artifact_store, "read_projection_recovery"),
            (artifact_store, "plan_governed_abandonment"),
            (artifact_store, "abandon_governed_stage"),
            (artifact_store, "inspect_governed_tombstone_ref"),
            (artifact_store, "inspect_governed_promotion"),
            (workspace_runtime, "abandon_terminal_artifact"),
            (workspace_runtime, "cleanup_terminal_workspace"),
            (workspace_runtime, "cleanup"),
            (task_store, "get_graph"),
            (task_store, "get_node"),
            (task_store, "get_attempt"),
            (task_store, "get_lease"),
            (task_store, "get_operation_reservation"),
            (task_store, "get_committed_operation_checkpoint"),
            (task_store, "get_graph_cancellation_for_graph"),
            (task_store, "resolve_internal_agent_takeover_lineage"),
            (task_store, "resolve_internal_agent_budget_rejection_terminal"),
            (task_store, "resolve_internal_agent_nonacceptance_checkpoint_terminal"),
            (command_repository, "get_by_graph"),
            (projection_store, "load"),
        )
        missing = [name for owner, name in required if not callable(getattr(owner, name, None))]
        if missing:
            raise TypeError(
                "Internal artifact terminal reconciler lacks: "
                + ",".join(sorted(set(missing)))
            )
        if claim_result_store is not None and not callable(
            getattr(claim_result_store, "complete_claim", None)
        ):
            raise TypeError(
                "Internal artifact terminal claim store must expose complete_claim"
            )
        repository = getattr(workspace_runtime, "repository", None)
        if not callable(getattr(repository, "list_terminal_lifecycle_candidates", None)):
            raise TypeError("Workspace lifecycle terminal inventory is required")
        self._artifacts = artifact_store
        self._workspaces = workspace_runtime
        self._workspace_repository = repository
        self._tasks = task_store
        self._commands = command_repository
        self._projections = projection_store
        self._claim_results = claim_result_store
        self._staging_cursor: str | None = None
        self._unsealed_staging_cursor: str | None = None
        self._lifecycle_cursor: str | None = None

    def run_once(self, *, limit: int = 100) -> tuple[dict[str, Any], ...]:
        if isinstance(limit, bool) or type(limit) is not int or not 1 <= limit <= 1_000:
            raise InternalArtifactTerminalReconciliationError(
                "terminal reconciliation limit must be within [1, 1000]"
            )
        reports: list[InternalArtifactTerminalReport] = []
        unsealed = self._artifacts.inventory_unsealed_governed_stages(
            limit=limit,
            after_stage_ref=self._unsealed_staging_cursor,
        )
        self._unsealed_staging_cursor = (
            str(unsealed[-1].stage_ref) if len(unsealed) == limit else None
        )
        for stage in unsealed:
            try:
                reason = self._unsealed_cancellation_reason(stage)
                if reason is None:
                    reports.append(
                        InternalArtifactTerminalReport(
                            workspace_id=str(stage.workspace_id),
                            attempt_id=str(stage.attempt_id),
                            action="HOLD",
                        )
                    )
                    continue
                cleanup = self._workspaces.cleanup(str(stage.workspace_id))
                receipt_ref = str(getattr(cleanup, "receipt_ref", "") or "")
                if "://" not in receipt_ref:
                    raise InternalArtifactTerminalReconciliationError(
                        "unsealed Workspace cleanup has no durable receipt"
                    )
                self._artifacts.discard_unsealed_governed_stage(stage)
                reports.append(
                    InternalArtifactTerminalReport(
                        workspace_id=str(stage.workspace_id),
                        attempt_id=str(stage.attempt_id),
                        action="UNSEALED_CLEANED",
                        reason_code=reason,
                        receipt_ref=receipt_ref,
                    )
                )
            except Exception as exc:  # preserve raw stage unless authority is exact
                reports.append(
                    InternalArtifactTerminalReport(
                        workspace_id=str(getattr(stage, "workspace_id", "unknown")),
                        attempt_id=str(getattr(stage, "attempt_id", "unknown")),
                        action="BLOCKED",
                        reason_code=str(
                            getattr(exc, "reason_code", INTERNAL_ARTIFACT_TERMINAL_INVALID)
                        ),
                    )
                )
        staging = self._artifacts.inventory_governed_seals(
            namespace="staging",
            limit=limit,
            after_stage_ref=self._staging_cursor,
        )
        self._staging_cursor = (
            str(staging[-1].stage_ref) if len(staging) == limit else None
        )
        for seal in staging:
            try:
                reason = self._terminal_abandonment_reason(seal)
                if reason is None:
                    reports.append(
                        InternalArtifactTerminalReport(
                            workspace_id=str(seal.workspace_id),
                            attempt_id=str(seal.attempt_id),
                            action="HOLD",
                        )
                    )
                    continue
                if reason == "TERMINAL_BUDGET_REJECTED":
                    self._complete_budget_rejection_claim(seal)
                elif reason in INTERNAL_NONACCEPTANCE_TERMINAL_REASONS:
                    self._complete_nonacceptance_claim(
                        seal,
                        terminal_reason=reason,
                    )
                receipt = self._workspaces.abandon_terminal_artifact(
                    workspace_id=str(seal.workspace_id),
                    execution_authority=dict(seal.execution_authority),
                    stage_ref=str(seal.stage_ref),
                    seal_digest=str(seal.seal_digest),
                    reason_code=reason,
                    artifact_planner=lambda seal=seal, reason=reason: (
                        self._artifacts.plan_governed_abandonment(
                            stage_ref=str(seal.stage_ref),
                            seal_digest=str(seal.seal_digest),
                            reason_code=reason,
                        )
                    ),
                    artifact_abandoner=lambda seal=seal, reason=reason: (
                        self._artifacts.abandon_governed_stage(
                            stage_ref=str(seal.stage_ref),
                            seal_digest=str(seal.seal_digest),
                            reason_code=reason,
                        )
                    ),
                )
                reports.append(
                    InternalArtifactTerminalReport(
                        workspace_id=str(seal.workspace_id),
                        attempt_id=str(seal.attempt_id),
                        action="ABANDONED",
                        reason_code=reason,
                        receipt_ref=str(receipt.tombstone_ref),
                    )
                )
            except Exception as exc:  # fail closed per artifact, preserve other evidence
                reports.append(
                    InternalArtifactTerminalReport(
                        workspace_id=str(getattr(seal, "workspace_id", "unknown")),
                        attempt_id=str(getattr(seal, "attempt_id", "unknown")),
                        action="BLOCKED",
                        reason_code=str(
                            getattr(exc, "reason_code", INTERNAL_ARTIFACT_TERMINAL_INVALID)
                        ),
                    )
                )

        candidates = self._workspace_repository.list_terminal_lifecycle_candidates(
            limit=limit,
            after_workspace_id=self._lifecycle_cursor,
        )
        self._lifecycle_cursor = (
            str(candidates[-1]["authority"].workspace_id)
            if len(candidates) == limit
            else None
        )
        for candidate in candidates:
            authority = candidate["authority"]
            lifecycle = candidate["lifecycle"]
            event = lifecycle.latest_event
            workspace_id = str(authority.workspace_id)
            attempt_id = str(event.attempt_id if event is not None else "unknown")
            try:
                if event is None or not event.terminal_ref:
                    raise InternalArtifactTerminalReconciliationError(
                        "terminal Workspace lifecycle has no terminal evidence"
                    )
                if lifecycle.state is WorkspaceLifecycleState.ABANDONED:
                    tombstone = self._artifacts.inspect_governed_tombstone_ref(
                        str(event.terminal_ref)
                    )
                    if (
                        str(tombstone.get("tombstone_ref") or "")
                        != str(event.terminal_ref)
                        or str(tombstone.get("reason_code") or "")
                        != str(event.reason_code or "")
                        or str(tombstone.get("stage_ref") or "").split("/")[2]
                        != workspace_id
                        or str(tombstone.get("stage_ref") or "").split("/")[3]
                        != governed_attempt_segment(attempt_id)
                    ):
                        raise InternalArtifactTerminalReconciliationError(
                            "Workspace abandonment and tombstone identity differ"
                        )
                elif lifecycle.state is WorkspaceLifecycleState.PROMOTED:
                    seal = self._artifacts.inspect_governed_stage_for_authority(
                        workspace_id=workspace_id,
                        execution_authority={
                            "root_work_order_id": authority.root_work_order_id,
                            "graph_id": event.graph_id,
                            "graph_revision": event.graph_revision,
                            "node_id": event.node_id,
                            "attempt_id": event.attempt_id,
                            "lease_id": event.task_lease_id,
                            "command_id": self._command_id_for_event(event),
                            "principal_id": authority.owner_principal_id,
                            "lease_owner_id": self._lease_owner_for_event(event),
                            "fencing_token": event.task_lease_fencing_token,
                        },
                    )
                    if seal.accepted_dir is None:
                        raise InternalArtifactTerminalReconciliationError(
                            "promoted Workspace has no exact accepted artifact"
                        )
                    self._assert_event_authority(event, seal.execution_authority)
                    promotion = self._artifacts.inspect_governed_promotion(
                        stage_ref=str(seal.stage_ref),
                        seal_digest=str(seal.seal_digest),
                    )
                    if str(promotion.get("accepted_ref") or "") != str(
                        event.terminal_ref
                    ):
                        raise InternalArtifactTerminalReconciliationError(
                            "Workspace promotion and accepted artifact differ"
                        )
                else:
                    raise InternalArtifactTerminalReconciliationError(
                        "non-terminal lifecycle entered terminal inventory"
                    )
                cleanup = self._workspaces.cleanup_terminal_workspace(
                    workspace_id=workspace_id,
                    expected_state=lifecycle.state,
                    expected_terminal_ref=str(event.terminal_ref),
                )
                reports.append(
                    InternalArtifactTerminalReport(
                        workspace_id=workspace_id,
                        attempt_id=attempt_id,
                        action="CLEANED",
                        receipt_ref=str(cleanup.receipt_ref),
                    )
                )
            except Exception as exc:
                reports.append(
                    InternalArtifactTerminalReport(
                        workspace_id=workspace_id,
                        attempt_id=attempt_id,
                        action="BLOCKED",
                        reason_code=str(
                            getattr(exc, "reason_code", INTERNAL_ARTIFACT_TERMINAL_INVALID)
                        ),
                    )
                )
        return tuple(report.to_dict() for report in reports)

    def _command_id_for_event(self, event: Any) -> str:
        record = self._commands.get_by_graph(
            graph_id=str(event.graph_id),
            graph_revision=int(event.graph_revision),
        )
        if not isinstance(record, Mapping):
            raise InternalArtifactTerminalReconciliationError(
                "Workspace terminal event has no HostCommand admission"
            )
        command = record.get("command")
        graph = record.get("graph")
        command_id = str(getattr(command, "command_id", "") or "")
        surface = getattr(getattr(command, "surface", None), "value", None)
        nodes = tuple(getattr(graph, "nodes", ()) or ())
        matching_nodes = [
            node
            for node in nodes
            if str(getattr(node, "node_id", "") or "") == str(event.node_id)
        ]
        if (
            not command_id
            or surface != "internal"
            or str(getattr(command, "graph_id", "") or "")
            != str(event.graph_id)
            or int(getattr(command, "graph_revision", 0) or 0)
            != int(event.graph_revision)
            or len(nodes) != 1
            or len(matching_nodes) != 1
            or str(getattr(matching_nodes[0], "route", "") or "")
            != _INTERNAL_ROUTE
        ):
            raise InternalArtifactTerminalReconciliationError(
                "Workspace terminal event Internal admission is missing or drifted"
            )
        return command_id

    def _lease_owner_for_event(self, event: Any) -> str:
        lease = self._tasks.get_lease(str(event.task_lease_id))
        owner = str(lease.get("owner_id") or "")
        if (
            not owner
            or str(lease.get("attempt_id") or "") != str(event.attempt_id)
            or int(lease.get("fencing_token") or 0)
            != int(event.task_lease_fencing_token)
        ):
            raise InternalArtifactTerminalReconciliationError(
                "Workspace terminal event historical Lease drifted"
            )
        return owner

    def _complete_budget_rejection_claim(self, seal: Any) -> None:
        """Settle an exact C-boundary claim before deleting its seal.

        The terminal worker may be the first process to observe this state
        after a crash.  It therefore repeats the durable terminal resolver
        rather than trusting a filename, prior worker output, or a stale
        in-memory completion result.
        """

        claims = self._claim_results
        if claims is None:
            return
        authority = dict(getattr(seal, "execution_authority", {}) or {})
        attempt_id = str(authority.get("attempt_id") or "").strip()
        graph_id = str(authority.get("graph_id") or "").strip()
        node_id = str(authority.get("node_id") or "").strip()
        revision = authority.get("graph_revision")
        if (
            not attempt_id
            or not graph_id
            or not node_id
            or type(revision) is not int
            or revision < 1
        ):
            raise InternalArtifactTerminalReconciliationError(
                "budget rejected artifact has invalid execution authority"
            )
        terminal = self._tasks.resolve_internal_agent_budget_rejection_terminal(
            attempt_id
        )
        attempt = terminal.get("attempt") if isinstance(terminal, Mapping) else None
        node = terminal.get("node") if isinstance(terminal, Mapping) else None
        graph = terminal.get("graph") if isinstance(terminal, Mapping) else None
        reservation = (
            terminal.get("reservation") if isinstance(terminal, Mapping) else None
        )
        budget = terminal.get("budget") if isinstance(terminal, Mapping) else None
        usage = terminal.get("usage") if isinstance(terminal, Mapping) else None
        if not all(
            isinstance(value, Mapping)
            for value in (attempt, node, graph, reservation, budget, usage)
        ):
            raise InternalArtifactTerminalReconciliationError(
                "budget rejected terminal authority is incomplete"
            )
        if (
            str(terminal.get("authority_kind") or "")
            != "INTERNAL_AGENT_BUDGET_REJECTED"
            or str(attempt.get("attempt_id") or "") != attempt_id
            or str(attempt.get("graph_id") or "") != graph_id
            or attempt.get("graph_revision") != revision
            or str(attempt.get("node_id") or "") != node_id
            or str(node.get("node_id") or "") != node_id
            or str(graph.get("graph_id") or "") != graph_id
            or graph.get("revision") != revision
        ):
            raise InternalArtifactTerminalReconciliationError(
                "budget rejected terminal lineage drifted"
            )
        record = self._commands.get_by_graph(
            graph_id=graph_id,
            graph_revision=revision,
        )
        frozen = {**authority, "route": "delegate", "target_id": "agent_runtime"}
        self._assert_internal_admission(record, frozen=frozen)
        command = record.get("command") if isinstance(record, Mapping) else None
        claim_ref = str(getattr(command, "ingress_claim_ref", "") or "").strip()
        if not claim_ref:
            return
        try:
            result_ref = build_internal_budget_rejection_result_ref(
                command_id=str(getattr(command, "command_id", "") or ""),
                ingress_claim_ref=claim_ref,
                graph_id=graph_id,
                graph_revision=revision,
                node_id=node_id,
                attempt_id=attempt_id,
                reservation_id=str(reservation.get("reservation_id") or ""),
                budget_id=str(budget.get("budget_id") or ""),
                usage_id=str(usage.get("usage_id") or ""),
            )
        except ValueError as exc:
            raise InternalArtifactTerminalReconciliationError(
                "budget rejected claim result identity is invalid"
            ) from exc
        claims.complete_claim(claim_ref=claim_ref, result_ref=result_ref)

    def _complete_nonacceptance_claim(
        self,
        seal: Any,
        *,
        terminal_reason: str,
    ) -> None:
        """Settle one sealed, executed-but-not-accepted checkpoint claim.

        This runs before tombstoning the protected artifact.  It intentionally
        excludes raw worker failures and pre-checkpoint cancellations: neither
        owns the committed projection lineage required to make a terminal
        ingress result independently re-openable.
        """

        claims = self._claim_results
        if claims is None:
            return
        if terminal_reason not in INTERNAL_NONACCEPTANCE_TERMINAL_REASONS:
            raise InternalArtifactTerminalReconciliationError(
                "unsupported non-acceptance terminal reason"
            )
        authority = dict(getattr(seal, "execution_authority", {}) or {})
        attempt_id = str(authority.get("attempt_id") or "").strip()
        graph_id = str(authority.get("graph_id") or "").strip()
        node_id = str(authority.get("node_id") or "").strip()
        lease_id = str(authority.get("lease_id") or "").strip()
        lease_owner_id = str(authority.get("lease_owner_id") or "").strip()
        fencing_token = authority.get("fencing_token")
        revision = authority.get("graph_revision")
        if (
            not all((attempt_id, graph_id, node_id, lease_id, lease_owner_id))
            or type(revision) is not int
            or revision < 1
            or type(fencing_token) is not int
            or fencing_token < 1
        ):
            raise InternalArtifactTerminalReconciliationError(
                "non-acceptance artifact has invalid execution authority"
            )
        terminal = self._tasks.resolve_internal_agent_nonacceptance_checkpoint_terminal(
            attempt_id
        )
        if not isinstance(terminal, Mapping):
            raise InternalArtifactTerminalReconciliationError(
                "non-acceptance terminal authority is incomplete"
            )
        historical = terminal.get("historical_lease")
        completion = terminal.get("completion_lease")
        reservation = terminal.get("operation_reservation")
        projection = terminal.get("projection")
        if not all(
            isinstance(value, Mapping)
            for value in (historical, completion, reservation, projection)
        ):
            raise InternalArtifactTerminalReconciliationError(
                "non-acceptance terminal lineage is incomplete"
            )
        if (
            str(terminal.get("authority_kind") or "")
            != "INTERNAL_AGENT_NONACCEPTANCE_TERMINAL"
            or str(terminal.get("terminal_reason") or "") != terminal_reason
            or str(historical.get("lease_id") or "") != lease_id
            or str(historical.get("attempt_id") or "") != attempt_id
            or str(historical.get("owner_id") or "") != lease_owner_id
            or int(historical.get("fencing_token") or 0) != fencing_token
            or str(completion.get("attempt_id") or "") != attempt_id
            or str(completion.get("status") or "") != "RELEASED"
            or str(reservation.get("attempt_id") or "") != attempt_id
            or str(reservation.get("lease_id") or "") != lease_id
            or str(projection.get("command_id") or "")
            != str(authority.get("command_id") or "")
            or str(projection.get("graph_id") or "") != graph_id
            or int(projection.get("graph_revision") or 0) != revision
            or str(projection.get("node_id") or "") != node_id
            or str(projection.get("attempt_id") or "") != attempt_id
        ):
            raise InternalArtifactTerminalReconciliationError(
                "non-acceptance terminal lineage drifted"
            )
        record = self._commands.get_by_graph(
            graph_id=graph_id,
            graph_revision=revision,
        )
        frozen = {**authority, "route": "delegate", "target_id": "agent_runtime"}
        self._assert_internal_admission(record, frozen=frozen)
        command = record.get("command") if isinstance(record, Mapping) else None
        claim_ref = str(getattr(command, "ingress_claim_ref", "") or "").strip()
        if not claim_ref:
            return
        try:
            result_ref = build_internal_nonacceptance_result_ref(
                command_id=str(getattr(command, "command_id", "") or ""),
                ingress_claim_ref=claim_ref,
                graph_id=graph_id,
                graph_revision=revision,
                node_id=node_id,
                attempt_id=attempt_id,
                historical_lease_id=lease_id,
                completion_lease_id=str(completion.get("lease_id") or ""),
                reservation_id=str(reservation.get("reservation_id") or ""),
                projection_id=str(projection.get("projection_id") or ""),
                terminal_reason=terminal_reason,
            )
        except ValueError as exc:
            raise InternalArtifactTerminalReconciliationError(
                "non-acceptance claim result identity is invalid"
            ) from exc
        claims.complete_claim(claim_ref=claim_ref, result_ref=result_ref)

    def _terminal_abandonment_reason(self, seal: Any) -> str | None:
        try:
            envelope = validate_projection_recovery_envelope(
                self._artifacts.read_projection_recovery(seal)
            )
        except ProjectionRecoveryContractError as exc:
            raise InternalArtifactTerminalReconciliationError(
                "sealed projection recovery envelope is missing or drifted"
            ) from exc
        frozen = dict(envelope["frozen_node_input"])
        self._assert_frozen_authority(seal, frozen)
        graph_id = str(frozen["graph_id"])
        revision = int(frozen["graph_revision"])
        node_id = str(frozen["node_id"])
        attempt_id = str(frozen["attempt_id"])
        lease_id = str(frozen["lease_id"])
        command = self._commands.get_by_graph(
            graph_id=graph_id,
            graph_revision=revision,
        )
        self._assert_internal_admission(command, frozen=frozen)
        graph = self._tasks.get_graph(graph_id, revision)
        node = self._tasks.get_node(graph_id, revision, node_id)
        attempt = self._tasks.get_attempt(attempt_id)
        lease = self._tasks.get_lease(lease_id)
        reservation = self._tasks.get_operation_reservation(
            str(frozen["reservation_id"])
        )
        self._assert_execution_records(
            frozen=frozen,
            graph=graph,
            node=node,
            attempt=attempt,
            lease=lease,
            reservation=reservation,
        )
        graph_status = str(graph.get("status") or "")
        node_status = str(node.get("status") or "")
        attempt_status = str(attempt.get("status") or "")
        lease_status = str(lease.get("status") or "")
        if (
            graph_status == "BLOCKED"
            and node_status == "FAILED"
            and attempt_status == "FAILED"
            and lease_status in {"RELEASED", "EXPIRED"}
        ):
            terminal = self._tasks.resolve_internal_agent_budget_rejection_terminal(
                attempt_id
            )
            if (
                not isinstance(terminal, Mapping)
                or terminal.get("authority_kind")
                != "INTERNAL_AGENT_BUDGET_REJECTED"
                or str(terminal.get("historical_lease_id") or "") != lease_id
            ):
                raise InternalArtifactTerminalReconciliationError(
                    "blocked Internal Agent artifact lacks exact budget rejection authority"
                )
            self._assert_committed_projection(frozen=frozen, seal=seal)
            return "TERMINAL_BUDGET_REJECTED"
        recovered_terminal_lease = False
        if (
            lease_status == "EXPIRED"
            and graph_status in {"FAILED", "CANCELLED", "BLOCKED"}
        ):
            self._tasks.resolve_internal_agent_takeover_lineage(
                graph_id=graph_id,
                graph_revision=revision,
                node_id=node_id,
                attempt_id=attempt_id,
                historical_lease_id=lease_id,
                historical_lease_owner=str(frozen["lease_owner_id"]),
                historical_fencing_token=int(frozen["fencing_token"]),
                expected_graph_status=graph_status,
                expected_node_status=node_status,
                expected_attempt_status=attempt_status,
            )
            recovered_terminal_lease = True
        released_execution = lease_status == "RELEASED" or recovered_terminal_lease
        terminal: str | None = None
        if (
            graph_status == "FAILED"
            and node_status == "VERIFICATION_FAILED"
            and attempt_status == "SUCCEEDED"
            and released_execution
        ):
            terminal = "TERMINAL_VERIFICATION_FAILED"
        elif (
            graph_status == "FAILED"
            and node_status == "FAILED"
            and attempt_status == "SUCCEEDED"
            and released_execution
        ):
            terminal = "TERMINAL_REVIEW_REJECTED"
        elif (
            graph_status == "FAILED"
            and node_status == "FAILED"
            and attempt_status == "FAILED"
            and released_execution
        ):
            terminal = "TERMINAL_WORKER_FAILED"
        elif (
            graph_status == "CANCELLED"
            and node_status == "CANCELLED"
            and attempt_status == "CANCELLED"
            and lease_status == "CANCELLED"
        ):
            terminal = "TERMINAL_EXECUTION_CANCELLED"
        elif (
            graph_status == "CANCELLED"
            and node_status == "CANCELLED"
            and attempt_status == "SUCCEEDED"
            and released_execution
        ):
            terminal = "TERMINAL_POST_REVIEW_CANCELLED"
        elif graph_status in {"FAILED", "CANCELLED"}:
            raise InternalArtifactTerminalReconciliationError(
                "terminal Task state does not match an abandonment matrix"
            )
        else:
            return None

        operation_status = str(reservation.get("status") or "")
        committed_required = attempt_status == "SUCCEEDED"
        if operation_status == "COMMITTED":
            self._assert_committed_projection(frozen=frozen, seal=seal)
        elif committed_required or operation_status not in {
            "RESERVED",
            "RECOVERY_REQUIRED",
            "RELEASED",
            "CANCELLED",
        }:
            raise InternalArtifactTerminalReconciliationError(
                "terminal Task has no exact Operation checkpoint"
            )
        return terminal

    def _unsealed_cancellation_reason(self, stage: Any) -> str | None:
        """Allow destructive cleanup only after finalized graph cancellation.

        An unsealed directory has no projection envelope, so it is never
        eligible for accepted promotion or failure abandonment.  The only
        safe recovery in this slice is the exact cancellation matrix, backed
        by the canonical finalized cancellation decision.
        """

        authority = dict(getattr(stage, "execution_authority", {}) or {})
        frozen = {
            **authority,
            "route": "delegate",
            "target_id": "agent_runtime",
        }
        required = {
            "root_work_order_id",
            "graph_id",
            "graph_revision",
            "node_id",
            "attempt_id",
            "lease_id",
            "command_id",
            "principal_id",
            "lease_owner_id",
            "fencing_token",
        }
        if set(authority) != required:
            raise InternalArtifactTerminalReconciliationError(
                "unsealed artifact execution authority differs"
            )
        graph_id = str(authority["graph_id"])
        revision = int(authority["graph_revision"])
        node_id = str(authority["node_id"])
        attempt_id = str(authority["attempt_id"])
        lease_id = str(authority["lease_id"])
        command = self._commands.get_by_graph(
            graph_id=graph_id,
            graph_revision=revision,
        )
        self._assert_internal_admission(command, frozen=frozen)
        graph = self._tasks.get_graph(graph_id, revision)
        node = self._tasks.get_node(graph_id, revision, node_id)
        attempt = self._tasks.get_attempt(attempt_id)
        lease = self._tasks.get_lease(lease_id)
        self._assert_unsealed_execution_records(
            authority=authority,
            graph=graph,
            node=node,
            attempt=attempt,
            lease=lease,
        )
        if (
            str(graph.get("status") or "") != "CANCELLED"
            or str(node.get("status") or "") != "CANCELLED"
            or str(attempt.get("status") or "") != "CANCELLED"
            or str(lease.get("status") or "") != "CANCELLED"
        ):
            return None
        cancellation = self._tasks.get_graph_cancellation_for_graph(
            graph_id,
            revision,
        )
        payload = cancellation.get("payload") if isinstance(cancellation, Mapping) else None
        if (
            not isinstance(cancellation, Mapping)
            or str(cancellation.get("status") or "") != "FINALIZED"
            or str(cancellation.get("graph_id") or "") != graph_id
            or int(cancellation.get("graph_revision") or 0) != revision
            or not isinstance(payload, Mapping)
            or str(payload.get("graph_target_status") or "") != "CANCELLED"
        ):
            raise InternalArtifactTerminalReconciliationError(
                "cancelled unsealed artifact has no finalized cancellation authority"
            )
        return "TERMINAL_EXECUTION_CANCELLED_UNSEALED"

    @staticmethod
    def _assert_frozen_authority(seal: Any, frozen: Mapping[str, Any]) -> None:
        required = {
            "root_work_order_id",
            "graph_id",
            "graph_revision",
            "node_id",
            "attempt_id",
            "lease_id",
            "command_id",
            "principal_id",
            "lease_owner_id",
            "fencing_token",
            "route",
            "target_id",
            "reservation_id",
            "business_scope",
            "operation_fingerprint",
        }
        if not required.issubset(frozen):
            raise InternalArtifactTerminalReconciliationError(
                "frozen node input lacks terminal authority fields"
            )
        authority = {
            key: frozen[key]
            for key in (
                "root_work_order_id",
                "graph_id",
                "graph_revision",
                "node_id",
                "attempt_id",
                "lease_id",
                "command_id",
                "principal_id",
                "lease_owner_id",
                "fencing_token",
            )
        }
        if (
            authority != dict(seal.execution_authority)
            or frozen.get("route") != "delegate"
            or frozen.get("target_id") != "agent_runtime"
        ):
            raise InternalArtifactTerminalReconciliationError(
                "sealed artifact and frozen execution authority differ"
            )

    @staticmethod
    def _assert_internal_admission(
        record: Any,
        *,
        frozen: Mapping[str, Any],
    ) -> None:
        if not isinstance(record, Mapping):
            raise InternalArtifactTerminalReconciliationError(
                "sealed artifact has no durable HostCommand admission"
            )
        command = record.get("command")
        graph = record.get("graph")
        surface = getattr(getattr(command, "surface", None), "value", None)
        nodes = tuple(getattr(graph, "nodes", ()) or ())
        selected = [node for node in nodes if getattr(node, "node_id", None) == frozen["node_id"]]
        if (
            surface != "internal"
            or getattr(command, "command_id", None) != frozen["command_id"]
            or getattr(command, "graph_id", None) != frozen["graph_id"]
            or getattr(command, "graph_revision", None) != frozen["graph_revision"]
            or len(nodes) != 1
            or len(selected) != 1
            or getattr(selected[0], "route", None) != _INTERNAL_ROUTE
        ):
            raise InternalArtifactTerminalReconciliationError(
                "sealed artifact is not owned by the Internal Agent route"
            )

    @staticmethod
    def _assert_execution_records(
        *,
        frozen: Mapping[str, Any],
        graph: Mapping[str, Any],
        node: Mapping[str, Any],
        attempt: Mapping[str, Any],
        lease: Mapping[str, Any],
        reservation: Mapping[str, Any],
    ) -> None:
        node_contract = node.get("node")
        if (
            str(graph.get("graph_id") or "") != str(frozen["graph_id"])
            or int(graph.get("revision") or 0) != int(frozen["graph_revision"])
            or str(node.get("node_id") or "") != str(frozen["node_id"])
            or not isinstance(node_contract, Mapping)
            or str(node_contract.get("route") or "") != _INTERNAL_ROUTE
            or str(attempt.get("attempt_id") or "") != str(frozen["attempt_id"])
            or str(attempt.get("graph_id") or "") != str(frozen["graph_id"])
            or int(attempt.get("graph_revision") or 0) != int(frozen["graph_revision"])
            or str(attempt.get("node_id") or "") != str(frozen["node_id"])
            or str(lease.get("lease_id") or "") != str(frozen["lease_id"])
            or str(lease.get("attempt_id") or "") != str(frozen["attempt_id"])
            or str(lease.get("owner_id") or "") != str(frozen["lease_owner_id"])
            or int(lease.get("fencing_token") or 0) != int(frozen["fencing_token"])
            or str(reservation.get("reservation_id") or "")
            != str(frozen["reservation_id"])
            or str(reservation.get("business_scope") or "")
            != str(frozen["business_scope"])
            or str(reservation.get("operation_fingerprint") or "")
            != str(frozen["operation_fingerprint"])
            or str(reservation.get("attempt_id") or "") != str(frozen["attempt_id"])
            or str(reservation.get("lease_id") or "") != str(frozen["lease_id"])
            or str(reservation.get("owner_id") or "") != str(frozen["lease_owner_id"])
            or int(reservation.get("fencing_token") or 0)
            != int(frozen["fencing_token"])
        ):
            raise InternalArtifactTerminalReconciliationError(
                "sealed artifact Task/Operation lineage drifted"
            )

    @staticmethod
    def _assert_unsealed_execution_records(
        *,
        authority: Mapping[str, Any],
        graph: Mapping[str, Any],
        node: Mapping[str, Any],
        attempt: Mapping[str, Any],
        lease: Mapping[str, Any],
    ) -> None:
        node_contract = node.get("node")
        if (
            str(graph.get("graph_id") or "") != str(authority["graph_id"])
            or int(graph.get("revision") or 0) != int(authority["graph_revision"])
            or str(graph.get("root_work_order_id") or "")
            != str(authority["root_work_order_id"])
            or str(node.get("node_id") or "") != str(authority["node_id"])
            or not isinstance(node_contract, Mapping)
            or str(node_contract.get("route") or "") != _INTERNAL_ROUTE
            or str(attempt.get("attempt_id") or "") != str(authority["attempt_id"])
            or str(attempt.get("graph_id") or "") != str(authority["graph_id"])
            or int(attempt.get("graph_revision") or 0)
            != int(authority["graph_revision"])
            or str(attempt.get("node_id") or "") != str(authority["node_id"])
            or str(lease.get("lease_id") or "") != str(authority["lease_id"])
            or str(lease.get("attempt_id") or "") != str(authority["attempt_id"])
            or str(lease.get("owner_id") or "")
            != str(authority["lease_owner_id"])
            or int(lease.get("fencing_token") or 0)
            != int(authority["fencing_token"])
        ):
            raise InternalArtifactTerminalReconciliationError(
                "unsealed artifact Task lineage drifted"
            )

    def _assert_committed_projection(
        self,
        *,
        frozen: Mapping[str, Any],
        seal: Any,
    ) -> None:
        checkpoint = self._tasks.get_committed_operation_checkpoint(
            str(frozen["reservation_id"])
        )
        if not isinstance(checkpoint, Mapping) or not isinstance(
            checkpoint.get("reservation"), Mapping
        ):
            raise InternalArtifactTerminalReconciliationError(
                "committed Operation checkpoint is incomplete"
            )
        reservation = checkpoint["reservation"]
        projection_ref = str(reservation.get("receipt_ref") or "")
        projection = self._projections.load(projection_ref)
        intents = (
            list(projection.get("acceptance_effect_intents") or [])
            if isinstance(projection, Mapping)
            else []
        )
        intent = intents[0] if len(intents) == 1 and isinstance(intents[0], Mapping) else {}
        payload = intent.get("payload") if isinstance(intent, Mapping) else None
        result = payload.get("result") if isinstance(payload, Mapping) else None
        protected = result.get("protected_artifact") if isinstance(result, Mapping) else None
        if (
            not isinstance(projection, Mapping)
            or str(projection.get("projection_id") or "") != projection_ref
            or str(projection.get("command_id") or "") != str(frozen["command_id"])
            or str(projection.get("graph_id") or "") != str(frozen["graph_id"])
            or int(projection.get("graph_revision") or 0)
            != int(frozen["graph_revision"])
            or str(projection.get("node_id") or "") != str(frozen["node_id"])
            or str(projection.get("attempt_id") or "") != str(frozen["attempt_id"])
            or str(projection.get("route_type") or "") != "delegate"
            or str(projection.get("target_id") or "") != "agent_runtime"
            or intent.get("effect_type") != "internal.agent.result_commit"
            or not isinstance(protected, Mapping)
            or dict(protected) != seal.protected_payload()
        ):
            raise InternalArtifactTerminalReconciliationError(
                "committed Operation projection identity drifted"
            )

    @staticmethod
    def _assert_event_authority(event: Any, authority: Mapping[str, Any]) -> None:
        if (
            str(event.graph_id) != str(authority.get("graph_id") or "")
            or int(event.graph_revision) != int(authority.get("graph_revision") or 0)
            or str(event.node_id) != str(authority.get("node_id") or "")
            or str(event.attempt_id) != str(authority.get("attempt_id") or "")
            or str(event.task_lease_id) != str(authority.get("lease_id") or "")
            or int(event.task_lease_fencing_token)
            != int(authority.get("fencing_token") or 0)
        ):
            raise InternalArtifactTerminalReconciliationError(
                "Workspace lifecycle and accepted artifact authority differ"
            )


class InternalArtifactTerminalContinuationWorker:
    """Lifecycle-owned bounded poller for the terminal reconciler."""

    def __init__(
        self,
        *,
        reconciler: InternalArtifactTerminalReconciler,
        interval_ms: int = 5_000,
        batch_limit: int = 100,
    ) -> None:
        if int(interval_ms) < 100:
            raise ValueError("terminal continuation interval must be at least 100ms")
        if int(batch_limit) < 1 or int(batch_limit) > 1_000:
            raise ValueError("terminal continuation limit must be within [1, 1000]")
        self._reconciler = reconciler
        self._interval_s = int(interval_ms) / 1_000.0
        self._batch_limit = int(batch_limit)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._last_report: tuple[dict[str, Any], ...] = ()
        self._last_failure: dict[str, Any] | None = None

    def run_once(self) -> tuple[dict[str, Any], ...]:
        report = self._reconciler.run_once(limit=self._batch_limit)
        with self._lock:
            self._last_report = tuple(dict(item) for item in report)
            blocked = [item for item in report if item.get("action") == "BLOCKED"]
            self._last_failure = (
                {
                    "reason_code": "INTERNAL_ARTIFACT_TERMINAL_BLOCKED",
                    "count": len(blocked),
                }
                if blocked
                else None
            )
        return report

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._wake.clear()

            def loop() -> None:
                while not self._stop.is_set():
                    self._wake.wait(self._interval_s)
                    self._wake.clear()
                    if self._stop.is_set():
                        break
                    try:
                        self.run_once()
                    except Exception as exc:  # retained for health; next poll retries
                        with self._lock:
                            self._last_failure = {
                                "reason_code": str(
                                    getattr(
                                        exc,
                                        "reason_code",
                                        INTERNAL_ARTIFACT_TERMINAL_INVALID,
                                    )
                                ),
                                "error_type": type(exc).__name__,
                                "ts_ms": time.time_ns() // 1_000_000,
                            }

            self._thread = threading.Thread(
                target=loop,
                name="internal-artifact-terminal-continuation",
                daemon=True,
            )
            self._thread.start()

    def wake(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def join(self, *, timeout_s: float = 10.0) -> None:
        with self._lock:
            thread = self._thread
        if thread is None:
            return
        thread.join(max(0.0, float(timeout_s)))
        if thread.is_alive():
            raise RuntimeError("INTERNAL_ARTIFACT_TERMINAL_WORKER_STOP_TIMEOUT")
        with self._lock:
            if self._thread is thread:
                self._thread = None

    def health_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": bool(self._thread is not None and self._thread.is_alive()),
                "last_report": tuple(dict(item) for item in self._last_report),
                "last_failure": (
                    dict(self._last_failure) if self._last_failure is not None else None
                ),
            }


__all__ = [
    "INTERNAL_ARTIFACT_TERMINAL_INVALID",
    "InternalArtifactTerminalContinuationWorker",
    "InternalArtifactTerminalReconciler",
    "InternalArtifactTerminalReconciliationError",
    "InternalArtifactTerminalReport",
]
