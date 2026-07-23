from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.app.governed_task_adapter import GovernedHostRuntime
from host_brain.app.host_startup_graph_continuation import (
    HostStartupGraphContinuation,
    HostStartupGraphContinuationError,
)
from host_brain.orchestration.host_route_contracts import (
    build_direct_host_route_contracts,
)
from natsume_contracts.task import (
    ExecutorAssignment,
    HostCommandSurface,
    TaskExecutor,
    build_host_command,
)


NOW = 1_900_000_000_000


class _StartupStore:
    def __init__(
        self,
        *,
        graphs: list[dict[str, Any]],
        snapshots: dict[tuple[str, int], dict[str, Any]],
    ) -> None:
        self.graphs = graphs
        self.snapshots = snapshots
        self.limits: list[int] = []

    def list_nonterminal_graphs(self, *, limit: int) -> list[dict[str, Any]]:
        self.limits.append(limit)
        return list(self.graphs[:limit])

    def read_scheduler_snapshot(
        self,
        graph_id: str,
        graph_revision: int,
    ) -> dict[str, Any]:
        return dict(self.snapshots[(graph_id, graph_revision)])


class _CommandRepository:
    def __init__(self, record: dict[str, Any]) -> None:
        self.record = record
        self.lookups: list[tuple[str, int]] = []

    def get_by_graph(
        self,
        *,
        graph_id: str,
        graph_revision: int,
    ) -> dict[str, Any]:
        self.lookups.append((graph_id, graph_revision))
        return dict(self.record)


class _Application:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute_stream(self, **kwargs: Any):
        self.calls.append(dict(kwargs))
        graph = kwargs["graph"]
        yield {
            "done": True,
            "execution_receipt": {
                "graph_id": graph.graph_id,
                "graph_revision": graph.revision,
                "status": "ACCEPTED",
                "accepted": True,
            },
        }


def _contracts() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    direct = build_direct_host_route_contracts(
        surface=HostCommandSurface.CHAT,
        request_id="request-host-startup",
        session_id="session-host-startup",
        principal_id="person-host-startup",
        ts_ms=NOW,
        intent_draft={
            "route": "chat",
            "target_id": "surface_reply",
            "task": "resume the exact durable Host graph",
            "action": "exec_intent",
            "target": "host",
        },
        governor_result={
            "request_id": "request-host-startup",
            "decision": "APPROVE",
            "tokens": {},
            "ts_ms": NOW,
        },
        sanitized_message="resume the exact durable Host graph",
        person_id="person-host-startup",
    )
    assignment = ExecutorAssignment(
        work_order_id=direct.work_order.work_order_id,
        executor=TaskExecutor.CANONICAL,
        assignment_id="runtime-assignment://host-startup/canonical",
        assigned_at_ms=NOW,
        is_new_task=True,
        reason_code="RUNTIME_NEW_TASK_CANONICAL",
    )
    graph_record = {
        "graph_id": direct.graph.graph_id,
        "revision": direct.graph.revision,
        "status": "CREATED",
    }
    record = {
        "command": direct.command,
        "work_order": direct.work_order,
        "assignment": assignment,
        "graph": direct.graph,
    }
    snapshot = {
        "observed_ts_ms": NOW,
        "graph_record": dict(graph_record),
        "active_leases": [],
    }
    return record, graph_record, snapshot


@pytest.mark.asyncio
async def test_host_runtime_activation_reenters_exact_nonterminal_graph() -> None:
    record, graph_record, snapshot = _contracts()
    graph_id = graph_record["graph_id"]
    graph_revision = graph_record["revision"]
    store = _StartupStore(
        graphs=[graph_record],
        snapshots={(graph_id, graph_revision): snapshot},
    )
    commands = _CommandRepository(record)
    application = _Application()
    runtime = GovernedHostRuntime(
        application=application,
        runtime_router=CanonicalRuntimeRouter(),
        command_repository=commands,
        task_store=store,
    )

    await runtime.activate()
    assert application.calls == []
    await runtime.recover_startup_graphs()

    assert store.limits == [100]
    assert commands.lookups == [(graph_id, graph_revision)]
    assert len(application.calls) == 1
    resumed = application.calls[0]
    assert resumed["command"] is record["command"]
    assert resumed["work_order"] is record["work_order"]
    assert resumed["assignment"] is record["assignment"]
    assert resumed["graph"] is record["graph"]
    assert runtime.startup_continuation_report == (
        {
            "graph_id": graph_id,
            "graph_revision": graph_revision,
            "status": "COMPLETED",
            "reason_code": None,
        },
    )
    await runtime.aclose()


