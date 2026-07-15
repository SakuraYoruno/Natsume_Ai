from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

import pytest

from host_brain.agent_runtime.verification_policy import (
    VerificationResult,
    VerificationStatus,
)
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.contracts.agent_runtime.acceptance_judge import judge_acceptance_result
from host_brain.contracts.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
)
from host_brain.persistence.task_repository import (
    AcceptanceEffectCommitScope,
    AcceptanceEffectStatus,
    AttemptStatus,
    DurableTaskStore,
    OutboxMessage,
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_INVALID,
    TASK_STORE_OUTBOX_CONFLICT,
    TASK_STORE_SCHEMA_NOT_READY,
    TaskStoreError,
    build_outbox_payload_ref,
)


ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "host_brain" / "audit" / "migrations"


@dataclass
class MutableClock:
    value: int = 10_000

    def __call__(self) -> int:
        return self.value

    def set(self, value: int) -> None:
        self.value = int(value)


def _event(
    aggregate_type: str,
    aggregate_id: str,
    aggregate_version: int,
    event_type: str,
    *,
    attempt_id: str | None = None,
    evidence_refs: tuple[str, ...] = (),
) -> OutboxMessage:
    return OutboxMessage.create(
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        aggregate_version=aggregate_version,
        event_type=event_type,
        payload_ref=build_outbox_payload_ref(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
        ),
        idempotency_key=f"gr06:{event_type}:{uuid.uuid4().hex}",
        attempt_id=attempt_id,
        evidence_refs=evidence_refs,
    )


def _store(tmp_path: Path, *, clock: MutableClock | None = None) -> DurableTaskStore:
    db_path = tmp_path / "gr06-effects.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="gr06-effect-contract"
    )
    assert migrated.success is True, migrated.as_dict()
    assert migrated.summary["current_version"] == 88
    store = DurableTaskStore(db_path, clock=clock or MutableClock())
    store.assert_acceptance_effect_intent_ready()
    return store


def _seed_attempt(store: DurableTaskStore) -> None:
    graph = TaskGraph(
        graph_id="graph-gr06",
        root_work_order_id="work-order::graph-gr06",
        revision=1,
        planner_ref="planner://gr06/frozen",
        status=TaskGraphStatus.CREATED,
        created_at=1,
        nodes=(
            TaskNode(
                node_id="node-gr06",
                dependencies=(),
                route="chat",
                input_refs=("input://gr06/1",),
                output_contract={"type": "staged-effect"},
                retry_policy={"max_attempts": 1},
                risk_level=1,
                acceptance_criteria=("effect staged",),
                status=TaskNodeStatus.READY,
                status_version=0,
            ),
        ),
    )
    store.create_graph(
        graph,
        outbox=_event("TASK_GRAPH", graph.graph_id, 0, "TASK_GRAPH_CREATED"),
    )
    store.transition_graph(
        graph.graph_id,
        graph.revision,
        expected_version=0,
        target_status=TaskGraphStatus.ACTIVE,
        outbox=_event("TASK_GRAPH", graph.graph_id, 1, "TASK_GRAPH_ACTIVE"),
    )
    store.create_attempt(
        attempt_id="attempt-gr06",
        graph_id=graph.graph_id,
        graph_revision=graph.revision,
        node_id="node-gr06",
        attempt_no=1,
        expected_node_version=0,
        outbox=_event(
            "TASK_ATTEMPT",
            "attempt-gr06",
            0,
            "TASK_ATTEMPT_CREATED",
            attempt_id="attempt-gr06",
        ),
    )


def _verification() -> VerificationResult:
    return VerificationResult(
        verification_id="verify-gr06",
        work_order_id="work-order::graph-gr06",
        required=True,
        status=VerificationStatus.PASS,
        task_graph_id="graph-gr06",
        task_node_id="node-gr06",
        reason_code=None,
        evidence_refs=("verification://gr06/pass/1",),
        checks=({"status": "PASS"},),
        started_at_ms=10_001,
        finished_at_ms=10_002,
    )


