"""SQLite persistence for immutable PlanningSearch calibration evidence."""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable, Iterator, Mapping

from host_brain.core.ids import now_ms
from host_brain.planning_search.calibration import (
    CalibrationContractError,
    CalibrationMetrics,
    CalibrationOutcome,
    CalibrationPrediction,
    CalibrationRecord,
)


CALIBRATION_SCHEMA_NOT_READY = "CALIBRATION_SCHEMA_NOT_READY"
CALIBRATION_INVALID = "CALIBRATION_INVALID"
CALIBRATION_NOT_FOUND = "CALIBRATION_NOT_FOUND"
CALIBRATION_REPLAY_IDENTITY_DRIFT = "CALIBRATION_REPLAY_IDENTITY_DRIFT"
CALIBRATION_OUTCOME_IDENTITY_MISMATCH = "CALIBRATION_OUTCOME_IDENTITY_MISMATCH"
CALIBRATION_OUTCOME_DRIFT = "CALIBRATION_OUTCOME_DRIFT"
CALIBRATION_DURABLE_DATA_INVALID = "CALIBRATION_DURABLE_DATA_INVALID"
CALIBRATION_STORE_UNAVAILABLE = "CALIBRATION_STORE_UNAVAILABLE"

PLANNING_CALIBRATION_MIGRATION_ASSET_KEY = "0105#planning_search_calibration"
PLANNING_CALIBRATION_MIGRATION_SHA256 = (
    "8a6ba21244e67a3b6858ee782e4b59c8b1de194136dfdf26c2cfaa247280783b"
)

