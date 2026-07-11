from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sqlite3

import pytest
from fastapi import HTTPException

from host_brain.agent_runtime.operator_read_model import (
    EXECUTION_INDEX_SCHEMA_VERSION,
    EXECUTION_REVIEW_SCHEMA_VERSION,
    OperatorReadModelService,
    build_execution_index_read_model,
    build_execution_review_read_model,
)
from host_brain.app.obs_routes_task_runtime import (
    _get_operator_read_model_service,
    get_task_runtime_execution,
    list_task_runtime_executions,
)
from host_brain.app.settings.core import get_core_settings
from host_brain.integrations.runtime_events import RuntimeEvent, RuntimeEventStore


def _node(node_id: str, *, dependencies: list[str], status: str, version: int) -> dict:
    return {
        "graph_id": "graph-1",
        "graph_revision": 3,
        "node_id": node_id,
        "status": status,
        "status_version": version,
        "created_ts_ms": 100,
        "updated_ts_ms": 900,
        "node": {
            "schema_version": "Natsume.agent.task_node.v1",
            "node_id": node_id,
            "dependencies": dependencies,
            "route": f"worker.{node_id}",
            "input_refs": [],
            "output_contract": {},
            "retry_policy": {"max_attempts": 2},
            "risk_level": 2,
            "acceptance_criteria": [f"{node_id} evidence exists"],
            "status": status,
            "status_version": version,
        },
    }


def _snapshot() -> dict:
    graph = {
        "schema_version": "Natsume.agent.task_graph.v1",
        "graph_id": "graph-1",
        "root_work_order_id": "work-1",
        "revision": 3,
        "planner_ref": "planner://canonical/revision-3",
        "status": "COMPLETED",
        "created_at": 100,
        "nodes": [],
    }
    nodes = [
        _node("plan", dependencies=[], status="ACCEPTED", version=6),
        _node("execute", dependencies=["plan"], status="ACCEPTED", version=8),
        _node("publish", dependencies=["execute"], status="ACCEPTED", version=7),
    ]
    graph["nodes"] = [dict(item["node"]) for item in nodes]
    return {
        "graph_record": {
            "graph_id": "graph-1",
            "revision": 3,
            "status": "COMPLETED",
            "status_version": 2,
            "source_kind": "CANONICAL",
            "created_ts_ms": 100,
            "updated_ts_ms": 1_000,
            "graph": graph,
            "nodes": nodes,
        },
        "attempts": [
            {
                "attempt_id": "attempt-plan-1",
                "node_id": "plan",
                "attempt_no": 1,
                "status": "SUCCEEDED",
                "status_version": 3,
                "owner_id": "worker-1",
                "created_ts_ms": 200,
                "started_ts_ms": 220,
                "completed_ts_ms": 300,
                "updated_ts_ms": 300,
                "operation_fingerprint": "must-not-leak",
            },
            {
                "attempt_id": "attempt-execute-2",
                "node_id": "execute",
                "attempt_no": 2,
                "status": "SUCCEEDED",
                "status_version": 4,
                "owner_id": "worker-2",
                "created_ts_ms": 400,
                "started_ts_ms": 430,
                "completed_ts_ms": 700,
                "updated_ts_ms": 700,
            },
        ],
        "active_leases": [],
        "budgets": [
            {
                "budget_id": "budget-1",
                "scope": "graph-1",
                "node_id": "execute",
                "attempt_id": "attempt-execute-2",
                "status": "CONSUMED",
                "status_version": 2,
                "limits": {"token_count": 1_000, "tool_call_count": 10, "time_ms": 20_000},
                "reserved": {"token_count": 800, "tool_call_count": 8, "time_ms": 15_000},
                "consumed": {"token_count": 500, "tool_call_count": 5, "time_ms": 9_000},
                "attempted_consumed": {"token_count": 500, "tool_call_count": 5, "time_ms": 9_000},
                "exceeded_dimensions": [],
                "created_ts_ms": 350,
                "updated_ts_ms": 710,
            }
        ],
    }


def _review(review_id: str, *, status: str, expires_at: int, token_ref: str | None = None) -> dict:
    return {
        "review": {
            "review_id": review_id,
            "review_type": "PRE_EXECUTION_APPROVAL",
            "subject_ref": "task-node://graph-1/execute",
            "subject_version": 4,
            "requester": "runtime",
            "reason": "Approve a governed side effect; api_key=must-not-leak",
            "risk_level": 4,
            "status": status,
            "expires_at": expires_at,
            "resume_phase": "PRE_EXECUTION",
            "checkpoint_id": "checkpoint://graph-1/execute/4",
            "token_ref": token_ref,
            "resume_count": 0,
        },
        "status_version": 1,
        "work_order_id": "work-1",
        "task_graph_id": "graph-1",
        "task_node_id": "execute",
        "side_effects": ["network write"],
        "evidence_refs": ["evidence://graph-1/review"],
        "created_ts_ms": 500,
        "updated_ts_ms": 520,
    }