def _prepare_node_acceptance(store: DurableTaskStore):
    lease = store.acquire_lease(
        "attempt-gr06",
        owner_id="worker-gr06",
        ttl_ms=10_000,
        expected_attempt_version=0,
        outbox=_event(
            "TASK_ATTEMPT",
            "attempt-gr06",
            1,
            "TASK_ATTEMPT_LEASED",
            attempt_id="attempt-gr06",
        ),
    )
    store.transition_node(
        "graph-gr06",
        1,
        "node-gr06",
        expected_version=1,
        target_status=TaskNodeStatus.RUNNING,
        outbox=_event(
            "TASK_NODE", "graph-gr06@1/node-gr06", 2, "TASK_NODE_RUNNING"
        ),
    )
    store.transition_attempt(
        "attempt-gr06",
        owner_id="worker-gr06",
        fencing_token=lease["fencing_token"],
        expected_version=1,
        target_status=AttemptStatus.RUNNING,
        outbox=_event(
            "TASK_ATTEMPT",
            "attempt-gr06",
            2,
            "TASK_ATTEMPT_RUNNING",
            attempt_id="attempt-gr06",
        ),
    )
    for expected_version, target in (
        (2, TaskNodeStatus.EXECUTED),
        (3, TaskNodeStatus.VERIFYING),
    ):
        store.transition_node(
            "graph-gr06",
            1,
            "node-gr06",
            expected_version=expected_version,
            target_status=target,
            outbox=_event(
                "TASK_NODE",
                "graph-gr06@1/node-gr06",
                expected_version + 1,
                f"TASK_NODE_{target.value}",
            ),
        )
    verification = _verification()
    store.transition_node(
        "graph-gr06",
        1,
        "node-gr06",
        expected_version=4,
        target_status=TaskNodeStatus.VERIFIED,
        outbox=_event(
            "TASK_NODE",
            "graph-gr06@1/node-gr06",
            5,
            "TASK_NODE_VERIFIED",
            evidence_refs=verification.evidence_refs,
        ),
        verification_result=verification,
    )
    store.transition_node(
        "graph-gr06",
        1,
        "node-gr06",
        expected_version=5,
        target_status=TaskNodeStatus.WAITING_ACCEPTANCE_REVIEW,
        outbox=_event(
            "TASK_NODE",
            "graph-gr06@1/node-gr06",
            6,
            "TASK_NODE_WAITING_ACCEPTANCE_REVIEW",
        ),
    )
    store.transition_attempt(
        "attempt-gr06",
        owner_id="worker-gr06",
        fencing_token=lease["fencing_token"],
        expected_version=2,
        target_status=AttemptStatus.SUCCEEDED,
        outbox=_event(
            "TASK_ATTEMPT",
            "attempt-gr06",
            3,
            "TASK_ATTEMPT_SUCCEEDED",
            attempt_id="attempt-gr06",
        ),
    )
    judgement = judge_acceptance_result(
        {
            "status": "EXECUTED",
            "work_order_id": "work-order::graph-gr06",
            "summary": "effect prepared",
            "result_text": "effect staged",
            "verification": {
                "status": "PASS",
                "evidence_refs": list(verification.evidence_refs),
            },
        },
        work_order_id="work-order::graph-gr06",
        acceptance_criteria=("effect staged",),
        task_graph_id="graph-gr06",
        task_node_id="node-gr06",
    )
    return judgement


def _accept_node(store: DurableTaskStore) -> None:
    judgement = _prepare_node_acceptance(store)
    store.transition_node(
        "graph-gr06",
        1,
        "node-gr06",
        expected_version=6,
        target_status=TaskNodeStatus.ACCEPTED,
        outbox=_event(
            "TASK_NODE",
            "graph-gr06@1/node-gr06",
            7,
            "TASK_NODE_ACCEPTED",
            evidence_refs=judgement.evidence_refs,
        ),
        acceptance_judgement=judgement,
    )


