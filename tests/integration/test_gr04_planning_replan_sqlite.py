from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.app.governed_task_adapter import GovernedHostRuntime
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphStatus,
    TaskNodeStatus,
)
from host_brain.governor.action_arbiter import build_governed_action_binding
from host_brain.orchestration.governor_grant import governor_grant_ref
from host_brain.orchestration.failure_replan_intent import (
    FAILURE_REPLAN_SCHEMA_VERSION,
)
from host_brain.orchestration.plan_intent_search import build_plan_intent_search
from host_brain.orchestration.planning_admission import GovernedPlanningAdmissionService
from host_brain.orchestration.planning_execution import (
    GovernedPlanningExecutionService,
    PlanningExecutionAuthority,
)
from host_brain.orchestration.planning_graph_compiler import PlanningGraphCompiler
from host_brain.orchestration.planning_recovery import (
    PLANNING_RECOVERY_NOT_REPLANNABLE,
    GovernedPlanningRecoveryService,
    PlanningRecoveryError,
)
from host_brain.persistence.host_command_repository import HostCommandRepository
from host_brain.persistence.task_repository import (
    DurableTaskStore,
    OutboxMessage,
    TASK_STORE_CAS_CONFLICT,
    TaskStoreError,
    build_outbox_payload_ref,
)
from natsume_contracts.task import HostCommandSurface


class _UnusedCommandApplication:
    def __init__(self) -> None:
        self.calls = 0

    def preflight_parent_governor_binding(self, **_kwargs):  # noqa: ANN003
        return None

    async def execute_stream(self, **_kwargs):  # noqa: ANN003
        self.calls += 1
        if False:
            yield {}


class _CapturingCommandApplication:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def preflight_parent_governor_binding(self, **_kwargs):  # noqa: ANN003
        return None

    async def execute_stream(self, **kwargs):  # noqa: ANN003
        self.calls.append(dict(kwargs))
        yield {
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE",
            "narrative": "captured immutable graph",
        }


def _approved_intent() -> tuple[dict, dict]:
    intent = {
        "schema_version": "Natsume.intent.v1",
        "id": "intent-gr04-sqlite-replan",
        "request_id": "request-gr04-sqlite-replan",
        "ts_ms": 1_000,
        "session_id": "session-gr04-sqlite-replan",
        "seq": 11,
        "person_id": "person-gr04-sqlite-replan",
        "principal_id": "person-gr04-sqlite-replan",
        "type": "query",
        "action": "exec_intent",
        "route": "plan",
        "target": "host",
        "target_id": "planning_search",
        "task": "inspect and verify the repository",
        "args": {"task": "inspect and verify the repository"},
        "planned_steps": [
            {
                "candidate_id": "inspect",
                "step_index": 1,
                "route": "skill",
                "target_id": "agent_worker",
                "task_description": "inspect the repository",
                "dependencies": [],
                "allow_parallel": True,
            },
            {
                "candidate_id": "verify",
                "step_index": 2,
                "route": "delegate",
                "target_id": "intent_agent",
                "task_description": "verify the inspected result",
                "dependencies": ["inspect"],
            },
        ],
        "nonce": "nonce-gr04-sqlite-replan",
    }
    binding = build_governed_action_binding(
        intent_envelope=intent,
        approval_token_ref="approval-secret-not-persisted-in-search",
        risk_level=3,
        issued_at_ms=1_000,
        expires_at_ms=601_000,
    )
    governor = {
        "request_id": intent["request_id"],
        "intent_id": intent["id"],
        "decision": "APPROVE",
        "reason_code": "APPROVED",
        "approved_action_binding": binding,
        "ts_ms": 1_000,
    }
    return intent, governor


def _approved_replan_intent(*, graph_id: str) -> tuple[dict, dict]:
    intent, _governor = _approved_intent()
    intent = {
        **intent,
        "id": "intent-gr04-production-replan",
        "request_id": "request-gr04-production-replan",
        "ts_ms": 2_000,
        "nonce": "nonce-gr04-production-replan",
        "task": "inspect again and verify after durable failure",
        "args": {"task": "inspect again and verify after durable failure"},
        "failure_replan": {
            "schema_version": FAILURE_REPLAN_SCHEMA_VERSION,
            "graph_id": graph_id,
            "previous_revision": 1,
        },
    }
    binding = build_governed_action_binding(
        intent_envelope=intent,
        approval_token_ref="approval-secret-not-persisted-in-replan",
        risk_level=3,
        issued_at_ms=2_000,
        expires_at_ms=602_000,
    )
    governor = {
        "request_id": intent["request_id"],
        "intent_id": intent["id"],
        "decision": "APPROVE",
        "reason_code": "APPROVED",
        "approved_action_binding": binding,
        "ts_ms": 2_000,
    }
    return intent, governor


