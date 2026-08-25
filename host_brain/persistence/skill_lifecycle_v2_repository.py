"""Single SQLite authority for the NE-05 Skill lifecycle V2."""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable, Iterator, Mapping

from host_brain.contracts.skill_lifecycle_v2 import (
    SkillActivationEvidenceV2,
    SkillLifecycleReceiptV2,
    SkillLifecycleV2Error,
)
from host_brain.core.ids import now_ms
from host_brain.persistence.skill_lifecycle_v2_evidence import (
    SkillLifecycleV2EvidenceMixin,
    SkillLifecycleV2RepositoryError,
    _canonical,
    _hex_digest,
    _sha256,
    _sha256_ref,
    _text,
)


SKILL_LIFECYCLE_V2_MIGRATION_ASSET_KEY = (
    "0141#skill_lifecycle_authority"
)
SKILL_LIFECYCLE_V2_MIGRATION_SHA256 = (
    "2b0d8c6ff285c6e5aacca3ed2de4e34cf0ac9f4eb5f9d95793277e081d302104"
)
_APPLIED_DISPOSITIONS = {
    "APPLIED",
    "APPLIED_WITH_COMPAT",
    "LEGACY_BACKFILL",
}
_REQUIRED_TABLES = {
    "skill_lifecycle_canary_operation_v2",
    "skill_lifecycle_outbox_v2",
    "skill_activation_evidence_v2",
    "skill_capability_maturity_decision_v1",
    "skill_lifecycle_operation_v2",
    "skill_active_version_projection_v2",
    "skill_lifecycle_receipt_v2",
}
_REQUIRED_TRIGGERS = {
    "skill_activation_evidence_v2_no_update",
    "skill_activation_evidence_v2_no_delete",
    "skill_maturity_decision_v1_no_update",
    "skill_maturity_decision_v1_no_delete",
    "skill_lifecycle_operation_v2_no_update",
    "skill_lifecycle_operation_v2_no_delete",
    "skill_lifecycle_receipt_v2_no_update",
    "skill_lifecycle_receipt_v2_no_delete",
    "skill_active_projection_v2_insert_guard",
    "skill_active_projection_v2_update_guard",
    "skill_active_projection_v2_delete_guard",
    "skill_canary_operation_v2_insert_guard",
    "skill_canary_operation_v2_update_guard",
    "skill_canary_operation_v2_delete_guard",
    "skill_lifecycle_outbox_v2_insert_guard",
    "skill_lifecycle_outbox_v2_update_guard",
    "skill_lifecycle_outbox_v2_delete_guard",
}