def _event(cursor: int, event_type: str, aggregate_type: str, aggregate_id: str) -> dict:
    return {
        "cursor": cursor,
        "event_id": f"event-{cursor}",
        "event_type": event_type,
        "source_kind": "TASK_STORE_OUTBOX",
        "source_ref": f"taskstore://outbox/{cursor}",
        "aggregate_type": aggregate_type,
        "aggregate_id": aggregate_id,
        "aggregate_version": cursor,
        "payload_ref": f"taskstore://event/{aggregate_type}/{aggregate_id}/{cursor}",
        "payload": {"thought": "raw thought sentinel"},
        "evidence_refs": [f"evidence://graph-1/{cursor}"],
        "dedupe_key": f"dedupe-{cursor}",
        "redaction_level": "INTERNAL_REDACTED",
        "ts_ms": 100 + cursor,
    }


def test_t_nle_21_complex_task_replays_from_plan_to_terminal() -> None:
    events = [
        _event(1, "TASK_GRAPH_CREATED", "TASK_GRAPH", "graph-1"),
        _event(2, "TASK_NODE_VERIFIED", "TASK_NODE", "graph-1@3/plan"),
        _event(3, "TASK_NODE_VERIFIED", "TASK_NODE", "graph-1@3/execute"),
        _event(4, "TASK_NODE_VERIFIED", "TASK_NODE", "graph-1@3/publish"),
        _event(5, "TASK_GRAPH_COMPLETED", "TASK_GRAPH", "graph-1"),
        {**_event(6, "DUPLICATE", "TASK_GRAPH", "graph-1"), "dedupe_key": "dedupe-5"},
    ]
    model = build_execution_review_read_model(
        _snapshot(),
        reviews=[_review("review-pending", status="PENDING", expires_at=2_000)],
        runtime_events=events,
        generated_at_ms=1_000,
        page_limit=100,
    )

    assert model["schema_version"] == EXECUTION_REVIEW_SCHEMA_VERSION
    assert model["read_only"] is True
    assert model["mutation_contract"] == {
        "direct_database_write": False,
        "review_commands": ["approve", "reject", "resume"],
        "resume_requires_bound_token": True,
        "automatic_resume": False,
    }
    assert model["execution"]["status"] == "COMPLETED"
    assert model["execution"]["terminal"] is True
    assert [(edge["from"], edge["to"]) for edge in model["dag"]["edges"]] == [
        ("execute", "publish"),
        ("plan", "execute"),
    ]
    assert {item["node_id"]: item["status"] for item in model["verification"]} == {
        "execute": "PASS",
        "plan": "PASS",
        "publish": "PASS",
    }
    assert model["budgets"][0]["consumed"]["token_count"] == 500
    assert [event["cursor"] for event in model["timeline"]] == [1, 2, 3, 4, 5]
    assert model["cursor_page"]["next_cursor"] == 5

    serialized = json.dumps(model, ensure_ascii=False)
    assert "raw thought sentinel" not in serialized
    assert "operation_fingerprint" not in serialized
    assert "must-not-leak" not in serialized


def test_t_nle_21_review_projection_fails_closed_for_expired_and_tokenless_resume() -> None:
    model = build_execution_review_read_model(
        _snapshot(),
        reviews=[
            _review("review-expired", status="PENDING", expires_at=999),
            _review(
                "review-approved",
                status="APPROVED",
                expires_at=2_000,
                token_ref="review-token://sha256/public-ref",
            ),
        ],
        generated_at_ms=1_000,
    )
    reviews = {review["review_id"]: review for review in model["reviews"]}
    assert reviews["review-expired"]["status"] == "EXPIRED"
    assert reviews["review-expired"]["actions"]["approve"] is False
    assert reviews["review-expired"]["actions"]["reject"] is False
    assert reviews["review-expired"]["actions"]["resume"] is False
    assert reviews["review-approved"]["actions"]["resume"] is True
    assert reviews["review-approved"]["actions"]["resume_requires_token"] is True
    assert "resume_token_ref" in reviews["review-approved"]["actions"]["resume_binding_fields"]
    assert reviews["review-approved"]["token_ref_public"] == "review-token://sha256/public-ref"
    assert "resume_token_ref" not in reviews["review-approved"]


def test_t_nle_21_execution_index_is_versioned_and_read_only() -> None:
    index = build_execution_index_read_model([_snapshot()["graph_record"]], generated_at_ms=1_000)
    assert index["schema_version"] == EXECUTION_INDEX_SCHEMA_VERSION
    assert index["read_only"] is True
    assert index["execution_count"] == 1
    assert index["executions"][0]["graph_id"] == "graph-1"
    assert index["executions"][0]["node_count"] == 3


class _TaskStore:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def list_graphs(self, *, limit: int) -> list[dict]:
        self.calls.append(("list_graphs", limit))
        return [_snapshot()["graph_record"]]

    def read_scheduler_snapshot(self, graph_id: str, revision: int) -> dict:
        self.calls.append(("read_scheduler_snapshot", graph_id, revision))
        return _snapshot()


