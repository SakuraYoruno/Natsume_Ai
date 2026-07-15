from __future__ import annotations

import asyncio
from dataclasses import replace
import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from host_brain.orchestration import GovernedCommandApplication
from host_brain.persistence.host_command_repository import HostCommandRepository
from host_brain.persistence.task_repository import DurableTaskStore, OutboxMessage
from host_brain.persistence.worker_projection_repository import (
    WORKER_PROJECTION_SCHEMA_VERSION,
    WorkerProjectionStore,
)
from host_brain.task_runtime import GovernedRuntimeError, GovernedTaskCoordinator
from host_brain.task_runtime.errors import (
    GOVERNED_ADMISSION_INVALID,
    GOVERNED_DURABLE_STATE_CONFLICT,
)
from tests.integration.test_gr03a_governed_durable_dag import (
    NOW,
    _Acceptance,
    _AsyncFanOutProbe,
    _FailingResolver,
    _Publisher,
    _Verifier,
    _contracts,
    _migrated_db,
)


class _CrashBeforeParallelSuccess(BaseException):
    """Simulate process loss after child commits without entering Store UoW."""


class _ProjectedTerminalWorker:
    """Two-root worker using the production WorkerProjectionStore contract."""

    def __init__(
        self,
        *,
        projection_store: WorkerProjectionStore,
        probe: _AsyncFanOutProbe,
        calls: list[str],
        completion_order: list[str],
        delays: dict[str, float],
    ) -> None:
        self._projection_store = projection_store
        self._probe = probe
        self._calls = calls
        self._completion_order = completion_order
        self._delays = dict(delays)

    async def execute_stream(
        self,
        *,
        command: Any,
        execution_context: dict[str, Any],
        **_: Any,
    ):
        node_id = str(execution_context["node_id"])
        attempt_id = str(execution_context["attempt_id"])
        self._calls.append(node_id)
        await self._probe.activate(node_id)
        try:
            await asyncio.wait_for(self._probe.all_roots_started.wait(), timeout=2.0)
            delay = float(self._delays.get(node_id, 0.0))
            if delay:
                await asyncio.sleep(delay)
            body = {
                "phase": "COMMIT",
                "done": True,
                "decision": "APPROVE",
                "reason_code": None,
                "command_id": command.command_id,
                "request_id": command.request_id,
                "narrative": f"durable result from {node_id}",
                "route_type": "skill",
                "target_id": node_id,
                "graph_id": str(execution_context["graph_id"]),
                "graph_revision": int(execution_context["graph_revision"]),
                "node_id": node_id,
                "attempt_id": attempt_id,
                "node_grant_ref": f"grant://gr03a/{node_id}",
                "audit_refs": {"worker": f"audit://gr03a/{node_id}"},
                "budget_usage": {
                    "schema_version": "Natsume.task_runtime.worker_budget_usage.v1",
                    "usage_id": f"worker-usage://{node_id}/b5",
                    "measurements": {
                        "token_count": 10,
                        "retry_count": 0,
                        "model_cost_micros": 25,
                    },
                    "evidence_refs": [f"model-budget://{node_id}/b5"],
                },
            }
            encoded = json.dumps(
                body,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            digest = "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            projection_id = (
                "worker-projection://gr03a/1/"
                f"{node_id}/{attempt_id}/{digest.removeprefix('sha256:')}"
            )
            self._projection_store.stage(
                projection_id=projection_id,
                projection={
                    **body,
                    "projection_id": projection_id,
                    "payload_digest": digest,
                    "schema_version": WORKER_PROJECTION_SCHEMA_VERSION,
                },
            )
            self._completion_order.append(node_id)
            self._probe.completed_nodes.add(node_id)
            yield {**body, "receipt_ref": projection_id}
        finally:
            await self._probe.deactivate()


class _ProjectedResolver:
    def __init__(self, worker: _ProjectedTerminalWorker) -> None:
        self._worker = worker

    def resolve(self, *, executor: str, worker_route: str) -> _ProjectedTerminalWorker:
        assert executor == "canonical"
        assert worker_route.startswith("skill:")
        return self._worker


class _NodeGrantService:
    def derive(
        self,
        *,
        parent_grant_ref: str,
        task_graph: Any,
        **_: Any,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            parent_grant_ref=parent_grant_ref,
            task_graph_id=task_graph.graph_id,
            graph_revision=task_graph.revision,
            grant_refs={
                node.node_id: f"grant://gr03a/{node.node_id}"
                for node in task_graph.nodes
            },
        )


