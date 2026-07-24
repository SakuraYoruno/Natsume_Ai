"""Durable owner for NE-05 Skill activation evidence and receipts."""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable, Iterator

from host_brain.contracts.skill_lifecycle import (
    SkillActivationEvidenceV1,
    SkillLifecycleError,
    SkillLifecycleReceiptV1,
)
from host_brain.core.ids import now_ms


SKILL_LIFECYCLE_MIGRATION_ASSET_KEY = "0109#skill_lifecycle"


class SkillLifecycleRepositoryError(RuntimeError):
    """The Skill lifecycle ledger failed closed."""


class SQLiteSkillLifecycleRepository:
    """Append-only SQLite owner for activation evidence and lifecycle receipts."""

    def __init__(self, db_path: str | Path, *, clock: Callable[[], int] = now_ms) -> None:
        raw = str(db_path or "").strip()
        if not raw or raw == ":memory:" or raw.lower().startswith("file:"):
            raise SkillLifecycleRepositoryError("SKILL_LIFECYCLE_DURABLE_PATH_REQUIRED")
        self.db_path = Path(raw).expanduser().resolve()
        self._clock = clock
        if not callable(clock):
            raise SkillLifecycleRepositoryError("SKILL_LIFECYCLE_CLOCK_INVALID")
        self.assert_schema_ready()

    def assert_schema_ready(self) -> None:
        if not self.db_path.is_file():
            raise SkillLifecycleRepositoryError("SKILL_LIFECYCLE_SCHEMA_UNAVAILABLE")
        with self._connection() as conn:
            try:
                asset = conn.execute(
                    "SELECT checksum, disposition FROM schema_migration_assets WHERE asset_key = ?",
                    (SKILL_LIFECYCLE_MIGRATION_ASSET_KEY,),
                ).fetchone()
            except sqlite3.Error as exc:
                raise SkillLifecycleRepositoryError("SKILL_LIFECYCLE_MIGRATION_DRIFT") from exc
            if asset is None or str(asset[1]) not in {"APPLIED", "APPLIED_WITH_COMPAT", "LEGACY_BACKFILL"}:
                raise SkillLifecycleRepositoryError("SKILL_LIFECYCLE_MIGRATION_DRIFT")
            for table, required in {
                "skill_activation_evidence": {
                    "activation_id", "evidence_digest", "bundle_id", "bundle_digest",
                    "bundle_version", "lab_report_ref", "review_ref", "sbom_digest",
                    "signature_audit_ref", "sandbox_receipt_ref", "rollback_ref",
                    "previous_bundle_id", "maturity_level", "new_capability", "reason_code",
                    "evidence_json", "created_at_ms",
                },
                "skill_lifecycle_receipt": {
                    "receipt_id", "receipt_digest", "activation_id", "event", "bundle_id",
                    "evidence_digest", "operation_id", "previous_bundle_id", "receipt_json",
                    "created_at_ms",
                },
            }.items():
                columns = {
                    str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
                }
                if columns != required:
                    raise SkillLifecycleRepositoryError("SKILL_LIFECYCLE_SCHEMA_DRIFT")
            triggers = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger'"
                ).fetchall()
            }
            required_triggers = {
                "skill_activation_evidence_no_update",
                "skill_activation_evidence_no_delete",
                "skill_lifecycle_receipt_no_update",
                "skill_lifecycle_receipt_no_delete",
            }
            if not required_triggers.issubset(triggers):
                raise SkillLifecycleRepositoryError("SKILL_LIFECYCLE_SCHEMA_DRIFT")

    def append_evidence(self, evidence: SkillActivationEvidenceV1) -> str:
        if not isinstance(evidence, SkillActivationEvidenceV1):
            raise SkillLifecycleRepositoryError("SKILL_LIFECYCLE_EVIDENCE_INVALID")
        encoded = _canonical(evidence.to_dict())
        with self._transaction() as conn:
            existing = conn.execute(
                "SELECT evidence_json FROM skill_activation_evidence WHERE activation_id = ?",
                (evidence.activation_id,),
            ).fetchone()
            if existing is not None:
                if str(existing[0]) != encoded:
                    raise SkillLifecycleRepositoryError("SKILL_LIFECYCLE_EVIDENCE_REPLAY_DRIFT")
                return evidence.evidence_digest
            try:
                conn.execute(
                    """
                    INSERT INTO skill_activation_evidence(
                        activation_id, evidence_digest, bundle_id, bundle_digest,
                        bundle_version, lab_report_ref, review_ref, sbom_digest,
                        signature_audit_ref, sandbox_receipt_ref, rollback_ref,
                        previous_bundle_id, maturity_level, new_capability,
                        reason_code, evidence_json, created_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        evidence.activation_id,
                        evidence.evidence_digest,
                        evidence.bundle_id,
                        evidence.bundle_digest,
                        evidence.bundle_version,
                        evidence.lab_report_ref,
                        evidence.review_ref,
                        evidence.sbom_digest,
                        evidence.signature_audit_ref,
                        evidence.sandbox_receipt_ref,
                        evidence.rollback_ref,
                        evidence.previous_bundle_id,
                        evidence.maturity_level,
                        int(evidence.new_capability),
                        evidence.reason_code,
                        encoded,
                        int(self._clock()),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise SkillLifecycleRepositoryError("SKILL_LIFECYCLE_EVIDENCE_CONFLICT") from exc
        return evidence.evidence_digest

    def get_evidence(self, *, activation_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT evidence_json FROM skill_activation_evidence WHERE activation_id = ?",
                (str(activation_id),),
            ).fetchone()
            return None if row is None else json.loads(str(row[0]))

    def latest_activated_evidence(
        self, *, bundle_id: str
    ) -> SkillActivationEvidenceV1 | None:
        """Return the exact evidence that previously activated one bundle.

        Rollback cannot reuse evidence from the version being replaced.  This
        query deliberately follows an immutable ``ACTIVATED`` receipt back to
        its immutable evidence row, so a staged target that has never passed
        its own approval cannot become active through rollback.
        """

        normalized = str(bundle_id or "").strip()
        if not normalized:
            raise SkillLifecycleRepositoryError(
                "SKILL_LIFECYCLE_BUNDLE_ID_INVALID"
            )
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT evidence.evidence_json
                FROM skill_lifecycle_receipt AS receipt
                INNER JOIN skill_activation_evidence AS evidence
                    ON evidence.activation_id = receipt.activation_id
                    AND evidence.evidence_digest = receipt.evidence_digest
                WHERE receipt.bundle_id = ? AND receipt.event = 'ACTIVATED'
                ORDER BY receipt.created_at_ms DESC, receipt.rowid DESC
                LIMIT 1
                """,
                (normalized,),
            ).fetchone()
        if row is None:
            return None
        try:
            raw = json.loads(str(row["evidence_json"]))
            if not isinstance(raw, dict):
                raise ValueError("evidence payload is not an object")
            return SkillActivationEvidenceV1(
                activation_id=raw.get("activation_id", ""),
                bundle_id=raw.get("bundle_id", ""),
                bundle_digest=raw.get("bundle_digest", ""),
                bundle_version=raw.get("bundle_version", ""),
                lab_report_ref=raw.get("lab_report_ref", ""),
                review_ref=raw.get("review_ref", ""),
                sbom_digest=raw.get("sbom_digest", ""),
                signature_audit_ref=raw.get("signature_audit_ref", ""),
                sandbox_receipt_ref=raw.get("sandbox_receipt_ref", ""),
                rollback_ref=raw.get("rollback_ref", ""),
                previous_bundle_id=raw.get("previous_bundle_id"),
                maturity_level=raw.get("maturity_level"),
                new_capability=raw.get("new_capability"),
                reason_code=raw.get("reason_code", ""),
                evidence_digest=raw.get("evidence_digest", ""),
            )
        except (TypeError, ValueError, SkillLifecycleError) as exc:
            raise SkillLifecycleRepositoryError(
                "SKILL_LIFECYCLE_ACTIVATION_EVIDENCE_CORRUPT"
            ) from exc

    def append_receipt(self, receipt: SkillLifecycleReceiptV1) -> str:
        if not isinstance(receipt, SkillLifecycleReceiptV1):
            raise SkillLifecycleRepositoryError("SKILL_LIFECYCLE_RECEIPT_INVALID")
        encoded = _canonical(receipt.to_dict())
        with self._transaction() as conn:
            existing = conn.execute(
                "SELECT receipt_json FROM skill_lifecycle_receipt WHERE receipt_id = ?",
                (receipt.receipt_id,),
            ).fetchone()
            if existing is not None:
                if str(existing[0]) != encoded:
                    raise SkillLifecycleRepositoryError("SKILL_LIFECYCLE_RECEIPT_REPLAY_DRIFT")
                return receipt.receipt_digest
            if conn.execute(
                "SELECT 1 FROM skill_activation_evidence WHERE activation_id = ? AND evidence_digest = ?",
                (receipt.activation_id, receipt.evidence_digest),
            ).fetchone() is None:
                raise SkillLifecycleRepositoryError("SKILL_LIFECYCLE_EVIDENCE_NOT_REGISTERED")
            try:
                conn.execute(
                    """
                    INSERT INTO skill_lifecycle_receipt(
                        receipt_id, receipt_digest, activation_id, event,
                        bundle_id, evidence_digest, operation_id, previous_bundle_id,
                        receipt_json, created_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        receipt.receipt_id,
                        receipt.receipt_digest,
                        receipt.activation_id,
                        receipt.event,
                        receipt.bundle_id,
                        receipt.evidence_digest,
                        receipt.operation_id,
                        receipt.previous_bundle_id,
                        encoded,
                        receipt.created_at_ms,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise SkillLifecycleRepositoryError("SKILL_LIFECYCLE_RECEIPT_CONFLICT") from exc
        return receipt.receipt_digest

    def list_receipts(
        self, *, bundle_id: str | None = None, activation_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        bounded = int(limit)
        if bounded < 1 or bounded > 500:
            raise SkillLifecycleRepositoryError("SKILL_LIFECYCLE_QUERY_LIMIT_INVALID")
        clauses: list[str] = []
        params: list[Any] = []
        if bundle_id is not None:
            clauses.append("bundle_id = ?")
            params.append(str(bundle_id))
        if activation_id is not None:
            clauses.append("activation_id = ?")
            params.append(str(activation_id))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connection() as conn:
            rows = conn.execute(
                f"SELECT receipt_json FROM skill_lifecycle_receipt{where} ORDER BY rowid ASC LIMIT ?",
                (*params, bounded),
            ).fetchall()
            return [json.loads(str(row[0])) for row in rows]

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


__all__ = [
    "SKILL_LIFECYCLE_MIGRATION_ASSET_KEY",
    "SQLiteSkillLifecycleRepository",
    "SkillLifecycleRepositoryError",
]
