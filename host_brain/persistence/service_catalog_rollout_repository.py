"""SQLite persistence owner for the five-stage Service Catalog rollout."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping
from uuid import uuid4

from host_brain.contracts.service_catalog import (
    _STAGE_RANK,
    _TERMINAL_EVENTS,
    _canonical,
    _digest,
    _now_ms,
    _stage,
    _text,
    _validate_receipt_transition,
    catalog_operation_identity_digest,
    CatalogCoordinatorAuthorityV1,
    CatalogRolloutOperationV1,
    ServiceCatalogRolloutError,
)


class SQLiteServiceCatalogRolloutRepository:
    """SQLite operation-first owner with digest-chain verification."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        if not self._db_path or self._db_path == ":memory:":
            raise ServiceCatalogRolloutError(
                "durable Service Catalog rollout requires a file-backed SQLite database"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def acquire_authority(
        self,
        *,
        runtime_scope_id: str,
        owner_id: str,
        lease_ms: int = 15_000,
        now_ms: int | None = None,
    ) -> CatalogCoordinatorAuthorityV1 | None:
        scope = _text(runtime_scope_id, "runtime_scope_id")
        owner = _text(owner_id, "owner_id")
        now = int(_now_ms() if now_ms is None else now_ms)
        if type(lease_ms) is not int or not 1_000 <= lease_ms <= 300_000:
            raise ServiceCatalogRolloutError("lease_ms is invalid")
        lease_id = "catalog-rollout-lease-" + uuid4().hex
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM service_catalog_rollout_coordinator_lease "
                "WHERE runtime_scope_id = ?",
                (scope,),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO service_catalog_rollout_coordinator_lease "
                    "(runtime_scope_id, owner_id, lease_id, fencing_token, issued_at_ms, "
                    "expires_at_ms, released_at_ms) VALUES (?, ?, ?, 1, ?, ?, NULL)",
                    (scope, owner, lease_id, now, now + lease_ms),
                )
            else:
                active = row["released_at_ms"] is None and int(row["expires_at_ms"]) > now
                if active:
                    return None
                fence = int(row["fencing_token"]) + 1
                updated = connection.execute(
                    "UPDATE service_catalog_rollout_coordinator_lease SET "
                    "owner_id = ?, lease_id = ?, fencing_token = ?, issued_at_ms = ?, "
                    "expires_at_ms = ?, released_at_ms = NULL WHERE runtime_scope_id = ? "
                    "AND fencing_token = ? AND (released_at_ms IS NOT NULL OR expires_at_ms <= ?)",
                    (owner, lease_id, fence, now, now + lease_ms, scope, fence - 1, now),
                )
                if updated.rowcount != 1:
                    return None
            acquired = connection.execute(
                "SELECT * FROM service_catalog_rollout_coordinator_lease "
                "WHERE runtime_scope_id = ?",
                (scope,),
            ).fetchone()
        assert acquired is not None
        return self._authority_from_row(acquired)

    def renew_authority(
        self,
        authority: CatalogCoordinatorAuthorityV1,
        *,
        lease_ms: int = 15_000,
        now_ms: int | None = None,
    ) -> CatalogCoordinatorAuthorityV1 | None:
        now = int(_now_ms() if now_ms is None else now_ms)
        with self._connection() as connection:
            updated = connection.execute(
                "UPDATE service_catalog_rollout_coordinator_lease SET expires_at_ms = ? "
                "WHERE runtime_scope_id = ? AND owner_id = ? AND lease_id = ? "
                "AND fencing_token = ? AND released_at_ms IS NULL AND expires_at_ms > ?",
                (
                    now + int(lease_ms),
                    authority.runtime_scope_id,
                    authority.owner_id,
                    authority.lease_id,
                    authority.fencing_token,
                    now,
                ),
            )
            if updated.rowcount != 1:
                return None
            row = connection.execute(
                "SELECT * FROM service_catalog_rollout_coordinator_lease "
                "WHERE runtime_scope_id = ?",
                (authority.runtime_scope_id,),
            ).fetchone()
        return self._authority_from_row(row) if row is not None else None

    def release_authority(
        self,
        authority: CatalogCoordinatorAuthorityV1,
        *,
        now_ms: int | None = None,
    ) -> bool:
        now = int(_now_ms() if now_ms is None else now_ms)
        with self._connection() as connection:
            updated = connection.execute(
                "UPDATE service_catalog_rollout_coordinator_lease "
                "SET released_at_ms = CASE WHEN ? < issued_at_ms THEN issued_at_ms ELSE ? END, "
                "expires_at_ms = CASE WHEN ? <= issued_at_ms THEN issued_at_ms + 1 ELSE ? END "
                "WHERE runtime_scope_id = ? AND owner_id = ? AND lease_id = ? "
                "AND fencing_token = ? AND released_at_ms IS NULL",
                (
                    now,
                    now,
                    now,
                    now,
                    authority.runtime_scope_id,
                    authority.owner_id,
                    authority.lease_id,
                    authority.fencing_token,
                ),
            )
        return updated.rowcount == 1

    def register_operation(
        self,
        operation: CatalogRolloutOperationV1,
        authority: CatalogCoordinatorAuthorityV1,
        *,
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        now = int(_now_ms() if now_ms is None else now_ms)
        existing = self.get_operation(operation.operation_id)
        if existing is not None:
            expected = operation.immutable_payload(authority, created_at_ms=int(existing["created_at_ms"]))
            if not _same_operation(existing, expected):
                raise ServiceCatalogRolloutError("operation replay identity drift")
            return existing
        payload = operation.immutable_payload(authority, created_at_ms=now)
        operation_digest = _digest(payload)
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO service_catalog_rollout_operation "
                    "(operation_id, operation_digest, runtime_scope_id, coordinator_owner_id, "
                    "coordinator_lease_id, coordinator_fencing_token, catalog_snapshot_digest, "
                    "candidate_owner_id, stage, stage_rank, endpoint_ids_json, rollback_ref, "
                    "created_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        operation.operation_id,
                        operation_digest,
                        authority.runtime_scope_id,
                        authority.owner_id,
                        authority.lease_id,
                        authority.fencing_token,
                        operation.catalog_snapshot_digest,
                        operation.candidate_owner_id,
                        operation.stage.value,
                        _STAGE_RANK[operation.stage],
                        _canonical(list(operation.endpoint_ids)),
                        operation.rollback_ref,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ServiceCatalogRolloutError(str(exc)) from exc
        registered = self.get_operation(operation.operation_id)
        assert registered is not None
        return registered

    def get_operation(self, operation_id: str) -> dict[str, Any] | None:
        resolved = _text(operation_id, "operation_id")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM service_catalog_rollout_operation WHERE operation_id = ?",
                (resolved,),
            ).fetchone()
        if row is None:
            return None
        operation = dict(row)
        payload = _operation_payload(operation)
        if operation["operation_digest"] != _digest(payload):
            raise ServiceCatalogRolloutError("operation digest mismatch")
        resolved_stage = _stage(str(operation["stage"]))
        if int(operation["stage_rank"]) != _STAGE_RANK[resolved_stage]:
            raise ServiceCatalogRolloutError("operation stage/rank identity drift")
        operation["endpoint_ids"] = tuple(json.loads(operation.pop("endpoint_ids_json")))
        return operation

    def append_receipt(
        self,
        operation_id: str,
        *,
        event_type: str,
        status: str,
        payload: Mapping[str, Any],
        authority: CatalogCoordinatorAuthorityV1 | None = None,
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        operation = self.get_operation(operation_id)
        if operation is None:
            raise ServiceCatalogRolloutError("rollout operation does not exist")
        event = _text(event_type, "event_type", 128).upper()
        resolved_status = _text(status, "status", 128).upper()
        body = dict(payload)
        if event == "ACTIVATION_STARTED":
            self._validate_terminal_started_payload(operation, body)
        receipt_authority = authority or self._operation_authority(operation)
        if receipt_authority.runtime_scope_id != str(operation["runtime_scope_id"]):
            raise ServiceCatalogRolloutError("receipt authority runtime scope drift")
        authority_payload = {
            "runtime_scope_id": receipt_authority.runtime_scope_id,
            "owner_id": receipt_authority.owner_id,
            "lease_id": receipt_authority.lease_id,
            "fencing_token": receipt_authority.fencing_token,
        }
        event_key = f"{operation_id}:{event}"
        now = int(_now_ms() if now_ms is None else now_ms)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM service_catalog_rollout_receipt WHERE event_key = ?",
                (event_key,),
            ).fetchone()
            if existing is not None:
                decoded = json.loads(existing["payload_json"])
                if existing["status"] != resolved_status or decoded != body:
                    raise ServiceCatalogRolloutError("receipt replay identity drift")
                result = dict(existing)
                result["payload"] = decoded
                return result
            latest = connection.execute(
                "SELECT event_type, status, receipt_digest "
                "FROM service_catalog_rollout_receipt "
                "WHERE operation_id = ? ORDER BY receipt_seq DESC LIMIT 1",
                (operation_id,),
            ).fetchone()
            _validate_receipt_transition(
                event=event,
                status=resolved_status,
                previous_event=(str(latest["event_type"]) if latest is not None else None),
                previous_status=(str(latest["status"]) if latest is not None else None),
            )
            previous = str(latest["receipt_digest"]) if latest is not None else None
            receipt_payload = {
                "operation_id": operation_id,
                "event_type": event,
                "status": resolved_status,
                "coordinator_authority": authority_payload,
                "payload": body,
                "previous_digest": previous,
                "created_at_ms": now,
            }
            receipt_digest = _digest(receipt_payload)
            receipt_id = "catalog-rollout-receipt-" + uuid4().hex
            try:
                connection.execute(
                    "INSERT INTO service_catalog_rollout_receipt "
                    "(receipt_id, operation_id, event_key, event_type, status, "
                    "runtime_scope_id, coordinator_owner_id, coordinator_lease_id, "
                    "coordinator_fencing_token, payload_json, previous_digest, receipt_digest, "
                    "created_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        receipt_id,
                        operation_id,
                        event_key,
                        event,
                        resolved_status,
                        receipt_authority.runtime_scope_id,
                        receipt_authority.owner_id,
                        receipt_authority.lease_id,
                        receipt_authority.fencing_token,
                        _canonical(body),
                        previous,
                        receipt_digest,
                        now,
                    ),
                )
                if event == "ACTIVATION_STARTED":
                    self._advance_head(connection, operation, body, now)
            except sqlite3.IntegrityError as exc:
                raise ServiceCatalogRolloutError(str(exc)) from exc
        return {
            "receipt_id": receipt_id,
            "operation_id": operation_id,
            "event_key": event_key,
            "event_type": event,
            "status": resolved_status,
            "coordinator_authority": authority_payload,
            "payload": body,
            "previous_digest": previous,
            "receipt_digest": receipt_digest,
            "created_at_ms": now,
        }

    @staticmethod
    def _validate_terminal_started_payload(
        operation: Mapping[str, Any],
        body: Mapping[str, Any],
    ) -> None:
        health_identity = body.get("health_identity")
        runtime_authority = body.get("runtime_authority")
        activation_identity = body.get("activation_identity")
        if (
            not isinstance(health_identity, Mapping)
            or not isinstance(runtime_authority, Mapping)
            or not isinstance(activation_identity, Mapping)
        ):
            raise ServiceCatalogRolloutError(
                "service catalog terminal authority is incomplete"
            )
        operation_id = str(operation["operation_id"])
        expected_operation_digest = catalog_operation_identity_digest(operation_id)
        if (
            str(body.get("catalog_snapshot_digest") or "")
            != str(operation["catalog_snapshot_digest"])
            or str(body.get("candidate_owner_id") or "")
            != str(operation["candidate_owner_id"])
            or str(body.get("stage") or "") != str(operation["stage"])
            or str(body.get("rollback_ref") or "")
            != str(operation["rollback_ref"])
            or str(activation_identity.get("operation_id") or "") != operation_id
            or str(activation_identity.get("operation_id_digest") or "")
            != expected_operation_digest
            or str(activation_identity.get("catalog_snapshot_digest") or "")
            != str(operation["catalog_snapshot_digest"])
            or str(activation_identity.get("candidate_owner_id") or "")
            != str(operation["candidate_owner_id"])
            or str(activation_identity.get("stage") or "")
            != str(operation["stage"])
            or str(runtime_authority.get("runtime_scope_id") or "")
            != str(operation["runtime_scope_id"])
            or str(health_identity.get("endpoint_id") or "")
            not in tuple(str(item) for item in operation["endpoint_ids"])
            or str(health_identity.get("owner_id") or "")
            != str(operation["candidate_owner_id"])
            or not str(health_identity.get("protocol_id") or "").strip()
            or not str(health_identity.get("protocol_version") or "").strip()
        ):
            raise ServiceCatalogRolloutError(
                "service catalog activation identity drift"
            )

    def list_receipts(self, operation_id: str) -> tuple[dict[str, Any], ...]:
        operation = self.get_operation(operation_id)
        if operation is None:
            return ()
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM service_catalog_rollout_receipt "
                "WHERE operation_id = ? ORDER BY receipt_seq",
                (operation_id,),
            ).fetchall()
        output: list[dict[str, Any]] = []
        previous: str | None = None
        for row in rows:
            item = dict(row)
            body = json.loads(item["payload_json"])
            if item["event_type"] == "ACTIVATION_STARTED":
                self._validate_terminal_started_payload(operation, body)
            expected = _digest(
                {
                    "operation_id": operation_id,
                    "event_type": item["event_type"],
                    "status": item["status"],
                    "coordinator_authority": {
                        "runtime_scope_id": item["runtime_scope_id"],
                        "owner_id": item["coordinator_owner_id"],
                        "lease_id": item["coordinator_lease_id"],
                        "fencing_token": int(item["coordinator_fencing_token"]),
                    },
                    "payload": body,
                    "previous_digest": previous,
                    "created_at_ms": int(item["created_at_ms"]),
                }
            )
            if item["previous_digest"] != previous or item["receipt_digest"] != expected:
                raise ServiceCatalogRolloutError("receipt digest chain mismatch")
            previous = item["receipt_digest"]
            item["payload"] = body
            output.append(item)
        return tuple(output)

    def operation_state(self, operation_id: str) -> str:
        receipts = self.list_receipts(operation_id)
        if not receipts:
            return "REGISTERED"
        latest = receipts[-1]
        return str(latest["status"])

    def find_pending(
        self,
        *,
        runtime_scope_id: str,
        candidate_owner_id: str,
        catalog_snapshot_digest: str,
        stage: CatalogMode | str,
    ) -> dict[str, Any] | None:
        resolved_stage = _stage(stage).value
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT operation_id FROM service_catalog_rollout_operation "
                "WHERE runtime_scope_id = ? AND candidate_owner_id = ? "
                "AND catalog_snapshot_digest = ? AND stage = ? "
                "ORDER BY created_at_ms DESC",
                (
                    _text(runtime_scope_id, "runtime_scope_id"),
                    _text(candidate_owner_id, "candidate_owner_id"),
                    _text(catalog_snapshot_digest, "catalog_snapshot_digest"),
                    resolved_stage,
                ),
            ).fetchall()
        for row in rows:
            receipts = self.list_receipts(str(row["operation_id"]))
            if receipts and receipts[-1]["event_type"] not in _TERMINAL_EVENTS:
                return self.get_operation(str(row["operation_id"]))
        return None

    def latest_terminal_receipt(
        self,
        *,
        runtime_scope_id: str,
        candidate_owner_id: str,
    ) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT receipt.* FROM service_catalog_rollout_receipt AS receipt "
                "JOIN service_catalog_rollout_operation AS operation "
                "ON operation.operation_id = receipt.operation_id "
                "WHERE operation.runtime_scope_id = ? AND operation.candidate_owner_id = ? "
                "AND receipt.event_type IN ('ACTIVATION_STARTED', 'ACTIVATION_FAILED', "
                "'ACTIVATION_ROLLED_BACK') ORDER BY receipt.receipt_seq DESC LIMIT 1",
                (
                    _text(runtime_scope_id, "runtime_scope_id"),
                    _text(candidate_owner_id, "candidate_owner_id"),
                ),
            ).fetchone()
        if row is None:
            return None
        receipt = dict(row)
        receipt["payload"] = json.loads(receipt["payload_json"])
        self.list_receipts(str(receipt["operation_id"]))
        return receipt

    def terminal_receipt(self, operation_id: str) -> dict[str, Any] | None:
        operation = self.get_operation(operation_id)
        if operation is None:
            return None
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM service_catalog_rollout_receipt "
                "WHERE operation_id = ? AND event_type IN "
                "('ACTIVATION_STARTED', 'ACTIVATION_FAILED', 'ACTIVATION_ROLLED_BACK') "
                "ORDER BY receipt_seq DESC LIMIT 1",
                (operation_id,),
            ).fetchone()
        if row is None:
            return None
        receipt = dict(row)
        receipt["payload"] = json.loads(receipt["payload_json"])
        self.list_receipts(operation_id)
        return receipt

    def rollout_head(
        self,
        *,
        runtime_scope_id: str,
        candidate_owner_id: str,
    ) -> dict[str, Any] | None:
        """Return a verified durable rollout head.

        ``service_catalog_rollout_head`` is only a mutable projection.  Callers
        must never use the row by itself as promotion authority, so this read
        reopens the immutable operation and terminal receipt and verifies the
        health-identity digest before returning it.
        """

        scope = _text(runtime_scope_id, "runtime_scope_id")
        candidate = _text(candidate_owner_id, "candidate_owner_id")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM service_catalog_rollout_head "
                "WHERE runtime_scope_id = ? AND candidate_owner_id = ?",
                (scope, candidate),
            ).fetchone()
            latest_started = connection.execute(
                "SELECT receipt.operation_id, receipt.created_at_ms "
                "FROM service_catalog_rollout_receipt AS receipt "
                "JOIN service_catalog_rollout_operation AS operation "
                "ON operation.operation_id = receipt.operation_id "
                "WHERE operation.runtime_scope_id = ? "
                "AND operation.candidate_owner_id = ? "
                "AND receipt.event_type = 'ACTIVATION_STARTED' "
                "ORDER BY receipt.receipt_seq DESC LIMIT 1",
                (scope, candidate),
            ).fetchone()
        if row is None:
            return None
        if latest_started is None:
            raise ServiceCatalogRolloutError("rollout head has no terminal activation")
        head = dict(row)
        operation_id = str(head["activation_operation_id"])
        operation = self.get_operation(operation_id)
        receipt = self.terminal_receipt(operation_id)
        if operation is None or receipt is None or receipt["event_type"] != "ACTIVATION_STARTED":
            raise ServiceCatalogRolloutError("rollout head terminal authority is unavailable")
        payload = receipt.get("payload")
        health_identity = payload.get("health_identity") if isinstance(payload, Mapping) else None
        if not isinstance(health_identity, Mapping):
            raise ServiceCatalogRolloutError("rollout head health identity is unavailable")
        if (
            str(operation["runtime_scope_id"]) != scope
            or str(operation["candidate_owner_id"]) != candidate
            or str(operation["stage"]) != str(head["stage"])
            or int(operation["stage_rank"]) != int(head["stage_rank"])
            or str(operation["catalog_snapshot_digest"])
            != str(head["catalog_snapshot_digest"])
            or _digest(dict(health_identity)) != str(head["health_identity_digest"])
            or str(latest_started["operation_id"]) != operation_id
            or int(latest_started["created_at_ms"]) != int(head["activated_at_ms"])
        ):
            raise ServiceCatalogRolloutError("rollout head projection identity drift")
        head["operation"] = operation
        head["terminal_receipt"] = receipt
        return head

    def _advance_head(
        self,
        connection: sqlite3.Connection,
        operation: Mapping[str, Any],
        body: Mapping[str, Any],
        now_ms: int,
    ) -> None:
        head = connection.execute(
            "SELECT * FROM service_catalog_rollout_head "
            "WHERE runtime_scope_id = ? AND candidate_owner_id = ?",
            (operation["runtime_scope_id"], operation["candidate_owner_id"]),
        ).fetchone()
        rank = int(operation["stage_rank"])
        if head is None and rank != 0:
            raise ServiceCatalogRolloutError("service catalog rollout must start at AUDIT")
        if head is not None and (rank < int(head["stage_rank"]) or rank > int(head["stage_rank"]) + 1):
            raise ServiceCatalogRolloutError("service catalog rollout stage regression or skip")
        health_identity = body.get("health_identity")
        if not isinstance(health_identity, Mapping):
            raise ServiceCatalogRolloutError("health identity is missing")
        connection.execute(
            "INSERT INTO service_catalog_rollout_head "
            "(runtime_scope_id, candidate_owner_id, stage, stage_rank, "
            "activation_operation_id, catalog_snapshot_digest, health_identity_digest, "
            "activated_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(runtime_scope_id, candidate_owner_id) DO UPDATE SET "
            "stage = excluded.stage, stage_rank = excluded.stage_rank, "
            "activation_operation_id = excluded.activation_operation_id, "
            "catalog_snapshot_digest = excluded.catalog_snapshot_digest, "
            "health_identity_digest = excluded.health_identity_digest, "
            "activated_at_ms = excluded.activated_at_ms",
            (
                operation["runtime_scope_id"],
                operation["candidate_owner_id"],
                operation["stage"],
                rank,
                operation["operation_id"],
                operation["catalog_snapshot_digest"],
                _digest(dict(health_identity)),
                now_ms,
            ),
        )

    @staticmethod
    def _authority_from_row(row: Mapping[str, Any]) -> CatalogCoordinatorAuthorityV1:
        return CatalogCoordinatorAuthorityV1(
            runtime_scope_id=str(row["runtime_scope_id"]),
            owner_id=str(row["owner_id"]),
            lease_id=str(row["lease_id"]),
            fencing_token=int(row["fencing_token"]),
            issued_at_ms=int(row["issued_at_ms"]),
            expires_at_ms=int(row["expires_at_ms"]),
        )

    def _operation_authority(
        self,
        operation: Mapping[str, Any],
    ) -> CatalogCoordinatorAuthorityV1:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM service_catalog_rollout_coordinator_lease "
                "WHERE runtime_scope_id = ?",
                (operation["runtime_scope_id"],),
            ).fetchone()
        if (
            row is None
            or str(row["owner_id"]) != str(operation["coordinator_owner_id"])
            or str(row["lease_id"]) != str(operation["coordinator_lease_id"])
            or int(row["fencing_token"])
            != int(operation["coordinator_fencing_token"])
        ):
            raise ServiceCatalogRolloutError(
                "operation coordinator authority has been superseded"
            )
        return self._authority_from_row(row)


