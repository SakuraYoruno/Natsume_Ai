"""SQLite repository/UoW for preference events, profiles, and deliveries."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, nullcontext
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
    "PreferenceRepository", "PreferenceRepositoryError",
]