class _TamperedProjectionReader:
    def __init__(
        self,
        delegate: WorkerProjectionStore,
        *,
        field_name: str,
        value: Any,
    ) -> None:
        self._delegate = delegate
        self._field_name = field_name
        self._value = value

    def load(self, receipt_ref: str) -> dict[str, Any] | None:
        loaded = self._delegate.load(receipt_ref)
        if not isinstance(loaded, dict):
            return None
        projection = dict(loaded)
        if projection.get("node_id") == "right":
            projection[self._field_name] = self._value
        return projection


def _b5_runtime(
    db_path: Path,
    *,
    projection_store: WorkerProjectionStore,
    worker_resolver: Any,
    task_store: DurableTaskStore | None = None,
    atomic_graph_success_enabled: bool = True,
) -> GovernedTaskCoordinator:
    return GovernedTaskCoordinator(
        task_store=task_store or DurableTaskStore(db_path, clock=lambda: NOW),
        outbox_publisher=_Publisher(),
        outbox_factory=OutboxMessage,
        worker_resolver=worker_resolver,
        verifier=_Verifier(),
        acceptance=_Acceptance(),
        projection_reader=projection_store,
        acceptance_effects_enabled=True,
        bounded_fanout_enabled=True,
        atomic_graph_success_enabled=atomic_graph_success_enabled,
        owner_id="gr03a-b5-worker",
    )


def _b5_fixture(
    tmp_path: Path,
    *,
    delays: dict[str, float],
    specs: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("left", ()),
        ("right", ()),
    ),
    concurrency: int = 2,
) -> tuple[
    Path,
    tuple[Any, Any, Any, Any],
    WorkerProjectionStore,
    DurableTaskStore,
    GovernedTaskCoordinator,
    _AsyncFanOutProbe,
    list[str],
    list[str],
]:
    db_path = _migrated_db(tmp_path)
    command, work_order, assignment, graph = _contracts(
        specs,
        concurrency=concurrency,
    )
    command = replace(command, governor_grant_ref="grant://gr03a/parent")
    depended_on = {
        dependency for _, dependencies in specs for dependency in dependencies
    }
    result_node_id = next(
        node_id for node_id, _ in reversed(specs) if node_id not in depended_on
    )
    graph = replace(
        graph,
        result_node_id=result_node_id,
        completion_policy="ATOMIC_GRAPH_SUCCESS_V1",
        nodes=tuple(
            replace(node, route=f"skill:{node.node_id}")
            for node in graph.nodes
        ),
    )
    contracts = (command, work_order, assignment, graph)
    assert contracts[3].result_node_id == result_node_id
    projection_store = WorkerProjectionStore(db_path, clock=lambda: NOW)
    task_store = DurableTaskStore(db_path, clock=lambda: NOW)
    calls: list[str] = []
    completion_order: list[str] = []
    probe = _AsyncFanOutProbe(
        tuple(node_id for node_id, dependencies in specs if not dependencies)
    )
    worker = _ProjectedTerminalWorker(
        projection_store=projection_store,
        probe=probe,
        calls=calls,
        completion_order=completion_order,
        delays=delays,
    )
    runtime = _b5_runtime(
        db_path,
        projection_store=projection_store,
        worker_resolver=_ProjectedResolver(worker),
        task_store=task_store,
    )
    return (
        db_path,
        contracts,
        projection_store,
        task_store,
        runtime,
        probe,
        calls,
        completion_order,
    )


async def _execute(
    db_path: Path,
    contracts: tuple[Any, Any, Any, Any],
    runtime: GovernedTaskCoordinator,
) -> list[dict[str, Any]]:
    app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=lambda: NOW),
        coordinator=runtime,
        node_grant_service=_NodeGrantService(),
    )
    return [
        event
        async for event in app.execute_stream(
            command=contracts[0],
            work_order=contracts[1],
            assignment=contracts[2],
            graph=contracts[3],
        )
    ]