def _operation_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "operation_id": str(row["operation_id"]),
        "runtime_scope_id": str(row["runtime_scope_id"]),
        "coordinator_owner_id": str(row["coordinator_owner_id"]),
        "coordinator_lease_id": str(row["coordinator_lease_id"]),
        "coordinator_fencing_token": int(row["coordinator_fencing_token"]),
        "catalog_snapshot_digest": str(row["catalog_snapshot_digest"]),
        "candidate_owner_id": str(row["candidate_owner_id"]),
        "stage": str(row["stage"]),
        "stage_rank": int(row["stage_rank"]),
        "endpoint_ids": list(json.loads(str(row["endpoint_ids_json"]))),
        "rollback_ref": str(row["rollback_ref"]),
        "created_at_ms": int(row["created_at_ms"]),
    }


def _same_operation(existing: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    comparable = {
        "operation_id": str(existing["operation_id"]),
        "runtime_scope_id": str(existing["runtime_scope_id"]),
        "coordinator_owner_id": str(existing["coordinator_owner_id"]),
        "coordinator_lease_id": str(existing["coordinator_lease_id"]),
        "coordinator_fencing_token": int(existing["coordinator_fencing_token"]),
        "catalog_snapshot_digest": str(existing["catalog_snapshot_digest"]),
        "candidate_owner_id": str(existing["candidate_owner_id"]),
        "stage": str(existing["stage"]),
        "stage_rank": int(existing["stage_rank"]),
        "endpoint_ids": list(existing["endpoint_ids"]),
        "rollback_ref": str(existing["rollback_ref"]),
        "created_at_ms": int(existing["created_at_ms"]),
    }
    return comparable == expected


__all__ = ["SQLiteServiceCatalogRolloutRepository"]