_ALLOWED_DISPOSITIONS = frozenset({"APPLIED", "APPLIED_WITH_COMPAT", "LEGACY_BACKFILL"})
_PREDICTION_COLUMNS = frozenset(
    {
        "prediction_id",
        "identity_digest",
        "domain",
        "scope_key",
        "request_id",
        "state_schema",
        "state_digest",
        "baseline_candidate_id",
        "executed_candidate_id",
        "executed_path_json",
        "shadow_recommended_candidate_id",
        "shadow_path_json",
        "search_trace_digest",
        "search_budget_json",
        "search_cost_json",
        "adapter_revision",
        "predicted_reward",
        "mode",
        "created_at_ms",
    }
)
_OUTCOME_COLUMNS = frozenset(
    {
        "prediction_id",
        "executed_candidate_id",
        "actual_reward",
        "outcome_ref",
        "verifier_refs_json",
        "outcome_snapshot_json",
        "outcome_digest",
        "reconciled_at_ms",
    }
)
_REQUIRED_TRIGGERS = frozenset(
    {
        "planning_calibration_prediction_no_update",
        "planning_calibration_prediction_no_delete",
        "planning_calibration_outcome_no_update",
        "planning_calibration_outcome_no_delete",
        "planning_calibration_outcome_candidate_bound",
    }
)
_PREDICTION_TABLE_SQL_FRAGMENTS = frozenset(
    {
        "PREDICTION_IDTEXTNOTNULLPRIMARYKEY",
        "IDENTITY_DIGESTTEXTNOTNULLUNIQUE",
        "CHECK(LENGTH(IDENTITY_DIGEST)=64)",
        "CHECK(LENGTH(STATE_DIGEST)=64)",
        "CHECK(LENGTH(SEARCH_TRACE_DIGEST)=64)",
        "PREDICTED_REWARDREALNOTNULLCHECK(PREDICTED_REWARD>=0.0ANDPREDICTED_REWARD<=1.0)",
        "MODETEXTNOTNULLCHECK(MODE='SHADOW_ONLY')",
        "UNIQUE(DOMAIN,SCOPE_KEY,REQUEST_ID,EXECUTED_CANDIDATE_ID)",
    }
)
_OUTCOME_TABLE_SQL_FRAGMENTS = frozenset(
    {
        "PREDICTION_IDTEXTNOTNULLPRIMARYKEYREFERENCESPLANNING_SEARCH_CALIBRATION_PREDICTION(PREDICTION_ID)ONDELETERESTRICT",
        "ACTUAL_REWARDREALNOTNULLCHECK(ACTUAL_REWARD>=0.0ANDACTUAL_REWARD<=1.0)",
        "CHECK(LENGTH(OUTCOME_DIGEST)=64)",
    }
)
_REQUIRED_TRIGGER_SEMANTICS = {
    "planning_calibration_prediction_no_update": (
        "planning_search_calibration_prediction",
        (
            "BEFOREUPDATEONPLANNING_SEARCH_CALIBRATION_PREDICTION",
            "RAISE(ABORT,'PLANNINGCALIBRATIONPREDICTIONISIMMUTABLE')",
        ),
    ),
    "planning_calibration_prediction_no_delete": (
        "planning_search_calibration_prediction",
        (
            "BEFOREDELETEONPLANNING_SEARCH_CALIBRATION_PREDICTION",
            "RAISE(ABORT,'PLANNINGCALIBRATIONPREDICTIONISAPPEND-ONLY')",
        ),
    ),
    "planning_calibration_outcome_no_update": (
        "planning_search_calibration_outcome",
        (
            "BEFOREUPDATEONPLANNING_SEARCH_CALIBRATION_OUTCOME",
            "RAISE(ABORT,'PLANNINGCALIBRATIONOUTCOMEISIMMUTABLE')",
        ),
    ),
    "planning_calibration_outcome_no_delete": (
        "planning_search_calibration_outcome",
        (
            "BEFOREDELETEONPLANNING_SEARCH_CALIBRATION_OUTCOME",
            "RAISE(ABORT,'PLANNINGCALIBRATIONOUTCOMEISAPPEND-ONLY')",
        ),
    ),
    "planning_calibration_outcome_candidate_bound": (
        "planning_search_calibration_outcome",
        (
            "BEFOREINSERTONPLANNING_SEARCH_CALIBRATION_OUTCOME",
            "WHENNOTEXISTS(SELECT1FROMPLANNING_SEARCH_CALIBRATION_PREDICTIONWHEREPREDICTION_ID=NEW.PREDICTION_IDANDEXECUTED_CANDIDATE_ID=NEW.EXECUTED_CANDIDATE_ID)",
            "RAISE(ABORT,'PLANNINGCALIBRATIONOUTCOMECANDIDATEDOESNOTMATCHPREDICTION')",
        ),
    ),
}
_REQUIRED_NAMED_INDEXES = {
    "idx_planning_calibration_prediction_domain_created": (
        "planning_search_calibration_prediction",
        ("domain", "created_at_ms", "prediction_id"),
    ),
    "idx_planning_calibration_prediction_scope_created": (
        "planning_search_calibration_prediction",
        ("domain", "scope_key", "created_at_ms", "prediction_id"),
    ),
    "idx_planning_calibration_outcome_reconciled": (
        "planning_search_calibration_outcome",
        ("reconciled_at_ms", "prediction_id"),
    ),
}
_REQUIRED_UNIQUE_INDEXES = {
    "planning_search_calibration_prediction": (
        ("prediction_id",),
        ("identity_digest",),
        ("domain", "scope_key", "request_id", "executed_candidate_id"),
    ),
    "planning_search_calibration_outcome": (("prediction_id",),),
}


