from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import sqlite3
import inspect

import pytest

from host_brain.agent_runtime.task_scheduler import (
    CanonicalShadowScheduler,
    SchedulerAction,
    ShadowSchedulerError,
    evaluate_configured_shadow,
)
from host_brain.agent_runtime.task_graph import TaskGraph, TaskGraphStatus, TaskNode, TaskNodeStatus
from host_brain.agent_runtime.task_store import DurableTaskStore, OutboxMessage, build_outbox_payload_ref


def _node(node_id: str, *, status: str = "CREATED", dependencies: list[str] | None = None, refs: list[str] | None = None, retry_limit: int = 2, backoff: int = 100) -> dict:
    return {
        "node_id": node_id,
        "status": status,
        "status_version": 0,
        "dependencies": dependencies or [],
        "input_refs": refs or [],
        "retry_policy": {
            "retry_limit": retry_limit,
            "retry_backoff_ms": backoff,
            "idempotency_key": f"idem:{node_id}",
            "fallback_policy": "",
        },
    }


def _graph(nodes: list[dict]) -> dict:
    return {"graph": {"graph_id": "graph-1", "revision": 3, "nodes": deepcopy(nodes)}, "nodes": deepcopy(nodes)}


def _actions(report) -> dict[str, str]:
    return {item.node_id: item.action.value for item in report.decisions}


def test_t_nle_07_ready_dependency_and_artifact_selection_is_pure_and_replayable() -> None:
    nodes = [
        _node("a", status="ACCEPTED"),
        _node("b", dependencies=["a"], refs=["artifact://graph-1/3/a"]),
        _node("c", dependencies=["b"], refs=["artifact://graph-1/3/b"]),
    ]
    graph = _graph(nodes)
    before = deepcopy(graph)
    scheduler = CanonicalShadowScheduler()
    report = scheduler.evaluate(
        graph,
        artifact_refs={"a": "artifact://graph-1/3/a"},
        now_ts_ms=1_000,
    )
    assert _actions(report) == {"a": "HOLD", "b": "READY", "c": "BLOCKED"}
    assert report.to_dict()["external_side_effect_count"] == 0
    assert graph == before
    assert report.to_dict() == scheduler.evaluate(
        graph,
        artifact_refs={"a": "artifact://graph-1/3/a"},
        now_ts_ms=1_000,
    ).to_dict()


def test_t_nle_07_missing_or_unbound_dependency_artifact_never_starts() -> None:
    scheduler = CanonicalShadowScheduler()
    graph = _graph([_node("a", status="VERIFIED"), _node("b", dependencies=["a"], refs=["artifact://expected"])])
    missing = scheduler.evaluate(graph, now_ts_ms=10)
    assert _actions(missing)["b"] == SchedulerAction.BLOCKED.value
    with pytest.raises(ShadowSchedulerError) as error:
        scheduler.evaluate(graph, artifact_refs={"a": "artifact://substituted"}, now_ts_ms=10)
    assert error.value.reason_code == "SCHEDULER_SNAPSHOT_INVALID"


def test_t_nle_07_retry_backoff_and_exhaustion_are_exact_and_replayable() -> None:
    scheduler = CanonicalShadowScheduler()
    graph = _graph([_node("a", status="RETRY_PENDING", retry_limit=2, backoff=100)])
    failed = [{"graph_id": "graph-1", "graph_revision": 3, "node_id": "a", "attempt_no": 1, "status": "FAILED", "updated_ts_ms": 1_000, "error_ref": "reason:TIMEOUT"}]
    waiting = scheduler.evaluate(graph, attempts=failed, now_ts_ms=1_099).decisions[0]
    assert (waiting.action, waiting.attempt_no, waiting.eligible_ts_ms) == (SchedulerAction.RETRY_WAIT, 2, 1_100)
    ready = scheduler.evaluate(graph, attempts=failed, now_ts_ms=1_100).decisions[0]
    assert (ready.action, ready.attempt_no) == (SchedulerAction.READY, 2)
    exhausted_attempts = failed + [
        {"graph_id": "graph-1", "graph_revision": 3, "node_id": "a", "attempt_no": 2, "status": "FAILED", "updated_ts_ms": 1_500, "error_ref": "reason:TIMEOUT"},
        {"graph_id": "graph-1", "graph_revision": 3, "node_id": "a", "attempt_no": 3, "status": "FAILED", "updated_ts_ms": 2_000, "error_ref": "reason:TIMEOUT"},
    ]
    assert scheduler.evaluate(graph, attempts=exhausted_attempts, now_ts_ms=9_000).decisions[0].action is SchedulerAction.RETRY_EXHAUSTED


