from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from host_brain.agent_runtime.artifact_store import governed_attempt_segment
from host_brain.agent_runtime.internal_artifact_terminal_reconciliation import (
    InternalArtifactTerminalContinuationWorker,
    InternalArtifactTerminalReconciler,
)
from host_brain.agent_runtime.workspace_runtime import DurableWorkspaceRuntime
from host_brain.contracts.agent_runtime.workspace_lifecycle import (
    WorkspaceLifecycleState,
)
from host_brain.contracts.agent_runtime.internal_nonacceptance_claim_result import (
    INTERNAL_NONACCEPTANCE_RESULT_REF_PREFIX,
)


def _authority(*, attempt_id: str = "attempt-raw") -> dict[str, Any]:
    return {
        "root_work_order_id": "work-order-root",
        "graph_id": "graph-internal",
        "graph_revision": 1,
        "node_id": "node-agent",
        "attempt_id": attempt_id,
        "lease_id": "lease-agent",
        "command_id": "command-agent",
        "principal_id": "principal-agent",
        "lease_owner_id": "worker-agent",
        "fencing_token": 7,
    }


class _Seal:
    def __init__(self, *, attempt_id: str = "attempt-raw", suffix: str = "a") -> None:
        self.execution_authority = _authority(attempt_id=attempt_id)
        self.workspace_id = "workspace-internal"
        self.attempt_id = attempt_id
        self.seal_digest = "sha256:" + (suffix * 64)
        self.stage_ref = (
            f"agent-stage://{self.workspace_id}/"
            f"{governed_attempt_segment(attempt_id)}/sha256/{suffix * 64}"
        )
        self.accepted_dir = None

    def protected_payload(self) -> dict[str, str]:
        return {
            "schema_version": "Natsume.agent.governed_artifact_stage.v1",
            "ref": self.stage_ref,
            "digest": self.seal_digest,
        }


class _Stage:
    def __init__(self, *, attempt_id: str = "attempt-raw", suffix: str = "c") -> None:
        self.execution_authority = _authority(attempt_id=attempt_id)
        self.workspace_id = "workspace-internal"
        self.attempt_id = attempt_id
        self.stage_ref = (
            f"agent-stage://{self.workspace_id}/"
            f"{governed_attempt_segment(attempt_id)}/sha256/{suffix * 64}"
        )