class _ReviewStore:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def list_reviews(self, *, task_graph_id: str, limit: int) -> list[dict]:
        self.calls.append(("list_reviews", task_graph_id, limit))
        return [_review("review-pending", status="PENDING", expires_at=2_000)]


class _EventStore:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def list_events_for_aggregates(self, *, aggregate_refs, after_cursor: int, limit: int) -> list[dict]:
        self.calls.append(("list_events_for_aggregates", tuple(aggregate_refs), after_cursor, limit))
        return [_event(1, "TASK_GRAPH_CREATED", "TASK_GRAPH", "graph-1")]


def test_t_nle_21_service_uses_only_store_read_facades() -> None:
    tasks = _TaskStore()
    reviews = _ReviewStore()
    events = _EventStore()
    service = OperatorReadModelService(
        task_store=tasks,
        review_store=reviews,
        event_store=events,
        clock=lambda: 1_000,
    )

    assert service.list_executions(limit=25)["schema_version"] == EXECUTION_INDEX_SCHEMA_VERSION
    detail = service.get_execution("graph-1", 3, after_cursor=0, event_limit=50)
    assert detail["schema_version"] == EXECUTION_REVIEW_SCHEMA_VERSION
    assert tasks.calls == [
        ("list_graphs", 25),
        ("read_scheduler_snapshot", "graph-1", 3),
    ]
    assert reviews.calls == [("list_reviews", "graph-1", 500)]
    assert events.calls and events.calls[0][0] == "list_events_for_aggregates"
    for store in (tasks, reviews, events):
        assert not any(name.startswith(("write", "create", "update", "approve", "reject", "resume")) for name, *_ in store.calls)


def test_t_nle_21_obs_routes_honor_operator_feature_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    service = OperatorReadModelService(
        task_store=_TaskStore(),
        review_store=_ReviewStore(),
        event_store=_EventStore(),
        clock=lambda: 1_000,
    )

    monkeypatch.setenv("HOST_NLE_OPERATOR_VIEWS_ENABLED", "false")
    get_core_settings.cache_clear()
    with pytest.raises(HTTPException) as disabled:
        asyncio.run(list_task_runtime_executions(limit=10, service=service))
    assert disabled.value.status_code == 404
    assert disabled.value.detail["reason_code"] == "NLE_OPERATOR_VIEWS_DISABLED"
    with pytest.raises(HTTPException) as dependency_disabled:
        _get_operator_read_model_service()
    assert dependency_disabled.value.status_code == 404

    monkeypatch.setenv("HOST_NLE_OPERATOR_VIEWS_ENABLED", "true")
    get_core_settings.cache_clear()
    index = asyncio.run(list_task_runtime_executions(limit=10, service=service))
    detail = asyncio.run(
        get_task_runtime_execution(
            "graph-1",
            revision=3,
            after_cursor=0,
            event_limit=100,
            service=service,
        )
    )
    assert index["schema_version"] == EXECUTION_INDEX_SCHEMA_VERSION
    assert detail["schema_version"] == EXECUTION_REVIEW_SCHEMA_VERSION

    get_core_settings.cache_clear()


def test_t_nle_21_aggregate_cursor_replay_is_exact_and_large_dag_safe(tmp_path: Path) -> None:
    db_path = tmp_path / "operator-events.sqlite3"
    migration = Path("host_brain/audit/migrations/0056_runtime_events.sql")
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(migration.read_text(encoding="utf-8"))
    store = RuntimeEventStore(db_path, clock=lambda: 1_000)
    for index in range(1, 4):
        store.append_event(
            RuntimeEvent.create(
                event_type="TASK_GRAPH_CREATED",
                source_kind="TASK",
                source_ref=f"task://operator/{index}",
                aggregate_type="TASK_GRAPH",
                aggregate_id=f"graph-{index}",
                aggregate_version=1,
                payload_ref=f"taskstore://event/TASK_GRAPH/graph-{index}/1",
                payload={"graph_ref": f"task-graph://graph-{index}/1"},
                evidence_refs=(f"evidence://operator/{index}",),
                ts_ms=1_000 + index,
            )
        )

    aggregate_refs = [("TASK_NODE", f"unused-{index}") for index in range(401)]
    aggregate_refs.append(("TASK_GRAPH", "graph-2"))
    replay = store.list_events_for_aggregates(
        aggregate_refs=aggregate_refs,
        after_cursor=0,
        limit=100,
    )
    assert [event["aggregate_id"] for event in replay] == ["graph-2"]


def test_t_nle_21_frontend_resume_adapter_has_no_boolean_bypass() -> None:
    source = Path(
        "host_brain/app/monitor_ui/src/features/execution/taskRuntimeModel.js"
    ).read_text(encoding="utf-8")
    assert "resume_token_ref is required" in source
    assert "resume_requires_token === true" in source
    assert "confirmed" not in source