class PlanningCalibrationRepositoryError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or CALIBRATION_INVALID)
        self.detail = str(detail or "planning calibration persistence failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


class SQLitePlanningCalibrationRepository:
    """One canonical SQLite owner for shadow-only calibration evidence.

    Prediction and outcome rows are append-only.  Replays are exact reads; a
    changed identity or outcome is rejected rather than silently corrected.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Callable[[], int] = now_ms,
        timeout_ms: int = 5_000,
        verify_schema: bool = True,
    ) -> None:
        raw = str(db_path or "").strip()
        if not raw or raw == ":memory:" or raw.lower().startswith("file:"):
            raise PlanningCalibrationRepositoryError(
                CALIBRATION_INVALID,
                "planning calibration requires a durable SQLite file path",
            )
        if not callable(clock):
            raise PlanningCalibrationRepositoryError(
                CALIBRATION_INVALID,
                "clock must be callable",
            )
        self.db_path = Path(raw).expanduser().resolve()
        self._clock = clock
        self._timeout_ms = max(1, int(timeout_ms))
        if verify_schema:
            self.assert_schema_ready()

    def assert_schema_ready(self) -> None:
        if not self.db_path.is_file():
            raise PlanningCalibrationRepositoryError(
                CALIBRATION_SCHEMA_NOT_READY,
                f"planning calibration database does not exist: {self.db_path}",
            )
        try:
            with self._connection() as conn:
                asset = conn.execute(
                    """
                    SELECT checksum, disposition
                    FROM schema_migration_assets
                    WHERE asset_key = ?
                    """,
                    (PLANNING_CALIBRATION_MIGRATION_ASSET_KEY,),
                ).fetchone()
                prediction_columns = _columns(
                    conn, "planning_search_calibration_prediction"
                )
                outcome_columns = _columns(
                    conn, "planning_search_calibration_outcome"
                )
                trigger_rows = conn.execute(
                    """
                    SELECT name, tbl_name, sql FROM sqlite_master
                    WHERE type = 'trigger'
                      AND name LIKE 'planning_calibration_%'
                    """
                ).fetchall()
                foreign_keys = int(conn.execute("PRAGMA foreign_keys").fetchone()[0])
                prediction_sql = _schema_sql(
                    conn, "table", "planning_search_calibration_prediction"
                )
                outcome_sql = _schema_sql(
                    conn, "table", "planning_search_calibration_outcome"
                )
                named_indexes_ready = all(
                    _named_index_matches(conn, table, name, columns)
                    for name, (table, columns) in _REQUIRED_NAMED_INDEXES.items()
                )
                unique_indexes_ready = all(
                    _has_unique_index(conn, table, columns)
                    for table, requirements in _REQUIRED_UNIQUE_INDEXES.items()
                    for columns in requirements
                )
                outcome_fk_ready = _has_outcome_prediction_foreign_key(conn)
        except sqlite3.DatabaseError as exc:
            raise PlanningCalibrationRepositoryError(
                CALIBRATION_SCHEMA_NOT_READY,
                "planning calibration schema cannot be verified",
            ) from exc
        trigger_names = {str(row["name"]) for row in trigger_rows}
        if (
            asset is None
            or str(asset["checksum"]) != PLANNING_CALIBRATION_MIGRATION_SHA256
            or str(asset["disposition"]) not in _ALLOWED_DISPOSITIONS
            or not _PREDICTION_COLUMNS.issubset(prediction_columns)
            or not _OUTCOME_COLUMNS.issubset(outcome_columns)
            or not _REQUIRED_TRIGGERS.issubset(trigger_names)
            or foreign_keys != 1
            or not _columns_are_not_null(prediction_columns)
            or not _columns_are_not_null(outcome_columns)
            or not _schema_has_fragments(prediction_sql, _PREDICTION_TABLE_SQL_FRAGMENTS)
            or not _schema_has_fragments(outcome_sql, _OUTCOME_TABLE_SQL_FRAGMENTS)
            or not _triggers_match_semantics(trigger_rows)
            or not named_indexes_ready
            or not unique_indexes_ready
            or not outcome_fk_ready
        ):
            raise PlanningCalibrationRepositoryError(
                CALIBRATION_SCHEMA_NOT_READY,
                "planning calibration migration 0105 is missing or drifted",
            )

    def record_prediction(self, prediction: CalibrationPrediction) -> CalibrationRecord:
        if not isinstance(prediction, CalibrationPrediction):
            raise PlanningCalibrationRepositoryError(
                CALIBRATION_INVALID,
                "prediction must be CalibrationPrediction",
            )
        created_at_ms = self._timestamp()
        try:
            with self._transaction() as conn:
                existing_id = conn.execute(
                    """
                    SELECT prediction_id
                    FROM planning_search_calibration_prediction
                    WHERE prediction_id = ?
                       OR identity_digest = ?
                       OR (
                            domain = ?
                        AND scope_key = ?
                        AND request_id = ?
                        AND executed_candidate_id = ?
                       )
                    ORDER BY created_at_ms, prediction_id
                    LIMIT 1
                    """,
                    (
                        prediction.prediction_id,
                        prediction.identity_digest,
                        prediction.domain,
                        prediction.scope_key,
                        prediction.request_id,
                        prediction.executed_candidate_id,
                    ),
                ).fetchone()
                if existing_id is not None:
                    record = self._record(conn, str(existing_id["prediction_id"]))
                    if record.prediction != prediction:
                        raise PlanningCalibrationRepositoryError(
                            CALIBRATION_REPLAY_IDENTITY_DRIFT,
                            "prediction identity already owns different immutable evidence",
                        )
                    return record
                conn.execute(
                    """
                    INSERT INTO planning_search_calibration_prediction(
                        prediction_id, identity_digest, domain, scope_key,
                        request_id, state_schema, state_digest,
                        baseline_candidate_id, executed_candidate_id,
                        executed_path_json, shadow_recommended_candidate_id,
                        shadow_path_json, search_trace_digest,
                        search_budget_json, search_cost_json, adapter_revision,
                        predicted_reward, mode, created_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        prediction.prediction_id,
                        prediction.identity_digest,
                        prediction.domain,
                        prediction.scope_key,
                        prediction.request_id,
                        prediction.state_schema,
                        prediction.state_digest,
                        prediction.baseline_candidate_id,
                        prediction.executed_candidate_id,
                        _canonical_json(list(prediction.executed_path_candidate_ids)),
                        prediction.shadow_recommended_candidate_id,
                        _canonical_json(list(prediction.shadow_path_candidate_ids)),
                        prediction.search_trace_digest,
                        _canonical_json(prediction.search_budget),
                        _canonical_json(prediction.search_cost),
                        prediction.adapter_revision,
                        prediction.predicted_reward,
                        prediction.mode,
                        created_at_ms,
                    ),
                )
                return self._record(conn, prediction.prediction_id)
        except PlanningCalibrationRepositoryError:
            raise
        except sqlite3.DatabaseError as exc:
            raise _store_error(exc) from exc

    def reconcile_outcome(self, outcome: CalibrationOutcome) -> CalibrationRecord:
        if not isinstance(outcome, CalibrationOutcome):
            raise PlanningCalibrationRepositoryError(
                CALIBRATION_INVALID,
                "outcome must be CalibrationOutcome",
            )
        reconciled_at_ms = self._timestamp()
        try:
            with self._transaction() as conn:
                record = self._record(conn, outcome.prediction_id)
                if record.prediction.executed_candidate_id != outcome.executed_candidate_id:
                    raise PlanningCalibrationRepositoryError(
                        CALIBRATION_OUTCOME_IDENTITY_MISMATCH,
                        "outcome candidate does not match the prediction's executed candidate",
                    )
                if record.outcome is not None:
                    if record.outcome != outcome:
                        raise PlanningCalibrationRepositoryError(
                            CALIBRATION_OUTCOME_DRIFT,
                            "prediction already owns a different immutable outcome",
                        )
                    return record
                if reconciled_at_ms < record.created_at_ms:
                    raise PlanningCalibrationRepositoryError(
                        CALIBRATION_INVALID,
                        "outcome timestamp cannot precede prediction timestamp",
                    )
                conn.execute(
                    """
                    INSERT INTO planning_search_calibration_outcome(
                        prediction_id, executed_candidate_id, actual_reward,
                        outcome_ref, verifier_refs_json, outcome_snapshot_json,
                        outcome_digest, reconciled_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        outcome.prediction_id,
                        outcome.executed_candidate_id,
                        outcome.actual_reward,
                        outcome.outcome_ref,
                        _canonical_json(list(outcome.verifier_refs)),
                        _canonical_json(outcome.outcome_snapshot),
                        outcome.outcome_digest,
                        reconciled_at_ms,
                    ),
                )
                return self._record(conn, outcome.prediction_id)
        except PlanningCalibrationRepositoryError:
            raise
        except sqlite3.IntegrityError as exc:
            raise PlanningCalibrationRepositoryError(
                CALIBRATION_OUTCOME_DRIFT,
                "outcome insert conflicts with immutable calibration evidence",
            ) from exc
        except sqlite3.DatabaseError as exc:
            raise _store_error(exc) from exc

    def get(self, prediction_id: str) -> CalibrationRecord | None:
        identifier = _identifier(prediction_id)
        try:
            with self._connection() as conn:
                row = conn.execute(
                    """
                    SELECT 1
                    FROM planning_search_calibration_prediction
                    WHERE prediction_id = ?
                    """,
                    (identifier,),
                ).fetchone()
                return None if row is None else self._record(conn, identifier)
        except PlanningCalibrationRepositoryError:
            raise
        except sqlite3.DatabaseError as exc:
            raise _store_error(exc) from exc

    def list_records(
        self,
        *,
        domain: str,
        scope_key: str | None = None,
        limit: int = 256,
    ) -> tuple[CalibrationRecord, ...]:
        normalized_domain = _text(domain, "domain", 128)
        normalized_scope = (
            None if scope_key is None else _text(scope_key, "scope_key", 512)
        )
        bounded_limit = max(1, min(int(limit), 1_024))
        where = "domain = ?"
        params: list[Any] = [normalized_domain]
        if normalized_scope is not None:
            where += " AND scope_key = ?"
            params.append(normalized_scope)
        params.append(bounded_limit)
        try:
            with self._connection() as conn:
                rows = conn.execute(
                    f"""
                    SELECT prediction_id
                    FROM planning_search_calibration_prediction
                    WHERE {where}
                    ORDER BY created_at_ms, prediction_id
                    LIMIT ?
                    """,
                    tuple(params),
                ).fetchall()
                return tuple(self._record(conn, str(row["prediction_id"])) for row in rows)
        except PlanningCalibrationRepositoryError:
            raise
        except sqlite3.DatabaseError as exc:
            raise _store_error(exc) from exc

    def metrics(
        self,
        *,
        domain: str,
        scope_key: str | None = None,
    ) -> CalibrationMetrics:
        normalized_domain = _text(domain, "domain", 128)
        normalized_scope = (
            None if scope_key is None else _text(scope_key, "scope_key", 512)
        )
        where = "p.domain = ?"
        params: list[Any] = [normalized_domain]
        if normalized_scope is not None:
            where += " AND p.scope_key = ?"
            params.append(normalized_scope)
        try:
            with self._connection() as conn:
                row = conn.execute(
                    f"""
                    SELECT
                        COUNT(*) AS predictions,
                        COUNT(o.prediction_id) AS reconciled,
                        AVG(ABS(p.predicted_reward - o.actual_reward)) AS mean_absolute_error
                    FROM planning_search_calibration_prediction AS p
                    LEFT JOIN planning_search_calibration_outcome AS o
                        ON o.prediction_id = p.prediction_id
                    WHERE {where}
                    """,
                    tuple(params),
                ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise _store_error(exc) from exc
        predictions = int(row["predictions"] or 0)
        reconciled = int(row["reconciled"] or 0)
        mae = (
            None
            if row["mean_absolute_error"] is None
            else float(row["mean_absolute_error"])
        )
        return CalibrationMetrics(
            domain=normalized_domain,
            predictions=predictions,
            reconciled=reconciled,
            pending=predictions - reconciled,
            mean_absolute_error=mae,
        )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=self._timeout_ms / 1000.0,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {self._timeout_ms}")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def _record(self, conn: sqlite3.Connection, prediction_id: str) -> CalibrationRecord:
        row = conn.execute(
            """
            SELECT
                p.prediction_id AS p_prediction_id,
                p.identity_digest AS p_identity_digest,
                p.domain AS p_domain,
                p.scope_key AS p_scope_key,
                p.request_id AS p_request_id,
                p.state_schema AS p_state_schema,
                p.state_digest AS p_state_digest,
                p.baseline_candidate_id AS p_baseline_candidate_id,
                p.executed_candidate_id AS p_executed_candidate_id,
                p.executed_path_json AS p_executed_path_json,
                p.shadow_recommended_candidate_id AS p_shadow_recommended_candidate_id,
                p.shadow_path_json AS p_shadow_path_json,
                p.search_trace_digest AS p_search_trace_digest,
                p.search_budget_json AS p_search_budget_json,
                p.search_cost_json AS p_search_cost_json,
                p.adapter_revision AS p_adapter_revision,
                p.predicted_reward AS p_predicted_reward,
                p.mode AS p_mode,
                p.created_at_ms AS p_created_at_ms,
                o.prediction_id AS o_prediction_id,
                o.executed_candidate_id AS o_executed_candidate_id,
                o.actual_reward AS o_actual_reward,
                o.outcome_ref AS o_outcome_ref,
                o.verifier_refs_json AS o_verifier_refs_json,
                o.outcome_snapshot_json AS o_outcome_snapshot_json,
                o.outcome_digest AS o_outcome_digest,
                o.reconciled_at_ms AS o_reconciled_at_ms
            FROM planning_search_calibration_prediction AS p
            LEFT JOIN planning_search_calibration_outcome AS o
                ON o.prediction_id = p.prediction_id
            WHERE p.prediction_id = ?
            """,
            (prediction_id,),
        ).fetchone()
        if row is None:
            raise PlanningCalibrationRepositoryError(
                CALIBRATION_NOT_FOUND,
                "calibration prediction does not exist",
            )
        try:
            prediction = CalibrationPrediction(
                prediction_id=str(row["p_prediction_id"]),
                domain=str(row["p_domain"]),
                scope_key=str(row["p_scope_key"]),
                request_id=str(row["p_request_id"]),
                state_schema=str(row["p_state_schema"]),
                state_digest=str(row["p_state_digest"]),
                baseline_candidate_id=str(row["p_baseline_candidate_id"]),
                executed_candidate_id=str(row["p_executed_candidate_id"]),
                executed_path_candidate_ids=tuple(
                    _parse_canonical_json(row["p_executed_path_json"], "executed_path_json")
                ),
                shadow_recommended_candidate_id=str(
                    row["p_shadow_recommended_candidate_id"]
                ),
                shadow_path_candidate_ids=tuple(
                    _parse_canonical_json(row["p_shadow_path_json"], "shadow_path_json")
                ),
                search_trace_digest=str(row["p_search_trace_digest"]),
                search_budget=_parse_canonical_json(
                    row["p_search_budget_json"], "search_budget_json"
                ),
                search_cost=_parse_canonical_json(
                    row["p_search_cost_json"], "search_cost_json"
                ),
                adapter_revision=str(row["p_adapter_revision"]),
                predicted_reward=row["p_predicted_reward"],
                mode=str(row["p_mode"]),
            )
            if prediction.identity_digest != str(row["p_identity_digest"]):
                raise CalibrationContractError("prediction identity digest drift")
            outcome: CalibrationOutcome | None = None
            reconciled_at_ms: int | None = None
            if row["o_prediction_id"] is not None:
                outcome = CalibrationOutcome(
                    prediction_id=str(row["o_prediction_id"]),
                    executed_candidate_id=str(row["o_executed_candidate_id"]),
                    actual_reward=row["o_actual_reward"],
                    outcome_ref=str(row["o_outcome_ref"]),
                    verifier_refs=tuple(
                        _parse_canonical_json(
                            row["o_verifier_refs_json"], "verifier_refs_json"
                        )
                    ),
                    outcome_snapshot=_parse_canonical_json(
                        row["o_outcome_snapshot_json"], "outcome_snapshot_json"
                    ),
                )
                if outcome.outcome_digest != str(row["o_outcome_digest"]):
                    raise CalibrationContractError("outcome digest drift")
                reconciled_at_ms = int(row["o_reconciled_at_ms"])
            return CalibrationRecord(
                prediction=prediction,
                outcome=outcome,
                created_at_ms=int(row["p_created_at_ms"]),
                reconciled_at_ms=reconciled_at_ms,
            )
        except (CalibrationContractError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PlanningCalibrationRepositoryError(
                CALIBRATION_DURABLE_DATA_INVALID,
                "stored planning calibration evidence is malformed or drifted",
            ) from exc

    def _timestamp(self) -> int:
        try:
            value = int(self._clock())
        except (TypeError, ValueError) as exc:
            raise PlanningCalibrationRepositoryError(
                CALIBRATION_INVALID,
                "clock returned an invalid calibration timestamp",
            ) from exc
        if value < 0:
            raise PlanningCalibrationRepositoryError(
                CALIBRATION_INVALID,
                "clock returned a negative calibration timestamp",
            )
        return value


def _columns(conn: sqlite3.Connection, table: str) -> dict[str, sqlite3.Row]:
    rows = conn.execute(f"PRAGMA table_info({_pragma_literal(table)})").fetchall()
    return {str(row["name"]): row for row in rows}


def _columns_are_not_null(columns: Mapping[str, sqlite3.Row]) -> bool:
    prediction_id = columns.get("prediction_id")
    return bool(
        prediction_id is not None
        and int(prediction_id["pk"]) == 1
        and all(int(row["notnull"]) == 1 for row in columns.values())
    )


def _schema_sql(conn: sqlite3.Connection, object_type: str, name: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = ? AND name = ?",
        (object_type, name),
    ).fetchone()
    return "" if row is None or row["sql"] is None else str(row["sql"])


def _schema_has_fragments(sql: str, fragments: frozenset[str]) -> bool:
    normalized = _normalize_sql(sql)
    return bool(normalized) and all(fragment in normalized for fragment in fragments)


def _triggers_match_semantics(rows: list[sqlite3.Row]) -> bool:
    by_name = {str(row["name"]): row for row in rows}
    for name, (table, fragments) in _REQUIRED_TRIGGER_SEMANTICS.items():
        row = by_name.get(name)
        if row is None or str(row["tbl_name"]) != table:
            return False
        normalized = _normalize_sql(str(row["sql"] or ""))
        if not all(fragment in normalized for fragment in fragments):
            return False
    return True


def _named_index_matches(
    conn: sqlite3.Connection,
    table: str,
    name: str,
    expected_columns: tuple[str, ...],
) -> bool:
    rows = conn.execute(f"PRAGMA index_list({_pragma_literal(table)})").fetchall()
    row = next((item for item in rows if str(item["name"]) == name), None)
    if row is None or int(row["unique"]) != 0:
        return False
    return _index_columns(conn, name) == expected_columns


def _has_unique_index(
    conn: sqlite3.Connection,
    table: str,
    expected_columns: tuple[str, ...],
) -> bool:
    rows = conn.execute(f"PRAGMA index_list({_pragma_literal(table)})").fetchall()
    return any(
        int(row["unique"]) == 1
        and _index_columns(conn, str(row["name"])) == expected_columns
        for row in rows
    )


def _index_columns(conn: sqlite3.Connection, name: str) -> tuple[str, ...]:
    rows = conn.execute(f"PRAGMA index_info({_pragma_literal(name)})").fetchall()
    return tuple(str(row["name"]) for row in sorted(rows, key=lambda row: int(row["seqno"])))


def _has_outcome_prediction_foreign_key(conn: sqlite3.Connection) -> bool:
    rows = conn.execute(
        "PRAGMA foreign_key_list('planning_search_calibration_outcome')"
    ).fetchall()
    return any(
        str(row["table"]) == "planning_search_calibration_prediction"
        and str(row["from"]) == "prediction_id"
        and str(row["to"]) == "prediction_id"
        and str(row["on_delete"]).upper() == "RESTRICT"
        for row in rows
    )


def _normalize_sql(value: str) -> str:
    return "".join(str(value or "").upper().split())


def _pragma_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _parse_canonical_json(value: Any, field_name: str) -> Any:
    raw = str(value)
    parsed = json.loads(raw)
    if _canonical_json(parsed) != raw:
        raise CalibrationContractError(f"{field_name} is not canonical JSON")
    return parsed


def _text(value: Any, field_name: str, limit: int) -> str:
    try:
        # Reuse the contract's normalization by constructing no evidence.
        text = str(value or "").strip()
    except Exception as exc:  # pragma: no cover - defensive conversion boundary
        raise PlanningCalibrationRepositoryError(CALIBRATION_INVALID, f"{field_name} is invalid") from exc
    if not text or len(text) > limit:
        raise PlanningCalibrationRepositoryError(
            CALIBRATION_INVALID,
            f"{field_name} must be non-empty and at most {limit} characters",
        )
    return text


def _identifier(value: Any) -> str:
    identifier = _text(value, "prediction_id", 256)
    if not identifier.startswith("calpred_"):
        raise PlanningCalibrationRepositoryError(
            CALIBRATION_INVALID,
            "prediction_id is not a calibration prediction identifier",
        )
    return identifier


def _store_error(exc: sqlite3.DatabaseError) -> PlanningCalibrationRepositoryError:
    detail = str(exc).lower()
    return PlanningCalibrationRepositoryError(
        CALIBRATION_STORE_UNAVAILABLE,
        "planning calibration SQLite operation failed"
        if "locked" not in detail and "busy" not in detail
        else "planning calibration SQLite store is busy",
    )


__all__ = [
    "CALIBRATION_DURABLE_DATA_INVALID",
    "CALIBRATION_INVALID",
    "CALIBRATION_NOT_FOUND",
    "CALIBRATION_OUTCOME_DRIFT",
    "CALIBRATION_OUTCOME_IDENTITY_MISMATCH",
    "CALIBRATION_REPLAY_IDENTITY_DRIFT",
    "CALIBRATION_SCHEMA_NOT_READY",
    "CALIBRATION_STORE_UNAVAILABLE",
    "PLANNING_CALIBRATION_MIGRATION_ASSET_KEY",
    "PLANNING_CALIBRATION_MIGRATION_SHA256",
    "PlanningCalibrationRepositoryError",
    "SQLitePlanningCalibrationRepository",
]
