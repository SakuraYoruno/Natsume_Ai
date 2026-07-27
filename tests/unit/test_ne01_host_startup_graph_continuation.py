from __future__ import annotations

import asyncio
from dataclasses import replace
import hashlib
import json
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
        snapshots: dict[tuple[str, int], dict[str, Any]],
    ) -> None:
        self.snapshots = snapshots

    def read_scheduler_snapshot(
        self,
        graph_id: str,
        graph_revision: int,
    ) -> dict[str, Any]:
        return dict(self.snapshots[(graph_id, graph_revision)])


class _CommandRepository:
    def __init__(
        self,
        record: dict[str, Any],
        *,
        additional_records: tuple[dict[str, Any], ...] = (),
    ) -> None:
        self.records = (record, *additional_records)
        self.inventory_calls: list[
            tuple[tuple[str, ...], int, dict[str, Any] | None]
        ] = []

    def list_nonterminal_admission_page(
        self,
        *,
        surfaces: tuple[str, ...],
        limit: int,
        cursor: dict[str, Any] | None,
    ) -> dict[str, Any]:
        normalized = tuple(str(item) for item in surfaces)
        copied_cursor = dict(cursor) if cursor is not None else None
        self.inventory_calls.append((normalized, int(limit), copied_cursor))
        filtered = []
        for record in self.records:
            command = record.get("command")
            if str(getattr(command, "surface", "")) in normalized:
                filtered.append(dict(record))
        filtered.sort(
            key=lambda item: (item["created_at_ms"], item["command_id"])
        )
        if cursor is not None:
            cursor_key = (cursor["created_at_ms"], cursor["command_id"])
            filtered = [
                item
                for item in filtered
                if (item["created_at_ms"], item["command_id"]) > cursor_key
            ]
        page_records = filtered[: int(limit)]
        has_more = len(filtered) > int(limit)
        surfaces_sha256 = "sha256:" + hashlib.sha256(
            json.dumps(
                list(normalized),
                ensure_ascii=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        next_cursor = None
        if has_more:
            tail = page_records[-1]
            next_cursor = {
                "schema_version": "Natsume.host.nonterminal_admission_cursor.v1",
                "surfaces_sha256": surfaces_sha256,
                "created_at_ms": tail["created_at_ms"],
                "command_id": tail["command_id"],
            }
        return {
            "schema_version": "Natsume.host.nonterminal_admission_page.v1",
            "surfaces_sha256": surfaces_sha256,
            "records": page_records,
            "next_cursor": next_cursor,
            "has_more": has_more,
        }


class _OutOfOrderCommandRepository(_CommandRepository):
    def list_nonterminal_admission_page(self, **kwargs: Any) -> dict[str, Any]:
        page = super().list_nonterminal_admission_page(**kwargs)
        page["records"] = list(reversed(page["records"]))
        return page


class _Application:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.cancellations: list[dict[str, Any]] = []

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

    async def request_graph_cancellation(self, **kwargs: Any) -> dict[str, Any]:
        self.cancellations.append(dict(kwargs))
        return {"status": "CANCELLED"}


def _contracts(
    *,
    suffix: str = "",
    ts_offset: int = 0,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    request_suffix = f"-{suffix}" if suffix else ""
    ts_ms = NOW + int(ts_offset)
    direct = build_direct_host_route_contracts(
        surface=HostCommandSurface.CHAT,
        request_id=f"request-host-startup{request_suffix}",
        session_id=f"session-host-startup{request_suffix}",
        principal_id=f"person-host-startup{request_suffix}",
        ts_ms=ts_ms,
        intent_draft={
            "route": "chat",
            "target_id": "surface_reply",
            "task": "resume the exact durable Host graph",
            "action": "exec_intent",
            "target": "host",
        },
        governor_result={
            "request_id": f"request-host-startup{request_suffix}",
            "decision": "APPROVE",
            "tokens": {},
            "ts_ms": ts_ms,
        },
        sanitized_message="resume the exact durable Host graph",
        person_id=f"person-host-startup{request_suffix}",
    )
    assignment = ExecutorAssignment(
        work_order_id=direct.work_order.work_order_id,
        executor=TaskExecutor.CANONICAL,
        assignment_id=(
            f"runtime-assignment://host-startup{request_suffix}/canonical"
        ),
        assigned_at_ms=ts_ms,
        is_new_task=True,
        reason_code="RUNTIME_NEW_TASK_CANONICAL",
    )
    graph_record = {
        "graph_id": direct.graph.graph_id,
        "revision": direct.graph.revision,
        "status": "CREATED",
    }
    record = {
        "command_id": direct.command.command_id,
        "created_at_ms": direct.command.created_at_ms,
        "graph_id": direct.graph.graph_id,
        "graph_revision": direct.graph.revision,
        "runtime_graph_status": "CREATED",
        "command": direct.command,
        "work_order": direct.work_order,
        "assignment": assignment,
        "graph": direct.graph,
    }
    snapshot = {
        "observed_ts_ms": ts_ms,
        "graph_record": dict(graph_record),
        "active_leases": [],
        "operation_execution_fences": [],
    }
    return record, graph_record, snapshot


@pytest.mark.asyncio
async def test_host_runtime_activation_reenters_exact_nonterminal_graph() -> None:
    record, graph_record, snapshot = _contracts()
    graph_id = graph_record["graph_id"]
    graph_revision = graph_record["revision"]
    store = _StartupStore(
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

    assert commands.inventory_calls == [
        (("chat", "gateway", "host_input", "tool"), 100, None)
    ]
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
    cancellation = await runtime.request_graph_cancellation(
        record,
        authority={"grant_ref": "grant://test/cancel"},
        source="test",
        reason_code="USER_CANCELLED",
    )
    assert cancellation == {"status": "CANCELLED"}

    async def wait_for_operation_completion_wake() -> None:
        while runtime.survivor_continuation_health["completed_cycles"] < 2:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_for_operation_completion_wake(), timeout=1.0)
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
async def test_host_runtime_periodically_reenters_after_live_lease_expires() -> None:
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
        snapshots={(graph_id, graph_revision): snapshot},
    )
    application = _Application()
    runtime = GovernedHostRuntime(
        application=application,
        runtime_router=CanonicalRuntimeRouter(),
        command_repository=_CommandRepository(record),
        task_store=store,
        survivor_interval_ms=10,
    )

    await runtime.activate()
    initial = await runtime.recover_startup_graphs()
    assert initial[0]["status"] == "DEFERRED"
    assert application.calls == []

    snapshot["observed_ts_ms"] = NOW + 60_001
    runtime.wake_survivor_continuation()

    async def wait_for_reentry() -> None:
        while not application.calls:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_for_reentry(), timeout=1.0)
    health = runtime.survivor_continuation_health
    assert health["completed_cycles"] >= 2
    assert health["last_report"] == (
        {
            "graph_id": graph_id,
            "graph_revision": graph_revision,
            "status": "COMPLETED",
            "reason_code": None,
        },
    )
    await runtime.aclose()
    assert runtime.survivor_continuation_health["running"] is False


@pytest.mark.asyncio
async def test_host_runtime_reenters_one_signed_reacquired_live_lease() -> None:
    record, graph_record, snapshot = _contracts()
    graph_id = graph_record["graph_id"]
    graph_revision = graph_record["revision"]
    snapshot["active_leases"] = [
        {
            "status": "ACTIVE",
            "attempt_id": "attempt-signed-reacquired",
            "lease_id": "lease-signed-reacquired",
            "owner_id": "host-owner-reacquired",
            "fencing_token": 2,
            "expires_ts_ms": NOW + 60_000,
        }
    ]
    snapshot["operation_execution_fences"] = [
        {
            "reservation_id": "reservation-signed-reacquired",
            "status": "RESERVED",
            "status_version": 2,
            "attempt_id": "attempt-signed-reacquired",
            "lease_id": "lease-signed-reacquired",
            "owner_id": "host-owner-reacquired",
            "fencing_token": 2,
            "signed_reacquisition_verified": True,
        }
    ]
    store = _StartupStore(
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
    report = await runtime.recover_startup_graphs()

    assert len(application.calls) == 1
    assert report == (
        {
            "graph_id": graph_id,
            "graph_revision": graph_revision,
            "status": "COMPLETED",
            "reason_code": None,
        },
    )
    await runtime.aclose()


@pytest.mark.asyncio
async def test_startup_inventory_filters_foreign_surface_before_exact_bound() -> None:
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
        snapshots={(graph_id, graph_revision): snapshot},
    )
    calls: list[tuple[Any, ...]] = []

    async def resume(*args: Any) -> dict[str, Any]:
        calls.append(args)
        return await _completed_terminal(graph_id, graph_revision)

    continuation = HostStartupGraphContinuation(
        task_store=store,
        command_repository=_CommandRepository(
            foreign_record,
            additional_records=(record,),
        ),
        resume_admission=resume,
    )

    report = await continuation.recover(page_limit=1)

    assert report == (
        {
            "graph_id": graph_id,
            "graph_revision": graph_revision,
            "status": "COMPLETED",
            "reason_code": None,
        },
    )
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_startup_continuation_recovers_every_paginated_admission() -> None:
    record, graph_record, snapshot = _contracts(suffix="first")
    other_record, other_graph_record, other_snapshot = _contracts(
        suffix="second",
        ts_offset=1,
    )
    store = _StartupStore(
        snapshots={
            (graph_record["graph_id"], graph_record["revision"]): snapshot,
            (
                other_graph_record["graph_id"],
                other_graph_record["revision"],
            ): other_snapshot,
        },
    )
    calls: list[tuple[Any, ...]] = []

    async def resume(*args: Any) -> dict[str, Any]:
        calls.append(args)
        graph = args[-1]
        return await _completed_terminal(graph.graph_id, graph.revision)

    commands = _CommandRepository(
        record,
        additional_records=(other_record,),
    )
    continuation = HostStartupGraphContinuation(
        task_store=store,
        command_repository=commands,
        resume_admission=resume,
    )

    report = await continuation.recover(page_limit=1)

    assert len(report) == 2
    assert {item["graph_id"] for item in report} == {
        graph_record["graph_id"],
        other_graph_record["graph_id"],
    }
    assert len(calls) == 2
    assert len(commands.inventory_calls) == 2
    assert commands.inventory_calls[0][-1] is None
    assert commands.inventory_calls[1][-1] is not None


@pytest.mark.asyncio
async def test_startup_continuation_rejects_non_monotonic_page_keys() -> None:
    record, graph_record, snapshot = _contracts(suffix="first")
    other_record, other_graph_record, other_snapshot = _contracts(
        suffix="second",
        ts_offset=1,
    )
    store = _StartupStore(
        snapshots={
            (graph_record["graph_id"], graph_record["revision"]): snapshot,
            (
                other_graph_record["graph_id"],
                other_graph_record["revision"],
            ): other_snapshot,
        },
    )
    continuation = HostStartupGraphContinuation(
        task_store=store,
        command_repository=_OutOfOrderCommandRepository(
            record,
            additional_records=(other_record,),
        ),
        resume_admission=lambda *_args: _completed_terminal(
            graph_record["graph_id"],
            graph_record["revision"],
        ),
    )

    with pytest.raises(HostStartupGraphContinuationError) as exc_info:
        await continuation.recover(page_limit=2)

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
