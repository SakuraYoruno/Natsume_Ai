"""SQLite authority for the NE-03 workspace World observation journal."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
from collections.abc import Mapping
from typing import Any, Callable, Iterator, Sequence

from host_brain.core.ids import now_ms
from host_brain.runtime.context_contracts import (
    ContextContractError,
    WorldFactExpiryV1,
    WorldFactV2,
    WorldObservationBatchV1,
    WorldObservationHeadV1,
)


WORLD_OBSERVATION_MIGRATION_ASSET_KEY = "0111#world_observation_journal"


class WorldObservationJournalError(RuntimeError):
    """The durable World observation authority failed closed."""


def _scope_id(workspace_id: str, subject_id: str) -> str:
    return hashlib.sha256(
        f"{workspace_id}\x00{subject_id}".encode("utf-8")
    ).hexdigest()


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _fact_from_mapping(payload: dict[str, object]) -> WorldFactV2:
    return WorldFactV2(
        workspace_id=payload["workspace_id"],
        subject_id=payload["subject_id"],
        key=payload["key"],
        value=payload["value"],
        source_ref=payload["source_ref"],
        confidence=payload["confidence"],
        observed_at_ms=payload["observed_at_ms"],
        expires_at_ms=payload["expires_at_ms"],
        conflict_group=payload.get("conflict_group", ""),
        revision=payload.get("revision", 1),
    )


def _expiry_from_mapping(payload: dict[str, object]) -> WorldFactExpiryV1:
    return WorldFactExpiryV1(
        fact_digest=payload["fact_digest"],
        authority_ref=payload["authority_ref"],
        effective_at_ms=payload["effective_at_ms"],
        reason_code=payload.get("reason_code", "TTL_EXPIRED"),
        expiry_digest=payload.get("expiry_digest", ""),
    )


def _batch_from_json(raw: str) -> WorldObservationBatchV1:
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise TypeError("batch JSON must be an object")
        return WorldObservationBatchV1(
            workspace_id=payload["workspace_id"],
            subject_id=payload["subject_id"],
            runtime_scope_id=payload["runtime_scope_id"],
            runtime_owner_id=payload["runtime_owner_id"],
            runtime_lease_id=payload["runtime_lease_id"],
            fencing_token=payload["fencing_token"],
            revision=payload["revision"],
            previous_batch_digest=payload["previous_batch_digest"],
            source_event_ref=payload["source_event_ref"],
            source_event_digest=payload["source_event_digest"],
            observed_at_ms=payload["observed_at_ms"],
            upserts=tuple(
                _fact_from_mapping(dict(item)) for item in payload.get("upserts", ())
            ),
            expiries=tuple(
                _expiry_from_mapping(dict(item))
                for item in payload.get("expiries", ())
            ),
            batch_digest=payload.get("batch_digest", ""),
        )
    except (ContextContractError, KeyError, TypeError, ValueError) as exc:
        raise WorldObservationJournalError(
            "WORLD_OBSERVATION_JOURNAL_EVIDENCE_INVALID"
        ) from exc


class SQLiteWorldObservationJournal:
    """Atomic writer and read-only ObservationPort over migration 0111."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        runtime_scope_id: str,
        runtime_authority_provider: Callable[[], Mapping[str, Any]],
        source_event_verifier: Callable[
            [
                str,
                str,
                str,
                str,
                int,
                Sequence[WorldFactV2],
                Sequence[WorldFactExpiryV1],
            ],
            bool,
        ],
        clock: Callable[[], int] = now_ms,
    ) -> None:
        raw = str(db_path or "").strip()
        if not raw or raw == ":memory:" or raw.lower().startswith("file:"):
            raise WorldObservationJournalError(
                "WORLD_OBSERVATION_DURABLE_PATH_REQUIRED"
            )
        self.db_path = Path(raw).expanduser().resolve()
        if not callable(clock):
            raise WorldObservationJournalError("WORLD_OBSERVATION_CLOCK_INVALID")
        self._clock = clock
        self._runtime_scope_id = str(runtime_scope_id or "").strip()
        if not self._runtime_scope_id:
            raise WorldObservationJournalError(
                "WORLD_OBSERVATION_RUNTIME_SCOPE_REQUIRED"
            )
        if not callable(runtime_authority_provider):
            raise WorldObservationJournalError(
                "WORLD_OBSERVATION_RUNTIME_AUTHORITY_REQUIRED"
            )
        self._runtime_authority_provider = runtime_authority_provider
        if not callable(source_event_verifier):
            raise WorldObservationJournalError(
                "WORLD_OBSERVATION_SOURCE_VERIFIER_REQUIRED"
            )
        self._source_event_verifier = source_event_verifier
        self.assert_schema_ready()

    def _read_runtime_authority(self, *, now: int) -> dict[str, Any]:
        try:
            raw = self._runtime_authority_provider()
        except Exception as exc:
            raise WorldObservationJournalError(
                "WORLD_OBSERVATION_RUNTIME_AUTHORITY_UNAVAILABLE"
            ) from exc
        if not isinstance(raw, Mapping):
            raise WorldObservationJournalError(
                "WORLD_OBSERVATION_RUNTIME_AUTHORITY_INVALID"
            )
        authority = dict(raw)
        scope = str(authority.get("runtime_scope_id") or "").strip()
        owner = str(authority.get("owner_id") or "").strip()
        lease = str(authority.get("lease_id") or "").strip()
        try:
            fence = int(authority.get("fencing_token"))
            issued = int(authority.get("issued_at_ms"))
            expires = int(authority.get("expires_at_ms"))
        except (TypeError, ValueError) as exc:
            raise WorldObservationJournalError(
                "WORLD_OBSERVATION_RUNTIME_AUTHORITY_INVALID"
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
            raise WorldObservationJournalError(
                "WORLD_OBSERVATION_RUNTIME_AUTHORITY_STALE"
            )
        authority.update(
            {
                "runtime_scope_id": scope,
                "owner_id": owner,
                "lease_id": lease,
                "fencing_token": fence,
                "issued_at_ms": issued,
                "expires_at_ms": expires,
            }
        )
        return authority

    @staticmethod
    def _authority_ref(authority: Mapping[str, Any]) -> str:
        identity = {
            "runtime_scope_id": authority["runtime_scope_id"],
            "owner_id": authority["owner_id"],
            "lease_id": authority["lease_id"],
            "fencing_token": authority["fencing_token"],
        }
        return "runtime-authority://sha256/" + hashlib.sha256(
            _canonical(identity).encode("utf-8")
        ).hexdigest()

    def current_expiry_authority_ref(self) -> str:
        """Return the exact live Host authority ref accepted by TTL writes."""

        now = int(self._clock())
        return self._authority_ref(self._read_runtime_authority(now=now))

    @staticmethod
    def _load_fact_projection(
        conn: sqlite3.Connection,
        *,
        scope_id: str,
    ) -> tuple[dict[str, WorldFactV2], set[str]]:
        active: dict[str, WorldFactV2] = {}
        expired: set[str] = set()
        rows = conn.execute(
            "SELECT batch_json FROM world_observation_batch "
            "WHERE scope_id = ? ORDER BY revision ASC",
            (scope_id,),
        ).fetchall()
        for row in rows:
            batch = _batch_from_json(str(row[0]))
            for expiry in batch.expiries:
                if expiry.fact_digest not in active:
                    raise WorldObservationJournalError(
                        "WORLD_OBSERVATION_DURABLE_PROJECTION_INVALID"
                    )
                active.pop(expiry.fact_digest)
                expired.add(expiry.fact_digest)
            for fact in batch.upserts:
                if fact.fact_digest in expired:
                    raise WorldObservationJournalError(
                        "WORLD_OBSERVATION_DURABLE_PROJECTION_INVALID"
                    )
                active[fact.fact_digest] = fact
        return active, expired

    def assert_schema_ready(self) -> None:
        if not self.db_path.is_file():
            raise WorldObservationJournalError(
                "WORLD_OBSERVATION_SCHEMA_UNAVAILABLE"
            )
        with self._connection() as conn:
            try:
                asset = conn.execute(
                    "SELECT checksum, disposition FROM schema_migration_assets WHERE asset_key = ?",
                    (WORLD_OBSERVATION_MIGRATION_ASSET_KEY,),
                ).fetchone()
                head_columns = {
                    str(row[1])
                    for row in conn.execute(
                        "PRAGMA table_info(world_observation_head)"
                    ).fetchall()
                }
                batch_columns = {
                    str(row[1])
                    for row in conn.execute(
                        "PRAGMA table_info(world_observation_batch)"
                    ).fetchall()
                }
                trigger_rows = {
                    str(row[0]): str(row[1] or "")
                    for row in conn.execute(
                        "SELECT name, sql FROM sqlite_master WHERE type = 'trigger'"
                    ).fetchall()
                }
            except sqlite3.Error as exc:
                raise WorldObservationJournalError(
                    "WORLD_OBSERVATION_MIGRATION_DRIFT"
                ) from exc
        migration_path = (
            Path(__file__).resolve().parents[1]
            / "audit"
            / "migrations"
            / "0111_world_observation_journal.sql"
        )
        expected_checksum = hashlib.sha256(
            migration_path.read_text(encoding="utf-8-sig").encode("utf-8")
        ).hexdigest()
        if asset is None or str(asset[0]) != expected_checksum or str(asset[1]) not in {
            "APPLIED",
            "APPLIED_WITH_COMPAT",
            "LEGACY_BACKFILL",
        }:
            raise WorldObservationJournalError("WORLD_OBSERVATION_MIGRATION_DRIFT")
        if head_columns != {
            "scope_id",
            "workspace_id",
            "subject_id",
            "revision",
            "batch_digest",
            "updated_at_ms",
        } or batch_columns != {
            "scope_id",
            "revision",
            "workspace_id",
            "subject_id",
            "runtime_scope_id",
            "runtime_owner_id",
            "runtime_lease_id",
            "fencing_token",
            "batch_digest",
            "previous_batch_digest",
            "source_event_ref",
            "source_event_digest",
            "observed_at_ms",
            "batch_json",
            "created_at_ms",
        }:
            raise WorldObservationJournalError("WORLD_OBSERVATION_SCHEMA_DRIFT")
        required_triggers = {
            "world_observation_batch_no_update",
            "world_observation_batch_no_delete",
            "world_observation_batch_runtime_authority",
        }
        if not required_triggers.issubset(trigger_rows):
            raise WorldObservationJournalError("WORLD_OBSERVATION_SCHEMA_DRIFT")
        authority_sql = "".join(
            trigger_rows["world_observation_batch_runtime_authority"]
            .lower()
            .split()
        )
        for fragment in (
            "host_embodiment_runtime_lease",
            "lease.runtime_scope_id=new.runtime_scope_id",
            "lease.owner_id=new.runtime_owner_id",
            "lease.lease_id=new.runtime_lease_id",
            "lease.fencing_token=new.fencing_token",
            "lease.expires_at_ms>new.created_at_ms",
            "lease.released_at_msisnull",
        ):
            if fragment not in authority_sql:
                raise WorldObservationJournalError(
                    "WORLD_OBSERVATION_SCHEMA_DRIFT"
                )

    def append_observation_batch(
        self,
        *,
        workspace_id: str,
        subject_id: str,
        source_event_ref: str,
        source_event_digest: str,
        observed_at_ms: int,
        upserts: Sequence[WorldFactV2] = (),
        expiries: Sequence[WorldFactExpiryV1] = (),
    ) -> WorldObservationBatchV1:
        workspace = str(workspace_id or "").strip()
        subject = str(subject_id or "").strip()
        if not upserts and not expiries:
            raise WorldObservationJournalError(
                "WORLD_OBSERVATION_EMPTY_TRANSITION_REJECTED"
            )
        scope = _scope_id(workspace, subject)
        created_at_ms = int(self._clock())
        authority = self._read_runtime_authority(now=created_at_ms)
        authority_ref = self._authority_ref(authority)
        if (
            isinstance(observed_at_ms, bool)
            or not isinstance(observed_at_ms, int)
            or observed_at_ms < 0
            or observed_at_ms > created_at_ms
        ):
            raise WorldObservationJournalError(
                "WORLD_OBSERVATION_TIME_INVALID"
            )
        try:
            source_verified = self._source_event_verifier(
                workspace,
                subject,
                str(source_event_ref or "").strip(),
                str(source_event_digest or "").strip().lower(),
                observed_at_ms,
                tuple(upserts),
                tuple(expiries),
            )
        except Exception as exc:
            raise WorldObservationJournalError(
                "WORLD_OBSERVATION_SOURCE_AUTHORITY_UNAVAILABLE"
            ) from exc
        if source_verified is not True:
            raise WorldObservationJournalError(
                "WORLD_OBSERVATION_SOURCE_AUTHORITY_REJECTED"
            )
        try:
            with self._transaction() as conn:
                existing = conn.execute(
                    "SELECT batch_json FROM world_observation_batch WHERE scope_id = ? AND source_event_ref = ?",
                    (scope, str(source_event_ref or "").strip()),
                ).fetchone()
                if existing is not None:
                    durable = _batch_from_json(str(existing[0]))
                    replay = WorldObservationBatchV1(
                        workspace_id=workspace,
                        subject_id=subject,
                        runtime_scope_id=durable.runtime_scope_id,
                        runtime_owner_id=durable.runtime_owner_id,
                        runtime_lease_id=durable.runtime_lease_id,
                        fencing_token=durable.fencing_token,
                        revision=durable.revision,
                        previous_batch_digest=durable.previous_batch_digest,
                        source_event_ref=source_event_ref,
                        source_event_digest=source_event_digest,
                        observed_at_ms=observed_at_ms,
                        upserts=tuple(upserts),
                        expiries=tuple(expiries),
                    )
                    if replay.batch_digest != durable.batch_digest:
                        raise WorldObservationJournalError(
                            "WORLD_OBSERVATION_REPLAY_DRIFT"
                        )
                    return durable

                head = conn.execute(
                    "SELECT revision, batch_digest, workspace_id, subject_id FROM world_observation_head WHERE scope_id = ?",
                    (scope,),
                ).fetchone()
                if head is None:
                    conn.execute(
                        "INSERT INTO world_observation_head(scope_id, workspace_id, subject_id, revision, batch_digest, updated_at_ms) VALUES (?, ?, ?, 0, '', ?)",
                        (scope, workspace, subject, created_at_ms),
                    )
                    revision = 1
                    previous_digest = ""
                else:
                    if str(head[2]) != workspace or str(head[3]) != subject:
                        raise WorldObservationJournalError(
                            "WORLD_OBSERVATION_SCOPE_HASH_COLLISION"
                        )
                    revision = int(head[0]) + 1
                    previous_digest = str(head[1])
                    previous_row = conn.execute(
                        "SELECT observed_at_ms, runtime_owner_id, runtime_lease_id, fencing_token "
                        "FROM world_observation_batch WHERE scope_id = ? AND revision = ?",
                        (scope, revision - 1),
                    ).fetchone()
                    if previous_row is None:
                        raise WorldObservationJournalError(
                            "WORLD_OBSERVATION_HEAD_DRIFT"
                        )
                    if observed_at_ms < int(previous_row[0]):
                        raise WorldObservationJournalError(
                            "WORLD_OBSERVATION_TIME_NOT_MONOTONIC"
                        )
                    previous_fence = int(previous_row[3])
                    if authority["fencing_token"] < previous_fence or (
                        authority["fencing_token"] == previous_fence
                        and (
                            authority["owner_id"] != str(previous_row[1])
                            or authority["lease_id"] != str(previous_row[2])
                        )
                    ) or (
                        authority["fencing_token"] > previous_fence
                        and authority["lease_id"] == str(previous_row[2])
                    ):
                        raise WorldObservationJournalError(
                            "WORLD_OBSERVATION_AUTHORITY_LINEAGE_INVALID"
                        )
                active, expired = self._load_fact_projection(conn, scope_id=scope)
                for fact in upserts:
                    if fact.workspace_id != workspace or fact.subject_id != subject:
                        raise WorldObservationJournalError(
                            "WORLD_OBSERVATION_FACT_SCOPE_INVALID"
                        )
                    if fact.fact_digest in expired:
                        raise WorldObservationJournalError(
                            "WORLD_OBSERVATION_EXPIRED_FACT_REACTIVATION"
                        )
                    if (
                        fact.observed_at_ms > observed_at_ms
                        or fact.observed_at_ms > created_at_ms
                        or fact.expires_at_ms <= created_at_ms
                    ):
                        raise WorldObservationJournalError(
                            "WORLD_OBSERVATION_FACT_TIME_INVALID"
                        )
                expiry_targets: set[str] = set()
                for expiry in expiries:
                    if expiry.fact_digest in expiry_targets:
                        raise WorldObservationJournalError(
                            "WORLD_OBSERVATION_DUPLICATE_EXPIRY"
                        )
                    expiry_targets.add(expiry.fact_digest)
                    target = active.get(expiry.fact_digest)
                    if target is None:
                        raise WorldObservationJournalError(
                            "WORLD_OBSERVATION_EXPIRY_TARGET_INVALID"
                        )
                    if (
                        expiry.authority_ref != authority_ref
                        or expiry.effective_at_ms != target.expires_at_ms
                        or expiry.effective_at_ms > observed_at_ms
                        or expiry.effective_at_ms > created_at_ms
                    ):
                        raise WorldObservationJournalError(
                            "WORLD_OBSERVATION_EXPIRY_AUTHORITY_INVALID"
                        )
                batch = WorldObservationBatchV1(
                    workspace_id=workspace,
                    subject_id=subject,
                    runtime_scope_id=authority["runtime_scope_id"],
                    runtime_owner_id=authority["owner_id"],
                    runtime_lease_id=authority["lease_id"],
                    fencing_token=authority["fencing_token"],
                    revision=revision,
                    previous_batch_digest=previous_digest,
                    source_event_ref=source_event_ref,
                    source_event_digest=source_event_digest,
                    observed_at_ms=observed_at_ms,
                    upserts=tuple(upserts),
                    expiries=tuple(expiries),
                )
                encoded = _canonical(batch.to_dict())
                conn.execute(
                    "INSERT INTO world_observation_batch(scope_id, revision, workspace_id, subject_id, runtime_scope_id, runtime_owner_id, runtime_lease_id, fencing_token, batch_digest, previous_batch_digest, source_event_ref, source_event_digest, observed_at_ms, batch_json, created_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        scope,
                        batch.revision,
                        workspace,
                        subject,
                        batch.runtime_scope_id,
                        batch.runtime_owner_id,
                        batch.runtime_lease_id,
                        batch.fencing_token,
                        batch.batch_digest,
                        batch.previous_batch_digest,
                        batch.source_event_ref,
                        batch.source_event_digest,
                        batch.observed_at_ms,
                        encoded,
                        created_at_ms,
                    ),
                )
                updated = conn.execute(
                    "UPDATE world_observation_head SET revision = ?, batch_digest = ?, updated_at_ms = ? WHERE scope_id = ? AND revision = ? AND batch_digest = ?",
                    (
                        batch.revision,
                        batch.batch_digest,
                        created_at_ms,
                        scope,
                        batch.revision - 1,
                        batch.previous_batch_digest,
                    ),
                )
                if updated.rowcount != 1:
                    raise WorldObservationJournalError(
                        "WORLD_OBSERVATION_HEAD_CAS_FAILED"
                    )
                return batch
        except WorldObservationJournalError:
            raise
        except (ContextContractError, sqlite3.Error) as exc:
            raise WorldObservationJournalError(
                "WORLD_OBSERVATION_APPEND_FAILED"
            ) from exc

    def read_observation_head(
        self,
        *,
        workspace_id: str,
        subject_id: str,
    ) -> WorldObservationHeadV1:
        workspace = str(workspace_id or "").strip()
        subject = str(subject_id or "").strip()
        scope = _scope_id(workspace, subject)
        with self._connection() as conn:
            row = conn.execute(
                "SELECT workspace_id, subject_id, revision, batch_digest "
                "FROM world_observation_head WHERE scope_id = ?",
                (scope,),
            ).fetchone()
        if row is None:
            return WorldObservationHeadV1(
                workspace_id=workspace,
                subject_id=subject,
                revision=0,
                batch_digest="",
            )
        if str(row[0]) != workspace or str(row[1]) != subject:
            raise WorldObservationJournalError(
                "WORLD_OBSERVATION_SCOPE_HASH_COLLISION"
            )
        return WorldObservationHeadV1(
            workspace_id=workspace,
            subject_id=subject,
            revision=int(row[2]),
            batch_digest=str(row[3]),
        )

    def list_observation_batches(
        self,
        *,
        workspace_id: str,
        subject_id: str,
        after_revision: int,
        through_revision: int,
        limit: int,
    ) -> tuple[WorldObservationBatchV1, ...]:
        if (
            isinstance(after_revision, bool)
            or not isinstance(after_revision, int)
            or after_revision < 0
        ):
            raise WorldObservationJournalError("WORLD_OBSERVATION_CURSOR_INVALID")
        bounded_limit = int(limit)
        if not 1 <= bounded_limit <= 1000:
            raise WorldObservationJournalError("WORLD_OBSERVATION_LIMIT_INVALID")
        if (
            isinstance(through_revision, bool)
            or not isinstance(through_revision, int)
            or through_revision < after_revision
        ):
            raise WorldObservationJournalError("WORLD_OBSERVATION_HEAD_INVALID")
        workspace = str(workspace_id or "").strip()
        subject = str(subject_id or "").strip()
        scope = _scope_id(workspace, subject)
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT revision, workspace_id, subject_id, runtime_scope_id, "
                "runtime_owner_id, runtime_lease_id, fencing_token, batch_digest, "
                "previous_batch_digest, source_event_ref, source_event_digest, "
                "observed_at_ms, batch_json FROM world_observation_batch "
                "WHERE scope_id = ? AND revision > ? AND revision <= ? "
                "ORDER BY revision ASC LIMIT ?",
                (scope, after_revision, through_revision, bounded_limit),
            ).fetchall()
        batches: list[WorldObservationBatchV1] = []
        for row in rows:
            batch = _batch_from_json(str(row[12]))
            physical = (
                int(row[0]),
                str(row[1]),
                str(row[2]),
                str(row[3]),
                str(row[4]),
                str(row[5]),
                int(row[6]),
                str(row[7]),
                str(row[8]),
                str(row[9]),
                str(row[10]),
                int(row[11]),
            )
            logical = (
                batch.revision,
                batch.workspace_id,
                batch.subject_id,
                batch.runtime_scope_id,
                batch.runtime_owner_id,
                batch.runtime_lease_id,
                batch.fencing_token,
                batch.batch_digest,
                batch.previous_batch_digest,
                batch.source_event_ref,
                batch.source_event_digest,
                batch.observed_at_ms,
            )
            if physical != logical or batch.runtime_scope_id != self._runtime_scope_id:
                raise WorldObservationJournalError(
                    "WORLD_OBSERVATION_PHYSICAL_ROW_DRIFT"
                )
            try:
                source_verified = self._source_event_verifier(
                    workspace,
                    subject,
                    batch.source_event_ref,
                    batch.source_event_digest,
                    batch.observed_at_ms,
                    batch.upserts,
                    batch.expiries,
                )
            except Exception as exc:
                raise WorldObservationJournalError(
                    "WORLD_OBSERVATION_SOURCE_AUTHORITY_UNAVAILABLE"
                ) from exc
            if source_verified is not True:
                raise WorldObservationJournalError(
                    "WORLD_OBSERVATION_SOURCE_AUTHORITY_REJECTED"
                )
            batches.append(batch)
        return tuple(batches)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise


__all__ = [
    "SQLiteWorldObservationJournal",
    "WORLD_OBSERVATION_MIGRATION_ASSET_KEY",
    "WorldObservationJournalError",
]