class _ArtifactStore:
    def __init__(
        self,
        seals: list[_Seal] | None = None,
        unsealed: list[_Stage] | None = None,
    ) -> None:
        self.seals = sorted(seals or [], key=lambda item: item.stage_ref)
        self.unsealed = sorted(unsealed or [], key=lambda item: item.stage_ref)
        self.inventory_cursors: list[tuple[str, str | None]] = []
        self.unsealed_inventory_cursors: list[str | None] = []
        self.abandoned: list[str] = []
        self.discarded: list[str] = []
        self.tombstones: dict[str, dict[str, Any]] = {}
        self.promotions: dict[str, dict[str, Any]] = {}
        self.accepted: dict[tuple[str, str], _Seal] = {}

    def inventory_governed_seals(
        self,
        *,
        namespace: str,
        limit: int,
        after_stage_ref: str | None = None,
    ) -> tuple[_Seal, ...]:
        self.inventory_cursors.append((namespace, after_stage_ref))
        if namespace == "accepted":
            source = sorted(self.accepted.values(), key=lambda item: item.stage_ref)
        else:
            source = self.seals
        return tuple(
            item
            for item in source
            if after_stage_ref is None or item.stage_ref > after_stage_ref
        )[:limit]

    def inventory_unsealed_governed_stages(
        self,
        *,
        limit: int,
        after_stage_ref: str | None = None,
    ) -> tuple[_Stage, ...]:
        self.unsealed_inventory_cursors.append(after_stage_ref)
        return tuple(
            item
            for item in self.unsealed
            if after_stage_ref is None or item.stage_ref > after_stage_ref
        )[:limit]

    def discard_unsealed_governed_stage(self, stage: _Stage) -> None:
        self.discarded.append(stage.stage_ref)
        self.unsealed = [
            item for item in self.unsealed if item.stage_ref != stage.stage_ref
        ]

    @staticmethod
    def read_projection_recovery(seal: _Seal) -> dict[str, Any]:
        frozen = {
            **seal.execution_authority,
            "route": "delegate",
            "target_id": "agent_runtime",
            "reservation_id": "reservation-agent",
            "business_scope": "internal:agent",
            "operation_fingerprint": "sha256:" + ("f" * 64),
        }
        return {
            "schema_version": "Natsume.internal.projection_recovery.v1",
            "frozen_node_input": frozen,
            "verified_node_grant": {"grant_ref": "grant://node/1"},
            "action_preview": None,
            "buffered_pre_acceptance_events": 0,
            "worker_timing": {
                "schema_version": "Natsume.internal.worker_timing.v1",
                "started_ts_ms": 100,
                "finished_ts_ms": 110,
                "elapsed_ms": 10,
            },
        }

    @staticmethod
    def plan_governed_abandonment(
        *,
        stage_ref: str,
        seal_digest: str,
        reason_code: str,
    ) -> str:
        del seal_digest, reason_code
        parts = stage_ref.split("/")
        return (
            f"agent-artifact-abandon://{parts[2]}/{parts[3]}/sha256/"
            + ("b" * 64)
        )

    def abandon_governed_stage(
        self,
        *,
        stage_ref: str,
        seal_digest: str,
        reason_code: str,
    ) -> str:
        ref = self.plan_governed_abandonment(
            stage_ref=stage_ref,
            seal_digest=seal_digest,
            reason_code=reason_code,
        )
        self.abandoned.append(stage_ref)
        return ref

    def inspect_governed_tombstone_ref(self, tombstone_ref: str) -> dict[str, Any]:
        return dict(self.tombstones[tombstone_ref])

    def inspect_governed_promotion(
        self,
        *,
        stage_ref: str,
        seal_digest: str,
    ) -> dict[str, Any]:
        del seal_digest
        return dict(self.promotions[stage_ref])

    def inspect_governed_stage_for_authority(
        self,
        *,
        workspace_id: str,
        execution_authority: dict[str, Any],
    ) -> _Seal:
        return self.accepted[(workspace_id, str(execution_authority["attempt_id"]))]


class _WorkspaceRepository:
    def __init__(self) -> None:
        self.candidates: list[dict[str, Any]] = []
        self.cursors: list[str | None] = []

    def list_terminal_lifecycle_candidates(
        self,
        *,
        limit: int,
        after_workspace_id: str | None = None,
    ) -> tuple[dict[str, Any], ...]:
        self.cursors.append(after_workspace_id)
        return tuple(
            item
            for item in self.candidates
            if after_workspace_id is None
            or item["authority"].workspace_id > after_workspace_id
        )[:limit]

    @staticmethod
    def resolve_internal_agent_takeover_lineage(**_values: Any) -> dict[str, Any]:
        raise AssertionError("a RELEASED fixture must not request takeover lineage")


class _WorkspaceRuntime:
    def __init__(self, repository: _WorkspaceRepository) -> None:
        self.repository = repository
        self.abandon_calls: list[dict[str, Any]] = []
        self.cleanup_calls: list[dict[str, Any]] = []
        self.unsealed_cleanup_calls: list[str] = []

    def abandon_terminal_artifact(self, **values: Any) -> Any:
        self.abandon_calls.append(dict(values))
        planned = values["artifact_planner"]()
        actual = values["artifact_abandoner"]()
        assert planned == actual
        return SimpleNamespace(tombstone_ref=actual)

    def cleanup_terminal_workspace(self, **values: Any) -> Any:
        self.cleanup_calls.append(dict(values))
        return SimpleNamespace(receipt_ref="workspace-cleanup://receipt/1")

    def cleanup(self, workspace_id: str) -> Any:
        self.unsealed_cleanup_calls.append(workspace_id)
        return SimpleNamespace(receipt_ref="workspace-cleanup://receipt/unsealed")


