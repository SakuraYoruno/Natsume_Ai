from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.contracts.agent_runtime.acceptance_judge import (
    AcceptanceCriterionStatus,
    AcceptanceCriterionVerdict,
    AcceptanceDecision,
    AcceptanceJudgement,
)
from host_brain.contracts.agent_runtime.budget_policy import (
    BudgetAmount,
    BudgetReservation,
    BudgetReservationStatus,
)
from host_brain.contracts.agent_runtime.parallel_success import (
    CanonicalParallelSuccessDecision,
    normalize_parallel_success_decision,
)
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphCompletionPolicy,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
)
from host_brain.contracts.agent_runtime.verification_contract import (
    VerificationResult,
    VerificationStatus,
)
from host_brain.persistence.task_repository import (
    AcceptanceEffectCommitScope,
    AcceptanceEffectIntentSpec,
    AttemptStatus,
    DurableTaskStore,
    OutboxMessage,
    TASK_STORE_CAS_CONFLICT,
    TaskStoreError,
    build_acceptance_effect_payload_digest,
    build_outbox_payload_ref,
)
from tests.task_runtime_authority_fixtures import (
    seed_raw_task_graph_for_test,
    seed_raw_acceptance_effect_for_test,
)
from host_brain.persistence.worker_projection_repository import WorkerProjectionStore


NOW = 1_930_000_000_000
OWNER = "runtime-ne01/parallel-success"
BUDGET_ID = "budget-ne01-parallel-success"


@dataclass
class Clock:
    value: int = NOW

    def __call__(self) -> int:
        return self.value


def _event(
    aggregate_type: str,
    aggregate_id: str,
    aggregate_version: int,
    event_type: str,
    *,
    attempt_id: str | None = None,
    evidence_refs: tuple[str, ...] = (),
    identity: str | None = None,
) -> OutboxMessage:
    stable = identity or uuid.uuid4().hex
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
        idempotency_key=f"ne01-parallel-success:{stable}",
        outbox_id=f"outbox::ne01-parallel-success:{stable}",
        attempt_id=attempt_id,
        evidence_refs=evidence_refs,
    )


def _graph(graph_id: str) -> TaskGraph:
    return TaskGraph(
        graph_id=graph_id,
        root_work_order_id=f"work-order::{graph_id}",
        revision=1,
        planner_ref="planner://ne01/parallel-success",
        status=TaskGraphStatus.CREATED,
        created_at=NOW,
        nodes=tuple(
            TaskNode(
                node_id=node_id,
                dependencies=(),
                route=f"skill:{node_id}",
                input_refs=(),
                output_contract={"type": "object"},
                retry_policy={"max_attempts": 1, "retry_backoff_ms": 0},
                risk_level=1,
                acceptance_criteria=("approved projection",),
                status=TaskNodeStatus.READY,
                status_version=0,
            )
            for node_id in ("left", "right")
        ),
        result_node_id="right",
        completion_policy=TaskGraphCompletionPolicy.ATOMIC_GRAPH_SUCCESS_V1,
    )


def _store(tmp_path: Path, graph_id: str) -> DurableTaskStore:
    db_path = tmp_path / f"{graph_id}.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id=f"migration::{graph_id}",
        ts_ms=NOW,
    )
    assert migrated.success is True, migrated.as_dict()
    store = DurableTaskStore(db_path, clock=Clock())
    seed_raw_task_graph_for_test(
        store,
        _graph(graph_id),
        outbox=_event("TASK_GRAPH", graph_id, 0, "TASK_GRAPH_CREATED"),
    )
    store.transition_graph(
        graph_id,
        1,
        expected_version=0,
        target_status=TaskGraphStatus.ACTIVE,
        outbox=_event("TASK_GRAPH", graph_id, 1, "TASK_GRAPH_ACTIVE"),
    )
    store.create_budget_reservation(
        BudgetReservation(
            budget_id=BUDGET_ID,
            scope=f"graph:{graph_id}@1",
            limits=BudgetAmount(token_count=100, tool_call_count=10, time_ms=60_000),
            reserved=BudgetAmount(token_count=100, tool_call_count=10, time_ms=60_000),
            status=BudgetReservationStatus.PENDING,
        ),
        graph_id=graph_id,
        graph_revision=1,
        node_id=None,
        attempt_id=None,
        outbox=_event(
            "BUDGET_RESERVATION",
            BUDGET_ID,
            0,
            "BUDGET_RESERVATION_CREATED",
        ),
    )
    store.transition_budget(
        BUDGET_ID,
        expected_version=0,
        target_status=BudgetReservationStatus.ACTIVE,
        outbox=_event(
            "BUDGET_RESERVATION",
            BUDGET_ID,
            1,
            "BUDGET_RESERVATION_ACTIVE",
        ),
    )
    return store


