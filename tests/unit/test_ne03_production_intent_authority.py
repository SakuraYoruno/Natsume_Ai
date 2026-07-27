from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sqlite3

import pytest

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
)
from host_brain.contracts.agent_runtime.review_policy import ReviewItem
from host_brain.contracts.agent_runtime.budget_policy import BudgetAmount, BudgetPolicy
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.governor.action_arbiter import build_governed_action_binding
from host_brain.integrations.runtime_events import RuntimeEventStore
from host_brain.orchestration import GovernedCommandApplication
from host_brain.orchestration.governor_grant import governor_grant_ref
from host_brain.persistence.review_repository import (
    REVIEW_RESUME_ALREADY_CONSUMED,
    ReviewStore,
    ReviewStoreError,
)
from host_brain.persistence.host_command_repository import HostCommandRepository
from host_brain.persistence.review_continuation_store import (
    REVIEW_CONTINUATION_CONFLICT,
    ReviewContinuationStore,
)
from host_brain.persistence.task_repository import DurableTaskStore
from host_brain.persistence.task_store_types import (
    OutboxMessage,
    build_outbox_payload_ref,
)
from host_brain.runtime.context_contracts import (
    ContextConstraintV1,
    SceneDecisionV2,
    WorldFactV2,
    WorldSnapshotV2,
)
from host_brain.runtime.intent_admission import (
    DurableIntentAdmissionVerifier,
    INTENT_ADMISSION_BLOCKED,
    INTENT_ADMISSION_INVALID,
    INTENT_ADMISSION_REVIEW_REQUIRED,
    IntentAdmissionError,
)
from host_brain.runtime.intent_arbiter import IntentArbiter
from host_brain.runtime.intent_authority import (
    IntentAuthorityError,
    IntentAuthorityVerifier,
    IntentPolicyIdentityV1,
)
from host_brain.runtime.production_intent_authority import (
    ProductionIntentAuthorityPort,
    build_canonical_intent_frame,
)
from host_brain.task_runtime.review_continuation_worker import (
    ReviewContinuationWorker,
)
from tests.task_runtime_authority_fixtures import seed_active_graph_budget_for_test
from natsume_contracts.task import HostCommand, HostCommandSurface, build_host_command
from natsume_contracts.task.v1 import ExecutorAssignment, TaskExecutor


NOW_MS = 2_000
POLICY = IntentPolicyIdentityV1(
    policy_ref="policy://intent-arbiter/production-v1",
    policy_digest="9" * 64,
)


class _CommandRepository:
    def __init__(self, record: dict[str, object]) -> None:
        self.record = record
        self.queries: list[tuple[str, int]] = []

    def get_by_graph(self, *, graph_id: str, graph_revision: int):
        self.queries.append((graph_id, graph_revision))
        return self.record


class _SceneResolver:
    def __init__(self, scene: SceneDecisionV2) -> None:
        self.scene = scene
        self.queries: list[dict[str, object]] = []

    def require_decision(self, **query: object) -> SceneDecisionV2:
        self.queries.append(dict(query))
        expected = {
            "workspace_id": self.scene.workspace_id,
            "subject_id": self.scene.subject_id,
            "scene_id": self.scene.scene_id,
            "revision": self.scene.revision,
            "decision_digest": self.scene.decision_digest,
        }
        if query != expected:
            raise RuntimeError("scene identity drift")
        return self.scene


class _EmptyLongTermGoalAuthority:
    def active_identity(self, **_kwargs):
        return {
            "goal_refs": [],
            "constraints": [],
            "authority_digest": "sha256:" + "0" * 64,
        }


EMPTY_GOALS = _EmptyLongTermGoalAuthority()


def _authority_fixture(*, expires_at_ms: int = 10_000, risk_level: int = 1):
    snapshot = WorldSnapshotV2(
        workspace_id="workspace-a",
        subject_id="person-a",
        revision=0,
        facts=(),
    )
    scene = SceneDecisionV2(
        workspace_id="workspace-a",
        subject_id="person-a",
        scene_id="scene.chat",
        revision=1,
        world_snapshot_digest=snapshot.snapshot_digest,
        context_digest="b" * 64,
        guardian_snapshot_digest="c" * 64,
        constraints=(),
    )
    binding = build_governed_action_binding(
        intent_envelope={
            "schema_version": "Natsume.intent.v1",
            "id": "intent-a",
            "ts_ms": 1_000,
            "session_id": "session-a",
            "seq": 1,
            "type": "query",
            "action": "exec_intent",
            "target": "host",
            "args": {"query": "hello"},
            "nonce": "nonce-a",
            "person_id": "person-a",
            "principal_id": "principal-a",
        },
        approval_token_ref="approval::intent-a",
        risk_level=risk_level,
        issued_at_ms=1_000,
        expires_at_ms=expires_at_ms,
    )
    decision = {
        "request_id": "request-a",
        "intent_id": "intent-a",
        "decision": "APPROVE",
        "reason_code": "APPROVE",
        "tokens": {"approval_token_ref": "approval::intent-a"},
        "approved_action_binding": binding,
        "stage": "S_INTENT_OK",
        "ts_ms": 1_000,
    }
    grant_ref = governor_grant_ref("request-a", decision)
    node = TaskNode(
        node_id="node-a",
        dependencies=(),
        route="chat:surface_reply",
        input_refs=("input://sha256/" + "1" * 64, grant_ref),
        output_contract={"type": "chat", "required": ["narrative"]},
        retry_policy={"max_attempts": 1, "retry_backoff_ms": 0},
        risk_level=risk_level,
        acceptance_criteria=("reply is durable",),
        status=TaskNodeStatus.READY,
    )
    graph = TaskGraph(
        graph_id="graph-a",
        root_work_order_id="work-order-a",
        revision=1,
        planner_ref="planner://host/direct",
        status=TaskGraphStatus.CREATED,
        created_at=1_000,
        result_node_id=node.node_id,
        nodes=(node,),
    )
    input_pack = {
        "runtime_context_schema": "natsume.runtime-context.request.v1",
        "runtime_context_principal_id": "principal-a",
        "workspace_id": "workspace-a",
        "subject_id": "person-a",
        "world_snapshot_v2": snapshot.to_dict(),
        "world_identity_v2": {
            "workspace_id": "workspace-a",
            "subject_id": "person-a",
            "revision": 0,
            "snapshot_digest": snapshot.snapshot_digest,
        },
        "scene_decision_v2": scene.to_dict(),
        "scene_identity_v2": {
            "schema": "natsume.scene-identity.v2",
            "workspace_id": "workspace-a",
            "subject_id": "person-a",
            "scene_id": scene.scene_id,
            "revision": scene.revision,
            "decision_digest": scene.decision_digest,
        },
        "intent_id": "intent-a",
        "intent_draft": {
            "route": "chat",
            "target_id": "surface_reply",
            "task": "say hello",
            "action": "exec_intent",
            "target": "host",
            "args": {"query": "hello"},
        },
        "bond_summary": {"relationship_state": "NORMAL"},
        "motive_state": {},
    }
    work_order = WorkOrderV3(
        work_order_id="work-order-a",
        domain="chat",
        run_mode="worker",
        objective="say hello",
        workspace={"workspace_id": "workspace-a"},
        input_pack=input_pack,
        allowed_actions=("render_reply",),
        forbidden_actions=("replan",),
        acceptance_criteria=("reply is durable",),
        verification_plan={"required": True},
        approval_policy={"required": True},
        review_policy={"review_required": False},
        budget_policy=BudgetPolicy(
            scope="work-order:work-order-a",
            limits=BudgetAmount(
                token_count=10_000,
                tool_call_count=20,
                time_ms=60_000,
                concurrency=2,
                retry_count=2,
                model_cost_micros=1_000_000,
            ),
        ),
        metadata={
            "planning_lineage": {
                "node_bindings": [
                    {
                        "node_id": "node-a",
                        "objective": "say hello",
                        "worker_route": "chat",
                        "target_id": "surface_reply",
                    }
                ]
            }
        },
        root_work_order_id="work-order-a",
        task_graph_id="graph-a",
        task_node_id="node-a",
        graph_revision=1,
        risk_level=risk_level,
        input_refs=("input://sha256/" + "1" * 64, grant_ref),
        output_contract={"type": "chat", "required": ["narrative"]},
        business_idempotency_scope="host-chat:surface_reply",
        operation_fingerprint="sha256:" + "2" * 64,
        approval_ref=grant_ref,
    )
    work_order.validate()
    command = build_host_command(
        surface=HostCommandSurface.CHAT,
        request_id="request-a",
        session_id="session-a",
        principal_id="principal-a",
        input_ref="input://sha256/" + "1" * 64,
        input_sha256="sha256:" + "1" * 64,
        root_work_order_id="work-order-a",
        graph_id="graph-a",
        graph_revision=1,
        route_type="chat",
        target_id="surface_reply",
        business_scope="host-chat:surface_reply",
        operation_fingerprint="sha256:" + "2" * 64,
        governor_grant_ref=grant_ref,
        created_at_ms=1_000,
    )
    ledger = SQLiteLedger(":memory:")
    ledger.log_governor(decision)
    record: dict[str, object] = {
        "command": command,
        "work_order": work_order,
        "graph": graph,
    }
    return record, scene, binding, ledger


