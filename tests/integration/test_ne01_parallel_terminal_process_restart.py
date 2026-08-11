from __future__ import annotations

import asyncio
from dataclasses import asdict, replace
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
from typing import Any, AsyncIterator

import pytest

from host_brain.agent_runtime.runtime_router import CanonicalRuntimeRouter
from host_brain.app.governed_task_adapter import GovernedHostRuntime
from host_brain.orchestration import GovernedCommandApplication
from host_brain.persistence.host_command_repository import HostCommandRepository
from host_brain.persistence.review_repository import ReviewStore
from host_brain.persistence.task_repository import DurableTaskStore
from host_brain.persistence.worker_projection_repository import WorkerProjectionStore
from host_brain.task_runtime import RetryableWorkerPreparationError
from host_brain.task_runtime.review_runtime import TaskNodeReviewRuntime
from tests.integration.test_gr03a_governed_durable_dag import (
    NOW,
    _Acceptance,
    _AsyncFanOutProbe,
    _DurableReviewWorker,
    _FailingResolver,
    _FanOutWorker,
    _StaticResolver,
    _Worker,
    _contracts,
    _coordinator,
    _migrated_db,
)
from tests.integration.test_ne01_host_worker_process_restart import _run_process


_OUTER_SETTLEMENT_EXIT_CODE = 94
_GRAPH_ID = "graph-gr03a"
_SPECS = (
    ("left", ()),
    ("right", ()),
    ("join", ("left", "right")),
)
_SCENARIO_RETRY_FAILURE = "retry_failure"
_SCENARIO_BUDGET = "budget"
_SCENARIO_REVIEW = "review"


class _RetryLeftFailRightResolver:
    """Produce a retryable sibling and a terminal sibling in one ready-set."""

    def resolve(self, *, executor: str, worker_route: str) -> _Worker:
        assert executor == "canonical"
        node_id = worker_route.removeprefix("worker:")
        if node_id == "left":
            raise RetryableWorkerPreparationError(
                "WORKER_PREPARATION_TRANSIENT",
                "left worker is temporarily unavailable",
            )
        raise RuntimeError(f"worker registry failed before reservation: {node_id}")


