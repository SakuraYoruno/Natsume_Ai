from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.planning_search_calibration_repository import (
    CALIBRATION_SCHEMA_NOT_READY,
    CALIBRATION_OUTCOME_DRIFT,
    CALIBRATION_OUTCOME_IDENTITY_MISMATCH,
    CALIBRATION_REPLAY_IDENTITY_DRIFT,
    PLANNING_CALIBRATION_MIGRATION_ASSET_KEY,
    PLANNING_CALIBRATION_MIGRATION_SHA256,
    PlanningCalibrationRepositoryError,
    SQLitePlanningCalibrationRepository,
)
from host_brain.planning_search import CalibrationOutcome, CalibrationPrediction


NOW = 2_001_000_000_000


def _db(tmp_path: Path) -> Path:
    path = tmp_path / "planning-calibration.sqlite3"
    result = SQLiteMigrationRunner(db_path=path).apply_migrations(
        request_id="d13-planning-calibration",
        ts_ms=NOW,
    )
    assert result.success is True, result.as_dict()
    return path


def _prediction(**overrides) -> CalibrationPrediction:
    values = {
        "domain": "pentest",
        "scope_key": "engagement:sqlite",
        "request_id": "request-sqlite-1",
        "state_schema": "pentest.virtual-state.v1",
        "state_digest": "a" * 64,
        "baseline_candidate_id": "baseline",
        "executed_candidate_id": "baseline",
        "executed_path_candidate_ids": ("baseline",),
        "shadow_recommended_candidate_id": "shadow",
        "shadow_path_candidate_ids": ("shadow", "follow-up"),
        "search_trace_digest": "b" * 64,
        "search_budget": {"max_rollouts": 4, "max_transitions": 8},
        "search_cost": {
            "measurement_kind": "VIRTUAL_SEARCH_COUNTERS",
            "model_tokens": 0,
            "model_cost_micros": 0,
            "rollouts": 4,
        },
        "adapter_revision": "pentest-planning-shadow-v2",
        "predicted_reward": 0.5,
    }
    values.update(overrides)
    return CalibrationPrediction(**values)


def _outcome(prediction: CalibrationPrediction, **overrides) -> CalibrationOutcome:
    values = {
        "prediction_id": prediction.prediction_id,
        "executed_candidate_id": prediction.executed_candidate_id,
        "actual_reward": 0.75,
        "outcome_ref": "fixture://outcome/sqlite-1",
        "verifier_refs": ("fixture://verifier/sqlite-1",),
        "outcome_snapshot": {
            "verification_status": "UNVERIFIED_RUNTIME_RECORD",
            "status": "OK",
        },
    }
    values.update(overrides)
    return CalibrationOutcome(**values)


def test_migration_creates_checksum_bound_append_only_calibration_store(tmp_path: Path) -> None:
    path = _db(tmp_path)
    repository = SQLitePlanningCalibrationRepository(path, clock=lambda: NOW + 1)

    with sqlite3.connect(path) as conn:
        asset = conn.execute(
            "SELECT checksum FROM schema_migration_assets WHERE asset_key = ?",
            (PLANNING_CALIBRATION_MIGRATION_ASSET_KEY,),
        ).fetchone()
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        triggers = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'planning_calibration_%'"
            ).fetchall()
        }

    assert asset == (PLANNING_CALIBRATION_MIGRATION_SHA256,)
    assert {
        "planning_search_calibration_prediction",
        "planning_search_calibration_outcome",
    } <= tables
    assert "planning_calibration_prediction_no_update" in triggers
    assert "planning_calibration_outcome_candidate_bound" in triggers
    assert repository.metrics(domain="pentest").predictions == 0


def test_migration_catalog_replay_accepts_preexisting_calibration_tables(
    tmp_path: Path,
) -> None:
    path = _db(tmp_path)
    original = _prediction()
    SQLitePlanningCalibrationRepository(path, clock=lambda: NOW + 1).record_prediction(original)

    # Historical recovery tests deliberately rewind only the migration catalog:
    # later physical tables remain after a lost catalog receipt.  0105 must
    # restore its catalog asset without treating its existing append-only
    # tables, indexes, or triggers as an execution failure.
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM schema_migration_assets WHERE migration_id >= 105")
        conn.execute("DELETE FROM schema_migrations WHERE migration_id >= 105")
        conn.commit()

    replayed = SQLiteMigrationRunner(db_path=path).apply_migrations(
        request_id="d13-planning-calibration-catalog-replay",
        ts_ms=NOW + 10,
    )
    assert replayed.success is True, replayed.as_dict()

    repository = SQLitePlanningCalibrationRepository(path, clock=lambda: NOW + 11)
    assert repository.get(original.prediction_id) is not None
    prediction = _prediction(request_id="request-sqlite-after-catalog-replay")
    assert repository.record_prediction(prediction).prediction == prediction


