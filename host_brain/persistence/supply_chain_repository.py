"""Persistence-owned Skill/Provider supply-chain quarantine repository.

The repository owns no runtime loading behavior and never creates schema.  It
only advances catalog-migrated metadata through a fail-closed state machine;
bundle bytes, signatures, credentials, and other private material are outside
this API and are never persisted here.
"""

from __future__ import annotations

from contextlib import contextmanager
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from host_brain.core.ids import now_ms as _now_ms
from typing import Any, Callable, Iterator, Mapping
import uuid


SKILL_SUPPLY_CHAIN_MIGRATION_ASSET_KEY = "0060#skill_supply_chain"
SKILL_SUPPLY_CHAIN_MIGRATION_SHA256 = (
    "ea7471978f063b6152f3f1699f6896fe5782bede412214b2162b3592b9a1618e"
)

_ALLOWED_MIGRATION_DISPOSITIONS = frozenset(
    {"APPLIED", "APPLIED_WITH_COMPAT", "LEGACY_BACKFILL"}
)
_SIGNATURE_ALGORITHMS = frozenset(
    {"ED25519", "ECDSA_P256_SHA256", "RSA_PSS_SHA256", "HMAC_SHA256"}
)
_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
_REASON_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{2,127}\Z")
_REF_PATTERN = re.compile(r"[a-z][a-z0-9+.-]*://[^\s\x00-\x1f\x7f]{1,1000}\Z")

_REQUIRED_SCHEMA: dict[str, frozenset[str]] = {
    "skill_supply_chain_bundle": frozenset(
        {
            "bundle_id",
            "bundle_kind",
            "bundle_name",
            "bundle_version",
            "publisher_id",
            "bundle_digest",
            "signature_key_id",
            "signature_algorithm",
            "signature_verified",
            "signature_verification_audit_ref",
            "sbom_digest",
            "quarantine_audit_ref",
            "sandbox_profile_id",
            "sandbox_profile_digest",
            "state",
            "reason_code",
            "latest_audit_ref",
            "intake_idempotency_digest",
            "activation_generation",
            "activation_fencing_token",
            "status_version",
            "created_at_ms",
            "updated_at_ms",
            "staged_at_ms",
            "activated_at_ms",
            "rejected_at_ms",
        }
    ),
    "skill_supply_chain_operation": frozenset(
        {
            "sequence",
            "operation_id",
            "idempotency_digest",
            "request_digest",
            "operation",
            "bundle_id",
            "previous_state",
            "new_state",
            "replaced_bundle_id",
            "activation_generation",
            "activation_fencing_token",
            "status_version",
            "reason_code",
            "audit_ref",
            "created_at_ms",
        }
    ),
}
_REQUIRED_PRIMARY_KEYS = {
    "skill_supply_chain_bundle": ("bundle_id",),
    "skill_supply_chain_operation": ("sequence",),
}
_REQUIRED_INDEXES = frozenset(
    {
        "uq_skill_supply_chain_active_identity",
        "idx_skill_supply_chain_bundle_state",
        "idx_skill_supply_chain_bundle_digest",
        "idx_skill_supply_chain_operation_bundle",
        "idx_skill_supply_chain_operation_replaced",
        "idx_skill_supply_chain_operation_audit",
    }
)
_REQUIRED_TABLE_SQL_SHA256 = {
    "skill_supply_chain_bundle": "7f942ac369c1b358e0ed6c73f131ae83b789176ad3158eb392aa9e99513c5fbf",
    "skill_supply_chain_operation": "08ba86b21c404cef428a8ede8660d30cc96567566b4c3453b9b46066977a4915",
}
_FORBIDDEN_COLUMN_PARTS = (
    "raw_bundle",
    "bundle_body",
    "signature_value",
    "signature_material",
    "private_key",
    "secret",
    "credential",
)


class BundleKind(str, Enum):
    SKILL = "SKILL"
    PROVIDER = "PROVIDER"