class _TaskStore:
    def __init__(self) -> None:
        self.graph_status = "BLOCKED"
        self.node_status = "WAITING_ACCEPTANCE_REVIEW"
        self.attempt_status = "SUCCEEDED"
        self.lease_status = "RELEASED"
        self.operation_status = "COMMITTED"
        self.protected_override: dict[str, str] | None = None
        self.seal: _Seal | None = None
        self.cancellation_status: str | None = "FINALIZED"
        self.root_work_order_id = "work-order-root"

    def get_graph(self, graph_id: str, revision: int) -> dict[str, Any]:
        return {
            "graph_id": graph_id,
            "revision": revision,
            "root_work_order_id": self.root_work_order_id,
            "status": self.graph_status,
        }

    def get_node(self, graph_id: str, revision: int, node_id: str) -> dict[str, Any]:
        del graph_id, revision
        return {
            "node_id": node_id,
            "status": self.node_status,
            "node": {"route": "delegate:agent_runtime"},
        }

    def get_attempt(self, attempt_id: str) -> dict[str, Any]:
        return {
            "attempt_id": attempt_id,
            "graph_id": "graph-internal",
            "graph_revision": 1,
            "node_id": "node-agent",
            "status": self.attempt_status,
        }

    def get_lease(self, lease_id: str) -> dict[str, Any]:
        return {
            "lease_id": lease_id,
            "attempt_id": "attempt-raw",
            "owner_id": "worker-agent",
            "fencing_token": 7,
            "status": self.lease_status,
        }

    def get_operation_reservation(self, reservation_id: str) -> dict[str, Any]:
        return {
            "reservation_id": reservation_id,
            "business_scope": "internal:agent",
            "operation_fingerprint": "sha256:" + ("f" * 64),
            "attempt_id": "attempt-raw",
            "lease_id": "lease-agent",
            "owner_id": "worker-agent",
            "fencing_token": 7,
            "status": self.operation_status,
            "receipt_ref": "worker-projection://internal/1",
        }

    def get_committed_operation_checkpoint(self, reservation_id: str) -> dict[str, Any]:
        return {"reservation": self.get_operation_reservation(reservation_id)}

    def resolve_internal_agent_takeover_lineage(
        self,
        **values: Any,
    ) -> dict[str, Any]:
        assert self.lease_status == "EXPIRED"
        assert values["historical_lease_id"] == "lease-agent"
        return {
            "historical_lease": self.get_lease("lease-agent"),
            "completion_lease": {
                "lease_id": "lease-agent-takeover",
                "attempt_id": values["attempt_id"],
                "owner_id": "worker-agent-takeover",
                "fencing_token": 8,
                "status": "RELEASED",
            },
            "recovered": True,
        }

    def resolve_internal_agent_budget_rejection_terminal(
        self,
        attempt_id: str,
    ) -> dict[str, Any]:
        if (
            attempt_id != "attempt-raw"
            or self.graph_status != "BLOCKED"
            or self.node_status != "FAILED"
            or self.attempt_status != "FAILED"
            or self.lease_status not in {"RELEASED", "EXPIRED"}
            or self.operation_status != "COMMITTED"
        ):
            raise RuntimeError("budget rejection authority drifted")
        return {
            "authority_kind": "INTERNAL_AGENT_BUDGET_REJECTED",
            "historical_lease_id": "lease-agent",
        }

    def resolve_internal_agent_nonacceptance_checkpoint_terminal(
        self,
        attempt_id: str,
    ) -> dict[str, Any]:
        reasons = {
            ("FAILED", "VERIFICATION_FAILED", "SUCCEEDED"): (
                "TERMINAL_VERIFICATION_FAILED"
            ),
            ("CANCELLED", "CANCELLED", "SUCCEEDED"): (
                "TERMINAL_POST_REVIEW_CANCELLED"
            ),
        }
        reason = reasons.get(
            (self.graph_status, self.node_status, self.attempt_status)
        )
        if (
            attempt_id != "attempt-raw"
            or reason is None
            or self.lease_status != "RELEASED"
            or self.operation_status != "COMMITTED"
        ):
            raise RuntimeError("non-acceptance terminal authority drifted")
        return {
            "authority_kind": "INTERNAL_AGENT_NONACCEPTANCE_TERMINAL",
            "terminal_reason": reason,
            "historical_lease": self.get_lease("lease-agent"),
            "completion_lease": self.get_lease("lease-agent"),
            "operation_reservation": self.get_operation_reservation(
                "reservation-agent"
            ),
            "projection": {
                "projection_id": "worker-projection://internal/1",
                "command_id": "command-agent",
                "graph_id": "graph-internal",
                "graph_revision": 1,
                "node_id": "node-agent",
                "attempt_id": "attempt-raw",
            },
        }

    def get_graph_cancellation_for_graph(
        self,
        graph_id: str,
        revision: int,
    ) -> dict[str, Any] | None:
        if self.cancellation_status is None:
            return None
        return {
            "status": self.cancellation_status,
            "graph_id": graph_id,
            "graph_revision": revision,
            "intent_ref": "taskcancel-intent://sha256/" + "a" * 64,
        }

    def get_graph_cancellation_decision_for_intent(
        self,
        intent_ref: str,
    ) -> dict[str, Any] | None:
        if self.cancellation_status is None:
            return None
        assert intent_ref == "taskcancel-intent://sha256/" + "a" * 64
        return {
            "intent_ref": intent_ref,
            "payload": {
                "graph_id": "graph-internal",
                "graph_revision": 1,
                "graph_target_status": "CANCELLED",
            },
        }