class _RecordingApplication:
    """Observe the exact terminal count without changing application semantics."""

    def __init__(self, delegate: GovernedCommandApplication) -> None:
        self._delegate = delegate
        self.terminals: list[dict[str, Any]] = []

    async def execute_stream(self, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        async for event in self._delegate.execute_stream(**kwargs):
            normalized = dict(event)
            if normalized.get("done") is True:
                self.terminals.append(normalized)
            yield normalized


def _scenario_contracts(scenario: str) -> tuple[Any, Any, Any, Any]:
    token_limit = 15 if scenario == _SCENARIO_BUDGET else 100_000
    command, work_order, assignment, graph = _contracts(
        _SPECS,
        concurrency=2,
        token_limit=token_limit,
    )
    if scenario == _SCENARIO_RETRY_FAILURE:
        graph = replace(
            graph,
            nodes=tuple(
                replace(
                    node,
                    retry_policy={
                        "max_attempts": 2,
                        "retry_backoff_ms": 60_000,
                    },
                )
                if node.node_id == "left"
                else node
                for node in graph.nodes
            ),
        )
    return command, work_order, assignment, graph


def _seed_outer_settlement_crash_process(
    db_path: str,
    marker_path: str,
    scenario: str,
) -> None:
    asyncio.run(
        _seed_outer_settlement_crash(
            db_path=Path(db_path),
            marker_path=Path(marker_path),
            scenario=scenario,
        )
    )


async def _seed_outer_settlement_crash(
    *,
    db_path: Path,
    marker_path: Path,
    scenario: str,
) -> None:
    contracts = _scenario_contracts(scenario)
    calls: list[str] = []
    acceptance: Any = _Acceptance()
    review_runtime: TaskNodeReviewRuntime | None = None
    projection_reader: WorkerProjectionStore | None = None

    if scenario == _SCENARIO_RETRY_FAILURE:
        resolver: Any = _RetryLeftFailRightResolver()
    elif scenario == _SCENARIO_BUDGET:
        probe = _AsyncFanOutProbe(("left", "right"))
        worker = _FanOutWorker(
            calls,
            db_path=db_path,
            probe=probe,
            require_full_barrier=True,
            node_delays={"right": 0.08},
            token_counts={"left": 5, "right": 20},
        )
        resolver = _StaticResolver(worker)
    elif scenario == _SCENARIO_REVIEW:
        projection_reader = WorkerProjectionStore(db_path, clock=lambda: NOW)
        worker = _DurableReviewWorker(calls, projection_reader)
        resolver = _StaticResolver(worker)
        acceptance = _Acceptance(decisions_by_node={"right": "REVIEW"})
        review_runtime = TaskNodeReviewRuntime(
            ReviewStore(db_path, clock=lambda: NOW),
            clock=lambda: NOW,
        )
    else:  # pragma: no cover - guarded by the parent process.
        raise AssertionError(f"unsupported process scenario: {scenario}")

    coordinator = _coordinator(
        db_path,
        calls,
        worker_resolver=resolver,
        acceptance=acceptance,
        review_runtime=review_runtime,
        projection_reader=projection_reader,
        bounded_fanout_enabled=True,
    )

    def crash_before_outer_settlement(*_: Any, **kwargs: Any) -> dict[str, Any]:
        snapshot = coordinator._store.read_scheduler_snapshot(_GRAPH_ID, 1)  # noqa: SLF001
        marker_path.write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "scenario": scenario,
                    "decision_ref": kwargs["decision_ref"],
                    "decision_payload": kwargs["decision_payload"],
                    "calls": calls,
                    "graph": snapshot["graph_record"],
                    "nodes": snapshot["graph_record"]["nodes"],
                    "attempts": snapshot["attempts"],
                    "budgets": snapshot["budgets"],
                    "budget_usage": snapshot["budget_usage"],
                    "reviews": (
                        ReviewStore(
                            db_path,
                            clock=lambda: NOW,
                        ).list_reviews(task_graph_id=_GRAPH_ID)
                        if scenario == _SCENARIO_REVIEW
                        else []
                    ),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        os._exit(_OUTER_SETTLEMENT_EXIT_CODE)

    coordinator._store.settle_parallel_graph_budget = (  # type: ignore[method-assign]  # noqa: SLF001
        crash_before_outer_settlement
    )
    application = GovernedCommandApplication(
        command_repository=HostCommandRepository(db_path, clock=lambda: NOW),
        coordinator=coordinator,
    )
    _ = [
        event
        async for event in application.execute_stream(
            command=contracts[0],
            work_order=contracts[1],
            assignment=contracts[2],
            graph=contracts[3],
        )
    ]
    raise AssertionError("seed process did not exit before outer settlement")


def _recover_outer_settlement_process(
    db_path: str,
    result_path: str,
    scenario: str,
    owner_id: str,
) -> None:
    asyncio.run(
        _recover_outer_settlement(
            db_path=Path(db_path),
            result_path=Path(result_path),
            scenario=scenario,
            owner_id=owner_id,
        )
    )


async def _recover_outer_settlement(
    *,
    db_path: Path,
    result_path: Path,
    scenario: str,
    owner_id: str,
) -> None:
    durable = DurableTaskStore(db_path, clock=lambda: NOW)
    startup = durable.recover_startup(
        recovery_id=f"parallel-terminal-process:{scenario}:{owner_id}"
    )
    commands = HostCommandRepository(
        db_path,
        clock=lambda: NOW,
        task_store=durable,
    )
    resolver = _FailingResolver()
    calls: list[str] = []
    acceptance: Any = _Acceptance()
    review_runtime: TaskNodeReviewRuntime | None = None
    projection_reader: WorkerProjectionStore | None = None
    if scenario == _SCENARIO_REVIEW:
        projection_reader = WorkerProjectionStore(db_path, clock=lambda: NOW)
        review_runtime = TaskNodeReviewRuntime(
            ReviewStore(db_path, clock=lambda: NOW),
            clock=lambda: NOW,
        )
        acceptance = _Acceptance(decisions_by_node={"right": "REVIEW"})

    application = _RecordingApplication(
        GovernedCommandApplication(
            command_repository=commands,
            coordinator=_coordinator(
                db_path,
                calls,
                task_store=durable,
                worker_resolver=resolver,
                acceptance=acceptance,
                review_runtime=review_runtime,
                projection_reader=projection_reader,
                bounded_fanout_enabled=True,
            ),
        )
    )
    runtime = GovernedHostRuntime(
        application=application,
        runtime_router=CanonicalRuntimeRouter(),
        command_repository=commands,
        task_store=durable,
        supported_routes=frozenset({"chat"}),
        survivor_interval_ms=60_000,
    )
    await runtime.activate()
    report = await runtime.recover_startup_graphs()
    await runtime.aclose()
    snapshot = durable.read_scheduler_snapshot(_GRAPH_ID, 1)
    with sqlite3.connect(db_path) as conn:
        terminal_outbox = [
            {
                "event_type": str(row[0]),
                "count": int(row[1]),
            }
            for row in conn.execute(
                """
                SELECT event_type, COUNT(*)
                  FROM task_runtime_outbox
                 WHERE event_type IN (
                       'TASK_GRAPH_FAILED',
                       'TASK_GRAPH_BLOCKED',
                       'TASK_GRAPH_COMPLETED',
                       'BUDGET_RESERVATION_RELEASED'
                 )
                 GROUP BY event_type
                 ORDER BY event_type
                """
            ).fetchall()
        ]
        denial_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM task_runtime_review_denial_settlement"
            ).fetchone()[0]
        )
    result_path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "startup": asdict(startup),
                "report": list(report),
                "resolve_count": resolver.resolve_count,
                "worker_calls": calls,
                "terminal_count": len(application.terminals),
                "terminals": application.terminals,
                "graph": snapshot["graph_record"],
                "nodes": snapshot["graph_record"]["nodes"],
                "attempts": snapshot["attempts"],
                "budgets": snapshot["budgets"],
                "terminal_outbox": terminal_outbox,
                "denial_count": denial_count,
                "reviews": ReviewStore(
                    db_path,
                    clock=lambda: NOW,
                ).list_reviews(task_graph_id=_GRAPH_ID),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _spawn_recovery(
    context: multiprocessing.context.SpawnContext,
    *,
    db_path: Path,
    result_path: Path,
    scenario: str,
    owner_id: str,
) -> dict[str, Any]:
    _run_process(
        context,
        target=_recover_outer_settlement_process,
        args=(str(db_path), str(result_path), scenario, owner_id),
        expected_exit_code=0,
    )
    return json.loads(result_path.read_text(encoding="utf-8"))