def test_production_intent_authority_reopens_all_three_durable_owners() -> None:
    record, scene, binding, ledger = _authority_fixture()
    repository = _CommandRepository(record)
    resolver = _SceneResolver(scene)
    port = ProductionIntentAuthorityPort(
        command_repository=repository,
        scene_resolver=None,
        governor_ledger=ledger,
        policy_identity=POLICY,
        long_term_goal_authority=EMPTY_GOALS,
        clock=lambda: NOW_MS,
    )
    port.bind_scene_resolver(resolver)
    frame = build_canonical_intent_frame(
        command=record["command"],  # type: ignore[arg-type]
        work_order=record["work_order"],  # type: ignore[arg-type]
        graph=record["graph"],  # type: ignore[arg-type]
        task_node_id="node-a",
        canonical_scene=scene,
        approved_action_binding=binding,
        policy_identity=POLICY,
        now_ms=NOW_MS,
    )

    attestation = IntentAuthorityVerifier(
        authority_port=port,
        policy_identity=POLICY,
        clock=lambda: NOW_MS,
    ).verify(frame=frame, scene=scene)

    assert attestation.intent_frame_digest == frame.frame_digest
    assert attestation.grant_principal_id == "principal-a"
    assert attestation.grant_issued_at_ms == 1_000
    assert attestation.grant_expires_at_ms == 10_000
    assert repository.queries == [("graph-a", 1)]
    assert resolver.queries == [
        {
            "workspace_id": "workspace-a",
            "subject_id": "person-a",
            "scene_id": "scene.chat",
            "revision": 1,
            "decision_digest": scene.decision_digest,
        }
    ]


def test_high_risk_intent_uses_context_pack_workspace_for_review_constraint() -> None:
    record, scene, binding, _ledger = _authority_fixture(risk_level=3)
    frame = build_canonical_intent_frame(
        command=record["command"],  # type: ignore[arg-type]
        work_order=record["work_order"],  # type: ignore[arg-type]
        graph=record["graph"],  # type: ignore[arg-type]
        task_node_id="node-a",
        canonical_scene=scene,
        approved_action_binding=binding,
        policy_identity=POLICY,
        now_ms=NOW_MS,
    )

    assert len(frame.risk_constraints) == 1
    constraint = frame.risk_constraints[0]
    assert constraint.code == "HIGH_RISK_REVIEW"
    assert constraint.disposition == "REVIEW"
    assert constraint.source_ref == "risk-policy://workspace-a/host-runtime-v1"


def test_production_intent_authority_rejects_cross_workspace_query() -> None:
    record, scene, _binding, ledger = _authority_fixture()
    port = ProductionIntentAuthorityPort(
        command_repository=_CommandRepository(record),
        scene_resolver=None,
        governor_ledger=ledger,
        policy_identity=POLICY,
        long_term_goal_authority=EMPTY_GOALS,
        clock=lambda: NOW_MS,
    )
    port.bind_scene_resolver(_SceneResolver(scene))

    with pytest.raises(IntentAuthorityError, match="Scene lookup"):
        port.resolve_intent_authority(
            workspace_id="workspace-b",
            subject_id="person-a",
            task_graph_id="graph-a",
            graph_revision=1,
            task_node_id="node-a",
            scene_id=scene.scene_id,
            scene_revision=scene.revision,
            scene_decision_digest=scene.decision_digest,
            grant_ref=record["command"].governor_grant_ref,  # type: ignore[union-attr]
        )


def test_production_intent_authority_rejects_frozen_scene_or_grant_drift() -> None:
    record, scene, binding, _ledger = _authority_fixture()
    work_order = record["work_order"]
    assert isinstance(work_order, WorkOrderV3)
    drifted_pack = deepcopy(work_order.input_pack)
    drifted_pack["scene_identity_v2"]["decision_digest"] = "0" * 64
    drifted_order = replace(work_order, input_pack=drifted_pack)

    with pytest.raises(IntentAuthorityError, match="Scene identity"):
        build_canonical_intent_frame(
            command=record["command"],  # type: ignore[arg-type]
            work_order=drifted_order,
            graph=record["graph"],  # type: ignore[arg-type]
            task_node_id="node-a",
            canonical_scene=scene,
            approved_action_binding=binding,
            policy_identity=POLICY,
            now_ms=NOW_MS,
        )

    drifted_binding = {**binding, "principal_id": "principal-b"}
    with pytest.raises(IntentAuthorityError, match="principal or subject"):
        build_canonical_intent_frame(
            command=record["command"],  # type: ignore[arg-type]
            work_order=work_order,
            graph=record["graph"],  # type: ignore[arg-type]
            task_node_id="node-a",
            canonical_scene=scene,
            approved_action_binding=drifted_binding,
            policy_identity=POLICY,
            now_ms=NOW_MS,
        )


@pytest.mark.parametrize("missing_field", ("action", "target", "route", "target_id"))
def test_grant_and_route_binding_rejects_missing_frozen_field(
    missing_field: str,
) -> None:
    record, scene, binding, _ledger = _authority_fixture()
    work_order = record["work_order"]
    assert isinstance(work_order, WorkOrderV3)
    drifted_pack = deepcopy(work_order.input_pack)
    del drifted_pack["intent_draft"][missing_field]

    with pytest.raises(IntentAuthorityError, match="is missing"):
        build_canonical_intent_frame(
            command=record["command"],  # type: ignore[arg-type]
            work_order=replace(work_order, input_pack=drifted_pack),
            graph=record["graph"],  # type: ignore[arg-type]
            task_node_id="node-a",
            canonical_scene=scene,
            approved_action_binding=binding,
            policy_identity=POLICY,
            now_ms=NOW_MS,
        )


