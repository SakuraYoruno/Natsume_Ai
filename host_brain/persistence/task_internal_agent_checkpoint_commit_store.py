"""Narrow atomic recovery for a seal-bound Internal Agent checkpoint.

This is not a generic reconciliation bypass.  It accepts only the production
Internal/Agent route, validates the complete canonical worker projection in
the same SQLite transaction, and then commits that exact projection as the
already-completed operation checkpoint.  Other routes and effects continue to
require the normal signed unknown-effect reconciliation path.
"""

from __future__ import annotations

from collections.abc import Mapping
import re
import sqlite3
from typing import Any

from host_brain.persistence.task_store_types import (
    IdempotencyStatus,
    OutboxMessage,
    TASK_STORE_CAS_CONFLICT,
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_INVALID,
    TaskStoreError,
)
from host_brain.persistence.task_record_codec import (
    _loads_object,
)
from host_brain.persistence.worker_projection_repository import (
    WORKER_PROJECTION_REPLAY_DRIFT,
    WorkerProjectionStore,
    WorkerProjectionStoreError,
    validate_worker_projection_payload,
    validate_worker_projection_record,
)


_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class TaskInternalAgentCheckpointCommitStoreMixin:
    """Atomic commit of one recovered Internal Agent checkpoint."""

    def _commit_internal_agent_recovered_checkpoint_uow(
        self,
        reservation_id: str,
        *,
        worker_projection_store: WorkerProjectionStore,
        attempt_id: str,
        lease_id: str,
        owner_id: str,
        fencing_token: int,
        expected_version: int,
        projection: Mapping[str, Any],
        stage_ref: str,
        seal_digest: str,
        outbox: OutboxMessage,
    ) -> dict[str, Any]:
        projection_value = dict(projection or {})
        projection_id = str(projection_value.get("projection_id") or "").strip()
        stage = str(stage_ref or "").strip()
        seal = str(seal_digest or "").strip().lower()
        if (
            not projection_id.startswith("worker-projection://")
            or not stage.startswith("agent-stage://")
            or _SHA256.fullmatch(seal) is None
            or type(fencing_token) is not int
            or fencing_token < 1
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "Internal Agent recovered checkpoint identity is invalid",
            )

        ts_ms = self._now()
        with self._transaction() as conn:
            reservation = self._require_operation_reservation(
                conn,
                str(reservation_id),
            )
            self._operation_record(reservation)
            status = str(reservation["status"])

            if status == IdempotencyStatus.COMMITTED.value:
                if (
                    str(reservation["receipt_ref"] or "") != projection_id
                    or int(reservation["status_version"])
                    != int(expected_version) + 1
                ):
                    raise TaskStoreError(
                        TASK_STORE_CAS_CONFLICT,
                        "Internal Agent checkpoint replay differs from COMMITTED authority",
                    )
                self._validate_internal_agent_recovery_uow(
                    conn,
                    worker_projection_store=worker_projection_store,
                    reservation=reservation,
                    attempt_id=attempt_id,
                    lease_id=lease_id,
                    owner_id=owner_id,
                    fencing_token=fencing_token,
                    projection=projection_value,
                    stage_ref=stage,
                    seal_digest=seal,
                    insert_projection=False,
                    ts_ms=ts_ms,
                )
                self._assert_mutation_outbox_replay(
                    conn,
                    outbox,
                    expected_aggregate=(
                        "OPERATION_RESERVATION",
                        str(reservation_id),
                        int(reservation["status_version"]),
                    ),
                    expected_event_type="OPERATION_COMMITTED",
                    expected_attempt_id=attempt_id,
                )
                return self._operation_record(reservation)

            if status != IdempotencyStatus.RECOVERY_REQUIRED.value:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "Internal Agent sealed recovery requires RECOVERY_REQUIRED authority",
                )
            self._require_version(
                reservation,
                int(expected_version),
                subject=f"OperationReservation {reservation_id}",
            )
            self._validate_internal_agent_recovery_uow(
                conn,
                worker_projection_store=worker_projection_store,
                reservation=reservation,
                attempt_id=attempt_id,
                lease_id=lease_id,
                owner_id=owner_id,
                fencing_token=fencing_token,
                projection=projection_value,
                stage_ref=stage,
                seal_digest=seal,
                insert_projection=True,
                ts_ms=ts_ms,
            )
            self._require_outbox_attempt(outbox, attempt_id)
            cursor = conn.execute(
                """
                UPDATE task_runtime_idempotency_reservation
                SET status = 'COMMITTED', receipt_ref = ?,
                    status_version = status_version + 1, updated_ts_ms = ?
                WHERE reservation_id = ? AND status_version = ?
                  AND status IN ('RESERVED','RECOVERY_REQUIRED')
                  AND attempt_id = ? AND lease_id = ? AND owner_id = ?
                  AND fencing_token = ? AND receipt_ref IS NULL
                """,
                (
                    projection_id,
                    ts_ms,
                    str(reservation_id),
                    int(expected_version),
                    str(attempt_id),
                    str(lease_id),
                    str(owner_id),
                    int(fencing_token),
                ),
            )
            self._require_cas(
                cursor,
                subject=f"OperationReservation {reservation_id}",
            )
            self._insert_outbox(
                conn,
                outbox,
                ts_ms=ts_ms,
                expected_aggregate=(
                    "OPERATION_RESERVATION",
                    str(reservation_id),
                    int(expected_version) + 1,
                ),
                expected_event_type="OPERATION_COMMITTED",
                expected_attempt_id=attempt_id,
            )
            updated = self._require_operation_reservation(
                conn,
                str(reservation_id),
            )
            return self._operation_record(updated)

    def _validate_internal_agent_recovery_uow(
        self,
        conn: sqlite3.Connection,
        *,
        worker_projection_store: WorkerProjectionStore,
        reservation: sqlite3.Row,
        attempt_id: str,
        lease_id: str,
        owner_id: str,
        fencing_token: int,
        projection: Mapping[str, Any],
        stage_ref: str,
        seal_digest: str,
        insert_projection: bool,
        ts_ms: int,
    ) -> None:
        projection_id = str(projection.get("projection_id") or "")
        existing_projection = conn.execute(
            "SELECT * FROM task_runtime_worker_projection WHERE projection_id = ?",
            (projection_id,),
        ).fetchone()
        if existing_projection is None and insert_projection:
            normalized = self._validated_internal_agent_projection(
                projection,
                stage_ref=stage_ref,
                seal_digest=seal_digest,
            )
            try:
                worker_projection_store._stage_in_transaction(  # noqa: SLF001
                    conn,
                    projection_id=projection_id,
                    projection=normalized,
                    created_ts_ms=int(ts_ms),
                )
            except WorkerProjectionStoreError as exc:
                raise TaskStoreError(
                    (
                        TASK_STORE_CAS_CONFLICT
                        if exc.reason_code == WORKER_PROJECTION_REPLAY_DRIFT
                        else TASK_STORE_DURABLE_DATA_INVALID
                    ),
                    "Internal Agent recovered projection could not be staged",
                ) from exc
            existing_projection = conn.execute(
                "SELECT * FROM task_runtime_worker_projection WHERE projection_id = ?",
                (projection_id,),
            ).fetchone()
        if existing_projection is None:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Internal Agent recovered checkpoint has no canonical projection",
            )
        try:
            durable_projection = validate_worker_projection_record(
                existing_projection
            )
        except WorkerProjectionStoreError as exc:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Internal Agent recovered projection is corrupt",
            ) from exc
        expected_projection = self._validated_internal_agent_projection(
            projection,
            stage_ref=stage_ref,
            seal_digest=seal_digest,
        )
        if durable_projection != expected_projection:
            raise TaskStoreError(
                TASK_STORE_CAS_CONFLICT,
                "Internal Agent recovered projection replay drifted",
            )

        attempt = self._require_attempt(conn, str(attempt_id))
        lease = self._require_lease(conn, str(lease_id))
        graph = self._require_graph(
            conn,
            str(durable_projection["graph_id"]),
            int(durable_projection["graph_revision"]),
        )
        node = self._require_node(
            conn,
            str(durable_projection["graph_id"]),
            int(durable_projection["graph_revision"]),
            str(durable_projection["node_id"]),
        )
        command = conn.execute(
            "SELECT * FROM host_command_journal WHERE command_id = ?",
            (str(durable_projection["command_id"]),),
        ).fetchone()
        if command is None:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Internal Agent recovered checkpoint has no HostCommand",
            )
        node_payload = _loads_object(
            str(node["node_json"]),
            field_name="Internal Agent TaskNode",
        )
        work_order_payload = _loads_object(
            str(command["work_order_json"]),
            field_name="Internal Agent WorkOrder",
        )
        recovered_pair = (
            str(attempt["status"]) == "RECOVERABLE"
            and attempt["owner_id"] is None
            and str(lease["status"]) == "EXPIRED"
        )
        effects = list(durable_projection.get("acceptance_effect_intents") or ())
        effect_payload = (
            dict(effects[0].get("payload") or {})
            if len(effects) == 1 and isinstance(effects[0], Mapping)
            else {}
        )
        audit = dict(durable_projection.get("audit_refs") or {})
        exact = (
            str(reservation["attempt_id"] or "") == str(attempt_id)
            and str(reservation["lease_id"] or "") == str(lease_id)
            and str(reservation["owner_id"] or "") == str(owner_id)
            and int(reservation["fencing_token"] or 0) == int(fencing_token)
            and str(attempt["attempt_id"]) == str(attempt_id)
            and str(attempt["graph_id"]) == str(durable_projection["graph_id"])
            and int(attempt["graph_revision"])
            == int(durable_projection["graph_revision"])
            and str(attempt["node_id"]) == str(durable_projection["node_id"])
            and str(attempt["operation_scope"] or "")
            == str(reservation["business_scope"])
            and str(attempt["operation_fingerprint"] or "")
            == str(reservation["operation_fingerprint"])
            and str(lease["attempt_id"]) == str(attempt_id)
            and str(lease["owner_id"]) == str(owner_id)
            and int(lease["fencing_token"]) == int(fencing_token)
            and str(graph["status"]) == "ACTIVE"
            and str(node["status"]) == "RUNNING"
            and str(node_payload.get("route") or "")
            == "delegate:agent_runtime"
            and str(command["surface"]) == "internal"
            and str(command["route_type"]) == "delegate"
            and str(command["target_id"]) == "agent_runtime"
            and str(command["command_id"])
            == str(durable_projection["command_id"])
            and str(command["request_id"])
            == str(durable_projection["request_id"])
            and str(command["graph_id"])
            == str(durable_projection["graph_id"])
            and int(command["graph_revision"])
            == int(durable_projection["graph_revision"])
            and str(command["operation_fingerprint"])
            == str(reservation["operation_fingerprint"])
            and str(command["business_scope"])
            == str(reservation["business_scope"])
            and str(effect_payload.get("operation_fingerprint") or "")
            == str(reservation["operation_fingerprint"])
            and str(effect_payload.get("work_order_id") or "")
            == str(command["root_work_order_id"])
            and str(work_order_payload.get("work_order_id") or "")
            == str(command["root_work_order_id"])
            and str(work_order_payload.get("root_work_order_id") or "")
            == str(command["root_work_order_id"])
            and str(audit.get("node_grant_ref") or "")
            == str(durable_projection["node_grant_ref"])
            and recovered_pair
        )
        if not exact:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Internal Agent checkpoint Task/Attempt/Lease authority drifted",
            )

    @staticmethod
    def _validated_internal_agent_projection(
        projection: Mapping[str, Any],
        *,
        stage_ref: str,
        seal_digest: str,
    ) -> dict[str, Any]:
        try:
            # Build a SQLite-shaped mapping so the canonical row validator can
            # be reused before insertion without trusting caller JSON.
            normalized = dict(projection)
            effects = normalized.get("acceptance_effect_intents")
            audit = normalized.get("audit_refs")
            if (
                normalized.get("route_type") != "delegate"
                or normalized.get("target_id") != "agent_runtime"
                or not isinstance(effects, list)
                or len(effects) != 1
                or not isinstance(effects[0], Mapping)
                or not isinstance(audit, Mapping)
                or not str(audit.get("node_grant_ref") or "").strip()
                or _SHA256.fullmatch(
                    str(audit.get("node_grant_digest") or "")
                )
                is None
                or not str(audit.get("action_preview_receipt_ref") or "").startswith(
                    "action-preview://"
                )
                or not str(audit.get("action_preview_event_id") or "").startswith(
                    "runtimeevent://"
                )
            ):
                raise ValueError("route/grant/Preview binding is invalid")
            effect = dict(effects[0])
            payload = effect.get("payload")
            if (
                set(effect) != {"effect_type", "commit_scope", "payload"}
                or effect.get("effect_type") != "internal.agent.result_commit"
                or effect.get("commit_scope") != "NODE"
                or not isinstance(payload, Mapping)
            ):
                raise ValueError("effect binding is invalid")
            result = payload.get("result")
            protected = (
                result.get("protected_artifact")
                if isinstance(result, Mapping)
                else None
            )
            if (
                not isinstance(protected, Mapping)
                or set(protected) != {"schema_version", "ref", "digest"}
                or protected.get("schema_version")
                != "Natsume.agent.governed_artifact_stage.v1"
                or protected.get("ref") != stage_ref
                or protected.get("digest") != seal_digest
                or payload.get("target_id") != "agent_runtime"
                or payload.get("operation_fingerprint") is None
            ):
                raise ValueError("protected artifact binding is invalid")
            # validate_worker_projection_record needs the physical columns;
            # use its lower-level payload validator through a local import to
            # avoid manufacturing a fake sqlite Row.
            return validate_worker_projection_payload(
                normalized,
                expected_projection_id=str(normalized.get("projection_id") or ""),
            )
        except (WorkerProjectionStoreError, TypeError, ValueError) as exc:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "Internal Agent recovered projection is not canonical: "
                f"{exc}",
            ) from exc



__all__ = ["TaskInternalAgentCheckpointCommitStoreMixin"]