class _CommandRepository:
    @staticmethod
    def get_by_graph(*, graph_id: str, graph_revision: int) -> dict[str, Any]:
        command = SimpleNamespace(
            command_id="command-agent",
            graph_id=graph_id,
            graph_revision=graph_revision,
            surface=SimpleNamespace(value="internal"),
            ingress_claim_ref="ingress-claim://internal/terminal",
        )
        graph = SimpleNamespace(
            nodes=(SimpleNamespace(node_id="node-agent", route="delegate:agent_runtime"),)
        )
        return {"command": command, "graph": graph}


class _ProjectionStore:
    def __init__(self, task_store: _TaskStore) -> None:
        self.tasks = task_store

    def load(self, projection_id: str) -> dict[str, Any]:
        assert self.tasks.seal is not None
        protected = (
            self.tasks.protected_override
            if self.tasks.protected_override is not None
            else self.tasks.seal.protected_payload()
        )
        return {
            "projection_id": projection_id,
            "command_id": "command-agent",
            "graph_id": "graph-internal",
            "graph_revision": 1,
            "node_id": "node-agent",
            "attempt_id": "attempt-raw",
            "route_type": "delegate",
            "target_id": "agent_runtime",
            "acceptance_effect_intents": [
                {
                    "effect_type": "internal.agent.result_commit",
                    "payload": {"result": {"protected_artifact": protected}},
                }
            ],
        }


class _ClaimStore:
    def __init__(self) -> None:
        self.completed: list[dict[str, Any]] = []

    def complete_claim(self, **values: Any) -> None:
        self.completed.append(dict(values))


def _reconciler(
    seal: _Seal | None = None,
    *,
    unsealed: _Stage | None = None,
    claim_store: _ClaimStore | None = None,
) -> tuple[
    InternalArtifactTerminalReconciler,
    _ArtifactStore,
    _WorkspaceRuntime,
    _WorkspaceRepository,
    _TaskStore,
]:
    artifacts = _ArtifactStore(
        [seal] if seal is not None else [],
        [unsealed] if unsealed is not None else [],
    )
    repository = _WorkspaceRepository()
    workspaces = _WorkspaceRuntime(repository)
    tasks = _TaskStore()
    tasks.seal = seal
    reconciler = InternalArtifactTerminalReconciler(
        artifact_store=artifacts,
        workspace_runtime=workspaces,
        task_store=tasks,
        command_repository=_CommandRepository(),
        projection_store=_ProjectionStore(tasks),
        claim_result_store=claim_store,
    )
    return reconciler, artifacts, workspaces, repository, tasks


def test_waiting_acceptance_review_is_held_without_abandonment() -> None:
    reconciler, artifacts, workspaces, _repository, _tasks = _reconciler(_Seal())
    report = reconciler.run_once()
    assert report[0]["action"] == "HOLD"
    assert workspaces.abandon_calls == []
    assert artifacts.abandoned == []