def test_grant_and_route_binding_rejects_command_or_node_drift() -> None:
    record, scene, binding, _ledger = _authority_fixture()
    command = record["command"]
    graph = record["graph"]
    assert isinstance(command, HostCommand)
    assert isinstance(graph, TaskGraph)

    with pytest.raises(IntentAuthorityError, match="command route binding"):
        build_canonical_intent_frame(
            command=replace(command, target_id="different-target"),
            work_order=record["work_order"],  # type: ignore[arg-type]
            graph=graph,
            task_node_id="node-a",
            canonical_scene=scene,
            approved_action_binding=binding,
            policy_identity=POLICY,
            now_ms=NOW_MS,
        )

    drifted_node = replace(graph.nodes[0], route="chat:different-target")
    with pytest.raises(IntentAuthorityError, match="TaskNode route binding"):
        build_canonical_intent_frame(
            command=command,
            work_order=record["work_order"],  # type: ignore[arg-type]
            graph=replace(graph, nodes=(drifted_node,)),
            task_node_id="node-a",
            canonical_scene=scene,
            approved_action_binding=binding,
            policy_identity=POLICY,
            now_ms=NOW_MS,
        )


def test_relationship_constraints_come_only_from_frozen_world_evidence() -> None:
    record, original_scene, binding, _ledger = _authority_fixture()
    work_order = record["work_order"]
    graph = record["graph"]
    assert isinstance(work_order, WorkOrderV3)
    assert isinstance(graph, TaskGraph)
    source_ref = "runtimeevent://sha256/" + "7" * 64
    snapshot = WorldSnapshotV2(
        workspace_id="workspace-a",
        subject_id="person-a",
        revision=1,
        facts=(
            WorldFactV2(
                workspace_id="workspace-a",
                subject_id="person-a",
                key="relationship.state",
                value="UNTRUSTED",
                source_ref=source_ref,
                confidence=1.0,
                observed_at_ms=1_000,
                expires_at_ms=10_000,
                conflict_group="relationship",
                revision=1,
            ),
        ),
    )
    scene = replace(
        original_scene,
        world_snapshot_digest=snapshot.snapshot_digest,
        decision_digest="",
    )
    input_pack = deepcopy(work_order.input_pack)
    input_pack["world_snapshot_v2"] = snapshot.to_dict()
    input_pack["world_identity_v2"] = {
        "workspace_id": snapshot.workspace_id,
        "subject_id": snapshot.subject_id,
        "revision": snapshot.revision,
        "snapshot_digest": snapshot.snapshot_digest,
    }
    input_pack["scene_decision_v2"] = scene.to_dict()
    input_pack["scene_identity_v2"]["decision_digest"] = scene.decision_digest
    # The legacy convenience projection disagrees deliberately.  It must not
    # participate in admission authority after World has been frozen.
    input_pack["bond_summary"] = {"relationship_state": "NORMAL"}
    constrained = replace(work_order, input_pack=input_pack)

    frame = build_canonical_intent_frame(
        command=record["command"],  # type: ignore[arg-type]
        work_order=constrained,
        graph=graph,
        task_node_id="node-a",
        canonical_scene=scene,
        approved_action_binding=binding,
        policy_identity=POLICY,
        now_ms=NOW_MS,
    )

    assert len(frame.relationship_constraints) == 1
    assert frame.relationship_constraints[0].code == "RELATIONSHIP_UNTRUSTED"
    assert frame.relationship_constraints[0].disposition == "BLOCK"
    assert frame.relationship_constraints[0].source_ref == source_ref


def test_legacy_bond_summary_cannot_fabricate_relationship_constraint() -> None:
    record, scene, binding, _ledger = _authority_fixture()
    work_order = record["work_order"]
    graph = record["graph"]
    assert isinstance(work_order, WorkOrderV3)
    assert isinstance(graph, TaskGraph)
    input_pack = deepcopy(work_order.input_pack)
    input_pack["bond_summary"] = {"relationship_state": "UNTRUSTED"}
    forged = replace(work_order, input_pack=input_pack)

    frame = build_canonical_intent_frame(
        command=record["command"],  # type: ignore[arg-type]
        work_order=forged,
        graph=graph,
        task_node_id="node-a",
        canonical_scene=scene,
        approved_action_binding=binding,
        policy_identity=POLICY,
        now_ms=NOW_MS,
    )

    assert frame.relationship_constraints == ()


def test_long_term_goal_refs_are_owned_by_frozen_world_evidence() -> None:
    record, original_scene, binding, _ledger = _authority_fixture()
    work_order = record["work_order"]
    graph = record["graph"]
    assert isinstance(work_order, WorkOrderV3)
    assert isinstance(graph, TaskGraph)
    frozen_goal = "goal://workspace-a/long-term/reliable-assistant"
    snapshot = WorldSnapshotV2(
        workspace_id="workspace-a",
        subject_id="person-a",
        revision=1,
        facts=(
            WorldFactV2(
                workspace_id="workspace-a",
                subject_id="person-a",
                key="goal.long_term.ref",
                value=frozen_goal,
                source_ref="runtimeevent://sha256/" + "8" * 64,
                confidence=1.0,
                observed_at_ms=1_000,
                expires_at_ms=10_000,
                conflict_group="long-term-goal",
                revision=1,
            ),
        ),
    )
    scene = replace(
        original_scene,
        world_snapshot_digest=snapshot.snapshot_digest,
        decision_digest="",
    )
    input_pack = deepcopy(work_order.input_pack)
    input_pack["world_snapshot_v2"] = snapshot.to_dict()
    input_pack["world_identity_v2"] = {
        "workspace_id": snapshot.workspace_id,
        "subject_id": snapshot.subject_id,
        "revision": snapshot.revision,
        "snapshot_digest": snapshot.snapshot_digest,
    }
    input_pack["scene_decision_v2"] = scene.to_dict()
    input_pack["scene_identity_v2"]["decision_digest"] = scene.decision_digest
    frozen_order = replace(work_order, input_pack=input_pack)

    frame = build_canonical_intent_frame(
        command=record["command"],  # type: ignore[arg-type]
        work_order=frozen_order,
        graph=graph,
        task_node_id="node-a",
        canonical_scene=scene,
        approved_action_binding=binding,
        policy_identity=POLICY,
        now_ms=NOW_MS,
    )
    assert frame.long_term_goal_refs == (frozen_goal,)

    forged_pack = deepcopy(input_pack)
    forged_pack["motive_state"] = {
        "long_term_goal_refs": ["goal://workspace-a/forged-caller-goal"]
    }
    with pytest.raises(IntentAuthorityError, match="absent from frozen World"):
        build_canonical_intent_frame(
            command=record["command"],  # type: ignore[arg-type]
            work_order=replace(work_order, input_pack=forged_pack),
            graph=graph,
            task_node_id="node-a",
            canonical_scene=scene,
            approved_action_binding=binding,
            policy_identity=POLICY,
            now_ms=NOW_MS,
        )