def _projection(
    db_path: Path,
    *,
    graph_id: str,
    node_id: str,
    attempt_id: str,
) -> dict:
    body = {
        "phase": "COMMIT",
        "done": True,
        "decision": "APPROVE",
        "reason_code": None,
        "command_id": f"command::{graph_id}",
        "request_id": f"request::{graph_id}",
        "narrative": f"approved {node_id}",
        "route_type": "skill",
        "target_id": node_id,
        "graph_id": graph_id,
        "graph_revision": 1,
        "node_id": node_id,
        "attempt_id": attempt_id,
        "node_grant_ref": f"grant://{graph_id}/{node_id}",
        "audit_refs": {},
    }
    encoded = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    projection_id = f"worker-projection://{graph_id}/1/{node_id}/{attempt_id}/{digest}"
    return WorkerProjectionStore(db_path, clock=Clock()).stage(
        projection_id=projection_id,
        projection={
            **body,
            "projection_id": projection_id,
            "payload_digest": f"sha256:{digest}",
            "schema_version": "Natsume.task_runtime.worker_projection.v1",
        },
    )


def _acceptance(
    graph_id: str,
    node_id: str,
    attempt_id: str,
) -> AcceptanceJudgement:
    evidence = f"acceptance://{graph_id}/{node_id}/approved"
    return AcceptanceJudgement(
        judgement_id=f"judgement::{graph_id}::{node_id}",
        work_order_id=f"work-order::{graph_id}",
        decision=AcceptanceDecision.ACCEPT,
        criteria=(
            AcceptanceCriterionVerdict(
                criterion_id=f"criterion::{node_id}",
                criterion_text="approved projection",
                verdict=AcceptanceCriterionStatus.PASS,
                evidence_refs=(evidence,),
            ),
        ),
        task_graph_id=graph_id,
        task_node_id=node_id,
        attempt_id=attempt_id,
        evidence_refs=(evidence,),
        started_at_ms=NOW,
        finished_at_ms=NOW + 1,
    )


def _verification(graph_id: str, node_id: str) -> VerificationResult:
    return VerificationResult(
        verification_id=f"verify::{graph_id}::{node_id}",
        work_order_id=f"work-order::{graph_id}",
        required=True,
        status=VerificationStatus.PASS,
        task_graph_id=graph_id,
        task_node_id=node_id,
        reason_code=None,
        evidence_refs=(f"verification://{graph_id}/{node_id}/pass",),
        checks=({"status": "PASS"},),
        started_at_ms=NOW,
        finished_at_ms=NOW + 1,
    )


