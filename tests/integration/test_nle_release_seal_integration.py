"""T-NLE-24 offline release-seal integration chain.

This test deliberately operates on a SQLite backup clone under ``tmp_path``.
It exercises the real M48 migrations and control-plane/read-model primitives,
but never opens the configured runtime database or calls an external service.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

import pytest
from fastapi import HTTPException

from host_brain.agent_runtime.operator_read_model import OperatorReadModelService
from host_brain.agent_runtime.review_policy import (
    ReviewItem,
    ReviewResumePhase,
    ReviewStatus,
    ReviewType,
)
from host_brain.agent_runtime.review_store import (
    REVIEW_RESUME_ALREADY_CONSUMED,
    ReviewStore,
    ReviewStoreError,
)
from host_brain.agent_runtime.runtime_router import (
    RUNTIME_KILL_SWITCH_ACTIVE,
    CanonicalRuntimeRouter,
    RuntimeExecutor,
    build_canary_metrics,
    build_legacy_drain_report,
)
from host_brain.agent_runtime.task_graph import (
    TaskGraph,
    TaskGraphStatus,
    TaskNode,
    TaskNodeStatus,
)
from host_brain.agent_runtime.task_store import (
    DurableTaskStore,
    OutboxMessage,
    build_outbox_payload_ref,
)
from host_brain.app import (
    internal_agent_routes,
    obs_routes_governance,
    obs_routes_life_evolution,
    obs_routes_task_runtime,
)
from host_brain.app.settings.core import get_core_settings
from host_brain.audit.migration_runner import (
    STATUS_APPLIED,
    STATUS_SKIPPED,
    SQLiteMigrationRunner,
)
from host_brain.integrations.runtime_events import (
    RuntimeEvent,
    RuntimeEventStore,
    RuntimeOutboxPublisher,
)
from host_brain.observability.degradation_state import (
    DEGRADATION_HEALTHY,
    DEGRADATION_RECOVERY_PENDING,
    DegradationStateMachine,
    set_global_degradation_state_machine,
)
from host_brain.observability.governance_view import build_governance_view
from host_brain.persona.life_evolution_view import build_life_evolution_view


ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "host_brain" / "audit" / "migrations"
SEAL_NOW = 1_800_300_000_000


class _Clock:
    def __init__(self, value: int = SEAL_NOW) -> None:
        self.value = int(value)

    def __call__(self) -> int:
        current = self.value
        self.value += 1
        return current


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _migration_sources() -> list[Path]:
    sources: list[Path] = []
    for migration_id in range(50, 58):
        matches = sorted(MIGRATIONS.glob(f"{migration_id:04d}_*.sql"))
        assert len(matches) == 1, f"expected one migration asset for {migration_id:04d}"
        sources.append(matches[0])
    return sources


def _copy_as_continuous_catalog(sources: list[Path], target: Path) -> list[Path]:
    target.mkdir(parents=True)
    copies: list[Path] = []
    for continuous_id, source in enumerate(sources, start=1):
        copy = target / f"{continuous_id:04d}_{source.name[5:]}"
        shutil.copyfile(source, copy)
        assert _sha256_file(copy) == _sha256_file(source)
        copies.append(copy)
    return copies


def _sqlite_backup(source: Path, target: Path) -> None:
    with sqlite3.connect(str(source)) as source_conn, sqlite3.connect(str(target)) as target_conn:
        source_conn.backup(target_conn)


def _graph() -> TaskGraph:
    node = TaskNode(
        node_id="seal-node-1",
        dependencies=(),
        route="skill:offline-seal",
        input_refs=("artifact://seal/input/1",),
        output_contract={"type": "seal-evidence"},
        retry_policy={"max_attempts": 1},
        risk_level=2,
        acceptance_criteria=("release chain is replayable",),
        status=TaskNodeStatus.READY,
        status_version=0,
    )
    graph = TaskGraph(
        graph_id="graph-release-seal",
        root_work_order_id="work-order://release-seal",
        revision=1,
        planner_ref="planner://release-seal/offline",
        status=TaskGraphStatus.CREATED,
        created_at=SEAL_NOW,
        nodes=(node,),
    )
    graph.validate()
    return graph


def _outbox() -> OutboxMessage:
    return OutboxMessage.create(
        aggregate_type="TASK_GRAPH",
        aggregate_id="graph-release-seal",
        aggregate_version=0,
        event_type="TASK_GRAPH_CREATED",
        payload_ref=build_outbox_payload_ref(
            aggregate_type="TASK_GRAPH",
            aggregate_id="graph-release-seal",
            aggregate_version=0,
        ),
        idempotency_key="release-seal-graph-created",
        evidence_refs=("test-run://T-NLE-24/task-graph",),
    )


async def _stream_body(response: object) -> str:
    chunks: list[str] = []
    body_iterator = getattr(response, "body_iterator")
    async for chunk in body_iterator:
        chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk))
    return "".join(chunks)


def test_t_nle_24_release_seal_key_chain_is_restart_safe_and_rollbackable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Clone a closed local baseline with SQLite's backup API.  The source file
    # and the checked-in migration assets are treated as immutable inputs.
    source_db = tmp_path / "source-baseline.sqlite3"
    clone_db = tmp_path / "release-candidate.sqlite3"
    with sqlite3.connect(str(source_db)) as conn:
        conn.execute(
            "CREATE TABLE seal_baseline(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO seal_baseline VALUES ('release', 'm48')")
    source_db_hash = _sha256_file(source_db)
    _sqlite_backup(source_db, clone_db)
    assert _sha256_file(source_db) == source_db_hash

    sources = _migration_sources()
    source_asset_hashes = {path: _sha256_file(path) for path in sources}
    catalog = tmp_path / "continuous-migration-catalog"
    copies = _copy_as_continuous_catalog(sources, catalog)
    assert len(copies) == 8

    runner = SQLiteMigrationRunner(db_path=clone_db, migrations_dir=catalog)
    first = runner.apply_migrations(request_id="t-nle-24-migrate-1", ts_ms=SEAL_NOW)
    second = runner.apply_migrations(request_id="t-nle-24-migrate-2", ts_ms=SEAL_NOW + 1)
    assert first.success is True and first.summary == {
        "scope": "sqlite",
        "current_version": 8,
        "latest_version": 8,
        "applied_count": 8,
        "skipped_count": 0,
        "failed_count": 0,
    }
    assert second.success is True
    assert second.summary["applied_count"] == 0
    assert second.summary["skipped_count"] == 8
    assert all(record.status == STATUS_APPLIED for record in first.records)
    assert all(record.status == STATUS_SKIPPED for record in second.records)
    assert _sha256_file(source_db) == source_db_hash
    assert {path: _sha256_file(path) for path in sources} == source_asset_hashes

    with sqlite3.connect(str(clone_db)) as conn:
        assert conn.execute("SELECT value FROM seal_baseline WHERE key='release'").fetchone() == ("m48",)
        assert conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone() == (8,)

    # Persist a real graph/outbox, publish it to the durable timeline, then
    # append a second event so restart and Last-Event-ID reconnect have a gap.
    clock = _Clock()
    task_store = DurableTaskStore(clone_db, clock=clock)
    event_store = RuntimeEventStore(clone_db, clock=clock)
    task_store.create_graph(_graph(), outbox=_outbox())
    published = RuntimeOutboxPublisher(
        task_store=task_store,
        event_store=event_store,
    ).publish_pending(limit=10)
    assert published["published_count"] == 1
    first_event = event_store.list_events(after_cursor=0, limit=10)[0]
    second_event = event_store.append_event(
        RuntimeEvent.create(
            event_type="TASK_GRAPH_SEAL_EVIDENCE_RECORDED",
            source_kind="TASK",
            source_ref="task://graph/release-seal",
            aggregate_type="TASK_GRAPH",
            aggregate_id="graph-release-seal",
            aggregate_version=1,
            payload_ref="taskstore://event/TASK_GRAPH/graph-release-seal/1",
            payload={
                "aggregate_type": "TASK_GRAPH",
                "aggregate_id": "graph-release-seal",
                "aggregate_version": 1,
                "payload_ref": "taskstore://event/TASK_GRAPH/graph-release-seal/1",
            },
            evidence_refs=("test-run://T-NLE-24/replay",),
            ts_ms=SEAL_NOW + 10,
        )
    )
    duplicate = event_store.append_event(
        RuntimeEvent.create(
            event_type="TASK_GRAPH_SEAL_EVIDENCE_RECORDED",
            source_kind="TASK",
            source_ref="task://graph/release-seal",
            aggregate_type="TASK_GRAPH",
            aggregate_id="graph-release-seal",
            aggregate_version=1,
            payload_ref="taskstore://event/TASK_GRAPH/graph-release-seal/1",
            payload={
                "aggregate_type": "TASK_GRAPH",
                "aggregate_id": "graph-release-seal",
                "aggregate_version": 1,
                "payload_ref": "taskstore://event/TASK_GRAPH/graph-release-seal/1",
            },
            evidence_refs=("test-run://T-NLE-24/replay",),
            ts_ms=SEAL_NOW + 10,
        )
    )
    assert duplicate["cursor"] == second_event["cursor"]

    reopened_events = RuntimeEventStore(clone_db, clock=clock)
    reconnect = reopened_events.list_events(after_cursor=first_event["cursor"], limit=10)
    assert [event["cursor"] for event in reconnect] == [second_event["cursor"]]
    assert len({event["dedupe_key"] for event in reopened_events.list_events(after_cursor=0)}) == 2

    # Exercise the real SSE endpoint's Last-Event-ID parsing/framing against
    # the cloned database, while keeping the degradation gate healthy.
    monkeypatch.setenv("HOST_RUNTIME_EVENT_DB_PATH", str(clone_db))
    monkeypatch.setenv("HOST_RUNTIME_EVENT_REPLAY_ENABLED", "true")
    get_core_settings.cache_clear()
    healthy_for_sse = DegradationStateMachine()
    set_global_degradation_state_machine(healthy_for_sse)
    try:
        response = asyncio.run(
            internal_agent_routes.stream_runtime_events_sse(
                after=0,
                limit=10,
                event_type=None,
                source_kind=None,
                last_event_id=str(first_event["cursor"]),
            )
        )
        wire = asyncio.run(_stream_body(response))
    finally:
        set_global_degradation_state_machine(None)
    assert f"id: {second_event['cursor']}" in wire
    assert f'"after":{first_event["cursor"]}' in wire
    assert f'"next_cursor":{second_event["cursor"]}' in wire
    assert wire.count("event: runtime_event\n") == 1

    # Inject and recover a dependency failure.  Recovery remains latched over
    # restart until a bound durable ReviewStore token is consumed exactly once.
    degradation = DegradationStateMachine(event_store=RuntimeEventStore(clone_db, clock=clock))
    degradation.enter_degradation(
        dependency_id="MEMORY",
        status="DOWN",
        evidence_refs=("probe-evidence://memory/down/t-nle-24",),
        source_ref="probe-source://memory/t-nle-24/down",
        now_ms=SEAL_NOW + 20,
        detail="isolated release-seal fault",
        injected=True,
    )
    degradation.recover_dependency(
        dependency_id="MEMORY",
        status="UP",
        evidence_refs=("probe-evidence://memory/up/t-nle-24",),
        source_ref="probe-source://memory/t-nle-24/up",
        now_ms=SEAL_NOW + 21,
    )
    restored = DegradationStateMachine(event_store=RuntimeEventStore(clone_db, clock=clock))
    assert restored.snapshot().status == DEGRADATION_RECOVERY_PENDING
    assert restored.evaluate_capability(
        capability_id="memory_write",
        static_risk_level=4,
    )["decision"] == "BLOCK"

    review_store = ReviewStore(clone_db, clock=clock)
    binding = restored.recovery_review_binding("MEMORY")
    review_store.create_review(
        ReviewItem(
            review_id="review-t-nle-24-memory-recovery",
            review_type=ReviewType.PRE_EXECUTION_APPROVAL,
            subject_ref=binding["subject_ref"],
            subject_version=binding["subject_version"],
            requester="release-seal",
            reason="evidence-backed recovery drill",
            risk_level=5,
            side_effects=tuple(binding["side_effects"]),
            evidence_refs=tuple(binding["evidence_refs"]),
            status=ReviewStatus.PENDING,
            expires_at=SEAL_NOW + 100_000,
            resume_phase=ReviewResumePhase.PRE_EXECUTION,
            checkpoint_id=binding["checkpoint_id"],
        )
    )
    approved = review_store.approve_review(
        "review-t-nle-24-memory-recovery",
        actor_id="release-operator",
        expected_subject_ref=binding["subject_ref"],
        expected_subject_version=binding["subject_version"],
        expected_resume_phase=ReviewResumePhase.PRE_EXECUTION,
        expected_checkpoint_id=binding["checkpoint_id"],
    )
    reopened_reviews = ReviewStore(clone_db, clock=clock)
    restored.acknowledge_recovery(
        dependency_id="MEMORY",
        review_store=reopened_reviews,
        review_id="review-t-nle-24-memory-recovery",
        resume_token_ref=approved["resume_token_ref"],
        now_ms=SEAL_NOW + 22,
    )
    assert reopened_reviews.get_review("review-t-nle-24-memory-recovery")["review"]["status"] == "CONSUMED"
    with pytest.raises(ReviewStoreError) as replayed_review:
        reopened_reviews.resume_review(
            "review-t-nle-24-memory-recovery",
            resume_token_ref=approved["resume_token_ref"],
            subject_ref=binding["subject_ref"],
            subject_version=binding["subject_version"],
            resume_phase=ReviewResumePhase.PRE_EXECUTION,
            checkpoint_id=binding["checkpoint_id"],
        )
    assert replayed_review.value.reason_code == REVIEW_RESUME_ALREADY_CONSUMED
    final_degradation = DegradationStateMachine(
        event_store=RuntimeEventStore(clone_db, clock=clock)
    )
    assert final_degradation.snapshot().status == DEGRADATION_HEALTHY

    # Canary selection, legacy drain and the kill switch are one deterministic
    # control-plane sequence; no executor or side effect is invoked.
    canonical = CanonicalRuntimeRouter(
        mode="canonical",
        canary_percent=100,
        canonical_owner="release-owner",
        confirmed_owner="release-owner",
        clock=clock,
    ).assign_new_task("work-order://release-seal/canary")
    assert canonical.executor is RuntimeExecutor.CANONICAL
    assert build_canary_metrics([canonical])["canonical_count"] == 1
    open_drain = build_legacy_drain_report(
        [
            {"job_id": "legacy-running", "status": "RUNNING"},
            {"job_id": "legacy-done", "status": "COMPLETED"},
        ]
    )
    closed_drain = build_legacy_drain_report(
        [{"job_id": "legacy-done", "status": "COMPLETED"}]
    )
    assert open_drain["drained"] is False and closed_drain["drained"] is True
    killed = CanonicalRuntimeRouter(
        mode="canonical",
        canary_percent=100,
        kill_switch=True,
        canonical_owner="release-owner",
        confirmed_owner="release-owner",
        clock=clock,
    ).assign_new_task("work-order://release-seal/rollback")
    assert killed.executor is RuntimeExecutor.LEGACY
    assert killed.reason_code == RUNTIME_KILL_SWITCH_ACTIVE

    # Build the real persisted operator projection plus the Life/Governance
    # public read models.  All three remain explicitly read-only.
    operator = OperatorReadModelService(
        task_store=DurableTaskStore(clone_db, clock=clock),
        review_store=ReviewStore(clone_db, clock=clock),
        event_store=RuntimeEventStore(clone_db, clock=clock),
        clock=clock,
    )
    execution_index = operator.list_executions(limit=10)
    execution = operator.get_execution(
        "graph-release-seal",
        1,
        after_cursor=first_event["cursor"],
        event_limit=10,
    )
    assert execution_index["read_only"] is True
    assert execution_index["executions"][0]["graph_id"] == "graph-release-seal"
    assert execution["read_only"] is True
    assert execution["cursor_page"]["event_count"] == 1
    assert execution["cursor_page"]["next_cursor"] == second_event["cursor"]

    life = build_life_evolution_view(
        person_id="release-seal",
        world_snapshot={
            "schema_version": "Natsume.world_projection.v1",
            "status": "OK",
            "generated_at_ms": SEAL_NOW,
            "facts": [
                {
                    "key": "runtime.release_mode",
                    "value": "canonical-canary",
                    "source": "release_seal",
                    "source_ref": "release-seal://T-NLE-24/runtime-mode",
                    "confidence": 1.0,
                    "observed_at_ms": SEAL_NOW,
                    "expires_at_ms": SEAL_NOW + 60_000,
                    "epistemic_type": "observed",
                    "conflict_state": "NONE",
                    "reason_codes": [],
                }
            ],
        },
        now_ms=SEAL_NOW,
    )
    governance = build_governance_view(
        capability_scorecards=[],
        degradation_state=final_degradation.snapshot().as_dict(),
        pentest_environment={"lab_mode": False, "allow_exploit": True},
        generated_at_ms=SEAL_NOW,
    )
    assert life["read_only"] is True and life["write_api_available"] is False
    assert life["world"]["facts"][0]["fact_class"] == "FACT"
    assert governance["read_only"] is True and governance["write_api"] is False
    assert governance["degradation"]["status"] == DEGRADATION_HEALTHY
    assert governance["domain_isolation"]["pentest"]["high_risk_entrypoints_disabled"] is True

    # Feature-flag rollback hides all incremental read surfaces and forces new
    # tasks back to legacy through the configured kill switch.
    monkeypatch.setenv("HOST_TASK_RUNTIME_MODE", "canonical")
    monkeypatch.setenv("HOST_TASK_RUNTIME_CANARY_PERCENT", "100")
    monkeypatch.setenv("HOST_TASK_RUNTIME_CANONICAL_OWNER", "release-owner")
    monkeypatch.setenv("HOST_TASK_RUNTIME_KILL_SWITCH", "false")
    monkeypatch.setenv("HOST_NLE_OPERATOR_VIEWS_ENABLED", "true")
    monkeypatch.setenv("HOST_NLE_LIFE_VIEWS_ENABLED", "true")
    monkeypatch.setenv("HOST_NLE_GOVERNANCE_VIEWS_ENABLED", "true")
    get_core_settings.cache_clear()
    enabled = get_core_settings()
    assert enabled.task_runtime_mode == "canonical"
    assert enabled.nle_operator_views_enabled is True
    assert enabled.nle_life_views_enabled is True
    assert enabled.nle_governance_views_enabled is True

    monkeypatch.setenv("HOST_TASK_RUNTIME_KILL_SWITCH", "true")
    monkeypatch.setenv("HOST_NLE_OPERATOR_VIEWS_ENABLED", "false")
    monkeypatch.setenv("HOST_NLE_LIFE_VIEWS_ENABLED", "false")
    monkeypatch.setenv("HOST_NLE_GOVERNANCE_VIEWS_ENABLED", "false")
    get_core_settings.cache_clear()
    rolled_back = get_core_settings()
    configured_rollback = CanonicalRuntimeRouter(
        mode=rolled_back.task_runtime_mode,
        canary_percent=rolled_back.task_runtime_canary_percent,
        kill_switch=rolled_back.task_runtime_kill_switch,
        canonical_owner=rolled_back.task_runtime_canonical_owner,
        confirmed_owner=rolled_back.task_runtime_canonical_owner,
        clock=clock,
    ).assign_new_task("work-order://release-seal/config-rollback")
    assert configured_rollback.executor is RuntimeExecutor.LEGACY
    assert configured_rollback.reason_code == RUNTIME_KILL_SWITCH_ACTIVE
    for gate in (
        obs_routes_task_runtime._require_operator_views_enabled,
        obs_routes_life_evolution._require_life_views_enabled,
        obs_routes_governance._require_governance_views_enabled,
    ):
        with pytest.raises(HTTPException) as hidden:
            gate()
        assert hidden.value.status_code == 404

    with sqlite3.connect(str(clone_db)) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert _sha256_file(source_db) == source_db_hash
    assert {path: _sha256_file(path) for path in sources} == source_asset_hashes
    assert "PRIVATE" not in json.dumps(
        {"operator": execution, "life": life, "governance": governance},
        ensure_ascii=False,
        sort_keys=True,
    )
    get_core_settings.cache_clear()