def test_production_intent_authority_rejects_goal_changed_after_freeze() -> None:
    record, original_scene, _binding, ledger = _authority_fixture()
    work_order = record["work_order"]
    assert isinstance(work_order, WorkOrderV3)
    frozen_goal = "long-term-goal://workspace-a/person-a/reliable-assistant/v1"
    snapshot = WorldSnapshotV2(
        workspace_id="workspace-a",
        subject_id="person-a",
        revision=1,
        facts=(
            WorldFactV2(
                workspace_id="workspace-a",
                subject_id="person-a",
                key="goal.long_term.ref",
                value=frozen_goal,
                source_ref="runtimeevent://sha256/" + "8" * 64,
                confidence=1.0,
                observed_at_ms=1_000,
                expires_at_ms=10_000,
                conflict_group="long-term-goal",
                revision=1,
            ),
        ),
    )
    scene = replace(
        original_scene,
        world_snapshot_digest=snapshot.snapshot_digest,
        decision_digest="",
    )
    input_pack = deepcopy(work_order.input_pack)
    input_pack["world_snapshot_v2"] = snapshot.to_dict()
    input_pack["world_identity_v2"] = {
        "workspace_id": snapshot.workspace_id,
        "subject_id": snapshot.subject_id,
        "revision": snapshot.revision,
        "snapshot_digest": snapshot.snapshot_digest,
    }
    input_pack["scene_decision_v2"] = scene.to_dict()
    input_pack["scene_identity_v2"]["decision_digest"] = scene.decision_digest
    record["work_order"] = replace(work_order, input_pack=input_pack)
    port = ProductionIntentAuthorityPort(
        command_repository=_CommandRepository(record),
        scene_resolver=_SceneResolver(scene),
        governor_ledger=ledger,
        policy_identity=POLICY,
        # Empty current identity represents revocation after Context freeze.
        long_term_goal_authority=EMPTY_GOALS,
        clock=lambda: NOW_MS,
    )

    with pytest.raises(
        IntentAuthorityError,
        match="long-term-goal authority changed",
    ):
        port.resolve_intent_authority(
            workspace_id="workspace-a",
            subject_id="person-a",
            task_graph_id="graph-a",
            graph_revision=1,
            task_node_id="node-a",
            scene_id=scene.scene_id,
            scene_revision=scene.revision,
            scene_decision_digest=scene.decision_digest,
            grant_ref=record["command"].governor_grant_ref,  # type: ignore[union-attr]
        )


def test_production_intent_authority_rejects_expired_durable_binding() -> None:
    record, scene, _binding, ledger = _authority_fixture(expires_at_ms=NOW_MS)
    port = ProductionIntentAuthorityPort(
        command_repository=_CommandRepository(record),
        scene_resolver=_SceneResolver(scene),
        governor_ledger=ledger,
        policy_identity=POLICY,
        long_term_goal_authority=EMPTY_GOALS,
        clock=lambda: NOW_MS,
    )

    with pytest.raises(IntentAuthorityError, match="expired"):
        port.resolve_intent_authority(
            workspace_id="workspace-a",
            subject_id="person-a",
            task_graph_id="graph-a",
            graph_revision=1,
            task_node_id="node-a",
            scene_id=scene.scene_id,
            scene_revision=scene.revision,
            scene_decision_digest=scene.decision_digest,
            grant_ref=record["command"].governor_grant_ref,  # type: ignore[union-attr]
        )


def test_production_intent_authority_cannot_rebind_scene_owner() -> None:
    record, scene, _binding, ledger = _authority_fixture()
    first = _SceneResolver(scene)
    port = ProductionIntentAuthorityPort(
        command_repository=_CommandRepository(record),
        scene_resolver=first,
        governor_ledger=ledger,
        policy_identity=POLICY,
        long_term_goal_authority=EMPTY_GOALS,
    )

    with pytest.raises(IntentAuthorityError, match="already bound"):
        port.bind_scene_resolver(_SceneResolver(scene))


def _runtime_events(tmp_path: Path, *, clock=lambda: NOW_MS) -> RuntimeEventStore:
    db_path = tmp_path / "intent-admission.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="ne03-intent-admission",
        ts_ms=NOW_MS,
    )
    assert result.success is True, result.as_dict()
    return RuntimeEventStore(db_path, clock=clock)


def _intent_admission_verifier(
    *,
    record: dict[str, object],
    scene: SceneDecisionV2,
    ledger: SQLiteLedger,
    event_store: RuntimeEventStore,
    clock: list[int],
) -> tuple[DurableIntentAdmissionVerifier, _CommandRepository, _SceneResolver]:
    graph = record["graph"]
    assert isinstance(graph, TaskGraph)
    with sqlite3.connect(event_store.db_path) as conn:
        existing_admission = conn.execute(
            """
            SELECT 1 FROM host_command_journal
            WHERE graph_id = ? AND graph_revision = ?
            """,
            (graph.graph_id, graph.revision),
        ).fetchone()
    if existing_admission is None:
        work_order = record["work_order"]
        command = record["command"]
        assignment = ExecutorAssignment(
            work_order_id=work_order.work_order_id,  # type: ignore[union-attr]
            executor=TaskExecutor.CANONICAL,
            assignment_id=(
                f"runtime-assignment://{work_order.work_order_id}/intent-admission"  # type: ignore[union-attr]
            ),
            assigned_at_ms=1_000,
            is_new_task=True,
            reason_code="RUNTIME_NEW_TASK_CANONICAL",
        )
        HostCommandRepository(
            event_store.db_path,
            clock=lambda: clock[0],
        ).admit(command, work_order, assignment, graph)
    repository = _CommandRepository(record)
    resolver = _SceneResolver(scene)
    port = ProductionIntentAuthorityPort(
        command_repository=repository,
        scene_resolver=resolver,
        governor_ledger=ledger,
        policy_identity=POLICY,
        long_term_goal_authority=EMPTY_GOALS,
        clock=lambda: clock[0],
    )
    verifier = DurableIntentAdmissionVerifier(
        authority_port=port,
        intent_arbiter=IntentArbiter(
            authority_port=port,
            policy_identity=POLICY,
            clock=lambda: clock[0],
        ),
        event_store=event_store,
        review_store=ReviewStore(event_store.db_path, clock=lambda: clock[0]),
        clock=lambda: clock[0],
    )
    return verifier, repository, resolver