@pytest.mark.parametrize(
    ("status", "expected"),
    [("CANCEL_REQUESTED", SchedulerAction.CANCEL), ("READY", SchedulerAction.RECOVER)],
)
def test_t_nle_07_cancel_and_restart_recovery_do_not_execute(status: str, expected: SchedulerAction) -> None:
    attempts = []
    if expected is SchedulerAction.RECOVER:
        attempts = [{"graph_id": "graph-1", "graph_revision": 3, "node_id": "a", "attempt_no": 1, "status": "RECOVERABLE", "updated_ts_ms": 1}]
    report = CanonicalShadowScheduler().evaluate(_graph([_node("a", status=status)]), attempts=attempts, now_ts_ms=2)
    assert report.decisions[0].action is expected
    assert report.to_dict()["external_side_effect_count"] == 0


def test_t_nle_07_budget_denial_and_legacy_diff_are_explicit() -> None:
    report = CanonicalShadowScheduler().evaluate(
        _graph([_node("a", status="READY")]),
        budget_allowed={"a": False},
        legacy_decisions=[{"node_id": "a", "action": "READY", "reason_code": "legacy"}],
        now_ts_ms=5,
    )
    assert report.decisions[0].action is SchedulerAction.BUDGET_BLOCKED
    assert report.unexplained_diff_count == 1
    assert report.legacy_digest.startswith("sha256:")


@pytest.mark.parametrize("bad", [True, 1.5, "1", None])
def test_t_nle_07_integer_fields_fail_closed_without_truncation(bad) -> None:
    graph = _graph([_node("a")])
    graph["graph"]["revision"] = bad
    with pytest.raises(ShadowSchedulerError):
        CanonicalShadowScheduler().evaluate(graph, now_ts_ms=1)


def test_t_nle_07_shadow_has_no_executor_worker_tool_or_provider_surface() -> None:
    scheduler = CanonicalShadowScheduler()
    public = {name for name in dir(scheduler) if not name.startswith("_")}
    assert public == {"evaluate", "evaluate_durable"}
    source = inspect.getsource(__import__("host_brain.agent_runtime.task_scheduler", fromlist=["*"]))
    for forbidden in ("agent_runtime.executor", "subprocess", "requests", "httpx", "execute_agent_runtime_payload"):
        assert forbidden not in source


