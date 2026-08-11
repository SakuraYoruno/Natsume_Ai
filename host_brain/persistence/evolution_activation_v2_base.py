"""SQLite transaction and immutable receipt kernel for NE-06."""

from __future__ import annotations

import base64
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable, Iterator, Mapping

from host_brain.contracts.evolution import (
    EvolutionTransitionReceiptV1,
    VersionActivationDecisionV1,
)
from host_brain.core.ids import now_ms
from host_brain.release.identity_snapshot import IdentitySnapshot

from .evolution_activation_v2_evidence import (
    EvolutionActivationEvidenceMixin,
    EvolutionActivationV2Error,
    bounded_text,
    canonical_json,
    hex_digest,
    payload_digest,
    sha256_hex,
)


EVOLUTION_ACTIVATION_V2_MIGRATION_ASSET_KEY = (
    "0142#evolution_activation_authority"
)
_REQUIRED_TABLES = {
    "evolution_policy_version_v2",
    "evolution_feedback_proposal_v2",
    "evolution_knowledge_entry_v2",
    "evolution_identity_snapshot_v2",
    "evolution_identity_regression_v2",
    "evolution_identity_plan_v2",
    "evolution_version_decision_v1",
    "evolution_activation_operation_v2",
    "evolution_policy_active_projection_v2",
    "evolution_knowledge_active_projection_v2",
    "evolution_identity_active_projection_v2",
    "evolution_transition_receipt_v1",
    "evolution_failure_evidence_v1",
    "evolution_knowledge_shadow_compare_v1",
}
_REQUIRED_TRIGGERS = {
    "evolution_policy_projection_v2_insert_guard",
    "evolution_policy_projection_v2_update_guard",
    "evolution_policy_projection_v2_delete_guard",
    "evolution_knowledge_projection_v2_insert_guard",
    "evolution_knowledge_projection_v2_update_guard",
    "evolution_knowledge_projection_v2_delete_guard",
    "evolution_identity_projection_v2_insert_guard",
    "evolution_identity_projection_v2_update_guard",
    "evolution_identity_projection_v2_delete_guard",
    "evolution_transition_receipt_v1_no_update",
    "evolution_feedback_proposal_v1_read_only",
    "evolution_knowledge_entry_v1_read_only",
    "evolution_identity_plan_v1_read_only",
}