def _require_intent_review(
    *,
    record: dict[str, object],
    original_scene: SceneDecisionV2,
    ledger: SQLiteLedger,
    event_store: RuntimeEventStore,
    clock: list[int],
) -> tuple[DurableIntentAdmissionVerifier, SceneDecisionV2, ReviewStore, dict]:
    scene = replace(
        original_scene,
        constraints=(
            ContextConstraintV1(
                code="SCENE_EXECUTION_REVIEW",
                disposition="REVIEW",
                origin="SCENE",
                source_ref="scene://workspace-a/person-a/scene.chat/revision/1",
            ),
        ),
        decision_digest="",
    )
    work_order = record["work_order"]
    assert isinstance(work_order, WorkOrderV3)
    input_pack = deepcopy(work_order.input_pack)
    input_pack["scene_decision_v2"] = scene.to_dict()
    input_pack["scene_identity_v2"]["decision_digest"] = scene.decision_digest
    record["work_order"] = replace(work_order, input_pack=input_pack)
    verifier, _, _ = _intent_admission_verifier(
        record=record,
        scene=scene,
        ledger=ledger,
        event_store=event_store,
        clock=clock,
    )
    with pytest.raises(IntentAdmissionError) as required:
        verifier.verify_intent_admission(
            command=record["command"],  # type: ignore[arg-type]
            work_order=record["work_order"],  # type: ignore[arg-type]
            graph=record["graph"],  # type: ignore[arg-type]
            replayed=False,
        )
    assert required.value.reason_code == INTENT_ADMISSION_REVIEW_REQUIRED
    reviews = ReviewStore(event_store.db_path, clock=lambda: clock[0])
    open_reviews = reviews.list_open_reviews()
    assert len(open_reviews) == 1
    return verifier, scene, reviews, open_reviews[0]


def test_intent_admission_receipt_replays_after_parent_grant_expiry(
    tmp_path: Path,
) -> None:
    record, scene, _binding, ledger = _authority_fixture()
    clock = [NOW_MS]
    store = _runtime_events(tmp_path, clock=lambda: clock[0])
    verifier, repository, resolver = _intent_admission_verifier(
        record=record,
        scene=scene,
        ledger=ledger,
        event_store=store,
        clock=clock,
    )

    first = verifier.verify_intent_admission(
        command=record["command"],  # type: ignore[arg-type]
        work_order=record["work_order"],  # type: ignore[arg-type]
        graph=record["graph"],  # type: ignore[arg-type]
        replayed=False,
    )
    assert first[0]["decision"] == "NO_ADDITIONAL_RESTRICTION"
    assert first[0]["proceed_allowed"] is True
    assert len(store.list_events(event_type="IntentAdmissionArbitrated")) == 1

    # A restarted boundary re-opens only the immutable command and Scene.  It
    # must not ask the now-expired Governor grant or IntentArbiter again.
    clock[0] = 20_000
    replay, replay_repository, replay_resolver = _intent_admission_verifier(
        record=record,
        scene=scene,
        ledger=ledger,
        event_store=RuntimeEventStore(store.db_path, clock=lambda: clock[0]),
        clock=clock,
    )
    second = replay.verify_intent_admission(
        command=record["command"],  # type: ignore[arg-type]
        work_order=record["work_order"],  # type: ignore[arg-type]
        graph=record["graph"],  # type: ignore[arg-type]
        replayed=True,
    )

    assert second == first
    assert repository.queries == [("graph-a", 1), ("graph-a", 1), ("graph-a", 1)]
    assert len(resolver.queries) == 3
    assert replay_repository.queries == [("graph-a", 1)]
    assert len(replay_resolver.queries) == 1


@pytest.mark.parametrize(
    ("disposition", "expected_reason"),
    [
        ("BLOCK", INTENT_ADMISSION_BLOCKED),
        ("REVIEW", INTENT_ADMISSION_REVIEW_REQUIRED),
    ],
)
def test_intent_admission_persists_non_executable_decision_before_failing_closed(
    tmp_path: Path,
    disposition: str,
    expected_reason: str,
) -> None:
    record, original_scene, _binding, ledger = _authority_fixture()
    scene = replace(
        original_scene,
        constraints=(
            ContextConstraintV1(
                code=f"SCENE_EXECUTION_{disposition}",
                disposition=disposition,
                origin="SCENE",
                source_ref="scene://workspace-a/person-a/scene.chat/revision/1",
            ),
        ),
        decision_digest="",
    )
    work_order = record["work_order"]
    assert isinstance(work_order, WorkOrderV3)
    input_pack = deepcopy(work_order.input_pack)
    input_pack["scene_decision_v2"] = scene.to_dict()
    input_pack["scene_identity_v2"]["decision_digest"] = scene.decision_digest
    record["work_order"] = replace(work_order, input_pack=input_pack)
    clock = [NOW_MS]
    store = _runtime_events(tmp_path, clock=lambda: clock[0])
    verifier, _, _ = _intent_admission_verifier(
        record=record,
        scene=scene,
        ledger=ledger,
        event_store=store,
        clock=clock,
    )

    with pytest.raises(IntentAdmissionError) as blocked:
        verifier.verify_intent_admission(
            command=record["command"],  # type: ignore[arg-type]
            work_order=record["work_order"],  # type: ignore[arg-type]
            graph=record["graph"],  # type: ignore[arg-type]
            replayed=False,
        )
    assert blocked.value.reason_code == expected_reason
    events = store.list_events(event_type="IntentAdmissionArbitrated")
    assert len(events) == 1
    assert events[0]["payload"]["decision"] == disposition
    assert events[0]["payload"]["proceed_allowed"] is False

    clock[0] = 20_000
    replay, _, _ = _intent_admission_verifier(
        record=record,
        scene=scene,
        ledger=ledger,
        event_store=RuntimeEventStore(store.db_path, clock=lambda: clock[0]),
        clock=clock,
    )
    with pytest.raises(IntentAdmissionError) as replayed:
        replay.verify_intent_admission(
            command=record["command"],  # type: ignore[arg-type]
            work_order=record["work_order"],  # type: ignore[arg-type]
            graph=record["graph"],  # type: ignore[arg-type]
            replayed=True,
        )
    assert replayed.value.reason_code == expected_reason


def test_intent_admission_rejects_durable_contract_drift_before_receipt_reuse(
    tmp_path: Path,
) -> None:
    record, scene, _binding, ledger = _authority_fixture()
    clock = [NOW_MS]
    store = _runtime_events(tmp_path, clock=lambda: clock[0])
    verifier, repository, _ = _intent_admission_verifier(
        record=record,
        scene=scene,
        ledger=ledger,
        event_store=store,
        clock=clock,
    )
    verifier.verify_intent_admission(
        command=record["command"],  # type: ignore[arg-type]
        work_order=record["work_order"],  # type: ignore[arg-type]
        graph=record["graph"],  # type: ignore[arg-type]
        replayed=False,
    )
    durable_order = record["work_order"]
    assert isinstance(durable_order, WorkOrderV3)
    repository.record = {
        **record,
        "work_order": replace(durable_order, objective="drifted objective"),
    }

    with pytest.raises(IntentAdmissionError) as drift:
        verifier.verify_intent_admission(
            command=record["command"],  # type: ignore[arg-type]
            work_order=record["work_order"],  # type: ignore[arg-type]
            graph=record["graph"],  # type: ignore[arg-type]
            replayed=True,
        )
    assert drift.value.reason_code == INTENT_ADMISSION_INVALID
    assert "differ" in drift.value.detail


