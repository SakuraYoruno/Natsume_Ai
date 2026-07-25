"""Connection-local authority for canonical Acceptance Effect staging.

Migration 0125 calls the registered SQLite function for every new Effect
Intent. Registration alone grants nothing: the current execution context must
hold the exact immutable Effect binding entered by the canonical private
staging hook while its owning Acceptance transaction is active.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
import re
import sqlite3


TASK_ACCEPTANCE_EFFECT_STAGING_AUTHORITY_FUNCTION = (
    "natsume_task_acceptance_effect_staging_authorized"
)
TASK_ACCEPTANCE_EFFECT_STAGING_AUTHORITY_MIGRATION_ID = 125
TASK_ACCEPTANCE_EFFECT_STAGING_AUTHORITY_MIGRATION_NAME = (
    "task_acceptance_effect_staging_authority"
)
_TRIGGER = "trg_task_runtime_acceptance_effect_staging_authority_insert"
_AUTHORIZED_IDENTITIES: ContextVar[
    tuple[tuple[str, str, int, str, str, str, str], ...]
] = ContextVar(
    "task_acceptance_effect_staging_authorized_identities",
    default=(),
)

_TRIGGER_SQL = """
    CREATE TRIGGER trg_task_runtime_acceptance_effect_staging_authority_insert
    BEFORE INSERT ON task_runtime_acceptance_effect_intent
    BEGIN
        SELECT CASE
            WHEN natsume_task_acceptance_effect_staging_authorized(
                NEW.effect_id,
                NEW.graph_id,
                NEW.graph_revision,
                NEW.node_id,
                NEW.attempt_id,
                NEW.commit_scope,
                NEW.idempotency_key
            ) <> 1 THEN
                RAISE(ABORT, 'TASK_ACCEPTANCE_EFFECT_STAGING_AUTHORITY_REQUIRED')
            WHEN NEW.status <> 'PENDING'
              OR NEW.available_ts_ms <> NEW.created_ts_ms
              OR NEW.updated_ts_ms <> NEW.created_ts_ms
              OR NEW.lease_owner IS NOT NULL
              OR NEW.lease_expires_ts_ms IS NOT NULL
              OR NEW.fencing_token <> 0
              OR NEW.attempt_count <> 0
              OR NEW.status_version <> 0
              OR NEW.receipt_ref IS NOT NULL
              OR NEW.reason_code IS NOT NULL
              OR NEW.last_error IS NOT NULL
              OR NEW.committed_ts_ms IS NOT NULL
              OR NEW.failed_ts_ms IS NOT NULL THEN
                RAISE(ABORT, 'TASK_ACCEPTANCE_EFFECT_INITIAL_STATE_REQUIRED')
        END;
    END
