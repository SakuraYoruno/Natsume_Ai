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
    ) -> None:
        row = conn.execute(
            """
            SELECT attempt.graph_id, attempt.graph_revision, attempt.node_id,
                   lease.attempt_id, lease.fencing_token,
                   lease.status, lease.expires_ts_ms
            FROM task_runtime_attempt AS attempt
            JOIN task_runtime_lease AS lease
              ON lease.attempt_id = attempt.attempt_id
            WHERE attempt.attempt_id = ? AND lease.lease_id = ?
            """,
            (attempt_id, task_lease_id),
        ).fetchone()
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
                and (
                    str(row["status"]) != "ACTIVE"
                    or type(row["expires_ts_ms"]) is not int
                    or int(row["expires_ts_ms"]) <= ts_ms
                )
            )
        ):
            raise self._workspace_operation_error(
                WORKSPACE_STALE_FENCE,
                "TaskNode Attempt/Lease binding is absent, expired, or stale",
            )

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
