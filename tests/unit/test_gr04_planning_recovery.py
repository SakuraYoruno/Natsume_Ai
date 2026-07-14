from __future__ import annotations

from dataclasses import replace

import pytest

from host_brain.contracts.agent_runtime.task_graph import (
    TASK_GRAPH_SCHEMA_VERSION,
    TASK_NODE_SCHEMA_VERSION,
    TaskGraph,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
)
from host_brain.orchestration.planning_recovery import (
    PLANNING_RECOVERY_DURABLE_DRIFT,
    PLANNING_RECOVERY_NOT_REPLANNABLE,
    DurablePlanningFailureSource,
    GovernedPlanningRecoveryService,
    PlanningRecoveryError,
)
from host_brain.orchestration.planning_execution import (
    GovernedPlanningExecutionService,
)


class _SnapshotStore:
    def __init__(self, snapshot: dict) -> None:
        self.snapshot = snapshot
        self.calls: list[tuple[str, int]] = []

    def read_scheduler_snapshot(self, graph_id: str, revision: int) -> dict:
        self.calls.append((graph_id, revision))
        return self.snapshot


class _ExecutionSpy(GovernedPlanningExecutionService):
    def __init__(self) -> None:
        self.prepare_calls = 0

    async def prepare_replan(self, *args, **kwargs):  # noqa: ANN002, ANN003
        self.prepare_calls += 1
        raise AssertionError("live graph must be rejected before PlanningSearch")


def _graph(status: TaskGraphStatus) -> TaskGraph:
    node_status = (
        TaskNodeStatus.FAILED
        if status is TaskGraphStatus.FAILED
        else TaskNodeStatus.WAITING_APPROVAL
    )
    node = TaskNode(
        schema_version=TASK_NODE_SCHEMA_VERSION,
        node_id="node-recover",
        dependencies=(),
        route="skill",
        input_refs=("artifact://input/recovery",),
        output_contract={"type": "result"},
        retry_policy={"max_attempts": 1},
        risk_level=3,
        acceptance_criteria=("recovery verified",),
        status=node_status,
        status_version=3,
    )
    return TaskGraph(
        schema_version=TASK_GRAPH_SCHEMA_VERSION,
        graph_id="graph-recovery",
        root_work_order_id="work-order-recovery",
        revision=1,
        planner_ref="planner://recovery/revision-1",
        status=status,
        created_at=100,
        nodes=(node,),
    )


def _snapshot(status: TaskGraphStatus) -> dict:
    graph = _graph(status)
    node = graph.nodes[0]
    return {
        "graph_record": {
            "graph_id": graph.graph_id,
            "revision": graph.revision,
            "root_work_order_id": graph.root_work_order_id,
            "planner_ref": graph.planner_ref,
            "status": graph.status.value,
            "status_version": 2,
            "source_kind": "CANONICAL",
            "graph": graph.to_dict(),
            "nodes": [
                {
                    "graph_id": graph.graph_id,
                    "graph_revision": graph.revision,
                    "node_id": node.node_id,
                    "status": node.status.value,
                    "status_version": node.status_version,
                    "node": node.to_dict(),
                }
            ],
        },
        "attempts": [
            {
                "attempt_id": "attempt-recovery-1",
                "graph_id": graph.graph_id,
                "graph_revision": graph.revision,
                "node_id": node.node_id,
                "status": "FAILED",
                "status_version": 4,
                "error_ref": "evidence://attempt/recovery-1",
            }
        ],
        "active_leases": [],
        "budgets": [],
        "budget_usage": [],
    }


@pytest.mark.asyncio
async def test_loads_failed_graph_and_derives_only_durable_failure_evidence() -> None:
    store = _SnapshotStore(_snapshot(TaskGraphStatus.FAILED))

    failure = await DurablePlanningFailureSource(store).load(
        graph_id="graph-recovery",
        previous_revision=1,
    )

    assert store.calls == [("graph-recovery", 1)]
    assert failure.previous_graph.status is TaskGraphStatus.FAILED
    assert failure.failure_evidence_refs == (
        "taskstore://event/TASK_GRAPH/graph-recovery/2",
        "taskstore://event/TASK_NODE/graph-recovery@1/node-recover/3",
        "taskstore://event/TASK_ATTEMPT/attempt-recovery-1/4",
        "evidence://attempt/recovery-1",
    )


@pytest.mark.asyncio
async def test_blocked_graph_is_replannable_without_mutating_durable_snapshot() -> None:
    snapshot = _snapshot(TaskGraphStatus.BLOCKED)
    before = snapshot["graph_record"]["graph"].copy()

    failure = await DurablePlanningFailureSource(_SnapshotStore(snapshot)).load(
        graph_id="graph-recovery",
        previous_revision=1,
    )

    assert failure.previous_graph.status is TaskGraphStatus.BLOCKED
    assert failure.failure_evidence_refs[0] == (
        "taskstore://event/TASK_GRAPH/graph-recovery/2"
    )
    assert snapshot["graph_record"]["graph"] == before


@pytest.mark.asyncio
async def test_live_graph_is_rejected_before_any_search_or_admission() -> None:
    snapshot = _snapshot(TaskGraphStatus.BLOCKED)
    graph = TaskGraph.from_mapping(snapshot["graph_record"]["graph"])
    active = replace(graph, status=TaskGraphStatus.ACTIVE)
    snapshot["graph_record"]["graph"] = active.to_dict()
    snapshot["graph_record"]["status"] = TaskGraphStatus.ACTIVE.value

    execution = _ExecutionSpy()
    recovery = GovernedPlanningRecoveryService(
        execution_service=execution,
        task_store=_SnapshotStore(snapshot),
    )
    with pytest.raises(PlanningRecoveryError) as raised:
        await recovery.prepare_failure_replan(
            object(),  # type: ignore[arg-type]
            authority=object(),  # type: ignore[arg-type]
            graph_id="graph-recovery",
            previous_revision=1,
            created_at=200,
        )

    assert raised.value.reason_code == PLANNING_RECOVERY_NOT_REPLANNABLE
    assert execution.prepare_calls == 0


@pytest.mark.asyncio
async def test_sql_json_identity_drift_is_rejected_fail_closed() -> None:
    snapshot = _snapshot(TaskGraphStatus.FAILED)
    snapshot["graph_record"]["graph_id"] = "graph-forged"

    with pytest.raises(PlanningRecoveryError) as raised:
        await DurablePlanningFailureSource(_SnapshotStore(snapshot)).load(
            graph_id="graph-recovery",
            previous_revision=1,
        )

    assert raised.value.reason_code == PLANNING_RECOVERY_DURABLE_DRIFT
