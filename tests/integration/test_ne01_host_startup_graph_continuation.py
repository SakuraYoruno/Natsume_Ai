from __future__ import annotations

from typing import Any

import pytest

from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.app.governed_task_adapter import GovernedHostRuntime
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.contracts.agent_runtime.task_graph import TaskGraphStatus
from host_brain.orchestration.host_route_contracts import (
    build_direct_host_route_contracts,
)
from host_brain.persistence.host_command_repository import HostCommandRepository
from host_brain.persistence.task_repository import (
    DurableTaskStore,
    OutboxMessage,
    build_outbox_payload_ref,
)
from natsume_contracts.task import (
    ExecutorAssignment,
    HostCommandSurface,
    TaskExecutor,
)


NOW = 1_900_000_000_000


class _CapturingApplication:
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


def _contracts() -> tuple[Any, Any, Any, Any]:
    direct = build_direct_host_route_contracts(
        surface=HostCommandSurface.CHAT,
        request_id="request-host-startup-sqlite",
        session_id="session-host-startup-sqlite",
        principal_id="person-host-startup-sqlite",
        ts_ms=NOW,
        intent_draft={
            "route": "chat",
            "target_id": "surface_reply",
            "task": "resume durable Host graph from SQLite",
            "action": "exec_intent",
            "target": "host",
        },
        governor_result={
            "request_id": "request-host-startup-sqlite",
            "decision": "APPROVE",
            "tokens": {},
            "ts_ms": NOW,
        },
        sanitized_message="resume durable Host graph from SQLite",
        person_id="person-host-startup-sqlite",
    )
    assignment = ExecutorAssignment(
        work_order_id=direct.work_order.work_order_id,
        executor=TaskExecutor.CANONICAL,
        assignment_id="runtime-assignment://host-startup-sqlite/canonical",
        assigned_at_ms=NOW,
        is_new_task=True,
        reason_code="RUNTIME_NEW_TASK_CANONICAL",
    )
    return direct.command, direct.work_order, assignment, direct.graph


@pytest.mark.asyncio
async def test_host_startup_continuation_reads_exact_sqlite_admission(
    tmp_path,
) -> None:
    db_path = tmp_path / "host-startup-continuation.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="ne01-host-startup-continuation-migrate",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()
    command, work_order, assignment, graph = _contracts()
    commands = HostCommandRepository(db_path, clock=lambda: NOW)
    admitted = commands.admit(command, work_order, assignment, graph)
    store = DurableTaskStore(db_path, clock=lambda: NOW)
    inventory = store.list_nonterminal_graphs(limit=10)
    assert [(item["graph_id"], item["revision"], item["status"]) for item in inventory] == [
        (graph.graph_id, graph.revision, TaskGraphStatus.CREATED.value)
    ]

    application = _CapturingApplication()
    runtime = GovernedHostRuntime(
        application=application,
        runtime_router=CanonicalRuntimeRouter(),
        command_repository=commands,
        task_store=store,
    )
    await runtime.activate()
    report = await runtime.recover_startup_graphs()

    assert len(application.calls) == 1
    resumed = application.calls[0]
    assert resumed["command"] == admitted["command"]
    assert resumed["work_order"] == admitted["work_order"]
    assert resumed["assignment"] == admitted["assignment"]
    assert resumed["graph"] == admitted["graph"]
    assert report == (
        {
            "graph_id": graph.graph_id,
            "graph_revision": graph.revision,
            "status": "COMPLETED",
            "reason_code": None,
        },
    )
    await runtime.aclose()

    current = store.get_graph(graph.graph_id, graph.revision)
    activated = store.transition_graph(
        graph.graph_id,
        graph.revision,
        expected_version=current["status_version"],
        target_status=TaskGraphStatus.ACTIVE,
        outbox=OutboxMessage.create(
            aggregate_type="TASK_GRAPH",
            aggregate_id=graph.graph_id,
            aggregate_version=current["status_version"] + 1,
            event_type="TASK_GRAPH_ACTIVE",
            payload_ref=build_outbox_payload_ref(
                aggregate_type="TASK_GRAPH",
                aggregate_id=graph.graph_id,
                aggregate_version=current["status_version"] + 1,
                graph_revision=graph.revision,
            ),
            idempotency_key="host-startup-continuation-activate",
            graph_revision=graph.revision,
        ),
    )
    store.transition_graph(
        graph.graph_id,
        graph.revision,
        expected_version=activated["status_version"],
        target_status=TaskGraphStatus.FAILED,
        outbox=OutboxMessage.create(
            aggregate_type="TASK_GRAPH",
            aggregate_id=graph.graph_id,
            aggregate_version=activated["status_version"] + 1,
            event_type="TASK_GRAPH_FAILED",
            payload_ref=build_outbox_payload_ref(
                aggregate_type="TASK_GRAPH",
                aggregate_id=graph.graph_id,
                aggregate_version=activated["status_version"] + 1,
                graph_revision=graph.revision,
            ),
            idempotency_key="host-startup-continuation-terminal-filter",
            graph_revision=graph.revision,
        ),
    )
    assert store.list_nonterminal_graphs(limit=10) == []