@pytest.mark.asyncio
async def test_host_runtime_startup_never_reenters_graph_with_live_lease() -> None:
    record, graph_record, snapshot = _contracts()
    graph_id = graph_record["graph_id"]
    graph_revision = graph_record["revision"]
    snapshot["active_leases"] = [
        {
            "status": "ACTIVE",
            "expires_ts_ms": NOW + 60_000,
        }
    ]
    store = _StartupStore(
        graphs=[graph_record],
        snapshots={(graph_id, graph_revision): snapshot},
    )
    application = _Application()
    runtime = GovernedHostRuntime(
        application=application,
        runtime_router=CanonicalRuntimeRouter(),
        command_repository=_CommandRepository(record),
        task_store=store,
    )

    await runtime.activate()
    await runtime.recover_startup_graphs()

    assert application.calls == []
    assert runtime.startup_continuation_report == (
        {
            "graph_id": graph_id,
            "graph_revision": graph_revision,
            "status": "DEFERRED",
            "reason_code": "GOVERNED_HOST_STARTUP_LIVE_LEASE",
        },
    )
    await runtime.aclose()


@pytest.mark.asyncio
async def test_startup_continuation_ignores_foreign_surface_before_host_tuple_check() -> None:
    record, graph_record, snapshot = _contracts()
    graph_id = graph_record["graph_id"]
    graph_revision = graph_record["revision"]
    foreign_record = {
        "command": build_host_command(
            surface=HostCommandSurface.SCHEDULER,
            request_id=record["command"].request_id,
            session_id=record["command"].session_id,
            principal_id=record["command"].principal_id,
            input_ref=record["command"].input_ref,
            input_sha256=record["command"].input_sha256,
            root_work_order_id=record["command"].root_work_order_id,
            graph_id=graph_id,
            graph_revision=graph_revision,
            route_type=record["command"].route_type,
            target_id=record["command"].target_id,
            business_scope=record["command"].business_scope,
            operation_fingerprint=record["command"].operation_fingerprint,
            created_at_ms=record["command"].created_at_ms,
            governor_grant_ref=record["command"].governor_grant_ref,
        )
    }
    store = _StartupStore(
        graphs=[graph_record],
        snapshots={(graph_id, graph_revision): snapshot},
    )
    calls: list[tuple[Any, ...]] = []

    async def resume(*args: Any) -> dict[str, Any]:
        calls.append(args)
        return await _completed_terminal(graph_id, graph_revision)

    continuation = HostStartupGraphContinuation(
        task_store=store,
        command_repository=_CommandRepository(foreign_record),
        resume_admission=resume,
    )

    report = await continuation.recover()

    assert report == ()
    assert calls == []


@pytest.mark.asyncio
async def test_startup_continuation_rejects_a_truncated_inventory() -> None:
    record, graph_record, snapshot = _contracts()
    graph_id = graph_record["graph_id"]
    graph_revision = graph_record["revision"]
    store = _StartupStore(
        graphs=[graph_record],
        snapshots={(graph_id, graph_revision): snapshot},
    )
    continuation = HostStartupGraphContinuation(
        task_store=store,
        command_repository=_CommandRepository(record),
        resume_admission=lambda *_args: _completed_terminal(graph_id, graph_revision),
    )

    with pytest.raises(HostStartupGraphContinuationError) as exc_info:
        await continuation.recover(page_limit=1)

    assert exc_info.value.reason_code == "GOVERNED_HOST_STARTUP_CONTINUATION_INVALID"


@pytest.mark.asyncio
async def test_startup_continuation_rejects_cross_work_order_tuple_drift() -> None:
    record, graph_record, snapshot = _contracts()
    graph_id = graph_record["graph_id"]
    graph_revision = graph_record["revision"]
    record["assignment"] = replace(
        record["assignment"],
        work_order_id="different-root-work-order",
    )
    store = _StartupStore(
        graphs=[graph_record],
        snapshots={(graph_id, graph_revision): snapshot},
    )
    calls: list[tuple[Any, ...]] = []

    async def resume(*args: Any) -> dict[str, Any]:
        calls.append(args)
        return await _completed_terminal(graph_id, graph_revision)

    continuation = HostStartupGraphContinuation(
        task_store=store,
        command_repository=_CommandRepository(record),
        resume_admission=resume,
    )

    with pytest.raises(HostStartupGraphContinuationError) as exc_info:
        await continuation.recover()

    assert exc_info.value.reason_code == "GOVERNED_HOST_STARTUP_CONTINUATION_INVALID"
    assert calls == []


async def _completed_terminal(graph_id: str, graph_revision: int) -> dict[str, Any]:
    return {
        "execution_receipt": {
            "graph_id": graph_id,
            "graph_revision": graph_revision,
            "status": "ACCEPTED",
            "accepted": True,
        }
    }
