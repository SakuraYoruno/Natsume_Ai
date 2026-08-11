from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
from threading import Event
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
    TASK_STORE_CANCELLATION_CONFLICT,
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_INVALID,
    TASK_STORE_OUTBOX_CONFLICT,
    TASK_STORE_SCHEMA_NOT_READY,
    TaskStoreError,
    build_outbox_payload_ref,
)
from host_brain.persistence.task_acceptance_effect_staging_authority import (
    canonical_task_acceptance_effect_staging_scope,
    register_task_acceptance_effect_staging_authority,
)
from tests.task_runtime_authority_fixtures import (
    seed_active_graph_budget_for_test,
    seed_raw_acceptance_effect_for_test,
    seed_raw_task_graph_for_test,
)


ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "host_brain" / "audit" / "migrations"
GR06_OPERATION_SCOPE = "gr06:node-operation"
GR06_OPERATION_FINGERPRINT = "sha256:" + ("a" * 64)
GR06_RESERVATION_ID = "reservation-gr06"
GR06_RECEIPT_REF = "worker-projection://gr06/accepted"


@dataclass
class MutableClock:
    value: int = 10_000

    def __call__(self) -> int:
        return self.value

    def set(self, value: int) -> None:
        self.value = int(value)


class BlockingSampleClock:
    """Pause one clock sample before the caller starts its writer transaction."""

    def __init__(self, value: int = 10_000) -> None:
        self.value = int(value)
        self.sampled = Event()
        self.resume = Event()
        self._block_next = False

    def arm(self) -> None:
        self.sampled.clear()
        self.resume.clear()
        self._block_next = True

    def __call__(self) -> int:
        value = self.value
        if self._block_next:
            self._block_next = False
            self.sampled.set()
            if not self.resume.wait(timeout=10):
                raise TimeoutError("blocked Effect clock sample was not released")
        return value


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
        graph_revision=(1 if aggregate_type == "TASK_GRAPH" else None),
        aggregate_version=aggregate_version,
        event_type=event_type,
        payload_ref=build_outbox_payload_ref(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
            graph_revision=(1 if aggregate_type == "TASK_GRAPH" else None),
        ),
        idempotency_key=f"gr06:{event_type}:{uuid.uuid4().hex}",
        attempt_id=attempt_id,
        evidence_refs=evidence_refs,
    )


def _store(
    tmp_path: Path,
    *,
    clock: MutableClock | BlockingSampleClock | None = None,
) -> DurableTaskStore:
    db_path = tmp_path / "gr06-effects.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="gr06-effect-contract"
    )
    assert migrated.success is True, migrated.as_dict()
    assert (
        migrated.summary["current_version"]
        == migrated.summary["latest_version"]
    )
    store = DurableTaskStore(db_path, clock=clock or MutableClock())
    store.assert_acceptance_effect_intent_ready()
    return store