def _assert_b5_terminal_assets(
    store: DurableTaskStore,
    *,
    terminal: dict[str, Any],
) -> dict[str, Any]:
    assert terminal["narrative"] == "durable result from right"
    receipt = terminal["execution_receipt"]
    assert receipt["node_id"] == "right"
    assert receipt["accepted"] is True
    assert receipt["projection"]["parallel_success"]["result_node_id"] == "right"
    graph = store.get_graph("graph-gr03a", 1)
    assert graph["status"] == "COMPLETED"
    assert {node["node_id"]: node["status"] for node in graph["nodes"]} == {
        "left": "ACCEPTED",
        "right": "ACCEPTED",
    }
    snapshot = store.read_scheduler_snapshot("graph-gr03a", 1)
    assert snapshot["budgets"][0]["status"] == "CONSUMED"
    effects = store.list_acceptance_effect_intents(
        graph_id="graph-gr03a",
        graph_revision=1,
    )
    assert {effect["effect_type"] for effect in effects} == {
        "conversation.user_turn",
        "life.foreground_completed",
    }
    decision = store.get_latest_parallel_success_decision("graph-gr03a", 1)
    assert decision is not None
    assert decision["payload"]["result_node_id"] == "right"
    assert {
        outcome["node_id"] for outcome in decision["payload"]["accepted_outcomes"]
    } == {"left", "right"}
    assert decision["decision_ref"] == receipt["projection"]["parallel_success"][
        "decision_ref"
    ]
    return decision


@pytest.mark.parametrize(
    ("delays", "expected_completion_order"),
    [
        ({"left": 0.04, "right": 0.0}, ["right", "left"]),
        ({"left": 0.0, "right": 0.04}, ["left", "right"]),
    ],
)
@pytest.mark.asyncio
async def test_b5_terminal_roots_overlap_and_result_authority_ignores_finish_order(
    tmp_path: Path,
    delays: dict[str, float],
    expected_completion_order: list[str],
) -> None:
    fixture = _b5_fixture(tmp_path, delays=delays)
    db_path, contracts, _, store, runtime, probe, calls, completion_order = fixture

    events = await _execute(db_path, contracts, runtime)

    assert calls == ["left", "right"]
    assert probe.entered_nodes == {"left", "right"}
    assert probe.peak_active == contracts[1].budget_policy.limits.concurrency == 2
    assert probe.active == 0
    assert completion_order == expected_completion_order
    assert len([event for event in events if event.get("done")]) == 1
    _assert_b5_terminal_assets(store, terminal=events[-1])


@pytest.mark.parametrize(
    ("specs", "expected_calls", "expected_result"),
    [
        ((("only", ()),), ["only"], "only"),
        (
            (("first", ()), ("final", ("first",))),
            ["first", "final"],
            "final",
        ),
    ],
)
@pytest.mark.asyncio
async def test_b5_atomic_success_activates_single_and_serial_graphs_before_deferred_nodes(
    tmp_path: Path,
    specs: tuple[tuple[str, tuple[str, ...]], ...],
    expected_calls: list[str],
    expected_result: str,
) -> None:
    fixture = _b5_fixture(
        tmp_path,
        delays={},
        specs=specs,
        concurrency=2,
    )
    db_path, contracts, _, store, runtime, _, calls, _ = fixture

    events = await _execute(db_path, contracts, runtime)

    assert calls == expected_calls
    assert events[-1]["execution_receipt"]["node_id"] == expected_result
    assert events[-1]["execution_receipt"]["accepted"] is True
    assert store.get_graph("graph-gr03a", 1)["status"] == "COMPLETED"
    assert store.read_scheduler_snapshot("graph-gr03a", 1)["budgets"][0][
        "status"
    ] == "CONSUMED"
    decision = store.get_latest_parallel_success_decision("graph-gr03a", 1)
    assert decision is not None
    assert decision["payload"]["result_node_id"] == expected_result


@pytest.mark.asyncio
async def test_b5_atomic_graph_fails_closed_on_flag_off_process_before_runtime_mutation(
    tmp_path: Path,
) -> None:
    fixture = _b5_fixture(tmp_path, delays={})
    db_path, contracts, projection_store, store, _, _, calls, _ = fixture
    forbidden_resolver = _FailingResolver()
    flag_off_runtime = _b5_runtime(
        db_path,
        projection_store=projection_store,
        worker_resolver=forbidden_resolver,
        task_store=store,
        atomic_graph_success_enabled=False,
    )

    with pytest.raises(GovernedRuntimeError) as raised:
        await _execute(db_path, contracts, flag_off_runtime)

    assert raised.value.reason_code == GOVERNED_ADMISSION_INVALID
    assert forbidden_resolver.resolve_count == 0
    assert calls == []
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_graph"
        ).fetchone() == (0,)
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_budget_reservation"
        ).fetchone() == (0,)
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_attempt"
        ).fetchone() == (0,)


