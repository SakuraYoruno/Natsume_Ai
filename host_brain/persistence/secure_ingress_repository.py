"""SQLite ports for Secure Ingress using catalog-owned schema 0058.

The store never creates or mutates schema at runtime.  Construction verifies
that migration 0058 has already been applied and fails closed otherwise.
"""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import threading
from typing import Any
from urllib.parse import quote
import uuid

from natsume_contracts.identity import CredentialProofKind, PrincipalKind

from host_brain.secure_ingress.models import (
    ClaimDecision,
    ClaimStatus,
    IngressAuditEvent,
    PrincipalAuthority,
    RateDecision,
    digest_identifier,
)
from host_brain.secure_ingress.audit_safety import assert_audit_event_safe


_REQUIRED_SCHEMA: dict[str, frozenset[str]] = {
    "secure_ingress_principal": frozenset(
        {
            "principal_id",
            "principal_kind",
            "tenant_id",
            "status",
            "version",
            "granted_scopes_json",
            "allowed_audiences_json",
            "allowed_actions_json",
            "valid_from_ms",
            "valid_until_ms",
            "updated_at_ms",
        }
    ),
    "secure_ingress_credential": frozenset(
        {
            "principal_id",
            "key_id",
            "credential_kind",
            "algorithm",
            "secret_ref",
            "status",
            "version",
            "valid_from_ms",
            "valid_until_ms",
            "updated_at_ms",
        }
    ),
    "secure_ingress_rate_event": frozenset(
        {"rate_key_digest", "principal_id", "action", "required_scopes_json", "rate_lane", "ts_ms"}
    ),
    "secure_ingress_nonce_claim": frozenset(
        {"principal_id", "nonce_digest", "request_id", "body_sha256", "claim_ref", "claimed_at_ms", "expires_at_ms"}
    ),
    "secure_ingress_idempotency_claim": frozenset(
        {
            "principal_id",
            "surface",
            "idempotency_digest",
            "action",
            "required_scopes_json",
            "request_id",
            "body_sha256",
            "claim_ref",
            "result_ref",
            "claim_status",
            "provisional_expires_at_ms",
            "claimed_at_ms",
            "updated_at_ms",
            "expires_at_ms",
        }
    ),
    "secure_ingress_audit": frozenset(
        {
            "audit_ref",
            "request_id",
            "surface",
            "principal_id",
            "body_sha256",
            "decision",
            "reason_code",
            "stage",
            "transport_key_digest",
            "side_effects_executed",
            "ts_ms",
        }
    ),
}

SECURE_INGRESS_MIGRATION_ASSET_KEY = "0058#secure_ingress"
SECURE_INGRESS_MIGRATION_SHA256 = "e5b8790b6ac9058fb48ef0a2af9aa162985985eb4ee2eeab61788b233047b95b"
_ALLOWED_MIGRATION_DISPOSITIONS = frozenset(
    {"APPLIED", "APPLIED_WITH_COMPAT", "LEGACY_BACKFILL"}
)
_REQUIRED_INDEXES = frozenset(
    {
        "idx_secure_ingress_credential_status",
        "idx_secure_ingress_rate_window",
        "idx_secure_ingress_nonce_expiry",
        "idx_secure_ingress_idempotency_expiry",
        "idx_secure_ingress_audit_request",
        "idx_secure_ingress_audit_principal",
        "idx_secure_ingress_audit_reason",
    }
)


class SecureIngressStoreError(RuntimeError):
    pass