"""


class TaskAcceptanceEffectStagingAuthoritySchemaError(RuntimeError):
    """Migration 0125 or its exact trigger shape is unavailable."""


def _required_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _normalize_identity(
    effect_id: object,
    graph_id: object,
    graph_revision: object,
    node_id: object,
    attempt_id: object,
    commit_scope: object,
    idempotency_key: object,
) -> tuple[str, str, int, str, str, str, str] | None:
    effect = _required_text(effect_id)
    graph = _required_text(graph_id)
    node = _required_text(node_id)
    attempt = _required_text(attempt_id)
    scope = str(commit_scope or "").strip().upper()
    key = _required_text(idempotency_key)
    if isinstance(graph_revision, bool):
        return None
    try:
        revision = int(graph_revision)
    except (TypeError, ValueError):
        return None
    if (
        effect is None
        or graph is None
        or revision < 1
        or node is None
        or attempt is None
        or scope not in {"NODE", "GRAPH"}
        or key is None
    ):
        return None
    return effect, graph, revision, node, attempt, scope, key


def _is_authorized(
    effect_id: object,
    graph_id: object,
    graph_revision: object,
    node_id: object,
    attempt_id: object,
    commit_scope: object,
    idempotency_key: object,
) -> int:
    identity = _normalize_identity(
        effect_id,
        graph_id,
        graph_revision,
        node_id,
        attempt_id,
        commit_scope,
        idempotency_key,
    )
    authorized = _AUTHORIZED_IDENTITIES.get()
    return int(
        identity is not None
        and bool(authorized)
        and authorized[-1] == identity
    )


def register_task_acceptance_effect_staging_authority(
    conn: sqlite3.Connection,
) -> None:
    """Register the fail-closed trigger callback on one TaskStore connection."""

    if not isinstance(conn, sqlite3.Connection):
        raise TypeError(
            "Acceptance Effect staging authority requires sqlite3.Connection"
        )
    conn.create_function(
        TASK_ACCEPTANCE_EFFECT_STAGING_AUTHORITY_FUNCTION,
        7,
        _is_authorized,
        deterministic=False,
    )


@contextmanager
def canonical_task_acceptance_effect_staging_scope(
    *,
    effect_id: object,
    graph_id: object,
    graph_revision: object,
    node_id: object,
    attempt_id: object,
    commit_scope: object,
    idempotency_key: object,
) -> Iterator[None]:
    """Authorize one exact immutable Effect identity in the current context."""

    identity = _normalize_identity(
        effect_id,
        graph_id,
        graph_revision,
        node_id,
        attempt_id,
        commit_scope,
        idempotency_key,
    )
    if identity is None:
        raise ValueError("canonical Acceptance Effect staging identity is invalid")
    previous = _AUTHORIZED_IDENTITIES.get()
    token = _AUTHORIZED_IDENTITIES.set((*previous, identity))
    try:
        yield
    finally:
        _AUTHORIZED_IDENTITIES.reset(token)


def assert_task_acceptance_effect_staging_authority_schema(
    conn: sqlite3.Connection,
) -> None:
    """Verify the exact migration receipt and complete trigger SQL."""

    try:
        migration = conn.execute(
            """
            SELECT name
            FROM schema_migrations
            WHERE migration_id = ?
            """,
            (TASK_ACCEPTANCE_EFFECT_STAGING_AUTHORITY_MIGRATION_ID,),
        ).fetchone()
    except sqlite3.DatabaseError as exc:
        raise TaskAcceptanceEffectStagingAuthoritySchemaError(
            "Acceptance Effect staging authority migration catalog is unavailable"
        ) from exc
    if (
        migration is None
        or str(migration[0])
        != TASK_ACCEPTANCE_EFFECT_STAGING_AUTHORITY_MIGRATION_NAME
    ):
        raise TaskAcceptanceEffectStagingAuthoritySchemaError(
            "Acceptance Effect staging authority migration 0125 is missing"
        )
    row = conn.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'trigger' AND name = ?
        """,
        (_TRIGGER,),
    ).fetchone()
    if row is None:
        raise TaskAcceptanceEffectStagingAuthoritySchemaError(
            "Acceptance Effect staging authority trigger is missing"
        )
    normalized = re.sub(r"\s+", "", str(row[0] or "")).lower()
    expected = re.sub(r"\s+", "", _TRIGGER_SQL).lower()
    if normalized != expected:
        raise TaskAcceptanceEffectStagingAuthoritySchemaError(
            "Acceptance Effect staging authority trigger SQL drifted"
        )


__all__ = [
    "TASK_ACCEPTANCE_EFFECT_STAGING_AUTHORITY_FUNCTION",
    "TASK_ACCEPTANCE_EFFECT_STAGING_AUTHORITY_MIGRATION_ID",
    "TASK_ACCEPTANCE_EFFECT_STAGING_AUTHORITY_MIGRATION_NAME",
    "TaskAcceptanceEffectStagingAuthoritySchemaError",
    "assert_task_acceptance_effect_staging_authority_schema",
    "canonical_task_acceptance_effect_staging_scope",
    "register_task_acceptance_effect_staging_authority",
]