def test_exact_cancelled_unsealed_stage_is_cleaned_without_worker_rerun() -> None:
    stage = _Stage()
    reconciler, artifacts, workspaces, _repository, tasks = _reconciler(
        unsealed=stage
    )
    tasks.graph_status = "CANCELLED"
    tasks.node_status = "CANCELLED"
    tasks.attempt_status = "CANCELLED"
    tasks.lease_status = "CANCELLED"

    report = reconciler.run_once()

    assert report == (
        {
            "workspace_id": stage.workspace_id,
            "attempt_id": stage.attempt_id,
            "action": "UNSEALED_CLEANED",
            "reason_code": "TERMINAL_EXECUTION_CANCELLED_UNSEALED",
            "receipt_ref": "workspace-cleanup://receipt/unsealed",
        },
    )
    assert artifacts.discarded == [stage.stage_ref]
    assert artifacts.unsealed == []
    assert workspaces.unsealed_cleanup_calls == [stage.workspace_id]


def test_unsealed_stage_without_finalized_cancellation_is_preserved() -> None:
    stage = _Stage()
    reconciler, artifacts, workspaces, _repository, tasks = _reconciler(
        unsealed=stage
    )
    tasks.graph_status = "CANCELLED"
    tasks.node_status = "CANCELLED"
    tasks.attempt_status = "CANCELLED"
    tasks.lease_status = "CANCELLED"
    tasks.cancellation_status = None

    report = reconciler.run_once()

    assert report[0]["action"] == "BLOCKED"
    assert artifacts.discarded == []
    assert artifacts.unsealed == [stage]
    assert workspaces.unsealed_cleanup_calls == []


def test_unsealed_stage_with_root_lineage_drift_is_preserved() -> None:
    stage = _Stage()
    reconciler, artifacts, workspaces, _repository, tasks = _reconciler(
        unsealed=stage
    )
    tasks.graph_status = "CANCELLED"
    tasks.node_status = "CANCELLED"
    tasks.attempt_status = "CANCELLED"
    tasks.lease_status = "CANCELLED"
    tasks.root_work_order_id = "work-order-drifted"

    report = reconciler.run_once()

    assert report[0]["action"] == "BLOCKED"
    assert artifacts.discarded == []
    assert artifacts.unsealed == [stage]
    assert workspaces.unsealed_cleanup_calls == []


@pytest.mark.parametrize(
    (
        "graph_status",
        "node_status",
        "attempt_status",
        "lease_status",
        "operation_status",
        "reason_code",
    ),
    (
        (
            "FAILED",
            "VERIFICATION_FAILED",
            "SUCCEEDED",
            "RELEASED",
            "COMMITTED",
            "TERMINAL_VERIFICATION_FAILED",
        ),
        (
            "FAILED",
            "FAILED",
            "SUCCEEDED",
            "RELEASED",
            "COMMITTED",
            "TERMINAL_REVIEW_REJECTED",
        ),
        (
            "FAILED",
            "FAILED",
            "FAILED",
            "RELEASED",
            "RECOVERY_REQUIRED",
            "TERMINAL_WORKER_FAILED",
        ),
        (
            "CANCELLED",
            "CANCELLED",
            "CANCELLED",
            "CANCELLED",
            "CANCELLED",
            "TERMINAL_EXECUTION_CANCELLED",
        ),
        (
            "CANCELLED",
            "CANCELLED",
            "SUCCEEDED",
            "RELEASED",
            "COMMITTED",
            "TERMINAL_POST_REVIEW_CANCELLED",
        ),
        (
            "BLOCKED",
            "FAILED",
            "FAILED",
            "RELEASED",
            "COMMITTED",
            "TERMINAL_BUDGET_REJECTED",
        ),
    ),
)
def test_exact_terminal_matrices_abandon_without_rerunning(
    graph_status: str,
    node_status: str,
    attempt_status: str,
    lease_status: str,
    operation_status: str,
    reason_code: str,
) -> None:
    seal = _Seal()
    reconciler, artifacts, workspaces, _repository, tasks = _reconciler(seal)
    tasks.graph_status = graph_status
    tasks.node_status = node_status
    tasks.attempt_status = attempt_status
    tasks.lease_status = lease_status
    tasks.operation_status = operation_status
    report = reconciler.run_once()
    assert report[0]["action"] == "ABANDONED"
    assert report[0]["reason_code"] == reason_code
    assert len(workspaces.abandon_calls) == 1
    assert artifacts.abandoned == [seal.stage_ref]


