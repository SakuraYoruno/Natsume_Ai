"""SQLite repository/UoW for preference events, profiles, and deliveries."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, nullcontext
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any


PREFERENCE_SCHEMA_NOT_READY = "PREFERENCE_SCHEMA_NOT_READY"
PREFERENCE_INVALID = "PREFERENCE_INVALID"
PREFERENCE_CONFLICT = "PREFERENCE_CONFLICT"
PREFERENCE_MIGRATION_ASSET_KEY = "0070#context_persistence"
PREFERENCE_MIGRATION_SHA256 = (
    "faf991332cffab3f9f7307b2101e9c88d6443b5b32972b08a3be946d97a43e67"
)
PREFERENCE_EFFECT_MIGRATION_ASSET_KEY = "0118#conversation_acceptance_effects"
PREFERENCE_EFFECT_MIGRATION_SHA256 = (
    "f9cacf633ce0611ac5d567919fc8f0273e9237f46a209dfabdeb1700ca5094e2"
)
_ALLOWED_DISPOSITIONS = frozenset(
    {"APPLIED", "APPLIED_WITH_COMPAT", "LEGACY_BACKFILL"}
)
_EVENT_COLUMNS = (
    "event_id", "person_id", "session_id", "domain", "source_kind",
    "polarity", "target_kind", "target_id", "target_label", "attributes_json",
    "evidence_refs_json", "confidence", "created_at_ms", "active",
)
_DELIVERY_COLUMNS = (
    "delivery_id", "person_id", "session_id", "domain", "platform",
    "conversation_id", "request_id", "job_id", "recommendations_ref",
    "ranked_recommendations_ref", "items_json", "message_payload_json",
    "created_at_ms", "active",
)
_REQUIRED_COLUMNS = {
    "user_preference_event": frozenset({"id", *_EVENT_COLUMNS}),
    "user_preference_profile": frozenset(
        {"person_id", "domain", "version", "profile_json", "updated_at_ms"}
    ),
    "recommendation_delivery_log": frozenset({"id", *_DELIVERY_COLUMNS}),
    "conversation_turn": frozenset(
        {"session_id", "role", "content", "ts_ms", "turn_index"}
    ),
}
_REQUIRED_INDEXES = frozenset(
    {
        "idx_user_preference_event_id", "idx_user_preference_event_person_domain",
        "idx_user_preference_event_target", "idx_user_preference_profile_updated",
        "idx_recommendation_delivery_id",
        "idx_recommendation_delivery_person_domain",
        "idx_recommendation_delivery_session",
        "idx_recommendation_delivery_conversation",
    }
)


class PreferenceRepositoryError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or PREFERENCE_INVALID)
        self.detail = str(detail or "preference persistence failed")
        super().__init__(f"{self.reason_code}: {self.detail}")


def _required(value: Any, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise PreferenceRepositoryError(PREFERENCE_INVALID, f"{field_name} is required")
    return normalized


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except (TypeError, json.JSONDecodeError):
        return None


def _digest_ref(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _sha256_ref(value: Any, field_name: str) -> str:
    text = str(value or "")
    if (
        len(text) != 71
        or not text.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in text[7:])
    ):
        raise PreferenceRepositoryError(
            PREFERENCE_INVALID,
            f"{field_name} must be a canonical sha256 reference",
        )
    return text


class PreferenceRepository:
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
        if not raw or (raw == ":memory:" and _borrowed_connection is None):
            raise PreferenceRepositoryError(PREFERENCE_INVALID, "valid database is required")
        self.db_path = raw if raw == ":memory:" else str(Path(raw).resolve())
        self._timeout_ms = max(1, int(timeout_ms))
        self._borrowed_connection = _borrowed_connection
        self._borrowed_lock = _borrowed_lock
        if verify_schema:
            self.assert_schema_ready(require_catalog=raw != ":memory:")

    @classmethod
    def from_ledger(cls, ledger: Any) -> "PreferenceRepository":
        if isinstance(ledger, sqlite3.Connection):
            database = ledger.execute("PRAGMA database_list").fetchone()
            database_file = str(database[2] or "") if database is not None else ""
            if database_file:
                return cls(database_file)
            return cls(
                ":memory:",
                _borrowed_connection=ledger,
                _borrowed_lock=None,
            )
        path = str(
            getattr(ledger, "canonical_db_path", "")
            or getattr(ledger, "db_path", "")
        ).strip()
        if path and path != ":memory:":
            return cls(path)
        connection = getattr(ledger, "_conn", None)
        lock = getattr(ledger, "_write_lock", None)
        if connection is None:
            raise PreferenceRepositoryError(
                PREFERENCE_INVALID, "in-memory ledger compatibility binding is unavailable"
            )
        return cls(
            path,
            _borrowed_connection=connection,
            _borrowed_lock=lock,
        )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.db_path,
            timeout=self._timeout_ms / 1000.0,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {self._timeout_ms}")
        return conn

    @contextmanager
    def _scope(self, *, write: bool) -> Iterator[sqlite3.Connection]:
        if self._borrowed_connection is not None:
            with (self._borrowed_lock or nullcontext()):
                try:
                    if write:
                        self._borrowed_connection.execute("BEGIN IMMEDIATE")
                    yield self._borrowed_connection
                    if write:
                        self._borrowed_connection.commit()
                except BaseException:
                    if write:
                        self._borrowed_connection.rollback()
                    raise
            return
        conn = self._connect()
        try:
            if write:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            if write:
                conn.commit()
        except BaseException:
            if write:
                conn.rollback()
            raise
        finally:
            conn.close()

    def assert_schema_ready(self, *, require_catalog: bool = True) -> None:
        try:
            with self._scope(write=False) as conn:
                columns = {
                    table: {
                        str(row[1])
                        for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
                    }
                    for table in _REQUIRED_COLUMNS
                }
                indexes = {
                    str(row[0])
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'index'"
                    ).fetchall()
                }
                asset = None if not require_catalog else conn.execute(
                    """
                    SELECT checksum, disposition FROM schema_migration_assets
                    WHERE asset_key = ?
                    """,
                    (PREFERENCE_MIGRATION_ASSET_KEY,),
                ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise PreferenceRepositoryError(
                PREFERENCE_SCHEMA_NOT_READY, "preference schema cannot be verified"
            ) from exc
        missing = {
            table: sorted(required - columns.get(table, set()))
            for table, required in _REQUIRED_COLUMNS.items()
            if not required <= columns.get(table, set())
        }
        bad_catalog = require_catalog and (
            asset is None
            or str(asset["checksum"]) != PREFERENCE_MIGRATION_SHA256
            or str(asset["disposition"]) not in _ALLOWED_DISPOSITIONS
        )
        if missing or not _REQUIRED_INDEXES <= indexes or bad_catalog:
            raise PreferenceRepositoryError(
                PREFERENCE_SCHEMA_NOT_READY,
                "preference schema migration is missing, stale, or checksum-drifted",
            )

    def append_events(
        self,
        events: Sequence[Mapping[str, Any]],
        *,
        profile_builder: Callable[..., Mapping[str, Any]],
        updated_at_ms: int,
    ) -> list[dict[str, Any]]:
        items = [dict(event) for event in events]
        if not items:
            return []
        records: list[dict[str, Any]] = []
        changed: set[tuple[str, str]] = set()
        with self._scope(write=True) as conn:
            for item in items:
                values = self._event_values(item)
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO user_preference_event(
                        event_id, person_id, session_id, domain, source_kind,
                        polarity, target_kind, target_id, target_label,
                        attributes_json, evidence_refs_json, confidence,
                        created_at_ms, active
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
                row = conn.execute(
                    f"SELECT {', '.join(_EVENT_COLUMNS)} FROM user_preference_event WHERE event_id = ?",
                    (values[0],),
                ).fetchone()
                if row is None:
                    raise PreferenceRepositoryError(PREFERENCE_CONFLICT, "event disappeared")
                record = dict(row)
                self._assert_event_replay(record, item)
                records.append(self._event_record(record))
                if cursor.rowcount == 1:
                    changed.add((str(record["person_id"]), str(record["domain"])))
            for person_id, domain in sorted(changed):
                rows = conn.execute(
                    f"""
                    SELECT {', '.join(_EVENT_COLUMNS)} FROM user_preference_event
                    WHERE person_id = ? AND domain = ? AND active = 1
                    ORDER BY created_at_ms ASC, id ASC
                    """,
                    (person_id, domain),
                ).fetchall()
                profile = dict(
                    profile_builder(
                        person_id=person_id,
                        domain=domain,
                        events=[self._event_record(row) for row in rows],
                    )
                )
                current = conn.execute(
                    "SELECT version FROM user_preference_profile WHERE person_id = ? AND domain = ?",
                    (person_id, domain),
                ).fetchone()
                version = int(current["version"] or 0) + 1 if current else 1
                profile.update(version=version, updated_at_ms=int(updated_at_ms))
                conn.execute(
                    """
                    INSERT INTO user_preference_profile(
                        person_id, domain, version, profile_json, updated_at_ms
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(person_id, domain) DO UPDATE SET
                        version = excluded.version, profile_json = excluded.profile_json,
                        updated_at_ms = excluded.updated_at_ms
                    """,
                    (person_id, domain, version, _dumps(profile), int(updated_at_ms)),
                )
        return records

    def assert_signal_effect_schema_ready(self, *, require_catalog: bool = True) -> None:
        """Require the immutable post-Acceptance preference receipt authority."""

        try:
            with self._scope(write=False) as conn:
                columns = {
                    table: {
                        str(row[1])
                        for row in conn.execute(
                            f'PRAGMA table_info("{table}")'
                        ).fetchall()
                    }
                    for table in (
                        "conversation_preference_effect_receipt",
                        "conversation_preference_profile_version",
                    )
                }
                indexes = {
                    str(row[0])
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'index'"
                    ).fetchall()
                }
                triggers = {
                    str(row[0])
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'trigger'"
                    ).fetchall()
                }
                asset = None if not require_catalog else conn.execute(
                    """
                    SELECT checksum, disposition FROM schema_migration_assets
                    WHERE asset_key = ?
                    """,
                    (PREFERENCE_EFFECT_MIGRATION_ASSET_KEY,),
                ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise PreferenceRepositoryError(
                PREFERENCE_SCHEMA_NOT_READY,
                "conversation preference effect schema cannot be verified",
            ) from exc
        required = {
            "conversation_preference_effect_receipt": {
                "operation_id",
                "payload_sha256",
                "disposition",
                "event_bindings_json",
                "profile_receipts_json",
                "completed_at_ms",
                "receipt_digest",
            },
            "conversation_preference_profile_version": {
                "operation_id",
                "person_id",
                "domain",
                "version",
                "profile_json",
                "profile_sha256",
                "updated_at_ms",
            },
        }
        bad_catalog = require_catalog and (
            asset is None
            or str(asset["checksum"]) != PREFERENCE_EFFECT_MIGRATION_SHA256
            or str(asset["disposition"]) not in _ALLOWED_DISPOSITIONS
        )
        if (
            any(
                not expected <= columns.get(table, set())
                for table, expected in required.items()
            )
            or not {
                "idx_conversation_preference_effect_completed",
                "idx_conversation_preference_profile_operation",
            } <= indexes
            or not {
                "conversation_preference_effect_receipt_no_update",
                "conversation_preference_effect_receipt_no_delete",
                "conversation_preference_profile_version_no_update",
                "conversation_preference_profile_version_no_delete",
                "conversation_preference_profile_version_parent_sealed",
            } <= triggers
            or bad_catalog
        ):
            raise PreferenceRepositoryError(
                PREFERENCE_SCHEMA_NOT_READY,
                "conversation preference effect migration is missing or drifted",
            )

    def commit_signal_effect(
        self,
        *,
        operation_id: str,
        payload_sha256: str,
        events: Sequence[Mapping[str, Any]],
        profile_builder: Callable[..., Mapping[str, Any]],
        completed_at_ms: int,
        evaluation_disposition: str | None = None,
    ) -> dict[str, Any]:
        """Atomically write deterministic events, profiles and one receipt."""

        operation = _required(operation_id, "operation_id")
        payload_digest = _sha256_ref(payload_sha256, "payload_sha256")
        completed = int(completed_at_ms)
        if completed < 0:
            raise PreferenceRepositoryError(
                PREFERENCE_INVALID,
                "completed_at_ms must be non-negative",
            )
        items = [dict(event) for event in events]
        requested_disposition = str(evaluation_disposition or "").strip().upper()
        if requested_disposition not in {"", "NO_SIGNAL", "DISABLED"}:
            raise PreferenceRepositoryError(
                PREFERENCE_INVALID,
                "evaluation_disposition is invalid",
            )
        if requested_disposition and items:
            raise PreferenceRepositoryError(
                PREFERENCE_INVALID,
                "non-applied evaluation cannot contain preference events",
            )
        with self._scope(write=True) as conn:
            existing = self._signal_effect_receipt_row(conn, operation)
            if existing is not None:
                receipt = self._decode_signal_effect_receipt(conn, existing)
                if receipt["payload_sha256"] != payload_digest:
                    raise PreferenceRepositoryError(
                        PREFERENCE_CONFLICT,
                        "preference effect operation replay payload drifted",
                    )
                return {**receipt, "replayed": True}

            records: list[dict[str, Any]] = []
            changed: set[tuple[str, str]] = set()
            for item in items:
                values = self._event_values(item)
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO user_preference_event(
                        event_id, person_id, session_id, domain, source_kind,
                        polarity, target_kind, target_id, target_label,
                        attributes_json, evidence_refs_json, confidence,
                        created_at_ms, active
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
                row = conn.execute(
                    f"SELECT {', '.join(_EVENT_COLUMNS)} FROM user_preference_event WHERE event_id = ?",
                    (values[0],),
                ).fetchone()
                if row is None:
                    raise PreferenceRepositoryError(
                        PREFERENCE_CONFLICT,
                        "preference event disappeared before effect receipt",
                    )
                record = dict(row)
                self._assert_event_replay(record, item)
                normalized = self._event_record(record)
                records.append(normalized)
                if cursor.rowcount == 1:
                    changed.add((str(record["person_id"]), str(record["domain"])))

            profile_receipts: list[dict[str, Any]] = []
            for person_id, domain in sorted(changed):
                rows = conn.execute(
                    f"""
                    SELECT {', '.join(_EVENT_COLUMNS)} FROM user_preference_event
                    WHERE person_id = ? AND domain = ? AND active = 1
                    ORDER BY created_at_ms ASC, id ASC
                    """,
                    (person_id, domain),
                ).fetchall()
                profile = dict(
                    profile_builder(
                        person_id=person_id,
                        domain=domain,
                        events=[self._event_record(row) for row in rows],
                    )
                )
                current = conn.execute(
                    "SELECT version FROM user_preference_profile WHERE person_id = ? AND domain = ?",
                    (person_id, domain),
                ).fetchone()
                version = int(current["version"] or 0) + 1 if current else 1
                profile.update(version=version, updated_at_ms=completed)
                profile_json = _dumps(profile)
                profile_digest = _digest_ref(profile_json.encode("utf-8"))
                conn.execute(
                    """
                    INSERT INTO conversation_preference_profile_version(
                        operation_id, person_id, domain, version,
                        profile_json, profile_sha256, updated_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        operation,
                        person_id,
                        domain,
                        version,
                        profile_json,
                        profile_digest,
                        completed,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO user_preference_profile(
                        person_id, domain, version, profile_json, updated_at_ms
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(person_id, domain) DO UPDATE SET
                        version = excluded.version,
                        profile_json = excluded.profile_json,
                        updated_at_ms = excluded.updated_at_ms
                    """,
                    (person_id, domain, version, profile_json, completed),
                )
                profile_receipts.append(
                    {
                        "person_id": person_id,
                        "domain": domain,
                        "version": version,
                        "profile_sha256": profile_digest,
                        "updated_at_ms": completed,
                    }
                )

            event_bindings = [
                {
                    "event_id": str(record["event_id"]),
                    "event_sha256": _digest_ref(
                        _dumps(record).encode("utf-8")
                    ),
                }
                for record in records
            ]
            disposition = (
                "APPLIED"
                if event_bindings
                else requested_disposition or "NO_SIGNAL"
            )
            receipt_material = {
                "operation_id": operation,
                "payload_sha256": payload_digest,
                "disposition": disposition,
                "event_bindings": event_bindings,
                "profile_receipts": profile_receipts,
                "completed_at_ms": completed,
            }
            receipt_digest = _digest_ref(
                _dumps(receipt_material).encode("utf-8")
            )
            conn.execute(
                """
                INSERT INTO conversation_preference_effect_receipt(
                    operation_id, payload_sha256, disposition,
                    event_bindings_json, profile_receipts_json,
                    completed_at_ms, receipt_digest
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    operation,
                    payload_digest,
                    disposition,
                    _dumps(event_bindings),
                    _dumps(profile_receipts),
                    completed,
                    receipt_digest,
                ),
            )
            row = self._signal_effect_receipt_row(conn, operation)
            if row is None:
                raise PreferenceRepositoryError(
                    PREFERENCE_CONFLICT,
                    "preference effect receipt disappeared",
                )
            return {
                **self._decode_signal_effect_receipt(conn, row),
                "replayed": False,
            }

    def get_signal_effect_receipt(self, operation_id: str) -> dict[str, Any] | None:
        operation = _required(operation_id, "operation_id")
        with self._scope(write=False) as conn:
            row = self._signal_effect_receipt_row(conn, operation)
            return (
                self._decode_signal_effect_receipt(conn, row)
                if row is not None
                else None
            )

    @staticmethod
    def _signal_effect_receipt_row(
        conn: sqlite3.Connection,
        operation_id: str,
    ) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT operation_id, payload_sha256, disposition,
                   event_bindings_json, profile_receipts_json,
                   completed_at_ms, receipt_digest
            FROM conversation_preference_effect_receipt
            WHERE operation_id = ?
            """,
            (operation_id,),
        ).fetchone()

    def _decode_signal_effect_receipt(
        self,
        conn: sqlite3.Connection,
        row: Mapping[str, Any],
    ) -> dict[str, Any]:
        event_bindings = _loads(row["event_bindings_json"])
        profile_receipts = _loads(row["profile_receipts_json"])
        if not isinstance(event_bindings, list) or not isinstance(
            profile_receipts,
            list,
        ):
            raise PreferenceRepositoryError(
                PREFERENCE_CONFLICT,
                "preference effect receipt JSON is invalid",
            )
        _sha256_ref(row["payload_sha256"], "receipt payload_sha256")
        _sha256_ref(row["receipt_digest"], "receipt receipt_digest")
        disposition = str(row["disposition"])
        if disposition not in {"APPLIED", "NO_SIGNAL", "DISABLED"} or (
            (disposition == "APPLIED") != bool(event_bindings)
        ):
            raise PreferenceRepositoryError(
                PREFERENCE_CONFLICT,
                "preference effect disposition conflicts with event bindings",
            )
        event_ids: set[str] = set()
        affected_profiles: set[tuple[str, str]] = set()
        for binding in event_bindings:
            if not isinstance(binding, Mapping) or set(binding) != {
                "event_id",
                "event_sha256",
            }:
                raise PreferenceRepositoryError(
                    PREFERENCE_CONFLICT,
                    "preference event binding is invalid",
                )
            event_id = _required(binding.get("event_id"), "event binding event_id")
            if event_id in event_ids:
                raise PreferenceRepositoryError(
                    PREFERENCE_CONFLICT,
                    "preference effect repeats an event binding",
                )
            event_ids.add(event_id)
            _sha256_ref(binding.get("event_sha256"), "event binding digest")
            event_row = conn.execute(
                f"SELECT {', '.join(_EVENT_COLUMNS)} FROM user_preference_event WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            if event_row is None:
                raise PreferenceRepositoryError(
                    PREFERENCE_CONFLICT,
                    "preference effect event is absent",
                )
            actual_digest = _digest_ref(
                _dumps(self._event_record(event_row)).encode("utf-8")
            )
            if actual_digest != binding.get("event_sha256"):
                raise PreferenceRepositoryError(
                    PREFERENCE_CONFLICT,
                    "preference effect event digest drifted",
                )
            affected_profiles.add(
                (str(event_row["person_id"]), str(event_row["domain"]))
            )
        profile_pairs: set[tuple[str, str]] = set()
        for profile_receipt in profile_receipts:
            if not isinstance(profile_receipt, Mapping) or set(profile_receipt) != {
                "person_id",
                "domain",
                "version",
                "profile_sha256",
                "updated_at_ms",
            }:
                raise PreferenceRepositoryError(
                    PREFERENCE_CONFLICT,
                    "preference profile receipt is invalid",
                )
            person_id = _required(
                profile_receipt.get("person_id"),
                "profile receipt person_id",
            )
            domain = _required(
                profile_receipt.get("domain"),
                "profile receipt domain",
            )
            pair = (person_id, domain)
            if pair in profile_pairs:
                raise PreferenceRepositoryError(
                    PREFERENCE_CONFLICT,
                    "preference effect repeats a profile receipt",
                )
            profile_pairs.add(pair)
            _sha256_ref(
                profile_receipt.get("profile_sha256"),
                "profile receipt digest",
            )
            try:
                expected_version = int(profile_receipt.get("version"))
                expected_updated = int(profile_receipt.get("updated_at_ms"))
            except (TypeError, ValueError) as exc:
                raise PreferenceRepositoryError(
                    PREFERENCE_CONFLICT,
                    "preference profile receipt version is invalid",
                ) from exc
            if expected_version < 1 or expected_updated < 0:
                raise PreferenceRepositoryError(
                    PREFERENCE_CONFLICT,
                    "preference profile receipt version is invalid",
                )
            profile_version_row = conn.execute(
                """
                SELECT operation_id, version, profile_json,
                       profile_sha256, updated_at_ms
                FROM conversation_preference_profile_version
                WHERE operation_id = ? AND person_id = ? AND domain = ?
                """,
                (str(row["operation_id"]), *pair),
            ).fetchone()
            if (
                profile_version_row is None
                or int(profile_version_row["version"]) != expected_version
                or int(profile_version_row["updated_at_ms"]) != expected_updated
                or str(profile_version_row["profile_sha256"])
                != profile_receipt["profile_sha256"]
                or _digest_ref(
                    str(profile_version_row["profile_json"]).encode("utf-8")
                )
                != profile_receipt["profile_sha256"]
            ):
                raise PreferenceRepositoryError(
                    PREFERENCE_CONFLICT,
                    "preference profile version receipt does not resolve exactly",
                )
            profile_payload = _loads(profile_version_row["profile_json"])
            if (
                not isinstance(profile_payload, Mapping)
                or profile_payload.get("person_id") != person_id
                or profile_payload.get("domain") != domain
                or int(profile_payload.get("version") or 0) != expected_version
                or int(profile_payload.get("updated_at_ms") or -1)
                != expected_updated
            ):
                raise PreferenceRepositoryError(
                    PREFERENCE_CONFLICT,
                    "preference profile version payload identity drifted",
                )
            active_profile = conn.execute(
                """
                SELECT version FROM user_preference_profile
                WHERE person_id = ? AND domain = ?
                """,
                pair,
            ).fetchone()
            if active_profile is None or int(active_profile["version"]) < expected_version:
                raise PreferenceRepositoryError(
                    PREFERENCE_CONFLICT,
                    "active preference profile regressed behind effect receipt",
                )
        if profile_pairs != affected_profiles:
            raise PreferenceRepositoryError(
                PREFERENCE_CONFLICT,
                "preference effect profile set differs from event set",
            )
        operation_profile_rows = conn.execute(
            """
            SELECT person_id, domain, version, profile_sha256, updated_at_ms
            FROM conversation_preference_profile_version
            WHERE operation_id = ?
            """,
            (str(row["operation_id"]),),
        ).fetchall()
        operation_profile_set = {
            (
                str(version_row["person_id"]),
                str(version_row["domain"]),
                int(version_row["version"]),
                str(version_row["profile_sha256"]),
                int(version_row["updated_at_ms"]),
            )
            for version_row in operation_profile_rows
        }
        receipt_profile_set = {
            (
                str(item["person_id"]),
                str(item["domain"]),
                int(item["version"]),
                str(item["profile_sha256"]),
                int(item["updated_at_ms"]),
            )
            for item in profile_receipts
        }
        if operation_profile_set != receipt_profile_set:
            raise PreferenceRepositoryError(
                PREFERENCE_CONFLICT,
                "preference effect profile version set differs from receipt",
            )
        material = {
            "operation_id": str(row["operation_id"]),
            "payload_sha256": str(row["payload_sha256"]),
            "disposition": disposition,
            "event_bindings": event_bindings,
            "profile_receipts": profile_receipts,
            "completed_at_ms": int(row["completed_at_ms"]),
        }
        if _digest_ref(_dumps(material).encode("utf-8")) != row["receipt_digest"]:
            raise PreferenceRepositoryError(
                PREFERENCE_CONFLICT,
                "preference effect receipt digest drifted",
            )
        return {**material, "receipt_digest": str(row["receipt_digest"])}

    @staticmethod
    def _event_values(item: Mapping[str, Any]) -> tuple[Any, ...]:
        return (
            _required(item.get("event_id"), "event_id"),
            _required(item.get("person_id"), "person_id"),
            str(item.get("session_id") or ""), _required(item.get("domain"), "domain"),
            _required(item.get("source_kind"), "source_kind"),
            _required(item.get("polarity"), "polarity"),
            _required(item.get("target_kind"), "target_kind"),
            _required(item.get("target_id"), "target_id"),
            str(item.get("target_label") or ""),
            _dumps(dict(item.get("attributes") or {})),
            _dumps(list(item.get("evidence_refs") or [])),
            float(item.get("confidence") or 0.0), int(item.get("created_at_ms") or 0),
            1 if item.get("active", True) else 0,
        )

    @staticmethod
    def _assert_event_replay(row: Mapping[str, Any], item: Mapping[str, Any]) -> None:
        expected = dict(zip(_EVENT_COLUMNS, PreferenceRepository._event_values(item), strict=True))
        mismatched = []
        for key in _EVENT_COLUMNS:
            actual, wanted = row[key], expected[key]
            if key in {"attributes_json", "evidence_refs_json"}:
                if _loads(actual) != _loads(wanted):
                    mismatched.append(key)
            elif actual != wanted:
                mismatched.append(key)
        if mismatched:
            raise PreferenceRepositoryError(
                PREFERENCE_CONFLICT,
                "preference event replay conflicts with canonical fields: "
                + ", ".join(mismatched),
            )

    @staticmethod
    def _event_record(row: Mapping[str, Any]) -> dict[str, Any]:
        record = dict(row)
        record["attributes"] = _loads(record.pop("attributes_json")) or {}
        record["evidence_refs"] = _loads(record.pop("evidence_refs_json")) or []
        record["active"] = bool(record["active"])
        return record

    def get_profile(self, *, person_id: str, domain: str) -> dict[str, Any] | None:
        with self._scope(write=False) as conn:
            row = conn.execute(
                """
                SELECT person_id, domain, version, profile_json, updated_at_ms
                FROM user_preference_profile
                WHERE person_id = ? AND domain = ? LIMIT 1
                """,
                (person_id, domain),
            ).fetchone()
        if row is None:
            return None
        profile = _loads(row["profile_json"])
        result = dict(profile) if isinstance(profile, dict) else {}
        result.setdefault("person_id", str(row["person_id"] or ""))
        result.setdefault("domain", str(row["domain"] or ""))
        result.setdefault("version", int(row["version"] or 1))
        result.setdefault("updated_at_ms", int(row["updated_at_ms"] or 0))
        return result

    def append_delivery(self, delivery: Mapping[str, Any]) -> dict[str, Any]:
        item = dict(delivery)
        values = self._delivery_values(item)
        with self._scope(write=True) as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO recommendation_delivery_log(
                    delivery_id, person_id, session_id, domain, platform,
                    conversation_id, request_id, job_id, recommendations_ref,
                    ranked_recommendations_ref, items_json, message_payload_json,
                    created_at_ms, active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            row = conn.execute(
                f"SELECT {', '.join(_DELIVERY_COLUMNS)} FROM recommendation_delivery_log WHERE delivery_id = ?",
                (values[0],),
            ).fetchone()
            if row is None:
                raise PreferenceRepositoryError(PREFERENCE_CONFLICT, "delivery disappeared")
            expected = dict(zip(_DELIVERY_COLUMNS, values, strict=True))
            mismatched = []
            for key in _DELIVERY_COLUMNS:
                if key == "created_at_ms":
                    continue
                if key in {"items_json", "message_payload_json"}:
                    unequal = _loads(row[key]) != _loads(expected[key])
                else:
                    unequal = row[key] != expected[key]
                if unequal:
                    mismatched.append(key)
            if mismatched:
                raise PreferenceRepositoryError(
                    PREFERENCE_CONFLICT,
                    "delivery replay conflicts with canonical fields: " + ", ".join(mismatched),
                )
            return self._delivery_record(row)

    @staticmethod
    def _delivery_values(item: Mapping[str, Any]) -> tuple[Any, ...]:
        return (
            _required(item.get("delivery_id"), "delivery_id"),
            _required(item.get("person_id"), "person_id"), str(item.get("session_id") or ""),
            _required(item.get("domain"), "domain"), str(item.get("platform") or ""),
            str(item.get("conversation_id") or ""), str(item.get("request_id") or ""),
            str(item.get("job_id") or ""), str(item.get("recommendations_ref") or ""),
            str(item.get("ranked_recommendations_ref") or ""),
            _dumps(list(item.get("items") or [])),
            _dumps(dict(item.get("message_payload") or {})),
            int(item.get("created_at_ms") or 0), 1 if item.get("active", True) else 0,
        )

    def get_latest_delivery(
        self,
        *,
        domain: str = "",
        person_id: str = "",
        session_id: str = "",
        conversation_id: str = "",
        created_before_ms: int | None = None,
    ) -> dict[str, Any] | None:
        where, params = ["active = 1"], []
        if str(domain or "").strip():
            where.append("domain = ?")
            params.append(str(domain).strip())
        identities = []
        for column, value in (
            ("person_id", person_id), ("session_id", session_id),
            ("conversation_id", conversation_id),
        ):
            if value:
                identities.append(f"{column} = ?"); params.append(value)
        if identities:
            where.append("(" + " OR ".join(identities) + ")")
        if created_before_ms is not None and int(created_before_ms) > 0:
            where.append("created_at_ms <= ?"); params.append(int(created_before_ms))
        with self._scope(write=False) as conn:
            row = conn.execute(
                f"""
                SELECT {', '.join(_DELIVERY_COLUMNS)} FROM recommendation_delivery_log
                WHERE {' AND '.join(where)} ORDER BY created_at_ms DESC, id DESC LIMIT 1
                """,
                tuple(params),
            ).fetchone()
        return self._delivery_record(row) if row is not None else None

    def get_feedback_delivery(
        self,
        *,
        person_id: str,
        session_id: str = "",
        conversation_id: str = "",
        domain: str,
        admitted_at_ms: int,
    ) -> dict[str, Any] | None:
        """Return only a pre-admission delivery in the exact conversation scope."""

        person = _required(person_id, "person_id")
        normalized_domain = _required(domain, "domain")
        session = str(session_id or "").strip()
        conversation = str(conversation_id or "").strip()
        if not conversation and not session:
            return None
        admitted = int(admitted_at_ms)
        if admitted < 0:
            raise PreferenceRepositoryError(
                PREFERENCE_INVALID,
                "admitted_at_ms must be non-negative",
            )
        where = [
            "active = 1",
            "domain = ?",
            "person_id = ?",
            "created_at_ms <= ?",
        ]
        params: list[Any] = [normalized_domain, person, admitted]
        if conversation:
            where.append("conversation_id = ?")
            params.append(conversation)
            if session:
                where.append("session_id = ?")
                params.append(session)
        else:
            where.append("session_id = ?")
            params.append(session)
        with self._scope(write=False) as conn:
            row = conn.execute(
                f"""
                SELECT {', '.join(_DELIVERY_COLUMNS)}
                FROM recommendation_delivery_log
                WHERE {' AND '.join(where)}
                ORDER BY created_at_ms DESC, id DESC LIMIT 1
                """,
                tuple(params),
            ).fetchone()
        return self._delivery_record(row) if row is not None else None

    @staticmethod
    def _delivery_record(row: Mapping[str, Any]) -> dict[str, Any]:
        record = dict(row)
        record["items"] = _loads(record.pop("items_json")) or []
        record["message_payload"] = _loads(record.pop("message_payload_json")) or {}
        record["active"] = bool(record["active"])
        return record

    def recent_turns(self, *, session_id: str, limit: int = 16) -> list[dict[str, Any]]:
        with self._scope(write=False) as conn:
            rows = conn.execute(
                """
                SELECT role, content, ts_ms, turn_index FROM conversation_turn
                WHERE session_id = ? ORDER BY ts_ms DESC, turn_index DESC LIMIT ?
                """,
                (session_id, max(1, int(limit))),
            ).fetchall()
        return [dict(row) for row in rows]


__all__ = [
    "PREFERENCE_CONFLICT", "PREFERENCE_INVALID", "PREFERENCE_MIGRATION_ASSET_KEY",
    "PREFERENCE_MIGRATION_SHA256", "PREFERENCE_SCHEMA_NOT_READY",
    "PREFERENCE_EFFECT_MIGRATION_ASSET_KEY", "PREFERENCE_EFFECT_MIGRATION_SHA256",
    "PreferenceRepository", "PreferenceRepositoryError",
]