@pytest.mark.asyncio
async def test_b5_atomic_graph_without_node_grant_service_is_rejected_before_admission(
    tmp_path: Path,
) -> None:
    fixture = _b5_fixture(tmp_path, delays={})
    db_path, contracts, _, _, runtime, _, calls, _ = fixture
    app = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=lambda: NOW),
        coordinator=runtime,
        node_grant_service=None,
    )

    with pytest.raises(GovernedRuntimeError) as raised:
        async for _ in app.execute_stream(
            command=contracts[0],
            work_order=contracts[1],
            assignment=contracts[2],
            graph=contracts[3],
        ):
            pass

    assert raised.value.reason_code == GOVERNED_ADMISSION_INVALID
    assert calls == []
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_graph"
        ).fetchone() == (0,)
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_budget_reservation"
        ).fetchone() == (0,)


@pytest.mark.asyncio
async def test_b5_store_response_loss_exactly_replays_outer_success(
    tmp_path: Path,
) -> None:
    fixture = _b5_fixture(tmp_path, delays={"left": 0.02, "right": 0.0})
    db_path, contracts, _, store, runtime, probe, calls, _ = fixture
    committed = store.complete_parallel_graph_success
    settlement_calls = 0

    def lose_first_response(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal settlement_calls
        settlement_calls += 1
        result = committed(*args, **kwargs)
        if settlement_calls == 1:
            raise TimeoutError("simulated committed B5 response loss")
        return result

    store.complete_parallel_graph_success = lose_first_response  # type: ignore[method-assign]

    events = await _execute(db_path, contracts, runtime)

    assert settlement_calls == 2
    assert calls == ["left", "right"]
    assert probe.peak_active == 2
    assert events[-1]["execution_receipt"]["replayed"] is True
    _assert_b5_terminal_assets(store, terminal=events[-1])
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_parallel_success_decision"
        ).fetchone() == (1,)
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_acceptance_effect_intent"
        ).fetchone() == (2,)
        assert conn.execute(
            """
            SELECT COUNT(*) FROM task_runtime_outbox
            WHERE event_type IN (
                'TASK_GRAPH_COMPLETED', 'BUDGET_RESERVATION_CONSUMED'
            )
            """
        ).fetchone() == (2,)


