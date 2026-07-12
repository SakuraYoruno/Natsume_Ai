from __future__ import annotations

import hashlib
import logging
from pathlib import Path
import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.integrations.evidence_settings import EvidenceSettings
from host_brain.integrations.graph_evidence import EvidencePlane
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


def test_all_nine_evidence_types_round_trip_through_semantic_facade() -> None:
    plane = EvidencePlane(":memory:", clock=lambda: 101)
    try:
        assert plane.status.schema_authorized is True
        plane.write_trace(
            request_id="request-9",
            graph_id="graph-9",
            trace=[{"node_id": "node-trace", "status": "OK"}],
        )
        plane.write_subgraph_summary(
            request_id="request-9",
            summary={"subgraph_id": "summary-9", "node_count": 1},
        )
        plane.write_clock_events(
            request_id="request-9",
            graph_id="graph-9",
            clock_events=[{"event_type": "TICK", "node_id": "clock-9"}],
        )
        plane.write_branch_manifest(
            request_id="request-9",
            graph_id="graph-9",
            manifest={"total_count": 1, "success_count": 1, "branches": [{"id": "b-9"}]},
        )
        plane.write_stream_events(
            request_id="request-9",
            graph_id="graph-9",
            events=[{"phase": "presence", "event_type": "START", "node_id": "stream-9"}],
        )
        plane.write_checkpoint(
            checkpoint_id="checkpoint-9",
            request_id="request-9",
            graph_id="graph-9",
        )
        plane.write_barrier_join(
            request_id="request-9",
            graph_id="graph-9",
            barrier_join_result={"passed": True},
        )
        plane.write_subgraph_trace(
            run_id="run-9",
            trace=[{"node_id": "subgraph-9", "input_digest": "in-9"}],
        )
        plane.write_llm_call(
            run_id="run-9",
            node_id="llm-9",
            model_id="model-9",
            prompt_tokens=7,
        )

        assert plane.query_trace(request_id="request-9")[0]["node_id"] == "node-trace"
        assert plane.query_subgraph_summary(request_id="request-9")[0]["subgraph_id"] == "summary-9"
        assert plane.query_clock_events(request_id="request-9")[0]["node_id"] == "clock-9"
        assert plane.query_branch_manifest(request_id="request-9")[0]["total_count"] == 1
        assert plane.query_stream_events(request_id="request-9")[0]["node_id"] == "stream-9"
        assert plane.query_checkpoint(checkpoint_id="checkpoint-9") is not None
        assert plane.query_barrier_join(request_id="request-9")[0]["passed"] is True
        assert plane.query_subgraph_trace(run_id="run-9")[0]["node_id"] == "subgraph-9"
        assert plane.query_llm_calls(run_id="run-9")[0]["model_id"] == "model-9"
    finally:
        plane.close()


def test_durable_repository_restarts_without_silent_schema_creation(tmp_path: Path) -> None:
    db_path = _migrated_db(tmp_path)
    first = EvidencePlane(db_path, clock=lambda: 202)
    first.write_trace(
        request_id="durable-request",
        graph_id="durable-graph",
        trace=[{"node_id": "durable-node"}],
    )
    first.close()

    restarted = EvidencePlane(db_path)
    try:
        assert restarted.status.schema_authorized is True
        assert restarted.query_trace(request_id="durable-request")[0]["node_id"] == "durable-node"
    finally:
        restarted.close()

    missing = tmp_path / "must-not-be-created.sqlite3"
    unavailable = EvidencePlane(missing)
    assert missing.exists() is False
    assert unavailable.status.reason_code == GRAPH_EVIDENCE_SCHEMA_NOT_READY
    assert unavailable.status.schema_authorized is False


def test_uncatalogued_schema_and_checksum_drift_fail_authority(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
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
    with caplog.at_level(logging.ERROR):
        plane = EvidencePlane(drifted)
    assert plane.status.reason_code == GRAPH_EVIDENCE_SCHEMA_NOT_READY
    assert plane.available is False
    assert "schema authority verification failed" in caplog.text


def test_failed_operation_rolls_back_and_facade_reports_degradation() -> None:
    def fail(stage: str) -> None:
        if stage == "append_traces:before_commit":
            raise RuntimeError("injected evidence outage")

    repository = GraphEvidenceRepository(":memory:", clock=lambda: 303, fault_injector=fail)
    plane = EvidencePlane(
        settings=EvidenceSettings(":memory:"),
        repository=repository,
    )
    try:
        plane.write_trace(
            request_id="rollback-request",
            graph_id="rollback-graph",
            trace=[{"node_id": "must-rollback"}],
        )
        assert plane.query_trace(request_id="rollback-request") == []
        assert plane.status.degraded is True
        assert plane.status.schema_authorized is True
    finally:
        plane.close()


def test_integration_facade_contains_no_persistence_implementation() -> None:
    source = (ROOT / "host_brain" / "integrations" / "graph_evidence.py").read_text(
        encoding="utf-8"
    )
    forbidden = (
        "sqlite3",
        "os.environ",
        "threading.local",
        "_connect",
        ".execute(",
        "CREATE TABLE",
        "INSERT INTO",
        "SELECT ",
    )
    for token in forbidden:
        assert token not in source, f"Graph Evidence facade still contains {token}"