def _node(records: list[dict[str, Any]], node_id: str) -> dict[str, Any]:
    matches = [record for record in records if record["node_id"] == node_id]
    assert len(matches) == 1
    return matches[0]


@pytest.mark.parametrize(
    ("scenario", "expected_status", "expected_decision_kind"),
    (
        (_SCENARIO_RETRY_FAILURE, "FAILED", "FAILURE"),
        (_SCENARIO_BUDGET, "BUDGET_BLOCKED", "BUDGET"),
    ),
)
def test_parallel_failure_budget_and_retry_settle_once_after_process_restart(
    tmp_path: Path,
    scenario: str,
    expected_status: str,
    expected_decision_kind: str,
) -> None:
    db_path = _migrated_db(tmp_path)
    context = multiprocessing.get_context("spawn")
    marker_path = tmp_path / f"{scenario}-crash.json"
    _run_process(
        context,
        target=_seed_outer_settlement_crash_process,
        args=(str(db_path), str(marker_path), scenario),
        expected_exit_code=_OUTER_SETTLEMENT_EXIT_CODE,
    )
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert marker["graph"]["status"] == "ACTIVE"
    assert marker["decision_payload"]["decision_kind"] == expected_decision_kind
    if scenario == _SCENARIO_RETRY_FAILURE:
        assert _node(marker["nodes"], "left")["status"] == "RETRY_PENDING"
        assert _node(marker["nodes"], "right")["status"] == "FAILED"
    else:
        assert marker["budgets"][0]["status"] == "EXCEEDED"

    recovered = _spawn_recovery(
        context,
        db_path=db_path,
        result_path=tmp_path / f"{scenario}-recovered.json",
        scenario=scenario,
        owner_id=f"{scenario}-owner-b",
    )
    assert recovered["resolve_count"] == 0
    assert recovered["worker_calls"] == []
    assert recovered["terminal_count"] == 1
    receipt = recovered["terminals"][0]["execution_receipt"]
    assert receipt["status"] == expected_status
    assert receipt["projection"]["parallel_settlement"]["decision_ref"] == (
        marker["decision_ref"]
    )
    assert recovered["graph"]["status"] in {"FAILED", "BLOCKED"}
    assert recovered["budgets"][0]["status"] == "RELEASED"

    terminal = _spawn_recovery(
        context,
        db_path=db_path,
        result_path=tmp_path / f"{scenario}-terminal.json",
        scenario=scenario,
        owner_id=f"{scenario}-owner-c",
    )
    assert terminal["report"] == []
    assert terminal["resolve_count"] == 0
    assert terminal["worker_calls"] == []
    assert terminal["terminal_count"] == 0
    assert terminal["graph"] == recovered["graph"]
    assert terminal["terminal_outbox"] == recovered["terminal_outbox"]
    inventory = DurableTaskStore(
        db_path,
        clock=lambda: NOW,
    ).inventory_terminal_reconciliation_cases(limit=10_000)
    assert inventory["discovered_count"] == 0
    assert inventory["inserted_count"] == 0
    assert not inventory["cases"]


