"""SQLite owner for cleanup-gated historical Code benchmark evidence.

The repository is intentionally isolated from Host, TaskGraph, AgentExecutor,
ModelAttempt and production composition.  It persists only preregistered,
trusted-process shadow experiment evidence and fails closed on drift.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable, Iterator, Mapping

from host_brain.core.ids import now_ms
from host_brain.persistence._sqlite_schema import (
    _pragma_literal,
    columns as _columns,
    named_index_matches as _named_index_matches,
    normalize_sql as _normalize_sql,
    schema_has_fragments as _schema_has_fragments,
    schema_sql as _schema_sql,
)
from host_brain.contracts.planning_search.benchmarking import (
    BenchmarkArm,
    BenchmarkArmOutcome,
    DomainBenchmarkReport,
    DomainBenchmarkWorkload,
)
from host_brain.contracts.planning_search.code_benchmark_ledger import (
    CodeBenchmarkExecutionBudget,
    CodeBenchmarkLedgerContractError,
    CodeBenchmarkRunReceipt,
    CodeBenchmarkRunSpec,
    CodeBenchmarkWorkloadRegistration,
)
from host_brain.contracts.planning_search.code_benchmark_models import (
    CODE_BENCHMARK_CLEANUP_STATUS,
    CodeBenchmarkArmEvidence,
    CodeBenchmarkCleanupReceipt,
    CodeHistoricalBenchmarkRun,
    CodeVerifierResult,
)
from host_brain.contracts.planning_search.code_benchmark_verification_contracts import (
    CodeBenchmarkControlledVerificationReceipt,
    CodeBenchmarkVerificationPlan,
)
from host_brain.contracts.planning_search.contracts import SearchBudget


CODE_BENCHMARK_DURABLE_DATA_INVALID = "CODE_BENCHMARK_DURABLE_DATA_INVALID"
CODE_BENCHMARK_INVALID = "CODE_BENCHMARK_INVALID"
CODE_BENCHMARK_NOT_FOUND = "CODE_BENCHMARK_NOT_FOUND"
CODE_BENCHMARK_PREREGISTRATION_REQUIRED = "CODE_BENCHMARK_PREREGISTRATION_REQUIRED"
CODE_BENCHMARK_REPLAY_DRIFT = "CODE_BENCHMARK_REPLAY_DRIFT"
CODE_BENCHMARK_SCHEMA_NOT_READY = "CODE_BENCHMARK_SCHEMA_NOT_READY"
CODE_BENCHMARK_STORE_UNAVAILABLE = "CODE_BENCHMARK_STORE_UNAVAILABLE"

CODE_BENCHMARK_MIGRATION_ASSET_KEY = "0106#code_historical_benchmark_ledger"
CODE_BENCHMARK_MIGRATION_SHA256 = (
    "bfa1fe5569109126ab64b17033884902e83c539bae68969358fecaa85357eb40"
)

_ALLOWED_DISPOSITIONS = frozenset({"APPLIED", "APPLIED_WITH_COMPAT", "LEGACY_BACKFILL"})
_TABLE_COLUMNS = {
    "planning_search_code_benchmark_workload": frozenset(
        {
            "registration_id",
            "registration_digest",
            "workload_id",
            "workload_digest",
            "cohort_key",
            "source_ref",
            "source_commit",
            "path_policy_digest",
            "execution_budget_json",
            "registration_json",
            "mode",
            "production_influence_enabled",
            "created_at_ms",
        }
    ),
    "planning_search_code_benchmark_run_spec": frozenset(
        {
            "run_id",
            "spec_digest",
            "registration_id",
            "registration_digest",
            "workload_digest",
            "baseline_arm_id",
            "selected_arm_id",
            "baseline_arm_digest",
            "selected_arm_digest",
            "spec_json",
            "mode",
            "production_influence_enabled",
            "created_at_ms",
        }
    ),
    "planning_search_code_benchmark_arm_execution": frozenset(
        {
            "run_id",
            "arm_role",
            "arm_id",
            "arm_digest",
            "evidence_digest",
            "source_ref",
            "source_commit",
            "path_policy_digest",
            "evidence_json",
            "outcome_json",
            "created_at_ms",
        }
    ),
    "planning_search_code_benchmark_cleanup_receipt": frozenset(
        {
            "run_id",
            "cleanup_digest",
            "cleanup_status",
            "cleanup_json",
            "created_at_ms",
        }
    ),
    "planning_search_code_benchmark_receipt": frozenset(
        {
            "run_id",
            "receipt_digest",
            "run_digest",
            "baseline_evidence_digest",
            "selected_evidence_digest",
            "cleanup_digest",
            "receipt_status",
            "claim_scope",
            "production_influence_enabled",
            "receipt_json",
            "created_at_ms",
        }
    ),
}
_TABLE_SQL_FRAGMENTS = {
    "planning_search_code_benchmark_workload": frozenset(
        {
            "REGISTRATION_IDTEXTNOTNULLPRIMARYKEY",
            "REGISTRATION_DIGESTTEXTNOTNULLUNIQUE",
            "WORKLOAD_DIGESTTEXTNOTNULLUNIQUE",
            "MODETEXTNOTNULLCHECK(MODE='PRIVATE_BARE_WORKTREE_TRUSTED_PROCESS_ONLY')",
            "PRODUCTION_INFLUENCE_ENABLEDINTEGERNOTNULLCHECK(PRODUCTION_INFLUENCE_ENABLED=0)",
        }
    ),
    "planning_search_code_benchmark_run_spec": frozenset(
        {
            "RUN_IDTEXTNOTNULLPRIMARYKEY",
            "SPEC_DIGESTTEXTNOTNULLUNIQUE",
            "REFERENCESPLANNING_SEARCH_CODE_BENCHMARK_WORKLOAD(REGISTRATION_ID)ONDELETERESTRICT",
            "CHECK(BASELINE_ARM_ID<>SELECTED_ARM_ID)",
        }
    ),
    "planning_search_code_benchmark_arm_execution": frozenset(
        {
            "PRIMARYKEY(RUN_ID,ARM_ROLE)",
            "CHECK(ARM_ROLEIN('BASELINE','SELECTED'))",
            "UNIQUE(RUN_ID,ARM_ID)",
        }
    ),
    "planning_search_code_benchmark_cleanup_receipt": frozenset(
        {
            "RUN_IDTEXTNOTNULLPRIMARYKEY",
            "CLEANUP_STATUSTEXTNOTNULLCHECK(CLEANUP_STATUS='VERIFIED_CLEAN')",
        }
    ),
    "planning_search_code_benchmark_receipt": frozenset(
        {
            "RUN_IDTEXTNOTNULLPRIMARYKEY",
            "RECEIPT_DIGESTTEXTNOTNULLUNIQUE",
            "RUN_DIGESTTEXTNOTNULLUNIQUE",
            "RECEIPT_STATUSTEXTNOTNULLCHECK(RECEIPT_STATUS='COMPLETED_CLEAN')",
            "CLAIM_SCOPETEXTNOTNULLCHECK(CLAIM_SCOPE='EXPERIMENT_EVIDENCE_ONLY')",
        }
    ),
}
_REQUIRED_NAMED_INDEXES = {
    "idx_code_benchmark_workload_cohort_created": (
        "planning_search_code_benchmark_workload",
        ("cohort_key", "created_at_ms", "registration_id"),
    ),
    "idx_code_benchmark_run_spec_registration_created": (
        "planning_search_code_benchmark_run_spec",
        ("registration_id", "created_at_ms", "run_id"),
    ),
    "idx_code_benchmark_arm_execution_evidence": (
        "planning_search_code_benchmark_arm_execution",
        ("evidence_digest", "created_at_ms", "run_id"),
    ),
    "idx_code_benchmark_receipt_created": (
        "planning_search_code_benchmark_receipt",
        ("created_at_ms", "run_id"),
    ),
}
_REQUIRED_TRIGGERS = {
    "code_benchmark_run_spec_registration_bound": (
        "planning_search_code_benchmark_run_spec",
        (
            "BEFOREINSERTONPLANNING_SEARCH_CODE_BENCHMARK_RUN_SPEC",
            "CODEBENCHMARKRUNSPECDOESNOTBINDWORKLOADREGISTRATION",
        ),
    ),
    "code_benchmark_arm_execution_bound": (
        "planning_search_code_benchmark_arm_execution",
        (
            "BEFOREINSERTONPLANNING_SEARCH_CODE_BENCHMARK_ARM_EXECUTION",
            "CODEBENCHMARKARMEXECUTIONDOESNOTBINDPREREGISTEREDARM",
        ),
    ),
    "code_benchmark_cleanup_receipt_pair_complete": (
        "planning_search_code_benchmark_cleanup_receipt",
        (
            "BEFOREINSERTONPLANNING_SEARCH_CODE_BENCHMARK_CLEANUP_RECEIPT",
            "CODEBENCHMARKCLEANUPREQUIRESBOTHPAIREDARMEXECUTIONS",
        ),
    ),
    "code_benchmark_receipt_cleanup_bound": (
        "planning_search_code_benchmark_receipt",
        (
            "BEFOREINSERTONPLANNING_SEARCH_CODE_BENCHMARK_RECEIPT",
            "CODEBENCHMARKRECEIPTREQUIRESCLEANUP-GATEDPAIREDEVIDENCE",
        ),
    ),
}
for _table, _prefix in (
    ("planning_search_code_benchmark_workload", "code_benchmark_workload"),
    ("planning_search_code_benchmark_run_spec", "code_benchmark_run_spec"),
    ("planning_search_code_benchmark_arm_execution", "code_benchmark_arm_execution"),
    ("planning_search_code_benchmark_cleanup_receipt", "code_benchmark_cleanup_receipt"),
    ("planning_search_code_benchmark_receipt", "code_benchmark_receipt"),
):
    _REQUIRED_TRIGGERS[f"{_prefix}_no_update"] = (
        _table,
        (f"BEFOREUPDATEON{_table.upper()}", "ISIMMUTABLE"),
    )
    _REQUIRED_TRIGGERS[f"{_prefix}_no_delete"] = (
        _table,
        (f"BEFOREDELETEON{_table.upper()}", "ISAPPEND-ONLY"),
    )


class CodeBenchmarkLedgerRepositoryError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or CODE_BENCHMARK_INVALID)
        self.detail = str(detail or "Code benchmark ledger persistence failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


class SQLiteCodeHistoricalBenchmarkLedger:
    """One canonical SQLite owner for D18 experiment evidence.

    Registration, run specs, paired arm facts, cleanup facts and terminal
    receipts are append-only.  The terminal row is visible only once all
    prerequisite facts are atomically present and cleanup has been verified.
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
            raise CodeBenchmarkLedgerRepositoryError(
                CODE_BENCHMARK_INVALID,
                "Code benchmark ledger requires a durable SQLite file path",
            )
        if not callable(clock):
            raise CodeBenchmarkLedgerRepositoryError(
                CODE_BENCHMARK_INVALID,
                "clock must be callable",
            )
        self.db_path = Path(raw).expanduser().resolve()
        self._clock = clock
        self._timeout_ms = max(1, int(timeout_ms))
        if verify_schema:
            self.assert_schema_ready()

    def assert_schema_ready(self) -> None:
        if not self.db_path.is_file():
            raise CodeBenchmarkLedgerRepositoryError(
                CODE_BENCHMARK_SCHEMA_NOT_READY,
                f"Code benchmark database does not exist: {self.db_path}",
            )
        try:
            with self._connection() as conn:
                asset = conn.execute(
                    """
                    SELECT checksum, disposition
                    FROM schema_migration_assets
                    WHERE asset_key = ?
                    """,
                    (CODE_BENCHMARK_MIGRATION_ASSET_KEY,),
                ).fetchone()
                table_columns = {
                    name: _columns(conn, name) for name in _TABLE_COLUMNS
                }
                table_sql = {
                    name: _schema_sql(conn, "table", name) for name in _TABLE_COLUMNS
                }
                trigger_rows = conn.execute(
                    """
                    SELECT name, tbl_name, sql FROM sqlite_master
                    WHERE type = 'trigger' AND name LIKE 'code_benchmark_%'
                    """
                ).fetchall()
                foreign_keys = int(conn.execute("PRAGMA foreign_keys").fetchone()[0])
                indexes_ready = all(
                    _named_index_matches(conn, table, name, columns)
                    for name, (table, columns) in _REQUIRED_NAMED_INDEXES.items()
                )
                foreign_keys_ready = _foreign_keys_ready(conn)
        except sqlite3.DatabaseError as exc:
            raise CodeBenchmarkLedgerRepositoryError(
                CODE_BENCHMARK_SCHEMA_NOT_READY,
                "Code benchmark ledger schema cannot be verified",
            ) from exc
        ready = (
            asset is not None
            and str(asset["checksum"]) == CODE_BENCHMARK_MIGRATION_SHA256
            and str(asset["disposition"]) in _ALLOWED_DISPOSITIONS
            and all(
                expected.issubset(table_columns[name])
                and _columns_are_not_null(table_columns[name])
                and _schema_has_fragments(table_sql[name], _TABLE_SQL_FRAGMENTS[name])
                for name, expected in _TABLE_COLUMNS.items()
            )
            and _triggers_match_semantics(trigger_rows)
            and indexes_ready
            and foreign_keys == 1
            and foreign_keys_ready
        )
        if not ready:
            raise CodeBenchmarkLedgerRepositoryError(
                CODE_BENCHMARK_SCHEMA_NOT_READY,
                "Code benchmark ledger migration 0106 is missing or drifted",
            )

    def register_workload(
        self, registration: CodeBenchmarkWorkloadRegistration
    ) -> CodeBenchmarkWorkloadRegistration:
        if not isinstance(registration, CodeBenchmarkWorkloadRegistration):
            raise CodeBenchmarkLedgerRepositoryError(
                CODE_BENCHMARK_INVALID,
                "registration must be CodeBenchmarkWorkloadRegistration",
            )
        created_at_ms = self._timestamp()
        try:
            with self._transaction() as conn:
                row = conn.execute(
                    """
                    SELECT registration_id
                    FROM planning_search_code_benchmark_workload
                    WHERE registration_id = ?
                       OR registration_digest = ?
                       OR workload_id = ?
                       OR workload_digest = ?
                    ORDER BY created_at_ms, registration_id
                    LIMIT 1
                    """,
                    (
                        registration.registration_id,
                        registration.registration_digest,
                        registration.workload.workload_id,
                        registration.workload.workload_digest,
                    ),
                ).fetchone()
                if row is not None:
                    existing = self._registration(conn, str(row["registration_id"]))
                    if not _same_registration(existing, registration):
                        raise CodeBenchmarkLedgerRepositoryError(
                            CODE_BENCHMARK_REPLAY_DRIFT,
                            "workload registration identity already owns different evidence",
                        )
                    return existing
                conn.execute(
                    """
                    INSERT INTO planning_search_code_benchmark_workload(
                        registration_id, registration_digest, workload_id,
                        workload_digest, cohort_key, source_ref, source_commit,
                        path_policy_digest, execution_budget_json, registration_json,
                        mode, production_influence_enabled, created_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        registration.registration_id,
                        registration.registration_digest,
                        registration.workload.workload_id,
                        registration.workload.workload_digest,
                        registration.workload.cohort_key,
                        registration.source_ref,
                        registration.source_commit,
                        registration.path_policy_digest,
                        _canonical_json(registration.execution_budget.to_dict()),
                        _canonical_json(registration.to_dict()),
                        registration.mode,
                        0,
                        created_at_ms,
                    ),
                )
                return self._registration(conn, registration.registration_id)
        except CodeBenchmarkLedgerRepositoryError:
            raise
        except sqlite3.DatabaseError as exc:
            raise _store_error(exc) from exc

    def preregister_run(self, spec: CodeBenchmarkRunSpec) -> CodeBenchmarkRunSpec:
        if not isinstance(spec, CodeBenchmarkRunSpec):
            raise CodeBenchmarkLedgerRepositoryError(
                CODE_BENCHMARK_INVALID,
                "spec must be CodeBenchmarkRunSpec",
            )
        created_at_ms = self._timestamp()
        try:
            with self._transaction() as conn:
                registered = self._registration_or_none(
                    conn, spec.registration.registration_id
                )
                if registered is None:
                    raise CodeBenchmarkLedgerRepositoryError(
                        CODE_BENCHMARK_PREREGISTRATION_REQUIRED,
                        "workload must be durably registered before a run spec",
                    )
                if not _same_registration(registered, spec.registration):
                    raise CodeBenchmarkLedgerRepositoryError(
                        CODE_BENCHMARK_REPLAY_DRIFT,
                        "run spec registration differs from durable registration",
                    )
                row = conn.execute(
                    """
                    SELECT run_id
                    FROM planning_search_code_benchmark_run_spec
                    WHERE run_id = ? OR spec_digest = ?
                    ORDER BY created_at_ms, run_id
                    LIMIT 1
                    """,
                    (spec.run_id, spec.spec_digest),
                ).fetchone()
                if row is not None:
                    existing = self._spec(conn, str(row["run_id"]))
                    if not _same_spec(existing, spec):
                        raise CodeBenchmarkLedgerRepositoryError(
                            CODE_BENCHMARK_REPLAY_DRIFT,
                            "run id/spec identity already owns different preregistration",
                        )
                    return existing
                conn.execute(
                    """
                    INSERT INTO planning_search_code_benchmark_run_spec(
                        run_id, spec_digest, registration_id, registration_digest,
                        workload_digest, baseline_arm_id, selected_arm_id,
                        baseline_arm_digest, selected_arm_digest, spec_json, mode,
                        production_influence_enabled, created_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        spec.run_id,
                        spec.spec_digest,
                        spec.registration.registration_id,
                        spec.registration.registration_digest,
                        spec.registration.workload.workload_digest,
                        spec.baseline.arm_id,
                        spec.selected.arm_id,
                        spec.baseline_arm_digest,
                        spec.selected_arm_digest,
                        _canonical_json(spec.to_dict()),
                        spec.mode,
                        0,
                        created_at_ms,
                    ),
                )
                return self._spec(conn, spec.run_id)
        except CodeBenchmarkLedgerRepositoryError:
            raise
        except sqlite3.DatabaseError as exc:
            raise _store_error(exc) from exc

    def record_completed_run(self, receipt: CodeBenchmarkRunReceipt) -> CodeBenchmarkRunReceipt:
        if not isinstance(receipt, CodeBenchmarkRunReceipt):
            raise CodeBenchmarkLedgerRepositoryError(
                CODE_BENCHMARK_INVALID,
                "receipt must be CodeBenchmarkRunReceipt",
            )
        created_at_ms = self._timestamp()
        try:
            with self._transaction() as conn:
                preregistered = self._spec_or_none(conn, receipt.spec.run_id)
                if preregistered is None:
                    raise CodeBenchmarkLedgerRepositoryError(
                        CODE_BENCHMARK_PREREGISTRATION_REQUIRED,
                        "completed run requires a durable preregistered run spec",
                    )
                if not _same_spec(preregistered, receipt.spec):
                    raise CodeBenchmarkLedgerRepositoryError(
                        CODE_BENCHMARK_REPLAY_DRIFT,
                        "completed run differs from its durable preregistration",
                    )
                row = conn.execute(
                    """
                    SELECT run_id
                    FROM planning_search_code_benchmark_receipt
                    WHERE run_id = ? OR receipt_digest = ? OR run_digest = ?
                    ORDER BY created_at_ms, run_id
                    LIMIT 1
                    """,
                    (receipt.spec.run_id, receipt.receipt_digest, receipt.run.run_digest),
                ).fetchone()
                if row is not None:
                    existing = self._completed(conn, str(row["run_id"]))
                    if not _same_receipt(existing, receipt):
                        raise CodeBenchmarkLedgerRepositoryError(
                            CODE_BENCHMARK_REPLAY_DRIFT,
                            "terminal benchmark receipt already owns different evidence",
                        )
                    return existing
                self._insert_arm_execution(
                    conn,
                    run_id=receipt.spec.run_id,
                    role="BASELINE",
                    arm=receipt.spec.baseline,
                    evidence=receipt.run.baseline_evidence,
                    outcome=receipt.run.report.baseline_outcome,
                    created_at_ms=created_at_ms,
                )
                self._insert_arm_execution(
                    conn,
                    run_id=receipt.spec.run_id,
                    role="SELECTED",
                    arm=receipt.spec.selected,
                    evidence=receipt.run.selected_evidence,
                    outcome=receipt.run.report.selected_outcome,
                    created_at_ms=created_at_ms,
                )
                cleanup = receipt.run.cleanup_receipt
                assert cleanup is not None
                conn.execute(
                    """
                    INSERT INTO planning_search_code_benchmark_cleanup_receipt(
                        run_id, cleanup_digest, cleanup_status, cleanup_json, created_at_ms
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        receipt.spec.run_id,
                        cleanup.receipt_digest,
                        cleanup.status,
                        _canonical_json(cleanup.to_dict()),
                        created_at_ms,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO planning_search_code_benchmark_receipt(
                        run_id, receipt_digest, run_digest,
                        baseline_evidence_digest, selected_evidence_digest,
                        cleanup_digest, receipt_status, claim_scope,
                        production_influence_enabled, receipt_json, created_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        receipt.spec.run_id,
                        receipt.receipt_digest,
                        receipt.run.run_digest,
                        receipt.run.baseline_evidence.evidence_digest,
                        receipt.run.selected_evidence.evidence_digest,
                        cleanup.receipt_digest,
                        receipt.status,
                        receipt.claim_scope,
                        0,
                        _canonical_json(receipt.to_dict()),
                        created_at_ms,
                    ),
                )
                return self._completed(conn, receipt.spec.run_id)
        except CodeBenchmarkLedgerRepositoryError:
            raise
        except sqlite3.IntegrityError as exc:
            raise CodeBenchmarkLedgerRepositoryError(
                CODE_BENCHMARK_REPLAY_DRIFT,
                "immutable Code benchmark evidence conflicts with an existing row",
            ) from exc
        except sqlite3.DatabaseError as exc:
            raise _store_error(exc) from exc

    def get_completed(self, run_id: str) -> CodeBenchmarkRunReceipt | None:
        normalized = _run_id(run_id)
        try:
            with self._connection() as conn:
                return self._completed_or_none(conn, normalized)
        except CodeBenchmarkLedgerRepositoryError:
            raise
        except sqlite3.DatabaseError as exc:
            raise _store_error(exc) from exc

    def list_completed(
        self, *, cohort_key: str, limit: int = 256
    ) -> tuple[CodeBenchmarkRunReceipt, ...]:
        normalized_cohort = _text(cohort_key, "cohort_key", 240)
        bounded_limit = max(1, min(int(limit), 1_024))
        try:
            with self._connection() as conn:
                rows = conn.execute(
                    """
                    SELECT receipt.run_id
                    FROM planning_search_code_benchmark_receipt AS receipt
                    INNER JOIN planning_search_code_benchmark_run_spec AS spec
                        ON spec.run_id = receipt.run_id
                    INNER JOIN planning_search_code_benchmark_workload AS workload
                        ON workload.registration_id = spec.registration_id
                    WHERE workload.cohort_key = ?
                    ORDER BY receipt.created_at_ms, receipt.run_id
                    LIMIT ?
                    """,
                    (normalized_cohort, bounded_limit),
                ).fetchall()
                return tuple(self._completed(conn, str(row["run_id"])) for row in rows)
        except CodeBenchmarkLedgerRepositoryError:
            raise
        except sqlite3.DatabaseError as exc:
            raise _store_error(exc) from exc

    def _insert_arm_execution(
        self,
        conn: sqlite3.Connection,
        *,
        run_id: str,
        role: str,
        arm: BenchmarkArm,
        evidence: CodeBenchmarkArmEvidence,
        outcome: BenchmarkArmOutcome | None,
        created_at_ms: int,
    ) -> None:
        if outcome is None:
            raise CodeBenchmarkLedgerRepositoryError(
                CODE_BENCHMARK_INVALID,
                "terminal receipt cannot have a missing arm outcome",
            )
        conn.execute(
            """
            INSERT INTO planning_search_code_benchmark_arm_execution(
                run_id, arm_role, arm_id, arm_digest, evidence_digest,
                source_ref, source_commit, path_policy_digest, evidence_json,
                outcome_json, created_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                role,
                arm.arm_id,
                _digest(arm.to_dict()),
                evidence.evidence_digest,
                evidence.source_ref,
                evidence.source_commit,
                evidence.path_policy_digest,
                _canonical_json(evidence.to_dict()),
                _canonical_json(outcome.to_dict()),
                created_at_ms,
            ),
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

    def _registration_or_none(
        self, conn: sqlite3.Connection, registration_id: str
    ) -> CodeBenchmarkWorkloadRegistration | None:
        row = conn.execute(
            """
            SELECT registration_id
            FROM planning_search_code_benchmark_workload
            WHERE registration_id = ?
            """,
            (registration_id,),
        ).fetchone()
        return None if row is None else self._registration(conn, registration_id)

    def _registration(
        self, conn: sqlite3.Connection, registration_id: str
    ) -> CodeBenchmarkWorkloadRegistration:
        row = conn.execute(
            """
            SELECT * FROM planning_search_code_benchmark_workload
            WHERE registration_id = ?
            """,
            (registration_id,),
        ).fetchone()
        if row is None:
            raise CodeBenchmarkLedgerRepositoryError(
                CODE_BENCHMARK_NOT_FOUND,
                "Code benchmark workload registration does not exist",
            )
        try:
            payload = _parse_canonical_json(row["registration_json"], "registration_json")
            registration = _registration_from_payload(payload)
            if (
                registration.registration_id != str(row["registration_id"])
                or registration.registration_digest != str(row["registration_digest"])
                or registration.workload.workload_id != str(row["workload_id"])
                or registration.workload.workload_digest != str(row["workload_digest"])
                or registration.workload.cohort_key != str(row["cohort_key"])
                or registration.source_ref != str(row["source_ref"])
                or registration.source_commit != str(row["source_commit"])
                or registration.path_policy_digest != str(row["path_policy_digest"])
                or _canonical_json(registration.execution_budget.to_dict())
                != str(row["execution_budget_json"])
                or registration.mode != str(row["mode"])
                or int(row["production_influence_enabled"]) != 0
            ):
                raise CodeBenchmarkLedgerContractError("registration durable identity drift")
            return registration
        except (
            CodeBenchmarkLedgerContractError,
            TypeError,
            ValueError,
            KeyError,
            json.JSONDecodeError,
        ) as exc:
            raise CodeBenchmarkLedgerRepositoryError(
                CODE_BENCHMARK_DURABLE_DATA_INVALID,
                "stored Code benchmark registration is malformed or drifted",
            ) from exc

    def _spec_or_none(
        self, conn: sqlite3.Connection, run_id: str
    ) -> CodeBenchmarkRunSpec | None:
        row = conn.execute(
            "SELECT run_id FROM planning_search_code_benchmark_run_spec WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return None if row is None else self._spec(conn, run_id)

    def _spec(self, conn: sqlite3.Connection, run_id: str) -> CodeBenchmarkRunSpec:
        row = conn.execute(
            "SELECT * FROM planning_search_code_benchmark_run_spec WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise CodeBenchmarkLedgerRepositoryError(
                CODE_BENCHMARK_NOT_FOUND,
                "Code benchmark run spec does not exist",
            )
        try:
            registration = self._registration(conn, str(row["registration_id"]))
            payload = _parse_canonical_json(row["spec_json"], "spec_json")
            spec = _spec_from_payload(payload, registration)
            if (
                spec.run_id != str(row["run_id"])
                or spec.spec_digest != str(row["spec_digest"])
                or spec.registration.registration_digest != str(row["registration_digest"])
                or spec.registration.workload.workload_digest != str(row["workload_digest"])
                or spec.baseline.arm_id != str(row["baseline_arm_id"])
                or spec.selected.arm_id != str(row["selected_arm_id"])
                or spec.baseline_arm_digest != str(row["baseline_arm_digest"])
                or spec.selected_arm_digest != str(row["selected_arm_digest"])
                or spec.mode != str(row["mode"])
                or int(row["production_influence_enabled"]) != 0
            ):
                raise CodeBenchmarkLedgerContractError("run spec durable identity drift")
            return spec
        except CodeBenchmarkLedgerRepositoryError:
            raise
        except (
            CodeBenchmarkLedgerContractError,
            TypeError,
            ValueError,
            KeyError,
            json.JSONDecodeError,
        ) as exc:
            raise CodeBenchmarkLedgerRepositoryError(
                CODE_BENCHMARK_DURABLE_DATA_INVALID,
                "stored Code benchmark run spec is malformed or drifted",
            ) from exc

    def _completed_or_none(
        self, conn: sqlite3.Connection, run_id: str
    ) -> CodeBenchmarkRunReceipt | None:
        row = conn.execute(
            "SELECT run_id FROM planning_search_code_benchmark_receipt WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return None if row is None else self._completed(conn, run_id)

    def _completed(self, conn: sqlite3.Connection, run_id: str) -> CodeBenchmarkRunReceipt:
        row = conn.execute(
            """
            SELECT receipt.*, cleanup.cleanup_json AS cleanup_json,
                   cleanup.cleanup_status AS cleanup_status,
                   baseline.evidence_json AS baseline_evidence_json,
                   baseline.outcome_json AS baseline_outcome_json,
                   baseline.evidence_digest AS baseline_row_evidence_digest,
                   selected.evidence_json AS selected_evidence_json,
                   selected.outcome_json AS selected_outcome_json,
                   selected.evidence_digest AS selected_row_evidence_digest
            FROM planning_search_code_benchmark_receipt AS receipt
            INNER JOIN planning_search_code_benchmark_cleanup_receipt AS cleanup
                ON cleanup.run_id = receipt.run_id
            INNER JOIN planning_search_code_benchmark_arm_execution AS baseline
                ON baseline.run_id = receipt.run_id AND baseline.arm_role = 'BASELINE'
            INNER JOIN planning_search_code_benchmark_arm_execution AS selected
                ON selected.run_id = receipt.run_id AND selected.arm_role = 'SELECTED'
            WHERE receipt.run_id = ?
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            raise CodeBenchmarkLedgerRepositoryError(
                CODE_BENCHMARK_DURABLE_DATA_INVALID,
                "terminal receipt is incomplete or lacks cleanup-gated pair evidence",
            )
        try:
            spec = self._spec(conn, run_id)
            payload = _parse_canonical_json(row["receipt_json"], "receipt_json")
            run_payload = payload["run"]
            run = _run_from_payload(run_payload)
            receipt = CodeBenchmarkRunReceipt(
                spec=spec,
                run=run,
                status=payload.get("status"),
                claim_scope=payload.get("claim_scope"),
                production_influence_enabled=payload.get("production_influence_enabled"),
                receipt_ref=payload.get("receipt_ref", ""),
            )
            baseline_evidence = _evidence_from_payload(
                _parse_canonical_json(row["baseline_evidence_json"], "baseline_evidence_json")
            )
            selected_evidence = _evidence_from_payload(
                _parse_canonical_json(row["selected_evidence_json"], "selected_evidence_json")
            )
            baseline_outcome = _outcome_from_payload(
                _parse_canonical_json(row["baseline_outcome_json"], "baseline_outcome_json")
            )
            selected_outcome = _outcome_from_payload(
                _parse_canonical_json(row["selected_outcome_json"], "selected_outcome_json")
            )
            cleanup = _cleanup_from_payload(
                _parse_canonical_json(row["cleanup_json"], "cleanup_json")
            )
            if (
                receipt.receipt_digest != str(row["receipt_digest"])
                or receipt.run.run_digest != str(row["run_digest"])
                or receipt.run.baseline_evidence.evidence_digest
                != str(row["baseline_evidence_digest"])
                or receipt.run.selected_evidence.evidence_digest
                != str(row["selected_evidence_digest"])
                or receipt.run.cleanup_receipt is None
                or receipt.run.cleanup_receipt.receipt_digest != str(row["cleanup_digest"])
                or str(row["cleanup_status"]) != CODE_BENCHMARK_CLEANUP_STATUS
                or receipt.status != str(row["receipt_status"])
                or receipt.claim_scope != str(row["claim_scope"])
                or int(row["production_influence_enabled"]) != 0
                or baseline_evidence != receipt.run.baseline_evidence
                or selected_evidence != receipt.run.selected_evidence
                or baseline_outcome != receipt.run.report.baseline_outcome
                or selected_outcome != receipt.run.report.selected_outcome
                or cleanup != receipt.run.cleanup_receipt
                or baseline_evidence.evidence_digest
                != str(row["baseline_row_evidence_digest"])
                or selected_evidence.evidence_digest
                != str(row["selected_row_evidence_digest"])
            ):
                raise CodeBenchmarkLedgerContractError("terminal receipt durable identity drift")
            return receipt
        except CodeBenchmarkLedgerRepositoryError:
            raise
        except (
            CodeBenchmarkLedgerContractError,
            TypeError,
            ValueError,
            KeyError,
            json.JSONDecodeError,
        ) as exc:
            raise CodeBenchmarkLedgerRepositoryError(
                CODE_BENCHMARK_DURABLE_DATA_INVALID,
                "stored Code benchmark terminal receipt is malformed or drifted",
            ) from exc

    def _timestamp(self) -> int:
        try:
            value = int(self._clock())
        except (TypeError, ValueError) as exc:
            raise CodeBenchmarkLedgerRepositoryError(
                CODE_BENCHMARK_INVALID,
                "clock returned an invalid Code benchmark timestamp",
            ) from exc
        if value < 0:
            raise CodeBenchmarkLedgerRepositoryError(
                CODE_BENCHMARK_INVALID,
                "clock returned a negative Code benchmark timestamp",
            )
        return value


def _registration_from_payload(payload: Mapping[str, Any]) -> CodeBenchmarkWorkloadRegistration:
    if not isinstance(payload, Mapping):
        raise CodeBenchmarkLedgerContractError("registration payload must be an object")
    workload = _workload_from_payload(payload["workload"])
    budget = CodeBenchmarkExecutionBudget(**payload["execution_budget"])
    controlled_plan_payload = payload.get("controlled_verification_plan")
    controlled_plan = (
        None
        if controlled_plan_payload is None
        else CodeBenchmarkVerificationPlan.from_dict(controlled_plan_payload)
    )
    return CodeBenchmarkWorkloadRegistration(
        workload=workload,
        execution_budget=budget,
        controlled_verification_plan=controlled_plan,
        registration_id=payload.get("registration_id", ""),
        mode=payload.get("mode"),
        production_influence_enabled=payload.get("production_influence_enabled"),
    )


def _spec_from_payload(
    payload: Mapping[str, Any], registration: CodeBenchmarkWorkloadRegistration
) -> CodeBenchmarkRunSpec:
    if not isinstance(payload, Mapping):
        raise CodeBenchmarkLedgerContractError("run spec payload must be an object")
    if payload.get("registration_id") != registration.registration_id:
        raise CodeBenchmarkLedgerContractError("run spec registration id drift")
    if payload.get("registration_digest") != registration.registration_digest:
        raise CodeBenchmarkLedgerContractError("run spec registration digest drift")
    if payload.get("workload_digest") != registration.workload.workload_digest:
        raise CodeBenchmarkLedgerContractError("run spec workload digest drift")
    return CodeBenchmarkRunSpec(
        run_id=payload["run_id"],
        registration=registration,
        baseline=_arm_from_payload(payload["baseline"]),
        selected=_arm_from_payload(payload["selected"]),
        mode=payload.get("mode"),
        production_influence_enabled=payload.get("production_influence_enabled"),
    )


def _run_from_payload(payload: Mapping[str, Any]) -> CodeHistoricalBenchmarkRun:
    if not isinstance(payload, Mapping):
        raise CodeBenchmarkLedgerContractError("run payload must be an object")
    report_payload = payload["report"]
    if not isinstance(report_payload, Mapping):
        raise CodeBenchmarkLedgerContractError("report payload must be an object")
    report = DomainBenchmarkReport(
        workload=_workload_from_payload(report_payload["workload"]),
        baseline=_arm_from_payload(report_payload["baseline"]),
        selected=_arm_from_payload(report_payload["selected"]),
        baseline_outcome=(
            None
            if report_payload.get("baseline_outcome") is None
            else _outcome_from_payload(report_payload["baseline_outcome"])
        ),
        selected_outcome=(
            None
            if report_payload.get("selected_outcome") is None
            else _outcome_from_payload(report_payload["selected_outcome"])
        ),
        mode=report_payload.get("mode"),
        production_influence_enabled=report_payload.get("production_influence_enabled"),
    )
    cleanup_payload = payload.get("cleanup_receipt")
    return CodeHistoricalBenchmarkRun(
        run_id=payload["run_id"],
        report=report,
        baseline_evidence=_evidence_from_payload(payload["baseline_evidence"]),
        selected_evidence=_evidence_from_payload(payload["selected_evidence"]),
        cleanup_receipt=(
            None if cleanup_payload is None else _cleanup_from_payload(cleanup_payload)
        ),
        mode=payload.get("mode"),
        production_influence_enabled=payload.get("production_influence_enabled"),
    )


def _workload_from_payload(payload: Mapping[str, Any]) -> DomainBenchmarkWorkload:
    if not isinstance(payload, Mapping):
        raise CodeBenchmarkLedgerContractError("workload payload must be an object")
    return DomainBenchmarkWorkload(
        workload_id=payload["workload_id"],
        domain=payload["domain"],
        workload_ref=payload["workload_ref"],
        baseline_ref=payload["baseline_ref"],
        verifier_refs=tuple(payload["verifier_refs"]),
        isolation=payload["isolation"],
        budget=SearchBudget(**payload["budget"]),
        cohort_key=payload["cohort_key"],
        workload_digest=payload.get("workload_digest", ""),
    )


def _arm_from_payload(payload: Mapping[str, Any]) -> BenchmarkArm:
    if not isinstance(payload, Mapping):
        raise CodeBenchmarkLedgerContractError("arm payload must be an object")
    virtual = payload.get("virtual_state")
    if virtual is None:
        virtual = {}
    if not isinstance(virtual, Mapping):
        raise CodeBenchmarkLedgerContractError("arm virtual_state must be an object or null")
    return BenchmarkArm(
        arm_id=payload["arm_id"],
        selected_candidate_id=payload["selected_candidate_id"],
        selected_path_candidate_ids=tuple(payload["selected_path_candidate_ids"]),
        trace_digest=payload["trace_digest"],
        counters=payload["counters"],
        virtual_state_schema=virtual.get("schema", ""),
        virtual_state_digest=virtual.get("digest", ""),
    )


def _outcome_from_payload(payload: Mapping[str, Any]) -> BenchmarkArmOutcome:
    if not isinstance(payload, Mapping):
        raise CodeBenchmarkLedgerContractError("outcome payload must be an object")
    return BenchmarkArmOutcome(
        arm_id=payload["arm_id"],
        verifier_receipt_refs=tuple(payload["verifier_receipt_refs"]),
        success=payload.get("success"),
        latency_ms=payload.get("latency_ms"),
        cost=payload["cost"],
        workspace_diff_digest=payload.get("workspace_diff_digest") or "",
        executed=payload.get("executed", False),
    )


def _evidence_from_payload(payload: Mapping[str, Any]) -> CodeBenchmarkArmEvidence:
    if not isinstance(payload, Mapping):
        raise CodeBenchmarkLedgerContractError("evidence payload must be an object")
    verifier_payload = payload["verifier"]
    if not isinstance(verifier_payload, Mapping):
        raise CodeBenchmarkLedgerContractError("verifier payload must be an object")
    verifier = CodeVerifierResult(
        success=verifier_payload["success"],
        receipt_ref=verifier_payload["receipt_ref"],
        cost=verifier_payload["cost"],
        evidence_digest=verifier_payload["evidence_digest"],
        controlled_receipt=(
            None
            if verifier_payload.get("controlled_receipt") is None
            else CodeBenchmarkControlledVerificationReceipt.from_dict(
                verifier_payload["controlled_receipt"]
            )
        ),
    )
    return CodeBenchmarkArmEvidence(
        arm_id=payload["arm_id"],
        source_ref=payload["source_ref"],
        source_commit=payload["source_commit"],
        path_policy_digest=payload["path_policy_digest"],
        changed_file_count=payload["changed_file_count"],
        changed_path_digest=payload["changed_path_digest"],
        workspace_diff_digest=payload["workspace_diff_digest"],
        verifier=verifier,
        receipt_ref=payload.get("receipt_ref", ""),
    )


def _cleanup_from_payload(payload: Mapping[str, Any]) -> CodeBenchmarkCleanupReceipt:
    if not isinstance(payload, Mapping):
        raise CodeBenchmarkLedgerContractError("cleanup payload must be an object")
    return CodeBenchmarkCleanupReceipt(
        run_id=payload["run_id"],
        source_before_digest=payload["source_before_digest"],
        source_after_digest=payload["source_after_digest"],
        private_bare_removed=payload["private_bare_removed"],
        worktrees_removed=payload["worktrees_removed"],
        run_root_removed=payload["run_root_removed"],
        cleanup_adapter_revision=payload.get("cleanup_adapter_revision", ""),
        status=payload.get("status"),
        receipt_ref=payload.get("receipt_ref", ""),
    )


def _columns_are_not_null(columns: Mapping[str, sqlite3.Row]) -> bool:
    return bool(columns) and all(int(row["notnull"]) == 1 for row in columns.values())


def _triggers_match_semantics(rows: list[sqlite3.Row]) -> bool:
    by_name = {str(row["name"]): row for row in rows}
    for name, (table, fragments) in _REQUIRED_TRIGGERS.items():
        row = by_name.get(name)
        if row is None or str(row["tbl_name"]) != table:
            return False
        normalized = _normalize_sql(str(row["sql"] or ""))
        if not all(fragment in normalized for fragment in fragments):
            return False
    return True


def _foreign_keys_ready(conn: sqlite3.Connection) -> bool:
    expected = {
        "planning_search_code_benchmark_run_spec": (
            "planning_search_code_benchmark_workload",
            "registration_id",
            "registration_id",
        ),
        "planning_search_code_benchmark_arm_execution": (
            "planning_search_code_benchmark_run_spec",
            "run_id",
            "run_id",
        ),
        "planning_search_code_benchmark_cleanup_receipt": (
            "planning_search_code_benchmark_run_spec",
            "run_id",
            "run_id",
        ),
        "planning_search_code_benchmark_receipt": (
            "planning_search_code_benchmark_run_spec",
            "run_id",
            "run_id",
        ),
    }
    for table, (target, from_column, to_column) in expected.items():
        rows = conn.execute(f"PRAGMA foreign_key_list({_pragma_literal(table)})").fetchall()
        if not any(
            str(row["table"]) == target
            and str(row["from"]) == from_column
            and str(row["to"]) == to_column
            and str(row["on_delete"]).upper() == "RESTRICT"
            for row in rows
        ):
            return False
    return True


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _parse_canonical_json(value: Any, field_name: str) -> Any:
    raw = str(value)
    parsed = json.loads(raw)
    if _canonical_json(parsed) != raw:
        raise CodeBenchmarkLedgerContractError(f"{field_name} is not canonical JSON")
    return parsed


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _same_registration(
    left: CodeBenchmarkWorkloadRegistration,
    right: CodeBenchmarkWorkloadRegistration,
) -> bool:
    """Compare durable identities, not unrounded in-memory float fields."""

    return _canonical_json(left.to_dict()) == _canonical_json(right.to_dict())


def _same_spec(left: CodeBenchmarkRunSpec, right: CodeBenchmarkRunSpec) -> bool:
    return _canonical_json(left.to_dict()) == _canonical_json(right.to_dict())


def _same_receipt(left: CodeBenchmarkRunReceipt, right: CodeBenchmarkRunReceipt) -> bool:
    return _canonical_json(left.to_dict()) == _canonical_json(right.to_dict())


def _text(value: Any, field_name: str, limit: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > limit:
        raise CodeBenchmarkLedgerRepositoryError(
            CODE_BENCHMARK_INVALID,
            f"{field_name} must be non-empty and at most {limit} characters",
        )
    return text


def _run_id(value: Any) -> str:
    identifier = _text(value, "run_id", 120).lower()
    if not identifier or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_.-" for char in identifier):
        raise CodeBenchmarkLedgerRepositoryError(
            CODE_BENCHMARK_INVALID,
            "run_id must be a lowercase opaque identifier",
        )
    return identifier


def _store_error(exc: sqlite3.DatabaseError) -> CodeBenchmarkLedgerRepositoryError:
    detail = str(exc).lower()
    return CodeBenchmarkLedgerRepositoryError(
        CODE_BENCHMARK_STORE_UNAVAILABLE,
        "Code benchmark ledger SQLite operation failed"
        if "locked" not in detail and "busy" not in detail
        else "Code benchmark ledger SQLite store is busy",
    )


__all__ = [
    "CODE_BENCHMARK_DURABLE_DATA_INVALID",
    "CODE_BENCHMARK_INVALID",
    "CODE_BENCHMARK_MIGRATION_ASSET_KEY",
    "CODE_BENCHMARK_MIGRATION_SHA256",
    "CODE_BENCHMARK_NOT_FOUND",
    "CODE_BENCHMARK_PREREGISTRATION_REQUIRED",
    "CODE_BENCHMARK_REPLAY_DRIFT",
    "CODE_BENCHMARK_SCHEMA_NOT_READY",
    "CODE_BENCHMARK_STORE_UNAVAILABLE",
    "CodeBenchmarkLedgerRepositoryError",
    "SQLiteCodeHistoricalBenchmarkLedger",
]
