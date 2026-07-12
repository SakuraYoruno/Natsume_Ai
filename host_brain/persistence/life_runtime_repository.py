"""Operation-scoped persistence owner for Life continuity and organ runtime.

All production SQL used by ``runtime.life_continuity`` and
``runtime.life_organs`` lives here. Schema is migration-only (0049/0057/0071),
and every mutation records an immutable ``life_runtime_uow`` provenance row in
the same transaction.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any, Iterator
import uuid


LIFE_RUNTIME_SCHEMA_NOT_READY = "LIFE_RUNTIME_SCHEMA_NOT_READY"
LIFE_RUNTIME_INVALID = "LIFE_RUNTIME_INVALID"
LIFE_RUNTIME_CONFLICT = "LIFE_RUNTIME_CONFLICT"
LIFE_RUNTIME_DURABLE_DATA_INVALID = "LIFE_RUNTIME_DURABLE_DATA_INVALID"

LIFE_RUNTIME_MIGRATION_ASSET_KEY = "0071#life_runtime_uow"
LIFE_RUNTIME_MIGRATION_SHA256 = "55abc3eb3372ca973f3fef2cec41b1660102c0f4da44c68eba7db54c15dc8b16"

_ALLOWED_DISPOSITIONS = frozenset({"APPLIED", "APPLIED_WITH_COMPAT", "LEGACY_BACKFILL"})
_REQUIRED_TABLES = frozenset(
    {
        "digital_life_state",
        "digital_life_state_event",
        "digital_life_state_occ_event",
        "dialogue_life_influence_log",
        "embodiment_dispatch_log",
        "work_environment_event",
        "proactive_life_outbox",
        "life_experience_event",
        "organ_runtime_state",
        "organ_runtime_event",
        "organ_coupling_event",
        "life_state_synthesis_log",
        "proactive_interaction_episode",
        "life_runtime_uow",
    }
)
_REQUIRED_INDEXES = frozenset(
    {
        "idx_life_runtime_uow_aggregate",
        "idx_life_runtime_uow_operation",
        "idx_digital_life_state_person_version",
        "idx_life_experience_unconsumed",
        "idx_proactive_episode_pending",
    }
)
_REQUIRED_TRIGGERS = frozenset(
    {"life_runtime_uow_no_update", "life_runtime_uow_no_delete"}
)


class LifeRuntimeRepositoryError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or LIFE_RUNTIME_INVALID)
        self.detail = str(detail or "Life runtime persistence failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


class LifeRuntimeRepository:
    """The sole SQL writer for the current production Life runtime slice."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        timeout_ms: int = 5_000,
        verify_schema: bool = True,
        _borrowed_connection: sqlite3.Connection | None = None,
        _borrowed_lock: Any | None = None,
    ) -> None:
        raw = str(db_path or "").strip()
        if not raw or raw.lower().startswith("file:"):
            raise LifeRuntimeRepositoryError(
                LIFE_RUNTIME_INVALID,
                "LifeRuntimeRepository requires a durable SQLite file path",
            )
        if raw == ":memory:" and _borrowed_connection is None:
            raise LifeRuntimeRepositoryError(
                LIFE_RUNTIME_INVALID,
                "in-memory Life runtime requires a borrowed ledger connection",
            )
        self.db_path = raw if raw == ":memory:" else Path(raw).expanduser().resolve()
        self._timeout_ms = max(1, int(timeout_ms))
        self._borrowed_connection = _borrowed_connection
        self._borrowed_lock = _borrowed_lock
        if self._borrowed_connection is not None:
            self._borrowed_connection.row_factory = sqlite3.Row
            self._install_borrowed_test_schema()
        if verify_schema:
            self.assert_schema_ready()

    @classmethod
    def from_ledger(cls, ledger: Any) -> "LifeRuntimeRepository":
        db_path = str(getattr(ledger, "canonical_db_path", "") or "").strip()
        if not db_path:
            db_path = str(getattr(ledger, "db_path", "") or "").strip()
        connection = getattr(ledger, "_conn", None)
        if not db_path and connection is not None:
            database_row = connection.execute("PRAGMA database_list").fetchone()
            database_file = str(database_row[2] or "") if database_row is not None else ""
            db_path = database_file or ":memory:"
        if db_path == ":memory:":
            lock = getattr(ledger, "_write_lock", None)
            if connection is None:
                raise LifeRuntimeRepositoryError(
                    LIFE_RUNTIME_INVALID,
                    "in-memory ledger does not expose its compatibility connection",
                )
            if lock is None:
                lock = getattr(ledger, "_life_runtime_repository_lock", None)
                if lock is None:
                    lock = threading.RLock()
                    setattr(ledger, "_life_runtime_repository_lock", lock)
            return cls(
                ":memory:",
                _borrowed_connection=connection,
                _borrowed_lock=lock,
            )
        if not db_path:
            raise LifeRuntimeRepositoryError(
                LIFE_RUNTIME_INVALID,
                "ledger does not expose canonical_db_path",
            )
        return cls(db_path)

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
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """Borrow only for legacy in-memory tests; durable calls open/close per operation."""

        if self._borrowed_connection is not None:
            with self._borrowed_lock:
                try:
                    yield self._borrowed_connection
                except BaseException:
                    if self._borrowed_connection.in_transaction:
                        self._borrowed_connection.rollback()
                    raise
                if self._borrowed_connection.in_transaction:
                    self._borrowed_connection.rollback()
                    raise LifeRuntimeRepositoryError(
                        LIFE_RUNTIME_DURABLE_DATA_INVALID,
                        "borrowed Life runtime transaction was not finalized",
                    )
            return
        conn = self._connect()
        try:
            try:
                yield conn
            except BaseException:
                if conn.in_transaction:
                    conn.rollback()
                raise
            if conn.in_transaction:
                conn.rollback()
                raise LifeRuntimeRepositoryError(
                    LIFE_RUNTIME_DURABLE_DATA_INVALID,
                    "Life runtime transaction was not finalized",
                )
        finally:
            conn.close()

    def _install_borrowed_test_schema(self) -> None:
        """Apply catalog SQL only to a borrowed ephemeral test database.

        This is intentionally impossible for durable production paths. It keeps
        legacy ``SQLiteLedger(':memory:')`` tests functional without restoring
        DDL strings or private connections to either runtime module.
        """

        assert self._borrowed_connection is not None
        migration_dir = Path(__file__).resolve().parents[1] / "audit" / "migrations"
        assets = (
            migration_dir / "0049_digital_life_causality.sql",
            migration_dir / "0057_life_occ.sql",
            migration_dir / "0071_life_runtime_uow.sql",
        )
        with self._borrowed_lock:
            existing = self._borrowed_connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='life_runtime_uow'"
            ).fetchone()
            if existing is None:
                for asset in assets:
                    self._borrowed_connection.executescript(asset.read_text(encoding="utf-8-sig"))

    def assert_schema_ready(self) -> None:
        require_catalog = self._borrowed_connection is None
        if require_catalog and not Path(self.db_path).is_file():
            raise LifeRuntimeRepositoryError(
                LIFE_RUNTIME_SCHEMA_NOT_READY,
                f"Life runtime database does not exist: {self.db_path}",
            )
        try:
            with self._connection() as conn:
                asset = None
                if require_catalog:
                    asset = conn.execute(
                        """
                        SELECT checksum, disposition FROM schema_migration_assets
                        WHERE asset_key = ?
                        """,
                        (LIFE_RUNTIME_MIGRATION_ASSET_KEY,),
                    ).fetchone()
                if require_catalog and (
                    asset is None
                    or str(asset["checksum"]) != LIFE_RUNTIME_MIGRATION_SHA256
                    or str(asset["disposition"]) not in _ALLOWED_DISPOSITIONS
                ):
                    raise LifeRuntimeRepositoryError(
                        LIFE_RUNTIME_SCHEMA_NOT_READY,
                        "0071 Life runtime migration is absent or checksum drifted",
                    )
                tables = {
                    str(row["name"])
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                indexes = {
                    str(row["name"])
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='index'"
                    ).fetchall()
                }
                triggers = {
                    str(row["name"])
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='trigger'"
                    ).fetchall()
                }
                if not _REQUIRED_TABLES.issubset(tables):
                    raise LifeRuntimeRepositoryError(
                        LIFE_RUNTIME_SCHEMA_NOT_READY, "Life runtime tables are incomplete"
                    )
                if not _REQUIRED_INDEXES.issubset(indexes):
                    raise LifeRuntimeRepositoryError(
                        LIFE_RUNTIME_SCHEMA_NOT_READY, "Life runtime indexes are incomplete"
                    )
                if not _REQUIRED_TRIGGERS.issubset(triggers):
                    raise LifeRuntimeRepositoryError(
                        LIFE_RUNTIME_SCHEMA_NOT_READY, "Life UoW immutability is incomplete"
                    )
        except LifeRuntimeRepositoryError:
            raise
        except sqlite3.Error as exc:
            raise LifeRuntimeRepositoryError(LIFE_RUNTIME_SCHEMA_NOT_READY, str(exc)) from exc

    # -- Life state -----------------------------------------------------

    def get_state(self, person_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT version, state_json, updated_at_ms FROM digital_life_state WHERE person_id=?",
                (str(person_id),),
            ).fetchone()
        if row is None:
            return None
        state = _decode_object(row["state_json"])
        if int(state.get("version") or 0) != int(row["version"]):
            raise LifeRuntimeRepositoryError(
                LIFE_RUNTIME_DURABLE_DATA_INVALID, "state JSON/version drift"
            )
        return state

    def get_state_version(self, person_id: str) -> int | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT version FROM digital_life_state WHERE person_id=?", (str(person_id),)
            ).fetchone()
        return int(row["version"]) if row is not None else None

    def save_state(
        self,
        state: Mapping[str, Any],
        *,
        expected_version: int | None,
        command_ref: str,
        source_kind: str,
        allow_single_writer_fallback: bool,
        now_ms: int,
        conflict_reason: str,
        fallback_reason: str,
    ) -> dict[str, Any]:
        payload = dict(state or {})
        person_id = str(payload.get("person_id") or "default")
        attempted = int(payload.get("version") or 1)
        payload.update({"person_id": person_id, "version": attempted})
        encoded = _canonical_json(payload)
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT version FROM digital_life_state WHERE person_id=?", (person_id,)
                ).fetchone()
                actual = int(row["version"]) if row is not None else None
                if expected_version is None:
                    if not allow_single_writer_fallback:
                        raise ValueError(
                            "expected_version is required when single-writer fallback is disabled"
                        )
                    conn.execute(
                        """
                        INSERT INTO digital_life_state(person_id, version, state_json, updated_at_ms)
                        VALUES(?, ?, ?, ?)
                        ON CONFLICT(person_id) DO UPDATE SET
                            version=excluded.version, state_json=excluded.state_json,
                            updated_at_ms=excluded.updated_at_ms
                        """,
                        (person_id, attempted, encoded, now_ms),
                    )
                    result = self._append_occ(
                        conn,
                        person_id=person_id,
                        expected=None,
                        actual=actual,
                        attempted=attempted,
                        status="FALLBACK_APPLIED",
                        reason_code=fallback_reason,
                        command_ref=command_ref,
                        source_kind=source_kind,
                        now_ms=now_ms,
                    )
                    self._record_uow(
                        conn,
                        operation="STATE_WRITE",
                        aggregate_id=person_id,
                        command_ref=command_ref,
                        payload=payload,
                        expected_version=None,
                        resulting_version=attempted,
                        status="APPLIED",
                        created_at_ms=now_ms,
                        identity=result["occ_event_id"],
                    )
                    conn.execute("COMMIT")
                    return {**result, "state_version_after": attempted}

                expected = int(expected_version)
                applied = False
                if actual is None and expected == 0:
                    try:
                        conn.execute(
                            """
                            INSERT INTO digital_life_state(person_id, version, state_json, updated_at_ms)
                            VALUES(?, ?, ?, ?)
                            """,
                            (person_id, attempted, encoded, now_ms),
                        )
                        applied = True
                    except sqlite3.IntegrityError:
                        actual_row = conn.execute(
                            "SELECT version FROM digital_life_state WHERE person_id=?",
                            (person_id,),
                        ).fetchone()
                        actual = int(actual_row["version"]) if actual_row else None
                elif actual is not None:
                    cursor = conn.execute(
                        """
                        UPDATE digital_life_state
                        SET version=?, state_json=?, updated_at_ms=?
                        WHERE person_id=? AND version=?
                        """,
                        (attempted, encoded, now_ms, person_id, expected),
                    )
                    applied = cursor.rowcount == 1
                    if not applied:
                        actual_row = conn.execute(
                            "SELECT version FROM digital_life_state WHERE person_id=?",
                            (person_id,),
                        ).fetchone()
                        actual = int(actual_row["version"]) if actual_row else None
                if not applied:
                    result = self._append_occ(
                        conn,
                        person_id=person_id,
                        expected=expected,
                        actual=actual,
                        attempted=attempted,
                        status="CONFLICT",
                        reason_code=conflict_reason,
                        command_ref=command_ref,
                        source_kind=source_kind,
                        now_ms=now_ms,
                    )
                    self._record_uow(
                        conn,
                        operation="STATE_CONFLICT",
                        aggregate_id=person_id,
                        command_ref=command_ref,
                        payload={"actual": actual, "attempted": attempted},
                        expected_version=expected,
                        resulting_version=actual,
                        status="CONFLICT",
                        created_at_ms=now_ms,
                        identity=result["occ_event_id"],
                    )
                    conn.execute("COMMIT")
                    return result
                result = self._append_occ(
                    conn,
                    person_id=person_id,
                    expected=expected,
                    actual=actual,
                    attempted=attempted,
                    status="APPLIED",
                    reason_code="",
                    command_ref=command_ref,
                    source_kind=source_kind,
                    now_ms=now_ms,
                )
                self._record_uow(
                    conn,
                    operation="STATE_WRITE",
                    aggregate_id=person_id,
                    command_ref=command_ref,
                    payload=payload,
                    expected_version=expected,
                    resulting_version=attempted,
                    status="APPLIED",
                    created_at_ms=now_ms,
                    identity=result["occ_event_id"],
                )
                conn.execute("COMMIT")
                return {**result, "state_version_after": attempted}
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    def record_state_conflict(
        self,
        *,
        person_id: str,
        expected_version: int,
        actual_version: int | None,
        attempted_version: int,
        command_ref: str,
        source_kind: str,
        reason_code: str,
        now_ms: int,
    ) -> dict[str, Any]:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            result = self._append_occ(
                conn,
                person_id=person_id,
                expected=expected_version,
                actual=actual_version,
                attempted=attempted_version,
                status="CONFLICT",
                reason_code=reason_code,
                command_ref=command_ref,
                source_kind=source_kind,
                now_ms=now_ms,
            )
            self._record_uow(
                conn,
                operation="STATE_CONFLICT",
                aggregate_id=person_id,
                command_ref=command_ref,
                payload=result,
                expected_version=expected_version,
                resulting_version=actual_version,
                status="CONFLICT",
                created_at_ms=now_ms,
                identity=result["occ_event_id"],
            )
            conn.execute("COMMIT")
        return result

    def append_state_event(
        self,
        *,
        event_id: str,
        person_id: str,
        source_kind: str,
        event_type: str,
        delta: Mapping[str, Any],
        state_before: Mapping[str, Any],
        state_after: Mapping[str, Any],
        visible_channels: Sequence[str],
        created_at_ms: int,
    ) -> None:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO digital_life_state_event(
                    event_id, person_id, source_kind, event_type, delta_json,
                    state_before_json, state_after_json, visible_channels_json, created_at_ms
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    person_id,
                    source_kind,
                    event_type,
                    _canonical_json(delta),
                    _canonical_json(state_before),
                    _canonical_json(state_after),
                    _canonical_json(list(visible_channels)),
                    created_at_ms,
                ),
            )
            self._record_uow(
                conn,
                operation="STATE_EVENT_APPEND",
                aggregate_id=person_id,
                command_ref=event_id,
                payload=delta,
                expected_version=int(state_before.get("version") or 0),
                resulting_version=int(state_after.get("version") or 0),
                status="APPLIED",
                created_at_ms=created_at_ms,
                identity=event_id,
            )
            conn.execute("COMMIT")

    # -- Experience and organs ----------------------------------------

    def append_experience(self, event: Mapping[str, Any]) -> None:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO life_experience_event(
                    event_id, person_id, session_id, source_kind, event_kind,
                    content_summary, evidence_json, salience, valence, uncertainty,
                    created_at_ms, consumed_by_organs
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    event["event_id"],
                    event["person_id"],
                    event.get("session_id"),
                    event["source_kind"],
                    event["event_kind"],
                    event.get("content_summary", ""),
                    _canonical_json(event.get("evidence", {})),
                    float(event.get("salience", 0.0)),
                    float(event.get("valence", 0.0)),
                    float(event.get("uncertainty", 0.0)),
                    int(event["created_at_ms"]),
                ),
            )
            self._record_uow(
                conn,
                operation="EXPERIENCE_APPEND",
                aggregate_id=str(event["person_id"]),
                command_ref=str(event["event_id"]),
                payload=event,
                expected_version=None,
                resulting_version=None,
                status="APPLIED",
                created_at_ms=int(event["created_at_ms"]),
                identity=str(event["event_id"]),
            )
            conn.execute("COMMIT")

    def get_experience(self, event_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM life_experience_event WHERE event_id=?", (str(event_id),)
            ).fetchone()
        if row is None:
            return None
        return {
            "event_id": str(row["event_id"]),
            "person_id": str(row["person_id"]),
            "session_id": str(row["session_id"] or ""),
            "source_kind": str(row["source_kind"]),
            "event_kind": str(row["event_kind"]),
            "content_summary": str(row["content_summary"]),
            "evidence": _decode_object(row["evidence_json"]),
            "salience": float(row["salience"] or 0.0),
            "valence": float(row["valence"] or 0.0),
            "uncertainty": float(row["uncertainty"] or 0.0),
            "created_at_ms": int(row["created_at_ms"]),
            "consumed_by_organs": bool(row["consumed_by_organs"]),
        }

    def get_organ_states(self, person_id: str) -> dict[str, dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT organ_id, state_json FROM organ_runtime_state WHERE person_id=?",
                (str(person_id),),
            ).fetchall()
        return {str(row["organ_id"]): _decode_object(row["state_json"]) for row in rows}

    def commit_organ_processing(
        self,
        *,
        experience_event_id: str,
        person_id: str,
        organ_events: Sequence[Mapping[str, Any]],
        coupling_events: Sequence[Mapping[str, Any]],
        organ_states: Mapping[str, Mapping[str, Any]],
        created_at_ms: int,
    ) -> bool:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                UPDATE life_experience_event SET consumed_by_organs=1
                WHERE event_id=? AND consumed_by_organs=0
                """,
                (experience_event_id,),
            )
            if cursor.rowcount != 1:
                conn.execute("ROLLBACK")
                return False
            try:
                for event in organ_events:
                    conn.execute(
                        """
                        INSERT INTO organ_runtime_event(
                            event_id, experience_event_id, person_id, organ_id,
                            delta_json, state_before_json, state_after_json, created_at_ms
                        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            event["event_id"],
                            experience_event_id,
                            person_id,
                            event["organ_id"],
                            _canonical_json(event["delta"]),
                            _canonical_json(event["state_before"]),
                            _canonical_json(event["state_after"]),
                            created_at_ms,
                        ),
                    )
                for event in coupling_events:
                    conn.execute(
                        """
                        INSERT INTO organ_coupling_event(
                            coupling_id, source_experience_event_id, person_id,
                            source_organs_json, target_organ_id, effect_json, created_at_ms
                        ) VALUES(?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            event["coupling_id"],
                            experience_event_id,
                            person_id,
                            _canonical_json(event["source_organs"]),
                            event["target_organ_id"],
                            _canonical_json(event["effect"]),
                            created_at_ms,
                        ),
                    )
                for organ_id, state in organ_states.items():
                    payload = dict(state)
                    payload["updated_at_ms"] = created_at_ms
                    conn.execute(
                        """
                        INSERT INTO organ_runtime_state(
                            person_id, organ_id, state_json, updated_at_ms
                        ) VALUES(?, ?, ?, ?)
                        ON CONFLICT(person_id, organ_id) DO UPDATE SET
                            state_json=excluded.state_json,
                            updated_at_ms=excluded.updated_at_ms
                        """,
                        (person_id, organ_id, _canonical_json(payload), created_at_ms),
                    )
                self._record_uow(
                    conn,
                    operation="ORGAN_PROCESS",
                    aggregate_id=person_id,
                    command_ref=experience_event_id,
                    payload={
                        "experience_event_id": experience_event_id,
                        "organ_event_count": len(organ_events),
                        "coupling_event_count": len(coupling_events),
                        "organ_state_count": len(organ_states),
                    },
                    expected_version=None,
                    resulting_version=None,
                    status="APPLIED",
                    created_at_ms=created_at_ms,
                    identity=experience_event_id,
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return True

    def update_organ_states(
        self,
        *,
        person_id: str,
        states: Mapping[str, Mapping[str, Any]],
        created_at_ms: int,
        command_ref: str,
    ) -> None:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for organ_id, state in states.items():
                conn.execute(
                    """
                    UPDATE organ_runtime_state SET state_json=?, updated_at_ms=?
                    WHERE person_id=? AND organ_id=?
                    """,
                    (_canonical_json(state), created_at_ms, person_id, organ_id),
                )
            self._record_uow(
                conn,
                operation="ORGAN_RECALIBRATE",
                aggregate_id=person_id,
                command_ref=command_ref,
                payload={"organ_ids": sorted(states)},
                expected_version=None,
                resulting_version=None,
                status="APPLIED",
                created_at_ms=created_at_ms,
                identity=command_ref,
            )
            conn.execute("COMMIT")

    def append_synthesis(
        self,
        *,
        synthesis_id: str,
        person_id: str,
        source_experience_event_id: str,
        organ_summary: Mapping[str, Any],
        state_patch: Mapping[str, Any],
        created_at_ms: int,
    ) -> None:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO life_state_synthesis_log(
                    synthesis_id, person_id, source_experience_event_id,
                    organ_summary_json, state_patch_json, created_at_ms
                ) VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    synthesis_id,
                    person_id,
                    source_experience_event_id,
                    _canonical_json(organ_summary),
                    _canonical_json(state_patch),
                    created_at_ms,
                ),
            )
            self._record_uow(
                conn,
                operation="SYNTHESIS_APPEND",
                aggregate_id=person_id,
                command_ref=source_experience_event_id,
                payload=state_patch,
                expected_version=None,
                resulting_version=None,
                status="APPLIED",
                created_at_ms=created_at_ms,
                identity=synthesis_id,
            )
            conn.execute("COMMIT")

    # -- Proactive episodes -------------------------------------------

    def open_proactive_episode(self, episode: Mapping[str, Any]) -> None:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO proactive_interaction_episode(
                    episode_id, person_id, source_action_id, source_outbox_id,
                    motive, organ_sources_json, message_text, expected_response_kind,
                    sent_at_ms, outcome, metadata_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    episode["episode_id"],
                    episode["person_id"],
                    episode.get("source_action_id"),
                    episode.get("source_outbox_id"),
                    episode.get("motive", ""),
                    _canonical_json(episode.get("organ_sources", [])),
                    episode.get("message_text", ""),
                    episode.get("expected_response_kind", ""),
                    episode["sent_at_ms"],
                    episode.get("outcome", "pending"),
                    _canonical_json(episode.get("metadata", {})),
                ),
            )
            self._record_uow(
                conn,
                operation="PROACTIVE_EPISODE_OPEN",
                aggregate_id=str(episode["person_id"]),
                command_ref=str(episode["episode_id"]),
                payload=episode,
                expected_version=None,
                resulting_version=None,
                status="APPLIED",
                created_at_ms=int(episode["sent_at_ms"]),
                identity=str(episode["episode_id"]),
            )
            conn.execute("COMMIT")

    def latest_pending_episode(self, person_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT episode_id, motive, message_text, sent_at_ms
                FROM proactive_interaction_episode
                WHERE person_id=? AND outcome='pending'
                ORDER BY sent_at_ms DESC LIMIT 1
                """,
                (str(person_id),),
            ).fetchone()
        return dict(row) if row is not None else None

    def close_proactive_episode(
        self,
        *,
        episode_id: str,
        person_id: str,
        user_response_turn_id: str,
        outcome: str,
        organ_delta_after: Mapping[str, Any],
        closed_at_ms: int,
    ) -> bool:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                UPDATE proactive_interaction_episode
                SET user_response_turn_id=?, outcome=?, organ_delta_after_json=?, closed_at_ms=?
                WHERE episode_id=? AND outcome='pending'
                """,
                (
                    user_response_turn_id,
                    outcome,
                    _canonical_json(organ_delta_after),
                    closed_at_ms,
                    episode_id,
                ),
            )
            if cursor.rowcount == 1:
                self._record_uow(
                    conn,
                    operation="PROACTIVE_EPISODE_CLOSE",
                    aggregate_id=person_id,
                    command_ref=episode_id,
                    payload={"outcome": outcome, "organ_delta_after": organ_delta_after},
                    expected_version=None,
                    resulting_version=None,
                    status="APPLIED",
                    created_at_ms=closed_at_ms,
                    identity=episode_id,
                )
            conn.execute("COMMIT")
        return cursor.rowcount == 1

    # -- Compatibility log projections -------------------------------

    def append_proactive_outbox(self, row: Mapping[str, Any]) -> None:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO proactive_life_outbox(
                    outbox_id, person_id, source_kind, motive, pressure, target_json,
                    message_json, gate_json, status, reason_code, result_json, created_at_ms
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["outbox_id"], row["person_id"], row["source_kind"], row.get("motive", ""),
                    float(row.get("pressure", 0.0)), _canonical_json(row.get("target", {})),
                    _canonical_json(row.get("message", {})), _canonical_json(row.get("gate", {})),
                    row.get("status", "UNKNOWN"), row.get("reason_code", ""),
                    _canonical_json(row.get("result", {})), row["created_at_ms"],
                ),
            )
            self._record_uow(
                conn, operation="PROACTIVE_OUTBOX_APPEND", aggregate_id=str(row["person_id"]),
                command_ref=str(row["outbox_id"]), payload=row, expected_version=None,
                resulting_version=None, status="APPLIED", created_at_ms=int(row["created_at_ms"]),
                identity=str(row["outbox_id"]),
            )
            conn.execute("COMMIT")

    def append_work_event(self, row: Mapping[str, Any]) -> None:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO work_environment_event(
                    event_id, person_id, session_id, request_id, event_type, work_id,
                    summary, urgency, emotional_effect_json, created_at_ms, active
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    row["event_id"], row["person_id"], row.get("session_id"), row.get("request_id"),
                    row["event_type"], row.get("work_id"), row.get("summary", ""),
                    row.get("urgency", "medium"), _canonical_json(row.get("emotional_effect", {})),
                    row["created_at_ms"],
                ),
            )
            self._record_uow(
                conn, operation="WORK_EVENT_APPEND", aggregate_id=str(row["person_id"]),
                command_ref=str(row["event_id"]), payload=row, expected_version=None,
                resulting_version=None, status="APPLIED", created_at_ms=int(row["created_at_ms"]),
                identity=str(row["event_id"]),
            )
            conn.execute("COMMIT")

    def append_dialogue_event(self, row: Mapping[str, Any]) -> None:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO dialogue_life_influence_log(
                    event_id, request_id, person_id, session_id, user_message,
                    reply_text, life_director_json, life_causality_json, created_at_ms
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["event_id"], row.get("request_id"), row["person_id"], row.get("session_id"),
                    row.get("user_message", ""), row.get("reply_text", ""),
                    _canonical_json(row.get("life_director", {})),
                    _canonical_json(row.get("life_causality", {})), row["created_at_ms"],
                ),
            )
            self._record_uow(
                conn, operation="DIALOGUE_EVENT_APPEND", aggregate_id=str(row["person_id"]),
                command_ref=str(row["event_id"]), payload=row, expected_version=None,
                resulting_version=None, status="APPLIED", created_at_ms=int(row["created_at_ms"]),
                identity=str(row["event_id"]),
            )
            conn.execute("COMMIT")

    def append_embodiment_dispatch(self, row: Mapping[str, Any]) -> None:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO embodiment_dispatch_log(
                    dispatch_id, person_id, source_kind, target_json, status,
                    reason_code, result_json, created_at_ms
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["dispatch_id"], row["person_id"], row["source_kind"],
                    _canonical_json(row.get("target", {})), row.get("status", "UNKNOWN"),
                    row.get("reason_code"), _canonical_json(row.get("result", {})),
                    row["created_at_ms"],
                ),
            )
            self._record_uow(
                conn, operation="EMBODIMENT_EVENT_APPEND", aggregate_id=str(row["person_id"]),
                command_ref=str(row["dispatch_id"]), payload=row, expected_version=None,
                resulting_version=None, status="APPLIED", created_at_ms=int(row["created_at_ms"]),
                identity=str(row["dispatch_id"]),
            )
            conn.execute("COMMIT")

    def latest_embodiment_dispatch(self, person_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT target_json, status, reason_code, result_json, created_at_ms
                FROM embodiment_dispatch_log WHERE person_id=?
                ORDER BY created_at_ms DESC LIMIT 1
                """,
                (str(person_id),),
            ).fetchone()
        if row is None:
            return None
        return {
            "target": _decode_object(row["target_json"]),
            "status": row["status"],
            "reason_code": row["reason_code"],
            "result": _decode_object(row["result_json"]),
            "created_at_ms": int(row["created_at_ms"]),
        }

    # -- Read models ---------------------------------------------------

    def list_experiences(self, person_id: str, *, limit: int) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM life_experience_event WHERE person_id=?
                ORDER BY created_at_ms DESC LIMIT ?
                """,
                (str(person_id), int(limit)),
            ).fetchall()
        return [
            {
                "event_id": str(row["event_id"]),
                "session_id": str(row["session_id"] or ""),
                "source_kind": str(row["source_kind"]),
                "event_kind": str(row["event_kind"]),
                "content_summary": str(row["content_summary"]),
                "evidence": _decode_object(row["evidence_json"]),
                "salience": float(row["salience"] or 0.0),
                "valence": float(row["valence"] or 0.0),
                "uncertainty": float(row["uncertainty"] or 0.0),
                "consumed_by_organs": bool(row["consumed_by_organs"]),
                "created_at_ms": int(row["created_at_ms"]),
            }
            for row in rows
        ]

    def list_inner_experiences(self, person_id: str, *, limit: int) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT event_kind, content_summary, evidence_json, created_at_ms
                FROM life_experience_event
                WHERE person_id=? AND source_kind='inner_world'
                ORDER BY created_at_ms DESC LIMIT ?
                """,
                (str(person_id), int(limit)),
            ).fetchall()
        return [
            {
                "event_kind": str(row["event_kind"]),
                "content_summary": str(row["content_summary"]),
                "evidence": _decode_object(row["evidence_json"]),
                "created_at_ms": int(row["created_at_ms"]),
            }
            for row in rows
        ]

    def list_proactive_episodes(self, person_id: str, *, limit: int) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM proactive_interaction_episode WHERE person_id=?
                ORDER BY sent_at_ms DESC LIMIT ?
                """,
                (str(person_id), int(limit)),
            ).fetchall()
        return [
            {
                "episode_id": row["episode_id"],
                "source_action_id": row["source_action_id"],
                "source_outbox_id": row["source_outbox_id"],
                "motive": row["motive"],
                "organ_sources": _decode_list(row["organ_sources_json"]),
                "message_text": row["message_text"],
                "expected_response_kind": row["expected_response_kind"],
                "sent_at_ms": int(row["sent_at_ms"]),
                "user_response_turn_id": row["user_response_turn_id"],
                "outcome": row["outcome"],
                "organ_delta_after": _decode_object(row["organ_delta_after_json"]),
                "closed_at_ms": int(row["closed_at_ms"]),
                "metadata": _decode_object(row["metadata_json"]),
            }
            for row in rows
        ]

    def list_proactive_outbox(self, person_id: str, *, limit: int) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM proactive_life_outbox WHERE person_id=?
                ORDER BY created_at_ms DESC LIMIT ?
                """,
                (str(person_id), int(limit)),
            ).fetchall()
        return [
            {
                "outbox_id": row["outbox_id"], "source_kind": row["source_kind"],
                "motive": row["motive"], "pressure": float(row["pressure"] or 0.0),
                "target": _decode_object(row["target_json"]),
                "message": _decode_object(row["message_json"]),
                "gate": _decode_object(row["gate_json"]), "status": row["status"],
                "reason_code": row["reason_code"], "result": _decode_object(row["result_json"]),
                "created_at_ms": int(row["created_at_ms"]),
            }
            for row in rows
        ]

    def list_causality(self, person_id: str, *, limit: int) -> dict[str, list[dict[str, Any]]]:
        with self._connection() as conn:
            state_rows = conn.execute(
                """SELECT event_id, source_kind, event_type, delta_json,
                          visible_channels_json, created_at_ms
                   FROM digital_life_state_event WHERE person_id=?
                   ORDER BY created_at_ms DESC LIMIT ?""",
                (person_id, limit),
            ).fetchall()
            dialogue_rows = conn.execute(
                """SELECT event_id, request_id, life_director_json,
                          life_causality_json, created_at_ms
                   FROM dialogue_life_influence_log WHERE person_id=?
                   ORDER BY created_at_ms DESC LIMIT ?""",
                (person_id, limit),
            ).fetchall()
            dispatch_rows = conn.execute(
                """SELECT dispatch_id, source_kind, target_json, status,
                          reason_code, created_at_ms
                   FROM embodiment_dispatch_log WHERE person_id=?
                   ORDER BY created_at_ms DESC LIMIT ?""",
                (person_id, limit),
            ).fetchall()
            work_rows = conn.execute(
                """SELECT event_id, event_type, work_id, summary, urgency,
                          emotional_effect_json, created_at_ms
                   FROM work_environment_event WHERE person_id=?
                   ORDER BY created_at_ms DESC LIMIT ?""",
                (person_id, limit),
            ).fetchall()
        return {
            "state_events": [
                {"event_id": r[0], "source_kind": r[1], "event_type": r[2],
                 "delta": _decode_object(r[3]), "visible_channels": _decode_list(r[4]),
                 "created_at_ms": r[5]} for r in state_rows
            ],
            "dialogue_influence": [
                {"event_id": r[0], "request_id": r[1], "life_director": _decode_object(r[2]),
                 "life_causality": _decode_object(r[3]), "created_at_ms": r[4]}
                for r in dialogue_rows
            ],
            "embodiment_dispatches": [
                {"dispatch_id": r[0], "source_kind": r[1], "target": _decode_object(r[2]),
                 "status": r[3], "reason_code": r[4], "created_at_ms": r[5]}
                for r in dispatch_rows
            ],
            "work_events": [
                {"event_id": r[0], "event_type": r[1], "work_id": r[2], "summary": r[3],
                 "urgency": r[4], "emotional_effect": _decode_object(r[5]),
                 "created_at_ms": r[6]} for r in work_rows
            ],
        }

    # -- Internal transaction helpers ---------------------------------

    def _append_occ(
        self,
        conn: sqlite3.Connection,
        *,
        person_id: str,
        expected: int | None,
        actual: int | None,
        attempted: int,
        status: str,
        reason_code: str,
        command_ref: str,
        source_kind: str,
        now_ms: int,
    ) -> dict[str, Any]:
        event_id = f"lifeocc_{uuid.uuid4().hex}"
        conn.execute(
            """
            INSERT INTO digital_life_state_occ_event(
                event_id, person_id, expected_version, actual_version,
                attempted_version, status, reason_code, command_ref,
                source_kind, created_at_ms
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id, person_id, expected, actual, attempted, status,
                reason_code, command_ref, source_kind, now_ms,
            ),
        )
        return {
            "status": status,
            "reason_code": reason_code,
            "person_id": person_id,
            "expected_version": expected,
            "actual_version": actual,
            "attempted_version": attempted,
            "occ_event_id": event_id,
        }

    @staticmethod
    def _record_uow(
        conn: sqlite3.Connection,
        *,
        operation: str,
        aggregate_id: str,
        command_ref: str,
        payload: Any,
        expected_version: int | None,
        resulting_version: int | None,
        status: str,
        created_at_ms: int,
        identity: str,
    ) -> None:
        canonical = _canonical_json(payload)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        uow_id = f"life-uow::{operation.lower()}::{identity}"
        if len(uow_id) > 240:
            uow_id = f"life-uow::{operation.lower()}::{digest}"
        conn.execute(
            """
            INSERT INTO life_runtime_uow(
                uow_id, operation, aggregate_id, command_ref, payload_digest,
                expected_version, resulting_version, status, writer_id, created_at_ms
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, 'life_runtime_repository', ?)
            """,
            (
                uow_id, operation, aggregate_id, command_ref or None, digest,
                expected_version, resulting_version, status, created_at_ms,
            ),
        )


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    except (TypeError, ValueError) as exc:
        raise LifeRuntimeRepositoryError(LIFE_RUNTIME_INVALID, "payload is not JSON") from exc


def _decode_object(value: Any) -> dict[str, Any]:
    try:
        decoded = json.loads(str(value or "{}"))
    except json.JSONDecodeError as exc:
        raise LifeRuntimeRepositoryError(
            LIFE_RUNTIME_DURABLE_DATA_INVALID, "stored JSON is invalid"
        ) from exc
    if not isinstance(decoded, dict):
        raise LifeRuntimeRepositoryError(
            LIFE_RUNTIME_DURABLE_DATA_INVALID, "stored JSON is not an object"
        )
    return decoded


def _decode_list(value: Any) -> list[Any]:
    try:
        decoded = json.loads(str(value or "[]"))
    except json.JSONDecodeError as exc:
        raise LifeRuntimeRepositoryError(
            LIFE_RUNTIME_DURABLE_DATA_INVALID, "stored JSON is invalid"
        ) from exc
    if not isinstance(decoded, list):
        raise LifeRuntimeRepositoryError(
            LIFE_RUNTIME_DURABLE_DATA_INVALID, "stored JSON is not a list"
        )
    return decoded


__all__ = [
    "LIFE_RUNTIME_CONFLICT",
    "LIFE_RUNTIME_DURABLE_DATA_INVALID",
    "LIFE_RUNTIME_INVALID",
    "LIFE_RUNTIME_MIGRATION_ASSET_KEY",
    "LIFE_RUNTIME_MIGRATION_SHA256",
    "LIFE_RUNTIME_SCHEMA_NOT_READY",
    "LifeRuntimeRepository",
    "LifeRuntimeRepositoryError",
]