def _graph_outbox(graph_id: str, *, version: int, status: str) -> OutboxMessage:
    return OutboxMessage.create(
        aggregate_type="TASK_GRAPH",
        aggregate_id=graph_id,
        graph_revision=1,
        aggregate_version=version,
        event_type=f"TASK_GRAPH_{status}",
        payload_ref=build_outbox_payload_ref(
            aggregate_type="TASK_GRAPH",
            aggregate_id=graph_id,
            aggregate_version=version,
            graph_revision=1,
        ),
        idempotency_key=f"gr04-replan:{graph_id}:{version}:{status}",
        outbox_id=f"outbox::gr04-replan::{graph_id}::{version}::{status}",
    )


def _node_outbox(
    graph_id: str,
    node_id: str,
    *,
    version: int,
    status: str,
) -> OutboxMessage:
    aggregate_id = f"{graph_id}@1/{node_id}"
    return OutboxMessage.create(
        aggregate_type="TASK_NODE",
        aggregate_id=aggregate_id,
        aggregate_version=version,
        event_type=f"TASK_NODE_{status}",
        payload_ref=build_outbox_payload_ref(
            aggregate_type="TASK_NODE",
            aggregate_id=aggregate_id,
            aggregate_version=version,
        ),
        idempotency_key=f"gr04-replan-node:{aggregate_id}:{version}:{status}",
        outbox_id=f"outbox::gr04-replan-node::{aggregate_id}::{version}::{status}",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("terminal_status", "should_replan"),
    [
        (TaskGraphStatus.FAILED, True),
        (TaskGraphStatus.BLOCKED, False),
    ],
)
async def test_real_sqlite_only_failed_revision_admits_exact_revision_plus_one(
    tmp_path,
    terminal_status: TaskGraphStatus,
    should_replan: bool,
) -> None:
    db_path = tmp_path / "planning-replan.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="gr04-replan-migrate",
    )
    assert migrated.success is True, migrated.as_dict()

    intent, governor = _approved_intent()
    adapter = build_plan_intent_search(intent, governor_result=governor)
    commands = HostCommandRepository(db_path)
    store = DurableTaskStore(db_path)
    command_application = _UnusedCommandApplication()
    service = GovernedPlanningExecutionService(
        search=adapter,  # type: ignore[arg-type]
        compiler=PlanningGraphCompiler(),
        admission=GovernedPlanningAdmissionService(
            command_repository=commands,
            runtime_router=CanonicalRuntimeRouter(),
        ),
        command_application=command_application,
    )
    grant_ref = governor_grant_ref(intent["request_id"], governor)
    authority = PlanningExecutionAuthority(
        surface=HostCommandSurface.CHAT,
        base_command_request_id=intent["request_id"],
        session_id=intent["session_id"],
        principal_id=intent["principal_id"],
        input_ref="input://sha256/" + "a" * 64,
        input_sha256="sha256:" + "a" * 64,
        governor_grant_ref=grant_ref,
        target_id="planning_search",
    )

    initial = await service.prepare_initial(
        adapter.request,
        authority=authority,
        created_at=100,
    )
    graph_id = initial.admission.graph.graph_id
    active = store.transition_graph(
        graph_id,
        1,
        expected_version=0,
        target_status=TaskGraphStatus.ACTIVE,
        outbox=_graph_outbox(graph_id, version=1, status="ACTIVE"),
    )
    assert active["status"] == TaskGraphStatus.ACTIVE.value
    terminal = store.transition_graph(
        graph_id,
        1,
        expected_version=1,
        target_status=terminal_status,
        outbox=_graph_outbox(
            graph_id,
            version=2,
            status=terminal_status.value,
        ),
    )
    previous_graph = TaskGraph.from_mapping(terminal["graph"])
    assert previous_graph.status is terminal_status
    failure_ref = build_outbox_payload_ref(
        aggregate_type="TASK_GRAPH",
        aggregate_id=graph_id,
        aggregate_version=2,
        graph_revision=1,
    )

    recovery = GovernedPlanningRecoveryService(
        execution_service=service,
        task_store=store,
    )
    if not should_replan:
        with pytest.raises(PlanningRecoveryError) as raised:
            await recovery.prepare_failure_replan(
                adapter.request,
                authority=authority,
                graph_id=graph_id,
                previous_revision=1,
                created_at=200,
            )
        assert raised.value.reason_code == PLANNING_RECOVERY_NOT_REPLANNABLE
        with sqlite3.connect(db_path) as conn:
            revision_count = conn.execute(
                "SELECT COUNT(*) FROM host_command_journal"
            ).fetchone()[0]
        assert revision_count == 1
        assert command_application.calls == 0
        return

    replanned = await recovery.prepare_failure_replan(
        adapter.request,
        authority=authority,
        graph_id=graph_id,
        previous_revision=1,
        created_at=200,
    )

    assert replanned.admission.graph.graph_id == graph_id
    assert replanned.admission.graph.root_work_order_id == previous_graph.root_work_order_id
    assert replanned.admission.graph.revision == 2
    assert replanned.compiled.previous_revision == 1
    assert replanned.compiled.failure_evidence_refs[0] == failure_ref
    assert command_application.calls == 0
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT root_work_order_id, graph_id, graph_revision, assignment_id
            FROM host_command_journal
            ORDER BY graph_revision
            """
        ).fetchall()
        supersession = conn.execute(
            """
            SELECT schema_version, graph_id, previous_revision,
                   replacement_revision, previous_command_id,
                   replacement_command_id, previous_graph_status_version,
                   failure_evidence_json, failure_evidence_sha256
            FROM task_runtime_graph_revision_supersession
            """
        ).fetchone()
    assert len(rows) == 2
    assert rows[0][0] == rows[1][0] == previous_graph.root_work_order_id
    assert rows[0][1] == rows[1][1] == graph_id
    assert [row[2] for row in rows] == [1, 2]
    assert rows[0][3] == rows[1][3]
    assert supersession is not None
    assert supersession[0] == "Natsume.task_runtime.graph_revision_supersession.v1"
    assert supersession[1:4] == (graph_id, 1, 2)
    assert supersession[4] != supersession[5]
    assert supersession[6] == 2
    assert failure_ref in supersession[7]
    assert supersession[8].startswith("sha256:")

    with sqlite3.connect(db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                """
                UPDATE task_runtime_graph_revision_supersession
                SET created_ts_ms = created_ts_ms + 1
                """
            )
        conn.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM task_runtime_graph_revision_supersession")
        conn.rollback()

    old_node = store.get_node(graph_id, 1, previous_graph.nodes[0].node_id)
    with pytest.raises(TaskStoreError) as fenced:
        store.transition_node(
            graph_id,
            1,
            previous_graph.nodes[0].node_id,
            expected_version=old_node["status_version"],
            target_status=TaskNodeStatus.WAITING_APPROVAL,
            outbox=_node_outbox(
                graph_id,
                previous_graph.nodes[0].node_id,
                version=old_node["status_version"] + 1,
                status="WAITING_APPROVAL",
            ),
        )
    assert fenced.value.reason_code == TASK_STORE_CAS_CONFLICT
    assert "superseded Graph revision" in fenced.value.detail

    restarted = GovernedPlanningExecutionService(
        search=adapter,  # type: ignore[arg-type]
        compiler=PlanningGraphCompiler(),
        admission=GovernedPlanningAdmissionService(
            command_repository=HostCommandRepository(db_path),
            runtime_router=CanonicalRuntimeRouter(),
        ),
        command_application=_UnusedCommandApplication(),
    )
    restarted_recovery = GovernedPlanningRecoveryService(
        execution_service=restarted,
        task_store=DurableTaskStore(db_path),
    )
    replay = await restarted_recovery.prepare_failure_replan(
        adapter.request,
        authority=authority,
        graph_id=graph_id,
        previous_revision=1,
        created_at=200,
    )
    assert replay.admission.replayed is True
    assert replay.admission.graph.to_dict() == replanned.admission.graph.to_dict()
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_graph_revision_supersession"
        ).fetchone() == (1,)


@pytest.mark.asyncio
async def test_replan_supersession_fault_rolls_back_replacement_and_authority(
    tmp_path,
) -> None:
    db_path = tmp_path / "planning-replan-atomic.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="gr04-replan-atomic-migrate",
    )
    assert migrated.success is True, migrated.as_dict()

    intent, governor = _approved_intent()
    adapter = build_plan_intent_search(intent, governor_result=governor)
    store = DurableTaskStore(db_path)
    authority = PlanningExecutionAuthority(
        surface=HostCommandSurface.CHAT,
        base_command_request_id=intent["request_id"],
        session_id=intent["session_id"],
        principal_id=intent["principal_id"],
        input_ref="input://sha256/" + "d" * 64,
        input_sha256="sha256:" + "d" * 64,
        governor_grant_ref=governor_grant_ref(intent["request_id"], governor),
        target_id="planning_search",
    )

    initial_service = GovernedPlanningExecutionService(
        search=adapter,  # type: ignore[arg-type]
        compiler=PlanningGraphCompiler(),
        admission=GovernedPlanningAdmissionService(
            command_repository=HostCommandRepository(db_path),
            runtime_router=CanonicalRuntimeRouter(),
        ),
        command_application=_UnusedCommandApplication(),
    )
    initial = await initial_service.prepare_initial(
        adapter.request,
        authority=authority,
        created_at=100,
    )
    graph_id = initial.admission.graph.graph_id
    store.transition_graph(
        graph_id,
        1,
        expected_version=0,
        target_status=TaskGraphStatus.ACTIVE,
        outbox=_graph_outbox(graph_id, version=1, status="ACTIVE"),
    )
    store.transition_graph(
        graph_id,
        1,
        expected_version=1,
        target_status=TaskGraphStatus.FAILED,
        outbox=_graph_outbox(graph_id, version=2, status="FAILED"),
    )

    def fault(stage: str) -> None:
        if stage == "after_supersession":
            raise RuntimeError("lost response after supersession insert")

    fault_service = GovernedPlanningExecutionService(
        search=adapter,  # type: ignore[arg-type]
        compiler=PlanningGraphCompiler(),
        admission=GovernedPlanningAdmissionService(
            command_repository=HostCommandRepository(
                db_path,
                fault_injector=fault,
            ),
            runtime_router=CanonicalRuntimeRouter(),
        ),
        command_application=_UnusedCommandApplication(),
    )
    recovery = GovernedPlanningRecoveryService(
        execution_service=fault_service,
        task_store=store,
    )
    with pytest.raises(RuntimeError, match="lost response"):
        await recovery.prepare_failure_replan(
            adapter.request,
            authority=authority,
            graph_id=graph_id,
            previous_revision=1,
            created_at=200,
        )

    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM host_command_journal"
        ).fetchone() == (1,)
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_graph"
        ).fetchone() == (1,)
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_graph_revision_supersession"
        ).fetchone() == (0,)


@pytest.mark.asyncio
async def test_governed_host_plan_route_exposes_only_explicit_failure_replan(
    tmp_path,
) -> None:
    db_path = tmp_path / "planning-replan-production-entry.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="gr04-production-replan-migrate",
    )
    assert migrated.success is True, migrated.as_dict()

    commands = HostCommandRepository(db_path)
    store = DurableTaskStore(db_path)
    application = _CapturingCommandApplication()
    runtime = GovernedHostRuntime(
        application=application,  # type: ignore[arg-type]
        runtime_router=CanonicalRuntimeRouter(),
        command_repository=commands,
        task_store=store,
        supported_routes=frozenset({"chat", "skill", "delegate"}),
        planning_enabled=True,
    )
    await runtime.activate()
    try:
        intent, governor = _approved_intent()
        initial_events = [
            event
            async for event in runtime.execute_stream(
                request_body=SimpleNamespace(),
                request_id=intent["request_id"],
                session_id=intent["session_id"],
                ts_ms=intent["ts_ms"],
                intent_draft=intent,
                governor_result=governor,
                person_id=intent["person_id"],
                sanitized_message=intent["task"],
            )
        ]
        assert initial_events[-1]["narrative"] == "captured immutable graph"
        initial_graph = application.calls[-1]["graph"]
        graph_id = initial_graph.graph_id
        assert initial_graph.revision == 1

        active = store.transition_graph(
            graph_id,
            1,
            expected_version=0,
            target_status=TaskGraphStatus.ACTIVE,
            outbox=_graph_outbox(graph_id, version=1, status="ACTIVE"),
        )
        assert active["status"] == "ACTIVE"
        failed = store.transition_graph(
            graph_id,
            1,
            expected_version=1,
            target_status=TaskGraphStatus.FAILED,
            outbox=_graph_outbox(graph_id, version=2, status="FAILED"),
        )
        immutable_failed = TaskGraph.from_mapping(failed["graph"]).to_dict()

        replan_intent, replan_governor = _approved_replan_intent(graph_id=graph_id)
        replan_events = [
            event
            async for event in runtime.execute_stream(
                request_body=SimpleNamespace(),
                request_id=replan_intent["request_id"],
                session_id=replan_intent["session_id"],
                ts_ms=replan_intent["ts_ms"],
                intent_draft=replan_intent,
                governor_result=replan_governor,
                person_id=replan_intent["person_id"],
                sanitized_message=replan_intent["task"],
            )
        ]
        assert replan_events[-1]["narrative"] == "captured immutable graph"
        replacement = application.calls[-1]["graph"]
        assert replacement.graph_id == graph_id
        assert replacement.root_work_order_id == initial_graph.root_work_order_id
        assert replacement.revision == 2
        assert TaskGraph.from_mapping(store.get_graph(graph_id, 1)["graph"]).to_dict() == immutable_failed

        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                """
                SELECT graph_id, graph_revision, root_work_order_id, request_id
                FROM host_command_journal
                ORDER BY graph_revision
                """
            ).fetchall()
        assert [(row[0], row[1]) for row in rows] == [(graph_id, 1), (graph_id, 2)]
        assert rows[0][2] == rows[1][2] == initial_graph.root_work_order_id
        assert rows[1][3].endswith(".graph-revision-2")
    finally:
        await runtime.aclose()