class SQLiteSecureIngressStore:
    def __init__(
        self,
        db_path: str | Path,
        *,
        rate_limit: int = 30,
        rate_window_ms: int = 60_000,
        attempt_rate_limit: int = 120,
        attempt_window_ms: int = 60_000,
        provisional_ttl_ms: int = 30_000,
    ) -> None:
        self._db_path = str(db_path)
        if self._db_path != ":memory:" and not Path(self._db_path).is_file():
            raise SecureIngressStoreError("SECURE_INGRESS_SCHEMA_UNAVAILABLE")
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=3000")
        self._lock = threading.RLock()
        self._rate_limit = max(1, int(rate_limit))
        self._rate_window_ms = max(1, int(rate_window_ms))
        self._attempt_rate_limit = max(1, int(attempt_rate_limit))
        self._attempt_window_ms = max(1, int(attempt_window_ms))
        self._provisional_ttl_ms = max(1, int(provisional_ttl_ms))
        try:
            self._verify_schema()
        except SecureIngressStoreError:
            raise
        except Exception as exc:
            self._conn.close()
            raise SecureIngressStoreError(
                "SECURE_INGRESS_SCHEMA_UNAVAILABLE"
            ) from exc

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _verify_schema(self) -> None:
        with self._lock:
            asset = self._conn.execute(
                """
                SELECT checksum, disposition
                FROM schema_migration_assets
                WHERE asset_key = ?
                """,
                (SECURE_INGRESS_MIGRATION_ASSET_KEY,),
            ).fetchone()
            if (
                asset is None
                or str(asset["checksum"]) != SECURE_INGRESS_MIGRATION_SHA256
                or str(asset["disposition"]) not in _ALLOWED_MIGRATION_DISPOSITIONS
            ):
                self._conn.close()
                raise SecureIngressStoreError("SECURE_INGRESS_MIGRATION_DRIFT")
            for table, required_columns in _REQUIRED_SCHEMA.items():
                rows = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
                columns = {str(row["name"]) for row in rows}
                if not required_columns.issubset(columns):
                    self._conn.close()
                    raise SecureIngressStoreError(
                        f"SECURE_INGRESS_SCHEMA_UNAVAILABLE:{table}"
                    )
            if self._primary_key("secure_ingress_nonce_claim") != (
                "principal_id",
                "nonce_digest",
            ):
                self._schema_drift("nonce primary key")
            if self._primary_key("secure_ingress_idempotency_claim") != (
                "principal_id",
                "surface",
                "idempotency_digest",
            ):
                self._schema_drift("idempotency primary key")
            if not self._has_unique_index(
                "secure_ingress_idempotency_claim",
                ("claim_ref",),
            ):
                self._schema_drift("claim_ref unique index")
            foreign_keys = self._conn.execute(
                "PRAGMA foreign_key_list(secure_ingress_credential)"
            ).fetchall()
            if not any(
                str(row["table"]) == "secure_ingress_principal"
                and str(row["from"]) == "principal_id"
                and str(row["to"]) == "principal_id"
                for row in foreign_keys
            ):
                self._schema_drift("credential principal foreign key")
            audit_sql = self._table_sql("secure_ingress_audit")
            claim_sql = self._table_sql("secure_ingress_idempotency_claim")
            if "CHECK(SIDE_EFFECTS_EXECUTED = 0)" not in audit_sql:
                self._schema_drift("audit side-effect check")
            if "PROVISIONAL" not in claim_sql or "COMMITTED" not in claim_sql or "ABORTED" not in claim_sql:
                self._schema_drift("claim status check")
            indexes = {
                str(row["name"])
                for row in self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index'"
                ).fetchall()
            }
            if not _REQUIRED_INDEXES.issubset(indexes):
                self._schema_drift("required indexes")

    def _schema_drift(self, detail: str) -> None:
        self._conn.close()
        raise SecureIngressStoreError(f"SECURE_INGRESS_SCHEMA_DRIFT:{detail}")

    def _primary_key(self, table: str) -> tuple[str, ...]:
        rows = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        keyed = sorted(
            ((int(row["pk"]), str(row["name"])) for row in rows if int(row["pk"]) > 0),
            key=lambda item: item[0],
        )
        return tuple(name for _, name in keyed)

    def _has_unique_index(self, table: str, columns: tuple[str, ...]) -> bool:
        for row in self._conn.execute(f"PRAGMA index_list({table})").fetchall():
            if int(row["unique"]) != 1:
                continue
            name = str(row["name"])
            indexed = tuple(
                str(item["name"])
                for item in self._conn.execute(f"PRAGMA index_info({name})").fetchall()
            )
            if indexed == columns:
                return True
        return False

    def _table_sql(self, table: str) -> str:
        row = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        return " ".join(str(row["sql"] if row else "").upper().split())

    def resolve(
        self,
        *,
        principal_id: str,
        key_id: str,
        at_ms: int,
    ) -> PrincipalAuthority | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT p.principal_id, p.principal_kind, p.tenant_id,
                       p.status AS principal_status, p.version AS principal_version,
                       p.granted_scopes_json, p.allowed_audiences_json,
                       p.allowed_actions_json, p.valid_from_ms AS principal_valid_from,
                       p.valid_until_ms AS principal_valid_until,
                       c.key_id, c.credential_kind, c.algorithm,
                       c.status AS credential_status, c.version AS credential_version,
                       c.valid_from_ms AS credential_valid_from,
                       c.valid_until_ms AS credential_valid_until
                FROM secure_ingress_principal AS p
                JOIN secure_ingress_credential AS c
                  ON c.principal_id = p.principal_id
                WHERE p.principal_id = ? AND c.key_id = ?
                LIMIT 1
                """,
                (str(principal_id), str(key_id)),
            ).fetchone()
        if row is None:
            return None
        try:
            if row["principal_status"] != "ACTIVE" or row["credential_status"] != "ACTIVE":
                return None
            if not self._within_validity(
                at_ms,
                int(row["principal_valid_from"]),
                row["principal_valid_until"],
            ) or not self._within_validity(
                at_ms,
                int(row["credential_valid_from"]),
                row["credential_valid_until"],
            ):
                return None
            return PrincipalAuthority(
                principal_id=str(row["principal_id"]),
                kind=PrincipalKind(str(row["principal_kind"])),
                tenant_id=str(row["tenant_id"]),
                key_id=str(row["key_id"]),
                credential_kind=CredentialProofKind(str(row["credential_kind"])),
                algorithm=str(row["algorithm"]),
                granted_scopes=self._string_tuple(row["granted_scopes_json"]),
                allowed_audiences=self._string_tuple(row["allowed_audiences_json"]),
                allowed_actions=self._string_tuple(row["allowed_actions_json"]),
                version=min(int(row["principal_version"]), int(row["credential_version"])),
                enabled=True,
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    def consume(
        self,
        *,
        principal_id: str,
        action: str,
        required_scopes: tuple[str, ...],
        at_ms: int,
    ) -> RateDecision:
        scopes_json = self._json_array(required_scopes)
        rate_key = digest_identifier(f"{principal_id}\n{action}\n{scopes_json}")
        assert rate_key is not None
        return self._consume_rate(
            rate_key=rate_key,
            principal_id=principal_id,
            action=action,
            scopes_json=scopes_json,
            at_ms=at_ms,
            limit=self._rate_limit,
            window_ms=self._rate_window_ms,
            lane="AUTHORITY",
        )

    def consume_attempt(
        self,
        *,
        transport_key: str,
        claimed_principal_id: str | None,
        key_id: str | None,
        at_ms: int,
    ) -> RateDecision:
        transport_digest = digest_identifier(transport_key)
        claimed_digest = digest_identifier(claimed_principal_id)
        key_digest = digest_identifier(key_id)
        if transport_digest is None:
            raise SecureIngressStoreError("SECURE_INGRESS_ATTEMPT_KEY_INVALID")
        global_rate_key = digest_identifier(f"{transport_digest}\nglobal")
        claimed_rate_key = digest_identifier(
            f"{transport_digest}\n{claimed_digest or 'anonymous'}\n{key_digest or 'no-key'}"
        )
        assert global_rate_key is not None and claimed_rate_key is not None
        global_decision = self._consume_rate(
            rate_key=global_rate_key,
            principal_id="transport-only",
            action="secure-ingress:attempt",
            scopes_json="[]",
            at_ms=at_ms,
            limit=self._attempt_rate_limit,
            window_ms=self._attempt_window_ms,
            lane="ATTEMPT",
        )
        claimed_decision = self._consume_rate(
            rate_key=claimed_rate_key,
            principal_id=claimed_digest or "anonymous",
            action="secure-ingress:attempt",
            scopes_json="[]",
            at_ms=at_ms,
            limit=self._attempt_rate_limit,
            window_ms=self._attempt_window_ms,
            lane="ATTEMPT",
        )
        return RateDecision(
            allowed=global_decision.allowed and claimed_decision.allowed,
            current_count=max(
                global_decision.current_count,
                claimed_decision.current_count,
            ),
            limit=self._attempt_rate_limit,
            window_ms=self._attempt_window_ms,
            backend="sqlite",
            available=True,
            lane="ATTEMPT",
        )

    def _consume_rate(
        self,
        *,
        rate_key: str,
        principal_id: str,
        action: str,
        scopes_json: str,
        at_ms: int,
        limit: int,
        window_ms: int,
        lane: str,
    ) -> RateDecision:
        cutoff = int(at_ms) - int(window_ms)
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                # A committed claim is durable command authority, not a
                # short-lived authentication cache.  HostCommand and
                # Scheduler occurrence replay may need its canonical binding
                # long after the credential/provisional lease expires.  Only
                # abandoned, aborted, or provisional claims are collectible;
                # committed idempotency keys remain single-use.
                self._conn.execute(
                    "DELETE FROM secure_ingress_rate_event WHERE rate_key_digest = ? AND ts_ms <= ?",
                    (rate_key, cutoff),
                )
                self._conn.execute(
                    """
                    INSERT INTO secure_ingress_rate_event(
                        rate_key_digest, principal_id, action, required_scopes_json,
                        rate_lane, ts_ms
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (rate_key, principal_id, action, scopes_json, lane, int(at_ms)),
                )
                row = self._conn.execute(
                    "SELECT COUNT(*) AS count FROM secure_ingress_rate_event WHERE rate_key_digest = ? AND ts_ms > ?",
                    (rate_key, cutoff),
                ).fetchone()
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        count = int(row["count"]) if row is not None else int(limit) + 1
        return RateDecision(
            allowed=count <= int(limit),
            current_count=count,
            limit=int(limit),
            window_ms=int(window_ms),
            backend="sqlite",
            available=True,
            lane=lane,
        )

    def try_claim(
        self,
        *,
        principal_id: str,
        surface: str,
        action: str,
        required_scopes: tuple[str, ...],
        idempotency_key: str,
        nonce: str,
        body_sha256: str,
        request_id: str,
        at_ms: int,
        expires_at_ms: int,
    ) -> ClaimDecision:
        idempotency_digest = digest_identifier(idempotency_key)
        nonce_digest = digest_identifier(nonce)
        if idempotency_digest is None or nonce_digest is None or int(expires_at_ms) <= int(at_ms):
            raise SecureIngressStoreError("SECURE_INGRESS_CLAIM_INVALID")
        scopes_json = self._json_array(required_scopes)
        claim_ref = f"secure-ingress-claim://{uuid.uuid4().hex}"
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._conn.execute(
                    """
                    DELETE FROM secure_ingress_nonce_claim
                    WHERE claim_ref IN (
                        SELECT claim_ref FROM secure_ingress_idempotency_claim
                        WHERE (claim_status != 'COMMITTED' AND expires_at_ms <= ?)
                           OR (claim_status = 'PROVISIONAL' AND provisional_expires_at_ms <= ?)
                    )
                    """,
                    (int(at_ms), int(at_ms)),
                )
                self._conn.execute(
                    """
                    DELETE FROM secure_ingress_idempotency_claim
                    WHERE (claim_status != 'COMMITTED' AND expires_at_ms <= ?)
                       OR (claim_status = 'PROVISIONAL' AND provisional_expires_at_ms <= ?)
                    """,
                    (int(at_ms), int(at_ms)),
                )
                existing = self._conn.execute(
                    """
                    SELECT claim.action, claim.required_scopes_json,
                           claim.request_id, claim.body_sha256, claim.claim_ref,
                           claim.result_ref, claim.claim_status,
                           (
                               SELECT audit.audit_ref
                               FROM secure_ingress_audit AS audit
                               WHERE audit.claim_ref = claim.claim_ref
                                 AND audit.decision = 'ALLOW'
                                 AND audit.reason_code = 'SECURE_INGRESS_ALLOWED'
                               ORDER BY audit.ts_ms, audit.audit_ref
                               LIMIT 1
                           ) AS canonical_audit_ref
                    FROM secure_ingress_idempotency_claim AS claim
                    WHERE claim.principal_id = ?
                      AND claim.surface = ?
                      AND claim.idempotency_digest = ?
                    """,
                    (principal_id, surface, idempotency_digest),
                ).fetchone()
                if existing is not None:
                    if str(existing["claim_status"]) == "ABORTED":
                        self._conn.execute(
                            "DELETE FROM secure_ingress_nonce_claim WHERE claim_ref = ?",
                            (str(existing["claim_ref"]),),
                        )
                        self._conn.execute(
                            "DELETE FROM secure_ingress_idempotency_claim WHERE claim_ref = ?",
                            (str(existing["claim_ref"]),),
                        )
                        existing = None
                if existing is not None:
                    same = (
                        str(existing["action"]) == action
                        and str(existing["required_scopes_json"]) == scopes_json
                        and str(existing["body_sha256"]) == body_sha256
                    )
                    self._conn.commit()
                    duplicate = ClaimStatus.DUPLICATE_SAME if same else ClaimStatus.IDEMPOTENCY_CONFLICT
                    return ClaimDecision(
                        duplicate,
                        str(existing["claim_ref"]),
                        str(existing["result_ref"]) if existing["result_ref"] else None,
                        (
                            str(existing["request_id"])
                            if same and existing["request_id"]
                            else None
                        ),
                        (
                            str(existing["body_sha256"])
                            if same and existing["body_sha256"]
                            else None
                        ),
                        (
                            str(existing["canonical_audit_ref"])
                            if same and existing["canonical_audit_ref"]
                            else None
                        ),
                    )
                replay = self._conn.execute(
                    "SELECT claim_ref FROM secure_ingress_nonce_claim WHERE principal_id = ? AND nonce_digest = ?",
                    (principal_id, nonce_digest),
                ).fetchone()
                if replay is not None:
                    self._conn.commit()
                    return ClaimDecision(ClaimStatus.REPLAY, str(replay["claim_ref"]))
                self._conn.execute(
                    """
                    INSERT INTO secure_ingress_nonce_claim(
                        principal_id, nonce_digest, request_id, body_sha256,
                        claim_ref, claimed_at_ms, expires_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        principal_id,
                        nonce_digest,
                        request_id,
                        body_sha256,
                        claim_ref,
                        int(at_ms),
                        int(expires_at_ms),
                    ),
                )
                self._conn.execute(
                    """
                    INSERT INTO secure_ingress_idempotency_claim(
                        principal_id, surface, idempotency_digest, action,
                        required_scopes_json, request_id, body_sha256, claim_ref,
                        result_ref, claim_status, provisional_expires_at_ms,
                        claimed_at_ms, updated_at_ms, expires_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 'PROVISIONAL', ?, ?, ?, ?)
                    """,
                    (
                        principal_id,
                        surface,
                        idempotency_digest,
                        action,
                        scopes_json,
                        request_id,
                        body_sha256,
                        claim_ref,
                        min(int(expires_at_ms), int(at_ms) + self._provisional_ttl_ms),
                        int(at_ms),
                        int(at_ms),
                        int(expires_at_ms),
                    ),
                )
                self._conn.commit()
                return ClaimDecision(ClaimStatus.NEW, claim_ref)
            except Exception:
                self._conn.rollback()
                raise

    def commit_with_audit(
        self,
        *,
        claim_ref: str,
        event: IngressAuditEvent,
    ) -> str:
        if (
            event.claim_ref != claim_ref
            or event.decision != "ALLOW"
            or event.reason_code != "SECURE_INGRESS_ALLOWED"
            or event.side_effects_executed
        ):
            raise SecureIngressStoreError("SECURE_INGRESS_ALLOW_AUDIT_MISMATCH")
        audit_ref = f"secure-ingress-audit://{uuid.uuid4().hex}"
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                row = self._conn.execute(
                    "SELECT claim_status FROM secure_ingress_idempotency_claim WHERE claim_ref = ?",
                    (claim_ref,),
                ).fetchone()
                if row is None or str(row["claim_status"]) != "PROVISIONAL":
                    raise SecureIngressStoreError("SECURE_INGRESS_CLAIM_NOT_PROVISIONAL")
                self._conn.execute(
                    """
                    UPDATE secure_ingress_idempotency_claim
                    SET claim_status = 'COMMITTED', provisional_expires_at_ms = NULL,
                        updated_at_ms = ?
                    WHERE claim_ref = ? AND claim_status = 'PROVISIONAL'
                    """,
                    (int(event.ts_ms), claim_ref),
                )
                self._insert_audit(self._conn, audit_ref=audit_ref, event=event)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return audit_ref

    def abort_claim(self, *, claim_ref: str) -> None:
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._conn.execute(
                    "DELETE FROM secure_ingress_nonce_claim WHERE claim_ref = ?",
                    (claim_ref,),
                )
                self._conn.execute(
                    """
                    UPDATE secure_ingress_idempotency_claim
                    SET claim_status = 'ABORTED', provisional_expires_at_ms = NULL,
                        updated_at_ms = claimed_at_ms
                    WHERE claim_ref = ? AND claim_status = 'PROVISIONAL'
                    """,
                    (claim_ref,),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def complete_claim(self, *, claim_ref: str, result_ref: str, at_ms: int | None = None) -> None:
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                row = self._conn.execute(
                    "SELECT result_ref, claim_status, updated_at_ms FROM secure_ingress_idempotency_claim WHERE claim_ref = ?",
                    (claim_ref,),
                ).fetchone()
                if row is None or str(row["claim_status"]) != "COMMITTED":
                    raise SecureIngressStoreError("SECURE_INGRESS_CLAIM_NOT_COMMITTED")
                if row["result_ref"] not in (None, result_ref):
                    raise SecureIngressStoreError("SECURE_INGRESS_RESULT_CONFLICT")
                updated_at = int(row["updated_at_ms"]) if at_ms is None else int(at_ms)
                self._conn.execute(
                    """
                    UPDATE secure_ingress_idempotency_claim
                    SET result_ref = ?, updated_at_ms = ?
                    WHERE claim_ref = ?
                    """,
                    (result_ref, updated_at, claim_ref),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def complete_review_decision_claim(
        self,
        *,
        claim_ref: str,
        result_ref: str,
        prior_result_ref: str | None,
        review_id: str,
        review_status: str,
        review_status_version: int,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        at_ms: int | None = None,
    ) -> None:
        """Complete or strictly repair a historical POST Review claim.

        Releases before 0096 could bind Secure Ingress to the worker receipt
        while the command was still waiting for acceptance.  Replacement is
        legal only when the exact HostCommand, terminal Review decision,
        graph/node lineage and currently stored pre-acceptance receipt all
        match in one writer transaction.
        """

        from host_brain.persistence.review_continuation_store import (  # noqa: PLC0415
            parse_task_review_subject,
        )

        normalized_status = str(review_status or "").strip().upper()
        normalized_review_id = str(review_id or "").strip()
        normalized_graph_id = str(graph_id or "").strip()
        normalized_node_id = str(node_id or "").strip()
        try:
            revision = int(graph_revision)
            status_version = int(review_status_version)
        except (TypeError, ValueError) as exc:
            raise SecureIngressStoreError(
                "SECURE_INGRESS_REVIEW_DECISION_INVALID"
            ) from exc
        expected_ref = (
            f"review-decision://{quote(normalized_review_id, safe='')}/"
            f"{normalized_status.lower()}/{status_version}"
        )
        if (
            normalized_status not in {"REJECTED", "EXPIRED"}
            or not normalized_review_id
            or not normalized_graph_id
            or not normalized_node_id
            or revision < 1
            or status_version < 1
            or str(result_ref or "").strip() != expected_ref
        ):
            raise SecureIngressStoreError(
                "SECURE_INGRESS_REVIEW_DECISION_INVALID"
            )

        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                claim = self._conn.execute(
                    """
                    SELECT result_ref, claim_status, updated_at_ms
                    FROM secure_ingress_idempotency_claim
                    WHERE claim_ref = ?
                    """,
                    (claim_ref,),
                ).fetchone()
                command = self._conn.execute(
                    """
                    SELECT graph_id, graph_revision, surface
                    FROM host_command_journal
                    WHERE ingress_claim_ref = ?
                    """,
                    (claim_ref,),
                ).fetchone()
                review = self._conn.execute(
                    """
                    SELECT status, status_version, resume_phase, subject_ref,
                           task_graph_id, task_node_id
                    FROM agent_runtime_review
                    WHERE review_id = ?
                    """,
                    (normalized_review_id,),
                ).fetchone()
                binding = (
                    parse_task_review_subject(review["subject_ref"])
                    if review is not None
                    else None
                )
                if (
                    claim is None
                    or str(claim["claim_status"]) != "COMMITTED"
                    or command is None
                    or str(command["surface"]) not in {"internal", "scheduler", "tool"}
                    or str(command["graph_id"]) != normalized_graph_id
                    or int(command["graph_revision"]) != revision
                    or review is None
                    or str(review["status"]) != normalized_status
                    or int(review["status_version"]) != status_version
                    or str(review["task_graph_id"] or "") != normalized_graph_id
                    or str(review["task_node_id"] or "") != normalized_node_id
                    or binding is None
                    or binding.graph_id != normalized_graph_id
                    or binding.graph_revision != revision
                    or binding.node_id != normalized_node_id
                ):
                    raise SecureIngressStoreError(
                        "SECURE_INGRESS_REVIEW_DECISION_BINDING_CONFLICT"
                    )
                current = (
                    None
                    if claim["result_ref"] is None
                    else str(claim["result_ref"])
                )
                previous = str(prior_result_ref or "").strip() or None
                if current == expected_ref:
                    self._conn.commit()
                    return
                if current is not None and (
                    str(review["resume_phase"]) != "POST_EXECUTION_ACCEPTANCE"
                    or previous is None
                    or current != previous
                ):
                    raise SecureIngressStoreError(
                        "SECURE_INGRESS_RESULT_CONFLICT"
                    )
                updated_at = int(claim["updated_at_ms"]) if at_ms is None else int(at_ms)
                cursor = self._conn.execute(
                    """
                    UPDATE secure_ingress_idempotency_claim
                    SET result_ref = ?, updated_at_ms = ?
                    WHERE claim_ref = ? AND result_ref IS ?
                    """,
                    (expected_ref, updated_at, claim_ref, current),
                )
                if cursor.rowcount != 1:
                    raise SecureIngressStoreError(
                        "SECURE_INGRESS_RESULT_CONFLICT"
                    )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def append(self, event: IngressAuditEvent) -> str:
        audit_ref = f"secure-ingress-audit://{uuid.uuid4().hex}"
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._insert_audit(self._conn, audit_ref=audit_ref, event=event)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return audit_ref

    def _insert_audit(
        self,
        conn: sqlite3.Connection,
        *,
        audit_ref: str,
        event: IngressAuditEvent,
    ) -> None:
        assert_audit_event_safe(event)
        conn.execute(
            """
            INSERT INTO secure_ingress_audit(
                audit_ref, request_id, correlation_id, surface, action,
                method, normalized_path, principal_id, tenant_id, key_id,
                credential_kind, credential_ref_digest,
                requested_scopes_json, required_scopes_json, body_sha256,
                idempotency_digest, nonce_digest, transport_key_digest,
                decision, reason_code, stage, claim_ref, rate_snapshot_json,
                side_effects_executed, ts_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_ref,
                event.request_id,
                event.correlation_id,
                event.surface,
                event.action,
                event.method,
                event.normalized_path,
                event.principal_id,
                event.tenant_id,
                event.key_id,
                event.credential_kind,
                event.credential_ref_digest,
                self._json_array(event.requested_scopes),
                self._json_array(event.required_scopes),
                event.body_sha256,
                event.idempotency_digest,
                event.nonce_digest,
                event.transport_key_digest,
                event.decision,
                event.reason_code,
                event.stage,
                event.claim_ref,
                self._json_object(event.rate_snapshot) if event.rate_snapshot is not None else None,
                0,
                int(event.ts_ms),
            ),
        )

    def read_audit(self, *, request_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if request_id is None:
                rows = self._conn.execute(
                    "SELECT * FROM secure_ingress_audit ORDER BY ts_ms, audit_ref"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM secure_ingress_audit WHERE request_id = ? ORDER BY ts_ms, audit_ref",
                    (request_id,),
                ).fetchall()
        return [dict(row) for row in rows]

    def resolve_committed_authority_by_audit(
        self,
        *,
        audit_ref: str,
    ) -> dict[str, Any] | None:
        """Resolve one canonical ALLOW audit and its committed claim.

        This is a read-only authority projection.  It deliberately returns no
        credential proof or secret material; callers must still enforce their
        own server-owned surface/action/scope/body contract.
        """

        reference = str(audit_ref or "").strip()
        if not reference.startswith("secure-ingress-audit://"):
            return None
        with self._lock:
            row = self._conn.execute(
                """
                SELECT
                    audit.audit_ref AS audit_ref,
                    audit.claim_ref AS audit_claim_ref,
                    audit.request_id AS audit_request_id,
                    audit.surface AS audit_surface,
                    audit.action AS audit_action,
                    audit.method AS audit_method,
                    audit.normalized_path AS audit_normalized_path,
                    audit.principal_id AS audit_principal_id,
                    audit.tenant_id AS audit_tenant_id,
                    audit.requested_scopes_json AS audit_requested_scopes_json,
                    audit.required_scopes_json AS audit_required_scopes_json,
                    audit.body_sha256 AS audit_body_sha256,
                    audit.decision AS audit_decision,
                    audit.reason_code AS audit_reason_code,
                    audit.stage AS audit_stage,
                    audit.side_effects_executed AS audit_side_effects_executed,
                    audit.ts_ms AS audit_ts_ms,
                    claim.claim_ref AS claim_ref,
                    claim.request_id AS claim_request_id,
                    claim.surface AS claim_surface,
                    claim.action AS claim_action,
                    claim.principal_id AS claim_principal_id,
                    claim.required_scopes_json AS claim_required_scopes_json,
                    claim.body_sha256 AS claim_body_sha256,
                    claim.claim_status AS claim_status,
                    claim.result_ref AS claim_result_ref,
                    claim.expires_at_ms AS claim_expires_at_ms
                FROM secure_ingress_audit AS audit
                JOIN secure_ingress_idempotency_claim AS claim
                  ON claim.claim_ref = audit.claim_ref
                WHERE audit.audit_ref = ?
                LIMIT 1
                """,
                (reference,),
            ).fetchone()
        if row is None or str(row["claim_status"]) != "COMMITTED":
            return None
        try:
            audit_required = self._string_tuple(
                row["audit_required_scopes_json"]
            )
            claim_required = self._string_tuple(
                row["claim_required_scopes_json"]
            )
            audit_requested = self._string_tuple(
                row["audit_requested_scopes_json"]
            )
            if (
                str(row["audit_claim_ref"] or "") != str(row["claim_ref"] or "")
                or str(row["audit_request_id"] or "")
                != str(row["claim_request_id"] or "")
                or str(row["audit_surface"] or "")
                != str(row["claim_surface"] or "")
                or str(row["audit_action"] or "")
                != str(row["claim_action"] or "")
                or str(row["audit_principal_id"] or "")
                != str(row["claim_principal_id"] or "")
                or str(row["audit_body_sha256"] or "")
                != str(row["claim_body_sha256"] or "")
                or audit_required != claim_required
            ):
                return None
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        return {
            "audit_ref": str(row["audit_ref"]),
            "claim_ref": str(row["claim_ref"]),
            "request_id": str(row["audit_request_id"]),
            "surface": str(row["audit_surface"]),
            "action": str(row["audit_action"]),
            "method": str(row["audit_method"]),
            "normalized_path": str(row["audit_normalized_path"]),
            "principal_id": str(row["audit_principal_id"]),
            "tenant_id": str(row["audit_tenant_id"]),
            "requested_scopes": audit_requested,
            "required_scopes": audit_required,
            "body_sha256": str(row["audit_body_sha256"]),
            "decision": str(row["audit_decision"]),
            "reason_code": str(row["audit_reason_code"]),
            "stage": str(row["audit_stage"]),
            "side_effects_executed": bool(
                int(row["audit_side_effects_executed"])
            ),
            "audit_ts_ms": int(row["audit_ts_ms"]),
            "claim_status": str(row["claim_status"]),
            "claim_result_ref": (
                str(row["claim_result_ref"])
                if row["claim_result_ref"] is not None
                else None
            ),
            "claim_expires_at_ms": int(row["claim_expires_at_ms"]),
        }

    @staticmethod
    def _within_validity(at_ms: int, valid_from_ms: int, valid_until_ms: Any) -> bool:
        return int(at_ms) >= int(valid_from_ms) and (
            valid_until_ms is None or int(at_ms) < int(valid_until_ms)
        )

    @staticmethod
    def _string_tuple(raw: Any) -> tuple[str, ...]:
        value = json.loads(str(raw))
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError("expected JSON string array")
        return tuple(sorted(set(value)))

    @staticmethod
    def _json_array(values: tuple[str, ...]) -> str:
        return json.dumps(sorted(set(values)), ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _json_object(value: dict[str, Any]) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


__all__ = [
    "SECURE_INGRESS_MIGRATION_ASSET_KEY",
    "SECURE_INGRESS_MIGRATION_SHA256",
    "SQLiteSecureIngressStore",
    "SecureIngressStoreError",
]