def test_schema_ready_rejects_semantically_wrong_trigger_after_catalog_receipt(
    tmp_path: Path,
) -> None:
    path = _db(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TRIGGER planning_calibration_prediction_no_update")
        conn.execute(
            """
            CREATE TRIGGER planning_calibration_prediction_no_update
            BEFORE UPDATE ON planning_search_calibration_prediction
            BEGIN
                SELECT 1;
            END
            """
        )
        conn.commit()

    with pytest.raises(PlanningCalibrationRepositoryError) as rejected:
        SQLitePlanningCalibrationRepository(path)
    assert rejected.value.reason_code == CALIBRATION_SCHEMA_NOT_READY


def test_schema_ready_rejects_semantically_wrong_named_index_after_catalog_receipt(
    tmp_path: Path,
) -> None:
    path = _db(tmp_path)
    with sqlite3.connect(path) as conn:
        conn.execute("DROP INDEX idx_planning_calibration_prediction_scope_created")
        conn.execute(
            """
            CREATE INDEX idx_planning_calibration_prediction_scope_created
            ON planning_search_calibration_prediction(domain, created_at_ms, prediction_id)
            """
        )
        conn.commit()

    with pytest.raises(PlanningCalibrationRepositoryError) as rejected:
        SQLitePlanningCalibrationRepository(path)
    assert rejected.value.reason_code == CALIBRATION_SCHEMA_NOT_READY


def test_metrics_use_full_sql_aggregate_instead_of_list_window(tmp_path: Path) -> None:
    path = _db(tmp_path)
    rows = [
        (
            f"calpred_{index:032x}",
            f"{index:064x}",
            "pentest",
            "engagement:bulk",
            f"bulk-request-{index}",
            "pentest.virtual-state.v1",
            "a" * 64,
            f"baseline-{index}",
            f"candidate-{index}",
            f'["candidate-{index}"]',
            f"shadow-{index}",
            f'["shadow-{index}"]',
            "b" * 64,
            '{"max_rollouts":4}',
            '{"model_tokens":0}',
            "bulk-fixture-v1",
            0.5,
            "SHADOW_ONLY",
            NOW + 100 + index,
        )
        for index in range(1_025)
    ]
    with sqlite3.connect(path) as conn:
        conn.executemany(
            """
            INSERT INTO planning_search_calibration_prediction(
                prediction_id, identity_digest, domain, scope_key, request_id,
                state_schema, state_digest, baseline_candidate_id,
                executed_candidate_id, executed_path_json,
                shadow_recommended_candidate_id, shadow_path_json,
                search_trace_digest, search_budget_json, search_cost_json,
                adapter_revision, predicted_reward, mode, created_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()

    metrics = SQLitePlanningCalibrationRepository(path).metrics(
        domain="pentest",
        scope_key="engagement:bulk",
    )
    assert metrics.predictions == 1_025
    assert metrics.reconciled == 0
    assert metrics.pending == 1_025
    assert metrics.mean_absolute_error is None


def test_restart_replay_reconcile_and_metrics_are_exact(tmp_path: Path) -> None:
    path = _db(tmp_path)
    prediction = _prediction()
    first = SQLitePlanningCalibrationRepository(path, clock=lambda: NOW + 1)
    created = first.record_prediction(prediction)

    restarted = SQLitePlanningCalibrationRepository(path, clock=lambda: NOW + 2)
    assert restarted.get(prediction.prediction_id) == created
    assert restarted.record_prediction(prediction) == created

    outcome = _outcome(prediction)
    reconciled = restarted.reconcile_outcome(outcome)
    third = SQLitePlanningCalibrationRepository(path, clock=lambda: NOW + 3)
    assert third.reconcile_outcome(outcome) == reconciled
    assert third.get(prediction.prediction_id) == reconciled
    metrics = third.metrics(domain="pentest", scope_key="engagement:sqlite")
    assert metrics.predictions == 1
    assert metrics.reconciled == 1
    assert metrics.pending == 0
    assert metrics.mean_absolute_error == 0.25


def test_identity_and_outcome_drift_fail_closed_and_sql_rows_cannot_mutate(tmp_path: Path) -> None:
    path = _db(tmp_path)
    repository = SQLitePlanningCalibrationRepository(path, clock=lambda: NOW + 10)
    prediction = _prediction()
    repository.record_prediction(prediction)

    with pytest.raises(PlanningCalibrationRepositoryError) as identity_drift:
        repository.record_prediction(_prediction(state_digest="c" * 64))
    assert identity_drift.value.reason_code == CALIBRATION_REPLAY_IDENTITY_DRIFT

    with pytest.raises(PlanningCalibrationRepositoryError) as wrong_candidate:
        repository.reconcile_outcome(
            _outcome(prediction, executed_candidate_id="other-candidate")
        )
    assert wrong_candidate.value.reason_code == CALIBRATION_OUTCOME_IDENTITY_MISMATCH

    outcome = _outcome(prediction)
    repository.reconcile_outcome(outcome)
    with pytest.raises(PlanningCalibrationRepositoryError) as outcome_drift:
        repository.reconcile_outcome(_outcome(prediction, actual_reward=0.2))
    assert outcome_drift.value.reason_code == CALIBRATION_OUTCOME_DRIFT

    with sqlite3.connect(path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                "UPDATE planning_search_calibration_prediction SET domain = 'code'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                "UPDATE planning_search_calibration_outcome SET actual_reward = 0.1"
            )
