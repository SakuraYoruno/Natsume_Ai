from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.graph_evidence_repository import (
    GRAPH_EVIDENCE_MIGRATION_ASSET_KEY,
    GRAPH_EVIDENCE_MIGRATION_SHA256,
    GRAPH_EVIDENCE_SCHEMA_NOT_READY,
    GraphEvidenceRepository,
    GraphEvidenceRepositoryError,
)


ROOT = Path(__file__).resolve().parents[2]
ASSET = ROOT / "host_brain" / "audit" / "migrations" / "0074_graph_evidence_persistence.sql"


def _migrated_db(tmp_path: Path) -> Path:
    path = tmp_path / "graph-evidence.sqlite3"
    result = SQLiteMigrationRunner(db_path=path).apply_migrations(
        request_id="aw03-graph-evidence-contract",
        ts_ms=1_900_000_000_000,
    )
    assert result.success is True, result.as_dict()
    return path


def test_0074_asset_is_transactional_checksum_bound_and_catalogued(tmp_path: Path) -> None:
    sql = ASSET.read_text(encoding="utf-8-sig")
    assert hashlib.sha256(sql.encode("utf-8")).hexdigest() == GRAPH_EVIDENCE_MIGRATION_SHA256
    assert GRAPH_EVIDENCE_MIGRATION_ASSET_KEY == "0074#graph_evidence_persistence"
    assert "BEGIN IMMEDIATE;" in sql
    assert sql.rstrip().endswith("COMMIT;")

    db_path = _migrated_db(tmp_path)
    with sqlite3.connect(db_path) as conn:
        asset = conn.execute(
            "SELECT checksum, disposition FROM schema_migration_assets WHERE asset_key = ?",
            (GRAPH_EVIDENCE_MIGRATION_ASSET_KEY,),
        ).fetchone()
    assert asset == (GRAPH_EVIDENCE_MIGRATION_SHA256, "APPLIED")


def test_all_nine_evidence_types_round_trip_through_typed_repository() -> None:
    plane = GraphEvidenceRepository(":memory:", clock=lambda: 101)
    try:
        plane.append_traces(
            request_id="request-9",
            graph_id="graph-9",
            trace=[{"node_id": "node-trace", "status": "OK"}],
        )
        plane.append_subgraph_summary(
            request_id="request-9",
            summary={"subgraph_id": "summary-9", "node_count": 1},
        )
        plane.append_clock_events(
            request_id="request-9",
            graph_id="graph-9",
            events=[{"event_type": "TICK", "node_id": "clock-9"}],
        )
        plane.append_branch_manifest(
            request_id="request-9",
            graph_id="graph-9",
            manifest={"total_count": 1, "success_count": 1, "branches": [{"id": "b-9"}]},
        )
        plane.append_stream_events(
            request_id="request-9",
            graph_id="graph-9",
            events=[{"phase": "presence", "event_type": "START", "node_id": "stream-9"}],
        )
        plane.save_checkpoint(
            checkpoint_id="checkpoint-9",
            request_id="request-9",
            graph_id="graph-9",
            interrupt_reason="",
            resume_deadline_at=0,
        )
        plane.append_barrier_join(
            request_id="request-9",
            graph_id="graph-9",
            result={"passed": True},
        )
        plane.append_subgraph_traces(
            run_id="run-9",
            trace=[{"node_id": "subgraph-9", "input_digest": "in-9"}],
        )
        plane.append_llm_call(
            run_id="run-9",
            node_id="llm-9",
            call={"model_id": "model-9", "prompt_tokens": 7},
        )

        assert plane.list_traces(request_id="request-9")[0]["node_id"] == "node-trace"
        assert plane.list_subgraph_summaries(request_id="request-9")[0]["subgraph_id"] == "summary-9"
        assert plane.list_clock_events(request_id="request-9")[0]["node_id"] == "clock-9"
        assert plane.list_branch_manifests(request_id="request-9")[0]["total_count"] == 1
        assert plane.list_stream_events(request_id="request-9")[0]["node_id"] == "stream-9"
        assert plane.list_stream_event_cursors_by_time(
            since_ms=0,
            end_ms=101,
        ) == [{"request_id": "request-9", "ts_ms": 101, "event_count": 1}]
        assert plane.get_checkpoint(checkpoint_id="checkpoint-9") is not None
        assert plane.list_barrier_joins(request_id="request-9")[0]["passed"] is True
        assert plane.list_subgraph_traces(run_id="run-9")[0]["node_id"] == "subgraph-9"
        assert plane.list_llm_calls(run_id="run-9")[0]["model_id"] == "model-9"
    finally:
        plane.close()


def test_durable_repository_restarts_without_silent_schema_creation(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    first = GraphEvidenceRepository(db_path, clock=lambda: 202)
    first.append_traces(
        request_id="durable-request",
        graph_id="durable-graph",
        trace=[{"node_id": "durable-node"}],
    )
    first.close()

    restarted = GraphEvidenceRepository(db_path)
    try:
        assert restarted.list_traces(request_id="durable-request")[0]["node_id"] == "durable-node"
    finally:
        restarted.close()

    missing = tmp_path / "must-not-be-created.sqlite3"
    with pytest.raises(GraphEvidenceRepositoryError) as unavailable:
        GraphEvidenceRepository(missing)
    assert missing.exists() is False
    assert unavailable.value.reason_code == GRAPH_EVIDENCE_SCHEMA_NOT_READY


def test_uncatalogued_schema_and_checksum_drift_fail_authority(
    tmp_path: Path,
) -> None:
    uncatalogued = tmp_path / "uncatalogued.sqlite3"
    with sqlite3.connect(uncatalogued) as conn:
        conn.executescript(ASSET.read_text(encoding="utf-8"))
    with pytest.raises(GraphEvidenceRepositoryError) as raised:
        GraphEvidenceRepository(uncatalogued)
    assert raised.value.reason_code == GRAPH_EVIDENCE_SCHEMA_NOT_READY

    drifted = _migrated_db(tmp_path)
    with sqlite3.connect(drifted) as conn:
        conn.execute(
            "UPDATE schema_migration_assets SET checksum = ? WHERE asset_key = ?",
            ("0" * 64, GRAPH_EVIDENCE_MIGRATION_ASSET_KEY),
        )
        conn.commit()
    with pytest.raises(GraphEvidenceRepositoryError) as drift_error:
        GraphEvidenceRepository(drifted)
    assert drift_error.value.reason_code == GRAPH_EVIDENCE_SCHEMA_NOT_READY


def test_failed_operation_rolls_back() -> None:
    def fail(stage: str) -> None:
        if stage == "append_traces:before_commit":
            raise RuntimeError("injected evidence outage")

    repository = GraphEvidenceRepository(":memory:", clock=lambda: 303, fault_injector=fail)
    try:
        with pytest.raises(RuntimeError, match="injected evidence outage"):
            repository.append_traces(
                request_id="rollback-request",
                graph_id="rollback-graph",
                trace=[{"node_id": "must-rollback"}],
            )
        assert repository.list_traces(request_id="rollback-request") == []
    finally:
        repository.close()


def test_legacy_graph_evidence_facade_is_deleted() -> None:
    assert not (ROOT / "host_brain" / "integrations" / "graph_evidence.py").exists()