def _accept_node(
    store: DurableTaskStore,
    *,
    graph_id: str,
    node_id: str,
) -> dict:
    attempt_id = f"attempt::{graph_id}::{node_id}"
    fingerprint = "sha256:" + hashlib.sha256(node_id.encode()).hexdigest()
    store.create_attempt(
        attempt_id=attempt_id,
        graph_id=graph_id,
        graph_revision=1,
        node_id=node_id,
        attempt_no=1,
        expected_node_version=0,
        operation_scope=f"graph:{graph_id}@1/{node_id}",
        operation_fingerprint=fingerprint,
        outbox=_event(
            "TASK_ATTEMPT",
            attempt_id,
            0,
            "TASK_ATTEMPT_CREATED",
            attempt_id=attempt_id,
        ),
    )
    lease = store.acquire_lease(
        attempt_id,
        owner_id=OWNER,
        ttl_ms=60_000,
        expected_attempt_version=0,
        outbox=_event(
            "TASK_ATTEMPT",
            attempt_id,
            1,
            "TASK_ATTEMPT_LEASED",
            attempt_id=attempt_id,
        ),
    )
    node = store.get_node(graph_id, 1, node_id)
    store.transition_node(
        graph_id,
        1,
        node_id,
        expected_version=int(node["status_version"]),
        target_status=TaskNodeStatus.RUNNING,
        outbox=_event(
            "TASK_NODE",
            f"{graph_id}@1/{node_id}",
            int(node["status_version"]) + 1,
            "TASK_NODE_RUNNING",
        ),
    )
    store.transition_attempt(
        attempt_id,
        owner_id=OWNER,
        fencing_token=int(lease["fencing_token"]),
        expected_version=1,
        target_status=AttemptStatus.RUNNING,
        outbox=_event(
            "TASK_ATTEMPT",
            attempt_id,
            2,
            "TASK_ATTEMPT_RUNNING",
            attempt_id=attempt_id,
        ),
    )
    reservation_id = f"operation::{graph_id}::{node_id}"
    reserved = store.reserve_operation(
        reservation_id=reservation_id,
        business_scope=f"graph:{graph_id}@1/{node_id}",
        operation_fingerprint=fingerprint,
        attempt_id=attempt_id,
        lease_id=str(lease["lease_id"]),
        owner_id=OWNER,
        fencing_token=int(lease["fencing_token"]),
        ttl_ms=60_000,
        outbox=_event(
            "OPERATION_RESERVATION",
            reservation_id,
            0,
            "OPERATION_RESERVED",
            attempt_id=attempt_id,
        ),
    )
    projection = _projection(
        store.db_path,
        graph_id=graph_id,
        node_id=node_id,
        attempt_id=attempt_id,
    )
    store.commit_operation(
        reservation_id,
        attempt_id=attempt_id,
        lease_id=str(lease["lease_id"]),
        owner_id=OWNER,
        fencing_token=int(lease["fencing_token"]),
        expected_version=int(reserved.reservation["status_version"]),
        receipt_ref=str(projection["projection_id"]),
        outbox=_event(
            "OPERATION_RESERVATION",
            reservation_id,
            1,
            "OPERATION_COMMITTED",
            attempt_id=attempt_id,
        ),
    )
    store.transition_attempt(
        attempt_id,
        owner_id=OWNER,
        fencing_token=int(lease["fencing_token"]),
        expected_version=2,
        target_status=AttemptStatus.SUCCEEDED,
        outbox=_event(
            "TASK_ATTEMPT",
            attempt_id,
            3,
            "TASK_ATTEMPT_SUCCEEDED",
            attempt_id=attempt_id,
        ),
    )
    for target in (
        TaskNodeStatus.EXECUTED,
        TaskNodeStatus.VERIFYING,
        TaskNodeStatus.VERIFIED,
    ):
        current = store.get_node(graph_id, 1, node_id)
        verification = _verification(graph_id, node_id) if target is TaskNodeStatus.VERIFIED else None
        evidence = verification.evidence_refs if verification is not None else ()
        store.transition_node(
            graph_id,
            1,
            node_id,
            expected_version=int(current["status_version"]),
            target_status=target,
            outbox=_event(
                "TASK_NODE",
                f"{graph_id}@1/{node_id}",
                int(current["status_version"]) + 1,
                f"TASK_NODE_{target.value}",
                evidence_refs=evidence,
            ),
            verification_result=verification,
        )
    current = store.get_node(graph_id, 1, node_id)
    judgement = _acceptance(graph_id, node_id, attempt_id)
    store.commit_node_acceptance(
        graph_id,
        1,
        node_id,
        expected_version=int(current["status_version"]),
        outbox=_event(
            "TASK_NODE",
            f"{graph_id}@1/{node_id}",
            int(current["status_version"]) + 1,
            "TASK_NODE_ACCEPTED",
            attempt_id=attempt_id,
            evidence_refs=judgement.evidence_refs,
        ),
        acceptance_judgement=judgement,
        attempt_id=attempt_id,
        receipt_ref=str(projection["projection_id"]),
        worker_output=projection,
    )
    return projection


def _effect(graph_id: str, attempt_id: str) -> AcceptanceEffectIntentSpec:
    payload = {"request_id": f"request::{graph_id}", "narrative": "approved right"}
    digest = build_acceptance_effect_payload_digest(payload)
    return AcceptanceEffectIntentSpec(
        effect_id=f"acceptance-effect://{graph_id}/reply",
        graph_id=graph_id,
        graph_revision=1,
        node_id="right",
        attempt_id=attempt_id,
        commit_scope=AcceptanceEffectCommitScope.GRAPH,
        effect_type="surface.reply_delivery",
        payload_ref=f"acceptance-payload://{graph_id}/reply",
        payload=payload,
        payload_sha256=digest,
        idempotency_key=f"acceptance-idempotency://{graph_id}/reply",
    )