@pytest.mark.parametrize(
    ("graph_status", "node_status", "attempt_status", "reason_code"),
    (
        (
            "FAILED",
            "VERIFICATION_FAILED",
            "SUCCEEDED",
            "TERMINAL_VERIFICATION_FAILED",
        ),
        (
            "CANCELLED",
            "CANCELLED",
            "SUCCEEDED",
            "TERMINAL_POST_REVIEW_CANCELLED",
        ),
    ),
)
def test_nonacceptance_terminal_claim_closes_before_abandonment(
    graph_status: str,
    node_status: str,
    attempt_status: str,
    reason_code: str,
) -> None:
    seal = _Seal()
    claims = _ClaimStore()
    reconciler, artifacts, workspaces, _repository, tasks = _reconciler(
        seal,
        claim_store=claims,
    )
    tasks.graph_status = graph_status
    tasks.node_status = node_status
    tasks.attempt_status = attempt_status
    tasks.lease_status = "RELEASED"
    tasks.operation_status = "COMMITTED"

    report = reconciler.run_once()

    assert report[0]["action"] == "ABANDONED"
    assert report[0]["reason_code"] == reason_code
    assert artifacts.abandoned == [seal.stage_ref]
    assert len(workspaces.abandon_calls) == 1
    assert len(claims.completed) == 1
    assert claims.completed[0]["claim_ref"] == "ingress-claim://internal/terminal"
    assert claims.completed[0]["result_ref"].startswith(
        INTERNAL_NONACCEPTANCE_RESULT_REF_PREFIX
    )


def test_raw_worker_failure_never_closes_nonacceptance_claim() -> None:
    seal = _Seal()
    claims = _ClaimStore()
    reconciler, artifacts, _workspaces, _repository, tasks = _reconciler(
        seal,
        claim_store=claims,
    )
    tasks.graph_status = "FAILED"
    tasks.node_status = "FAILED"
    tasks.attempt_status = "FAILED"
    tasks.lease_status = "RELEASED"
    tasks.operation_status = "RECOVERY_REQUIRED"

    report = reconciler.run_once()

    assert report[0]["action"] == "ABANDONED"
    assert artifacts.abandoned == [seal.stage_ref]
    assert claims.completed == []


def test_committed_projection_must_bind_the_exact_protected_seal() -> None:
    seal = _Seal()
    reconciler, artifacts, workspaces, _repository, tasks = _reconciler(seal)
    tasks.graph_status = "FAILED"
    tasks.node_status = "VERIFICATION_FAILED"
    tasks.protected_override = {
        **seal.protected_payload(),
        "digest": "sha256:" + ("9" * 64),
    }
    report = reconciler.run_once()
    assert report[0]["action"] == "BLOCKED"
    assert workspaces.abandon_calls == []
    assert artifacts.abandoned == []


def test_staging_cursor_rotates_past_a_full_hold_page() -> None:
    seals = [_Seal(attempt_id=f"attempt-{index}", suffix=value) for index, value in enumerate("abc")]
    reconciler, artifacts, _workspaces, _repository, tasks = _reconciler()
    artifacts.seals = sorted(seals, key=lambda item: item.stage_ref)
    tasks.seal = seals[0]
    first = reconciler.run_once(limit=2)
    second = reconciler.run_once(limit=2)
    assert len(first) == 2
    assert len(second) == 1
    assert artifacts.inventory_cursors[0] == ("staging", None)
    assert artifacts.inventory_cursors[1][0] == "staging"
    assert artifacts.inventory_cursors[1][1] is not None