def test_intent_admission_persists_one_exact_receipt_per_graph_node(
    tmp_path: Path,
) -> None:
    record, scene, _binding, ledger = _authority_fixture()
    graph = record["graph"]
    work_order = record["work_order"]
    assert isinstance(graph, TaskGraph)
    assert isinstance(work_order, WorkOrderV3)
    second = replace(
        graph.nodes[0],
        node_id="node-b",
        dependencies=("node-a",),
        status=TaskNodeStatus.BLOCKED,
        status_version=0,
    )
    graph = replace(graph, nodes=(*graph.nodes, second), result_node_id="node-b")
    metadata = deepcopy(work_order.metadata)
    metadata["planning_lineage"]["node_bindings"].append(
        {
            "node_id": "node-b",
            "objective": "verify hello",
            "worker_route": "chat",
            "target_id": "surface_reply",
        }
    )
    work_order = replace(work_order, metadata=metadata)
    graph.validate()
    work_order.validate()
    record["graph"] = graph
    record["work_order"] = work_order
    clock = [NOW_MS]
    store = _runtime_events(tmp_path, clock=lambda: clock[0])
    verifier, _, _ = _intent_admission_verifier(
        record=record,
        scene=scene,
        ledger=ledger,
        event_store=store,
        clock=clock,
    )

    receipts = verifier.verify_intent_admission(
        command=record["command"],  # type: ignore[arg-type]
        work_order=work_order,
        graph=graph,
        replayed=False,
    )

    assert {item["task_node_id"] for item in receipts} == {"node-a", "node-b"}
    events = store.list_events(event_type="IntentAdmissionArbitrated")
    assert len(events) == 2
    assert len({item["event_id"] for item in events}) == 2


def test_intent_review_consumption_appends_continuation_and_complete_set(
    tmp_path: Path,
) -> None:
    record, original_scene, _binding, ledger = _authority_fixture()
    clock = [NOW_MS]
    store = _runtime_events(tmp_path, clock=lambda: clock[0])
    verifier, _scene, reviews, review_record = _require_intent_review(
        record=record,
        original_scene=original_scene,
        ledger=ledger,
        event_store=store,
        clock=clock,
    )
    review = review_record["review"]
    approved = reviews.approve_review(
        review["review_id"],
        actor_id="operator-a",
        expected_subject_ref=review["subject_ref"],
        expected_subject_version=review["subject_version"],
        expected_resume_phase=review["resume_phase"],
        expected_checkpoint_id=review["checkpoint_id"],
    )
    resumed = reviews.resume_review(
        review["review_id"],
        resume_token_ref=approved["resume_token_ref"],
        subject_ref=review["subject_ref"],
        subject_version=review["subject_version"],
        resume_phase=review["resume_phase"],
        checkpoint_id=review["checkpoint_id"],
        resume_result_ref="intent-review-result://graph-a/1/node-a",
    )
    assert resumed["review"]["review"]["status"] == "CONSUMED"

    result = verifier.verify_intent_admission(
        command=record["command"],  # type: ignore[arg-type]
        work_order=record["work_order"],  # type: ignore[arg-type]
        graph=record["graph"],  # type: ignore[arg-type]
        replayed=True,
    )

    assert result[0]["decision"] == "REVIEW"
    assert result[0]["proceed_allowed"] is False
    continuation = store.list_events(event_type="IntentAdmissionReviewContinued")
    assert len(continuation) == 1
    assert continuation[0]["payload"]["continuation_allowed"] is True
    assert continuation[0]["payload"]["permission_effect"] == "RESTRICT_ONLY"
    assert len(
        store.list_events(event_type="IntentAdmissionReceiptSetCompleted")
    ) == 1

    with pytest.raises(ReviewStoreError) as reused:
        reviews.resume_review(
            review["review_id"],
            resume_token_ref=approved["resume_token_ref"],
            subject_ref=review["subject_ref"],
            subject_version=review["subject_version"],
            resume_phase=review["resume_phase"],
            checkpoint_id=review["checkpoint_id"],
        )
    assert reused.value.reason_code == REVIEW_RESUME_ALREADY_CONSUMED


def test_intent_review_rejection_is_permanently_blocked(tmp_path: Path) -> None:
    record, original_scene, _binding, ledger = _authority_fixture()
    clock = [NOW_MS]
    store = _runtime_events(tmp_path, clock=lambda: clock[0])
    verifier, _scene, reviews, review_record = _require_intent_review(
        record=record,
        original_scene=original_scene,
        ledger=ledger,
        event_store=store,
        clock=clock,
    )
    review_id = review_record["review"]["review_id"]
    reviews.reject_review(review_id, actor_id="operator-a")

    with pytest.raises(IntentAdmissionError) as rejected:
        verifier.verify_intent_admission(
            command=record["command"],  # type: ignore[arg-type]
            work_order=record["work_order"],  # type: ignore[arg-type]
            graph=record["graph"],  # type: ignore[arg-type]
            replayed=True,
        )
    assert rejected.value.reason_code == INTENT_ADMISSION_BLOCKED
    assert store.list_events(event_type="IntentAdmissionReviewContinued") == []


def test_intent_review_identity_drift_cannot_continue(tmp_path: Path) -> None:
    record, original_scene, _binding, ledger = _authority_fixture()
    clock = [NOW_MS]
    store = _runtime_events(tmp_path, clock=lambda: clock[0])
    verifier, _scene, _reviews, review_record = _require_intent_review(
        record=record,
        original_scene=original_scene,
        ledger=ledger,
        event_store=store,
        clock=clock,
    )
    review = dict(review_record["review"])
    review["subject_ref"] = "intent-node://graph-a/1/drifted"
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "UPDATE agent_runtime_review SET review_json = ? WHERE review_id = ?",
            (
                json.dumps(review, sort_keys=True, separators=(",", ":")),
                review["review_id"],
            ),
        )

    with pytest.raises(IntentAdmissionError) as drifted:
        verifier.verify_intent_admission(
            command=record["command"],  # type: ignore[arg-type]
            work_order=record["work_order"],  # type: ignore[arg-type]
            graph=record["graph"],  # type: ignore[arg-type]
            replayed=True,
        )
    assert drifted.value.reason_code == INTENT_ADMISSION_INVALID
    assert "drifted" in drifted.value.detail


@pytest.mark.parametrize("forgery", ["subject", "evidence"])
def test_intent_review_ready_checkpoint_rejects_forged_subject_or_evidence(
    tmp_path: Path,
    forgery: str,
) -> None:
    record, original_scene, _binding, ledger = _authority_fixture()
    clock = [NOW_MS]
    store = _runtime_events(tmp_path, clock=lambda: clock[0])
    _verifier, _scene, reviews, review_record = _require_intent_review(
        record=record,
        original_scene=original_scene,
        ledger=ledger,
        event_store=store,
        clock=clock,
    )
    base = ReviewItem.from_mapping(review_record["review"])
    forged = replace(
        base,
        review_id=f"{base.review_id}-forged-{forgery}",
        checkpoint_id=f"intent-checkpoint://forged/{forgery}",
        subject_ref=(
            base.subject_ref + "&caller_override=READY"
            if forgery == "subject"
            else base.subject_ref
        ),
        evidence_refs=(
            (
                "runtimeevent://sha256/" + "0" * 64,
                *base.evidence_refs[1:],
            )
            if forgery == "evidence"
            else base.evidence_refs
        ),
    )

    with pytest.raises(ReviewStoreError) as rejected:
        reviews.create_review(
            forged,
            work_order_id="work-order-a",
            task_graph_id="graph-a",
            task_node_id="node-a",
        )

    assert rejected.value.reason_code == REVIEW_CONTINUATION_CONFLICT


