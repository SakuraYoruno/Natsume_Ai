"""Terminal continuation for governed write-capable Agent Skills.

The Agent executor owns a Workspace and a protected artifact stage while a
TaskNode is running.  Promotion is already post-Acceptance-only.  This module
closes the opposite side of that lifecycle: it abandons or cleans an exact
stage only after reopening the canonical Task terminal and its immutable
decision evidence.  It never executes an Agent and never invents a terminal
from filenames or an in-memory result.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from host_brain.agent_runtime.governed_skill_work_order import (
    WRITE_AGENT_RUNTIME_SKILL_IDS,
)
from host_brain.agent_runtime.workspace_artifact_effect import (
    AGENT_WORKSPACE_ARTIFACT_EFFECT_TYPE,
    canonical_workspace_artifact_digest,
)
from host_brain.contracts.agent_runtime.work_order import (
    WorkOrderV3,
    canonicalize_work_order,
)
from host_brain.persistence.worker_projection_repository import (
    validate_worker_projection_payload,
)


SKILL_ARTIFACT_TERMINAL_INVALID = "SKILL_ARTIFACT_TERMINAL_INVALID"


class SkillArtifactTerminalReconciliationError(RuntimeError):
    def __init__(self, detail: str) -> None:
        self.reason_code = SKILL_ARTIFACT_TERMINAL_INVALID
        self.detail = str(detail or "Agent Skill artifact terminal state is invalid")
        super().__init__(f"{self.reason_code}: {self.detail}")


@dataclass(frozen=True, slots=True)
class _SkillArtifactBinding:
    command: Any
    graph: Any
    node: Any
    sealed_order: WorkOrderV3
    authority: dict[str, Any]
    target_id: str
    reservation_id: str
    workspace_id: str
    attempt_id: str


@dataclass(frozen=True, slots=True)
class SkillArtifactTerminalReport:
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


def direct_write_skill_command_id(
    authority: Mapping[str, Any],
    command_repository: Any,
) -> str | None:
    """Return the exact direct-Skill command owner, if this route owns it."""

    graph_id = str(authority.get("graph_id") or "")
    node_id = str(authority.get("node_id") or "")
    try:
        graph_revision = int(authority.get("graph_revision"))
    except (TypeError, ValueError):
        return None
    if not graph_id or not node_id or graph_revision < 1:
        return None
    record = command_repository.get_by_graph(
        graph_id=graph_id,
        graph_revision=graph_revision,
    )
    if not isinstance(record, Mapping):
        return None
    command = record.get("command")
    graph = record.get("graph")
    target_id = str(getattr(command, "target_id", "") or "")
    authority_command_id = str(authority.get("command_id") or "")
    matching = [
        node
        for node in tuple(getattr(graph, "nodes", ()) or ())
        if str(getattr(node, "node_id", "") or "") == node_id
    ]
    exact = (
        getattr(getattr(command, "surface", None), "value", None)
        != "internal"
        and str(getattr(command, "route_type", "") or "") == "skill"
        and target_id in WRITE_AGENT_RUNTIME_SKILL_IDS
        and str(getattr(command, "graph_id", "") or "") == graph_id
        and int(getattr(command, "graph_revision", 0) or 0)
        == graph_revision
        and (
            not authority_command_id
            or str(getattr(command, "command_id", "") or "")
            == authority_command_id
        )
        and len(matching) == 1
        and str(getattr(matching[0], "route", "") or "")
        == f"skill:{target_id}"
    )
    command_id = str(getattr(command, "command_id", "") or "")
    return command_id if exact and command_id else None


def direct_write_skill_event_command_id(
    event: Any,
    command_repository: Any,
) -> str | None:
    """Resolve a terminal lifecycle event to its direct-Skill command."""

    return direct_write_skill_command_id(
        {
            "graph_id": event.graph_id,
            "graph_revision": event.graph_revision,
            "node_id": event.node_id,
        },
        command_repository,
    )


def is_direct_write_skill_artifact(
    artifact: Any,
    command_repository: Any,
) -> bool:
    """Identify the exact direct-Skill owner without trusting filenames."""

    authority = dict(getattr(artifact, "execution_authority", {}) or {})
    return direct_write_skill_command_id(
        authority,
        command_repository,
    ) is not None


class GovernedSkillArtifactTerminalReconciler:
    """Abandon exact direct-Skill stages after canonical Task settlement."""

    def __init__(
        self,
        *,
        artifact_store: Any,
        workspace_runtime: Any,
        task_store: Any,
        command_repository: Any,
        projection_store: Any,
    ) -> None:
        required = (
            (artifact_store, "inventory_governed_seals"),
            (artifact_store, "read_sealed_json"),
            (artifact_store, "plan_governed_abandonment"),
            (artifact_store, "abandon_governed_stage"),
            (workspace_runtime, "abandon_terminal_artifact"),
            (task_store, "get_graph"),
            (task_store, "get_node"),
            (task_store, "get_attempt"),
            (task_store, "get_lease"),
            (task_store, "get_operation_reservation"),
            (task_store, "get_committed_operation_checkpoint"),
            (task_store, "get_node_verification_decision"),
            (command_repository, "get_by_graph"),
            (projection_store, "load"),
        )
        missing = [
            name for owner, name in required if not callable(getattr(owner, name, None))
        ]
        if missing:
            raise TypeError(
                "Agent Skill artifact terminal reconciler lacks: "
                + ",".join(sorted(set(missing)))
            )
        self._artifacts = artifact_store
        self._workspaces = workspace_runtime
        self._tasks = task_store
        self._commands = command_repository
        self._projections = projection_store
        self._staging_cursor: str | None = None

    def run_once(self, *, limit: int = 100) -> tuple[dict[str, Any], ...]:
        if isinstance(limit, bool) or type(limit) is not int or not 1 <= limit <= 1_000:
            raise SkillArtifactTerminalReconciliationError(
                "terminal reconciliation limit must be within [1, 1000]"
            )
        reports: list[SkillArtifactTerminalReport] = []
        seals = self._artifacts.inventory_governed_seals(
            namespace="staging",
            limit=limit,
            after_stage_ref=self._staging_cursor,
        )
        self._staging_cursor = (
            str(seals[-1].stage_ref) if len(seals) == limit else None
        )
        for seal in seals:
            try:
                raw_order = self._artifacts.read_sealed_json(
                    seal,
                    "work_order.json",
                )
                binding = self._skill_binding(
                    artifact=seal,
                    raw_order=raw_order,
                )
                if binding is None:
                    continue
                reason = self._sealed_terminal_reason(binding, seal=seal)
                if reason is None:
                    reports.append(
                        SkillArtifactTerminalReport(
                            workspace_id=binding.workspace_id,
                            attempt_id=binding.attempt_id,
                            action="HOLD",
                        )
                    )
                    continue
                receipt = self._workspaces.abandon_terminal_artifact(
                    workspace_id=binding.workspace_id,
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
                    SkillArtifactTerminalReport(
                        workspace_id=binding.workspace_id,
                        attempt_id=binding.attempt_id,
                        action="ABANDONED",
                        reason_code=reason,
                        receipt_ref=str(receipt.tombstone_ref),
                    )
                )
            except Exception as exc:
                reports.append(
                    SkillArtifactTerminalReport(
                        workspace_id=str(getattr(seal, "workspace_id", "unknown")),
                        attempt_id=str(getattr(seal, "attempt_id", "unknown")),
                        action="BLOCKED",
                        reason_code=str(
                            getattr(
                                exc,
                                "reason_code",
                                SKILL_ARTIFACT_TERMINAL_INVALID,
                            )
                        ),
                    )
                )
        return tuple(report.to_dict() for report in reports)

    def _skill_binding(
        self,
        *,
        artifact: Any,
        raw_order: Mapping[str, Any],
    ) -> _SkillArtifactBinding | None:
        authority = dict(getattr(artifact, "execution_authority", {}) or {})
        required_authority = {
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
        if set(authority) != required_authority:
            self._invalid("Agent Skill artifact execution authority differs")
        record = self._commands.get_by_graph(
            graph_id=str(authority["graph_id"]),
            graph_revision=int(authority["graph_revision"]),
        )
        if not isinstance(record, Mapping):
            self._invalid("Agent Skill artifact has no HostCommand admission")
        command = record.get("command")
        graph = record.get("graph")
        route_type = str(getattr(command, "route_type", "") or "")
        target_id = str(getattr(command, "target_id", "") or "")
        surface = getattr(getattr(command, "surface", None), "value", None)
        if (
            route_type != "skill"
            or target_id not in WRITE_AGENT_RUNTIME_SKILL_IDS
            or surface == "internal"
        ):
            return None
        nodes = tuple(getattr(graph, "nodes", ()) or ())
        selected = [
            node
            for node in nodes
            if str(getattr(node, "node_id", "") or "")
            == str(authority["node_id"])
        ]
        expected = (
            (getattr(command, "command_id", None), authority["command_id"]),
            (getattr(command, "root_work_order_id", None), authority["root_work_order_id"]),
            (getattr(command, "graph_id", None), authority["graph_id"]),
            (getattr(command, "graph_revision", None), authority["graph_revision"]),
            (getattr(command, "principal_id", None), authority["principal_id"]),
        )
        if (
            len(selected) != 1
            or str(getattr(selected[0], "route", "") or "")
            != f"skill:{target_id}"
            or any(actual != wanted for actual, wanted in expected)
        ):
            self._invalid("Agent Skill artifact admission identity drifted")
        try:
            sealed_order = canonicalize_work_order(raw_order)
        except Exception as exc:
            raise SkillArtifactTerminalReconciliationError(
                "Agent Skill artifact WorkOrder is invalid"
            ) from exc
        metadata = dict(sealed_order.metadata or {})
        workspace = dict(sealed_order.workspace or {})
        reservation_id = str(metadata.get("reservation_id") or "").strip()
        workspace_id = str(getattr(artifact, "workspace_id", "") or "")
        order_exact = (
            sealed_order.work_order_id == authority["root_work_order_id"]
            and sealed_order.root_work_order_id == authority["root_work_order_id"]
            and sealed_order.task_graph_id == authority["graph_id"]
            and sealed_order.graph_revision == authority["graph_revision"]
            and sealed_order.task_node_id == authority["node_id"]
            and metadata.get("attempt_id") == authority["attempt_id"]
            and metadata.get("skill_id") == target_id
            and reservation_id
            and sealed_order.operation_fingerprint
            == getattr(command, "operation_fingerprint", None)
            and sealed_order.business_idempotency_scope
            == getattr(command, "business_scope", None)
            and sealed_order.requires_write
            and int(sealed_order.risk_level) >= 4
            and str(sealed_order.review_ref or "").startswith("review://")
            and workspace.get("strategy") == "durable_workspace"
            and workspace.get("workspace_id") == workspace_id
        )
        if not order_exact:
            self._invalid("Agent Skill artifact WorkOrder authority drifted")
        expected_workspace = self._workspaces.workspace_id_for(
            graph_id=str(authority["graph_id"]),
            graph_revision=int(authority["graph_revision"]),
        )
        if workspace_id != expected_workspace:
            self._invalid("Agent Skill artifact Workspace epoch drifted")
        operation = self._tasks.get_operation_reservation(reservation_id)
        if not isinstance(operation, Mapping):
            self._invalid("Agent Skill artifact Operation is missing")
        operation_exact = (
            str(operation.get("reservation_id") or "") == reservation_id
            and str(operation.get("attempt_id") or "") == authority["attempt_id"]
            and str(operation.get("lease_id") or "") == authority["lease_id"]
            and str(operation.get("owner_id") or "") == authority["lease_owner_id"]
            and int(operation.get("fencing_token") or 0)
            == int(authority["fencing_token"])
            and str(operation.get("business_scope") or "")
            == sealed_order.business_idempotency_scope
            and str(operation.get("operation_fingerprint") or "")
            == sealed_order.operation_fingerprint
        )
        if not operation_exact:
            self._invalid("Agent Skill artifact Operation authority drifted")
        return _SkillArtifactBinding(
            command=command,
            graph=graph,
            node=selected[0],
            sealed_order=sealed_order,
            authority=authority,
            target_id=target_id,
            reservation_id=reservation_id,
            workspace_id=workspace_id,
            attempt_id=str(authority["attempt_id"]),
        )

    def _sealed_terminal_reason(
        self,
        binding: _SkillArtifactBinding,
        *,
        seal: Any,
    ) -> str | None:
        graph, node, attempt, lease, operation = self._task_records(binding)
        graph_status = str(graph.get("status") or "")
        node_status = str(node.get("status") or "")
        attempt_status = str(attempt.get("status") or "")
        operation_status = str(operation.get("status") or "")
        terminal_graph = graph_status in {"FAILED", "CANCELLED", "BLOCKED"}
        terminal_node = node_status in {
            "FAILED",
            "VERIFICATION_FAILED",
            "CANCELLED",
        }
        if not terminal_graph and not terminal_node:
            return None
        if (
            graph_status != "FAILED"
            or node_status != "VERIFICATION_FAILED"
            or attempt_status != "SUCCEEDED"
            or operation_status != "COMMITTED"
        ):
            self._invalid(
                "unproven Agent Skill terminal matrix remains fail closed"
            )
        self._assert_committed_checkpoint(binding)
        self._assert_projection_descriptor(binding, seal=seal)
        decision = self._tasks.get_node_verification_decision(
            str(binding.authority["graph_id"]),
            int(binding.authority["graph_revision"]),
            str(binding.authority["node_id"]),
        )
        if (
            not isinstance(decision, Mapping)
            or str(decision.get("target_status") or "")
            != "VERIFICATION_FAILED"
            or str(decision.get("attempt_id") or "") != binding.attempt_id
        ):
            self._invalid("Agent Skill Verification terminal drifted")
        return "TERMINAL_VERIFICATION_FAILED"

    def _task_records(
        self,
        binding: _SkillArtifactBinding,
    ) -> tuple[
        Mapping[str, Any],
        Mapping[str, Any],
        Mapping[str, Any],
        Mapping[str, Any],
        Mapping[str, Any],
    ]:
        authority = binding.authority
        graph = self._tasks.get_graph(
            str(authority["graph_id"]),
            int(authority["graph_revision"]),
        )
        node = self._tasks.get_node(
            str(authority["graph_id"]),
            int(authority["graph_revision"]),
            str(authority["node_id"]),
        )
        attempt = self._tasks.get_attempt(binding.attempt_id)
        lease = self._tasks.get_lease(str(authority["lease_id"]))
        operation = self._tasks.get_operation_reservation(binding.reservation_id)
        if not all(
            isinstance(value, Mapping)
            for value in (graph, node, attempt, lease, operation)
        ):
            self._invalid("Agent Skill Task terminal records are incomplete")
        node_contract = node.get("node")
        exact = (
            str(graph.get("graph_id") or "") == str(authority["graph_id"])
            and int(graph.get("revision") or 0)
            == int(authority["graph_revision"])
            and str(graph.get("root_work_order_id") or "")
            == str(authority["root_work_order_id"])
            and str(node.get("node_id") or "") == str(authority["node_id"])
            and isinstance(node_contract, Mapping)
            and str(node_contract.get("route") or "")
            == f"skill:{binding.target_id}"
            and str(attempt.get("attempt_id") or "") == binding.attempt_id
            and str(attempt.get("graph_id") or "") == str(authority["graph_id"])
            and int(attempt.get("graph_revision") or 0)
            == int(authority["graph_revision"])
            and str(attempt.get("node_id") or "") == str(authority["node_id"])
            and str(lease.get("lease_id") or "") == str(authority["lease_id"])
            and str(lease.get("attempt_id") or "") == binding.attempt_id
            and str(lease.get("owner_id") or "")
            == str(authority["lease_owner_id"])
            and int(lease.get("fencing_token") or 0)
            == int(authority["fencing_token"])
            and str(operation.get("reservation_id") or "")
            == binding.reservation_id
        )
        if not exact:
            self._invalid("Agent Skill Task/Lease lineage drifted")
        return graph, node, attempt, lease, operation

    def _assert_committed_checkpoint(
        self,
        binding: _SkillArtifactBinding,
    ) -> None:
        checkpoint = self._tasks.get_committed_operation_checkpoint(
            binding.reservation_id
        )
        reservation = (
            checkpoint.get("reservation")
            if isinstance(checkpoint, Mapping)
            else None
        )
        if (
            not isinstance(reservation, Mapping)
            or str(reservation.get("reservation_id") or "")
            != binding.reservation_id
            or str(reservation.get("attempt_id") or "")
            != binding.attempt_id
            or not str(reservation.get("receipt_ref") or "").startswith(
                "worker-projection://"
            )
        ):
            self._invalid("Agent Skill committed checkpoint drifted")

    def _assert_projection_descriptor(
        self,
        binding: _SkillArtifactBinding,
        *,
        seal: Any,
    ) -> None:
        operation = self._tasks.get_operation_reservation(
            binding.reservation_id
        )
        projection_ref = str(operation.get("receipt_ref") or "")
        raw = self._projections.load(projection_ref)
        projection = validate_worker_projection_payload(
            raw,
            expected_projection_id=projection_ref,
        )
        protected = seal.protected_payload()
        audit = projection.get("audit_refs")
        effects = projection.get("acceptance_effect_intents")
        proposals = [
            item
            for item in (effects if isinstance(effects, list) else [])
            if isinstance(item, Mapping)
            and item.get("effect_type")
            == AGENT_WORKSPACE_ARTIFACT_EFFECT_TYPE
        ]
        result = self._artifacts.read_sealed_json(seal, "result.json")
        result_digest = canonical_workspace_artifact_digest(result)
        approved_descriptor = (
            proposals[0].get("payload")
            if len(proposals) == 1
            and isinstance(proposals[0].get("payload"), Mapping)
            else None
        )
        rejected_descriptor = (
            audit.get("terminal_workspace_artifact")
            if isinstance(audit, Mapping)
            else None
        )
        descriptor_exact = (
            projection.get("decision") == "APPROVE"
            and isinstance(approved_descriptor, Mapping)
            and approved_descriptor.get("protected_artifact") == protected
            and approved_descriptor.get("result_digest") == result_digest
            and approved_descriptor.get("target_id") == binding.target_id
            and approved_descriptor.get("operation_fingerprint")
            == binding.sealed_order.operation_fingerprint
        ) or (
            projection.get("decision") == "REJECT"
            and not proposals
            and rejected_descriptor == protected
            and isinstance(audit, Mapping)
            and audit.get("terminal_workspace_result_digest") == result_digest
        )
        exact = (
            projection.get("command_id") == binding.authority["command_id"]
            and projection.get("graph_id") == binding.authority["graph_id"]
            and projection.get("graph_revision")
            == binding.authority["graph_revision"]
            and projection.get("node_id") == binding.authority["node_id"]
            and projection.get("attempt_id") == binding.attempt_id
            and projection.get("route_type") == "skill"
            and projection.get("target_id") == binding.target_id
            and descriptor_exact
        )
        if not exact:
            self._invalid("Agent Skill projection and protected stage drifted")

    @staticmethod
    def _invalid(detail: str) -> None:
        raise SkillArtifactTerminalReconciliationError(detail)


class AgentArtifactTerminalReconcilerGroup:
    """Run route-specific reconcilers behind one lifecycle-owned worker."""

    def __init__(self, *reconcilers: Any) -> None:
        if not reconcilers or any(
            not callable(getattr(item, "run_once", None))
            for item in reconcilers
        ):
            raise TypeError("terminal reconciler group requires run_once members")
        self._reconcilers = tuple(reconcilers)

    def run_once(self, *, limit: int = 100) -> tuple[dict[str, Any], ...]:
        reports: list[dict[str, Any]] = []
        for reconciler in self._reconcilers:
            reports.extend(
                dict(item)
                for item in reconciler.run_once(limit=limit)
            )
        return tuple(reports)


__all__ = [
    "AgentArtifactTerminalReconcilerGroup",
    "GovernedSkillArtifactTerminalReconciler",
    "SKILL_ARTIFACT_TERMINAL_INVALID",
    "SkillArtifactTerminalReconciliationError",
    "SkillArtifactTerminalReport",
    "direct_write_skill_command_id",
    "direct_write_skill_event_command_id",
    "is_direct_write_skill_artifact",
]