class SQLiteSkillLifecycleV2Repository(SkillLifecycleV2EvidenceMixin):
    """Atomic canary, evidence, activation, rollback and active projection owner."""

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
            raise SkillLifecycleV2RepositoryError(
                "SKILL_LIFECYCLE_V2_DURABLE_PATH_REQUIRED"
            )
        if not callable(clock):
            raise SkillLifecycleV2RepositoryError(
                "SKILL_LIFECYCLE_V2_CLOCK_INVALID"
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
            "natsume_skill_lifecycle_write_authorized",
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
            raise SkillLifecycleV2RepositoryError(
                "SKILL_LIFECYCLE_V2_SCHEMA_NOT_READY"
            )
        try:
            with self._connect() as conn:
                asset = conn.execute(
                    """
                    SELECT checksum, disposition
                    FROM schema_migration_assets
                    WHERE asset_key = ?
                    """,
                    (SKILL_LIFECYCLE_V2_MIGRATION_ASSET_KEY,),
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
                        "SELECT name FROM sqlite_master "
                        "WHERE type = 'trigger'"
                    ).fetchall()
                }
        except sqlite3.Error as exc:
            raise SkillLifecycleV2RepositoryError(
                "SKILL_LIFECYCLE_V2_SCHEMA_NOT_READY"
            ) from exc
        if (
            asset is None
            or str(asset["checksum"])
            != SKILL_LIFECYCLE_V2_MIGRATION_SHA256
            or str(asset["disposition"]) not in _APPLIED_DISPOSITIONS
            or not _REQUIRED_TABLES.issubset(tables)
            or not _REQUIRED_TRIGGERS.issubset(triggers)
        ):
            raise SkillLifecycleV2RepositoryError(
                "SKILL_LIFECYCLE_V2_SCHEMA_NOT_READY"
            )

    def request_canary(
        self,
        *,
        workspace_id: str,
        bundle_id: str,
        runtime_adapter_id: str,
        runtime_protocol_version: str,
        sandbox_attestation_digest: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        workspace = _text(workspace_id, "workspace_id", 128).lower()
        bundle_key = _text(bundle_id, "bundle_id")
        adapter = _text(runtime_adapter_id, "runtime_adapter_id", 128).lower()
        protocol = _text(
            runtime_protocol_version,
            "runtime_protocol_version",
            128,
        ).lower()
        sandbox_digest = _hex_digest(
            sandbox_attestation_digest,
            "sandbox_attestation_digest",
        )
        idem = _text(idempotency_key, "idempotency_key", 512)
        idem_digest = _sha256(f"CANARY|{bundle_key}|{idem}")
        now = int(self._clock())
        with self._transaction() as conn:
            bundle = self._require_bundle(conn, bundle_key)
            if str(bundle["state"]) != "STAGED":
                self._invalid("SKILL_LIFECYCLE_V2_BUNDLE_NOT_STAGED")
            request = {
                "schema": "natsume.skill-canary-request.v1",
                "workspace_id": workspace,
                "bundle_id": bundle_key,
                "bundle_digest": str(bundle["bundle_digest"]),
                "runtime_adapter_id": adapter,
                "runtime_protocol_version": protocol,
                "sandbox_attestation_digest": sandbox_digest,
            }
            request_json = _canonical(request)
            request_digest = _sha256(request_json)
            replay = conn.execute(
                """
                SELECT *
                FROM skill_lifecycle_canary_operation_v2
                WHERE idempotency_digest = ?
                """,
                (idem_digest,),
            ).fetchone()
            if replay is not None:
                if str(replay["request_digest"]) != request_digest:
                    self._invalid(
                        "SKILL_LIFECYCLE_V2_IDEMPOTENCY_DRIFT"
                    )
                return self._canary_record(replay)
            operation_id = (
                f"skill-canary-operation://sha256/{idem_digest}"
            )
            outbox_id = f"skill-canary-outbox://sha256/{idem_digest}"
            conn.execute(
                """
                INSERT INTO skill_lifecycle_canary_operation_v2(
                    operation_id, idempotency_digest, request_digest,
                    workspace_id, bundle_id, bundle_digest,
                    runtime_adapter_id, runtime_protocol_version,
                    sandbox_attestation_digest, outbox_id, status,
                    downstream_receipt_digest, result_digest, reason_code,
                    request_json, result_json, created_at_ms, updated_at_ms,
                    terminal_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING',
                    NULL, NULL, 'SKILL_CANARY_PENDING', ?, NULL, ?, ?, NULL)
                """,
                (
                    operation_id,
                    idem_digest,
                    request_digest,
                    workspace,
                    bundle_key,
                    str(bundle["bundle_digest"]),
                    adapter,
                    protocol,
                    sandbox_digest,
                    outbox_id,
                    request_json,
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO skill_lifecycle_outbox_v2(
                    outbox_id, operation_id, destination_adapter_id,
                    payload_digest, payload_json, state, lease_owner,
                    lease_expires_at_ms, fencing_token, attempt_count,
                    downstream_receipt_digest, created_at_ms, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, 'PENDING', NULL, NULL, 0, 0,
                    NULL, ?, ?)
                """,
                (
                    outbox_id,
                    operation_id,
                    adapter,
                    request_digest,
                    request_json,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                """
                SELECT *
                FROM skill_lifecycle_canary_operation_v2
                WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
            return self._canary_record(row)

    def claim_canary_outbox(
        self,
        *,
        lease_owner: str,
        lease_ms: int,
    ) -> dict[str, Any] | None:
        owner = _text(lease_owner, "lease_owner", 255)
        duration = int(lease_ms)
        if duration < 1:
            self._invalid("SKILL_LIFECYCLE_V2_LEASE_INVALID")
        now = int(self._clock())
        with self._transaction() as conn:
            expired = conn.execute(
                """
                SELECT *
                FROM skill_lifecycle_outbox_v2
                WHERE state = 'CLAIMED' AND lease_expires_at_ms <= ?
                ORDER BY updated_at_ms, outbox_id
                LIMIT 1
                """,
                (now,),
            ).fetchone()
            if expired is not None:
                self._freeze_expired_claim(conn, expired, now=now)
                return None
            row = conn.execute(
                """
                SELECT *
                FROM skill_lifecycle_outbox_v2
                WHERE state = 'PENDING'
                ORDER BY created_at_ms, outbox_id
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            fence = int(row["fencing_token"]) + 1
            updated = conn.execute(
                """
                UPDATE skill_lifecycle_outbox_v2
                SET state = 'CLAIMED', lease_owner = ?,
                    lease_expires_at_ms = ?, fencing_token = ?,
                    attempt_count = attempt_count + 1, updated_at_ms = ?
                WHERE outbox_id = ? AND state = 'PENDING'
                """,
                (
                    owner,
                    now + duration,
                    fence,
                    now,
                    str(row["outbox_id"]),
                ),
            )
            if updated.rowcount != 1:
                self._invalid("SKILL_LIFECYCLE_V2_OUTBOX_CONFLICT")
            claimed = conn.execute(
                """
                SELECT *
                FROM skill_lifecycle_outbox_v2
                WHERE outbox_id = ?
                """,
                (str(row["outbox_id"]),),
            ).fetchone()
            return dict(claimed)

    def settle_canary(
        self,
        *,
        operation_id: str,
        lease_owner: str,
        fencing_token: int,
        outcome: str,
        downstream_receipt_digest: str,
        verifier_identity: str,
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        operation_key = _text(operation_id, "operation_id")
        owner = _text(lease_owner, "lease_owner", 255)
        fence = int(fencing_token)
        normalized_outcome = str(outcome or "").strip().upper()
        if normalized_outcome not in {"PASS", "FAIL", "UNKNOWN"}:
            self._invalid("SKILL_LIFECYCLE_V2_CANARY_OUTCOME_INVALID")
        downstream_digest = _hex_digest(
            downstream_receipt_digest,
            "downstream_receipt_digest",
        )
        verifier = _text(verifier_identity, "verifier_identity", 255)
        result_payload = {
            "schema": "natsume.skill-canary-result.v1",
            "operation_id": operation_key,
            "outcome": normalized_outcome,
            "downstream_receipt_digest": downstream_digest,
            "verifier_identity": verifier,
            "result": dict(result),
        }
        result_json = _canonical(result_payload)
        result_digest = _sha256(result_json)
        now = int(self._clock())
        with self._transaction() as conn:
            operation = self._require_canary(conn, operation_key)
            if str(operation["status"]) != "PENDING":
                if (
                    str(operation["result_digest"]) == result_digest
                    and str(operation["downstream_receipt_digest"])
                    == downstream_digest
                ):
                    return self._canary_record(operation)
                self._invalid("SKILL_LIFECYCLE_V2_CANARY_REPLAY_DRIFT")
            outbox = conn.execute(
                """
                SELECT *
                FROM skill_lifecycle_outbox_v2
                WHERE operation_id = ?
                """,
                (operation_key,),
            ).fetchone()
            if (
                outbox is None
                or str(outbox["state"]) != "CLAIMED"
                or str(outbox["lease_owner"]) != owner
                or int(outbox["fencing_token"]) != fence
                or int(outbox["lease_expires_at_ms"] or -1) < now
            ):
                self._invalid("SKILL_LIFECYCLE_V2_CANARY_CLAIM_STALE")
            status = {
                "PASS": "PASSED",
                "FAIL": "FAILED",
                "UNKNOWN": "RECONCILIATION_REQUIRED",
            }[normalized_outcome]
            reason = {
                "PASS": "SKILL_CANARY_PASSED",
                "FAIL": "SKILL_CANARY_FAILED",
                "UNKNOWN": "SKILL_CANARY_RECONCILIATION_REQUIRED",
            }[normalized_outcome]
            outbox_state = (
                "ACKNOWLEDGED"
                if normalized_outcome in {"PASS", "FAIL"}
                else "UNKNOWN"
            )
            conn.execute(
                """
                UPDATE skill_lifecycle_canary_operation_v2
                SET status = ?, downstream_receipt_digest = ?,
                    result_digest = ?, reason_code = ?, result_json = ?,
                    updated_at_ms = ?, terminal_at_ms = ?
                WHERE operation_id = ? AND status = 'PENDING'
                """,
                (
                    status,
                    downstream_digest,
                    result_digest,
                    reason,
                    result_json,
                    now,
                    now,
                    operation_key,
                ),
            )
            conn.execute(
                """
                UPDATE skill_lifecycle_outbox_v2
                SET state = ?, lease_expires_at_ms = NULL,
                    downstream_receipt_digest = ?, updated_at_ms = ?
                WHERE operation_id = ? AND state = 'CLAIMED'
                    AND lease_owner = ? AND fencing_token = ?
                """,
                (
                    outbox_state,
                    downstream_digest,
                    now,
                    operation_key,
                    owner,
                    fence,
                ),
            )
            terminal = self._require_canary(conn, operation_key)
            return self._canary_record(terminal)

    def activate(
        self,
        evidence: SkillActivationEvidenceV2,
        *,
        expected_activation_generation: int,
        fencing_token: int,
        idempotency_key: str,
    ) -> SkillLifecycleReceiptV2:
        if not isinstance(evidence, SkillActivationEvidenceV2):
            self._invalid("SKILL_LIFECYCLE_V2_EVIDENCE_INVALID")
        expected_generation = int(expected_activation_generation)
        fence = int(fencing_token)
        if expected_generation < 0 or fence < 1:
            self._invalid("SKILL_LIFECYCLE_V2_FENCE_INVALID")
        request = {
            "operation": "ACTIVATE",
            "evidence": evidence.to_dict(),
            "expected_activation_generation": expected_generation,
            "fencing_token": fence,
        }
        return self._activate_transaction(
            evidence=evidence,
            request=request,
            idempotency_key=idempotency_key,
            expected_generation=expected_generation,
            fencing_token=fence,
            event="ACTIVATED",
            resolve_authorities=True,
        )

    def degrade(
        self,
        *,
        bundle_id: str,
        idempotency_key: str,
        reason_code: str = "SKILL_DEGRADED_BY_GUARDIAN",
    ) -> SkillLifecycleReceiptV2:
        bundle_key = _text(bundle_id, "bundle_id")
        reason = _text(reason_code, "reason_code", 128).upper()
        request = {
            "operation": "DEGRADE",
            "bundle_id": bundle_key,
            "reason_code": reason,
        }
        request_json = _canonical(request)
        request_digest = _sha256(request_json)
        idem_digest = _sha256(
            f"DEGRADE|{bundle_key}|"
            f"{_text(idempotency_key, 'idempotency_key', 512)}"
        )
        now = int(self._clock())
        with self._transaction() as conn:
            replay = self._replay_operation(
                conn,
                idempotency_digest=idem_digest,
                request_digest=request_digest,
            )
            if replay is not None:
                return replay
            projection = conn.execute(
                """
                SELECT *
                FROM skill_active_version_projection_v2
                WHERE bundle_id = ?
                """,
                (bundle_key,),
            ).fetchone()
            if projection is None:
                self._invalid("SKILL_LIFECYCLE_V2_ACTIVE_VERSION_MISSING")
            evidence = self._evidence_by_activation(
                conn, str(projection["activation_id"])
            )
            decision = self._decision_by_activation(
                conn, evidence.activation_id
            )
            bundle = self._require_bundle(conn, bundle_key)
            if str(bundle["state"]) != "ACTIVE":
                self._invalid("SKILL_LIFECYCLE_V2_BUNDLE_NOT_ACTIVE")
            operation_id = (
                f"skill-lifecycle-operation://sha256/{idem_digest}"
            )
            self._reject_supply_bundle(
                conn,
                bundle=bundle,
                operation_id=operation_id,
                reason_code=reason,
                audit_ref=(
                    "skill-lifecycle-receipt://pending/"
                    f"{idem_digest}"
                ),
                now=now,
            )
            conn.execute(
                """
                DELETE FROM skill_active_version_projection_v2
                WHERE bundle_id = ?
                """,
                (bundle_key,),
            )
            receipt = self._build_receipt(
                operation_id=operation_id,
                event="DEGRADED",
                evidence=evidence,
                maturity_decision_digest=str(decision["decision_digest"]),
                active_projection_digest=None,
                previous_bundle_id=None,
                created_at_ms=now,
            )
            self._insert_terminal(
                conn,
                operation_id=operation_id,
                idempotency_digest=idem_digest,
                request_digest=request_digest,
                operation="DEGRADE",
                evidence=evidence,
                previous_bundle_id=None,
                request_json=request_json,
                receipt=receipt,
            )
            return receipt

    def rollback(
        self,
        *,
        current_bundle_id: str,
        target_bundle_id: str,
        expected_activation_generation: int,
        fencing_token: int,
        idempotency_key: str,
    ) -> SkillLifecycleReceiptV2:
        current_key = _text(current_bundle_id, "current_bundle_id")
        target_key = _text(target_bundle_id, "target_bundle_id")
        if current_key == target_key:
            self._invalid("SKILL_LIFECYCLE_V2_ROLLBACK_TARGET_INVALID")
        expected_generation = int(expected_activation_generation)
        fence = int(fencing_token)
        request = {
            "operation": "ROLLBACK",
            "current_bundle_id": current_key,
            "target_bundle_id": target_key,
            "expected_activation_generation": expected_generation,
            "fencing_token": fence,
        }
        request_json = _canonical(request)
        request_digest = _sha256(request_json)
        idem_digest = _sha256(
            f"ROLLBACK|{current_key}|{target_key}|"
            f"{_text(idempotency_key, 'idempotency_key', 512)}"
        )
        now = int(self._clock())
        with self._transaction() as conn:
            replay = self._replay_operation(
                conn,
                idempotency_digest=idem_digest,
                request_digest=request_digest,
            )
            if replay is not None:
                return replay
            current = self._require_bundle(conn, current_key)
            target = self._require_bundle(conn, target_key)
            if (
                str(current["bundle_kind"]) != str(target["bundle_kind"])
                or str(current["bundle_name"]) != str(target["bundle_name"])
                or str(target["state"]) != "STAGED"
                or str(current["state"]) not in {"ACTIVE", "REJECTED"}
            ):
                self._invalid(
                    "SKILL_LIFECYCLE_V2_ROLLBACK_TARGET_SCOPE_INVALID"
                )
            historical = conn.execute(
                """
                SELECT activation_id
                FROM skill_activation_evidence_v2
                WHERE bundle_id = ?
                ORDER BY created_at_ms DESC, activation_id DESC
                LIMIT 1
                """,
                (target_key,),
            ).fetchone()
            if historical is None:
                self._invalid(
                    "SKILL_LIFECYCLE_V2_ROLLBACK_EVIDENCE_MISSING"
                )
            source = self._evidence_by_activation(
                conn, str(historical["activation_id"])
            )
            source_decision = self._decision_by_activation(
                conn, source.activation_id
            )
            activation_identity = _sha256(
                "|".join(
                    (
                        source.evidence_digest,
                        current_key,
                        target_key,
                        idem_digest,
                    )
                )
            )
            evidence = SkillActivationEvidenceV2(
                activation_id=(
                    "skill-activation://rollback/"
                    f"{activation_identity}"
                ),
                workspace_id=source.workspace_id,
                bundle_id=target_key,
                bundle_digest=str(target["bundle_digest"]),
                bundle_version=str(target["bundle_version"]),
                runtime_adapter_id=source.runtime_adapter_id,
                runtime_protocol_version=source.runtime_protocol_version,
                lab_report_digest=source.lab_report_digest,
                review_id=source.review_id,
                review_status_version=source.review_status_version,
                sbom_digest=source.sbom_digest,
                signature_audit_ref=source.signature_audit_ref,
                sandbox_attestation_digest=(
                    source.sandbox_attestation_digest
                ),
                canary_receipt_digest=source.canary_receipt_digest,
                maturity_decision_id=(
                    "skill-maturity-decision://rollback/"
                    f"{activation_identity}"
                ),
                maturity_level=int(source_decision["effective_level"]),
                previous_bundle_id=current_key,
                rollback_target_bundle_id=None,
                new_capability=False,
                reason_code="SKILL_ROLLBACK_ACTIVATED",
            )
            self._assert_generation_and_fence(
                conn,
                target=target,
                expected_generation=expected_generation,
                fencing_token=fence,
            )
            return self._commit_activation(
                conn,
                evidence=evidence,
                request_json=request_json,
                request_digest=request_digest,
                idempotency_digest=idem_digest,
                expected_generation=expected_generation,
                fencing_token=fence,
                event="ROLLED_BACK",
                prior_bundle=current,
                prior_level=int(source_decision["effective_level"]),
                direction="ROLLBACK",
                now=now,
            )

    def resolve_active(
        self,
        *,
        bundle_kind: str,
        bundle_name: str,
    ) -> dict[str, Any] | None:
        kind = _text(bundle_kind, "bundle_kind", 16).upper()
        name = _text(bundle_name, "bundle_name")
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT projection.*, bundle.state,
                       bundle.activation_generation AS supply_generation,
                       bundle.bundle_digest AS supply_digest,
                       bundle.bundle_version AS supply_version
                FROM skill_active_version_projection_v2 AS projection
                JOIN skill_supply_chain_bundle AS bundle
                  ON bundle.bundle_id = projection.bundle_id
                WHERE projection.bundle_kind = ?
                  AND projection.bundle_name = ?
                """,
                (kind, name),
            ).fetchone()
            if row is None:
                return None
            self._assert_projection_row(row)
            return dict(row)

    def list_active_versions(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT projection.*, bundle.state,
                       bundle.activation_generation AS supply_generation,
                       bundle.bundle_digest AS supply_digest,
                       bundle.bundle_version AS supply_version
                FROM skill_active_version_projection_v2 AS projection
                JOIN skill_supply_chain_bundle AS bundle
                  ON bundle.bundle_id = projection.bundle_id
                ORDER BY projection.bundle_kind, projection.bundle_name
                """
            ).fetchall()
            for row in rows:
                self._assert_projection_row(row)
            return [dict(row) for row in rows]

    def _activate_transaction(
        self,
        *,
        evidence: SkillActivationEvidenceV2,
        request: Mapping[str, Any],
        idempotency_key: str,
        expected_generation: int,
        fencing_token: int,
        event: str,
        resolve_authorities: bool,
    ) -> SkillLifecycleReceiptV2:
        request_json = _canonical(request)
        request_digest = _sha256(request_json)
        idem_digest = _sha256(
            f"{event}|{evidence.bundle_id}|"
            f"{_text(idempotency_key, 'idempotency_key', 512)}"
        )
        now = int(self._clock())
        with self._transaction() as conn:
            replay = self._replay_operation(
                conn,
                idempotency_digest=idem_digest,
                request_digest=request_digest,
            )
            if replay is not None:
                return replay
            target = self._require_bundle(conn, evidence.bundle_id)
            if resolve_authorities:
                self._resolve_activation_authorities(
                    conn,
                    evidence=evidence,
                    target=target,
                    now=now,
                )
            self._assert_generation_and_fence(
                conn,
                target=target,
                expected_generation=expected_generation,
                fencing_token=fencing_token,
            )
            prior = conn.execute(
                """
                SELECT *
                FROM skill_supply_chain_bundle
                WHERE bundle_kind = ? AND bundle_name = ?
                  AND state = 'ACTIVE'
                LIMIT 1
                """,
                (str(target["bundle_kind"]), str(target["bundle_name"])),
            ).fetchone()
            prior_projection = (
                None
                if prior is None
                else conn.execute(
                    """
                    SELECT *
                    FROM skill_active_version_projection_v2
                    WHERE bundle_id = ?
                    """,
                    (str(prior["bundle_id"]),),
                ).fetchone()
            )
            prior_level = (
                None
                if prior_projection is None
                else int(prior_projection["maturity_level"])
            )
            direction = self._maturity_direction(
                prior_level=prior_level,
                requested=evidence.maturity_level,
                new_capability=evidence.new_capability,
            )
            return self._commit_activation(
                conn,
                evidence=evidence,
                request_json=request_json,
                request_digest=request_digest,
                idempotency_digest=idem_digest,
                expected_generation=expected_generation,
                fencing_token=fencing_token,
                event=event,
                prior_bundle=prior,
                prior_level=prior_level,
                direction=direction,
                now=now,
            )

    def _commit_activation(
        self,
        conn: sqlite3.Connection,
        *,
        evidence: SkillActivationEvidenceV2,
        request_json: str,
        request_digest: str,
        idempotency_digest: str,
        expected_generation: int,
        fencing_token: int,
        event: str,
        prior_bundle: sqlite3.Row | None,
        prior_level: int | None,
        direction: str,
        now: int,
    ) -> SkillLifecycleReceiptV2:
        target = self._require_bundle(conn, evidence.bundle_id)
        operation_id = (
            f"skill-lifecycle-operation://sha256/{idempotency_digest}"
        )
        self._insert_evidence(conn, evidence=evidence, now=now)
        decision = {
            "schema": "natsume.skill-capability-maturity-decision.v1",
            "decision_id": evidence.maturity_decision_id,
            "activation_id": evidence.activation_id,
            "capability_id": str(target["bundle_name"]),
            "bundle_id": evidence.bundle_id,
            "prior_level": prior_level,
            "requested_level": evidence.maturity_level,
            "effective_level": evidence.maturity_level,
            "direction": direction,
            "lab_report_digest": evidence.lab_report_digest,
            "review_id": evidence.review_id,
            "automatic_upgrade": False,
            "review_required": True,
        }
        decision_json = _canonical(decision)
        decision_digest = _sha256(decision_json)
        updated = conn.execute(
            """
            INSERT INTO skill_capability_maturity_decision_v1(
                decision_id, decision_digest, activation_id, capability_id,
                bundle_id, prior_level, requested_level, effective_level,
                direction, lab_report_digest, review_id, decision_json,
                created_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence.maturity_decision_id,
                decision_digest,
                evidence.activation_id,
                str(target["bundle_name"]),
                evidence.bundle_id,
                prior_level,
                evidence.maturity_level,
                evidence.maturity_level,
                direction,
                evidence.lab_report_digest,
                evidence.review_id,
                decision_json,
                now,
            ),
        )
        if prior_bundle is not None:
            conn.execute(
                """
                UPDATE skill_supply_chain_bundle
                SET state = 'STAGED',
                    reason_code = 'SUPERSEDED_BY_V2_ACTIVATION',
                    latest_audit_ref = ?,
                    status_version = status_version + 1,
                    updated_at_ms = ?
                WHERE bundle_id = ? AND state = 'ACTIVE'
                """,
                (
                    f"skill-lifecycle-operation://{operation_id}",
                    now,
                    str(prior_bundle["bundle_id"]),
                ),
            )
            conn.execute(
                """
                DELETE FROM skill_active_version_projection_v2
                WHERE bundle_kind = ? AND bundle_name = ?
                """,
                (
                    str(prior_bundle["bundle_kind"]),
                    str(prior_bundle["bundle_name"]),
                ),
            )
        new_generation = expected_generation + 1
        new_status_version = int(target["status_version"]) + 1
        updated = conn.execute(
            """
            UPDATE skill_supply_chain_bundle
            SET state = 'ACTIVE', reason_code = ?, latest_audit_ref = ?,
                activation_generation = ?, activation_fencing_token = ?,
                status_version = ?, updated_at_ms = ?, activated_at_ms = ?,
                rejected_at_ms = NULL
            WHERE bundle_id = ? AND state = 'STAGED'
                AND status_version = ?
            """,
            (
                evidence.reason_code,
                f"skill-lifecycle-evidence://sha256/{evidence.evidence_digest}",
                new_generation,
                fencing_token,
                new_status_version,
                now,
                now,
                evidence.bundle_id,
                int(target["status_version"]),
            ),
        )
        if updated.rowcount != 1:
            self._invalid("SKILL_LIFECYCLE_V2_STATUS_CONFLICT")
        self._insert_supply_activation_operation(
            conn,
            operation_id=operation_id,
            idempotency_digest=idempotency_digest,
            request_digest=request_digest,
            target=target,
            replaced_bundle_id=(
                None
                if prior_bundle is None
                else str(prior_bundle["bundle_id"])
            ),
            activation_generation=new_generation,
            fencing_token=fencing_token,
            status_version=new_status_version,
            audit_ref=(
                f"skill-lifecycle-evidence://sha256/"
                f"{evidence.evidence_digest}"
            ),
            reason_code=evidence.reason_code,
            now=now,
        )
        projection = {
            "bundle_kind": str(target["bundle_kind"]),
            "bundle_name": str(target["bundle_name"]),
            "bundle_id": evidence.bundle_id,
            "bundle_digest": evidence.bundle_digest,
            "bundle_version": evidence.bundle_version,
            "runtime_adapter_id": evidence.runtime_adapter_id,
            "runtime_protocol_version": evidence.runtime_protocol_version,
            "activation_id": evidence.activation_id,
            "maturity_decision_id": evidence.maturity_decision_id,
            "maturity_level": evidence.maturity_level,
            "activation_generation": new_generation,
        }
        projection_json = _canonical(projection)
        projection_digest = _sha256(projection_json)
        conn.execute(
            """
            INSERT INTO skill_active_version_projection_v2(
                bundle_kind, bundle_name, bundle_id, bundle_digest,
                bundle_version, runtime_adapter_id,
                runtime_protocol_version, activation_id,
                maturity_decision_id, maturity_level,
                activation_generation, projection_digest, updated_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                projection["bundle_kind"],
                projection["bundle_name"],
                projection["bundle_id"],
                projection["bundle_digest"],
                projection["bundle_version"],
                projection["runtime_adapter_id"],
                projection["runtime_protocol_version"],
                projection["activation_id"],
                projection["maturity_decision_id"],
                projection["maturity_level"],
                projection["activation_generation"],
                projection_digest,
                now,
            ),
        )
        receipt = self._build_receipt(
            operation_id=operation_id,
            event=event,
            evidence=evidence,
            maturity_decision_digest=decision_digest,
            active_projection_digest=projection_digest,
            previous_bundle_id=(
                None
                if prior_bundle is None
                else str(prior_bundle["bundle_id"])
            ),
            created_at_ms=now,
        )
        self._insert_terminal(
            conn,
            operation_id=operation_id,
            idempotency_digest=idempotency_digest,
            request_digest=request_digest,
            operation=(
                "ROLLBACK" if event == "ROLLED_BACK" else "ACTIVATE"
            ),
            evidence=evidence,
            previous_bundle_id=(
                None
                if prior_bundle is None
                else str(prior_bundle["bundle_id"])
            ),
            request_json=request_json,
            receipt=receipt,
        )
        return receipt

    def _insert_evidence(
        self,
        conn: sqlite3.Connection,
        *,
        evidence: SkillActivationEvidenceV2,
        now: int,
    ) -> None:
        encoded = _canonical(evidence.to_dict())
        try:
            conn.execute(
                """
                INSERT INTO skill_activation_evidence_v2(
                    activation_id, evidence_digest, workspace_id, bundle_id,
                    bundle_digest, bundle_version, runtime_adapter_id,
                    runtime_protocol_version, lab_report_digest, review_id,
                    review_status_version, sbom_digest, signature_audit_ref,
                    sandbox_attestation_digest, canary_receipt_digest,
                    maturity_decision_id, maturity_level, previous_bundle_id,
                    rollback_target_bundle_id, new_capability, reason_code,
                    evidence_json, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence.activation_id,
                    evidence.evidence_digest,
                    evidence.workspace_id,
                    evidence.bundle_id,
                    evidence.bundle_digest,
                    evidence.bundle_version,
                    evidence.runtime_adapter_id,
                    evidence.runtime_protocol_version,
                    evidence.lab_report_digest,
                    evidence.review_id,
                    evidence.review_status_version,
                    evidence.sbom_digest,
                    evidence.signature_audit_ref,
                    evidence.sandbox_attestation_digest,
                    evidence.canary_receipt_digest,
                    evidence.maturity_decision_id,
                    evidence.maturity_level,
                    evidence.previous_bundle_id,
                    evidence.rollback_target_bundle_id,
                    int(evidence.new_capability),
                    evidence.reason_code,
                    encoded,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            self._invalid(
                "SKILL_LIFECYCLE_V2_EVIDENCE_CONFLICT",
                cause=exc,
            )

    def _insert_terminal(
        self,
        conn: sqlite3.Connection,
        *,
        operation_id: str,
        idempotency_digest: str,
        request_digest: str,
        operation: str,
        evidence: SkillActivationEvidenceV2,
        previous_bundle_id: str | None,
        request_json: str,
        receipt: SkillLifecycleReceiptV2,
    ) -> None:
        conn.execute(
            """
            INSERT INTO skill_lifecycle_receipt_v2(
                receipt_id, receipt_digest, operation_id, event,
                activation_id, bundle_id, evidence_digest,
                maturity_decision_digest, active_projection_digest,
                previous_bundle_id, receipt_json, created_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                receipt.receipt_id,
                receipt.receipt_digest,
                operation_id,
                receipt.event,
                evidence.activation_id,
                evidence.bundle_id,
                evidence.evidence_digest,
                receipt.maturity_decision_digest,
                receipt.active_projection_digest,
                previous_bundle_id,
                _canonical(receipt.to_dict()),
                receipt.created_at_ms,
            ),
        )
        conn.execute(
            """
            INSERT INTO skill_lifecycle_operation_v2(
                operation_id, idempotency_digest, request_digest, operation,
                activation_id, bundle_id, previous_bundle_id, receipt_id,
                operation_json, created_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                operation_id,
                idempotency_digest,
                request_digest,
                operation,
                evidence.activation_id,
                evidence.bundle_id,
                previous_bundle_id,
                receipt.receipt_id,
                request_json,
                receipt.created_at_ms,
            ),
        )

    def _insert_supply_activation_operation(
        self,
        conn: sqlite3.Connection,
        *,
        operation_id: str,
        idempotency_digest: str,
        request_digest: str,
        target: sqlite3.Row,
        replaced_bundle_id: str | None,
        activation_generation: int,
        fencing_token: int,
        status_version: int,
        audit_ref: str,
        reason_code: str,
        now: int,
    ) -> None:
        conn.execute(
            """
            INSERT INTO skill_supply_chain_operation(
                operation_id, idempotency_digest, request_digest, operation,
                bundle_id, previous_state, new_state, replaced_bundle_id,
                activation_generation, activation_fencing_token,
                status_version, reason_code, audit_ref, created_at_ms
            ) VALUES (?, ?, ?, 'ACTIVATE', ?, 'STAGED', 'ACTIVE', ?, ?,
                ?, ?, ?, ?, ?)
            """,
            (
                operation_id,
                _sha256_ref(f"supply|{idempotency_digest}"),
                _sha256_ref(f"supply|{request_digest}"),
                str(target["bundle_id"]),
                replaced_bundle_id,
                activation_generation,
                fencing_token,
                status_version,
                reason_code,
                audit_ref,
                now,
            ),
        )

    def _reject_supply_bundle(
        self,
        conn: sqlite3.Connection,
        *,
        bundle: sqlite3.Row,
        operation_id: str,
        reason_code: str,
        audit_ref: str,
        now: int,
    ) -> None:
        status_version = int(bundle["status_version"]) + 1
        updated = conn.execute(
            """
            UPDATE skill_supply_chain_bundle
            SET state = 'REJECTED', reason_code = ?, latest_audit_ref = ?,
                status_version = ?, updated_at_ms = ?, rejected_at_ms = ?
            WHERE bundle_id = ? AND state = 'ACTIVE'
                AND status_version = ?
            """,
            (
                reason_code,
                audit_ref,
                status_version,
                now,
                now,
                str(bundle["bundle_id"]),
                int(bundle["status_version"]),
            ),
        )
        if updated.rowcount != 1:
            self._invalid("SKILL_LIFECYCLE_V2_STATUS_CONFLICT")
        digest = _sha256(operation_id)
        conn.execute(
            """
            INSERT INTO skill_supply_chain_operation(
                operation_id, idempotency_digest, request_digest, operation,
                bundle_id, previous_state, new_state, replaced_bundle_id,
                activation_generation, activation_fencing_token,
                status_version, reason_code, audit_ref, created_at_ms
            ) VALUES (?, ?, ?, 'REJECT', ?, 'ACTIVE', 'REJECTED', NULL,
                ?, ?, ?, ?, ?, ?)
            """,
            (
                operation_id,
                _sha256_ref(f"supply-reject-idem|{digest}"),
                _sha256_ref(f"supply-reject-request|{digest}"),
                str(bundle["bundle_id"]),
                int(bundle["activation_generation"]),
                int(bundle["activation_fencing_token"]),
                status_version,
                reason_code,
                audit_ref,
                now,
            ),
        )

    def _assert_generation_and_fence(
        self,
        conn: sqlite3.Connection,
        *,
        target: sqlite3.Row,
        expected_generation: int,
        fencing_token: int,
    ) -> None:
        generation = conn.execute(
            """
            SELECT COALESCE(MAX(activation_generation), 0) AS generation,
                   COALESCE(MAX(activation_fencing_token), 0) AS fence
            FROM skill_supply_chain_bundle
            WHERE bundle_kind = ? AND bundle_name = ?
            """,
            (str(target["bundle_kind"]), str(target["bundle_name"])),
        ).fetchone()
        if (
            int(generation["generation"]) != expected_generation
            or fencing_token <= int(generation["fence"])
        ):
            self._invalid("SKILL_LIFECYCLE_V2_FENCE_CONFLICT")

    def _replay_operation(
        self,
        conn: sqlite3.Connection,
        *,
        idempotency_digest: str,
        request_digest: str,
    ) -> SkillLifecycleReceiptV2 | None:
        row = conn.execute(
            """
            SELECT operation.request_digest, receipt.receipt_json
            FROM skill_lifecycle_operation_v2 AS operation
            JOIN skill_lifecycle_receipt_v2 AS receipt
              ON receipt.receipt_id = operation.receipt_id
            WHERE operation.idempotency_digest = ?
            """,
            (idempotency_digest,),
        ).fetchone()
        if row is None:
            return None
        if str(row["request_digest"]) != request_digest:
            self._invalid("SKILL_LIFECYCLE_V2_IDEMPOTENCY_DRIFT")
        try:
            return SkillLifecycleReceiptV2.from_mapping(
                json.loads(str(row["receipt_json"]))
            )
        except (json.JSONDecodeError, SkillLifecycleV2Error) as exc:
            raise SkillLifecycleV2RepositoryError(
                "SKILL_LIFECYCLE_V2_RECEIPT_DRIFT"
            ) from exc

    def _build_receipt(
        self,
        *,
        operation_id: str,
        event: str,
        evidence: SkillActivationEvidenceV2,
        maturity_decision_digest: str,
        active_projection_digest: str | None,
        previous_bundle_id: str | None,
        created_at_ms: int,
    ) -> SkillLifecycleReceiptV2:
        identity = _sha256(
            "|".join(
                (
                    operation_id,
                    event,
                    evidence.evidence_digest,
                    maturity_decision_digest,
                    active_projection_digest or "",
                )
            )
        )
        return SkillLifecycleReceiptV2(
            receipt_id=(
                f"skill-lifecycle-receipt://{event.lower()}/{identity}"
            ),
            operation_id=operation_id,
            event=event,
            activation_id=evidence.activation_id,
            bundle_id=evidence.bundle_id,
            evidence_digest=evidence.evidence_digest,
            maturity_decision_digest=maturity_decision_digest,
            active_projection_digest=active_projection_digest,
            previous_bundle_id=previous_bundle_id,
            created_at_ms=created_at_ms,
        )

    def _freeze_expired_claim(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        now: int,
    ) -> None:
        operation_id = str(row["operation_id"])
        result = {
            "schema": "natsume.skill-canary-result.v1",
            "operation_id": operation_id,
            "outcome": "UNKNOWN",
            "reason_code": "SKILL_CANARY_LEASE_EXPIRED",
            "fencing_token": int(row["fencing_token"]),
        }
        result_json = _canonical(result)
        result_digest = _sha256(result_json)
        downstream = _sha256(
            f"unknown|{operation_id}|{int(row['fencing_token'])}"
        )
        conn.execute(
            """
            UPDATE skill_lifecycle_canary_operation_v2
            SET status = 'RECONCILIATION_REQUIRED',
                downstream_receipt_digest = ?, result_digest = ?,
                reason_code = 'SKILL_CANARY_RECONCILIATION_REQUIRED',
                result_json = ?, updated_at_ms = ?, terminal_at_ms = ?
            WHERE operation_id = ? AND status = 'PENDING'
            """,
            (
                downstream,
                result_digest,
                result_json,
                now,
                now,
                operation_id,
            ),
        )
        conn.execute(
            """
            UPDATE skill_lifecycle_outbox_v2
            SET state = 'UNKNOWN', lease_expires_at_ms = NULL,
                downstream_receipt_digest = ?, updated_at_ms = ?
            WHERE outbox_id = ? AND state = 'CLAIMED'
            """,
            (downstream, now, str(row["outbox_id"])),
        )

    @staticmethod
    def _maturity_direction(
        *,
        prior_level: int | None,
        requested: int,
        new_capability: bool,
    ) -> str:
        if prior_level is None:
            return "NEW" if new_capability else "MAINTAIN"
        if requested > prior_level:
            return "UPGRADE"
        if requested < prior_level:
            return "DOWNGRADE"
        return "MAINTAIN"

    def _require_canary(
        self, conn: sqlite3.Connection, operation_id: str
    ) -> sqlite3.Row:
        row = conn.execute(
            """
            SELECT *
            FROM skill_lifecycle_canary_operation_v2
            WHERE operation_id = ?
            """,
            (operation_id,),
        ).fetchone()
        if row is None:
            self._invalid("SKILL_LIFECYCLE_V2_CANARY_NOT_FOUND")
        return row

    @staticmethod
    def _invalid(
        reason_code: str,
        detail: str = "",
        *,
        cause: BaseException | None = None,
    ) -> None:
        error = SkillLifecycleV2RepositoryError(reason_code, detail)
        if cause is None:
            raise error
        raise error from cause


__all__ = [
    "SKILL_LIFECYCLE_V2_MIGRATION_ASSET_KEY",
    "SQLiteSkillLifecycleV2Repository",
    "SkillLifecycleV2RepositoryError",
]