@pytest.mark.asyncio
async def test_consumed_intent_review_is_automatically_resumed_after_process_restart(
    tmp_path: Path,
) -> None:
    record, original_scene, _binding, ledger = _authority_fixture()
    clock = [NOW_MS]
    store = _runtime_events(tmp_path, clock=lambda: clock[0])
    _verifier, scene, reviews, review_record = _require_intent_review(
        record=record,
        original_scene=original_scene,
        ledger=ledger,
        event_store=store,
        clock=clock,
    )
    review = review_record["review"]
    approved = reviews.approve_review(
        review["review_id"],
        actor_id="operator-a",
        expected_subject_ref=review["subject_ref"],
        expected_subject_version=review["subject_version"],
        expected_resume_phase=review["resume_phase"],
        expected_checkpoint_id=review["checkpoint_id"],
    )
    reviews.resume_review(
        review["review_id"],
        resume_token_ref=approved["resume_token_ref"],
        subject_ref=review["subject_ref"],
        subject_version=review["subject_version"],
        resume_phase=review["resume_phase"],
        checkpoint_id=review["checkpoint_id"],
        resume_result_ref="intent-review-result://graph-a/1/node-a/restart",
    )

    # Simulate a new Host process after the original parent grant has expired.
    clock[0] = 20_000
    restarted, _repository, _resolver = _intent_admission_verifier(
        record=record,
        scene=scene,
        ledger=ledger,
        event_store=RuntimeEventStore(store.db_path, clock=lambda: clock[0]),
        clock=clock,
    )
    task_store = DurableTaskStore(store.db_path, clock=lambda: clock[0])
    budget_id = "budget-intent-review-restart"
    seed_active_graph_budget_for_test(
        task_store,
        graph_id="graph-a",
        graph_revision=1,
        budget_id=budget_id,
        scope="graph:graph-a@1:intent-review-restart",
        limits=record["work_order"].budget_policy.limits,  # type: ignore[union-attr]
        outbox=OutboxMessage.create(
            aggregate_type="BUDGET_RESERVATION",
            aggregate_id=budget_id,
            aggregate_version=0,
            event_type="BUDGET_RESERVATION_CREATED",
            payload_ref=build_outbox_payload_ref(
                aggregate_type="BUDGET_RESERVATION",
                aggregate_id=budget_id,
                aggregate_version=0,
            ),
            idempotency_key=f"ne03:{budget_id}",
            outbox_id=f"outbox::ne03::{budget_id}",
        ),
    )

    class _RestartedRuntime:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def continue_review_continuation(self, claim: dict) -> dict:
            self.calls.append(dict(claim))
            receipts = restarted.verify_intent_admission(
                command=record["command"],  # type: ignore[arg-type]
                work_order=record["work_order"],  # type: ignore[arg-type]
                graph=record["graph"],  # type: ignore[arg-type]
                replayed=True,
            )
            attempt_id = "attempt-intent-review-restart"
            task_store.create_attempt(
                attempt_id=attempt_id,
                graph_id="graph-a",
                graph_revision=1,
                node_id="node-a",
                attempt_no=1,
                expected_node_version=0,
                outbox=OutboxMessage.create(
                    aggregate_type="TASK_ATTEMPT",
                    aggregate_id=attempt_id,
                    aggregate_version=0,
                    event_type="TASK_ATTEMPT_CREATED",
                    payload_ref=build_outbox_payload_ref(
                        aggregate_type="TASK_ATTEMPT",
                        aggregate_id=attempt_id,
                        aggregate_version=0,
                    ),
                    idempotency_key=f"ne03:{attempt_id}",
                    attempt_id=attempt_id,
                    outbox_id=f"outbox::ne03::{attempt_id}",
                ),
            )
            owner_id = "intent-review-executor-restarted"

            def _attempt_event(version: int, event_type: str) -> OutboxMessage:
                return OutboxMessage.create(
                    aggregate_type="TASK_ATTEMPT",
                    aggregate_id=attempt_id,
                    aggregate_version=version,
                    event_type=event_type,
                    payload_ref=build_outbox_payload_ref(
                        aggregate_type="TASK_ATTEMPT",
                        aggregate_id=attempt_id,
                        aggregate_version=version,
                    ),
                    idempotency_key=f"ne03:{attempt_id}:{version}",
                    attempt_id=attempt_id,
                    outbox_id=f"outbox::ne03::{attempt_id}::{version}",
                )

            lease = task_store.acquire_lease(
                attempt_id,
                owner_id=owner_id,
                ttl_ms=5_000,
                expected_attempt_version=0,
                outbox=_attempt_event(1, "TASK_ATTEMPT_LEASED"),
            )
            task_store.transition_attempt(
                attempt_id,
                owner_id=owner_id,
                fencing_token=int(lease["fencing_token"]),
                expected_version=1,
                target_status="RUNNING",
                outbox=_attempt_event(2, "TASK_ATTEMPT_RUNNING"),
            )
            task_store.transition_attempt(
                attempt_id,
                owner_id=owner_id,
                fencing_token=int(lease["fencing_token"]),
                expected_version=2,
                target_status="SUCCEEDED",
                outbox=_attempt_event(3, "TASK_ATTEMPT_SUCCEEDED"),
            )
            return {"continued": True, "receipt_count": len(receipts)}

    continuation_store = ReviewContinuationStore(
        store.db_path,
        clock=lambda: clock[0],
    )
    continuation_store.assert_review_continuation_ready(force_revalidate=True)
    runtime = _RestartedRuntime()
    worker = ReviewContinuationWorker(
        store=continuation_store,
        runtime=runtime,
        review_expirer=ReviewStore(store.db_path, clock=lambda: clock[0]),
        worker_id="intent-review-worker-restarted",
        claim_lease_ms=5_000,
        poll_interval_ms=10,
        retry_delay_ms=0,
    )

    assert await worker.run_once() == 1
    assert len(runtime.calls) == 1
    assert runtime.calls[0]["review_status"] == "CONSUMED"
    assert continuation_store.get_review_continuation(review["review_id"]) is None
    assert task_store.get_attempt("attempt-intent-review-restart")["status"] == "SUCCEEDED"
    assert len(store.list_events(event_type="IntentAdmissionReviewContinued")) == 1
    assert len(store.list_events(event_type="IntentAdmissionReceiptSetCompleted")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["REJECTED", "EXPIRED"])
