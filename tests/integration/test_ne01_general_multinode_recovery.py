from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraphCompletionPolicy,
)
from host_brain.orchestration import GovernedCommandApplication
from host_brain.persistence.host_command_repository import HostCommandRepository
from host_brain.persistence.task_repository import DurableTaskStore
from host_brain.persistence.worker_projection_repository import WorkerProjectionStore
from host_brain.task_runtime import AdmittedExecution
from tests.integration.test_gr03a_governed_durable_dag import _migrated_db
from tests.integration.test_ne01_multinode_budget_settlement_hold import (
    _CheckpointNodeGrantService,
    _CheckpointNodeGrantVerifier,
    _ParallelCommittedCheckpointDoubleLossStore,
    _TakeoverOrderStore,
)
from tests.integration.test_ne01_operation_online_takeover import (
    _Clock,
    _ProjectedWorker,
    _contracts,
    _coordinator,
    _events,
)


def _with_parent_grant(contracts):
    return (
        replace(
            contracts[0],
            governor_grant_ref="governor-grant://general-recovery/parent",
        ),
        *contracts[1:],
    )


@pytest.mark.asyncio
async def test_diamond_graph_recovers_middle_wave_and_releases_dependencies_once(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    node_ids = ("root", "left", "right", "join", "tail")
    contracts = _with_parent_grant(
        _contracts(
            (
                ("root", ()),
                ("left", ("root",)),
                ("right", ("root",)),
                ("join", ("left", "right")),
                ("tail", ("join",)),
            ),
            concurrency=2,
            completion_policy=TaskGraphCompletionPolicy.ATOMIC_GRAPH_SUCCESS_V1,
        )
    )
    admitted = AdmittedExecution.from_contracts(
        command=contracts[0],
        assignment=contracts[2],
        graph=contracts[3],
    )
    clock = _Clock()
    calls: list[str] = []
    projections = WorkerProjectionStore(db_path, clock=clock)
    worker = _ProjectedWorker(
        projections=projections,
        calls=calls,
        delay_seconds=0.01,
    )
    durable = DurableTaskStore(db_path, clock=clock)
    first_store = _ParallelCommittedCheckpointDoubleLossStore(
        durable,
        node_ids={"left", "right"},
        consume_order=("left", "right"),
    )
    first_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=first_store,
            projections=projections,
            worker=worker,
            owner_id="diamond-recovery-owner-a",
            bounded_fanout_enabled=True,
            atomic_graph_success_enabled=True,
            node_grant_verifier=_CheckpointNodeGrantVerifier(),
        ),
        node_grant_service=_CheckpointNodeGrantService(),
    )

    first = await _events(first_app, contracts)

    assert first[-1]["execution_receipt"]["status"] == "RECOVERY_REQUIRED"
    assert set(calls) == {"root", "left", "right"}
    assert calls.count("root") == calls.count("left") == calls.count("right") == 1
    held = durable.read_scheduler_snapshot(
        admitted.graph_id,
        admitted.graph_revision,
    )
    assert held["graph_record"]["status"] == "ACTIVE"
    assert durable.get_node(
        admitted.graph_id,
        admitted.graph_revision,
        "join",
    )["status"] == "BLOCKED"
    assert durable.get_node(
        admitted.graph_id,
        admitted.graph_revision,
        "tail",
    )["status"] == "BLOCKED"

    clock.advance(25)
    reopened = DurableTaskStore(db_path, clock=clock)
    ordered = _TakeoverOrderStore(reopened)
    recovered_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=ordered,
            projections=WorkerProjectionStore(db_path, clock=clock),
            worker=worker,
            owner_id="diamond-recovery-owner-b",
            bounded_fanout_enabled=True,
            atomic_graph_success_enabled=True,
            node_grant_verifier=_CheckpointNodeGrantVerifier(),
        ),
        node_grant_service=_CheckpointNodeGrantService(),
    )

    recovered = await _events(recovered_app, contracts)

    assert recovered[-1]["execution_receipt"]["status"] == "ACCEPTED"
    assert ordered.node_ids == ["left", "right"]
    assert sorted(calls) == sorted(node_ids)
    assert all(calls.count(node_id) == 1 for node_id in node_ids)
    assert calls.index("join") > max(calls.index("left"), calls.index("right"))
    assert calls.index("tail") > calls.index("join")
    final = reopened.read_scheduler_snapshot(
        admitted.graph_id,
        admitted.graph_revision,
    )
    assert final["graph_record"]["status"] == "COMPLETED"
    assert final["budgets"][0]["status"] == "CONSUMED"
    assert final["active_leases"] == []
    assert {
        reopened.get_node(
            admitted.graph_id,
            admitted.graph_revision,
            node_id,
        )["status"]
        for node_id in node_ids
    } == {"ACCEPTED"}

    replay = await _events(recovered_app, contracts)
    assert replay[-1]["execution_receipt"]["status"] == "ACCEPTED"
    assert all(calls.count(node_id) == 1 for node_id in node_ids)