def test_abandoned_tombstone_is_reopened_before_workspace_cleanup() -> None:
    reconciler, artifacts, workspaces, repository, _tasks = _reconciler()
    attempt_id = "attempt-raw"
    stage_ref = (
        "agent-stage://workspace-internal/"
        f"{governed_attempt_segment(attempt_id)}/sha256/" + ("a" * 64)
    )
    tombstone_ref = (
        "agent-artifact-abandon://workspace-internal/"
        f"{governed_attempt_segment(attempt_id)}/sha256/" + ("b" * 64)
    )
    event = SimpleNamespace(
        graph_id="graph-internal",
        graph_revision=1,
        node_id="node-agent",
        attempt_id=attempt_id,
        task_lease_id="lease-agent",
        task_lease_fencing_token=7,
        terminal_ref=tombstone_ref,
        reason_code="TERMINAL_WORKER_FAILED",
    )
    repository.candidates = [
        {
            "authority": SimpleNamespace(
                workspace_id="workspace-internal",
                root_work_order_id="work-order-root",
                owner_principal_id="principal-agent",
            ),
            "lifecycle": SimpleNamespace(
                state=WorkspaceLifecycleState.ABANDONED,
                latest_event=event,
            ),
        }
    ]
    artifacts.tombstones[tombstone_ref] = {
        "stage_ref": stage_ref,
        "seal_digest": "sha256:" + ("a" * 64),
        "reason_code": "TERMINAL_WORKER_FAILED",
        "tombstone_ref": tombstone_ref,
    }
    report = reconciler.run_once()
    assert report == (
        {
            "workspace_id": "workspace-internal",
            "attempt_id": attempt_id,
            "action": "CLEANED",
            "reason_code": None,
            "receipt_ref": "workspace-cleanup://receipt/1",
        },
    )
    assert workspaces.cleanup_calls[0]["expected_state"] is (
        WorkspaceLifecycleState.ABANDONED
    )


def test_promoted_artifact_is_exactly_reopened_before_workspace_cleanup() -> None:
    reconciler, artifacts, workspaces, repository, _tasks = _reconciler()
    seal = _Seal()
    seal.accepted_dir = SimpleNamespace()
    accepted_ref = (
        "agent-artifact://workspace-internal/attempt-raw/sha256/" + ("a" * 64)
    )
    artifacts.accepted[(seal.workspace_id, seal.attempt_id)] = seal
    artifacts.promotions[seal.stage_ref] = {
        "schema_version": "Natsume.agent.governed_artifact_stage.v1",
        "stage_ref": seal.stage_ref,
        "seal_digest": seal.seal_digest,
        "accepted_ref": accepted_ref,
        "acceptance_ref": "acceptance://internal/1",
    }
    event = SimpleNamespace(
        graph_id="graph-internal",
        graph_revision=1,
        node_id="node-agent",
        attempt_id="attempt-raw",
        task_lease_id="lease-agent",
        task_lease_fencing_token=7,
        terminal_ref=accepted_ref,
        reason_code=None,
    )
    repository.candidates = [
        {
            "authority": SimpleNamespace(
                workspace_id="workspace-internal",
                root_work_order_id="work-order-root",
                owner_principal_id="principal-agent",
            ),
            "lifecycle": SimpleNamespace(
                state=WorkspaceLifecycleState.PROMOTED,
                latest_event=event,
            ),
        }
    ]
    report = reconciler.run_once()
    assert report[0]["action"] == "CLEANED"
    assert workspaces.cleanup_calls[0]["expected_terminal_ref"] == accepted_ref


def test_promoted_artifact_drift_blocks_cleanup() -> None:
    reconciler, artifacts, workspaces, repository, _tasks = _reconciler()
    seal = _Seal()
    seal.accepted_dir = SimpleNamespace()
    accepted_ref = (
        "agent-artifact://workspace-internal/attempt-raw/sha256/" + ("a" * 64)
    )
    artifacts.accepted[(seal.workspace_id, seal.attempt_id)] = seal
    artifacts.promotions[seal.stage_ref] = {
        "accepted_ref": "agent-artifact://workspace-internal/attempt-raw/sha256/"
        + ("9" * 64)
    }
    event = SimpleNamespace(
        graph_id="graph-internal",
        graph_revision=1,
        node_id="node-agent",
        attempt_id="attempt-raw",
        task_lease_id="lease-agent",
        task_lease_fencing_token=7,
        terminal_ref=accepted_ref,
        reason_code=None,
    )
    repository.candidates = [
        {
            "authority": SimpleNamespace(
                workspace_id="workspace-internal",
                root_work_order_id="work-order-root",
                owner_principal_id="principal-agent",
            ),
            "lifecycle": SimpleNamespace(
                state=WorkspaceLifecycleState.PROMOTED,
                latest_event=event,
            ),
        }
    ]
    report = reconciler.run_once()
    assert report[0]["action"] == "BLOCKED"
    assert workspaces.cleanup_calls == []