def _fixture(
    tmp_path: Path,
    graph_id: str,
) -> tuple[
    DurableTaskStore,
    CanonicalParallelSuccessDecision,
    OutboxMessage,
    OutboxMessage,
    AcceptanceEffectIntentSpec,
    dict[str, dict],
]:
    store = _store(tmp_path, graph_id)
    projections = {
        node_id: _accept_node(store, graph_id=graph_id, node_id=node_id)
        for node_id in ("left", "right")
    }
    node_states = {
        node_id: {
            "status": (node := store.get_node(graph_id, 1, node_id))["status"],
            "status_version": node["status_version"],
        }
        for node_id in ("left", "right")
    }
    effect = _effect(graph_id, str(projections["right"]["attempt_id"]))
    graph_outbox = _event(
        "TASK_GRAPH",
        graph_id,
        2,
        "TASK_GRAPH_COMPLETED",
        identity=f"{graph_id}:graph-completed",
    )
    budget_outbox = _event(
        "BUDGET_RESERVATION",
        BUDGET_ID,
        2,
        "BUDGET_RESERVATION_CONSUMED",
        identity=f"{graph_id}:budget-consumed",
    )
    decision = normalize_parallel_success_decision(
        {
            "schema_version": "Natsume.task_runtime.parallel_success.v1",
            "graph_id": graph_id,
            "graph_revision": 1,
            "graph_status_before": "ACTIVE",
            "graph_status_version_before": 1,
            "graph_target_status": "COMPLETED",
            "completion_policy": "ATOMIC_GRAPH_SUCCESS_V1",
            "budget_id": BUDGET_ID,
            "budget_status_before": "ACTIVE",
            "budget_status_version_before": 1,
            "budget_target_status": "CONSUMED",
            "result_node_id": "right",
            "result_attempt_id": projections["right"]["attempt_id"],
            "result_receipt_ref": projections["right"]["projection_id"],
            "node_states": node_states,
            "accepted_outcomes": [
                {
                    "node_id": node_id,
                    "attempt_id": projection["attempt_id"],
                    "receipt_ref": projection["projection_id"],
                    "payload_sha256": projection["payload_digest"],
                }
                for node_id, projection in projections.items()
            ],
            "effect_intents": [
                {
                    "effect_id": effect.effect_id,
                    "node_id": effect.node_id,
                    "attempt_id": effect.attempt_id,
                    "commit_scope": "GRAPH",
                    "effect_type": effect.effect_type,
                    "payload_ref": effect.payload_ref,
                    "payload_sha256": effect.payload_sha256,
                    "idempotency_key": effect.idempotency_key,
                }
            ],
            "graph_outbox_id": graph_outbox.outbox_id,
            "budget_outbox_id": budget_outbox.outbox_id,
        }
    )
    evidence = (
        decision.decision_ref,
        str(projections["right"]["projection_id"]),
        effect.effect_id,
    )
    return (
        store,
        decision,
        replace(graph_outbox, evidence_refs=evidence),
        replace(budget_outbox, evidence_refs=evidence),
        effect,
        node_states,
    )


def _complete(
    store: DurableTaskStore,
    decision: CanonicalParallelSuccessDecision,
    graph_outbox: OutboxMessage,
    budget_outbox: OutboxMessage,
    effect: AcceptanceEffectIntentSpec,
    node_states: dict[str, dict],
) -> dict:
    return store.complete_parallel_graph_success(
        str(decision.payload["graph_id"]),
        1,
        budget_id=BUDGET_ID,
        expected_graph_version=1,
        expected_budget_version=1,
        expected_node_states=node_states,
        decision_ref=decision.decision_ref,
        decision_payload=decision.payload,
        graph_outbox=graph_outbox,
        budget_outbox=budget_outbox,
        effect_intents=(effect,),
    )