class BundleState(str, Enum):
    QUARANTINED = "QUARANTINED"
    STAGED = "STAGED"
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"


class SupplyChainAcceptance(str, Enum):
    NEW = "NEW"
    DUPLICATE_SAME = "DUPLICATE_SAME"


class SkillSupplyChainStoreError(RuntimeError):
    """Fail-closed repository error exposing only a stable reason code."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


def _canonical_json(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise SkillSupplyChainStoreError("SKILL_SUPPLY_CHAIN_REQUEST_INVALID") from exc


def _digest_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _request_digest(value: Mapping[str, Any]) -> str:
    return _digest_text(_canonical_json(value))


def _required_text(value: Any, reason_code: str, *, max_length: int) -> str:
    normalized = str(value or "").strip()
    if (
        not normalized
        or len(normalized) > max_length
        or any(ord(char) < 32 or ord(char) == 127 for char in normalized)
    ):
        raise SkillSupplyChainStoreError(reason_code)
    return normalized


def _optional_text(value: Any, reason_code: str, *, max_length: int) -> str | None:
    if value is None:
        return None
    normalized = _required_text(value, reason_code, max_length=max_length)
    return normalized


def _digest(value: Any, reason_code: str, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    normalized = str(value or "").strip()
    if not _DIGEST_PATTERN.fullmatch(normalized):
        raise SkillSupplyChainStoreError(reason_code)
    return normalized


def _audit_ref(value: Any, reason_code: str) -> str:
    normalized = str(value or "").strip()
    if not _REF_PATTERN.fullmatch(normalized):
        raise SkillSupplyChainStoreError(reason_code)
    return normalized


def _reason_code(value: Any) -> str:
    normalized = str(value or "").strip().upper()
    if not _REASON_PATTERN.fullmatch(normalized):
        raise SkillSupplyChainStoreError("SKILL_SUPPLY_CHAIN_REASON_CODE_INVALID")
    return normalized


def _kind(value: BundleKind | str) -> str:
    try:
        normalized = value.value if isinstance(value, BundleKind) else str(value).upper()
        return BundleKind(normalized).value
    except ValueError as exc:
        raise SkillSupplyChainStoreError("SKILL_SUPPLY_CHAIN_BUNDLE_KIND_INVALID") from exc


def _signature_algorithm(value: Any) -> str:
    normalized = str(value or "").strip().upper().replace("-", "_")
    if normalized not in _SIGNATURE_ALGORITHMS:
        raise SkillSupplyChainStoreError(
            "SKILL_SUPPLY_CHAIN_SIGNATURE_ALGORITHM_UNTRUSTED"
        )
    return normalized


def _idempotency_digest(*, operation: str, bundle_id: str, value: Any) -> str:
    normalized = _required_text(
        value,
        "SKILL_SUPPLY_CHAIN_IDEMPOTENCY_REQUIRED",
        max_length=2048,
    )
    return _digest_text(f"skill-supply-chain\n{operation}\n{bundle_id}\n{normalized}")


class SQLiteSkillSupplyChainStore:
    """Catalog-bound SQLite repository with strict staged activation fencing."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Callable[[], int] = _now_ms,
        timeout_ms: int = 5_000,
        verify_schema: bool = True,
    ) -> None:
        raw_path = str(db_path or "").strip()
        if not raw_path or raw_path == ":memory:" or raw_path.lower().startswith("file:"):
            raise SkillSupplyChainStoreError(
                "SKILL_SUPPLY_CHAIN_DURABLE_PATH_REQUIRED"
            )
        self.db_path = Path(raw_path)
        self._clock = clock
        self._timeout_ms = max(1, int(timeout_ms))
        if verify_schema:
            self.assert_schema_ready()

    def _now(self) -> int:
        value = int(self._clock())
        if value < 0:
            raise SkillSupplyChainStoreError("SKILL_SUPPLY_CHAIN_CLOCK_INVALID")
        return value

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=self._timeout_ms / 1000.0,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {self._timeout_ms}")
        return conn

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except sqlite3.OperationalError as exc:
            conn.rollback()
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                raise SkillSupplyChainStoreError(
                    "SKILL_SUPPLY_CHAIN_STORE_BUSY"
                ) from exc
            raise SkillSupplyChainStoreError(
                "SKILL_SUPPLY_CHAIN_STORE_UNAVAILABLE"
            ) from exc
        except sqlite3.Error as exc:
            conn.rollback()
            raise SkillSupplyChainStoreError(
                "SKILL_SUPPLY_CHAIN_STORE_UNAVAILABLE"
            ) from exc
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def assert_schema_ready(self) -> None:
        if not self.db_path.is_file():
            raise SkillSupplyChainStoreError(
                "SKILL_SUPPLY_CHAIN_SCHEMA_UNAVAILABLE"
            )
        conn = self._connect()
        try:
            try:
                asset = conn.execute(
                    """
                    SELECT checksum, disposition
                    FROM schema_migration_assets
                    WHERE asset_key = ?
                    """,
                    (SKILL_SUPPLY_CHAIN_MIGRATION_ASSET_KEY,),
                ).fetchone()
            except sqlite3.Error as exc:
                raise SkillSupplyChainStoreError(
                    "SKILL_SUPPLY_CHAIN_MIGRATION_DRIFT"
                ) from exc
            if (
                asset is None
                or str(asset["checksum"]) != SKILL_SUPPLY_CHAIN_MIGRATION_SHA256
                or str(asset["disposition"]) not in _ALLOWED_MIGRATION_DISPOSITIONS
            ):
                raise SkillSupplyChainStoreError(
                    "SKILL_SUPPLY_CHAIN_MIGRATION_DRIFT"
                )

            for table, required_columns in _REQUIRED_SCHEMA.items():
                rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
                columns = {str(row["name"]) for row in rows}
                if columns != set(required_columns):
                    raise SkillSupplyChainStoreError(
                        "SKILL_SUPPLY_CHAIN_SCHEMA_DRIFT"
                    )
                if any(
                    forbidden in column.lower()
                    for column in columns
                    for forbidden in _FORBIDDEN_COLUMN_PARTS
                ):
                    raise SkillSupplyChainStoreError(
                        "SKILL_SUPPLY_CHAIN_PRIVATE_MATERIAL_SCHEMA_FORBIDDEN"
                    )
                primary_key = tuple(
                    str(row["name"])
                    for row in sorted(
                        (row for row in rows if int(row["pk"]) > 0),
                        key=lambda item: int(item["pk"]),
                    )
                )
                if primary_key != _REQUIRED_PRIMARY_KEYS[table]:
                    raise SkillSupplyChainStoreError(
                        "SKILL_SUPPLY_CHAIN_SCHEMA_DRIFT"
                    )
                sql_digest = hashlib.sha256(
                    self._table_sql(conn, table).encode("utf-8")
                ).hexdigest()
                if sql_digest != _REQUIRED_TABLE_SQL_SHA256[table]:
                    raise SkillSupplyChainStoreError(
                        "SKILL_SUPPLY_CHAIN_SCHEMA_DRIFT"
                    )

            indexes = {
                str(row["name"])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index'"
                ).fetchall()
            }
            if not _REQUIRED_INDEXES.issubset(indexes):
                raise SkillSupplyChainStoreError(
                    "SKILL_SUPPLY_CHAIN_SCHEMA_DRIFT"
                )
            active_index = conn.execute(
                """
                SELECT sql FROM sqlite_master
                WHERE type = 'index' AND name = 'uq_skill_supply_chain_active_identity'
                """
            ).fetchone()
            active_sql = "".join(str(active_index["sql"] if active_index else "").upper().split())
            if (
                "UNIQUEINDEX" not in active_sql
                or "(BUNDLE_KIND,BUNDLE_NAME)" not in active_sql
                or "WHERESTATE='ACTIVE'" not in active_sql
            ):
                raise SkillSupplyChainStoreError(
                    "SKILL_SUPPLY_CHAIN_SCHEMA_DRIFT"
                )
        finally:
            conn.close()

    @staticmethod
    def _table_sql(conn: sqlite3.Connection, table: str) -> str:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        return " ".join(str(row["sql"] if row else "").upper().split())

    def intake_bundle(
        self,
        *,
        bundle_id: str,
        bundle_kind: BundleKind | str,
        bundle_name: str,
        bundle_version: str,
        publisher_id: str,
        bundle_digest: str,
        quarantine_audit_ref: str,
        idempotency_key: str,
        reason_code: str = "BUNDLE_INTAKE_QUARANTINED",
        signature_key_id: str | None = None,
        signature_algorithm: str | None = None,
        sbom_digest: str | None = None,
    ) -> dict[str, Any]:
        normalized_id = _required_text(
            bundle_id, "SKILL_SUPPLY_CHAIN_BUNDLE_ID_REQUIRED", max_length=255
        )
        normalized_kind = _kind(bundle_kind)
        normalized_name = _required_text(
            bundle_name, "SKILL_SUPPLY_CHAIN_BUNDLE_NAME_REQUIRED", max_length=255
        )
        normalized_version = _required_text(
            bundle_version,
            "SKILL_SUPPLY_CHAIN_BUNDLE_VERSION_REQUIRED",
            max_length=128,
        )
        normalized_publisher = _required_text(
            publisher_id, "SKILL_SUPPLY_CHAIN_PUBLISHER_REQUIRED", max_length=255
        )
        normalized_digest = _digest(
            bundle_digest, "SKILL_SUPPLY_CHAIN_BUNDLE_DIGEST_INVALID"
        )
        assert normalized_digest is not None
        normalized_quarantine_ref = _audit_ref(
            quarantine_audit_ref, "SKILL_SUPPLY_CHAIN_QUARANTINE_AUDIT_REQUIRED"
        )
        normalized_reason = _reason_code(reason_code)
        normalized_key_id = _optional_text(
            signature_key_id,
            "SKILL_SUPPLY_CHAIN_SIGNATURE_KEY_ID_INVALID",
            max_length=255,
        )
        normalized_algorithm = (
            None if signature_algorithm is None else _signature_algorithm(signature_algorithm)
        )
        if (normalized_key_id is None) != (normalized_algorithm is None):
            raise SkillSupplyChainStoreError(
                "SKILL_SUPPLY_CHAIN_SIGNATURE_METADATA_INCOMPLETE"
            )
        normalized_sbom = _digest(
            sbom_digest,
            "SKILL_SUPPLY_CHAIN_SBOM_DIGEST_INVALID",
            required=False,
        )
        idem_digest = _idempotency_digest(
            operation="INTAKE", bundle_id=normalized_id, value=idempotency_key
        )
        request = {
            "bundle_id": normalized_id,
            "bundle_kind": normalized_kind,
            "bundle_name": normalized_name,
            "bundle_version": normalized_version,
            "publisher_id": normalized_publisher,
            "bundle_digest": normalized_digest,
            "quarantine_audit_ref": normalized_quarantine_ref,
            "reason_code": normalized_reason,
            "signature_key_id": normalized_key_id,
            "signature_algorithm": normalized_algorithm,
            "sbom_digest": normalized_sbom,
        }
        req_digest = _request_digest(request)
        now = self._now()

        try:
            with self._transaction() as conn:
                replay = self._operation_replay(conn, idem_digest, req_digest)
                if replay is not None:
                    return self._mutation_result(
                        conn, replay, SupplyChainAcceptance.DUPLICATE_SAME
                    )
                if self._identity_exists(conn, normalized_id, normalized_kind, normalized_name, normalized_version):
                    raise SkillSupplyChainStoreError(
                        "SKILL_SUPPLY_CHAIN_BUNDLE_IDENTITY_CONFLICT"
                    )
                conn.execute(
                    """
                    INSERT INTO skill_supply_chain_bundle(
                        bundle_id, bundle_kind, bundle_name, bundle_version,
                        publisher_id, bundle_digest, signature_key_id,
                        signature_algorithm, signature_verified,
                        signature_verification_audit_ref, sbom_digest,
                        quarantine_audit_ref, sandbox_profile_id,
                        sandbox_profile_digest, state, reason_code,
                        latest_audit_ref, intake_idempotency_digest,
                        activation_generation, activation_fencing_token,
                        status_version, created_at_ms, updated_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?, NULL, NULL,
                              'QUARANTINED', ?, ?, ?, 0, 0, 0, ?, ?)
                    """,
                    (
                        normalized_id,
                        normalized_kind,
                        normalized_name,
                        normalized_version,
                        normalized_publisher,
                        normalized_digest,
                        normalized_key_id,
                        normalized_algorithm,
                        normalized_sbom,
                        normalized_quarantine_ref,
                        normalized_reason,
                        normalized_quarantine_ref,
                        idem_digest,
                        now,
                        now,
                    ),
                )
                operation = self._insert_operation(
                    conn,
                    idempotency_digest=idem_digest,
                    request_digest=req_digest,
                    operation="INTAKE",
                    bundle_id=normalized_id,
                    previous_state=None,
                    new_state=BundleState.QUARANTINED.value,
                    replaced_bundle_id=None,
                    activation_generation=0,
                    activation_fencing_token=0,
                    status_version=0,
                    reason_code=normalized_reason,
                    audit_ref=normalized_quarantine_ref,
                    created_at_ms=now,
                )
                return self._mutation_result(conn, operation, SupplyChainAcceptance.NEW)
        except sqlite3.IntegrityError as exc:
            raise SkillSupplyChainStoreError(
                "SKILL_SUPPLY_CHAIN_BUNDLE_IDENTITY_CONFLICT"
            ) from exc

    def stage_bundle(
        self,
        *,
        bundle_id: str,
        expected_status_version: int,
        signature_key_id: str,
        signature_algorithm: str,
        signature_verification_audit_ref: str,
        sbom_digest: str,
        sandbox_profile_id: str,
        sandbox_profile_digest: str,
        audit_ref: str,
        idempotency_key: str,
        reason_code: str = "BUNDLE_VERIFIED_AND_STAGED",
    ) -> dict[str, Any]:
        normalized_id = _required_text(
            bundle_id, "SKILL_SUPPLY_CHAIN_BUNDLE_ID_REQUIRED", max_length=255
        )
        version = self._status_version(expected_status_version)
        key_id = _required_text(
            signature_key_id,
            "SKILL_SUPPLY_CHAIN_SIGNATURE_KEY_ID_REQUIRED",
            max_length=255,
        )
        algorithm = _signature_algorithm(signature_algorithm)
        verification_ref = _audit_ref(
            signature_verification_audit_ref,
            "SKILL_SUPPLY_CHAIN_SIGNATURE_AUDIT_REQUIRED",
        )
        sbom = _digest(sbom_digest, "SKILL_SUPPLY_CHAIN_SBOM_DIGEST_INVALID")
        profile_id = _required_text(
            sandbox_profile_id,
            "SKILL_SUPPLY_CHAIN_SANDBOX_PROFILE_REQUIRED",
            max_length=255,
        )
        profile_digest = _digest(
            sandbox_profile_digest,
            "SKILL_SUPPLY_CHAIN_SANDBOX_PROFILE_DIGEST_INVALID",
        )
        audit = _audit_ref(audit_ref, "SKILL_SUPPLY_CHAIN_AUDIT_REF_REQUIRED")
        reason = _reason_code(reason_code)
        idem_digest = _idempotency_digest(
            operation="STAGE", bundle_id=normalized_id, value=idempotency_key
        )
        request = {
            "bundle_id": normalized_id,
            "expected_status_version": version,
            "signature_key_id": key_id,
            "signature_algorithm": algorithm,
            "signature_verification_audit_ref": verification_ref,
            "sbom_digest": sbom,
            "sandbox_profile_id": profile_id,
            "sandbox_profile_digest": profile_digest,
            "audit_ref": audit,
            "reason_code": reason,
        }
        req_digest = _request_digest(request)
        now = self._now()
        with self._transaction() as conn:
            replay = self._operation_replay(conn, idem_digest, req_digest)
            if replay is not None:
                return self._mutation_result(
                    conn, replay, SupplyChainAcceptance.DUPLICATE_SAME
                )
            row = self._bundle_required(conn, normalized_id)
            self._expect_state_version(row, BundleState.QUARANTINED, version)
            new_version = version + 1
            updated = conn.execute(
                """
                UPDATE skill_supply_chain_bundle
                SET signature_key_id = ?, signature_algorithm = ?,
                    signature_verified = 1,
                    signature_verification_audit_ref = ?, sbom_digest = ?,
                    sandbox_profile_id = ?, sandbox_profile_digest = ?,
                    state = 'STAGED', reason_code = ?, latest_audit_ref = ?,
                    status_version = ?, updated_at_ms = ?, staged_at_ms = ?
                WHERE bundle_id = ? AND state = 'QUARANTINED' AND status_version = ?
                """,
                (
                    key_id,
                    algorithm,
                    verification_ref,
                    sbom,
                    profile_id,
                    profile_digest,
                    reason,
                    audit,
                    new_version,
                    now,
                    now,
                    normalized_id,
                    version,
                ),
            )
            if updated.rowcount != 1:
                raise SkillSupplyChainStoreError(
                    "SKILL_SUPPLY_CHAIN_STATUS_CONFLICT"
                )
            operation = self._insert_operation(
                conn,
                idempotency_digest=idem_digest,
                request_digest=req_digest,
                operation="STAGE",
                bundle_id=normalized_id,
                previous_state=BundleState.QUARANTINED.value,
                new_state=BundleState.STAGED.value,
                replaced_bundle_id=None,
                activation_generation=int(row["activation_generation"]),
                activation_fencing_token=int(row["activation_fencing_token"]),
                status_version=new_version,
                reason_code=reason,
                audit_ref=audit,
                created_at_ms=now,
            )
            return self._mutation_result(conn, operation, SupplyChainAcceptance.NEW)

    def get_bundle(self, *, bundle_id: str) -> dict[str, Any] | None:
        normalized_id = _required_text(
            bundle_id, "SKILL_SUPPLY_CHAIN_BUNDLE_ID_REQUIRED", max_length=255
        )
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM skill_supply_chain_bundle WHERE bundle_id = ?",
                (normalized_id,),
            ).fetchone()
            return None if row is None else self._bundle_dict(row)
        finally:
            conn.close()

    def get_active_bundle(
        self, *, bundle_kind: BundleKind | str, bundle_name: str
    ) -> dict[str, Any] | None:
        normalized_kind = _kind(bundle_kind)
        normalized_name = _required_text(
            bundle_name, "SKILL_SUPPLY_CHAIN_BUNDLE_NAME_REQUIRED", max_length=255
        )
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT * FROM skill_supply_chain_bundle
                WHERE bundle_kind = ? AND bundle_name = ? AND state = 'ACTIVE'
                LIMIT 1
                """,
                (normalized_kind, normalized_name),
            ).fetchone()
            return None if row is None else self._bundle_dict(row)
        finally:
            conn.close()

    def list_bundles(
        self,
        *,
        state: BundleState | str | None = None,
        bundle_kind: BundleKind | str | None = None,
        bundle_name: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if state is not None:
            try:
                raw_state = state.value if isinstance(state, BundleState) else str(state).upper()
                normalized_state = BundleState(raw_state).value
            except ValueError as exc:
                raise SkillSupplyChainStoreError(
                    "SKILL_SUPPLY_CHAIN_STATE_INVALID"
                ) from exc
            clauses.append("state = ?")
            params.append(normalized_state)
        if bundle_kind is not None:
            clauses.append("bundle_kind = ?")
            params.append(_kind(bundle_kind))
        if bundle_name is not None:
            clauses.append("bundle_name = ?")
            params.append(
                _required_text(
                    bundle_name,
                    "SKILL_SUPPLY_CHAIN_BUNDLE_NAME_REQUIRED",
                    max_length=255,
                )
            )
        bounded_limit = self._limit(limit)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        conn = self._connect()
        try:
            rows = conn.execute(
                f"""
                SELECT * FROM skill_supply_chain_bundle{where}
                ORDER BY updated_at_ms DESC, bundle_id ASC LIMIT ?
                """,
                (*params, bounded_limit),
            ).fetchall()
            return [self._bundle_dict(row) for row in rows]
        finally:
            conn.close()

    def list_audit_operations(
        self,
        *,
        bundle_id: str | None = None,
        audit_ref: str | None = None,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses = ["sequence > ?"]
        params: list[Any] = [max(0, int(after_sequence))]
        if bundle_id is not None:
            normalized_id = _required_text(
                bundle_id, "SKILL_SUPPLY_CHAIN_BUNDLE_ID_REQUIRED", max_length=255
            )
            clauses.append("(bundle_id = ? OR replaced_bundle_id = ?)")
            params.extend((normalized_id, normalized_id))
        if audit_ref is not None:
            clauses.append("audit_ref = ?")
            params.append(
                _audit_ref(audit_ref, "SKILL_SUPPLY_CHAIN_AUDIT_REF_REQUIRED")
            )
        conn = self._connect()
        try:
            rows = conn.execute(
                f"""
                SELECT * FROM skill_supply_chain_operation
                WHERE {' AND '.join(clauses)}
                ORDER BY sequence ASC LIMIT ?
                """,
                (*params, self._limit(limit)),
            ).fetchall()
            return [self._operation_dict(row) for row in rows]
        finally:
            conn.close()

    @staticmethod
    def _limit(value: int) -> int:
        normalized = int(value)
        if normalized < 1 or normalized > 500:
            raise SkillSupplyChainStoreError(
                "SKILL_SUPPLY_CHAIN_QUERY_LIMIT_INVALID"
            )
        return normalized

    @staticmethod
    def _status_version(value: int) -> int:
        normalized = int(value)
        if normalized < 0:
            raise SkillSupplyChainStoreError(
                "SKILL_SUPPLY_CHAIN_STATUS_VERSION_INVALID"
            )
        return normalized

    @staticmethod
    def _bundle_required(conn: sqlite3.Connection, bundle_id: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM skill_supply_chain_bundle WHERE bundle_id = ?",
            (bundle_id,),
        ).fetchone()
        if row is None:
            raise SkillSupplyChainStoreError("SKILL_SUPPLY_CHAIN_BUNDLE_NOT_FOUND")
        return row

    @staticmethod
    def _expect_state_version(
        row: sqlite3.Row, state: BundleState, status_version: int
    ) -> None:
        if str(row["state"]) != state.value or int(row["status_version"]) != status_version:
            raise SkillSupplyChainStoreError("SKILL_SUPPLY_CHAIN_STATUS_CONFLICT")

    @staticmethod
    def _identity_exists(
        conn: sqlite3.Connection,
        bundle_id: str,
        bundle_kind: str,
        bundle_name: str,
        bundle_version: str,
    ) -> bool:
        return conn.execute(
            """
            SELECT 1 FROM skill_supply_chain_bundle
            WHERE bundle_id = ?
               OR (bundle_kind = ? AND bundle_name = ? AND bundle_version = ?)
            LIMIT 1
            """,
            (bundle_id, bundle_kind, bundle_name, bundle_version),
        ).fetchone() is not None

    @staticmethod
    def _operation_replay(
        conn: sqlite3.Connection, idempotency_digest: str, request_digest: str
    ) -> sqlite3.Row | None:
        row = conn.execute(
            """
            SELECT * FROM skill_supply_chain_operation
            WHERE idempotency_digest = ?
            """,
            (idempotency_digest,),
        ).fetchone()
        if row is None:
            return None
        if str(row["request_digest"]) != request_digest:
            raise SkillSupplyChainStoreError(
                "SKILL_SUPPLY_CHAIN_IDEMPOTENCY_CONFLICT"
            )
        return row

    @staticmethod
    def _insert_operation(
        conn: sqlite3.Connection,
        *,
        idempotency_digest: str,
        request_digest: str,
        operation: str,
        bundle_id: str,
        previous_state: str | None,
        new_state: str,
        replaced_bundle_id: str | None,
        activation_generation: int,
        activation_fencing_token: int,
        status_version: int,
        reason_code: str,
        audit_ref: str,
        created_at_ms: int,
    ) -> sqlite3.Row:
        operation_id = f"skill-supply-chain-operation://{uuid.uuid4().hex}"
        conn.execute(
            """
            INSERT INTO skill_supply_chain_operation(
                operation_id, idempotency_digest, request_digest, operation,
                bundle_id, previous_state, new_state, replaced_bundle_id,
                activation_generation, activation_fencing_token, status_version,
                reason_code, audit_ref, created_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                operation_id,
                idempotency_digest,
                request_digest,
                operation,
                bundle_id,
                previous_state,
                new_state,
                replaced_bundle_id,
                activation_generation,
                activation_fencing_token,
                status_version,
                reason_code,
                audit_ref,
                created_at_ms,
            ),
        )
        row = conn.execute(
            "SELECT * FROM skill_supply_chain_operation WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        assert row is not None
        return row

    def _mutation_result(
        self,
        conn: sqlite3.Connection,
        operation: sqlite3.Row,
        acceptance: SupplyChainAcceptance,
    ) -> dict[str, Any]:
        bundle = self._bundle_required(conn, str(operation["bundle_id"]))
        return {
            "acceptance_status": acceptance.value,
            "bundle": self._bundle_dict(bundle),
            "operation": self._operation_dict(operation),
        }

    @staticmethod
    def _bundle_dict(row: sqlite3.Row) -> dict[str, Any]:
        fields = (
            "bundle_id",
            "bundle_kind",
            "bundle_name",
            "bundle_version",
            "publisher_id",
            "bundle_digest",
            "signature_key_id",
            "signature_algorithm",
            "signature_verified",
            "signature_verification_audit_ref",
            "sbom_digest",
            "quarantine_audit_ref",
            "sandbox_profile_id",
            "sandbox_profile_digest",
            "state",
            "reason_code",
            "latest_audit_ref",
            "activation_generation",
            "activation_fencing_token",
            "status_version",
            "created_at_ms",
            "updated_at_ms",
            "staged_at_ms",
            "activated_at_ms",
            "rejected_at_ms",
        )
        result = {field: row[field] for field in fields}
        result["signature_verified"] = bool(result["signature_verified"])
        return result

    @staticmethod
    def _operation_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            field: row[field]
            for field in (
                "sequence",
                "operation_id",
                "idempotency_digest",
                "request_digest",
                "operation",
                "bundle_id",
                "previous_state",
                "new_state",
                "replaced_bundle_id",
                "activation_generation",
                "activation_fencing_token",
                "status_version",
                "reason_code",
                "audit_ref",
                "created_at_ms",
            )
        }


__all__ = [
    "BundleKind",
    "BundleState",
    "SKILL_SUPPLY_CHAIN_MIGRATION_ASSET_KEY",
    "SKILL_SUPPLY_CHAIN_MIGRATION_SHA256",
    "SQLiteSkillSupplyChainStore",
    "SkillSupplyChainStoreError",
    "SupplyChainAcceptance",
]