def test_worker_health_reports_blocked_without_exception_detail() -> None:
    seal = _Seal()
    reconciler, _artifacts, _workspaces, _repository, tasks = _reconciler(seal)
    tasks.graph_status = "FAILED"
    tasks.node_status = "VERIFICATION_FAILED"
    tasks.protected_override = {
        **seal.protected_payload(),
        "digest": "sha256:" + ("9" * 64),
    }
    worker = InternalArtifactTerminalContinuationWorker(reconciler=reconciler)
    worker.run_once()
    failure = worker.health_snapshot()["last_failure"]
    assert failure == {
        "reason_code": "INTERNAL_ARTIFACT_TERMINAL_BLOCKED",
        "count": 1,
    }


def test_workspace_abandonment_persists_journal_before_tombstone_and_replays() -> None:
    authority = _authority()
    stage_ref = (
        "agent-stage://workspace-internal/"
        f"{governed_attempt_segment('attempt-raw')}/sha256/" + ("a" * 64)
    )
    tombstone_ref = (
        "agent-artifact-abandon://workspace-internal/"
        f"{governed_attempt_segment('attempt-raw')}/sha256/" + ("b" * 64)
    )
    ordering: list[str] = []

    class Repository:
        snapshot = SimpleNamespace(
            state=WorkspaceLifecycleState.ACTIVE,
            revision=0,
            latest_event=None,
        )
        append_calls = 0

        def get_lifecycle(self, _workspace_id: str) -> Any:
            return self.snapshot

        def abandon_workspace(self, _workspace_id: str, **values: Any) -> Any:
            ordering.append("JOURNAL")
            self.append_calls += 1
            event = SimpleNamespace(
                event_id=values["event_id"],
                from_state=WorkspaceLifecycleState.ACTIVE,
                graph_id=authority["graph_id"],
                graph_revision=authority["graph_revision"],
                node_id=authority["node_id"],
                attempt_id=authority["attempt_id"],
                task_lease_id=authority["lease_id"],
                task_lease_fencing_token=authority["fencing_token"],
                terminal_ref=values["abandonment_ref"],
                reason_code=values["reason_code"],
                acceptance_event_id=None,
            )
            self.snapshot = SimpleNamespace(
                state=WorkspaceLifecycleState.ABANDONED,
                revision=1,
                latest_event=event,
            )
            return SimpleNamespace(event=event)

    repository = Repository()
    runtime = object.__new__(DurableWorkspaceRuntime)
    runtime.repository = repository
    runtime._promotion_authority = lambda **_kwargs: dict(authority)
    runtime._durable_ref = lambda value, _field: str(value)
    runtime._required = lambda value, _field, _maximum: str(value)
    runtime._continuation_lease = lambda *_args, **_kwargs: SimpleNamespace(
        lease_owner="workspace-worker",
        lease_token="workspace-token",
        lease_fencing_token=3,
    )

    def plan() -> str:
        ordering.append("PLAN")
        return tombstone_ref

    def abandon() -> str:
        ordering.append("TOMBSTONE")
        return tombstone_ref

    first = DurableWorkspaceRuntime.abandon_terminal_artifact(
        runtime,
        workspace_id="workspace-internal",
        execution_authority=authority,
        stage_ref=stage_ref,
        seal_digest="sha256:" + ("a" * 64),
        reason_code="TERMINAL_WORKER_FAILED",
        artifact_planner=plan,
        artifact_abandoner=abandon,
    )
    second = DurableWorkspaceRuntime.abandon_terminal_artifact(
        runtime,
        workspace_id="workspace-internal",
        execution_authority=authority,
        stage_ref=stage_ref,
        seal_digest="sha256:" + ("a" * 64),
        reason_code="TERMINAL_WORKER_FAILED",
        artifact_planner=plan,
        artifact_abandoner=abandon,
    )
    assert ordering == ["PLAN", "JOURNAL", "TOMBSTONE", "PLAN", "TOMBSTONE"]
    assert repository.append_calls == 1
    assert first.replayed is False
    assert second.replayed is True
