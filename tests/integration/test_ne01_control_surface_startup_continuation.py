from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any, Mapping

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.app.host_startup_graph_continuation import (
    HostStartupGraphContinuation,
)
from host_brain.app.periodic_durable_continuation import (
    PeriodicDurableContinuation,
)
from host_brain.composition.runtime_group import GovernedRuntimeGroup
from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetAmount,
    BudgetPolicy,
)
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
)
from host_brain.contracts.agent_runtime.work_order import WorkOrderV3
from host_brain.persistence.host_command_repository import HostCommandRepository
from host_brain.persistence.task_repository import DurableTaskStore
from natsume_contracts.task import (
    ExecutorAssignment,
    HostCommandSurface,
    TaskExecutor,
    build_host_command,
)


NOW = 1_900_000_000_000
CONTROL_SURFACES = (
    HostCommandSurface.INTERNAL,
    HostCommandSurface.SCHEDULER,
    HostCommandSurface.OPERATOR,
)


def _contracts(
    index: int,
    surface: HostCommandSurface,
) -> tuple[Any, WorkOrderV3, ExecutorAssignment, TaskGraph]:
    suffix = f"{index:04d}-{surface.value}"
    graph_id = f"graph-control-survivor-{suffix}"
    work_order_id = f"work-control-survivor-{suffix}"
    node_id = f"node-control-survivor-{suffix}"
    fingerprint = "sha256:" + hashlib.sha256(
        suffix.encode("utf-8")
    ).hexdigest()
    grant_ref = f"governor-grant://control-survivor/{suffix}"
    output_contract = {
        "type": "control_survivor_result",
        "required": ["receipt_ref"],
    }
    work_order = WorkOrderV3(
        work_order_id=work_order_id,
        domain="organ_ops",
        run_mode="worker",
        objective="recover the exact durable control-surface graph",
        acceptance_criteria=("control result accepted",),
        metadata={"surface": surface.value},
        root_work_order_id=work_order_id,
        task_graph_id=graph_id,
        task_node_id=node_id,
        graph_revision=1,
        risk_level=1,
        output_contract=output_contract,
        budget_policy=BudgetPolicy(
            scope=f"work-order:{work_order_id}",
            limits=BudgetAmount(
                token_count=1_000,
                tool_call_count=4,
                time_ms=30_000,
                concurrency=1,
                retry_count=1,
                model_cost_micros=10_000,
            ),
        ),
        business_idempotency_scope=f"control-survivor:{surface.value}",
        operation_fingerprint=fingerprint,
        approval_ref=grant_ref,
    )
    node = TaskNode(
        node_id=node_id,
        dependencies=(),
        route="delegate:control-survivor",
        input_refs=(grant_ref,),
        output_contract=output_contract,
        retry_policy={"max_attempts": 1, "retry_backoff_ms": 0},
        risk_level=1,
        acceptance_criteria=work_order.acceptance_criteria,
        status=TaskNodeStatus.READY,
    )
    graph = TaskGraph(
        graph_id=graph_id,
        root_work_order_id=work_order_id,
        revision=1,
        planner_ref="planner://governed-control-survivor/v1",
        status=TaskGraphStatus.CREATED,
        created_at=NOW + index,
        nodes=(node,),
        result_node_id=node_id,
    )
    target_id = (
        "operator_state_control"
        if surface is HostCommandSurface.OPERATOR
        else f"{surface.value}_control"
    )
    command = build_host_command(
        surface=surface,
        request_id=f"request-control-survivor-{suffix}",
        session_id="session-control-survivor",
        principal_id="principal-control-survivor",
        governor_grant_ref=grant_ref,
        input_ref=f"control-input://{suffix}",
        input_sha256="sha256:"
        + hashlib.sha256(f"input-{suffix}".encode("utf-8")).hexdigest(),
        root_work_order_id=work_order_id,
        graph_id=graph_id,
        graph_revision=1,
        route_type=(
            "operator"
            if surface is HostCommandSurface.OPERATOR
            else "delegate"
        ),
        target_id=target_id,
        business_scope=work_order.business_idempotency_scope,
        operation_fingerprint=fingerprint,
        created_at_ms=NOW + index,
    )
    assignment = ExecutorAssignment(
        work_order_id=work_order_id,
        executor=TaskExecutor.CANONICAL,
        assignment_id=f"runtime-assignment://control-survivor/{suffix}",
        assigned_at_ms=NOW + index,
        is_new_task=True,
        reason_code="RUNTIME_NEW_TASK_CANONICAL",
    )
    return command, work_order, assignment, graph


def _record(
    index: int,
    surface: HostCommandSurface,
) -> dict[str, Any]:
    command, work_order, assignment, graph = _contracts(index, surface)
    return {
        "command_id": command.command_id,
        "created_at_ms": command.created_at_ms,
        "graph_id": graph.graph_id,
        "graph_revision": graph.revision,
        "runtime_graph_status": graph.status.value,
        "command": command,
        "work_order": work_order,
        "assignment": assignment,
        "graph": graph,
    }