def _stage(
    store: DurableTaskStore,
    *,
    effect_id: str = "acceptance-effect://gr06/assistant-turn",
    commit_scope: AcceptanceEffectCommitScope = AcceptanceEffectCommitScope.NODE,
    idempotency_key: str = "gr06:assistant-turn",
    payload: bytes = b'{"role":"assistant"}',
) -> dict[str, object]:
    return store.stage_acceptance_effect_intent(
        effect_id=effect_id,
        graph_id="graph-gr06",
        graph_revision=1,
        node_id="node-gr06",
        attempt_id="attempt-gr06",
        commit_scope=commit_scope,
        effect_type="conversation.assistant_turn",
        payload_ref="effect-payload://gr06/assistant-turn",
        payload=json.loads(payload.decode("utf-8")),
        payload_sha256="sha256:" + hashlib.sha256(
            json.dumps(
                json.loads(payload.decode("utf-8")),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        idempotency_key=idempotency_key,
    )


def _effect_spec(
    *,
    effect_id: str = "acceptance-effect://gr06/assistant-turn",
    commit_scope: AcceptanceEffectCommitScope = AcceptanceEffectCommitScope.NODE,
    idempotency_key: str = "gr06:assistant-turn",
    payload: bytes = b'{"role":"assistant"}',
) -> dict[str, object]:
    return {
        "effect_id": effect_id,
        "graph_id": "graph-gr06",
        "graph_revision": 1,
        "node_id": "node-gr06",
        "attempt_id": "attempt-gr06",
        "commit_scope": commit_scope,
        "effect_type": "conversation.assistant_turn",
        "payload_ref": "effect-payload://gr06/assistant-turn",
        "payload": json.loads(payload.decode("utf-8")),
        "payload_sha256": "sha256:" + hashlib.sha256(
            json.dumps(
                json.loads(payload.decode("utf-8")),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "idempotency_key": idempotency_key,
    }


def test_gr06_migration_0087_is_forward_owned_and_fails_closed_when_missing(
    tmp_path: Path,
) -> None:
    legacy_path = tmp_path / "head-54.sqlite3"
    with sqlite3.connect(legacy_path) as conn:
        conn.executescript((MIGRATIONS / "0052_task_runtime.sql").read_text(encoding="utf-8"))
        conn.executescript(
            (MIGRATIONS / "0054_task_runtime_recovery.sql").read_text(encoding="utf-8")
        )
    legacy = DurableTaskStore(legacy_path)
    with pytest.raises(TaskStoreError) as missing:
        legacy.assert_acceptance_effect_intent_ready()
    assert missing.value.reason_code == TASK_STORE_SCHEMA_NOT_READY

    store = _store(tmp_path)
    with sqlite3.connect(store.db_path) as conn:
        columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(task_runtime_acceptance_effect_intent)"
            ).fetchall()
        }
        attempt_binding = {
            (row[3], row[4])
            for row in conn.execute(
                "PRAGMA foreign_key_list(task_runtime_acceptance_effect_intent)"
            ).fetchall()
            if row[2] == "task_runtime_attempt"
        }
    assert {
        "effect_id",
        "graph_id",
        "graph_revision",
        "node_id",
        "attempt_id",
        "commit_scope",
        "effect_type",
        "payload_ref",
        "payload_json",
        "payload_sha256",
        "idempotency_key",
        "lease_owner",
        "lease_expires_ts_ms",
        "fencing_token",
        "status",
        "receipt_ref",
    } <= columns
    assert attempt_binding == {
        ("attempt_id", "attempt_id"),
        ("graph_id", "graph_id"),
        ("graph_revision", "graph_revision"),
        ("node_id", "node_id"),
    }


def test_gr06_stage_get_list_are_exactly_idempotent_and_binding_safe(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _seed_attempt(store)

    created = _stage(store)
    replay = _stage(store)

    assert created["status"] == AcceptanceEffectStatus.PENDING.value
    assert created["effect_type"] == "conversation.assistant_turn"
    assert created["payload"] == {"role": "assistant"}
    assert created["payload_json"] == '{"role":"assistant"}'
    assert created["fencing_token"] == 0
    assert created["replayed"] is False
    assert replay["effect_id"] == created["effect_id"]
    assert replay["replayed"] is True
    assert store.get_acceptance_effect_intent(str(created["effect_id"]))["status"] == "PENDING"
    assert [row["effect_id"] for row in store.list_acceptance_effect_intents(
        status=AcceptanceEffectStatus.PENDING,
        graph_id="graph-gr06",
        graph_revision=1,
        node_id="node-gr06",
    )] == [created["effect_id"]]

    with pytest.raises(TaskStoreError) as drift:
        _stage(store, payload=b'{"role":"user"}')
    assert drift.value.reason_code == TASK_STORE_OUTBOX_CONFLICT

    with pytest.raises(TaskStoreError) as reused_key:
        _stage(
            store,
            effect_id="acceptance-effect://gr06/different",
            idempotency_key="gr06:assistant-turn",
        )
    assert reused_key.value.reason_code == TASK_STORE_OUTBOX_CONFLICT

    with pytest.raises(TaskStoreError) as bad_digest:
        store.stage_acceptance_effect_intent(
            effect_id="acceptance-effect://gr06/bad-digest",
            graph_id="graph-gr06",
            graph_revision=1,
            node_id="node-gr06",
            attempt_id="attempt-gr06",
            commit_scope="NODE",
            effect_type="conversation.assistant_turn",
            payload_ref="effect-payload://gr06/bad",
            payload={"role": "assistant"},
            payload_sha256="sha256:not-a-digest",
            idempotency_key="gr06:bad-digest",
        )
    assert bad_digest.value.reason_code == TASK_STORE_INVALID

    with pytest.raises(TaskStoreError) as digest_mismatch:
        store.stage_acceptance_effect_intent(
            effect_id="acceptance-effect://gr06/digest-mismatch",
            graph_id="graph-gr06",
            graph_revision=1,
            node_id="node-gr06",
            attempt_id="attempt-gr06",
            commit_scope="NODE",
            effect_type="conversation.assistant_turn",
            payload_ref="effect-payload://gr06/digest-mismatch",
            payload={"role": "assistant"},
            payload_sha256="sha256:" + ("0" * 64),
            idempotency_key="gr06:digest-mismatch",
        )
    assert digest_mismatch.value.reason_code == TASK_STORE_INVALID

    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            UPDATE task_runtime_acceptance_effect_intent
            SET payload_json = ?
            WHERE effect_id = ?
            """,
            ('{"role": "assistant"}', created["effect_id"]),
        )
        conn.commit()
    with pytest.raises(TaskStoreError) as durable_payload_drift:
        store.get_acceptance_effect_intent(str(created["effect_id"]))
    assert durable_payload_drift.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID


def test_gr06_claim_is_acceptance_gated_and_terminal_transitions_are_idempotent(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _seed_attempt(store)
    node_effect = _stage(store)
    graph_effect = _stage(
        store,
        effect_id="acceptance-effect://gr06/final-delivery",
        commit_scope=AcceptanceEffectCommitScope.GRAPH,
        idempotency_key="gr06:final-delivery",
    )

    assert store.claim_acceptance_effect_intent(
        owner_id="dispatcher-a", effect_id=str(node_effect["effect_id"])
    ) is None
    _accept_node(store)

    claimed = store.claim_acceptance_effect_intent(
        owner_id="dispatcher-a",
        lease_ms=500,
        effect_id=str(node_effect["effect_id"]),
    )
    assert claimed is not None
    assert claimed["status"] == AcceptanceEffectStatus.CLAIMED.value
    assert claimed["fencing_token"] == 1
    assert claimed["attempt_count"] == 1

    claim_replay = store.claim_acceptance_effect_intent(
        owner_id="dispatcher-a",
        lease_ms=500,
        effect_id=str(node_effect["effect_id"]),
    )
    assert claim_replay is not None
    assert claim_replay["replayed"] is True
    assert claim_replay["fencing_token"] == claimed["fencing_token"]
    assert claim_replay["attempt_count"] == claimed["attempt_count"]

    committed = store.commit_acceptance_effect_intent(
        str(node_effect["effect_id"]),
        owner_id="dispatcher-a",
        fencing_token=int(claimed["fencing_token"]),
        receipt_ref="conversation-receipt://gr06/assistant-turn",
    )
    replay = store.commit_acceptance_effect_intent(
        str(node_effect["effect_id"]),
        owner_id="dispatcher-a",
        fencing_token=int(claimed["fencing_token"]),
        receipt_ref="conversation-receipt://gr06/assistant-turn",
    )
    assert committed["status"] == AcceptanceEffectStatus.COMMITTED.value
    assert committed["replayed"] is False
    assert replay["replayed"] is True
    with pytest.raises(TaskStoreError) as receipt_drift:
        store.commit_acceptance_effect_intent(
            str(node_effect["effect_id"]),
            owner_id="dispatcher-a",
            fencing_token=int(claimed["fencing_token"]),
            receipt_ref="conversation-receipt://gr06/different",
        )
    assert receipt_drift.value.reason_code == TASK_STORE_OUTBOX_CONFLICT

    assert store.claim_acceptance_effect_intent(
        owner_id="dispatcher-b", effect_id=str(graph_effect["effect_id"])
    ) is None
    store.transition_graph(
        "graph-gr06",
        1,
        expected_version=1,
        target_status=TaskGraphStatus.COMPLETED,
        outbox=_event("TASK_GRAPH", "graph-gr06", 2, "TASK_GRAPH_COMPLETED"),
    )
    graph_claim = store.claim_acceptance_effect_intent(
        owner_id="dispatcher-b", effect_id=str(graph_effect["effect_id"])
    )
    assert graph_claim is not None
    failed = store.fail_acceptance_effect_intent(
        str(graph_effect["effect_id"]),
        owner_id="dispatcher-b",
        fencing_token=int(graph_claim["fencing_token"]),
        reason_code="DELIVERY_REJECTED",
        detail="channel rejected the prepared payload",
    )
    failed_replay = store.fail_acceptance_effect_intent(
        str(graph_effect["effect_id"]),
        owner_id="dispatcher-b",
        fencing_token=int(graph_claim["fencing_token"]),
        reason_code="DELIVERY_REJECTED",
        detail="channel rejected the prepared payload",
    )
    assert failed["status"] == AcceptanceEffectStatus.FAILED.value
    assert failed["receipt_ref"] is None
    assert failed_replay["replayed"] is True


def test_gr06_node_acceptance_and_effect_staging_are_one_atomic_commit(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _seed_attempt(store)
    judgement = _prepare_node_acceptance(store)
    spec = _effect_spec()
    acceptance_event = _event(
        "TASK_NODE",
        "graph-gr06@1/node-gr06",
        7,
        "TASK_NODE_ACCEPTED",
        evidence_refs=judgement.evidence_refs,
    )

    committed = store.transition_node_and_stage_acceptance_effects(
        "graph-gr06",
        1,
        "node-gr06",
        expected_version=6,
        outbox=acceptance_event,
        acceptance_judgement=judgement,
        effect_intents=(spec,),
    )

    assert committed["node"]["status"] == TaskNodeStatus.ACCEPTED.value
    assert committed["effect_intents"][0]["status"] == AcceptanceEffectStatus.PENDING.value
    assert committed["effect_intents"][0]["replayed"] is False
    replay = store.transition_node_and_stage_acceptance_effects(
        "graph-gr06",
        1,
        "node-gr06",
        expected_version=6,
        outbox=acceptance_event,
        acceptance_judgement=judgement,
        effect_intents=(spec,),
    )
    assert replay["replayed"] is True
    assert replay["effect_intents"][0]["replayed"] is True
    assert store.claim_acceptance_effect_intent(
        owner_id="dispatcher-atomic-node",
        effect_id=str(spec["effect_id"]),
    ) is not None


def test_gr06_effect_replay_is_exact_and_drift_rolls_back_node_acceptance(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _seed_attempt(store)
    judgement = _prepare_node_acceptance(store)
    original = _effect_spec()
    store.stage_acceptance_effect_intent(**original)
    drifted = _effect_spec(payload=b'{"role":"user"}')

    with pytest.raises(TaskStoreError) as conflict:
        store.transition_node_and_stage_acceptance_effects(
            "graph-gr06",
            1,
            "node-gr06",
            expected_version=6,
            outbox=_event(
                "TASK_NODE",
                "graph-gr06@1/node-gr06",
                7,
                "TASK_NODE_ACCEPTED",
                evidence_refs=judgement.evidence_refs,
            ),
            acceptance_judgement=judgement,
            effect_intents=(drifted,),
        )
    assert conflict.value.reason_code == TASK_STORE_OUTBOX_CONFLICT
    assert store.get_node("graph-gr06", 1, "node-gr06")["status"] == (
        TaskNodeStatus.WAITING_ACCEPTANCE_REVIEW.value
    )
    with sqlite3.connect(store.db_path) as conn:
        accepted_events = conn.execute(
            "SELECT COUNT(*) FROM task_runtime_outbox WHERE event_type = 'TASK_NODE_ACCEPTED'"
        ).fetchone()[0]
    assert accepted_events == 0

    replayed = store.transition_node_and_stage_acceptance_effects(
        "graph-gr06",
        1,
        "node-gr06",
        expected_version=6,
        outbox=_event(
            "TASK_NODE",
            "graph-gr06@1/node-gr06",
            7,
            "TASK_NODE_ACCEPTED",
            evidence_refs=judgement.evidence_refs,
        ),
        acceptance_judgement=judgement,
        effect_intents=(original,),
    )
    assert replayed["node"]["status"] == TaskNodeStatus.ACCEPTED.value
    assert replayed["effect_intents"][0]["replayed"] is True


def test_gr06_graph_completion_and_graph_effect_staging_are_acceptance_gated(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _seed_attempt(store)
    graph_spec = _effect_spec(
        effect_id="acceptance-effect://gr06/final-delivery",
        commit_scope=AcceptanceEffectCommitScope.GRAPH,
        idempotency_key="gr06:final-delivery",
    )

    with pytest.raises(TaskStoreError) as gate:
        store.transition_graph_and_stage_acceptance_effects(
            "graph-gr06",
            1,
            expected_version=1,
            outbox=_event(
                "TASK_GRAPH",
                "graph-gr06",
                2,
                "TASK_GRAPH_COMPLETED",
            ),
            effect_intents=(graph_spec,),
        )
    assert gate.value.reason_code == TASK_STORE_INVALID
    assert store.get_graph("graph-gr06", 1)["status"] == TaskGraphStatus.ACTIVE.value
    with pytest.raises(TaskStoreError):
        store.get_acceptance_effect_intent(str(graph_spec["effect_id"]))

    _accept_node(store)
    committed = store.transition_graph_and_stage_acceptance_effects(
        "graph-gr06",
        1,
        expected_version=1,
        outbox=_event(
            "TASK_GRAPH",
            "graph-gr06",
            2,
            "TASK_GRAPH_COMPLETED",
        ),
        effect_intents=(graph_spec,),
    )
    assert committed["graph"]["status"] == TaskGraphStatus.COMPLETED.value
    assert committed["effect_intents"][0]["commit_scope"] == (
        AcceptanceEffectCommitScope.GRAPH.value
    )
    assert store.claim_acceptance_effect_intent(
        owner_id="dispatcher-atomic-graph",
        effect_id=str(graph_spec["effect_id"]),
    ) is not None
