"""Append-only accepted HEAD, promotion and abandonment persistence.

This focused mixin extends :class:`WorkspaceRepository` without modifying the
frozen 0101 allocation/lease/cleanup schema.  The first lifecycle event treats
an existing ACTIVE 0101 authority as revision zero and every later decision is
an immutable, digest-chained receipt.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Mapping

import host_brain.persistence.workspace_terminal_budget_authority as _terminal_budget
import host_brain.persistence.workspace_terminal_failure_authority as _terminal_failure
from host_brain.contracts.agent_runtime.workspace_lifecycle import (
    WORKSPACE_LIFECYCLE_SCHEMA_VERSION,
    WorkspaceLifecycleContractError,
    WorkspaceLifecycleEventV1,
    WorkspaceLifecycleMutationV1,
    WorkspaceLifecycleSnapshotV1,
    WorkspaceLifecycleState,
)
from host_brain.persistence.workspace_operation_fencing import (
    WORKSPACE_AUTHORITY_INVALID,
    WORKSPACE_STALE_FENCE,
)
from host_brain.persistence.worker_projection_repository import (
    WorkerProjectionStoreError,
    validate_worker_projection_record,
)
from host_brain.persistence.workspace_terminal_cancellation_authority import (
    finalized_cancellation_preserves_node_state,
    finalized_post_review_cancellation_allows_workspace_abandonment,
)
WORKSPACE_LIFECYCLE_CONFLICT = "WORKSPACE_LIFECYCLE_CONFLICT"
WORKSPACE_LIFECYCLE_DATA_INVALID = "WORKSPACE_LIFECYCLE_DATA_INVALID"
WORKSPACE_LIFECYCLE_REPLAY_DRIFT = "WORKSPACE_LIFECYCLE_REPLAY_DRIFT"
WORKSPACE_LIFECYCLE_SCHEMA_NOT_READY = "WORKSPACE_LIFECYCLE_SCHEMA_NOT_READY"
WORKSPACE_LIFECYCLE_MIGRATION_ASSET_KEY = "0116#agent_workspace_lifecycle"
# Frozen after the 0116 asset is finalized.
WORKSPACE_LIFECYCLE_MIGRATION_SHA256 = (
    "4f97520ec5ba17ac16d02ec2aac6e5c1175e9c7b81cc0c8bc50ee39c15a5e968"
)

_EVENT_TABLE = "agent_workspace_lifecycle_event"
_ALLOWED_MIGRATION_DISPOSITIONS = frozenset(
    {"APPLIED", "APPLIED_WITH_COMPAT", "LEGACY_BACKFILL"}
)
_REQUIRED_INDEXES = {
    "idx_agent_workspace_lifecycle_latest": (
        "workspace_id",
        "transition_revision",
        "event_id",
    ),
    "idx_agent_workspace_lifecycle_execution": (
        "graph_id",
        "graph_revision",
        "node_id",
        "attempt_id",
        "task_lease_id",
    ),
}
_REQUIRED_TRIGGERS = frozenset(
    {
        "trg_agent_workspace_lifecycle_update_guard",
        "trg_agent_workspace_lifecycle_delete_guard",
        "trg_agent_workspace_lifecycle_sequence_guard",
        "trg_agent_workspace_lifecycle_binding_guard",
        "trg_agent_workspace_lifecycle_acceptance_guard",
    }
)
_TABLE_SQL_SHA256 = "2448bcb3c221e577c619eb558d62ee6e384579140f471a31417f22fab1c6a167"
_TRIGGER_SQL_SHA256: dict[str, str] = {
    "trg_agent_workspace_lifecycle_acceptance_guard": (
        "e81d410ec8c6a98fe6a67da8416ebd89c603f01842b233cecac8ded4faf9a51b"
    ),
    "trg_agent_workspace_lifecycle_binding_guard": (
        "fa0e526d5371040a08beb8e56c39ba6463084f6717537ff028f2ab91493a02b3"
    ),
    "trg_agent_workspace_lifecycle_delete_guard": (
        "50a68c547972e9dd50d9548d0b2021c3a3f913c91a36d6c57b07c2492e71044f"
    ),
    "trg_agent_workspace_lifecycle_sequence_guard": (
        "5da552b9f8ba3c9bf848bacf411c9c39c80acf85014d04c526e484dcd6f91319"
    ),
    "trg_agent_workspace_lifecycle_update_guard": (
        "587a7525eb205be798d69bed2f16fb7462b39f5281f15832212518132a57ee1f"
    ),
}
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


class WorkspaceLifecycleRepositoryMixin:
    """Persistence methods mixed into the canonical WorkspaceRepository."""

    def assert_lifecycle_schema_ready(self) -> None:
        if bool(getattr(self, "_workspace_lifecycle_schema_verified", False)):
            return
        source = (
            Path(__file__).resolve().parents[1]
            / "audit"
            / "migrations"
            / "0116_agent_workspace_lifecycle.sql"
        )
        try:
            source_checksum = hashlib.sha256(source.read_bytes()).hexdigest()
            conn = self._connect()
            try:
                asset = conn.execute(
                    """
                    SELECT checksum, disposition
                    FROM schema_migration_assets
                    WHERE asset_key = ?
                    """,
                    (WORKSPACE_LIFECYCLE_MIGRATION_ASSET_KEY,),
                ).fetchone()
                table = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                    (_EVENT_TABLE,),
                ).fetchone()
                columns = {
                    str(row[1]): (str(row[2]).upper(), int(row[3]), int(row[5]))
                    for row in conn.execute(
                        f"PRAGMA table_info({_EVENT_TABLE})"
                    ).fetchall()
                }
                indexes = {
                    str(row[1]): tuple(
                        str(item[2])
                        for item in conn.execute(
                            f'PRAGMA index_info("{str(row[1])}")'
                        ).fetchall()
                    )
                    for row in conn.execute(
                        f"PRAGMA index_list({_EVENT_TABLE})"
                    ).fetchall()
                }
                triggers = {
                    str(row[0]): str(row[1])
                    for row in conn.execute(
                        """
                        SELECT name, sql FROM sqlite_master
                        WHERE type = 'trigger' AND tbl_name = ?
                        """,
                        (_EVENT_TABLE,),
                    ).fetchall()
                }
            finally:
                conn.close()
        except (OSError, sqlite3.DatabaseError) as exc:
            raise self._workspace_operation_error(
                WORKSPACE_LIFECYCLE_SCHEMA_NOT_READY,
                "workspace lifecycle schema cannot be verified",
            ) from exc

        required_columns = {
            "event_id",
            "schema_version",
            "workspace_id",
            "transition_revision",
            "from_state",
            "to_state",
            "graph_id",
            "graph_revision",
            "node_id",
            "attempt_id",
            "task_lease_id",
            "task_lease_fencing_token",
            "workspace_lease_ref",
            "workspace_lease_owner",
            "workspace_lease_fencing_token",
            "base_commit",
            "accepted_head",
            "tree_digest",
            "diff_digest",
            "verification_ref",
            "acceptance_ref",
            "acceptance_event_id",
            "terminal_ref",
            "reason_code",
            "previous_event_digest",
            "event_digest",
            "created_ts_ms",
        }
        trigger_digests = {
            name: _normalized_sql_digest(sql) for name, sql in triggers.items()
        }
        valid_unique_shapes = {
            columns_tuple
            for name, columns_tuple in indexes.items()
            if name.startswith("sqlite_autoindex_agent_workspace_lifecycle_event_")
        }
        required_unique_shapes = {
            ("event_id",),
            ("event_digest",),
            ("workspace_id", "transition_revision"),
        }
        if (
            source_checksum != WORKSPACE_LIFECYCLE_MIGRATION_SHA256
            or asset is None
            or str(asset["checksum"]) != WORKSPACE_LIFECYCLE_MIGRATION_SHA256
            or str(asset["disposition"]) not in _ALLOWED_MIGRATION_DISPOSITIONS
            or table is None
            or _normalized_sql_digest(table["sql"]) != _TABLE_SQL_SHA256
            or set(columns) != required_columns
            or columns["event_id"] != ("TEXT", 0, 1)
            or any(columns[name][1] != 1 for name in required_columns - {
                "event_id",
                "base_commit",
                "accepted_head",
                "tree_digest",
                "diff_digest",
                "verification_ref",
                "acceptance_ref",
                "acceptance_event_id",
                "terminal_ref",
                "reason_code",
                "previous_event_digest",
            })
            or any(indexes.get(name) != shape for name, shape in _REQUIRED_INDEXES.items())
            or not required_unique_shapes.issubset(valid_unique_shapes)
            or set(triggers) != _REQUIRED_TRIGGERS
            or trigger_digests != _TRIGGER_SQL_SHA256
        ):
            raise self._workspace_operation_error(
                WORKSPACE_LIFECYCLE_SCHEMA_NOT_READY,
                "workspace lifecycle migration 0116 is missing or drifted",
            )
        self._workspace_lifecycle_schema_verified = True

    def get_lifecycle(self, workspace_id: str) -> WorkspaceLifecycleSnapshotV1:
        self.assert_lifecycle_schema_ready()
        reference = _required_text(workspace_id, field="workspace_id", maximum=120)
        with self._transaction(write=False) as conn:
            self._require_row(conn, reference)
            return self._read_lifecycle(conn, reference)

    def list_terminal_lifecycle_candidates(
        self,
        *,
        limit: int = 100,
        after_workspace_id: str | None = None,
    ) -> tuple[dict[str, Any], ...]:
        """Return bounded terminal lifecycle/workspace pairs from one snapshot.

        The 0116 journal deliberately does not mutate the frozen 0101
        workspace row.  A PROMOTED or ABANDONED lifecycle can therefore still
        own an ACTIVE worktree after a crash.  This read is the authoritative
        discovery boundary used to start, or resume, cleanup; it never changes
        either authority.
        """

        self.assert_lifecycle_schema_ready()
        if isinstance(limit, bool):
            raise self._workspace_operation_error(
                WORKSPACE_AUTHORITY_INVALID,
                "terminal lifecycle limit must be an integer",
            )
        try:
            bounded = int(limit)
        except (TypeError, ValueError) as exc:
            raise self._workspace_operation_error(
                WORKSPACE_AUTHORITY_INVALID,
                "terminal lifecycle limit must be an integer",
            ) from exc
        if bounded < 1 or bounded > 1_000:
            raise self._workspace_operation_error(
                WORKSPACE_AUTHORITY_INVALID,
                "terminal lifecycle limit must be between 1 and 1000",
            )
        cursor = str(after_workspace_id or "").strip()
        if cursor and len(cursor) > 120:
            raise self._workspace_operation_error(
                WORKSPACE_AUTHORITY_INVALID,
                "terminal lifecycle cursor is invalid",
            )
        with self._transaction(write=False) as conn:
            rows = conn.execute(
                """
                SELECT workspace.*
                FROM agent_workspace_authority AS workspace
                JOIN agent_workspace_lifecycle_event AS lifecycle
                  ON lifecycle.workspace_id = workspace.workspace_id
                WHERE workspace.status = 'ACTIVE'
                  AND (? = '' OR workspace.workspace_id > ?)
                  AND lifecycle.transition_revision = (
                      SELECT MAX(latest.transition_revision)
                      FROM agent_workspace_lifecycle_event AS latest
                      WHERE latest.workspace_id = workspace.workspace_id
                  )
                  AND lifecycle.to_state IN ('PROMOTED', 'ABANDONED')
                ORDER BY workspace.workspace_id
                LIMIT ?
                """,
                (cursor, cursor, bounded),
            ).fetchall()
            candidates: list[dict[str, Any]] = []
            for row in rows:
                authority = self._authority(row)
                lifecycle = self._read_lifecycle(conn, authority.workspace_id)
                if lifecycle.state not in {
                    WorkspaceLifecycleState.PROMOTED,
                    WorkspaceLifecycleState.ABANDONED,
                }:
                    raise self._workspace_operation_error(
                        WORKSPACE_LIFECYCLE_DATA_INVALID,
                        "terminal lifecycle inventory changed within one snapshot",
                    )
                candidates.append(
                    {
                        "authority": authority,
                        "lifecycle": lifecycle,
                    }
                )
            return tuple(candidates)

    def accept_workspace(
        self,
        workspace_id: str,
        *,
        event_id: str,
        expected_transition_revision: int,
        node_id: str,
        attempt_id: str,
        task_lease_id: str,
        task_lease_fencing_token: int,
        lease_owner: str,
        lease_token: str,
        lease_fencing_token: int,
        base_commit: str,
        accepted_head: str,
        tree_digest: str,
        diff_digest: str,
        verification_ref: str,
        acceptance_ref: str,
    ) -> WorkspaceLifecycleMutationV1:
        return self._append_lifecycle_event(
            workspace_id=workspace_id,
            event_id=event_id,
            expected_transition_revision=expected_transition_revision,
            expected_from=WorkspaceLifecycleState.ACTIVE,
            to_state=WorkspaceLifecycleState.ACCEPTED,
            node_id=node_id,
            attempt_id=attempt_id,
            task_lease_id=task_lease_id,
            task_lease_fencing_token=task_lease_fencing_token,
            lease_owner=lease_owner,
            lease_token=lease_token,
            lease_fencing_token=lease_fencing_token,
            base_commit=base_commit,
            accepted_head=accepted_head,
            tree_digest=tree_digest,
            diff_digest=diff_digest,
            verification_ref=verification_ref,
            acceptance_ref=acceptance_ref,
            acceptance_event_id=None,
            terminal_ref=None,
            reason_code=None,
        )

    def promote_workspace(
        self,
        workspace_id: str,
        *,
        event_id: str,
        expected_transition_revision: int,
        acceptance_event_id: str,
        promotion_ref: str,
        lease_owner: str,
        lease_token: str,
        lease_fencing_token: int,
    ) -> WorkspaceLifecycleMutationV1:
        return self._append_accepted_terminal(
            workspace_id=workspace_id,
            event_id=event_id,
            expected_transition_revision=expected_transition_revision,
            acceptance_event_id=acceptance_event_id,
            to_state=WorkspaceLifecycleState.PROMOTED,
            terminal_ref=promotion_ref,
            reason_code=None,
            lease_owner=lease_owner,
            lease_token=lease_token,
            lease_fencing_token=lease_fencing_token,
        )

    def abandon_workspace(
        self,
        workspace_id: str,
        *,
        event_id: str,
        expected_transition_revision: int,
        node_id: str,
        attempt_id: str,
        task_lease_id: str,
        task_lease_fencing_token: int,
        abandonment_ref: str,
        reason_code: str,
        lease_owner: str,
        lease_token: str,
        lease_fencing_token: int,
        acceptance_event_id: str | None = None,
    ) -> WorkspaceLifecycleMutationV1:
        self.assert_lifecycle_schema_ready()
        reference = _required_text(workspace_id, field="workspace_id", maximum=120)
        event_ref = _required_text(event_id, field="event_id", maximum=240)
        expected_revision = _nonnegative_int(
            expected_transition_revision,
            field="expected_transition_revision",
        )
        with self._transaction(write=True, workspace_lock_id=reference) as conn:
            snapshot = self._read_lifecycle(conn, reference)
            existing = self._find_event(conn, event_ref)
            if (
                existing is not None
                and existing.acceptance_event_id != acceptance_event_id
            ):
                raise self._workspace_operation_error(
                    WORKSPACE_LIFECYCLE_REPLAY_DRIFT,
                    "workspace abandonment replay drifted at acceptance_event_id",
                )
            authority_row = self._require_row(conn, reference)
            workspace_lease = self._workspace_lease_identity(
                authority_row,
                workspace_id=reference,
                lease_owner=lease_owner,
                lease_token=lease_token,
                lease_fencing_token=lease_fencing_token,
                validate_live=existing is None,
            )
            if snapshot.state is WorkspaceLifecycleState.ACCEPTED or (
                existing is not None
                and existing.from_state is WorkspaceLifecycleState.ACCEPTED
            ):
                accepted = self._accepted_event(snapshot, acceptance_event_id)
                values = _event_values(
                    event_id=event_ref,
                    workspace_id=reference,
                    transition_revision=expected_revision + 1,
                    from_state=WorkspaceLifecycleState.ACCEPTED,
                    to_state=WorkspaceLifecycleState.ABANDONED,
                    graph_id=accepted.graph_id,
                    graph_revision=accepted.graph_revision,
                    node_id=node_id,
                    attempt_id=attempt_id,
                    task_lease_id=task_lease_id,
                    task_lease_fencing_token=task_lease_fencing_token,
                    workspace_lease=workspace_lease,
                    accepted=accepted,
                    acceptance_event_id=accepted.event_id,
                    terminal_ref=abandonment_ref,
                    reason_code=reason_code,
                )
            else:
                if acceptance_event_id is not None:
                    raise self._workspace_operation_error(
                        WORKSPACE_LIFECYCLE_CONFLICT,
                        "ACTIVE abandonment cannot reference an accepted receipt",
                    )
                authority = self._authority(authority_row)
                values = _event_values(
                    event_id=event_ref,
                    workspace_id=reference,
                    transition_revision=expected_revision + 1,
                    from_state=WorkspaceLifecycleState.ACTIVE,
                    to_state=WorkspaceLifecycleState.ABANDONED,
                    graph_id=authority.graph_id,
                    graph_revision=authority.graph_revision,
                    node_id=node_id,
                    attempt_id=attempt_id,
                    task_lease_id=task_lease_id,
                    task_lease_fencing_token=task_lease_fencing_token,
                    workspace_lease=workspace_lease,
                    accepted=None,
                    acceptance_event_id=None,
                    terminal_ref=abandonment_ref,
                    reason_code=reason_code,
                )
            return self._commit_or_replay_lifecycle_event(
                conn,
                snapshot=snapshot,
                existing=existing,
                expected_revision=expected_revision,
                values=values,
            )

    def _append_accepted_terminal(
        self,
        *,
        workspace_id: str,
        event_id: str,
        expected_transition_revision: int,
        acceptance_event_id: str,
        to_state: WorkspaceLifecycleState,
        terminal_ref: str,
        reason_code: str | None,
        lease_owner: str,
        lease_token: str,
        lease_fencing_token: int,
    ) -> WorkspaceLifecycleMutationV1:
        self.assert_lifecycle_schema_ready()
        reference = _required_text(workspace_id, field="workspace_id", maximum=120)
        event_ref = _required_text(event_id, field="event_id", maximum=240)
        expected_revision = _nonnegative_int(
            expected_transition_revision,
            field="expected_transition_revision",
        )
        with self._transaction(write=True, workspace_lock_id=reference) as conn:
            snapshot = self._read_lifecycle(conn, reference)
            existing = self._find_event(conn, event_ref)
            if (
                existing is not None
                and existing.acceptance_event_id != acceptance_event_id
            ):
                raise self._workspace_operation_error(
                    WORKSPACE_LIFECYCLE_REPLAY_DRIFT,
                    "workspace terminal replay drifted at acceptance_event_id",
                )
            accepted = self._accepted_event(snapshot, acceptance_event_id)
            workspace_lease = self._workspace_lease_identity(
                self._require_row(conn, reference),
                workspace_id=reference,
                lease_owner=lease_owner,
                lease_token=lease_token,
                lease_fencing_token=lease_fencing_token,
                validate_live=existing is None,
            )
            values = _event_values(
                event_id=event_ref,
                workspace_id=reference,
                transition_revision=expected_revision + 1,
                from_state=WorkspaceLifecycleState.ACCEPTED,
                to_state=to_state,
                graph_id=accepted.graph_id,
                graph_revision=accepted.graph_revision,
                node_id=accepted.node_id,
                attempt_id=accepted.attempt_id,
                task_lease_id=accepted.task_lease_id,
                task_lease_fencing_token=accepted.task_lease_fencing_token,
                workspace_lease=workspace_lease,
                accepted=accepted,
                acceptance_event_id=accepted.event_id,
                terminal_ref=terminal_ref,
                reason_code=reason_code,
            )
            return self._commit_or_replay_lifecycle_event(
                conn,
                snapshot=snapshot,
                existing=existing,
                expected_revision=expected_revision,
                values=values,
            )

    def _append_lifecycle_event(
        self,
        *,
        workspace_id: str,
        event_id: str,
        expected_transition_revision: int,
        expected_from: WorkspaceLifecycleState,
        to_state: WorkspaceLifecycleState,
        node_id: str,
        attempt_id: str,
        task_lease_id: str,
        task_lease_fencing_token: int,
        lease_owner: str,
        lease_token: str,
        lease_fencing_token: int,
        base_commit: str | None,
        accepted_head: str | None,
        tree_digest: str | None,
        diff_digest: str | None,
        verification_ref: str | None,
        acceptance_ref: str | None,
        acceptance_event_id: str | None,
        terminal_ref: str | None,
        reason_code: str | None,
    ) -> WorkspaceLifecycleMutationV1:
        self.assert_lifecycle_schema_ready()
        reference = _required_text(workspace_id, field="workspace_id", maximum=120)
        event_ref = _required_text(event_id, field="event_id", maximum=240)
        expected_revision = _nonnegative_int(
            expected_transition_revision,
            field="expected_transition_revision",
        )
        with self._transaction(write=True, workspace_lock_id=reference) as conn:
            snapshot = self._read_lifecycle(conn, reference)
            existing = self._find_event(conn, event_ref)
            authority_row = self._require_row(conn, reference)
            authority = self._authority(authority_row)
            workspace_lease = self._workspace_lease_identity(
                authority_row,
                workspace_id=reference,
                lease_owner=lease_owner,
                lease_token=lease_token,
                lease_fencing_token=lease_fencing_token,
                validate_live=existing is None,
            )
            values = _event_values(
                event_id=event_ref,
                workspace_id=reference,
                transition_revision=expected_revision + 1,
                from_state=expected_from,
                to_state=to_state,
                graph_id=authority.graph_id,
                graph_revision=authority.graph_revision,
                node_id=node_id,
                attempt_id=attempt_id,
                task_lease_id=task_lease_id,
                task_lease_fencing_token=task_lease_fencing_token,
                workspace_lease=workspace_lease,
                accepted=None,
                acceptance_event_id=acceptance_event_id,
                terminal_ref=terminal_ref,
                reason_code=reason_code,
                acceptance_values={
                    "base_commit": base_commit,
                    "accepted_head": accepted_head,
                    "tree_digest": tree_digest,
                    "diff_digest": diff_digest,
                    "verification_ref": verification_ref,
                    "acceptance_ref": acceptance_ref,
                },
            )
            return self._commit_or_replay_lifecycle_event(
                conn,
                snapshot=snapshot,
                existing=existing,
                expected_revision=expected_revision,
                values=values,
            )

    def _commit_or_replay_lifecycle_event(
        self,
        conn: sqlite3.Connection,
        *,
        snapshot: WorkspaceLifecycleSnapshotV1,
        existing: WorkspaceLifecycleEventV1 | None,
        expected_revision: int,
        values: Mapping[str, Any],
    ) -> WorkspaceLifecycleMutationV1:
        if existing is not None:
            _assert_exact_replay(self, existing, values)
            return WorkspaceLifecycleMutationV1(
                event=existing,
                current_state=snapshot.state,
                current_revision=snapshot.revision,
                replayed=True,
            )
        from_state = WorkspaceLifecycleState(str(values["from_state"]))
        if snapshot.revision != expected_revision or snapshot.state is not from_state:
            raise self._workspace_operation_error(
                WORKSPACE_STALE_FENCE,
                "workspace lifecycle revision or state is stale",
            )
        ts_ms = self._now()
        self._assert_workspace_lease_binding_at_commit(
            conn,
            workspace_id=str(values["workspace_id"]),
            workspace_lease_ref=str(values["workspace_lease_ref"]),
            workspace_lease_owner=str(values["workspace_lease_owner"]),
            workspace_lease_fencing_token=int(
                values["workspace_lease_fencing_token"]
            ),
            ts_ms=ts_ms,
        )
        self._assert_task_execution_binding(
            conn,
            graph_id=str(values["graph_id"]),
            graph_revision=int(values["graph_revision"]),
            node_id=str(values["node_id"]),
            attempt_id=str(values["attempt_id"]),
            task_lease_id=str(values["task_lease_id"]),
            task_lease_fencing_token=int(values["task_lease_fencing_token"]),
            ts_ms=ts_ms,
            require_live=(from_state is WorkspaceLifecycleState.ACTIVE),
            allow_terminal_abandonment=(
                from_state is WorkspaceLifecycleState.ACTIVE
                and WorkspaceLifecycleState(str(values["to_state"]))
                is WorkspaceLifecycleState.ABANDONED
            ),
            terminal_reason_code=str(values.get("reason_code") or ""),
        )
        try:
            event = WorkspaceLifecycleEventV1.create(
                **values,
                previous_event_digest=(
                    snapshot.latest_event.event_digest
                    if snapshot.latest_event is not None
                    else None
                ),
                created_ts_ms=ts_ms,
            )
        except (KeyError, TypeError, ValueError, WorkspaceLifecycleContractError) as exc:
            raise self._workspace_operation_error(
                WORKSPACE_AUTHORITY_INVALID,
                "workspace lifecycle event is not canonical",
            ) from exc
        try:
            conn.execute(
                f"""
                INSERT INTO {_EVENT_TABLE}(
                    event_id, schema_version, workspace_id,
                    transition_revision, from_state, to_state,
                    graph_id, graph_revision, node_id, attempt_id,
                    task_lease_id, task_lease_fencing_token,
                    workspace_lease_ref, workspace_lease_owner,
                    workspace_lease_fencing_token, base_commit, accepted_head,
                    tree_digest, diff_digest, verification_ref, acceptance_ref,
                    acceptance_event_id, terminal_ref, reason_code,
                    previous_event_digest, event_digest, created_ts_ms
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                _event_insert_values(event),
            )
            self._inject_workspace_lifecycle_fault("after_event_insert")
        except sqlite3.IntegrityError as exc:
            raise self._workspace_operation_error(
                WORKSPACE_LIFECYCLE_CONFLICT,
                "workspace lifecycle append lost its durable CAS or binding",
            ) from exc
        current = self._read_lifecycle(conn, event.workspace_id)
        return WorkspaceLifecycleMutationV1(
            event=event,
            current_state=current.state,
            current_revision=current.revision,
        )

    def _read_lifecycle(
        self,
        conn: sqlite3.Connection,
        workspace_id: str,
    ) -> WorkspaceLifecycleSnapshotV1:
        rows = conn.execute(
            f"""
            SELECT * FROM {_EVENT_TABLE}
            WHERE workspace_id = ?
            ORDER BY transition_revision, event_id
            """,
            (workspace_id,),
        ).fetchall()
        if not rows:
            return WorkspaceLifecycleSnapshotV1(
                workspace_id=workspace_id,
                state=WorkspaceLifecycleState.ACTIVE,
                revision=0,
                latest_event=None,
                accepted_event=None,
            )
        events = tuple(self._lifecycle_event(row) for row in rows)
        accepted: WorkspaceLifecycleEventV1 | None = None
        previous: WorkspaceLifecycleEventV1 | None = None
        for index, event in enumerate(events, start=1):
            if event.workspace_id != workspace_id or event.transition_revision != index:
                raise self._workspace_operation_error(
                    WORKSPACE_LIFECYCLE_DATA_INVALID,
                    "workspace lifecycle revision sequence is corrupt",
                )
            if previous is None:
                if (
                    event.from_state is not WorkspaceLifecycleState.ACTIVE
                    or event.previous_event_digest is not None
                ):
                    raise self._workspace_operation_error(
                        WORKSPACE_LIFECYCLE_DATA_INVALID,
                        "workspace lifecycle genesis is corrupt",
                    )
            elif (
                event.from_state is not previous.to_state
                or event.previous_event_digest != previous.event_digest
            ):
                raise self._workspace_operation_error(
                    WORKSPACE_LIFECYCLE_DATA_INVALID,
                    "workspace lifecycle digest chain is corrupt",
                )
            if event.to_state is WorkspaceLifecycleState.ACCEPTED:
                if accepted is not None:
                    raise self._workspace_operation_error(
                        WORKSPACE_LIFECYCLE_DATA_INVALID,
                        "workspace lifecycle has multiple accepted receipts",
                    )
                accepted = event
            if event.from_state is WorkspaceLifecycleState.ACCEPTED:
                if accepted is None or not _preserves_acceptance(event, accepted):
                    raise self._workspace_operation_error(
                        WORKSPACE_LIFECYCLE_DATA_INVALID,
                        "workspace terminal event drifted from its accepted receipt",
                    )
            previous = event
        latest = events[-1]
        try:
            return WorkspaceLifecycleSnapshotV1(
                workspace_id=workspace_id,
                state=latest.to_state,
                revision=latest.transition_revision,
                latest_event=latest,
                accepted_event=accepted,
            )
        except WorkspaceLifecycleContractError as exc:
            raise self._workspace_operation_error(
                WORKSPACE_LIFECYCLE_DATA_INVALID,
                "workspace lifecycle snapshot is corrupt",
            ) from exc

    def _accepted_event(
        self,
        snapshot: WorkspaceLifecycleSnapshotV1,
        acceptance_event_id: str | None,
    ) -> WorkspaceLifecycleEventV1:
        reference = _required_text(
            acceptance_event_id,
            field="acceptance_event_id",
            maximum=240,
        )
        accepted = snapshot.accepted_event
        if accepted is None or accepted.event_id != reference:
            raise self._workspace_operation_error(
                WORKSPACE_LIFECYCLE_CONFLICT,
                "workspace accepted receipt does not match current authority",
            )
        return accepted

    def _find_event(
        self,
        conn: sqlite3.Connection,
        event_id: str,
    ) -> WorkspaceLifecycleEventV1 | None:
        row = conn.execute(
            f"SELECT * FROM {_EVENT_TABLE} WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        return self._lifecycle_event(row) if row is not None else None

    def _workspace_lease_identity(
        self,
        row: sqlite3.Row,
        *,
        workspace_id: str,
        lease_owner: str,
        lease_token: str,
        lease_fencing_token: int,
        validate_live: bool,
    ) -> dict[str, Any]:
        owner = _required_text(lease_owner, field="lease_owner", maximum=240)
        token = _required_text(lease_token, field="lease_token", maximum=512)
        fence = _positive_int(
            lease_fencing_token,
            field="lease_fencing_token",
        )
        token_digest = _digest(token)
        if validate_live:
            self._assert_live_lease(
                row,
                owner=owner,
                token_digest=token_digest,
                fence=fence,
                ts_ms=self._now(),
            )
        encoded = json.dumps(
            {
                "workspace_id": workspace_id,
                "lease_owner": owner,
                "lease_token_digest": token_digest,
                "lease_fencing_token": fence,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return {
            "workspace_lease_ref": "workspace-lease://" + hashlib.sha256(
                encoded.encode("utf-8")
            ).hexdigest(),
            "workspace_lease_owner": owner,
            "workspace_lease_fencing_token": fence,
        }

    def _assert_task_execution_binding(
        self,
        conn: sqlite3.Connection,
        *,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        attempt_id: str,
        task_lease_id: str,
        task_lease_fencing_token: int,
        ts_ms: int,
        require_live: bool,
        allow_terminal_abandonment: bool,
        terminal_reason_code: str,
    ) -> None:
        row = conn.execute(
            """
            SELECT attempt.graph_id, attempt.graph_revision, attempt.node_id,
                   attempt.status AS attempt_status,
                   attempt.owner_id AS attempt_owner_id,
                   attempt.operation_scope AS attempt_operation_scope,
                   attempt.operation_fingerprint AS attempt_operation_fingerprint,
                   lease.attempt_id, lease.fencing_token,
                   lease.lease_id, lease.owner_id AS lease_owner_id,
                   lease.status AS lease_status, lease.expires_ts_ms,
                   lease.released_ts_ms AS lease_released_ts_ms,
                   graph.status AS graph_status,
                   node.status AS node_status,
                   node.node_json AS node_json
            FROM task_runtime_attempt AS attempt
            JOIN task_runtime_lease AS lease
              ON lease.attempt_id = attempt.attempt_id
            JOIN task_runtime_graph AS graph
              ON graph.graph_id = attempt.graph_id
             AND graph.revision = attempt.graph_revision
            JOIN task_runtime_node AS node
              ON node.graph_id = attempt.graph_id
             AND node.graph_revision = attempt.graph_revision
             AND node.node_id = attempt.node_id
            WHERE attempt.attempt_id = ? AND lease.lease_id = ?
            """,
            (attempt_id, task_lease_id),
        ).fetchone()
        active_binding = bool(
            row is not None
            and str(row["lease_status"]) == "ACTIVE"
            and type(row["expires_ts_ms"]) is int
            and int(row["expires_ts_ms"]) > ts_ms
        )
        completed_binding = bool(
            row is not None
            and str(row["attempt_status"]) == "SUCCEEDED"
            and str(row["lease_status"]) == "RELEASED"
        )
        recovered_task_binding = (
            self._internal_agent_recovered_task_binding(
                conn,
                row=row,
                attempt_id=attempt_id,
                historical_lease_id=task_lease_id,
                historical_fencing_token=task_lease_fencing_token,
            )
            if row is not None
            else None
        )
        recovered_completed_binding = bool(
            row is not None
            and str(row["attempt_status"]) == "SUCCEEDED"
            and str(row["graph_status"]) == "COMPLETED"
            and str(row["node_status"]) == "ACCEPTED"
            and recovered_task_binding is not None
        )
        terminal_graph_accepted_node_lineage = bool(
            allow_terminal_abandonment
            and row is not None
            and str(row["graph_status"]) in {"FAILED", "CANCELLED"}
            and str(row["node_status"]) == "ACCEPTED"
            and str(row["attempt_status"]) == "SUCCEEDED"
            and (
                str(row["lease_status"]) == "RELEASED"
                or recovered_task_binding is not None
            )
            and conn.execute(
                """
                SELECT COUNT(*)
                FROM task_runtime_node_acceptance_decision AS acceptance
                JOIN task_runtime_node_artifact AS artifact
                  ON artifact.graph_id = acceptance.graph_id
                 AND artifact.graph_revision = acceptance.graph_revision
                 AND artifact.node_id = acceptance.node_id
                 AND artifact.attempt_id = acceptance.attempt_id
                JOIN task_runtime_idempotency_reservation AS operation
                  ON operation.attempt_id = acceptance.attempt_id
                 AND operation.status = 'COMMITTED'
                 AND operation.receipt_ref = artifact.receipt_ref
                WHERE acceptance.graph_id = ?
                  AND acceptance.graph_revision = ?
                  AND acceptance.node_id = ?
                  AND acceptance.attempt_id = ?
                  AND acceptance.target_status = 'ACCEPTED'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM task_runtime_acceptance_effect_intent AS effect
                      WHERE effect.graph_id = acceptance.graph_id
                        AND effect.graph_revision = acceptance.graph_revision
                        AND effect.node_id = acceptance.node_id
                        AND effect.attempt_id = acceptance.attempt_id
                  )
                """,
                (
                    graph_id,
                    graph_revision,
                    node_id,
                    attempt_id,
                ),
            ).fetchone()[0]
            == 1
        )
        terminal_graph_accepted_node_binding = bool(
            terminal_graph_accepted_node_lineage
            and (
                str(row["graph_status"]) == "FAILED"
                or (
                    str(row["graph_status"]) == "CANCELLED"
                    and finalized_cancellation_preserves_node_state(
                        conn,
                        graph_id=graph_id,
                        graph_revision=graph_revision,
                        node_id=node_id,
                        expected_status="ACCEPTED",
                    )
                )
            )
        )
        terminal_graph_cancelled_node_binding = bool(
            allow_terminal_abandonment
            and row is not None
            and str(row["graph_status"]) == "CANCELLED"
            and str(row["node_status"]) == "CANCELLED"
            and (
                (
                    str(row["attempt_status"]) == "CANCELLED"
                    and str(row["lease_status"]) == "CANCELLED"
                    and finalized_cancellation_preserves_node_state(
                        conn,
                        graph_id=graph_id,
                        graph_revision=graph_revision,
                        node_id=node_id,
                        expected_status="CANCELLED",
                    )
                )
                or (
                    str(row["attempt_status"]) == "SUCCEEDED"
                    and (
                        str(row["lease_status"]) == "RELEASED"
                        or recovered_task_binding is not None
                    )
                    and (
                        finalized_post_review_cancellation_allows_workspace_abandonment(
                            conn,
                            graph_id=graph_id,
                            graph_revision=graph_revision,
                            node_id=node_id,
                            attempt_id=attempt_id,
                        )
                    )
                )
            )
        )
        terminal_abandonment_binding = bool(
            allow_terminal_abandonment
            and row is not None
            and (
                (
                    str(row["graph_status"]) == "FAILED"
                    and str(row["node_status"])
                    in {"FAILED", "VERIFICATION_FAILED"}
                    and str(row["attempt_status"]) in {"FAILED", "SUCCEEDED"}
                    and (
                        str(row["lease_status"]) == "RELEASED"
                        or recovered_task_binding is not None
                    )
                )
                or terminal_graph_accepted_node_binding
                or terminal_graph_cancelled_node_binding
                or (
                    str(row["lease_status"]) == "EXPIRED"
                    and _terminal_failure.finalized_no_effect_failure_allows_workspace_abandonment(
                        conn, graph_id=graph_id, graph_revision=graph_revision,
                        node_id=node_id, attempt_id=attempt_id, lease_id=task_lease_id,
                    )
                )
                or (
                    str(row["graph_status"]) == "BLOCKED"
                    and str(row["node_status"]) == "FAILED"
                    and str(row["attempt_status"]) == "FAILED"
                    and _terminal_budget.allows_abandonment(conn, row, terminal_reason_code)
                )
            )
        )
        if (
            row is None
            or str(row["graph_id"]) != graph_id
            or type(row["graph_revision"]) is not int
            or int(row["graph_revision"]) != graph_revision
            or str(row["node_id"]) != node_id
            or str(row["attempt_id"]) != attempt_id
            or type(row["fencing_token"]) is not int
            or int(row["fencing_token"]) != task_lease_fencing_token
            or (
                require_live
                and not (
                    terminal_abandonment_binding
                    if allow_terminal_abandonment
                    else (
                        active_binding
                        or completed_binding
                        or recovered_completed_binding
                    )
                )
            )
        ):
            raise self._workspace_operation_error(
                WORKSPACE_STALE_FENCE,
                "TaskNode Attempt/Lease binding is absent, expired, or stale",
            )

    def _internal_agent_recovered_task_binding(
        self,
        conn: sqlite3.Connection,
        *,
        row: sqlite3.Row,
        attempt_id: str,
        historical_lease_id: str,
        historical_fencing_token: int,
    ) -> dict[str, Any] | None:
        """Return one exact historical→takeover lineage, otherwise ``None``."""

        if (
            str(row["attempt_id"]) != str(attempt_id)
            or str(row["lease_id"]) != str(historical_lease_id)
            or int(row["fencing_token"] or 0) != int(historical_fencing_token)
            or str(row["lease_status"]) != "EXPIRED"
            or row["lease_released_ts_ms"] is None
        ):
            return None
        reservations = conn.execute(
            """
            SELECT * FROM task_runtime_idempotency_reservation
            WHERE attempt_id = ? ORDER BY reservation_id
            """,
            (str(attempt_id),),
        ).fetchall()
        leases = conn.execute(
            """
            SELECT * FROM task_runtime_lease
            WHERE attempt_id = ?
            ORDER BY fencing_token, acquired_ts_ms, lease_id
            """,
            (str(attempt_id),),
        ).fetchall()
        if len(reservations) != 1 or len(leases) < 2:
            return None
        reservation = reservations[0]
        final_lease = leases[-1]
        historical_index = next(
            (
                index
                for index, lease in enumerate(leases)
                if str(lease["lease_id"]) == str(historical_lease_id)
            ),
            -1,
        )
        if historical_index < 0 or historical_index == len(leases) - 1:
            return None
        tokens = [int(lease["fencing_token"] or 0) for lease in leases]
        if any(
            right != left + 1
            for left, right in zip(tokens, tokens[1:], strict=False)
        ):
            return None
        if any(
            str(lease["status"]) != "EXPIRED"
            or lease["released_ts_ms"] is None
            for lease in leases[:-1]
        ):
            return None
        if any(
            int(leases[index + 1]["acquired_ts_ms"])
            < int(lease["released_ts_ms"])
            for index, lease in enumerate(leases[:-1])
        ):
            return None
        projection = conn.execute(
            """
            SELECT * FROM task_runtime_worker_projection
            WHERE projection_id = ?
            """,
            (str(reservation["receipt_ref"] or ""),),
        ).fetchone()
        commands = conn.execute(
            """
            SELECT * FROM host_command_journal
            WHERE graph_id = ? AND graph_revision = ?
            ORDER BY command_id
            """,
            (str(row["graph_id"]), int(row["graph_revision"])),
        ).fetchall()
        command = commands[0] if len(commands) == 1 else None
        node_count = conn.execute(
            """
            SELECT COUNT(*) AS total FROM task_runtime_node
            WHERE graph_id = ? AND graph_revision = ?
            """,
            (str(row["graph_id"]), int(row["graph_revision"])),
        ).fetchone()
        try:
            node_payload = json.loads(str(row["node_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        try:
            projection_record = (
                validate_worker_projection_record(projection)
                if projection is not None
                else None
            )
        except WorkerProjectionStoreError:
            return None
        exact = (
            projection_record is not None
            and command is not None
            and node_count is not None
            and int(node_count["total"]) == 1
            and isinstance(node_payload, Mapping)
            and str(node_payload.get("node_id") or "") == str(row["node_id"])
            and str(node_payload.get("route") or "")
            == "delegate:agent_runtime"
            and str(reservation["status"]) == "COMMITTED"
            and str(reservation["lease_id"]) == str(historical_lease_id)
            and str(reservation["owner_id"]) == str(row["lease_owner_id"])
            and int(reservation["fencing_token"] or 0)
            == int(historical_fencing_token)
            and str(reservation["business_scope"])
            == str(row["attempt_operation_scope"])
            and str(reservation["operation_fingerprint"])
            == str(row["attempt_operation_fingerprint"])
            and str(command["surface"]) == "internal"
            and str(command["route_type"]) == "delegate"
            and str(command["target_id"]) == "agent_runtime"
            and str(command["graph_id"]) == str(row["graph_id"])
            and int(command["graph_revision"]) == int(row["graph_revision"])
            and str(command["business_scope"])
            == str(reservation["business_scope"])
            and str(command["operation_fingerprint"])
            == str(reservation["operation_fingerprint"])
            and str(projection_record["command_id"]) == str(command["command_id"])
            and str(projection_record["graph_id"]) == str(row["graph_id"])
            and int(projection_record["graph_revision"])
            == int(row["graph_revision"])
            and str(projection_record["node_id"]) == str(row["node_id"])
            and str(projection_record["attempt_id"]) == str(attempt_id)
            and str(projection_record["route_type"]) == "delegate"
            and str(projection_record["target_id"]) == "agent_runtime"
            and str(final_lease["status"]) == "RELEASED"
            and final_lease["released_ts_ms"] is not None
            and int(final_lease["fencing_token"])
            > int(historical_fencing_token)
            and str(final_lease["owner_id"]) == str(row["attempt_owner_id"])
        )
        if not exact:
            return None
        return {
            "historical_lease": dict(leases[historical_index]),
            "completion_lease": dict(final_lease),
            "operation_reservation": dict(reservation),
            "recovered": True,
        }

    def _assert_workspace_lease_binding_at_commit(
        self,
        conn: sqlite3.Connection,
        *,
        workspace_id: str,
        workspace_lease_ref: str,
        workspace_lease_owner: str,
        workspace_lease_fencing_token: int,
        ts_ms: int,
    ) -> None:
        row = self._require_row(conn, workspace_id)
        token_digest = str(row["lease_token_digest"] or "")
        encoded = json.dumps(
            {
                "workspace_id": workspace_id,
                "lease_owner": workspace_lease_owner,
                "lease_token_digest": token_digest,
                "lease_fencing_token": workspace_lease_fencing_token,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        canonical_ref = "workspace-lease://" + hashlib.sha256(
            encoded.encode("utf-8")
        ).hexdigest()
        if (
            str(row["status"]) != "ACTIVE"
            or str(row["lease_owner"] or "") != workspace_lease_owner
            or not _DIGEST.fullmatch(token_digest)
            or type(row["lease_fencing_token"]) is not int
            or int(row["lease_fencing_token"]) != workspace_lease_fencing_token
            or type(row["lease_expires_ts_ms"]) is not int
            or int(row["lease_expires_ts_ms"]) <= ts_ms
            or workspace_lease_ref != canonical_ref
        ):
            raise self._workspace_operation_error(
                WORKSPACE_STALE_FENCE,
                "workspace lease expired or drifted before lifecycle commit",
            )

    def _lifecycle_event(self, row: sqlite3.Row) -> WorkspaceLifecycleEventV1:
        try:
            payload = {key: row[key] for key in row.keys()}
            return WorkspaceLifecycleEventV1.from_mapping(payload)
        except (KeyError, TypeError, ValueError, WorkspaceLifecycleContractError) as exc:
            raise self._workspace_operation_error(
                WORKSPACE_LIFECYCLE_DATA_INVALID,
                "workspace lifecycle row is corrupt",
            ) from exc

    def _inject_workspace_lifecycle_fault(self, point: str) -> None:
        fault_injector = getattr(self, "_workspace_lifecycle_fault_injector", None)
        if fault_injector is not None:
            fault_injector(point)


def _event_values(
    *,
    event_id: str,
    workspace_id: str,
    transition_revision: int,
    from_state: WorkspaceLifecycleState,
    to_state: WorkspaceLifecycleState,
    graph_id: str,
    graph_revision: int,
    node_id: str,
    attempt_id: str,
    task_lease_id: str,
    task_lease_fencing_token: int,
    workspace_lease: Mapping[str, Any],
    accepted: WorkspaceLifecycleEventV1 | None,
    acceptance_event_id: str | None,
    terminal_ref: str | None,
    reason_code: str | None,
    acceptance_values: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    evidence = dict(acceptance_values or {})
    if accepted is not None:
        evidence = {
            "base_commit": accepted.base_commit,
            "accepted_head": accepted.accepted_head,
            "tree_digest": accepted.tree_digest,
            "diff_digest": accepted.diff_digest,
            "verification_ref": accepted.verification_ref,
            "acceptance_ref": accepted.acceptance_ref,
        }
    return {
        "event_id": _required_text(event_id, field="event_id", maximum=240),
        "workspace_id": _required_text(
            workspace_id,
            field="workspace_id",
            maximum=120,
        ),
        "transition_revision": _positive_int(
            transition_revision,
            field="transition_revision",
        ),
        "from_state": from_state,
        "to_state": to_state,
        "graph_id": _required_text(graph_id, field="graph_id", maximum=240),
        "graph_revision": _positive_int(graph_revision, field="graph_revision"),
        "node_id": _required_text(node_id, field="node_id", maximum=240),
        "attempt_id": _required_text(attempt_id, field="attempt_id", maximum=240),
        "task_lease_id": _required_text(
            task_lease_id,
            field="task_lease_id",
            maximum=240,
        ),
        "task_lease_fencing_token": _positive_int(
            task_lease_fencing_token,
            field="task_lease_fencing_token",
        ),
        "workspace_lease_ref": workspace_lease["workspace_lease_ref"],
        "workspace_lease_owner": workspace_lease["workspace_lease_owner"],
        "workspace_lease_fencing_token": workspace_lease[
            "workspace_lease_fencing_token"
        ],
        "base_commit": evidence.get("base_commit"),
        "accepted_head": evidence.get("accepted_head"),
        "tree_digest": evidence.get("tree_digest"),
        "diff_digest": evidence.get("diff_digest"),
        "verification_ref": evidence.get("verification_ref"),
        "acceptance_ref": evidence.get("acceptance_ref"),
        "acceptance_event_id": acceptance_event_id,
        "terminal_ref": terminal_ref,
        "reason_code": reason_code,
        "schema_version": WORKSPACE_LIFECYCLE_SCHEMA_VERSION,
    }


def _assert_exact_replay(
    repository: Any,
    existing: WorkspaceLifecycleEventV1,
    values: Mapping[str, Any],
) -> None:
    for key, expected in values.items():
        if key == "schema_version":
            actual = existing.schema_version
        else:
            actual = getattr(existing, key)
        if actual != expected:
            raise repository._workspace_operation_error(
                WORKSPACE_LIFECYCLE_REPLAY_DRIFT,
                f"workspace lifecycle replay drifted at {key}",
            )


def _preserves_acceptance(
    event: WorkspaceLifecycleEventV1,
    accepted: WorkspaceLifecycleEventV1,
) -> bool:
    return (
        event.acceptance_event_id == accepted.event_id
        and event.graph_id == accepted.graph_id
        and event.graph_revision == accepted.graph_revision
        and event.node_id == accepted.node_id
        and event.attempt_id == accepted.attempt_id
        and event.task_lease_id == accepted.task_lease_id
        and event.task_lease_fencing_token == accepted.task_lease_fencing_token
        and event.base_commit == accepted.base_commit
        and event.accepted_head == accepted.accepted_head
        and event.tree_digest == accepted.tree_digest
        and event.diff_digest == accepted.diff_digest
        and event.verification_ref == accepted.verification_ref
        and event.acceptance_ref == accepted.acceptance_ref
    )


def _event_insert_values(event: WorkspaceLifecycleEventV1) -> tuple[Any, ...]:
    return (
        event.event_id,
        event.schema_version,
        event.workspace_id,
        event.transition_revision,
        event.from_state.value,
        event.to_state.value,
        event.graph_id,
        event.graph_revision,
        event.node_id,
        event.attempt_id,
        event.task_lease_id,
        event.task_lease_fencing_token,
        event.workspace_lease_ref,
        event.workspace_lease_owner,
        event.workspace_lease_fencing_token,
        event.base_commit,
        event.accepted_head,
        event.tree_digest,
        event.diff_digest,
        event.verification_ref,
        event.acceptance_ref,
        event.acceptance_event_id,
        event.terminal_ref,
        event.reason_code,
        event.previous_event_digest,
        event.event_digest,
        event.created_ts_ms,
    )


def _required_text(value: Any, *, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    normalized = value.strip()
    if normalized != value or len(normalized) > maximum:
        raise ValueError(f"{field} is not canonical")
    return normalized


def _positive_int(value: Any, *, field: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _nonnegative_int(value: Any, *, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()

def _normalized_sql_digest(value: Any) -> str:
    normalized = re.sub(r"\s+", "", str(value or "").lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


__all__ = [
    "WORKSPACE_LIFECYCLE_CONFLICT",
    "WORKSPACE_LIFECYCLE_DATA_INVALID",
    "WORKSPACE_LIFECYCLE_MIGRATION_ASSET_KEY",
    "WORKSPACE_LIFECYCLE_MIGRATION_SHA256",
    "WORKSPACE_LIFECYCLE_REPLAY_DRIFT",
    "WORKSPACE_LIFECYCLE_SCHEMA_NOT_READY",
    "WorkspaceLifecycleRepositoryMixin",
]