class _Inventory:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = sorted(
            records,
            key=lambda item: (item["created_at_ms"], item["command_id"]),
        )
        self.calls: list[dict[str, Any]] = []

    def list_nonterminal_admission_page(
        self,
        *,
        surfaces: tuple[str, ...],
        limit: int,
        cursor: dict[str, Any] | None,
    ) -> dict[str, Any]:
        copied_cursor = dict(cursor) if cursor is not None else None
        self.calls.append(
            {
                "surfaces": tuple(surfaces),
                "limit": int(limit),
                "cursor": copied_cursor,
            }
        )
        surface_set = set(surfaces)
        records = [
            record
            for record in self.records
            if record["command"].surface.value in surface_set
        ]
        if cursor is not None:
            cursor_key = (cursor["created_at_ms"], cursor["command_id"])
            records = [
                record
                for record in records
                if (record["created_at_ms"], record["command_id"]) > cursor_key
            ]
        page_records = records[:limit]
        has_more = len(records) > limit
        surfaces_sha256 = "sha256:" + hashlib.sha256(
            json.dumps(
                list(surfaces),
                ensure_ascii=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        next_cursor = None
        if has_more:
            tail = page_records[-1]
            next_cursor = {
                "schema_version": (
                    "Natsume.host.nonterminal_admission_cursor.v1"
                ),
                "surfaces_sha256": surfaces_sha256,
                "created_at_ms": tail["created_at_ms"],
                "command_id": tail["command_id"],
            }
        return {
            "schema_version": "Natsume.host.nonterminal_admission_page.v1",
            "surfaces_sha256": surfaces_sha256,
            "records": list(page_records),
            "next_cursor": next_cursor,
            "has_more": has_more,
        }


class _SnapshotStore:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = {
            (record["graph_id"], record["graph_revision"]): record
            for record in records
        }

    def read_scheduler_snapshot(
        self,
        graph_id: str,
        graph_revision: int,
    ) -> dict[str, Any]:
        record = self.records[(graph_id, graph_revision)]
        return {
            "observed_ts_ms": NOW + 10_000,
            "graph_record": {
                "graph_id": graph_id,
                "revision": graph_revision,
                "status": record["runtime_graph_status"],
            },
            "active_leases": [],
        }


class _SurfaceRuntime:
    def __init__(self, name: str) -> None:
        self.name = name
        self.startup_records: list[dict[str, Any]] = []
        self.active = False
        self.accepting = False

    async def activate(self) -> None:
        self.active = True
        self.accepting = True

    async def close_admission(self) -> None:
        self.accepting = False

    async def drain(self) -> None:
        return None

    async def aclose(self) -> None:
        self.active = False

    async def continue_startup_admission(
        self,
        record: Mapping[str, Any],
    ) -> dict[str, Any]:
        assert self.active is True
        assert self.accepting is True
        copied = dict(record)
        self.startup_records.append(copied)
        command = copied["command"]
        return {
            "execution_receipt": {
                "graph_id": command.graph_id,
                "graph_revision": command.graph_revision,
                "status": "ACCEPTED",
                "accepted": True,
                "receipt_ref": (
                    f"task-receipt://control-survivor/{self.name}/"
                    f"{command.command_id}"
                ),
            }
        }


class _BlockingSurfaceRuntime(_SurfaceRuntime):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False

    async def continue_startup_admission(
        self,
        record: Mapping[str, Any],
    ) -> dict[str, Any]:
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return await super().continue_startup_admission(record)


def _runtimes() -> dict[str, _SurfaceRuntime]:
    return {
        name: _SurfaceRuntime(name)
        for name in ("host", "internal_agent", "scheduler", "operator")
    }


@pytest.mark.asyncio
async def test_runtime_group_pages_and_routes_all_control_survivors() -> None:
    records = [
        _record(index + 1, CONTROL_SURFACES[index % len(CONTROL_SURFACES)])
        for index in range(101)
    ]
    inventory = _Inventory(records)
    runtimes = _runtimes()
    group = GovernedRuntimeGroup(
        runtimes,
        command_repository=inventory,
        startup_task_store=_SnapshotStore(records),
        startup_continuation_factory=HostStartupGraphContinuation,
        startup_survivor_factory=PeriodicDurableContinuation,
        startup_survivor_interval_ms=60_000,
    )

    await group.activate()
    report = await group.recover_startup_graphs()

    assert len(report) == 101
    assert {item["status"] for item in report} == {"COMPLETED"}
    assert len(inventory.calls) == 2
    assert inventory.calls[0] == {
        "surfaces": ("internal", "operator", "scheduler"),
        "limit": 100,
        "cursor": None,
    }
    assert inventory.calls[1]["cursor"] is not None
    assert len(runtimes["host"].startup_records) == 0
    assert len(runtimes["internal_agent"].startup_records) == 34
    assert len(runtimes["scheduler"].startup_records) == 34
    assert len(runtimes["operator"].startup_records) == 33
    assert group.startup_survivor_health["running"] is True
    assert group.startup_survivor_health["completed_cycles"] == 1

    await group.close_admission()
    await group.drain()
    await group.aclose()
    assert group.startup_survivor_health["running"] is False


@pytest.mark.asyncio
async def test_runtime_group_serializes_concurrent_mandatory_recovery() -> None:
    records = [_record(1, HostCommandSurface.INTERNAL)]
    inventory = _Inventory(records)
    group = GovernedRuntimeGroup(
        _runtimes(),
        command_repository=inventory,
        startup_task_store=_SnapshotStore(records),
        startup_continuation_factory=HostStartupGraphContinuation,
        startup_survivor_factory=PeriodicDurableContinuation,
        startup_survivor_interval_ms=60_000,
    )
    await group.activate()

    first, second = await asyncio.gather(
        group.recover_startup_graphs(),
        group.recover_startup_graphs(),
    )

    assert first == second
    assert len(first) == 1
    assert len(inventory.calls) == 1
    await group.close_admission()
    await group.drain()
    await group.aclose()


@pytest.mark.asyncio
async def test_runtime_group_shutdown_waits_for_mandatory_control_scan() -> None:
    record = _record(1, HostCommandSurface.INTERNAL)
    inventory = _Inventory([record])
    store = _SnapshotStore([record])
    runtimes = _runtimes()
    blocking = _BlockingSurfaceRuntime("internal_agent")
    runtimes["internal_agent"] = blocking
    group = GovernedRuntimeGroup(
        runtimes,
        command_repository=inventory,
        startup_task_store=store,
        startup_continuation_factory=HostStartupGraphContinuation,
        startup_survivor_factory=PeriodicDurableContinuation,
        startup_survivor_interval_ms=60_000,
    )
    await group.activate()

    recovery = asyncio.create_task(group.recover_startup_graphs())
    await asyncio.wait_for(blocking.started.wait(), timeout=1.0)
    closing = asyncio.create_task(group.close_admission())
    await asyncio.sleep(0)
    assert recovery.done() is False
    assert closing.done() is False
    assert blocking.accepting is True

    blocking.release.set()
    report = await asyncio.wait_for(recovery, timeout=1.0)
    await asyncio.wait_for(closing, timeout=1.0)

    assert len(report) == 1
    assert blocking.cancelled is False
    assert blocking.accepting is False
    assert group.startup_survivor_health["running"] is False
    await group.drain()
    await group.aclose()


@pytest.mark.asyncio
async def test_runtime_group_shutdown_joins_active_control_scan_without_cancel() -> None:
    inventory = _Inventory([])
    store = _SnapshotStore([])
    runtimes = _runtimes()
    blocking = _BlockingSurfaceRuntime("internal_agent")
    runtimes["internal_agent"] = blocking
    group = GovernedRuntimeGroup(
        runtimes,
        command_repository=inventory,
        startup_task_store=store,
        startup_continuation_factory=HostStartupGraphContinuation,
        startup_survivor_factory=PeriodicDurableContinuation,
        startup_survivor_interval_ms=60_000,
    )
    await group.activate()
    assert await group.recover_startup_graphs() == ()

    record = _record(1, HostCommandSurface.INTERNAL)
    inventory.records.append(record)
    store.records[(record["graph_id"], record["graph_revision"])] = record
    group.wake_startup_survivor()
    await asyncio.wait_for(blocking.started.wait(), timeout=1.0)

    closing = asyncio.create_task(group.close_admission())
    await asyncio.sleep(0)
    assert closing.done() is False
    assert blocking.accepting is True
    blocking.release.set()
    await asyncio.wait_for(closing, timeout=1.0)

    assert blocking.cancelled is False
    assert blocking.accepting is False
    assert len(blocking.startup_records) == 1
    await group.drain()
    await group.aclose()


@pytest.mark.asyncio
async def test_runtime_group_recovers_control_surfaces_from_real_sqlite(
    tmp_path,
) -> None:
    db_path = tmp_path / "control-surface-survivor.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="ne01-control-survivor-migrate",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()
    store = DurableTaskStore(db_path, clock=lambda: NOW + 10_000)
    commands = HostCommandRepository(
        db_path,
        clock=lambda: NOW + 10_000,
        task_store=store,
    )
    contracts = [
        _contracts(index + 1, surface)
        for index, surface in enumerate(CONTROL_SURFACES)
    ]
    for command, work_order, assignment, graph in contracts:
        commands.admit(command, work_order, assignment, graph)
    runtimes = _runtimes()
    group = GovernedRuntimeGroup(
        runtimes,
        command_repository=commands,
        startup_task_store=store,
        startup_continuation_factory=HostStartupGraphContinuation,
        startup_survivor_factory=PeriodicDurableContinuation,
        startup_survivor_interval_ms=60_000,
    )

    await group.activate()
    report = await group.recover_startup_graphs()

    assert len(report) == 3
    assert {
        record["command"].surface
        for runtime in runtimes.values()
        for record in runtime.startup_records
    } == set(CONTROL_SURFACES)
    assert len(runtimes["internal_agent"].startup_records) == 1
    assert len(runtimes["scheduler"].startup_records) == 1
    assert len(runtimes["operator"].startup_records) == 1
    await group.close_admission()
    await group.drain()
    await group.aclose()
