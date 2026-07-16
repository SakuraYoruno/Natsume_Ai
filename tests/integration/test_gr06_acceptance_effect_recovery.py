from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import sqlite3
import threading

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
)
from host_brain.persistence.task_repository import (
    AcceptanceEffectCommitScope,
    AcceptanceEffectStatus,
    DurableTaskStore,
    OutboxMessage,
    TASK_STORE_LEASE_CONFLICT,
    TaskStoreError,
    build_outbox_payload_ref,
)


@dataclass
class LockedClock:
    value: int = 50_000
    lock: threading.Lock = field(default_factory=threading.Lock)

    def __call__(self) -> int:
        with self.lock:
            return self.value

    def set(self, value: int) -> None:
        with self.lock:
            self.value = int(value)


def _accepted_store(tmp_path: Path, clock: LockedClock) -> DurableTaskStore:
    db_path = tmp_path / "gr06-effect-recovery.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="gr06-effect-recovery"
    )
    assert migrated.success is True, migrated.as_dict()
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            INSERT INTO task_runtime_graph(
                graph_id, revision, root_work_order_id, planner_ref, status,
                status_version, graph_json, source_kind, created_ts_ms, updated_ts_ms
            ) VALUES (?, ?, ?, ?, 'ACTIVE', 1, '{}', 'CANONICAL', ?, ?)
            """,
            (
                "graph-gr06-recovery",
                1,
                "work-order::graph-gr06-recovery",
                "planner://gr06/recovery",
                clock(),
                clock(),
            ),
        )
        conn.execute(
            """
            INSERT INTO task_runtime_node(
                graph_id, graph_revision, node_id, status, status_version,
                node_json, created_ts_ms, updated_ts_ms
            ) VALUES (?, ?, ?, 'ACCEPTED', 7, '{}', ?, ?)
            """,
            ("graph-gr06-recovery", 1, "node-gr06-recovery", clock(), clock()),
        )
        conn.execute(
            """
            INSERT INTO task_runtime_attempt(
                attempt_id, graph_id, graph_revision, node_id, attempt_no,
                status, status_version, owner_id, created_ts_ms,
                completed_ts_ms, updated_ts_ms
            ) VALUES (?, ?, ?, ?, 1, 'SUCCEEDED', 3, ?, ?, ?, ?)
            """,
            (
                "attempt-gr06-recovery",
                "graph-gr06-recovery",
                1,
                "node-gr06-recovery",
                "worker-gr06",
                clock(),
                clock(),
                clock(),
            ),
        )
        conn.commit()
    return DurableTaskStore(db_path, clock=clock)


def _event(
    aggregate_type: str,
    aggregate_id: str,
    aggregate_version: int,
    event_type: str,
) -> OutboxMessage:
    return OutboxMessage.create(
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        graph_revision=(1 if aggregate_type == "TASK_GRAPH" else None),
        aggregate_version=aggregate_version,
        event_type=event_type,
        payload_ref=build_outbox_payload_ref(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
            graph_revision=(1 if aggregate_type == "TASK_GRAPH" else None),
        ),
        idempotency_key=(
            f"gr06-integration:{aggregate_type}:{aggregate_id}:"
            f"{aggregate_version}:{event_type}"
        ),
        outbox_id=(
            f"outbox::gr06-integration::{aggregate_type}::{aggregate_id}::"
            f"{aggregate_version}"
        ),
    )


def test_gr06_effect_claim_is_single_winner_restart_safe_and_fenced(
    tmp_path: Path,
) -> None:
    clock = LockedClock()
    store = _accepted_store(tmp_path, clock)
    effect_id = "acceptance-effect://gr06/recovery/final-reply"
    payload = b'{"text":"accepted"}'
    store.stage_acceptance_effect_intent(
        effect_id=effect_id,
        graph_id="graph-gr06-recovery",
        graph_revision=1,
        node_id="node-gr06-recovery",
        attempt_id="attempt-gr06-recovery",
        commit_scope="NODE",
        effect_type="conversation.assistant_turn",
        payload_ref="effect-payload://gr06/recovery/final-reply",
        payload=json.loads(payload.decode("utf-8")),
        payload_sha256="sha256:" + hashlib.sha256(payload).hexdigest(),
        idempotency_key="gr06:recovery:final-reply",
    )

    barrier = threading.Barrier(2)

    def claim(owner: str) -> dict[str, object] | None:
        contender = DurableTaskStore(store.db_path, clock=clock)
        barrier.wait(timeout=5)
        return contender.claim_acceptance_effect_intent(
            owner_id=owner,
            lease_ms=100,
            effect_id=effect_id,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, ("dispatcher-a", "dispatcher-b")))

    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    first = winners[0]
    first_owner = str(first["lease_owner"])
    assert first["fencing_token"] == 1
    assert first["attempt_count"] == 1

    clock.set(50_101)
    restarted = DurableTaskStore(store.db_path, clock=clock)
    reclaimed = restarted.claim_acceptance_effect_intent(
        owner_id="dispatcher-restarted",
        lease_ms=1_000,
        effect_id=effect_id,
    )
    assert reclaimed is not None
    assert reclaimed["fencing_token"] == 2
    assert reclaimed["attempt_count"] == 2

    with pytest.raises(TaskStoreError) as stale:
        store.commit_acceptance_effect_intent(
            effect_id,
            owner_id=first_owner,
            fencing_token=int(first["fencing_token"]),
            receipt_ref="delivery-receipt://gr06/stale",
        )
    assert stale.value.reason_code == TASK_STORE_LEASE_CONFLICT

    committed = restarted.commit_acceptance_effect_intent(
        effect_id,
        owner_id="dispatcher-restarted",
        fencing_token=int(reclaimed["fencing_token"]),
        receipt_ref="delivery-receipt://gr06/final-reply",
    )
    assert committed["status"] == AcceptanceEffectStatus.COMMITTED.value

    after_second_restart = DurableTaskStore(store.db_path, clock=clock)
    durable = after_second_restart.get_acceptance_effect_intent(effect_id)
    assert durable["status"] == AcceptanceEffectStatus.COMMITTED.value
    assert durable["receipt_ref"] == "delivery-receipt://gr06/final-reply"
    assert after_second_restart.claim_acceptance_effect_intent(
        owner_id="dispatcher-c", effect_id=effect_id
    ) is None


def test_gr06_atomic_graph_acceptance_effect_survives_process_restart(
    tmp_path: Path,
) -> None:
    clock = LockedClock(value=70_000)
    db_path = tmp_path / "gr06-atomic-restart.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="gr06-atomic-restart"
    )
    assert migrated.success is True, migrated.as_dict()
    store = DurableTaskStore(db_path, clock=clock)
    graph = TaskGraph(
        graph_id="graph-gr06-atomic-restart",
        root_work_order_id="work-order::gr06-atomic-restart",
        revision=1,
        planner_ref="planner://gr06/atomic-restart",
        status=TaskGraphStatus.ACTIVE,
        created_at=clock(),
        nodes=(
            TaskNode(
                node_id="node-gr06-atomic-restart",
                dependencies=(),
                route="chat",
                input_refs=("input://gr06/atomic-restart",),
                output_contract={"type": "accepted-chat"},
                retry_policy={"max_attempts": 1},
                risk_level=1,
                acceptance_criteria=("accepted",),
                status=TaskNodeStatus.ACCEPTED,
                status_version=7,
            ),
        ),
    )
    store.create_graph(
        graph,
        outbox=_event(
            "TASK_GRAPH",
            graph.graph_id,
            0,
            "TASK_GRAPH_CREATED",
        ),
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            INSERT INTO task_runtime_attempt(
                attempt_id, graph_id, graph_revision, node_id, attempt_no,
                status, status_version, owner_id, created_ts_ms,
                completed_ts_ms, updated_ts_ms
            ) VALUES (?, ?, ?, ?, 1, 'SUCCEEDED', 3, ?, ?, ?, ?)
            """,
            (
                "attempt-gr06-atomic-restart",
                graph.graph_id,
                graph.revision,
                "node-gr06-atomic-restart",
                "worker-gr06-atomic-restart",
                clock(),
                clock(),
                clock(),
            ),
        )
        conn.commit()

    payload = b'{"text":"accepted after restart"}'
    effect_id = "acceptance-effect://gr06/atomic-restart/final-delivery"
    committed = store.transition_graph_and_stage_acceptance_effects(
        graph.graph_id,
        graph.revision,
        expected_version=0,
        outbox=_event(
            "TASK_GRAPH",
            graph.graph_id,
            1,
            "TASK_GRAPH_COMPLETED",
        ),
        effect_intents=(
            {
                "effect_id": effect_id,
                "graph_id": graph.graph_id,
                "graph_revision": graph.revision,
                "node_id": "node-gr06-atomic-restart",
                "attempt_id": "attempt-gr06-atomic-restart",
                "commit_scope": AcceptanceEffectCommitScope.GRAPH,
                "effect_type": "delivery.final_reply",
                "payload_ref": "effect-payload://gr06/atomic-restart/final-delivery",
                "payload": json.loads(payload.decode("utf-8")),
                "payload_sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
                "idempotency_key": "gr06:atomic-restart:final-delivery",
            },
        ),
    )
    assert committed["graph"]["status"] == TaskGraphStatus.COMPLETED.value

    restarted = DurableTaskStore(db_path, clock=clock)
    assert restarted.get_graph(graph.graph_id, graph.revision)["status"] == (
        TaskGraphStatus.COMPLETED.value
    )
    durable_effect = restarted.get_acceptance_effect_intent(effect_id)
    assert durable_effect["status"] == AcceptanceEffectStatus.PENDING.value
    assert restarted.claim_acceptance_effect_intent(
        owner_id="dispatcher-after-process-restart",
        effect_id=effect_id,
    ) is not None