@pytest.mark.asyncio
async def test_b5_two_lost_store_responses_reread_frozen_terminal_authority(
    tmp_path: Path,
) -> None:
    fixture = _b5_fixture(tmp_path, delays={"left": 0.01, "right": 0.0})
    db_path, contracts, _, store, runtime, _, calls, _ = fixture
    committed = store.complete_parallel_graph_success
    settlement_calls = 0

    def lose_both_responses(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal settlement_calls
        settlement_calls += 1
        committed(*args, **kwargs)
        raise TimeoutError("simulated repeated committed B5 response loss")

    store.complete_parallel_graph_success = lose_both_responses  # type: ignore[method-assign]

    events = await _execute(db_path, contracts, runtime)

    assert settlement_calls == 2
    assert calls == ["left", "right"]
    assert events[-1]["execution_receipt"]["replayed"] is True
    _assert_b5_terminal_assets(store, terminal=events[-1])


@pytest.mark.asyncio
async def test_b5_restart_after_all_children_accepted_commits_without_worker_rerun(
    tmp_path: Path,
) -> None:
    fixture = _b5_fixture(tmp_path, delays={"left": 0.02, "right": 0.0})
    db_path, contracts, projection_store, store, runtime, probe, calls, _ = fixture
    outer_calls = 0

    def crash_before_outer_commit(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal outer_calls
        outer_calls += 1
        assert store.get_graph("graph-gr03a", 1)["status"] == "ACTIVE"
        assert {
            store.get_node("graph-gr03a", 1, node_id)["status"]
            for node_id in ("left", "right")
        } == {"ACCEPTED"}
        raise _CrashBeforeParallelSuccess()

    store.complete_parallel_graph_success = crash_before_outer_commit  # type: ignore[method-assign]
    with pytest.raises(_CrashBeforeParallelSuccess):
        await _execute(db_path, contracts, runtime)

    assert outer_calls == 1
    assert calls == ["left", "right"]
    assert probe.peak_active == 2
    assert store.get_graph("graph-gr03a", 1)["status"] == "ACTIVE"
    before = store.read_scheduler_snapshot("graph-gr03a", 1)
    assert len(before["attempts"]) == 2
    assert before["budgets"][0]["status"] == "ACTIVE"
    assert store.get_latest_parallel_success_decision("graph-gr03a", 1) is None

    forbidden_resolver = _FailingResolver()
    restarted_store = DurableTaskStore(db_path, clock=lambda: NOW)
    restarted_runtime = _b5_runtime(
        db_path,
        projection_store=projection_store,
        worker_resolver=forbidden_resolver,
        task_store=restarted_store,
    )
    events = await _execute(db_path, contracts, restarted_runtime)

    assert forbidden_resolver.resolve_count == 0
    assert calls == ["left", "right"]
    after = restarted_store.read_scheduler_snapshot("graph-gr03a", 1)
    assert len(after["attempts"]) == len(before["attempts"]) == 2
    _assert_b5_terminal_assets(restarted_store, terminal=events[-1])


@pytest.mark.asyncio
async def test_b5_completed_restart_replays_frozen_decision_without_recompiling_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _b5_fixture(tmp_path, delays={"left": 0.01, "right": 0.0})
    db_path, contracts, projection_store, store, runtime, _, calls, _ = fixture
    first = await _execute(db_path, contracts, runtime)
    first_decision = _assert_b5_terminal_assets(store, terminal=first[-1])

    def forbidden_effect_recompile(**_: Any) -> Any:
        raise AssertionError("completed Graph must use its frozen 0091 authority")

    monkeypatch.setattr(
        "host_brain.task_runtime.coordinator.compile_graph_acceptance_effects",
        forbidden_effect_recompile,
    )
    forbidden_resolver = _FailingResolver()
    restarted_store = DurableTaskStore(db_path, clock=lambda: NOW)
    restarted_runtime = _b5_runtime(
        db_path,
        projection_store=projection_store,
        worker_resolver=forbidden_resolver,
        task_store=restarted_store,
    )

    replay = await _execute(db_path, contracts, restarted_runtime)

    assert forbidden_resolver.resolve_count == 0
    assert calls == ["left", "right"]
    assert replay[-1]["execution_receipt"]["replayed"] is True
    replay_decision = _assert_b5_terminal_assets(
        restarted_store,
        terminal=replay[-1],
    )
    assert replay_decision["decision_ref"] == first_decision["decision_ref"]


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("narrative", "tampered result"),
        ("command_id", "host-command://tampered"),
        ("node_grant_ref", "grant://gr03a/tampered"),
    ],
)
@pytest.mark.asyncio
async def test_b5_tampered_reader_projection_fails_before_outer_commit(
    tmp_path: Path,
    field_name: str,
    value: Any,
) -> None:
    fixture = _b5_fixture(tmp_path, delays={"left": 0.01, "right": 0.0})
    db_path, contracts, projection_store, store, runtime, _, calls, _ = fixture
    runtime._projection_reader = _TamperedProjectionReader(  # noqa: SLF001
        projection_store,
        field_name=field_name,
        value=value,
    )

    with pytest.raises(GovernedRuntimeError) as raised:
        await _execute(db_path, contracts, runtime)

    assert raised.value.reason_code == GOVERNED_DURABLE_STATE_CONFLICT
    assert calls == ["left", "right"]
    assert store.get_graph("graph-gr03a", 1)["status"] == "ACTIVE"
    assert {
        store.get_node("graph-gr03a", 1, node_id)["status"]
        for node_id in ("left", "right")
    } == {"ACCEPTED"}
    snapshot = store.read_scheduler_snapshot("graph-gr03a", 1)
    assert snapshot["budgets"][0]["status"] == "ACTIVE"
    assert store.list_acceptance_effect_intents(
        graph_id="graph-gr03a",
        graph_revision=1,
    ) == []
    assert store.get_latest_parallel_success_decision("graph-gr03a", 1) is None
