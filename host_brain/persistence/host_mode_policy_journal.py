"""SQLite authority for Host auto-mode rules and interaction observations."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

from host_brain.contracts.host_mode_policy import (
    HOST_MODE_AUTO_RULES,
    HOST_MODE_POLICY_EVENT_KINDS,
    HostModePolicyCommitV1,
    HostModePolicyEventV1,
    HostModePolicySnapshotV1,
)
from host_brain.core.ids import now_ms


HOST_MODE_POLICY_MIGRATION_ASSET_KEY = "0129#host_mode_policy_journal"
_MIGRATION_FILE = "0129_host_mode_policy_journal.sql"
_ALLOWED_MIGRATION_DISPOSITIONS = frozenset(
    {"APPLIED", "APPLIED_WITH_COMPAT", "LEGACY_BACKFILL"}
)
_EVENT_COLUMNS = frozenset(
    {
        "runtime_scope_id",
        "revision",
        "operation_id",
        "event_kind",
        "input_digest",
        "event_payload_json",
        "policy_json",
        "last_interaction_ts_ms",
        "previous_event_digest",
        "event_digest",
        "runtime_owner_id",
        "runtime_lease_id",
        "runtime_fencing_token",
        "runtime_issued_at_ms",
        "runtime_expires_at_ms",
        "observed_at_ms",
        "created_at_ms",
    }
)
_HEAD_COLUMNS = frozenset(
    {
        "runtime_scope_id",
        "revision",
        "operation_id",
        "policy_json",
        "last_interaction_ts_ms",
        "event_digest",
        "updated_at_ms",
    }
)
_REQUIRED_TRIGGERS = frozenset(
    {
        "host_mode_policy_event_no_update",
        "host_mode_policy_event_no_delete",
        "host_mode_policy_head_no_delete",
        "host_mode_policy_event_requires_active_runtime_lease",
        "host_mode_policy_event_requires_contiguous_head",
        "host_mode_policy_head_requires_matching_event_insert",
        "host_mode_policy_head_requires_matching_event_update",
    }
)


class HostModePolicyJournalError(RuntimeError):
    """The canonical mode-policy journal failed closed."""


def _canonical(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise HostModePolicyJournalError(
            "HOST_MODE_POLICY_CANONICAL_JSON_INVALID"
        ) from exc


def _digest(value: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(
        _canonical(value).encode("utf-8")
    ).hexdigest()


def _required_text(value: Any, *, field: str, limit: int = 512) -> str:
    text = str(value or "").strip()
    if (
        not text
        or len(text.encode("utf-8")) > int(limit)
        or any(ord(character) < 32 or ord(character) == 127 for character in text)
    ):
        raise HostModePolicyJournalError(f"HOST_MODE_POLICY_{field.upper()}_INVALID")
    return text


def _non_negative_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or type(value) is not int or value < 0:
        raise HostModePolicyJournalError(f"HOST_MODE_POLICY_{field.upper()}_INVALID")
    return value


def _positive_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or type(value) is not int or value < 1:
        raise HostModePolicyJournalError(f"HOST_MODE_POLICY_{field.upper()}_INVALID")
    return value


def _rules(value: Mapping[str, Any]) -> dict[str, bool]:
    candidate = dict(value or {})
    if set(candidate) != set(HOST_MODE_AUTO_RULES) or any(
        type(candidate[key]) is not bool for key in HOST_MODE_AUTO_RULES
    ):
        raise HostModePolicyJournalError("HOST_MODE_POLICY_RULES_INVALID")
    return {key: bool(candidate[key]) for key in sorted(HOST_MODE_AUTO_RULES)}


def _json_mapping(raw: Any, *, field: str) -> dict[str, Any]:
    try:
        value = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HostModePolicyJournalError(
            f"HOST_MODE_POLICY_{field.upper()}_INVALID"
        ) from exc
    if not isinstance(value, Mapping):
        raise HostModePolicyJournalError(
            f"HOST_MODE_POLICY_{field.upper()}_INVALID"
        )
    return dict(value)


def _event_digest_payload(
    *,
    runtime_scope_id: str,
    revision: int,
    operation_id: str,
    event_kind: str,
    input_digest: str,
    event_payload: Mapping[str, Any],
    auto_rules: Mapping[str, bool],
    last_interaction_ts_ms: int,
    previous_event_digest: str,
    runtime_owner_id: str,
    runtime_lease_id: str,
    runtime_fencing_token: int,
    runtime_issued_at_ms: int,
    runtime_expires_at_ms: int,
    observed_at_ms: int,
    created_at_ms: int,
) -> dict[str, Any]:
    return {
        "auto_rules": _rules(auto_rules),
        "created_at_ms": int(created_at_ms),
        "event_kind": str(event_kind),
        "event_payload": dict(event_payload),
        "input_digest": str(input_digest),
        "last_interaction_ts_ms": int(last_interaction_ts_ms),
        "observed_at_ms": int(observed_at_ms),
        "operation_id": str(operation_id),
        "previous_event_digest": str(previous_event_digest),
        "revision": int(revision),
        "runtime_expires_at_ms": int(runtime_expires_at_ms),
        "runtime_fencing_token": int(runtime_fencing_token),
        "runtime_issued_at_ms": int(runtime_issued_at_ms),
        "runtime_lease_id": str(runtime_lease_id),
        "runtime_owner_id": str(runtime_owner_id),
        "runtime_scope_id": str(runtime_scope_id),
    }


def _event_from_row(row: sqlite3.Row) -> HostModePolicyEventV1:
    return HostModePolicyEventV1(
        runtime_scope_id=str(row["runtime_scope_id"]),
        revision=int(row["revision"]),
        operation_id=str(row["operation_id"]),
        event_kind=str(row["event_kind"]),
        input_digest=str(row["input_digest"]),
        event_payload=_json_mapping(
            row["event_payload_json"],
            field="event_payload_json",
        ),
        auto_rules=_rules(
            _json_mapping(row["policy_json"], field="policy_json")
        ),
        last_interaction_ts_ms=int(row["last_interaction_ts_ms"]),
        previous_event_digest=str(row["previous_event_digest"]),
        event_digest=str(row["event_digest"]),
        runtime_owner_id=str(row["runtime_owner_id"]),
        runtime_lease_id=str(row["runtime_lease_id"]),
        runtime_fencing_token=int(row["runtime_fencing_token"]),
        runtime_issued_at_ms=int(row["runtime_issued_at_ms"]),
        runtime_expires_at_ms=int(row["runtime_expires_at_ms"]),
        observed_at_ms=int(row["observed_at_ms"]),
        created_at_ms=int(row["created_at_ms"]),
    )


def _snapshot_from_row(row: sqlite3.Row) -> HostModePolicySnapshotV1:
    return HostModePolicySnapshotV1(
        runtime_scope_id=str(row["runtime_scope_id"]),
        revision=int(row["revision"]),
        auto_rules=_rules(
            _json_mapping(row["policy_json"], field="policy_json")
        ),
        last_interaction_ts_ms=int(row["last_interaction_ts_ms"]),
        operation_id=str(row["operation_id"]),
        event_digest=str(row["event_digest"]),
        updated_at_ms=int(row["updated_at_ms"]),
    )


class SQLiteHostModePolicyJournal:
    """Append-only mode-policy events plus one transactional canonical head."""

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
        raw_path = str(db_path or "").strip()
        if not raw_path or raw_path == ":memory:" or raw_path.lower().startswith("file:"):
            raise HostModePolicyJournalError(
                "HOST_MODE_POLICY_DURABLE_PATH_REQUIRED"
            )
        self.db_path = Path(raw_path).expanduser().resolve()
        self._runtime_scope_id = _required_text(
            runtime_scope_id,
            field="runtime_scope_id",
        )
        if not callable(runtime_authority_provider):
            raise HostModePolicyJournalError(
                "HOST_MODE_POLICY_AUTHORITY_PROVIDER_REQUIRED"
            )
        if not callable(clock):
            raise HostModePolicyJournalError("HOST_MODE_POLICY_CLOCK_INVALID")
        if replay_page_size < 1 or replay_page_size > 10_000:
            raise HostModePolicyJournalError(
                "HOST_MODE_POLICY_REPLAY_PAGE_SIZE_INVALID"
            )
        if fault_injector is not None and not callable(fault_injector):
            raise HostModePolicyJournalError(
                "HOST_MODE_POLICY_FAULT_INJECTOR_INVALID"
            )
        self._authority_provider = runtime_authority_provider
        self._clock = clock
        self._page_size = int(replay_page_size)
        self._fault_injector = fault_injector

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            str(self.db_path),
            timeout=30,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()

    def assert_schema_ready(self) -> None:
        if not self.db_path.is_file():
            raise HostModePolicyJournalError(
                "HOST_MODE_POLICY_SCHEMA_UNAVAILABLE"
            )
        migration_path = (
            Path(__file__).resolve().parents[1]
            / "audit"
            / "migrations"
            / _MIGRATION_FILE
        )
        try:
            checksum = hashlib.sha256(
                migration_path.read_text(encoding="utf-8-sig").encode("utf-8")
            ).hexdigest()
            with self._connection() as connection:
                asset = connection.execute(
                    """
                    SELECT checksum, disposition
                    FROM schema_migration_assets
                    WHERE asset_key = ?
                    """,
                    (HOST_MODE_POLICY_MIGRATION_ASSET_KEY,),
                ).fetchone()
                event_columns = frozenset(
                    str(row[1])
                    for row in connection.execute(
                        "PRAGMA table_info(host_mode_policy_event)"
                    ).fetchall()
                )
                head_columns = frozenset(
                    str(row[1])
                    for row in connection.execute(
                        "PRAGMA table_info(host_mode_policy_head)"
                    ).fetchall()
                )
                triggers = frozenset(
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'trigger'"
                    ).fetchall()
                )
        except (OSError, sqlite3.Error) as exc:
            raise HostModePolicyJournalError(
                "HOST_MODE_POLICY_MIGRATION_DRIFT"
            ) from exc
        if (
            asset is None
            or str(asset["checksum"]) != checksum
            or str(asset["disposition"]) not in _ALLOWED_MIGRATION_DISPOSITIONS
        ):
            raise HostModePolicyJournalError("HOST_MODE_POLICY_MIGRATION_DRIFT")
        if (
            event_columns != _EVENT_COLUMNS
            or head_columns != _HEAD_COLUMNS
            or not _REQUIRED_TRIGGERS.issubset(triggers)
        ):
            raise HostModePolicyJournalError("HOST_MODE_POLICY_SCHEMA_DRIFT")

    def _authority(self, *, created_at_ms: int) -> dict[str, Any]:
        try:
            raw = self._authority_provider()
        except Exception as exc:
            raise HostModePolicyJournalError(
                "HOST_MODE_POLICY_RUNTIME_AUTHORITY_UNAVAILABLE"
            ) from exc
        if not isinstance(raw, Mapping):
            raise HostModePolicyJournalError(
                "HOST_MODE_POLICY_RUNTIME_AUTHORITY_INVALID"
            )
        value = dict(raw)
        scope = _required_text(
            value.get("runtime_scope_id"),
            field="runtime_scope_id",
        )
        owner = _required_text(value.get("owner_id"), field="owner_id")
        lease = _required_text(value.get("lease_id"), field="lease_id")
        fence = _positive_int(value.get("fencing_token"), field="fencing_token")
        issued = _non_negative_int(value.get("issued_at_ms"), field="issued_at_ms")
        expires = _positive_int(value.get("expires_at_ms"), field="expires_at_ms")
        if (
            scope != self._runtime_scope_id
            or expires <= issued
            or issued > created_at_ms
            or expires <= created_at_ms
        ):
            raise HostModePolicyJournalError(
                "HOST_MODE_POLICY_RUNTIME_AUTHORITY_STALE"
            )
        return {
            "runtime_owner_id": owner,
            "runtime_lease_id": lease,
            "runtime_fencing_token": fence,
            "runtime_issued_at_ms": issued,
            "runtime_expires_at_ms": expires,
        }

    def load(self) -> HostModePolicySnapshotV1 | None:
        self.assert_schema_ready()
        with self._connection() as connection:
            connection.execute("BEGIN")
            try:
                snapshot, _ = self._verified_state(connection)
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()
                return snapshot

    def load_or_initialize(
        self,
        *,
        initial_auto_rules: Mapping[str, bool],
        initial_last_interaction_ts_ms: int,
        operation_id: str,
        observed_at_ms: int,
    ) -> HostModePolicyCommitV1:
        rules = _rules(initial_auto_rules)
        interaction = _non_negative_int(
            initial_last_interaction_ts_ms,
            field="last_interaction_ts_ms",
        )
        operation = _required_text(operation_id, field="operation_id")
        observed = _non_negative_int(observed_at_ms, field="observed_at_ms")
        created = int(self._clock())
        authority = self._authority(created_at_ms=created)
        self.assert_schema_ready()
        with self._write() as connection:
            snapshot, events = self._verified_state(connection)
            if snapshot is not None:
                return HostModePolicyCommitV1(
                    snapshot=snapshot,
                    event=events[-1],
                    exact_replay=True,
                )
            return self._insert(
                connection,
                revision=1,
                operation_id=operation,
                event_kind="INITIALIZED",
                input_payload={
                    "initial_auto_rules": rules,
                    "initial_last_interaction_ts_ms": interaction,
                },
                event_payload={"initialized": True},
                auto_rules=rules,
                last_interaction_ts_ms=interaction,
                previous_event_digest="",
                observed_at_ms=observed,
                insert_head=True,
                created_at_ms=created,
                authority=authority,
            )

    def append(
        self,
        *,
        expected_revision: int,
        operation_id: str,
        event_kind: str,
        input_payload: Mapping[str, Any],
        event_payload: Mapping[str, Any],
        auto_rules: Mapping[str, bool],
        last_interaction_ts_ms: int,
        observed_at_ms: int,
    ) -> HostModePolicyCommitV1:
        expected = _positive_int(expected_revision, field="expected_revision")
        operation = _required_text(operation_id, field="operation_id")
        kind = str(event_kind or "").strip().upper()
        if kind not in HOST_MODE_POLICY_EVENT_KINDS - {"INITIALIZED"}:
            raise HostModePolicyJournalError(
                "HOST_MODE_POLICY_EVENT_KIND_INVALID"
            )
        input_value = dict(input_payload or {})
        payload_value = dict(event_payload or {})
        next_rules = _rules(auto_rules)
        interaction = _non_negative_int(
            last_interaction_ts_ms,
            field="last_interaction_ts_ms",
        )
        observed = _non_negative_int(observed_at_ms, field="observed_at_ms")
        input_digest = _digest(input_value)
        created = int(self._clock())
        authority = self._authority(created_at_ms=created)
        self.assert_schema_ready()
        with self._write() as connection:
            snapshot, _ = self._verified_state(connection)
            existing_row = connection.execute(
                """
                SELECT * FROM host_mode_policy_event
                WHERE runtime_scope_id = ? AND operation_id = ?
                """,
                (self._runtime_scope_id, operation),
            ).fetchone()
            if existing_row is not None:
                existing = _event_from_row(existing_row)
                if (
                    existing.event_kind != kind
                    or existing.input_digest != input_digest
                    or dict(existing.event_payload) != payload_value
                    or dict(existing.auto_rules) != next_rules
                    or existing.last_interaction_ts_ms != interaction
                    or existing.observed_at_ms != observed
                ):
                    raise HostModePolicyJournalError(
                        "HOST_MODE_POLICY_IDEMPOTENCY_DRIFT"
                    )
                if snapshot is None or snapshot.revision < existing.revision:
                    raise HostModePolicyJournalError(
                        "HOST_MODE_POLICY_HEAD_DRIFT"
                    )
                return HostModePolicyCommitV1(
                    snapshot=snapshot,
                    event=existing,
                    exact_replay=True,
                )
            if snapshot is None:
                raise HostModePolicyJournalError(
                    "HOST_MODE_POLICY_NOT_INITIALIZED"
                )
            if snapshot.revision != expected:
                raise HostModePolicyJournalError(
                    "HOST_MODE_POLICY_EXPECTED_HEAD_CONFLICT"
                )
            return self._insert(
                connection,
                revision=expected + 1,
                operation_id=operation,
                event_kind=kind,
                input_payload=input_value,
                event_payload=payload_value,
                auto_rules=next_rules,
                last_interaction_ts_ms=interaction,
                previous_event_digest=snapshot.event_digest,
                observed_at_ms=observed,
                insert_head=False,
                created_at_ms=created,
                authority=authority,
            )

    def get_operation(
        self,
        operation_id: str,
    ) -> HostModePolicyEventV1 | None:
        operation = _required_text(operation_id, field="operation_id")
        self.assert_schema_ready()
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM host_mode_policy_event
                WHERE runtime_scope_id = ? AND operation_id = ?
                """,
                (self._runtime_scope_id, operation),
            ).fetchone()
        return _event_from_row(row) if row is not None else None

    def history(self, *, limit: int = 100) -> Sequence[HostModePolicyEventV1]:
        bounded = max(1, min(int(limit), 10_000))
        self.assert_schema_ready()
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM host_mode_policy_event
                WHERE runtime_scope_id = ?
                ORDER BY revision DESC
                LIMIT ?
                """,
                (self._runtime_scope_id, bounded),
            ).fetchall()
        return tuple(_event_from_row(row) for row in rows)

    def _insert(
        self,
        connection: sqlite3.Connection,
        *,
        revision: int,
        operation_id: str,
        event_kind: str,
        input_payload: Mapping[str, Any],
        event_payload: Mapping[str, Any],
        auto_rules: Mapping[str, bool],
        last_interaction_ts_ms: int,
        previous_event_digest: str,
        observed_at_ms: int,
        insert_head: bool,
        created_at_ms: int,
        authority: Mapping[str, Any],
    ) -> HostModePolicyCommitV1:
        created = _non_negative_int(created_at_ms, field="created_at_ms")
        if created < int(observed_at_ms):
            raise HostModePolicyJournalError(
                "HOST_MODE_POLICY_TIME_REGRESSION"
            )
        input_digest = _digest(dict(input_payload))
        digest_payload = _event_digest_payload(
            runtime_scope_id=self._runtime_scope_id,
            revision=revision,
            operation_id=operation_id,
            event_kind=event_kind,
            input_digest=input_digest,
            event_payload=event_payload,
            auto_rules=auto_rules,
            last_interaction_ts_ms=last_interaction_ts_ms,
            previous_event_digest=previous_event_digest,
            observed_at_ms=observed_at_ms,
            created_at_ms=created,
            **authority,
        )
        event_digest = _digest(digest_payload)
        policy_json = _canonical(_rules(auto_rules))
        event_payload_json = _canonical(dict(event_payload))
        connection.execute(
            """
            INSERT INTO host_mode_policy_event (
                runtime_scope_id, revision, operation_id, event_kind,
                input_digest, event_payload_json, policy_json,
                last_interaction_ts_ms, previous_event_digest, event_digest,
                runtime_owner_id, runtime_lease_id, runtime_fencing_token,
                runtime_issued_at_ms, runtime_expires_at_ms,
                observed_at_ms, created_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self._runtime_scope_id,
                revision,
                operation_id,
                event_kind,
                input_digest,
                event_payload_json,
                policy_json,
                last_interaction_ts_ms,
                previous_event_digest,
                event_digest,
                authority["runtime_owner_id"],
                authority["runtime_lease_id"],
                authority["runtime_fencing_token"],
                authority["runtime_issued_at_ms"],
                authority["runtime_expires_at_ms"],
                observed_at_ms,
                created,
            ),
        )
        if self._fault_injector is not None:
            self._fault_injector("after_event_insert")
        if insert_head:
            connection.execute(
                """
                INSERT INTO host_mode_policy_head (
                    runtime_scope_id, revision, operation_id, policy_json,
                    last_interaction_ts_ms, event_digest, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._runtime_scope_id,
                    revision,
                    operation_id,
                    policy_json,
                    last_interaction_ts_ms,
                    event_digest,
                    observed_at_ms,
                ),
            )
        else:
            cursor = connection.execute(
                """
                UPDATE host_mode_policy_head
                SET revision = ?, operation_id = ?, policy_json = ?,
                    last_interaction_ts_ms = ?, event_digest = ?,
                    updated_at_ms = ?
                WHERE runtime_scope_id = ? AND revision = ?
                  AND event_digest = ?
                """,
                (
                    revision,
                    operation_id,
                    policy_json,
                    last_interaction_ts_ms,
                    event_digest,
                    observed_at_ms,
                    self._runtime_scope_id,
                    revision - 1,
                    previous_event_digest,
                ),
            )
            if cursor.rowcount != 1:
                raise HostModePolicyJournalError(
                    "HOST_MODE_POLICY_HEAD_CAS_LOST"
                )
        if self._fault_injector is not None:
            self._fault_injector("after_head_write")
        row = connection.execute(
            """
            SELECT * FROM host_mode_policy_event
            WHERE runtime_scope_id = ? AND revision = ?
            """,
            (self._runtime_scope_id, revision),
        ).fetchone()
        head = connection.execute(
            """
            SELECT * FROM host_mode_policy_head
            WHERE runtime_scope_id = ?
            """,
            (self._runtime_scope_id,),
        ).fetchone()
        if row is None or head is None:
            raise HostModePolicyJournalError(
                "HOST_MODE_POLICY_COMMIT_LOST"
            )
        return HostModePolicyCommitV1(
            snapshot=_snapshot_from_row(head),
            event=_event_from_row(row),
            exact_replay=False,
        )

    def _verified_state(
        self,
        connection: sqlite3.Connection,
    ) -> tuple[
        HostModePolicySnapshotV1 | None,
        tuple[HostModePolicyEventV1, ...],
    ]:
        head_row = connection.execute(
            """
            SELECT * FROM host_mode_policy_head
            WHERE runtime_scope_id = ?
            """,
            (self._runtime_scope_id,),
        ).fetchone()
        rows: list[sqlite3.Row] = []
        cursor_revision = 0
        while True:
            page = connection.execute(
                """
                SELECT * FROM host_mode_policy_event
                WHERE runtime_scope_id = ? AND revision > ?
                ORDER BY revision ASC
                LIMIT ?
                """,
                (self._runtime_scope_id, cursor_revision, self._page_size),
            ).fetchall()
            if not page:
                break
            rows.extend(page)
            cursor_revision = int(page[-1]["revision"])
        if head_row is None:
            if rows:
                raise HostModePolicyJournalError(
                    "HOST_MODE_POLICY_HEAD_MISSING"
                )
            return None, ()
        events = tuple(_event_from_row(row) for row in rows)
        if not events:
            raise HostModePolicyJournalError(
                "HOST_MODE_POLICY_HEAD_ORPHANED"
            )
        previous_digest = ""
        for expected_revision, event in enumerate(events, start=1):
            payload = _event_digest_payload(
                runtime_scope_id=event.runtime_scope_id,
                revision=event.revision,
                operation_id=event.operation_id,
                event_kind=event.event_kind,
                input_digest=event.input_digest,
                event_payload=event.event_payload,
                auto_rules=event.auto_rules,
                last_interaction_ts_ms=event.last_interaction_ts_ms,
                previous_event_digest=event.previous_event_digest,
                runtime_owner_id=event.runtime_owner_id,
                runtime_lease_id=event.runtime_lease_id,
                runtime_fencing_token=event.runtime_fencing_token,
                runtime_issued_at_ms=event.runtime_issued_at_ms,
                runtime_expires_at_ms=event.runtime_expires_at_ms,
                observed_at_ms=event.observed_at_ms,
                created_at_ms=event.created_at_ms,
            )
            if (
                event.revision != expected_revision
                or event.previous_event_digest != previous_digest
                or event.event_kind not in HOST_MODE_POLICY_EVENT_KINDS
                or event.event_digest != _digest(payload)
            ):
                raise HostModePolicyJournalError(
                    "HOST_MODE_POLICY_DIGEST_CHAIN_INVALID"
                )
            previous_digest = event.event_digest
        snapshot = _snapshot_from_row(head_row)
        terminal = events[-1]
        if (
            snapshot.revision != terminal.revision
            or snapshot.operation_id != terminal.operation_id
            or dict(snapshot.auto_rules) != dict(terminal.auto_rules)
            or snapshot.last_interaction_ts_ms
            != terminal.last_interaction_ts_ms
            or snapshot.event_digest != terminal.event_digest
            or snapshot.updated_at_ms != terminal.observed_at_ms
        ):
            raise HostModePolicyJournalError(
                "HOST_MODE_POLICY_HEAD_DRIFT"
            )
        return snapshot, events


__all__ = [
    "HOST_MODE_POLICY_MIGRATION_ASSET_KEY",
    "HostModePolicyJournalError",
    "SQLiteHostModePolicyJournal",
]