def _insert_pending_cancellation_proof_marker(
    store: DurableTaskStore,
    *,
    effect_id: str,
    suffix: str,
) -> None:
    """Commit one 0098 proof marker after its authority already validated it.

    The late-mutation guard is intentionally deny-only and does not duplicate
    proof verification.  This fixture therefore inserts the already-committed
    marker directly while foreign-key validation remains the proof Store's
    separate contract responsibility.
    """

    digest = "sha256:" + ("a" * 64)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(
            """
            INSERT INTO task_runtime_effect_reconciliation_proof(
                proof_id, schema_version, purpose, case_id, intent_ref,
                effect_id, graph_id, graph_revision, node_id, attempt_id,
                case_snapshot_sha256, subject_snapshot_json,
                subject_snapshot_sha256, outcome, target_resolution_status,
                receipt_ref, executor_quiescence_ref, reconciler_actor_id,
                attestation_ref, attestation_key_id, attestation_signature,
                issued_ts_ms, proof_binding_json, proof_digest, outbox_id,
                created_ts_ms
            ) VALUES (?, ?, 'PENDING_CANCELLATION', ?, ?, ?, ?, 1, ?, ?, ?,
                      '{}', ?, 'NO_SIDE_EFFECT', 'CANCELLED', NULL, ?, ?, ?, ?,
                      ?, 10000, '{}', ?, ?, 10000)
            """,
            (
                f"effect-proof://gr06/{suffix}",
                "Natsume.task_runtime.acceptance_effect_reconciliation.proof.v1",
                f"effect-case://gr06/{suffix}",
                f"taskcancel-intent://gr06/{suffix}",
                effect_id,
                "graph-gr06",
                "node-gr06",
                "attempt-gr06",
                digest,
                digest,
                f"quiescence://gr06/{suffix}",
                "reconciler://gr06/test",
                f"attestation://gr06/{suffix}",
                "reconciler-gr06-v1",
                "ed25519:" + ("A" * 88),
                digest,
                f"outbox::effect-proof::gr06::{suffix}",
            ),
        )
        conn.commit()


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
    seed_raw_task_graph_for_test(
        store,
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
    seed_active_graph_budget_for_test(
        store,
        graph_id=graph.graph_id,
        graph_revision=graph.revision,
        budget_id="budget-gr06",
        scope="graph:graph-gr06@1:acceptance-effect",
        outbox=_event(
            "BUDGET_RESERVATION",
            "budget-gr06",
            0,
            "BUDGET_RESERVATION_CREATED",
        ),
    )
    store.create_attempt(
        attempt_id="attempt-gr06",
        graph_id=graph.graph_id,
        graph_revision=graph.revision,
        node_id="node-gr06",
        attempt_no=1,
        expected_node_version=0,
        operation_scope=GR06_OPERATION_SCOPE,
        operation_fingerprint=GR06_OPERATION_FINGERPRINT,
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
    reserved = store.reserve_operation(
        reservation_id=GR06_RESERVATION_ID,
        business_scope=GR06_OPERATION_SCOPE,
        operation_fingerprint=GR06_OPERATION_FINGERPRINT,
        attempt_id="attempt-gr06",
        lease_id=lease["lease_id"],
        owner_id="worker-gr06",
        fencing_token=lease["fencing_token"],
        ttl_ms=10_000,
        outbox=_event(
            "OPERATION_RESERVATION",
            GR06_RESERVATION_ID,
            0,
            "OPERATION_RESERVED",
            attempt_id="attempt-gr06",
        ),
    )
    assert reserved.acquired is True
    store.commit_operation(
        GR06_RESERVATION_ID,
        attempt_id="attempt-gr06",
        lease_id=lease["lease_id"],
        owner_id="worker-gr06",
        fencing_token=lease["fencing_token"],
        expected_version=0,
        receipt_ref=GR06_RECEIPT_REF,
        outbox=_event(
            "OPERATION_RESERVATION",
            GR06_RESERVATION_ID,
            1,
            "OPERATION_COMMITTED",
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
        attempt_id="attempt-gr06",
    )
    return judgement


def _accept_node(store: DurableTaskStore) -> None:
    judgement = _prepare_node_acceptance(store)
    acceptance_event = _event(
        "TASK_NODE",
        "graph-gr06@1/node-gr06",
        7,
        "TASK_NODE_ACCEPTED",
        attempt_id="attempt-gr06",
        evidence_refs=judgement.evidence_refs,
    )
    with sqlite3.connect(store.db_path) as conn:
        has_artifact_schema = conn.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'task_runtime_node_artifact'
            """
        ).fetchone() is not None
    if not has_artifact_schema:
        # Explicit compatibility coverage for a database pinned before 0102.
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
        return
    node_effects = tuple(
        item
        for item in store.list_acceptance_effect_intents(
            graph_id="graph-gr06",
            graph_revision=1,
            node_id="node-gr06",
        )
        if item["commit_scope"] == AcceptanceEffectCommitScope.NODE.value
    )
    store.commit_node_acceptance(
        "graph-gr06",
        1,
        "node-gr06",
        expected_version=6,
        outbox=acceptance_event,
        acceptance_judgement=judgement,
        attempt_id="attempt-gr06",
        receipt_ref=GR06_RECEIPT_REF,
        worker_output={"result_text": "effect staged"},
        effect_intents=node_effects,
    )


def _stage(
    store: DurableTaskStore,
    *,
    effect_id: str = "acceptance-effect://gr06/assistant-turn",
    commit_scope: AcceptanceEffectCommitScope = AcceptanceEffectCommitScope.NODE,
    idempotency_key: str = "gr06:assistant-turn",
    payload: bytes = b'{"role":"assistant"}',
) -> dict[str, object]:
    return seed_raw_acceptance_effect_for_test(
        store,
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
    attempt_id: str = "attempt-gr06",
    payload: bytes = b'{"role":"assistant"}',
) -> dict[str, object]:
    return {
        "effect_id": effect_id,
        "graph_id": "graph-gr06",
        "graph_revision": 1,
        "node_id": "node-gr06",
        "attempt_id": attempt_id,
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


def _insert_raw_effect(
    conn: sqlite3.Connection,
    spec: dict[str, object],
    *,
    status: str = "PENDING",
) -> None:
    payload_json = json.dumps(
        spec["payload"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    conn.execute(
        """
        INSERT INTO task_runtime_acceptance_effect_intent(
            effect_id, graph_id, graph_revision, node_id, attempt_id,
            commit_scope, effect_type, payload_ref, payload_json,
            payload_sha256, idempotency_key, status, available_ts_ms,
            fencing_token, attempt_count, status_version,
            created_ts_ms, updated_ts_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 10000,
                  0, 0, 0, 10000, 10000)
        """,
        (
            spec["effect_id"],
            spec["graph_id"],
            spec["graph_revision"],
            spec["node_id"],
            spec["attempt_id"],
            str(spec["commit_scope"].value),
            spec["effect_type"],
            spec["payload_ref"],
            payload_json,
            spec["payload_sha256"],
            spec["idempotency_key"],
            status,
        ),
    )


def test_gr06_0125_effect_staging_requires_exact_scope_and_initial_state(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _seed_attempt(store)
    spec = _effect_spec(
        effect_id="acceptance-effect://gr06/0125-authority",
        idempotency_key="gr06:0125-authority",
    )

    with sqlite3.connect(store.db_path) as plain:
        with pytest.raises(
            sqlite3.OperationalError,
            match="no such function: natsume_task_acceptance_effect_staging_authorized",
        ):
            _insert_raw_effect(plain, spec)

    with store._transaction() as conn:
        with pytest.raises(
            sqlite3.IntegrityError,
            match="TASK_ACCEPTANCE_EFFECT_STAGING_AUTHORITY_REQUIRED",
        ):
            _insert_raw_effect(conn, spec)
        with canonical_task_acceptance_effect_staging_scope(
            effect_id="acceptance-effect://gr06/wrong",
            graph_id=spec["graph_id"],
            graph_revision=spec["graph_revision"],
            node_id=spec["node_id"],
            attempt_id=spec["attempt_id"],
            commit_scope=spec["commit_scope"],
            idempotency_key=spec["idempotency_key"],
        ):
            with pytest.raises(
                sqlite3.IntegrityError,
                match="TASK_ACCEPTANCE_EFFECT_STAGING_AUTHORITY_REQUIRED",
            ):
                _insert_raw_effect(conn, spec)
        with canonical_task_acceptance_effect_staging_scope(
            effect_id=spec["effect_id"],
            graph_id=spec["graph_id"],
            graph_revision=spec["graph_revision"],
            node_id=spec["node_id"],
            attempt_id=spec["attempt_id"],
            commit_scope=spec["commit_scope"],
            idempotency_key=spec["idempotency_key"],
        ):
            with pytest.raises(
                sqlite3.IntegrityError,
                match="TASK_ACCEPTANCE_EFFECT_INITIAL_STATE_REQUIRED",
            ):
                _insert_raw_effect(conn, spec, status="CLAIMED")

    judgement = _prepare_node_acceptance(store)
    outbox = _event(
        "TASK_NODE",
        "graph-gr06@1/node-gr06",
        7,
        "TASK_NODE_ACCEPTED",
        attempt_id="attempt-gr06",
        evidence_refs=judgement.evidence_refs,
    )
    created = store.commit_node_acceptance(
        "graph-gr06",
        1,
        "node-gr06",
        expected_version=6,
        outbox=outbox,
        acceptance_judgement=judgement,
        attempt_id="attempt-gr06",
        receipt_ref=GR06_RECEIPT_REF,
        worker_output={"result_text": "effect staged"},
        effect_intents=(spec,),
    )
    replayed = store.commit_node_acceptance(
        "graph-gr06",
        1,
        "node-gr06",
        expected_version=6,
        outbox=outbox,
        acceptance_judgement=judgement,
        attempt_id="attempt-gr06",
        receipt_ref=GR06_RECEIPT_REF,
        worker_output={"result_text": "effect staged"},
        effect_intents=(spec,),
    )
    assert created["replayed"] is False
    assert replayed["replayed"] is True
    assert [item["effect_id"] for item in replayed["effect_intents"]] == [
        spec["effect_id"]
    ]


def test_gr06_0125_trigger_tamper_fails_schema_readiness(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with sqlite3.connect(store.db_path) as conn:
        row = conn.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'trigger'
              AND name = 'trg_task_runtime_acceptance_effect_staging_authority_insert'
            """
        ).fetchone()
        assert row is not None
        tampered = str(row[0]).replace(
            ") <> 1 THEN",
            ") <> 1 AND 1 = 0 THEN",
            1,
        )
        conn.execute(
            "DROP TRIGGER "
            "trg_task_runtime_acceptance_effect_staging_authority_insert"
        )
        conn.execute(tampered)
        conn.commit()

    with pytest.raises(TaskStoreError) as captured:
        store.assert_acceptance_effect_intent_ready()
    assert captured.value.reason_code == TASK_STORE_SCHEMA_NOT_READY
    assert "trigger SQL drifted" in str(captured.value)


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
        seed_raw_acceptance_effect_for_test(
            store,
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
        seed_raw_acceptance_effect_for_test(
            store,
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


@pytest.mark.parametrize(
    "commit_scope",
    (AcceptanceEffectCommitScope.NODE, AcceptanceEffectCommitScope.GRAPH),
)
def test_gr06_claim_requires_the_exact_attempt_that_materialized_the_artifact(
    tmp_path: Path,
    commit_scope: AcceptanceEffectCommitScope,
) -> None:
    """A successful sibling Attempt cannot borrow another Attempt's Acceptance."""

    store = _store(tmp_path)
    _seed_attempt(store)
    _accept_node(store)
    stale_attempt_id = "attempt-gr06-stale"
    effect_id = f"acceptance-effect://gr06/stale-{commit_scope.value.lower()}"
    spec = _effect_spec(
        effect_id=effect_id,
        commit_scope=commit_scope,
        idempotency_key=f"gr06:stale-{commit_scope.value.lower()}",
        attempt_id=stale_attempt_id,
    )
    payload_json = json.dumps(
        spec["payload"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            """
            INSERT INTO task_runtime_attempt(
                attempt_id, graph_id, graph_revision, node_id, attempt_no,
                status, status_version, owner_id, operation_scope,
                operation_fingerprint, created_ts_ms, acquired_ts_ms,
                started_ts_ms, completed_ts_ms, updated_ts_ms
            ) VALUES (?, 'graph-gr06', 1, 'node-gr06', 2, 'SUCCEEDED', 3,
                      'worker-gr06-stale', ?, ?, 10000, 10000, 10000, 10001, 10001)
            """,
            (
                stale_attempt_id,
                "gr06:node-operation:stale",
                "sha256:" + ("b" * 64),
            ),
        )
        insert_sql = """
                INSERT INTO task_runtime_acceptance_effect_intent(
                    effect_id, graph_id, graph_revision, node_id, attempt_id,
                    commit_scope, effect_type, payload_ref, payload_json,
                    payload_sha256, idempotency_key, status, available_ts_ms,
                    fencing_token, attempt_count, status_version,
                    created_ts_ms, updated_ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', 10000,
                          0, 0, 0, 10000, 10000)
                """
        insert_values = (
            spec["effect_id"],
            spec["graph_id"],
            spec["graph_revision"],
            spec["node_id"],
            spec["attempt_id"],
            commit_scope.value,
            spec["effect_type"],
            spec["payload_ref"],
            payload_json,
            spec["payload_sha256"],
            spec["idempotency_key"],
        )
        with pytest.raises(
            sqlite3.OperationalError,
            match="no such function: natsume_task_acceptance_effect_staging_authorized",
        ):
            conn.execute(insert_sql, insert_values)

        # Explicit test-only authority models a historical row that cannot be
        # produced by a plain production connection after migration 0125.
        register_task_acceptance_effect_staging_authority(conn)
        with canonical_task_acceptance_effect_staging_scope(
            effect_id=spec["effect_id"],
            graph_id=spec["graph_id"],
            graph_revision=spec["graph_revision"],
            node_id=spec["node_id"],
            attempt_id=spec["attempt_id"],
            commit_scope=commit_scope.value,
            idempotency_key=spec["idempotency_key"],
        ):
            conn.execute(insert_sql, insert_values)
        conn.commit()

    if commit_scope is AcceptanceEffectCommitScope.GRAPH:
        store.transition_graph(
            "graph-gr06",
            1,
            expected_version=1,
            target_status=TaskGraphStatus.COMPLETED,
            outbox=_event("TASK_GRAPH", "graph-gr06", 2, "TASK_GRAPH_COMPLETED"),
        )

    assert store.claim_acceptance_effect_intent(
        owner_id="dispatcher-stale",
        effect_id=effect_id,
    ) is None

    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """
            UPDATE task_runtime_acceptance_effect_intent
            SET status = 'CLAIMED', lease_owner = 'dispatcher-stale',
                lease_expires_ts_ms = 20000, fencing_token = 1,
                attempt_count = 1, status_version = 1
            WHERE effect_id = ?
            """,
            (effect_id,),
        )
        conn.commit()
    with pytest.raises(TaskStoreError) as replay_bypass:
        store.claim_acceptance_effect_intent(
            owner_id="dispatcher-stale",
            effect_id=effect_id,
        )
    assert replay_bypass.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID


@pytest.mark.parametrize("claimed_replay", (False, True))
def test_gr06_claim_revalidates_artifact_content_before_dispatch(
    tmp_path: Path,
    claimed_replay: bool,
) -> None:
    store = _store(tmp_path)
    _seed_attempt(store)
    effect = _stage(store)
    _accept_node(store)
    effect_id = str(effect["effect_id"])

    with sqlite3.connect(store.db_path) as conn:
        conn.execute("DROP TRIGGER trg_task_runtime_node_artifact_immutable_update")
        conn.execute(
            """
            UPDATE task_runtime_node_artifact
            SET payload_json = '{"receipt_ref":"worker-projection://tampered"}'
            WHERE graph_id = 'graph-gr06' AND graph_revision = 1
              AND node_id = 'node-gr06'
            """
        )
        if claimed_replay:
            conn.execute(
                """
                UPDATE task_runtime_acceptance_effect_intent
                SET status = 'CLAIMED', lease_owner = 'dispatcher-tamper',
                    lease_expires_ts_ms = 20000, fencing_token = 1,
                    attempt_count = 1, status_version = 1
                WHERE effect_id = ?
                """,
                (effect_id,),
            )
        conn.commit()

    with pytest.raises(TaskStoreError) as tampered:
        store.claim_acceptance_effect_intent(
            owner_id="dispatcher-tamper",
            effect_id=effect_id,
        )
    assert tampered.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID


def test_gr06_delivery_mutations_fail_closed_without_0125_authority(
    tmp_path: Path,
) -> None:
    migrations = tmp_path / "migrations-through-0124"
    migrations.mkdir()
    for source in MIGRATIONS.glob("*.sql"):
        if int(source.name.split("_", 1)[0]) <= 124:
            shutil.copy2(source, migrations / source.name)
    db_path = tmp_path / "gr06-effects-before-0125.sqlite3"
    migrated = SQLiteMigrationRunner(
        db_path=db_path,
        migrations_dir=migrations,
    ).apply_migrations(request_id="gr06-effect-before-0125")
    assert migrated.success is True, migrated.as_dict()
    assert migrated.summary["current_version"] == 124

    store = DurableTaskStore(db_path, clock=MutableClock())
    with pytest.raises(TaskStoreError) as missing:
        store.assert_acceptance_effect_intent_ready()
    assert missing.value.reason_code == TASK_STORE_SCHEMA_NOT_READY
    assert "migration 0125 is missing" in str(missing.value)


@pytest.mark.parametrize("mutation", ("commit", "fail", "reschedule"))
def test_gr06_pending_cancellation_proof_wins_before_late_delivery_mutation(
    tmp_path: Path,
    mutation: str,
) -> None:
    clock = BlockingSampleClock()
    store = _store(tmp_path, clock=clock)
    _seed_attempt(store)
    effect_id = f"acceptance-effect://gr06/proof-first-{mutation}"
    created = _stage(
        store,
        effect_id=effect_id,
        idempotency_key=f"gr06:proof-first:{mutation}",
    )
    _accept_node(store)
    owner_id = f"dispatcher-proof-first-{mutation}"
    claimed = store.claim_acceptance_effect_intent(
        owner_id=owner_id,
        lease_ms=500,
        effect_id=str(created["effect_id"]),
    )
    assert claimed is not None
    before = store.get_acceptance_effect_intent(effect_id)

    def mutate() -> dict[str, object]:
        if mutation == "commit":
            return store.commit_acceptance_effect_intent(
                effect_id,
                owner_id=owner_id,
                fencing_token=int(claimed["fencing_token"]),
                receipt_ref="conversation-receipt://gr06/proof-first",
            )
        if mutation == "fail":
            return store.fail_acceptance_effect_intent(
                effect_id,
                owner_id=owner_id,
                fencing_token=int(claimed["fencing_token"]),
                reason_code="DELIVERY_REJECTED",
                detail="proof committed before late failure",
            )
        return store.reschedule_acceptance_effect_intent(
            effect_id,
            owner_id=owner_id,
            fencing_token=int(claimed["fencing_token"]),
            reason_code="DELIVERY_RETRY",
            detail="proof committed before late retry",
            delay_ms=0,
        )

    clock.arm()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(mutate)
        assert clock.sampled.wait(timeout=10)
        try:
            _insert_pending_cancellation_proof_marker(
                store,
                effect_id=effect_id,
                suffix=mutation,
            )
        finally:
            clock.resume.set()
        with pytest.raises(TaskStoreError) as blocked:
            future.result(timeout=10)

    assert blocked.value.reason_code == TASK_STORE_CANCELLATION_CONFLICT
    after = store.get_acceptance_effect_intent(effect_id)
    assert after["status"] == AcceptanceEffectStatus.CLAIMED.value
    assert after["status_version"] == before["status_version"]
    assert after["fencing_token"] == before["fencing_token"]
    assert after["attempt_count"] == before["attempt_count"]
    assert after["lease_owner"] == before["lease_owner"]
    assert after["lease_expires_ts_ms"] == before["lease_expires_ts_ms"]


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
        attempt_id="attempt-gr06",
        evidence_refs=judgement.evidence_refs,
    )

    committed = store.transition_node_and_stage_acceptance_effects(
        "graph-gr06",
        1,
        "node-gr06",
        expected_version=6,
        outbox=acceptance_event,
        acceptance_judgement=judgement,
        attempt_id="attempt-gr06",
        receipt_ref=GR06_RECEIPT_REF,
        worker_output={"result_text": "effect staged"},
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
        attempt_id="attempt-gr06",
        receipt_ref=GR06_RECEIPT_REF,
        worker_output={"result_text": "effect staged"},
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
    seed_raw_acceptance_effect_for_test(store, **original)
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
                attempt_id="attempt-gr06",
                evidence_refs=judgement.evidence_refs,
            ),
            acceptance_judgement=judgement,
            attempt_id="attempt-gr06",
            receipt_ref=GR06_RECEIPT_REF,
            worker_output={"result_text": "effect staged"},
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
            attempt_id="attempt-gr06",
            evidence_refs=judgement.evidence_refs,
        ),
        acceptance_judgement=judgement,
        attempt_id="attempt-gr06",
        receipt_ref=GR06_RECEIPT_REF,
        worker_output={"result_text": "effect staged"},
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


def test_gr06_standalone_graph_effect_cannot_be_added_after_completion(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _seed_attempt(store)
    _accept_node(store)
    store.transition_graph(
        "graph-gr06",
        1,
        expected_version=1,
        target_status=TaskGraphStatus.COMPLETED,
        outbox=_event("TASK_GRAPH", "graph-gr06", 2, "TASK_GRAPH_COMPLETED"),
    )

    effect_id = "acceptance-effect://gr06/late-graph-effect"
    with pytest.raises(TaskStoreError) as late:
        _stage(
            store,
            effect_id=effect_id,
            commit_scope=AcceptanceEffectCommitScope.GRAPH,
            idempotency_key="gr06:late-graph-effect",
        )
    assert late.value.reason_code == TASK_STORE_OUTBOX_CONFLICT
    with pytest.raises(TaskStoreError):
        store.get_acceptance_effect_intent(effect_id)