def test_parallel_success_commits_every_terminal_asset_and_exact_replays(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, "graph-parallel-success-atomic")
    first = _complete(*fixture)
    replay = _complete(*fixture)
    store, decision, *_ = fixture

    assert first["replayed"] is False
    assert replay["replayed"] is True
    assert first["decision"] == replay["decision"]
    assert store.get_graph("graph-parallel-success-atomic", 1)["status"] == "COMPLETED"
    assert store.reconcile_budget(BUDGET_ID)["status"] == "CONSUMED"
    assert store.get_parallel_success_decision(decision.decision_ref)[
        "decision_ref"
    ] == decision.decision_ref
    assert store.get_latest_parallel_success_decision(
        "graph-parallel-success-atomic", 1
    )["decision_ref"] == decision.decision_ref
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_parallel_success_decision"
        ).fetchone() == (1,)
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_acceptance_effect_intent"
        ).fetchone() == (1,)
        assert conn.execute(
            """
            SELECT COUNT(*) FROM task_runtime_outbox
            WHERE event_type IN ('TASK_GRAPH_COMPLETED', 'BUDGET_RESERVATION_CONSUMED')
            """
        ).fetchone() == (2,)


def test_parallel_success_late_fault_rolls_back_graph_budget_effects_and_outboxes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path, "graph-parallel-success-rollback")
    store = fixture[0]
    before_outbox = len(store.list_pending_outbox(limit=1_000))

    def fail_decision(*args, **kwargs):
        raise TaskStoreError(TASK_STORE_CAS_CONFLICT, "injected decision failure")

    monkeypatch.setattr(store, "_insert_parallel_success_decision", fail_decision)
    with pytest.raises(TaskStoreError, match="injected decision failure"):
        _complete(*fixture)

    assert store.get_graph("graph-parallel-success-rollback", 1)["status"] == "ACTIVE"
    assert store.reconcile_budget(BUDGET_ID)["status"] == "ACTIVE"
    assert len(store.list_pending_outbox(limit=1_000)) == before_outbox
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_acceptance_effect_intent"
        ).fetchone() == (0,)
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_parallel_success_decision"
        ).fetchone() == (0,)


def test_parallel_success_effect_drift_fails_before_any_terminal_write(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, "graph-parallel-success-drift")
    store, decision, graph_outbox, budget_outbox, effect, node_states = fixture
    drifted = replace(effect, payload_ref="acceptance-payload://drifted")
    before_outbox = len(store.list_pending_outbox(limit=1_000))

    with pytest.raises(TaskStoreError):
        _complete(
            store,
            decision,
            graph_outbox,
            budget_outbox,
            drifted,
            node_states,
        )

    assert store.get_graph("graph-parallel-success-drift", 1)["status"] == "ACTIVE"
    assert store.reconcile_budget(BUDGET_ID)["status"] == "ACTIVE"
    assert len(store.list_pending_outbox(limit=1_000)) == before_outbox


def test_parallel_success_rejects_rogue_graph_effect_and_freezes_effect_set(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, "graph-parallel-success-effect-fence")
    store, decision, graph_outbox, budget_outbox, effect, node_states = fixture
    rogue = replace(
        effect,
        effect_id="acceptance-effect://graph-parallel-success-effect-fence/rogue",
        payload_ref="acceptance-payload://graph-parallel-success-effect-fence/rogue",
        idempotency_key="acceptance-idempotency://graph-parallel-success-effect-fence/rogue",
    )
    seed_raw_acceptance_effect_for_test(
        store,
        effect_id=rogue.effect_id,
        graph_id=rogue.graph_id,
        graph_revision=rogue.graph_revision,
        node_id=rogue.node_id,
        attempt_id=rogue.attempt_id,
        commit_scope=rogue.commit_scope,
        effect_type=rogue.effect_type,
        payload_ref=rogue.payload_ref,
        payload=rogue.payload,
        payload_sha256=rogue.payload_sha256,
        idempotency_key=rogue.idempotency_key,
    )

    with pytest.raises(TaskStoreError, match="unbound effect"):
        _complete(
            store,
            decision,
            graph_outbox,
            budget_outbox,
            effect,
            node_states,
        )
    assert store.get_graph("graph-parallel-success-effect-fence", 1)["status"] == "ACTIVE"
    assert store.reconcile_budget(BUDGET_ID)["status"] == "ACTIVE"

    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "DELETE FROM task_runtime_acceptance_effect_intent WHERE effect_id = ?",
            (rogue.effect_id,),
        )
        conn.commit()
    _complete(
        store,
        decision,
        graph_outbox,
        budget_outbox,
        effect,
        node_states,
    )
    with pytest.raises(TaskStoreError, match="permanently freezes"):
        seed_raw_acceptance_effect_for_test(
            store,
            effect_id=rogue.effect_id,
            graph_id=rogue.graph_id,
            graph_revision=rogue.graph_revision,
            node_id=rogue.node_id,
            attempt_id=rogue.attempt_id,
            commit_scope=rogue.commit_scope,
            effect_type=rogue.effect_type,
            payload_ref=rogue.payload_ref,
            payload=rogue.payload,
            payload_sha256=rogue.payload_sha256,
            idempotency_key=rogue.idempotency_key,
        )


