"""SQLite owner for restrictive, workspace-scoped long-term goals."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from host_brain.contracts.long_term_goal import (
    LongTermGoalConstraintSpecV1,
    LongTermGoalContractError,
    LongTermGoalSnapshotV1,
    normalize_long_term_goal_constraint_specs,
)
from host_brain.contracts.runtime_context import ContextConstraintV1, WorldFactV2
from host_brain.core.ids import now_ms


LONG_TERM_GOAL_MIGRATION_ASSET_KEY = "0137#long_term_goal_authority"
_MIGRATION_FILE = "0137_long_term_goal_authority.sql"
_ALLOWED_MIGRATION_DISPOSITIONS = frozenset(
    {"APPLIED", "APPLIED_WITH_COMPAT", "LEGACY_BACKFILL"}
)
_COMMANDS = frozenset({"long_term_goal.activate", "long_term_goal.revoke"})
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+~-]{0,239}$")
_GOAL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,159}$")
_WORLD_GOAL_TTL_MS = 5 * 60_000
_EVENT_COLUMNS = frozenset(
    {
        "runtime_scope_id",
        "workspace_id",
        "subject_id",
        "goal_id",
        "version",
        "expected_version",
        "operation_id",
        "event_kind",
        "goal_ref",
        "constraints_json",
        "input_digest",
        "previous_event_digest",
        "event_digest",
        "ingress_audit_ref",
        "principal_id",
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
        "workspace_id",
        "subject_id",
        "goal_id",
        "version",
        "status",
        "goal_ref",
        "constraints_json",
        "operation_id",
        "event_digest",
        "updated_at_ms",
    }
)
_REQUIRED_TRIGGERS = frozenset(
    {
        "long_term_goal_event_no_update",
        "long_term_goal_event_no_delete",
        "long_term_goal_head_no_delete",
        "long_term_goal_event_requires_active_runtime_lease",
        "long_term_goal_event_requires_contiguous_head",
        "long_term_goal_head_requires_matching_event_insert",
        "long_term_goal_head_requires_matching_event_update",
    }
)


class LongTermGoalRepositoryError(RuntimeError):
    """The canonical long-term-goal authority failed closed."""


def _canonical(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise LongTermGoalRepositoryError(
            "LONG_TERM_GOAL_CANONICAL_JSON_INVALID"
        ) from exc


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _required_text(value: Any, field: str, limit: int = 512) -> str:
    result = str(value or "").strip()
    if (
        not result
        or len(result.encode("utf-8")) > limit
        or any(ord(character) < 32 or ord(character) == 127 for character in result)
    ):
        raise LongTermGoalRepositoryError(
            f"LONG_TERM_GOAL_{field.upper()}_INVALID"
        )
    return result


def _identifier(value: Any, field: str) -> str:
    result = _required_text(value, field, 240)
    if _IDENTIFIER_RE.fullmatch(result) is None:
        raise LongTermGoalRepositoryError(
            f"LONG_TERM_GOAL_{field.upper()}_INVALID"
        )
    return result


def _goal_id(value: Any) -> str:
    result = _required_text(value, "goal_id", 160)
    if _GOAL_ID_RE.fullmatch(result) is None:
        raise LongTermGoalRepositoryError("LONG_TERM_GOAL_GOAL_ID_INVALID")
    return result


def _non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or type(value) is not int or value < 0:
        raise LongTermGoalRepositoryError(
            f"LONG_TERM_GOAL_{field.upper()}_INVALID"
        )
    return value


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or type(value) is not int or value < 1:
        raise LongTermGoalRepositoryError(
            f"LONG_TERM_GOAL_{field.upper()}_INVALID"
        )
    return value


def _digest_identifier(prefix: str, value: object) -> str:
    text = str(value or "").strip()
    if text and _IDENTIFIER_RE.fullmatch(text):
        return text
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
    return f"{prefix}-{digest}"


def derive_long_term_goal_scope(
    *,
    principal_id: str,
    tenant_id: str | None,
) -> tuple[str, str]:
    """Derive the same server-owned workspace and subject as Runtime Context."""

    principal = _digest_identifier(
        "principal",
        _required_text(principal_id, "principal_id"),
    )
    tenant = str(tenant_id or "").strip()
    workspace = _digest_identifier(
        "workspace",
        f"tenant:{tenant}" if tenant else f"personal:{principal}",
    )
    subject = _digest_identifier("subject", principal)
    return workspace, subject


def _goal_ref(
    *,
    workspace_id: str,
    subject_id: str,
    goal_id: str,
    version: int,
) -> str:
    return (
        f"long-term-goal://{workspace_id}/{subject_id}/{goal_id}/v{version}"
    )


def _constraints_from_json(
    raw: Any,
    *,
    goal_ref: str,
) -> tuple[ContextConstraintV1, ...]:
    try:
        values = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise LongTermGoalRepositoryError(
            "LONG_TERM_GOAL_CONSTRAINTS_INVALID"
        ) from exc
    if not isinstance(values, list):
        raise LongTermGoalRepositoryError("LONG_TERM_GOAL_CONSTRAINTS_INVALID")
    try:
        constraints = tuple(
            ContextConstraintV1(
                code=item["code"],
                disposition=item["disposition"],
                origin=item["origin"],
                source_ref=item["source_ref"],
                reason=item.get("reason", ""),
            )
            for item in values
            if isinstance(item, Mapping)
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise LongTermGoalRepositoryError(
            "LONG_TERM_GOAL_CONSTRAINTS_INVALID"
        ) from exc
    if len(constraints) != len(values) or any(
        item.origin != "LONG_TERM_GOAL" or item.source_ref != goal_ref
        for item in constraints
    ):
        raise LongTermGoalRepositoryError("LONG_TERM_GOAL_CONSTRAINTS_INVALID")
    return tuple(sorted(constraints, key=lambda item: item.constraint_digest))


def _snapshot_from_row(row: sqlite3.Row) -> LongTermGoalSnapshotV1:
    goal_ref = str(row["goal_ref"])
    return LongTermGoalSnapshotV1(
        runtime_scope_id=str(row["runtime_scope_id"]),
        workspace_id=str(row["workspace_id"]),
        subject_id=str(row["subject_id"]),
        goal_id=str(row["goal_id"]),
        version=int(row["version"]),
        status=str(row["status"]),
        goal_ref=goal_ref,
        constraints=_constraints_from_json(
            row["constraints_json"],
            goal_ref=goal_ref,
        ),
        operation_id=str(row["operation_id"]),
        event_digest=str(row["event_digest"]),
        updated_at_ms=int(row["updated_at_ms"]),
    )


class SQLiteLongTermGoalAuthority:
    """One runtime-fenced writer and exact replay reader for long-term goals."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        runtime_scope_id: str,
        runtime_authority_provider: Callable[[], Mapping[str, Any]],
        clock: Callable[[], int] = now_ms,
    ) -> None:
        raw_path = str(db_path or "").strip()
        if (
            not raw_path
            or raw_path == ":memory:"
            or raw_path.lower().startswith("file:")
        ):
            raise LongTermGoalRepositoryError(
                "LONG_TERM_GOAL_DURABLE_PATH_REQUIRED"
            )
        self.db_path = Path(raw_path).expanduser().resolve()
        self._runtime_scope_id = _identifier(
            runtime_scope_id,
            "runtime_scope_id",
        )
        if not callable(runtime_authority_provider):
            raise LongTermGoalRepositoryError(
                "LONG_TERM_GOAL_RUNTIME_AUTHORITY_PROVIDER_REQUIRED"
            )
        if not callable(clock):
            raise LongTermGoalRepositoryError("LONG_TERM_GOAL_CLOCK_INVALID")
        self._runtime_authority_provider = runtime_authority_provider
        self._clock = clock

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

    def assert_schema_ready(self) -> None:
        migration = (
            Path(__file__).resolve().parents[1]
            / "audit"
            / "migrations"
            / _MIGRATION_FILE
        )
        try:
            checksum = hashlib.sha256(
                migration.read_text(encoding="utf-8-sig").encode("utf-8")
            ).hexdigest()
            with self._connection() as connection:
                asset = connection.execute(
                    """
                    SELECT checksum, disposition
                    FROM schema_migration_assets
                    WHERE asset_key = ?
                    """,
                    (LONG_TERM_GOAL_MIGRATION_ASSET_KEY,),
                ).fetchone()
                event_columns = frozenset(
                    str(row[1])
                    for row in connection.execute(
                        "PRAGMA table_info(long_term_goal_event)"
                    ).fetchall()
                )
                head_columns = frozenset(
                    str(row[1])
                    for row in connection.execute(
                        "PRAGMA table_info(long_term_goal_head)"
                    ).fetchall()
                )
                triggers = frozenset(
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'trigger'"
                    ).fetchall()
                )
        except (OSError, sqlite3.Error) as exc:
            raise LongTermGoalRepositoryError(
                "LONG_TERM_GOAL_MIGRATION_DRIFT"
            ) from exc
        if (
            asset is None
            or str(asset["checksum"]) != checksum
            or str(asset["disposition"]) not in _ALLOWED_MIGRATION_DISPOSITIONS
            or event_columns != _EVENT_COLUMNS
            or head_columns != _HEAD_COLUMNS
            or not _REQUIRED_TRIGGERS.issubset(triggers)
        ):
            raise LongTermGoalRepositoryError("LONG_TERM_GOAL_MIGRATION_DRIFT")

    def get_head(
        self,
        *,
        workspace_id: str,
        subject_id: str,
        goal_id: str,
    ) -> LongTermGoalSnapshotV1 | None:
        self.assert_schema_ready()
        scope = (
            _identifier(workspace_id, "workspace_id"),
            _identifier(subject_id, "subject_id"),
            _goal_id(goal_id),
        )
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM long_term_goal_head
                WHERE runtime_scope_id = ?
                  AND workspace_id = ?
                  AND subject_id = ?
                  AND goal_id = ?
                """,
                (self._runtime_scope_id, *scope),
            ).fetchone()
        return None if row is None else _snapshot_from_row(row)

    def list_active(
        self,
        *,
        workspace_id: str,
        subject_id: str,
    ) -> tuple[LongTermGoalSnapshotV1, ...]:
        self.assert_schema_ready()
        workspace = _identifier(workspace_id, "workspace_id")
        subject = _identifier(subject_id, "subject_id")
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM long_term_goal_head
                WHERE runtime_scope_id = ?
                  AND workspace_id = ?
                  AND subject_id = ?
                  AND status = 'ACTIVE'
                ORDER BY goal_id ASC
                """,
                (self._runtime_scope_id, workspace, subject),
            ).fetchall()
        return tuple(_snapshot_from_row(row) for row in rows)

    def list_active_world_facts(
        self,
        *,
        workspace_id: str,
        subject_id: str,
        observed_at_ms: int,
        source_ref: str,
    ) -> tuple[WorldFactV2, ...]:
        observed = _non_negative_int(observed_at_ms, "observed_at_ms")
        source = _required_text(source_ref, "source_ref", 1024)
        facts: list[WorldFactV2] = []
        for goal in self.list_active(
            workspace_id=workspace_id,
            subject_id=subject_id,
        ):
            conflict = f"long-term-goal:{goal.goal_id}"
            facts.append(
                WorldFactV2(
                    workspace_id=goal.workspace_id,
                    subject_id=goal.subject_id,
                    key="goal.long_term.ref",
                    value=goal.goal_ref,
                    source_ref=source,
                    confidence=1.0,
                    observed_at_ms=observed,
                    expires_at_ms=observed + _WORLD_GOAL_TTL_MS,
                    conflict_group=conflict,
                    revision=goal.version,
                )
            )
            facts.extend(
                WorldFactV2(
                    workspace_id=goal.workspace_id,
                    subject_id=goal.subject_id,
                    key="goal.long_term.constraint",
                    value=constraint.to_dict(),
                    source_ref=source,
                    confidence=1.0,
                    observed_at_ms=observed,
                    expires_at_ms=observed + _WORLD_GOAL_TTL_MS,
                    conflict_group=conflict,
                    revision=goal.version,
                )
                for constraint in goal.constraints
            )
        return tuple(facts)

    def active_identity(
        self,
        *,
        workspace_id: str,
        subject_id: str,
    ) -> dict[str, Any]:
        goals = self.list_active(
            workspace_id=workspace_id,
            subject_id=subject_id,
        )
        payload = [goal.to_dict() for goal in goals]
        return {
            "goal_refs": [goal.goal_ref for goal in goals],
            "constraints": [
                constraint.to_dict()
                for goal in goals
                for constraint in goal.constraints
            ],
            "authority_digest": _digest(payload),
        }

    def apply_accepted_transition(
        self,
        *,
        authority: Mapping[str, Any],
        command_id: str,
        operation_id: str,
        workspace_id: str,
        subject_id: str,
        goal_id: str,
        expected_version: int,
        constraints: Sequence[Mapping[str, Any]] | Sequence[
            LongTermGoalConstraintSpecV1
        ] = (),
        observed_at_ms: int,
    ) -> LongTermGoalSnapshotV1:
        command = _required_text(command_id, "command_id")
        if command not in _COMMANDS:
            raise LongTermGoalRepositoryError(
                "LONG_TERM_GOAL_COMMAND_UNSUPPORTED"
            )
        operation = _required_text(operation_id, "operation_id")
        workspace = _identifier(workspace_id, "workspace_id")
        subject = _identifier(subject_id, "subject_id")
        goal = _goal_id(goal_id)
        expected = _non_negative_int(expected_version, "expected_version")
        observed = _non_negative_int(observed_at_ms, "observed_at_ms")
        ingress = self._ingress_authority(authority)
        derived = derive_long_term_goal_scope(
            principal_id=ingress["principal_id"],
            tenant_id=ingress["tenant_id"],
        )
        if derived != (workspace, subject):
            raise LongTermGoalRepositoryError(
                "LONG_TERM_GOAL_INGRESS_SCOPE_DRIFT"
            )
        specs = (
            normalize_long_term_goal_constraint_specs(constraints)
            if command == "long_term_goal.activate"
            else ()
        )
        if command == "long_term_goal.revoke" and tuple(constraints):
            raise LongTermGoalRepositoryError(
                "LONG_TERM_GOAL_REVOKE_CONSTRAINTS_INVALID"
            )
        input_payload = {
            "command_id": command,
            "operation_id": operation,
            "workspace_id": workspace,
            "subject_id": subject,
            "goal_id": goal,
            "expected_version": expected,
            "constraints": [item.to_dict() for item in specs],
            "observed_at_ms": observed,
            "ingress_audit_ref": ingress["audit_ref"],
            "principal_id": ingress["principal_id"],
        }
        input_digest = _digest(input_payload)
        created = int(self._clock())
        runtime = self._runtime_authority(created_at_ms=created)
        self.assert_schema_ready()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = connection.execute(
                    """
                    SELECT * FROM long_term_goal_event
                    WHERE runtime_scope_id = ? AND operation_id = ?
                    """,
                    (self._runtime_scope_id, operation),
                ).fetchone()
                if replay is not None:
                    if str(replay["input_digest"]) != input_digest:
                        raise LongTermGoalRepositoryError(
                            "LONG_TERM_GOAL_OPERATION_CONFLICT"
                        )
                    result = self._snapshot_from_event(replay)
                    connection.commit()
                    return result
                head = connection.execute(
                    """
                    SELECT * FROM long_term_goal_head
                    WHERE runtime_scope_id = ?
                      AND workspace_id = ?
                      AND subject_id = ?
                      AND goal_id = ?
                    """,
                    (self._runtime_scope_id, workspace, subject, goal),
                ).fetchone()
                actual = 0 if head is None else int(head["version"])
                if actual != expected:
                    raise LongTermGoalRepositoryError(
                        "LONG_TERM_GOAL_EXPECTED_VERSION_STALE"
                    )
                if (
                    command == "long_term_goal.revoke"
                    and (
                        head is None
                        or str(head["status"]).upper() != "ACTIVE"
                    )
                ):
                    raise LongTermGoalRepositoryError(
                        "LONG_TERM_GOAL_ACTIVE_HEAD_REQUIRED"
                    )
                version = actual + 1
                goal_ref = _goal_ref(
                    workspace_id=workspace,
                    subject_id=subject,
                    goal_id=goal,
                    version=version,
                )
                bound_constraints = tuple(
                    item.bind(goal_ref) for item in specs
                )
                constraints_json = _canonical(
                    [item.to_dict() for item in bound_constraints]
                )
                previous = "" if head is None else str(head["event_digest"])
                event_kind = (
                    "ACTIVATED"
                    if command == "long_term_goal.activate"
                    else "REVOKED"
                )
                event_payload = {
                    **input_payload,
                    "version": version,
                    "event_kind": event_kind,
                    "goal_ref": goal_ref,
                    "constraints": [
                        item.to_dict() for item in bound_constraints
                    ],
                    "previous_event_digest": previous,
                    **runtime,
                    "created_at_ms": created,
                }
                event_digest = _digest(event_payload)
                connection.execute(
                    """
                    INSERT INTO long_term_goal_event(
                        runtime_scope_id, workspace_id, subject_id, goal_id,
                        version, expected_version, operation_id, event_kind,
                        goal_ref, constraints_json, input_digest,
                        previous_event_digest, event_digest, ingress_audit_ref,
                        principal_id, runtime_owner_id, runtime_lease_id,
                        runtime_fencing_token, runtime_issued_at_ms,
                        runtime_expires_at_ms, observed_at_ms, created_at_ms
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        self._runtime_scope_id,
                        workspace,
                        subject,
                        goal,
                        version,
                        expected,
                        operation,
                        event_kind,
                        goal_ref,
                        constraints_json,
                        input_digest,
                        previous,
                        event_digest,
                        ingress["audit_ref"],
                        ingress["principal_id"],
                        runtime["runtime_owner_id"],
                        runtime["runtime_lease_id"],
                        runtime["runtime_fencing_token"],
                        runtime["runtime_issued_at_ms"],
                        runtime["runtime_expires_at_ms"],
                        observed,
                        created,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO long_term_goal_head(
                        runtime_scope_id, workspace_id, subject_id, goal_id,
                        version, status, goal_ref, constraints_json,
                        operation_id, event_digest, updated_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(
                        runtime_scope_id, workspace_id, subject_id, goal_id
                    ) DO UPDATE SET
                        version = excluded.version,
                        status = excluded.status,
                        goal_ref = excluded.goal_ref,
                        constraints_json = excluded.constraints_json,
                        operation_id = excluded.operation_id,
                        event_digest = excluded.event_digest,
                        updated_at_ms = excluded.updated_at_ms
                    """,
                    (
                        self._runtime_scope_id,
                        workspace,
                        subject,
                        goal,
                        version,
                        "ACTIVE" if event_kind == "ACTIVATED" else "REVOKED",
                        goal_ref,
                        constraints_json,
                        operation,
                        event_digest,
                        observed,
                    ),
                )
                row = connection.execute(
                    """
                    SELECT * FROM long_term_goal_head
                    WHERE runtime_scope_id = ?
                      AND workspace_id = ?
                      AND subject_id = ?
                      AND goal_id = ?
                    """,
                    (self._runtime_scope_id, workspace, subject, goal),
                ).fetchone()
                if row is None:
                    raise LongTermGoalRepositoryError(
                        "LONG_TERM_GOAL_HEAD_COMMIT_FAILED"
                    )
                result = _snapshot_from_row(row)
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def resolve_accepted_transition(
        self,
        *,
        operation_id: str,
    ) -> LongTermGoalSnapshotV1 | None:
        self.assert_schema_ready()
        operation = _required_text(operation_id, "operation_id")
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM long_term_goal_event
                WHERE runtime_scope_id = ? AND operation_id = ?
                """,
                (self._runtime_scope_id, operation),
            ).fetchone()
        return None if row is None else self._snapshot_from_event(row)

    def _snapshot_from_event(
        self,
        row: sqlite3.Row,
    ) -> LongTermGoalSnapshotV1:
        goal_ref = str(row["goal_ref"])
        return LongTermGoalSnapshotV1(
            runtime_scope_id=str(row["runtime_scope_id"]),
            workspace_id=str(row["workspace_id"]),
            subject_id=str(row["subject_id"]),
            goal_id=str(row["goal_id"]),
            version=int(row["version"]),
            status=(
                "ACTIVE"
                if str(row["event_kind"]) == "ACTIVATED"
                else "REVOKED"
            ),
            goal_ref=goal_ref,
            constraints=_constraints_from_json(
                row["constraints_json"],
                goal_ref=goal_ref,
            ),
            operation_id=str(row["operation_id"]),
            event_digest=str(row["event_digest"]),
            updated_at_ms=int(row["observed_at_ms"]),
        )

    def _ingress_authority(
        self,
        authority: Mapping[str, Any],
    ) -> dict[str, str]:
        if not isinstance(authority, Mapping) or authority.get("allowed") is not True:
            raise LongTermGoalRepositoryError(
                "LONG_TERM_GOAL_INGRESS_AUTHORITY_INVALID"
            )
        action = str(authority.get("operator_command_id") or "").strip()
        if action not in _COMMANDS:
            raise LongTermGoalRepositoryError(
                "LONG_TERM_GOAL_INGRESS_AUTHORITY_INVALID"
            )
        return {
            "audit_ref": _required_text(
                authority.get("audit_ref"),
                "ingress_audit_ref",
                1024,
            ),
            "principal_id": _required_text(
                authority.get("principal_id"),
                "principal_id",
            ),
            "tenant_id": str(authority.get("tenant_id") or "").strip(),
        }

    def _runtime_authority(self, *, created_at_ms: int) -> dict[str, Any]:
        try:
            raw = self._runtime_authority_provider()
        except Exception as exc:
            raise LongTermGoalRepositoryError(
                "LONG_TERM_GOAL_RUNTIME_AUTHORITY_UNAVAILABLE"
            ) from exc
        if not isinstance(raw, Mapping):
            raise LongTermGoalRepositoryError(
                "LONG_TERM_GOAL_RUNTIME_AUTHORITY_INVALID"
            )
        value = dict(raw)
        scope = _identifier(value.get("runtime_scope_id"), "runtime_scope_id")
        issued = _non_negative_int(value.get("issued_at_ms"), "issued_at_ms")
        expires = _positive_int(value.get("expires_at_ms"), "expires_at_ms")
        if (
            scope != self._runtime_scope_id
            or issued > created_at_ms
            or expires <= issued
            or expires <= created_at_ms
        ):
            raise LongTermGoalRepositoryError(
                "LONG_TERM_GOAL_RUNTIME_AUTHORITY_STALE"
            )
        return {
            "runtime_owner_id": _required_text(
                value.get("owner_id"),
                "runtime_owner_id",
            ),
            "runtime_lease_id": _required_text(
                value.get("lease_id"),
                "runtime_lease_id",
            ),
            "runtime_fencing_token": _positive_int(
                value.get("fencing_token"),
                "runtime_fencing_token",
            ),
            "runtime_issued_at_ms": issued,
            "runtime_expires_at_ms": expires,
        }


__all__ = [
    "LONG_TERM_GOAL_MIGRATION_ASSET_KEY",
    "LongTermGoalRepositoryError",
    "SQLiteLongTermGoalAuthority",
    "derive_long_term_goal_scope",
]