@pytest.mark.asyncio
async def test_eight_root_checkpoints_recover_without_fixed_four_node_ceiling(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    node_ids = tuple(f"root-{index:02d}" for index in range(8))
    contracts = _with_parent_grant(
        _contracts(
            tuple((node_id, ()) for node_id in node_ids),
            concurrency=8,
            token_limit=100,
            completion_policy=TaskGraphCompletionPolicy.ATOMIC_GRAPH_SUCCESS_V1,
        )
    )
    admitted = AdmittedExecution.from_contracts(
        command=contracts[0],
        assignment=contracts[2],
        graph=contracts[3],
    )
    clock = _Clock()
    calls: list[str] = []
    projections = WorkerProjectionStore(db_path, clock=clock)
    worker = _ProjectedWorker(
        projections=projections,
        calls=calls,
        delay_seconds=0.01,
    )
    durable = DurableTaskStore(db_path, clock=clock)
    first_store = _ParallelCommittedCheckpointDoubleLossStore(
        durable,
        node_ids=set(node_ids),
        consume_order=node_ids,
    )
    first_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=first_store,
            projections=projections,
            worker=worker,
            owner_id="wide-recovery-owner-a",
            bounded_fanout_enabled=True,
            atomic_graph_success_enabled=True,
            node_grant_verifier=_CheckpointNodeGrantVerifier(),
        ),
        node_grant_service=_CheckpointNodeGrantService(),
    )

    first = await _events(first_app, contracts)

    assert first[-1]["execution_receipt"]["status"] == "RECOVERY_REQUIRED"
    assert sorted(calls) == sorted(node_ids)
    assert all(calls.count(node_id) == 1 for node_id in node_ids)

    clock.advance(25)
    reopened = DurableTaskStore(db_path, clock=clock)
    ordered = _TakeoverOrderStore(reopened)
    recovered_app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=clock),
        coordinator=_coordinator(
            store=ordered,
            projections=WorkerProjectionStore(db_path, clock=clock),
            worker=worker,
            owner_id="wide-recovery-owner-b",
            bounded_fanout_enabled=True,
            atomic_graph_success_enabled=True,
            node_grant_verifier=_CheckpointNodeGrantVerifier(),
        ),
        node_grant_service=_CheckpointNodeGrantService(),
    )

    recovered = await _events(recovered_app, contracts)

    assert recovered[-1]["execution_receipt"]["status"] == "ACCEPTED"
    assert ordered.node_ids == list(node_ids)
    assert all(calls.count(node_id) == 1 for node_id in node_ids)
    final = reopened.read_scheduler_snapshot(
        admitted.graph_id,
        admitted.graph_revision,
    )
    assert final["graph_record"]["status"] == "COMPLETED"
    assert final["budgets"][0]["status"] == "CONSUMED"
    assert final["active_leases"] == []
    assert all(
        reopened.get_node(
            admitted.graph_id,
            admitted.graph_revision,
            node_id,
        )["status"]
        == "ACCEPTED"
        for node_id in node_ids
    )