def test_t_nle_07_durable_snapshot_is_read_only_and_shadow_requires_owner(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    db_path = tmp_path / "scheduler.sqlite3"
    migrations = root / "host_brain" / "audit" / "migrations"
    with sqlite3.connect(db_path) as conn:
        conn.executescript((migrations / "0052_task_runtime.sql").read_text(encoding="utf-8"))
        conn.executescript((migrations / "0054_task_runtime_recovery.sql").read_text(encoding="utf-8"))
    store = DurableTaskStore(db_path, clock=lambda: 100)
    node = TaskNode(
        node_id="a", dependencies=(), route="skill:test", input_refs=(),
        output_contract={"type": "result"},
        retry_policy={"retry_limit": 1, "retry_backoff_ms": 10, "idempotency_key": "idem:a", "fallback_policy": ""},
        risk_level=1, acceptance_criteria=("done",), status=TaskNodeStatus.READY,
    )
    graph = TaskGraph(
        graph_id="graph-1", root_work_order_id="wo-1", revision=1,
        planner_ref="planner://1", status=TaskGraphStatus.CREATED,
        created_at=1, nodes=(node,),
    )
    store.create_graph(
        graph,
        outbox=OutboxMessage.create(
            aggregate_type="TASK_GRAPH", aggregate_id="graph-1", aggregate_version=0,
            event_type="TASK_GRAPH_CREATED",
            payload_ref=build_outbox_payload_ref(aggregate_type="TASK_GRAPH", aggregate_id="graph-1", aggregate_version=0),
            idempotency_key="create:graph-1",
        ),
    )
    before = db_path.read_bytes()
    with pytest.raises(ShadowSchedulerError) as error:
        evaluate_configured_shadow(
            CanonicalShadowScheduler(), store, "graph-1", 1,
            runtime_mode="shadow", configured_owner="runtime-owner",
            confirmed_owner="wrong-owner", now_ts_ms=100,
        )
    assert error.value.reason_code == "SCHEDULER_OWNER_REQUIRED"
    report = evaluate_configured_shadow(
        CanonicalShadowScheduler(), store, "graph-1", 1,
        runtime_mode="shadow", configured_owner="runtime-owner",
        confirmed_owner="runtime-owner", now_ts_ms=100,
    )
    assert report is not None and report.decisions[0].action is SchedulerAction.READY
    assert db_path.read_bytes() == before
    assert evaluate_configured_shadow(
        CanonicalShadowScheduler(), object(), "missing", 1,
        runtime_mode="legacy", configured_owner="", confirmed_owner="", now_ts_ms=100,
    ) is None


def test_t_nle_07_expired_or_missing_active_lease_recovers_and_success_never_replays() -> None:
    graph = _graph([_node("a", status="LEASED")])
    class Store:
        def __init__(self, attempts, leases):
            self.attempts = attempts
            self.leases = leases
        def read_scheduler_snapshot(self, graph_id, revision):
            return {"graph_record": graph, "attempts": deepcopy(self.attempts), "active_leases": deepcopy(self.leases), "budgets": []}
    leased = {"attempt_id": "attempt-1", "graph_id": "graph-1", "graph_revision": 3, "node_id": "a", "attempt_no": 1, "status": "RUNNING", "updated_ts_ms": 1}
    expired = {"attempt_id": "attempt-1", "expires_ts_ms": 99, "fencing_token": 2}
    scheduler = CanonicalShadowScheduler()
    assert scheduler.evaluate_durable(Store([leased], [expired]), "graph-1", 3, now_ts_ms=100).decisions[0].action is SchedulerAction.RECOVER
    assert scheduler.evaluate_durable(Store([leased], []), "graph-1", 3, now_ts_ms=100).decisions[0].action is SchedulerAction.RECOVER
    succeeded = dict(leased, status="SUCCEEDED")
    assert scheduler.evaluate_durable(Store([succeeded], []), "graph-1", 3, now_ts_ms=100).decisions[0].reason_code == "SCHEDULER_ATTEMPT_SUCCEEDED"


def test_t_nle_07_attempt_identity_gaps_and_duplicates_fail_closed() -> None:
    graph = _graph([_node("a", status="RETRY_PENDING")])
    attempts = [
        {"graph_id": "graph-1", "graph_revision": 3, "node_id": "a", "attempt_no": number, "status": "FAILED", "updated_ts_ms": 1, "error_ref": "reason:TIMEOUT"}
        for number in (1, 3)
    ]
    with pytest.raises(ShadowSchedulerError):
        CanonicalShadowScheduler().evaluate(graph, attempts=attempts, now_ts_ms=100)


def test_t_nle_07_executor_adapter_is_legacy_noop_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HOST_TASK_RUNTIME_MODE", raising=False)
    monkeypatch.delenv("HOST_TASK_RUNTIME_SHADOW_OWNER", raising=False)
    from host_brain.app.settings.core import get_core_settings
    get_core_settings.cache_clear()
    try:
        from host_brain.agent_runtime.executor import evaluate_task_runtime_shadow
        assert evaluate_task_runtime_shadow(
            object(), "graph-1", 1, confirmed_owner="", now_ts_ms=1
        ) is None
    finally:
        get_core_settings.cache_clear()


def test_t_nle_07_frozen_max_attempts_policy_maps_without_mutating_snapshot() -> None:
    graph = _graph([_node("a", status="RETRY_PENDING")])
    graph["nodes"][0]["retry_policy"] = {"max_attempts": 2}
    graph["graph"]["nodes"][0]["retry_policy"] = {"max_attempts": 2}
    before = deepcopy(graph)
    attempt = {"graph_id": "graph-1", "graph_revision": 3, "node_id": "a", "attempt_no": 1, "status": "FAILED", "updated_ts_ms": 1, "error_ref": "reason:TIMEOUT"}
    decision = CanonicalShadowScheduler().evaluate(graph, attempts=[attempt], now_ts_ms=1).decisions[0]
    assert (decision.action, decision.attempt_no) == (SchedulerAction.READY, 2)
    assert graph == before


def test_t_nle_07_durable_budget_status_is_enforced_without_override() -> None:
    graph = _graph([_node("a", status="READY")])
    class Store:
        def read_scheduler_snapshot(self, graph_id, revision):
            return {
                "graph_record": graph,
                "attempts": [],
                "active_leases": [],
                "budgets": [{"graph_id": "graph-1", "graph_revision": 3, "node_id": "a", "status": "EXCEEDED"}],
            }
    decision = CanonicalShadowScheduler().evaluate_durable(Store(), "graph-1", 3, now_ts_ms=1).decisions[0]
    assert decision.action is SchedulerAction.BUDGET_BLOCKED


def test_t_nle_07_retry_classifier_only_retries_explicit_transient_codes() -> None:
    graph = _graph([_node("a", status="RETRY_PENDING")])
    terminal = {"graph_id": "graph-1", "graph_revision": 3, "node_id": "a", "attempt_no": 1, "status": "FAILED", "updated_ts_ms": 1, "error_ref": "reason:POLICY_DENIED"}
    decision = CanonicalShadowScheduler().evaluate(graph, attempts=[terminal], now_ts_ms=1_000).decisions[0]
    assert decision.reason_code == "SCHEDULER_RETRY_CLASSIFIED_TERMINAL"
