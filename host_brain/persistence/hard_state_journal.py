"""SQLite authority for the runtime-fenced Host HardState mode journal."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

from host_brain.contracts.host_hard_state import (
    HOST_MODE_TRANSITIONS,
    HOST_SYS_MODES,
    HostHardStateCommitV1,
    HostHardStateEventV1,
    HostHardStateSnapshotV1,
)
from host_brain.core.ids import now_ms


HOST_HARD_STATE_MIGRATION_ASSET_KEY = "0115#host_hard_state_journal"
HOST_HARD_STATE_MIGRATION_SHA256 = (
    "688cc1675d1816bfdde3f7e31b526bcc14871026d7214d2874a649caef3b04ff"
)
_ALLOWED_MIGRATION_DISPOSITIONS = frozenset(
    {"APPLIED", "APPLIED_WITH_COMPAT", "LEGACY_BACKFILL"}
)
_EVENT_COLUMNS = frozenset(
    {
        "runtime_scope_id",
        "revision",
        "event_id",
        "idempotency_key",
        "event_kind",
        "from_mode",
        "to_mode",
        "source",
        "reason",
        "previous_event_digest",
        "event_digest",
        "runtime_owner_id",
        "runtime_lease_id",
        "runtime_fencing_token",
        "runtime_issued_at_ms",
        "runtime_expires_at_ms",
        "transition_ts_ms",
        "created_at_ms",
    }
)
_HEAD_COLUMNS = frozenset(
    {
        "runtime_scope_id",
        "revision",
        "sys_mode",
        "event_id",
        "event_digest",
        "updated_at_ms",
    }
)
_REQUIRED_TRIGGERS = frozenset(
    {
        "host_hard_state_event_no_update",
        "host_hard_state_event_no_delete",
        "host_hard_state_head_no_delete",
        "host_hard_state_event_requires_active_runtime_lease",
        "host_hard_state_event_requires_contiguous_head",
        "host_hard_state_head_requires_matching_event_insert",
        "host_hard_state_head_requires_matching_event_update",
    }
)


class HardStateJournalError(RuntimeError):
    """The durable HardState authority failed closed."""


def _canonical(payload: Mapping[str, object]) -> str:
    return json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256_ref(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _event_digest(payload: Mapping[str, object]) -> str:
    return _sha256_ref(_canonical(payload))


def _event_payload(
    *,
    runtime_scope_id: str,
    revision: int,
    event_id: str,
    idempotency_key: str,
    event_kind: str,
    from_mode: str,
    to_mode: str,
    source: str,
    reason: str,
    previous_event_digest: str,
    runtime_owner_id: str,
    runtime_lease_id: str,
    runtime_fencing_token: int,
    runtime_issued_at_ms: int,
    runtime_expires_at_ms: int,
    transition_ts_ms: int,
    created_at_ms: int,
) -> dict[str, object]:
    return {
        "created_at_ms": int(created_at_ms),
        "event_id": event_id,
        "event_kind": event_kind,
        "from_mode": from_mode,
        "idempotency_key": idempotency_key,
        "previous_event_digest": previous_event_digest,
        "reason": reason,
        "revision": int(revision),
        "runtime_expires_at_ms": int(runtime_expires_at_ms),
        "runtime_fencing_token": int(runtime_fencing_token),
        "runtime_issued_at_ms": int(runtime_issued_at_ms),
        "runtime_lease_id": runtime_lease_id,
        "runtime_owner_id": runtime_owner_id,
        "runtime_scope_id": runtime_scope_id,
        "source": source,
        "to_mode": to_mode,
        "transition_ts_ms": int(transition_ts_ms),
    }


def _event_from_row(row: sqlite3.Row) -> HostHardStateEventV1:
    return HostHardStateEventV1(
        runtime_scope_id=str(row["runtime_scope_id"]),
        revision=int(row["revision"]),
        event_id=str(row["event_id"]),
        event_kind=str(row["event_kind"]),
        from_mode=str(row["from_mode"]),
        to_mode=str(row["to_mode"]),
        source=str(row["source"]),
        reason=str(row["reason"]),
        previous_event_digest=str(row["previous_event_digest"]),
        event_digest=str(row["event_digest"]),
        runtime_owner_id=str(row["runtime_owner_id"]),
        runtime_lease_id=str(row["runtime_lease_id"]),
        runtime_fencing_token=int(row["runtime_fencing_token"]),
        runtime_issued_at_ms=int(row["runtime_issued_at_ms"]),
        runtime_expires_at_ms=int(row["runtime_expires_at_ms"]),
        transition_ts_ms=int(row["transition_ts_ms"]),
        created_at_ms=int(row["created_at_ms"]),
    )


class SQLiteHardStateJournal:
    """Append-only journal plus a transactional canonical head."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        runtime_scope_id: str,
        runtime_authority_provider: Callable[[], Mapping[str, Any]],
        clock: Callable[[], int] = now_ms,
        replay_page_size: int = 256,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        raw = str(db_path or "").strip()
        if not raw or raw == ":memory:" or raw.lower().startswith("file:"):
            raise HardStateJournalError("HOST_HARD_STATE_DURABLE_PATH_REQUIRED")
        self.db_path = Path(raw).expanduser().resolve()
        self._runtime_scope_id = str(runtime_scope_id or "").strip()
        if not self._runtime_scope_id:
            raise HardStateJournalError("HOST_HARD_STATE_RUNTIME_SCOPE_REQUIRED")
        if not callable(runtime_authority_provider):
            raise HardStateJournalError("HOST_HARD_STATE_AUTHORITY_PROVIDER_REQUIRED")
        if not callable(clock):
            raise HardStateJournalError("HOST_HARD_STATE_CLOCK_INVALID")
        self._runtime_authority_provider = runtime_authority_provider
        self._clock = clock
        self._replay_page_size = max(1, min(1000, int(replay_page_size)))
        if fault_injector is not None and not callable(fault_injector):
            raise HardStateJournalError("HOST_HARD_STATE_FAULT_INJECTOR_INVALID")
        self._fault_injector = fault_injector
        self.assert_schema_ready()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(self.db_path), timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()

    @contextmanager
    def _read_transaction(self) -> Iterator[sqlite3.Connection]:
        """Keep paginated replay and its head read on one SQLite snapshot."""

        with self._connection() as connection:
            connection.execute("BEGIN")
            try:
                yield connection
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()

    def assert_schema_ready(self) -> None:
        if not self.db_path.is_file():
            raise HardStateJournalError("HOST_HARD_STATE_SCHEMA_UNAVAILABLE")
        migration_path = (
            Path(__file__).resolve().parents[1]
            / "audit"
            / "migrations"
            / "0115_host_hard_state_journal.sql"
        )
        source_checksum = hashlib.sha256(
            migration_path.read_text(encoding="utf-8-sig").encode("utf-8")
        ).hexdigest()
        try:
            with self._connection() as connection:
                asset = connection.execute(
                    "SELECT checksum, disposition FROM schema_migration_assets WHERE asset_key = ?",
                    (HOST_HARD_STATE_MIGRATION_ASSET_KEY,),
                ).fetchone()
                event_info = connection.execute(
                    "PRAGMA table_info(host_hard_state_event)"
                ).fetchall()
                head_info = connection.execute(
                    "PRAGMA table_info(host_hard_state_head)"
                ).fetchall()
                event_columns = frozenset(str(row[1]) for row in event_info)
                head_columns = frozenset(str(row[1]) for row in head_info)
                event_shape = {
                    str(row[1]): (str(row[2]).upper(), int(row[3]), int(row[5]))
                    for row in event_info
                }
                head_shape = {
                    str(row[1]): (str(row[2]).upper(), int(row[3]), int(row[5]))
                    for row in head_info
                }
                head_foreign_keys = {
                    (str(row[2]), str(row[3]), str(row[4]))
                    for row in connection.execute(
                        "PRAGMA foreign_key_list(host_hard_state_head)"
                    ).fetchall()
                }
                indexes = {
                    str(row[1])
                    for row in connection.execute(
                        "PRAGMA index_list(host_hard_state_event)"
                    ).fetchall()
                }
                triggers = {
                    str(row[0]): str(row[1] or "")
                    for row in connection.execute(
                        "SELECT name, sql FROM sqlite_master WHERE type = 'trigger'"
                    ).fetchall()
                }
        except (OSError, sqlite3.Error) as exc:
            raise HardStateJournalError("HOST_HARD_STATE_MIGRATION_DRIFT") from exc
        if (
            asset is None
            or source_checksum != HOST_HARD_STATE_MIGRATION_SHA256
            or str(asset[0]) != HOST_HARD_STATE_MIGRATION_SHA256
            or str(asset[1]) not in _ALLOWED_MIGRATION_DISPOSITIONS
        ):
            raise HardStateJournalError("HOST_HARD_STATE_MIGRATION_DRIFT")
        if event_columns != _EVENT_COLUMNS or head_columns != _HEAD_COLUMNS:
            raise HardStateJournalError("HOST_HARD_STATE_SCHEMA_DRIFT")
        expected_event_shape = {
            name: (
                "INTEGER"
                if name
                in {
                    "revision",
                    "runtime_fencing_token",
                    "runtime_issued_at_ms",
                    "runtime_expires_at_ms",
                    "transition_ts_ms",
                    "created_at_ms",
                }
                else "TEXT",
                1,
                1 if name == "runtime_scope_id" else 2 if name == "revision" else 0,
            )
            for name in _EVENT_COLUMNS
        }
        expected_head_shape = {
            name: (
                "INTEGER" if name in {"revision", "updated_at_ms"} else "TEXT",
                1,
                1 if name == "runtime_scope_id" else 0,
            )
            for name in _HEAD_COLUMNS
        }
        if event_shape != expected_event_shape or head_shape != expected_head_shape:
            raise HardStateJournalError("HOST_HARD_STATE_SCHEMA_DRIFT")
        if head_foreign_keys != {
            ("host_hard_state_event", "runtime_scope_id", "runtime_scope_id"),
            ("host_hard_state_event", "revision", "revision"),
        }:
            raise HardStateJournalError("HOST_HARD_STATE_SCHEMA_DRIFT")
        if "idx_host_hard_state_event_scope_time" not in indexes:
            raise HardStateJournalError("HOST_HARD_STATE_SCHEMA_DRIFT")
        if not _REQUIRED_TRIGGERS.issubset(triggers):
            raise HardStateJournalError("HOST_HARD_STATE_SCHEMA_DRIFT")
        authority_sql = "".join(
            triggers["host_hard_state_event_requires_active_runtime_lease"]
            .lower()
            .split()
        )
        for fragment in (
            "host_embodiment_runtime_lease",
            "lease.runtime_scope_id=new.runtime_scope_id",
            "lease.owner_id=new.runtime_owner_id",
            "lease.lease_id=new.runtime_lease_id",
            "lease.fencing_token=new.runtime_fencing_token",
            "lease.released_at_msisnull",
            "lease.expires_at_ms>new.transition_ts_ms",
        ):
            if fragment not in authority_sql:
                raise HardStateJournalError("HOST_HARD_STATE_SCHEMA_DRIFT")

    def _read_authority(self, *, now: int) -> dict[str, Any]:
        try:
            raw = self._runtime_authority_provider()
        except Exception as exc:
            raise HardStateJournalError(
                "HOST_HARD_STATE_RUNTIME_AUTHORITY_UNAVAILABLE"
            ) from exc
        if not isinstance(raw, Mapping):
            raise HardStateJournalError("HOST_HARD_STATE_RUNTIME_AUTHORITY_INVALID")
        authority = dict(raw)
        try:
            scope = str(authority.get("runtime_scope_id") or "").strip()
            owner = str(authority.get("owner_id") or "").strip()
            lease = str(authority.get("lease_id") or "").strip()
            fence = int(authority.get("fencing_token"))
            issued = int(authority.get("issued_at_ms"))
            expires = int(authority.get("expires_at_ms"))
        except (TypeError, ValueError) as exc:
            raise HardStateJournalError(
                "HOST_HARD_STATE_RUNTIME_AUTHORITY_INVALID"
            ) from exc
        if (
            scope != self._runtime_scope_id
            or not owner
            or not lease
            or fence < 1
            or issued > now
            or expires <= now
            or authority.get("released_at_ms") is not None
        ):
            raise HardStateJournalError("HOST_HARD_STATE_RUNTIME_AUTHORITY_STALE")
        return {
            "runtime_scope_id": scope,
            "owner_id": owner,
            "lease_id": lease,
            "fencing_token": fence,
            "issued_at_ms": issued,
            "expires_at_ms": expires,
        }

    def _assert_authority_row(
        self,
        connection: sqlite3.Connection,
        authority: Mapping[str, Any],
        *,
        now: int,
    ) -> dict[str, Any]:
        row = connection.execute(
            "SELECT runtime_scope_id, owner_id, lease_id, fencing_token, issued_at_ms, expires_at_ms "
            "FROM host_embodiment_runtime_lease "
            "WHERE runtime_scope_id = ? AND owner_id = ? AND lease_id = ? "
            "AND fencing_token = ? AND issued_at_ms = ? AND expires_at_ms >= ? "
            "AND released_at_ms IS NULL AND expires_at_ms > ?",
            (
                self._runtime_scope_id,
                authority["owner_id"],
                authority["lease_id"],
                int(authority["fencing_token"]),
                int(authority["issued_at_ms"]),
                int(authority["expires_at_ms"]),
                int(now),
            ),
        ).fetchone()
        if row is None:
            raise HardStateJournalError("HOST_HARD_STATE_RUNTIME_AUTHORITY_FENCED")
        return dict(row)

    @staticmethod
    def _snapshot_from_event(event: HostHardStateEventV1) -> HostHardStateSnapshotV1:
        return HostHardStateSnapshotV1(
            runtime_scope_id=event.runtime_scope_id,
            revision=event.revision,
            sys_mode=event.to_mode,
            event_id=event.event_id,
            event_digest=event.event_digest,
            updated_at_ms=event.transition_ts_ms,
        )

    def _verified_state(
        self, connection: sqlite3.Connection
    ) -> tuple[HostHardStateSnapshotV1 | None, list[HostHardStateEventV1]]:
        head = connection.execute(
            "SELECT * FROM host_hard_state_head WHERE runtime_scope_id = ?",
            (self._runtime_scope_id,),
        ).fetchone()
        events: list[HostHardStateEventV1] = []
        cursor = 0
        prior: HostHardStateEventV1 | None = None
        lease_fences: dict[str, int] = {}
        while True:
            rows = connection.execute(
                "SELECT * FROM host_hard_state_event "
                "WHERE runtime_scope_id = ? AND revision > ? "
                "ORDER BY revision ASC LIMIT ?",
                (self._runtime_scope_id, cursor, self._replay_page_size),
            ).fetchall()
            if not rows:
                break
            for row in rows:
                event = _event_from_row(row)
                if event.revision != cursor + 1:
                    raise HardStateJournalError("HOST_HARD_STATE_REVISION_CHAIN_INVALID")
                if str(row["idempotency_key"]) != _sha256_ref(event.event_id):
                    raise HardStateJournalError("HOST_HARD_STATE_IDEMPOTENCY_DRIFT")
                if prior is None:
                    valid_identity = (
                        event.event_kind == "INITIALIZED"
                        and event.from_mode == event.to_mode
                        and event.previous_event_digest == ""
                    )
                else:
                    valid_identity = (
                        event.event_kind == "MODE_CHANGED"
                        and event.from_mode == prior.to_mode
                        and event.previous_event_digest == prior.event_digest
                    )
                if not valid_identity:
                    raise HardStateJournalError("HOST_HARD_STATE_DIGEST_CHAIN_INVALID")
                if (
                    event.runtime_scope_id != self._runtime_scope_id
                    or not event.runtime_owner_id
                    or not event.runtime_lease_id
                    or event.runtime_fencing_token < 1
                    or event.runtime_issued_at_ms > event.transition_ts_ms
                    or event.runtime_issued_at_ms > event.created_at_ms
                    or event.runtime_expires_at_ms <= event.transition_ts_ms
                    or event.runtime_expires_at_ms <= event.created_at_ms
                    or event.transition_ts_ms > event.created_at_ms
                ):
                    raise HardStateJournalError("HOST_HARD_STATE_AUTHORITY_LINEAGE_INVALID")
                prior_fence_for_lease = lease_fences.setdefault(
                    event.runtime_lease_id, event.runtime_fencing_token
                )
                if prior_fence_for_lease != event.runtime_fencing_token:
                    raise HardStateJournalError("HOST_HARD_STATE_LEASE_REUSE_INVALID")
                if prior is not None:
                    if (
                        event.runtime_fencing_token < prior.runtime_fencing_token
                        or event.transition_ts_ms < prior.transition_ts_ms
                        or event.created_at_ms < prior.created_at_ms
                        or event.runtime_issued_at_ms < prior.runtime_issued_at_ms
                    ):
                        raise HardStateJournalError("HOST_HARD_STATE_AUTHORITY_LINEAGE_INVALID")
                    if event.runtime_fencing_token == prior.runtime_fencing_token:
                        if (
                            event.runtime_owner_id != prior.runtime_owner_id
                            or event.runtime_lease_id != prior.runtime_lease_id
                            or event.runtime_issued_at_ms != prior.runtime_issued_at_ms
                            or event.runtime_expires_at_ms < prior.runtime_expires_at_ms
                        ):
                            raise HardStateJournalError("HOST_HARD_STATE_AUTHORITY_LINEAGE_INVALID")
                    elif event.runtime_lease_id == prior.runtime_lease_id:
                        raise HardStateJournalError("HOST_HARD_STATE_LEASE_REUSE_INVALID")
                payload = _event_payload(
                    runtime_scope_id=event.runtime_scope_id,
                    revision=event.revision,
                    event_id=event.event_id,
                    idempotency_key=str(row["idempotency_key"]),
                    event_kind=event.event_kind,
                    from_mode=event.from_mode,
                    to_mode=event.to_mode,
                    source=event.source,
                    reason=event.reason,
                    previous_event_digest=event.previous_event_digest,
                    runtime_owner_id=event.runtime_owner_id,
                    runtime_lease_id=event.runtime_lease_id,
                    runtime_fencing_token=event.runtime_fencing_token,
                    runtime_issued_at_ms=event.runtime_issued_at_ms,
                    runtime_expires_at_ms=event.runtime_expires_at_ms,
                    transition_ts_ms=event.transition_ts_ms,
                    created_at_ms=event.created_at_ms,
                )
                if _event_digest(payload) != event.event_digest:
                    raise HardStateJournalError("HOST_HARD_STATE_EVENT_DIGEST_INVALID")
                events.append(event)
                prior = event
                cursor = event.revision
            if len(rows) < self._replay_page_size:
                break
        if not events:
            if head is not None:
                raise HardStateJournalError("HOST_HARD_STATE_HEAD_ORPHANED")
            return None, []
        if head is None or prior is None:
            raise HardStateJournalError("HOST_HARD_STATE_HEAD_MISSING")
        snapshot = self._snapshot_from_event(prior)
        if (
            str(head["runtime_scope_id"]) != snapshot.runtime_scope_id
            or int(head["revision"]) != snapshot.revision
            or str(head["sys_mode"]) != snapshot.sys_mode
            or str(head["event_id"]) != snapshot.event_id
            or str(head["event_digest"]) != snapshot.event_digest
            or int(head["updated_at_ms"]) != snapshot.updated_at_ms
        ):
            raise HardStateJournalError("HOST_HARD_STATE_HEAD_DRIFT")
        return snapshot, events

    def load(self) -> HostHardStateSnapshotV1 | None:
        with self._read_transaction() as connection:
            snapshot, _ = self._verified_state(connection)
        return snapshot

    def _insert_event(
        self,
        connection: sqlite3.Connection,
        *,
        revision: int,
        event_id: str,
        event_kind: str,
        from_mode: str,
        to_mode: str,
        source: str,
        reason: str,
        previous_event_digest: str,
        transition_ts_ms: int,
        created_at_ms: int,
        authority: Mapping[str, Any],
    ) -> HostHardStateEventV1:
        idempotency_key = _sha256_ref(event_id)
        payload = _event_payload(
            runtime_scope_id=self._runtime_scope_id,
            revision=revision,
            event_id=event_id,
            idempotency_key=idempotency_key,
            event_kind=event_kind,
            from_mode=from_mode,
            to_mode=to_mode,
            source=source,
            reason=reason,
            previous_event_digest=previous_event_digest,
            runtime_owner_id=str(authority["owner_id"]),
            runtime_lease_id=str(authority["lease_id"]),
            runtime_fencing_token=int(authority["fencing_token"]),
            runtime_issued_at_ms=int(authority["issued_at_ms"]),
            runtime_expires_at_ms=int(authority["expires_at_ms"]),
            transition_ts_ms=transition_ts_ms,
            created_at_ms=created_at_ms,
        )
        digest = _event_digest(payload)
        connection.execute(
            "INSERT INTO host_hard_state_event(" + ",".join(payload) + ",event_digest) "
            "VALUES (" + ",".join("?" for _ in range(len(payload) + 1)) + ")",
            tuple(payload.values()) + (digest,),
        )
        row = connection.execute(
            "SELECT * FROM host_hard_state_event WHERE runtime_scope_id = ? AND revision = ?",
            (self._runtime_scope_id, revision),
        ).fetchone()
        if row is None:
            raise HardStateJournalError("HOST_HARD_STATE_EVENT_APPEND_LOST")
        return _event_from_row(row)

    def load_or_initialize(
        self,
        *,
        initial_mode: str,
        event_id: str,
        transition_ts_ms: int,
    ) -> HostHardStateCommitV1:
        mode = self._normalize_mode(initial_mode)
        identity = self._normalize_text(event_id, field="EVENT_ID", limit=512)
        transition_ts = self._normalize_ts(transition_ts_ms)
        preflight_now = int(self._clock())
        if transition_ts > preflight_now:
            raise HardStateJournalError("HOST_HARD_STATE_TIME_INVALID")
        authority = self._read_authority(now=preflight_now)
        try:
            with self._transaction() as connection:
                mutation_now = int(self._clock())
                if transition_ts > mutation_now:
                    raise HardStateJournalError("HOST_HARD_STATE_TIME_INVALID")
                durable_authority = self._assert_authority_row(
                    connection, authority, now=mutation_now
                )
                snapshot, events = self._verified_state(connection)
                if snapshot is not None:
                    return HostHardStateCommitV1(
                        snapshot=snapshot,
                        event=events[-1],
                        exact_replay=False,
                    )
                event = self._insert_event(
                    connection,
                    revision=1,
                    event_id=identity,
                    event_kind="INITIALIZED",
                    from_mode=mode,
                    to_mode=mode,
                    source="host_bootstrap",
                    reason="durable Host HardState authority initialized",
                    previous_event_digest="",
                    transition_ts_ms=transition_ts,
                    created_at_ms=mutation_now,
                    authority=durable_authority,
                )
                self._inject_fault("after_event_append")
                connection.execute(
                    "INSERT INTO host_hard_state_head(runtime_scope_id, revision, sys_mode, event_id, event_digest, updated_at_ms) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        self._runtime_scope_id,
                        event.revision,
                        event.to_mode,
                        event.event_id,
                        event.event_digest,
                        event.transition_ts_ms,
                    ),
                )
                self._inject_fault("after_head_write")
                snapshot = self._snapshot_from_event(event)
                return HostHardStateCommitV1(snapshot=snapshot, event=event)
        except HardStateJournalError:
            raise
        except sqlite3.Error as exc:
            raise HardStateJournalError("HOST_HARD_STATE_INITIALIZE_FAILED") from exc

    def transition(
        self,
        *,
        expected_revision: int,
        expected_mode: str,
        target_mode: str,
        event_id: str,
        source: str,
        reason: str,
        transition_ts_ms: int,
    ) -> HostHardStateCommitV1:
        revision = int(expected_revision)
        if revision < 1:
            raise HardStateJournalError("HOST_HARD_STATE_EXPECTED_REVISION_INVALID")
        from_mode = self._normalize_mode(expected_mode)
        to_mode = self._normalize_mode(target_mode)
        identity = self._normalize_text(event_id, field="EVENT_ID", limit=512)
        transition_source = self._normalize_text(source, field="SOURCE", limit=128)
        transition_reason = str(reason or "")
        if len(transition_reason) > 2048:
            raise HardStateJournalError("HOST_HARD_STATE_REASON_INVALID")
        transition_ts = self._normalize_ts(transition_ts_ms)
        preflight_now = int(self._clock())
        if transition_ts > preflight_now:
            raise HardStateJournalError("HOST_HARD_STATE_TIME_INVALID")
        authority = self._read_authority(now=preflight_now)
        try:
            with self._transaction() as connection:
                mutation_now = int(self._clock())
                if transition_ts > mutation_now:
                    raise HardStateJournalError("HOST_HARD_STATE_TIME_INVALID")
                durable_authority = self._assert_authority_row(
                    connection, authority, now=mutation_now
                )
                snapshot, events = self._verified_state(connection)
                if snapshot is None:
                    raise HardStateJournalError("HOST_HARD_STATE_NOT_INITIALIZED")
                existing = next((event for event in events if event.event_id == identity), None)
                if existing is not None:
                    self._assert_exact_replay(
                        existing,
                        expected_revision=revision,
                        expected_mode=from_mode,
                        target_mode=to_mode,
                        source=transition_source,
                        reason=transition_reason,
                    )
                    return HostHardStateCommitV1(
                        snapshot=snapshot,
                        event=existing,
                        exact_replay=True,
                    )
                if snapshot.revision != revision or snapshot.sys_mode != from_mode:
                    raise HardStateJournalError("HOST_HARD_STATE_EXPECTED_HEAD_CONFLICT")
                if from_mode == to_mode:
                    raise HardStateJournalError("HOST_HARD_STATE_NOOP_TRANSITION_REJECTED")
                if to_mode not in HOST_MODE_TRANSITIONS.get(from_mode, frozenset()):
                    raise HardStateJournalError("HOST_HARD_STATE_TRANSITION_PATH_REJECTED")
                if transition_ts < snapshot.updated_at_ms:
                    raise HardStateJournalError("HOST_HARD_STATE_TRANSITION_TIME_REGRESSION")
                event = self._insert_event(
                    connection,
                    revision=revision + 1,
                    event_id=identity,
                    event_kind="MODE_CHANGED",
                    from_mode=from_mode,
                    to_mode=to_mode,
                    source=transition_source,
                    reason=transition_reason,
                    previous_event_digest=snapshot.event_digest,
                    transition_ts_ms=transition_ts,
                    created_at_ms=mutation_now,
                    authority=durable_authority,
                )
                self._inject_fault("after_event_append")
                updated = connection.execute(
                    "UPDATE host_hard_state_head SET revision = ?, sys_mode = ?, event_id = ?, event_digest = ?, updated_at_ms = ? "
                    "WHERE runtime_scope_id = ? AND revision = ? AND sys_mode = ? AND event_digest = ?",
                    (
                        event.revision,
                        event.to_mode,
                        event.event_id,
                        event.event_digest,
                        event.transition_ts_ms,
                        self._runtime_scope_id,
                        revision,
                        from_mode,
                        snapshot.event_digest,
                    ),
                )
                if updated.rowcount != 1:
                    raise HardStateJournalError("HOST_HARD_STATE_HEAD_CAS_LOST")
                self._inject_fault("after_head_write")
                return HostHardStateCommitV1(
                    snapshot=self._snapshot_from_event(event),
                    event=event,
                )
        except HardStateJournalError:
            raise
        except sqlite3.Error as exc:
            raise HardStateJournalError("HOST_HARD_STATE_TRANSITION_FAILED") from exc

    def replay_transition(
        self,
        *,
        target_mode: str,
        event_id: str,
        source: str,
        reason: str,
    ) -> HostHardStateCommitV1 | None:
        """Read an exact historical commit without reapplying its effect."""

        to_mode = self._normalize_mode(target_mode)
        identity = self._normalize_text(event_id, field="EVENT_ID", limit=512)
        transition_source = self._normalize_text(source, field="SOURCE", limit=128)
        transition_reason = str(reason or "")
        if len(transition_reason) > 2048:
            raise HardStateJournalError("HOST_HARD_STATE_REASON_INVALID")
        with self._read_transaction() as connection:
            snapshot, events = self._verified_state(connection)
            if snapshot is None:
                return None
            existing = next(
                (event for event in events if event.event_id == identity), None
            )
            if existing is None:
                return None
            self._assert_exact_replay(
                existing,
                target_mode=to_mode,
                source=transition_source,
                reason=transition_reason,
            )
            return HostHardStateCommitV1(
                snapshot=snapshot,
                event=existing,
                exact_replay=True,
            )

    def history(self, *, limit: int = 100) -> Sequence[HostHardStateEventV1]:
        resolved_limit = max(1, min(1000, int(limit)))
        with self._read_transaction() as connection:
            _, events = self._verified_state(connection)
        return tuple(reversed(events[-resolved_limit:]))

    @staticmethod
    def _assert_exact_replay(
        event: HostHardStateEventV1,
        *,
        expected_revision: int | None = None,
        expected_mode: str | None = None,
        target_mode: str,
        source: str,
        reason: str,
    ) -> None:
        if (
            event.event_kind != "MODE_CHANGED"
            or (
                expected_revision is not None
                and event.revision != expected_revision + 1
            )
            or (expected_mode is not None and event.from_mode != expected_mode)
            or event.to_mode != target_mode
            or event.source != source
            or event.reason != reason
        ):
            raise HardStateJournalError("HOST_HARD_STATE_REPLAY_DRIFT")

    @staticmethod
    def _normalize_mode(value: str) -> str:
        mode = str(value or "").strip().lower()
        if mode not in HOST_SYS_MODES:
            raise HardStateJournalError("HOST_HARD_STATE_MODE_INVALID")
        return mode

    @staticmethod
    def _normalize_text(value: str, *, field: str, limit: int) -> str:
        text = str(value or "").strip()
        if not text or len(text) > limit:
            raise HardStateJournalError(f"HOST_HARD_STATE_{field}_INVALID")
        return text

    @staticmethod
    def _normalize_ts(value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise HardStateJournalError("HOST_HARD_STATE_TIME_INVALID")
        return int(value)

    def _inject_fault(self, point: str) -> None:
        if self._fault_injector is not None:
            self._fault_injector(point)


__all__ = [
    "HOST_HARD_STATE_MIGRATION_ASSET_KEY",
    "HOST_HARD_STATE_MIGRATION_SHA256",
    "HardStateJournalError",
    "SQLiteHardStateJournal",
]