def test_parallel_success_getter_rejects_terminal_state_drift(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, "graph-parallel-success-getter-drift")
    _complete(*fixture)
    store, decision, *_ = fixture
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            UPDATE task_runtime_budget_reservation
            SET status = 'RELEASED'
            WHERE budget_id = ?
            """,
            (BUDGET_ID,),
        )
        conn.commit()

    with pytest.raises(TaskStoreError, match="terminal Budget binding drifted"):
        store.get_parallel_success_decision(decision.decision_ref)


def test_parallel_success_response_loss_replays_after_store_restart(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, "graph-parallel-success-restart")
    first = _complete(*fixture)
    original, decision, graph_outbox, budget_outbox, effect, node_states = fixture
    restarted = DurableTaskStore(original.db_path, clock=Clock(NOW + 100))

    replay = _complete(
        restarted,
        decision,
        graph_outbox,
        budget_outbox,
        effect,
        node_states,
    )

    assert first["decision"] == replay["decision"]
    assert replay["replayed"] is True
    with sqlite3.connect(restarted.db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_parallel_success_decision"
        ).fetchone() == (1,)
        assert conn.execute(
            "SELECT COUNT(*) FROM task_runtime_acceptance_effect_intent"
        ).fetchone() == (1,)


def test_parallel_success_projection_digest_drift_rolls_back_terminal_write(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, "graph-parallel-success-projection-drift")
    store = fixture[0]
    before_outbox = len(store.list_pending_outbox(limit=1_000))
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            UPDATE task_runtime_worker_projection
            SET payload_digest = ?
            WHERE node_id = 'left'
            """,
            ("sha256:" + "f" * 64,),
        )
        conn.commit()

    with pytest.raises(TaskStoreError, match="projection is invalid"):
        _complete(*fixture)
    assert store.get_graph(
        "graph-parallel-success-projection-drift", 1
    )["status"] == "ACTIVE"
    assert store.reconcile_budget(BUDGET_ID)["status"] == "ACTIVE"
    assert len(store.list_pending_outbox(limit=1_000)) == before_outbox


def test_0091_enforces_unique_graph_and_result_attempt_fk(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, "graph-parallel-success-ddl")
    _complete(*fixture)
    store = fixture[0]
    columns = """
        decision_ref, schema_version, graph_id, graph_revision,
        graph_version_before, graph_version_after, budget_id,
        budget_version_before, budget_version_after, result_node_id,
        result_attempt_id, result_receipt_ref, payload_json, payload_sha256,
        graph_outbox_id, budget_outbox_id, created_ts_ms
    """
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                f"""
                INSERT INTO task_runtime_parallel_success_decision({columns})
                SELECT decision_ref || '-duplicate', schema_version, graph_id,
                       graph_revision, graph_version_before, graph_version_after,
                       budget_id, budget_version_before, budget_version_after,
                       result_node_id, result_attempt_id, result_receipt_ref,
                       payload_json, payload_sha256, graph_outbox_id,
                       budget_outbox_id, created_ts_ms
                FROM task_runtime_parallel_success_decision
                """
            )
        original = conn.execute(
            "SELECT * FROM task_runtime_parallel_success_decision"
        ).fetchone()
        assert original is not None
        conn.execute("DELETE FROM task_runtime_parallel_success_decision")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                f"""
                INSERT INTO task_runtime_parallel_success_decision({columns})
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    original[0],
                    original[1],
                    original[2],
                    original[3],
                    original[4],
                    original[5],
                    original[6],
                    original[7],
                    original[8],
                    original[9],
                    "attempt://does-not-exist",
                    original[11],
                    original[12],
                    original[13],
                    original[14],
                    original[15],
                    original[16],
                ),
            )
        conn.rollback()