def test_parallel_review_blocks_then_denies_without_worker_replay_after_restart(
    tmp_path: Path,
) -> None:
    db_path = _migrated_db(tmp_path)
    context = multiprocessing.get_context("spawn")
    marker_path = tmp_path / "review-crash.json"
    _run_process(
        context,
        target=_seed_outer_settlement_crash_process,
        args=(str(db_path), str(marker_path), _SCENARIO_REVIEW),
        expected_exit_code=_OUTER_SETTLEMENT_EXIT_CODE,
    )
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert marker["graph"]["status"] == "ACTIVE"
    assert marker["decision_payload"]["decision_kind"] == "REVIEW"
    assert _node(marker["nodes"], "left")["status"] == "ACCEPTED"
    assert _node(marker["nodes"], "right")["status"] == (
        "WAITING_ACCEPTANCE_REVIEW"
    )
    assert len(marker["reviews"]) == 1

    blocked = _spawn_recovery(
        context,
        db_path=db_path,
        result_path=tmp_path / "review-blocked.json",
        scenario=_SCENARIO_REVIEW,
        owner_id="review-owner-b",
    )
    assert blocked["resolve_count"] == 0
    assert blocked["worker_calls"] == []
    assert blocked["terminal_count"] == 1
    blocked_receipt = blocked["terminals"][0]["execution_receipt"]
    assert blocked_receipt["status"] == "WAITING_ACCEPTANCE_REVIEW"
    assert blocked_receipt["projection"]["parallel_settlement"]["decision_ref"] == (
        marker["decision_ref"]
    )
    assert blocked["graph"]["status"] == "BLOCKED"
    assert blocked["budgets"][0]["status"] == "ACTIVE"

    review = blocked["reviews"][0]["review"]
    rejected = ReviewStore(
        db_path,
        clock=lambda: NOW + 1,
    ).reject_review(
        review["review_id"],
        actor_id="reviewer://parallel-process-restart",
    )
    assert rejected["review"]["status"] == "REJECTED"

    denied = _spawn_recovery(
        context,
        db_path=db_path,
        result_path=tmp_path / "review-denied.json",
        scenario=_SCENARIO_REVIEW,
        owner_id="review-owner-c",
    )
    assert denied["resolve_count"] == 0
    assert denied["worker_calls"] == []
    assert denied["terminal_count"] == 1
    denied_receipt = denied["terminals"][0]["execution_receipt"]
    assert denied_receipt["status"] == "REVIEW_DENIED"
    assert denied["graph"]["status"] == "FAILED"
    assert denied["budgets"][0]["status"] == "RELEASED"
    assert denied["denial_count"] == 1

    terminal = _spawn_recovery(
        context,
        db_path=db_path,
        result_path=tmp_path / "review-terminal.json",
        scenario=_SCENARIO_REVIEW,
        owner_id="review-owner-d",
    )
    assert terminal["report"] == []
    assert terminal["resolve_count"] == 0
    assert terminal["worker_calls"] == []
    assert terminal["terminal_count"] == 0
    assert terminal["graph"] == denied["graph"]
    assert terminal["denial_count"] == 1