async def test_denied_intent_review_worker_terminalizes_graph_without_retry_loop(
    tmp_path: Path,
    terminal_status: str,
) -> None:
    record, original_scene, _binding, ledger = _authority_fixture()
    clock = [NOW_MS]
    store = _runtime_events(tmp_path, clock=lambda: clock[0])
    verifier, _scene, reviews, review_record = _require_intent_review(
        record=record,
        original_scene=original_scene,
        ledger=ledger,
        event_store=store,
        clock=clock,
    )
    review = review_record["review"]
    if terminal_status == "REJECTED":
        reviews.reject_review(review["review_id"], actor_id="operator-a")
    else:
        clock[0] = int(review["expires_at"])
        sweep = reviews.expire_due()
        assert review["review_id"] in sweep["expired_review_ids"]

    command = record["command"]
    work_order = record["work_order"]
    graph = record["graph"]
    assignment = ExecutorAssignment(
        work_order_id="work-order-a",
        executor=TaskExecutor.CANONICAL,
        assignment_id="runtime-assignment://work-order-a/intent-review-terminal",
        assigned_at_ms=1_000,
        is_new_task=True,
        reason_code="RUNTIME_NEW_TASK_CANONICAL",
    )

    class _AdmissionRepository:
        @staticmethod
        def admit(command_arg, work_order_arg, assignment_arg, graph_arg):
            return {
                "command": command_arg,
                "work_order": work_order_arg,
                "assignment": assignment_arg,
                "graph": graph_arg,
                "created": False,
                "replayed": True,
            }

    class _TerminalCoordinator:
        def __init__(self) -> None:
            self.cancellations: list[dict] = []
            self.execute_calls = 0

        async def execute(self, **_kwargs):
            self.execute_calls += 1
            raise AssertionError("denied Intent Review must never execute a TaskNode")

        async def request_graph_cancellation(self, **kwargs):
            self.cancellations.append(dict(kwargs))
            with sqlite3.connect(store.db_path) as conn:
                conn.execute(
                    """
                    UPDATE task_runtime_node
                    SET status = 'CANCELLED', status_version = status_version + 1,
                        updated_ts_ms = ?
                    WHERE graph_id = 'graph-a' AND graph_revision = 1
                      AND node_id = 'node-a' AND status = 'READY'
                    """,
                    (clock[0],),
                )
                conn.execute(
                    """
                    UPDATE task_runtime_graph
                    SET status = 'CANCELLED', status_version = status_version + 1,
                        updated_ts_ms = ?
                    WHERE graph_id = 'graph-a' AND revision = 1
                    """,
                    (clock[0],),
                )
            return {
                "done": True,
                "decision": "CANCELLED",
                "reason_code": kwargs["reason_code"],
            }

    coordinator = _TerminalCoordinator()
    app = GovernedCommandApplication(
        command_repository=_AdmissionRepository(),
        coordinator=coordinator,  # type: ignore[arg-type]
        intent_admission_verifier=verifier,
    )

    class _ApplicationRuntime:
        async def continue_review_continuation(self, claim: dict) -> dict:
            return await app.execute_review_continuation(
                command=command,  # type: ignore[arg-type]
                work_order=work_order,
                assignment=assignment,
                graph=graph,
                continuation_claim=claim,
            )

    continuation_store = ReviewContinuationStore(
        store.db_path,
        clock=lambda: clock[0],
    )
    worker = ReviewContinuationWorker(
        store=continuation_store,
        runtime=_ApplicationRuntime(),
        review_expirer=reviews,
        worker_id=f"intent-review-{terminal_status.lower()}-worker",
        claim_lease_ms=5_000,
        poll_interval_ms=10,
        retry_delay_ms=0,
    )

    assert await worker.run_once() == 1
    assert continuation_store.get_review_continuation(review["review_id"]) is None
    assert coordinator.execute_calls == 0
    assert len(coordinator.cancellations) == 1
    cancellation = coordinator.cancellations[0]
    assert cancellation["source"] == "SYSTEM"
    assert cancellation["reason_code"] == f"INTENT_REVIEW_{terminal_status}"
    assert cancellation["request_identity"] == f"review://{review['review_id']}"
    with sqlite3.connect(store.db_path) as conn:
        node_status = conn.execute(
            """
            SELECT status FROM task_runtime_node
            WHERE graph_id = 'graph-a' AND graph_revision = 1 AND node_id = 'node-a'
            """
        ).fetchone()[0]
        graph_status = conn.execute(
            "SELECT status FROM task_runtime_graph WHERE graph_id = 'graph-a' AND revision = 1"
        ).fetchone()[0]
    assert node_status == "CANCELLED"
    assert graph_status == "CANCELLED"


class _FailSecondFinalReceiptOnce:
    def __init__(self, delegate: RuntimeEventStore) -> None:
        self.delegate = delegate
        self.db_path = delegate.db_path
        self.final_count = 0
        self.failed = False

    def append_event(self, event):
        if event.event_type == "IntentAdmissionArbitrated":
            self.final_count += 1
            if self.final_count == 2 and not self.failed:
                self.failed = True
                raise RuntimeError("injected process crash before second final receipt")
        return self.delegate.append_event(event)

    def get_event_by_id(self, *, event_id: str):
        return self.delegate.get_event_by_id(event_id=event_id)


def test_multinode_partial_final_receipts_recover_from_prepared_set_after_grant_expiry(
    tmp_path: Path,
) -> None:
    record, scene, _binding, ledger = _authority_fixture()
    graph = record["graph"]
    work_order = record["work_order"]
    assert isinstance(graph, TaskGraph)
    assert isinstance(work_order, WorkOrderV3)
    second = replace(
        graph.nodes[0],
        node_id="node-b",
        dependencies=("node-a",),
        status=TaskNodeStatus.BLOCKED,
    )
    graph = replace(graph, nodes=(*graph.nodes, second), result_node_id="node-b")
    metadata = deepcopy(work_order.metadata)
    metadata["planning_lineage"]["node_bindings"].append(
        {
            "node_id": "node-b",
            "objective": "verify hello",
            "worker_route": "chat",
            "target_id": "surface_reply",
        }
    )
    work_order = replace(work_order, metadata=metadata)
    record["graph"] = graph
    record["work_order"] = work_order
    clock = [NOW_MS]
    durable_store = _runtime_events(tmp_path, clock=lambda: clock[0])
    crashing_store = _FailSecondFinalReceiptOnce(durable_store)
    verifier, _, _ = _intent_admission_verifier(
        record=record,
        scene=scene,
        ledger=ledger,
        event_store=crashing_store,  # type: ignore[arg-type]
        clock=clock,
    )

    with pytest.raises(IntentAdmissionError) as crashed:
        verifier.verify_intent_admission(
            command=record["command"],  # type: ignore[arg-type]
            work_order=work_order,
            graph=graph,
            replayed=False,
        )
    assert crashed.value.reason_code == INTENT_ADMISSION_INVALID
    assert len(
        durable_store.list_events(event_type="IntentAdmissionReceiptSetPrepared")
    ) == 1
    assert len(durable_store.list_events(event_type="IntentAdmissionArbitrated")) == 1

    # The parent grant is now expired.  The exact graph-level preparation proof
    # permits repair of only the missing final receipt; no arbitration authority
    # or live parent grant is reopened.
    clock[0] = 20_000
    restarted, repository, _ = _intent_admission_verifier(
        record=record,
        scene=scene,
        ledger=ledger,
        event_store=RuntimeEventStore(durable_store.db_path, clock=lambda: clock[0]),
        clock=clock,
    )
    assert restarted.has_prepared_intent_receipt_set(
        command=record["command"],  # type: ignore[arg-type]
        work_order=work_order,
        graph=graph,
    ) is True
    result = restarted.verify_intent_admission(
        command=record["command"],  # type: ignore[arg-type]
        work_order=work_order,
        graph=graph,
        replayed=True,
    )
    assert {item["task_node_id"] for item in result} == {"node-a", "node-b"}
    assert len(durable_store.list_events(event_type="IntentAdmissionArbitrated")) == 2
    assert len(
        durable_store.list_events(event_type="IntentAdmissionReceiptSetCompleted")
    ) == 1
    # One query for the explicit prepared-set proof and one for verification;
    # no per-node canonical arbitration lookups occur after expiry.
    assert repository.queries == [("graph-a", 1), ("graph-a", 1)]