class EvolutionActivationRepositoryBase(
    EvolutionActivationEvidenceMixin
):
    """Atomic policy, Knowledge and Identity transition owner."""

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
            raise EvolutionActivationV2Error(
                "EVOLUTION_ACTIVATION_V2_DURABLE_PATH_REQUIRED"
            )
        if not callable(clock):
            raise EvolutionActivationV2Error(
                "EVOLUTION_ACTIVATION_V2_CLOCK_INVALID"
            )
        self.db_path = Path(raw).expanduser().resolve()
        self._clock = clock
        self._timeout_ms = max(1, int(timeout_ms))
        if verify_schema:
            self.assert_schema_ready()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=self._timeout_ms / 1000.0,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {self._timeout_ms}")
        conn.create_function(
            "natsume_evolution_activation_write_authorized",
            0,
            lambda: 1,
            deterministic=True,
        )
        return conn

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def assert_schema_ready(self) -> None:
        if not self.db_path.is_file():
            self._invalid("EVOLUTION_ACTIVATION_V2_SCHEMA_NOT_READY")
        try:
            with self._connect() as conn:
                asset = conn.execute(
                    """
                    SELECT disposition
                    FROM schema_migration_assets
                    WHERE asset_key = ?
                    """,
                    (EVOLUTION_ACTIVATION_V2_MIGRATION_ASSET_KEY,),
                ).fetchone()
                tables = {
                    str(row[0])
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }
                triggers = {
                    str(row[0])
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'trigger'"
                    ).fetchall()
                }
        except sqlite3.Error as exc:
            raise EvolutionActivationV2Error(
                "EVOLUTION_ACTIVATION_V2_SCHEMA_DRIFT"
            ) from exc
        if (
            asset is None
            or str(asset[0])
            not in {"APPLIED", "APPLIED_WITH_COMPAT", "LEGACY_BACKFILL"}
            or not _REQUIRED_TABLES.issubset(tables)
            or not _REQUIRED_TRIGGERS.issubset(triggers)
        ):
            self._invalid("EVOLUTION_ACTIVATION_V2_SCHEMA_DRIFT")

    def active_policy(
        self,
        *,
        workspace_id: str,
        subject_id: str,
        policy_kind: str,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = self._active_policy(
                conn,
                workspace_id=workspace_id,
                subject_id=subject_id,
                policy_kind=policy_kind,
            )
        if row is None:
            return None
        return {
            "workspace_id": str(row["workspace_id"]),
            "subject_id": str(row["subject_id"]),
            "policy_kind": str(row["policy_kind"]),
            "active_version": str(row["active_version"]),
            "active_digest": str(row["active_digest"]),
            "payload": json.loads(str(row["payload_json"])),
            "activation_generation": int(row["activation_generation"]),
            "projection_digest": str(row["projection_digest"]),
        }

    def record_failure(
        self,
        *,
        workspace_id: str,
        subject_kind: str,
        subject_id: str,
        operation: str,
        reason_code: str,
        detail: str,
    ) -> str:
        timestamp = int(self._clock())
        payload = {
            "schema": "natsume.evolution-failure-evidence.v1",
            "workspace_id": bounded_text(
                workspace_id, "workspace_id", 128
            ),
            "subject_kind": bounded_text(
                subject_kind, "subject_kind", 32
            ).upper(),
            "subject_id_digest": sha256_hex(
                bounded_text(subject_id, "subject_id", 1_024)
            ),
            "operation": bounded_text(operation, "operation", 64).upper(),
            "reason_code": bounded_text(
                reason_code, "reason_code", 128
            ).upper(),
            "detail_digest": sha256_hex(str(detail or "")),
            "created_at_ms": timestamp,
        }
        failure_id = (
            "evolution-failure://sha256/" + payload_digest(payload)
        )
        encoded = canonical_json(payload)
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO evolution_failure_evidence_v1(
                    failure_id, workspace_id, subject_kind,
                    subject_id_digest, operation, reason_code,
                    detail_digest, evidence_json, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    failure_id,
                    payload["workspace_id"],
                    payload["subject_kind"],
                    payload["subject_id_digest"],
                    payload["operation"],
                    payload["reason_code"],
                    payload["detail_digest"],
                    encoded,
                    timestamp,
                ),
            )
        return failure_id

    def public_runtime_summary(
        self,
        *,
        workspace_id: str,
    ) -> dict[str, Any]:
        """Return bounded, body-free health counters for Obs projection."""

        workspace = bounded_text(
            workspace_id,
            "workspace_id",
            128,
        )
        with self._connect() as conn:
            counts = {
                "active_policy_count": int(
                    conn.execute(
                        """
                        SELECT COUNT(*)
                        FROM evolution_policy_active_projection_v2
                        WHERE workspace_id = ?
                        """,
                        (workspace,),
                    ).fetchone()[0]
                ),
                "active_knowledge_count": int(
                    conn.execute(
                        """
                        SELECT COUNT(*)
                        FROM evolution_knowledge_active_projection_v2
                        WHERE workspace_id = ?
                        """,
                        (workspace,),
                    ).fetchone()[0]
                ),
                "active_identity_count": int(
                    conn.execute(
                        """
                        SELECT COUNT(*)
                        FROM evolution_identity_active_projection_v2
                        WHERE workspace_id = ?
                        """,
                        (workspace,),
                    ).fetchone()[0]
                ),
                "transition_receipt_count": int(
                    conn.execute(
                        """
                        SELECT COUNT(*)
                        FROM evolution_transition_receipt_v1
                        WHERE workspace_id = ?
                        """,
                        (workspace,),
                    ).fetchone()[0]
                ),
                "failure_evidence_count": int(
                    conn.execute(
                        """
                        SELECT COUNT(*)
                        FROM evolution_failure_evidence_v1
                        WHERE workspace_id = ?
                        """,
                        (workspace,),
                    ).fetchone()[0]
                ),
            }
            latest = conn.execute(
                """
                SELECT reason_code, operation, created_at_ms
                FROM evolution_failure_evidence_v1
                WHERE workspace_id = ?
                ORDER BY created_at_ms DESC, failure_id DESC
                LIMIT 1
                """,
                (workspace,),
            ).fetchone()
        return {
            "schema": "natsume.evolution-runtime-summary.v1",
            "workspace_id": workspace,
            **counts,
            "latest_failure": (
                None
                if latest is None
                else {
                    "reason_code": str(latest["reason_code"]),
                    "operation": str(latest["operation"]),
                    "created_at_ms": int(latest["created_at_ms"]),
                }
            ),
        }

    def _append_terminal(
        self,
        conn: sqlite3.Connection,
        *,
        decision: VersionActivationDecisionV1,
        request: Mapping[str, Any],
        idempotency_key: str,
        receipt_id: str,
        event: str,
        generation: int,
        projection_digest: str,
    ) -> EvolutionTransitionReceiptV1:
        request_encoded = canonical_json(dict(request))
        request_digest = sha256_hex(request_encoded)
        key_digest = sha256_hex(
            bounded_text(idempotency_key, "idempotency_key", 512)
        )
        decision_encoded = canonical_json(decision.to_dict())
        conn.execute(
            """
            INSERT INTO evolution_version_decision_v1(
                decision_id, decision_digest, operation_id, workspace_id,
                subject_kind, subject_id, action, target_version,
                target_digest, previous_version, previous_digest,
                expected_generation, authority_refs_json, reason_code,
                decision_json, created_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision.decision_id,
                decision.decision_digest,
                decision.operation_id,
                decision.workspace_id,
                decision.subject_kind,
                decision.subject_id,
                decision.action,
                decision.target_version,
                decision.target_digest,
                decision.previous_version,
                decision.previous_digest,
                decision.expected_generation,
                canonical_json(list(decision.authority_refs)),
                decision.reason_code,
                decision_encoded,
                int(self._clock()),
            ),
        )
        conn.execute(
            """
            INSERT INTO evolution_activation_operation_v2(
                operation_id, idempotency_digest, request_digest,
                workspace_id, subject_kind, subject_id, action,
                decision_id, receipt_id, request_json, created_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision.operation_id,
                key_digest,
                request_digest,
                decision.workspace_id,
                decision.subject_kind,
                decision.subject_id,
                decision.action,
                decision.decision_id,
                receipt_id,
                request_encoded,
                int(self._clock()),
            ),
        )
        receipt = EvolutionTransitionReceiptV1(
            receipt_id=receipt_id,
            operation_id=decision.operation_id,
            decision_digest=decision.decision_digest,
            workspace_id=decision.workspace_id,
            subject_kind=decision.subject_kind,
            subject_id=decision.subject_id,
            event=event,
            target_version=decision.target_version,
            target_digest=decision.target_digest,
            previous_version=decision.previous_version,
            previous_digest=decision.previous_digest,
            active_generation=generation,
            projection_digest=projection_digest,
            created_at_ms=int(self._clock()),
        )
        conn.execute(
            """
            INSERT INTO evolution_transition_receipt_v1(
                receipt_id, receipt_digest, operation_id, decision_digest,
                workspace_id, subject_kind, subject_id, event,
                target_version, target_digest, previous_version,
                previous_digest, active_generation, projection_digest,
                receipt_json, created_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                receipt.receipt_id,
                receipt.receipt_digest,
                receipt.operation_id,
                receipt.decision_digest,
                receipt.workspace_id,
                receipt.subject_kind,
                receipt.subject_id,
                receipt.event,
                receipt.target_version,
                receipt.target_digest,
                receipt.previous_version,
                receipt.previous_digest,
                receipt.active_generation,
                receipt.projection_digest,
                canonical_json(receipt.to_dict()),
                receipt.created_at_ms,
            ),
        )
        return receipt

    def _replay(
        self,
        conn: sqlite3.Connection,
        idempotency_key: str,
        request: Mapping[str, Any],
    ) -> EvolutionTransitionReceiptV1 | None:
        key_digest = sha256_hex(
            bounded_text(idempotency_key, "idempotency_key", 512)
        )
        encoded = canonical_json(dict(request))
        row = conn.execute(
            """
            SELECT operation.request_digest, operation.request_json,
                   receipt.receipt_json
            FROM evolution_activation_operation_v2 AS operation
            JOIN evolution_transition_receipt_v1 AS receipt
              ON receipt.receipt_id = operation.receipt_id
            WHERE operation.idempotency_digest = ?
            """,
            (key_digest,),
        ).fetchone()
        if row is None:
            return None
        if (
            str(row["request_digest"]) != sha256_hex(encoded)
            or str(row["request_json"]) != encoded
        ):
            self._invalid("EVOLUTION_ACTIVATION_V2_REPLAY_DRIFT")
        payload = json.loads(str(row["receipt_json"]))
        payload.pop("schema", None)
        payload.pop("receipt_digest", None)
        return EvolutionTransitionReceiptV1(**payload)

    @staticmethod
    def _identities(idempotency_key: str) -> tuple[str, str, str]:
        digest = sha256_hex(
            bounded_text(idempotency_key, "idempotency_key", 512)
        )
        return (
            f"evolution-operation://sha256/{digest}",
            f"evolution-decision://sha256/{digest}",
            f"evolution-receipt://sha256/{digest}",
        )

    @staticmethod
    def _generation(
        row: sqlite3.Row | None, *, expected_generation: int
    ) -> int:
        expected = int(expected_generation)
        current = (
            0 if row is None else int(row["activation_generation"])
        )
        if expected < 0 or current != expected:
            raise EvolutionActivationV2Error(
                "EVOLUTION_ACTIVATION_V2_GENERATION_CONFLICT"
            )
        return current + 1

    @staticmethod
    def _policy_projection(
        *, row: sqlite3.Row, generation: int
    ) -> tuple[dict[str, Any], str]:
        projection = {
            "workspace_id": str(row["workspace_id"]),
            "subject_id": str(row["subject_id"]),
            "policy_kind": str(row["policy_kind"]),
            "active_version": str(row["version"]),
            "active_digest": str(row["version_digest"]),
            "payload": json.loads(str(row["payload_json"])),
            "activation_generation": int(generation),
        }
        return projection, payload_digest(projection)

    @staticmethod
    def _identity_projection(
        *,
        snapshot: sqlite3.Row,
        activation_version: int,
        generation: int,
    ) -> tuple[dict[str, Any], str]:
        projection = {
            "workspace_id": str(snapshot["workspace_id"]),
            "snapshot_ref": str(snapshot["snapshot_ref"]),
            "identity_digest": str(snapshot["identity_digest"]),
            "snapshot_hash": str(snapshot["snapshot_hash"]),
            "activation_version": int(activation_version),
            "activation_generation": int(generation),
        }
        return projection, payload_digest(projection)

    @staticmethod
    def _policy_digest_from_ref(value: str) -> str:
        prefix = "evolution-policy-version://sha256/"
        normalized = bounded_text(value, "rollback_ref", 1_024)
        if not normalized.startswith(prefix):
            raise EvolutionActivationV2Error(
                "EVOLUTION_FEEDBACK_ROLLBACK_REF_INVALID"
            )
        return hex_digest(
            normalized[len(prefix) :], "rollback_version_digest"
        )

    @staticmethod
    def _active_policy(
        conn: sqlite3.Connection,
        *,
        workspace_id: str,
        subject_id: str,
        policy_kind: str,
    ) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT *
            FROM evolution_policy_active_projection_v2
            WHERE workspace_id = ? AND subject_id = ? AND policy_kind = ?
            """,
            (
                bounded_text(workspace_id, "workspace_id", 128),
                bounded_text(subject_id, "subject_id", 255),
                bounded_text(policy_kind, "policy_kind", 64).upper(),
            ),
        ).fetchone()

    def _policy_version(
        self,
        conn: sqlite3.Connection,
        *,
        workspace_id: str,
        subject_id: str,
        policy_kind: str,
        version: str,
    ) -> sqlite3.Row:
        row = conn.execute(
            """
            SELECT *
            FROM evolution_policy_version_v2
            WHERE workspace_id = ? AND subject_id = ?
              AND policy_kind = ? AND version = ?
            """,
            (
                bounded_text(workspace_id, "workspace_id", 128),
                bounded_text(subject_id, "subject_id", 255),
                bounded_text(policy_kind, "policy_kind", 64).upper(),
                bounded_text(version, "version", 128),
            ),
        ).fetchone()
        if row is None:
            self._invalid("EVOLUTION_POLICY_VERSION_NOT_FOUND")
        return row

    def _knowledge_entry(
        self, conn: sqlite3.Connection, entry_id: str
    ) -> sqlite3.Row:
        row = conn.execute(
            """
            SELECT *
            FROM evolution_knowledge_entry_v2
            WHERE entry_id = ?
            """,
            (bounded_text(entry_id, "entry_id", 255),),
        ).fetchone()
        if row is None:
            self._invalid("EVOLUTION_KNOWLEDGE_ENTRY_V2_NOT_FOUND")
        return row

    @staticmethod
    def _active_knowledge(
        conn: sqlite3.Connection, entry: sqlite3.Row
    ) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT *
            FROM evolution_knowledge_active_projection_v2
            WHERE workspace_id = ? AND namespace = ? AND logical_key = ?
            """,
            (
                entry["workspace_id"],
                entry["namespace"],
                entry["logical_key"],
            ),
        ).fetchone()

    def _identity_snapshot(
        self,
        conn: sqlite3.Connection,
        snapshot_ref: str,
        workspace_id: str,
    ) -> sqlite3.Row:
        row = conn.execute(
            """
            SELECT *
            FROM evolution_identity_snapshot_v2
            WHERE snapshot_ref = ?
            """,
            (bounded_text(snapshot_ref, "snapshot_ref", 1_024),),
        ).fetchone()
        if row is None or str(row["workspace_id"]) != bounded_text(
            workspace_id, "workspace_id", 128
        ):
            self._invalid("EVOLUTION_IDENTITY_SNAPSHOT_V2_NOT_FOUND")
        try:
            IdentitySnapshot.from_mapping(
                json.loads(str(row["snapshot_json"]))
            )
        except Exception as exc:
            raise EvolutionActivationV2Error(
                "EVOLUTION_IDENTITY_SNAPSHOT_V2_DRIFT"
            ) from exc
        return row

    @staticmethod
    def _encode_cursor(*, scope_digest: str, last_key: str) -> str:
        raw = canonical_json(
            {
                "schema": "natsume.knowledge-cursor.v1",
                "scope_digest": scope_digest,
                "last_key": last_key,
            }
        ).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    def _decode_cursor(
        self, cursor: str, *, expected_scope_digest: str
    ) -> str:
        normalized = str(cursor or "").strip()
        if not normalized:
            return ""
        try:
            padded = normalized + "=" * (-len(normalized) % 4)
            payload = json.loads(
                base64.urlsafe_b64decode(padded).decode("utf-8")
            )
        except Exception as exc:
            raise EvolutionActivationV2Error(
                "EVOLUTION_KNOWLEDGE_CURSOR_INVALID"
            ) from exc
        if (
            not isinstance(payload, Mapping)
            or payload.get("schema") != "natsume.knowledge-cursor.v1"
            or payload.get("scope_digest") != expected_scope_digest
        ):
            self._invalid("EVOLUTION_KNOWLEDGE_CURSOR_SCOPE_DRIFT")
        return str(payload.get("last_key") or "")


__all__ = [
    "EVOLUTION_ACTIVATION_V2_MIGRATION_ASSET_KEY",
    "EvolutionActivationRepositoryBase",
]
