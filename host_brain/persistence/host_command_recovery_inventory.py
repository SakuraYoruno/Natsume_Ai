"""Surface-filtered durable Host recovery inventory.

This focused read-model mixin keeps startup inventory SQL out of the command
admission UoW while reusing the repository's connection and canonical record
validation.  It owns no connection factory and performs no writes.
"""

from __future__ import annotations

from collections.abc import Sequence
import hashlib
import json
from typing import Any

from host_brain.persistence.host_command_repository_codec import (
    required_int,
    required_text,
)
from host_brain.persistence.host_command_repository_types import (
    HOST_COMMAND_INVALID,
    HostCommandRepositoryError,
)

_PAGE_SCHEMA_VERSION = "Natsume.host.nonterminal_admission_page.v1"
_CURSOR_SCHEMA_VERSION = "Natsume.host.nonterminal_admission_cursor.v1"
_ACTIONABLE_BLOCKED_NODE_STATUSES = (
    "READY",
    "WAITING_APPROVAL",
    "LEASED",
    "RUNNING",
    "EXECUTED",
    "VERIFYING",
    "VERIFIED",
    "WAITING_ACCEPTANCE_REVIEW",
    "RETRY_PENDING",
    "CANCEL_REQUESTED",
)
_ACTIONABLE_BLOCKED_NODE_SQL = ", ".join(
    f"'{status}'" for status in _ACTIONABLE_BLOCKED_NODE_STATUSES
)


class HostCommandRecoveryInventoryMixin:
    """Read one bounded, same-snapshot inventory for Host continuation."""

    def list_nonterminal_admission_page(
        self,
        *,
        surfaces: Sequence[str],
        limit: int = 100,
        cursor: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Read one immutable-key page without an overflow deadlock.

        Recovery mutates ``task_runtime_graph.updated_ts_ms`` while it works,
        so that mutable column cannot be a safe cursor.  HostCommand
        ``created_at_ms`` and ``command_id`` are immutable admission fields;
        together they provide a strict keyset cursor.  A subsequent periodic
        scan starts from the beginning and therefore also observes admissions
        created while an earlier scan was running.
        """

        normalized_surfaces, size = _normalize_query(
            surfaces=surfaces,
            limit=limit,
        )
        surfaces_sha256 = _surfaces_sha256(normalized_surfaces)
        normalized_cursor = _normalize_cursor(
            cursor,
            surfaces_sha256=surfaces_sha256,
        )
        placeholders = ", ".join("?" for _ in normalized_surfaces)
        cursor_sql = ""
        cursor_parameters: tuple[Any, ...] = ()
        if normalized_cursor is not None:
            cursor_sql = """
                  AND (
                      journal.created_at_ms > ?
                      OR (
                          journal.created_at_ms = ?
                          AND journal.command_id > ?
                      )
                  )
            """
            cursor_parameters = (
                normalized_cursor["created_at_ms"],
                normalized_cursor["created_at_ms"],
                normalized_cursor["command_id"],
            )

        conn = self._connect()
        try:
            conn.execute("BEGIN")
            rows = conn.execute(
                f"""
                SELECT journal.*, graph.status AS runtime_graph_status,
                       graph.updated_ts_ms AS runtime_graph_updated_ts_ms
                FROM host_command_journal AS journal
                JOIN task_runtime_graph AS graph
                  ON graph.graph_id = journal.graph_id
                 AND graph.revision = journal.graph_revision
                WHERE graph.status IN ('CREATED', 'ACTIVE', 'BLOCKED')
                  AND (
                      graph.status <> 'BLOCKED'
                      OR EXISTS (
                          SELECT 1
                            FROM task_runtime_node AS actionable_node
                           WHERE actionable_node.graph_id = graph.graph_id
                             AND actionable_node.graph_revision = graph.revision
                             AND actionable_node.status IN (
                                 {_ACTIONABLE_BLOCKED_NODE_SQL}
                             )
                      )
                  )
                  AND journal.surface IN ({placeholders})
                  {cursor_sql}
                ORDER BY journal.created_at_ms, journal.command_id
                LIMIT ?
                """,
                (
                    *normalized_surfaces,
                    *cursor_parameters,
                    size + 1,
                ),
            ).fetchall()
            page_rows = rows[:size]
            records = [
                self._validated_record(conn, row)
                for row in page_rows
            ]
            has_more = len(rows) > size
            next_cursor = (
                _build_cursor(
                    records[-1],
                    surfaces_sha256=surfaces_sha256,
                )
                if has_more and records
                else None
            )
            conn.commit()
            return {
                "schema_version": _PAGE_SCHEMA_VERSION,
                "surfaces_sha256": surfaces_sha256,
                "records": records,
                "next_cursor": next_cursor,
                "has_more": has_more,
            }
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def list_nonterminal_admissions(
        self,
        *,
        surfaces: Sequence[str],
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return Host admissions plus one overflow sentinel when present."""

        normalized_surfaces, size = _normalize_query(
            surfaces=surfaces,
            limit=limit,
        )

        # Only internally generated placeholders enter the SQL text. Surface
        # values and the bound remain positional parameters.
        placeholders = ", ".join("?" for _ in normalized_surfaces)
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            rows = conn.execute(
                f"""
                SELECT journal.*, graph.status AS runtime_graph_status,
                       graph.updated_ts_ms AS runtime_graph_updated_ts_ms
                FROM host_command_journal AS journal
                JOIN task_runtime_graph AS graph
                  ON graph.graph_id = journal.graph_id
                 AND graph.revision = journal.graph_revision
                WHERE graph.status IN ('CREATED', 'ACTIVE', 'BLOCKED')
                  AND (
                      graph.status <> 'BLOCKED'
                      OR EXISTS (
                          SELECT 1
                            FROM task_runtime_node AS actionable_node
                           WHERE actionable_node.graph_id = graph.graph_id
                             AND actionable_node.graph_revision = graph.revision
                             AND actionable_node.status IN (
                                 {_ACTIONABLE_BLOCKED_NODE_SQL}
                             )
                      )
                  )
                  AND journal.surface IN ({placeholders})
                ORDER BY graph.updated_ts_ms DESC,
                         journal.graph_id,
                         journal.graph_revision DESC,
                         journal.command_id
                LIMIT ?
                """,
                (*normalized_surfaces, size + 1),
            ).fetchall()
            records = [self._validated_record(conn, row) for row in rows]
            conn.commit()
            return records
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()


def _normalize_query(
    *,
    surfaces: Sequence[str],
    limit: int,
) -> tuple[tuple[str, ...], int]:
    if isinstance(surfaces, (str, bytes, bytearray)) or not isinstance(
        surfaces,
        Sequence,
    ):
        raise HostCommandRepositoryError(
            HOST_COMMAND_INVALID,
            "nonterminal admission surfaces must be a sequence",
        )
    normalized_surfaces = tuple(
        dict.fromkeys(
            required_text(
                surface,
                field_name="surface",
                max_length=160,
            )
            for surface in surfaces
        )
    )
    if not normalized_surfaces or len(normalized_surfaces) > 16:
        raise HostCommandRepositoryError(
            HOST_COMMAND_INVALID,
            "nonterminal admission surfaces must contain 1 to 16 values",
        )
    size = required_int(
        limit,
        field_name="nonterminal admission limit",
        minimum=1,
    )
    if size > 500:
        raise HostCommandRepositoryError(
            HOST_COMMAND_INVALID,
            "nonterminal admission limit must be <= 500",
        )
    return normalized_surfaces, size


def _surfaces_sha256(surfaces: Sequence[str]) -> str:
    encoded = json.dumps(
        list(surfaces),
        ensure_ascii=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _normalize_cursor(
    value: dict[str, Any] | None,
    *,
    surfaces_sha256: str,
) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "surfaces_sha256",
        "created_at_ms",
        "command_id",
    }:
        raise HostCommandRepositoryError(
            HOST_COMMAND_INVALID,
            "nonterminal admission cursor shape is invalid",
        )
    created_at_ms = required_int(
        value.get("created_at_ms"),
        field_name="nonterminal admission cursor created_at_ms",
        minimum=0,
    )
    command_id = required_text(
        value.get("command_id"),
        field_name="nonterminal admission cursor command_id",
        max_length=512,
    )
    if (
        value.get("schema_version") != _CURSOR_SCHEMA_VERSION
        or value.get("surfaces_sha256") != surfaces_sha256
    ):
        raise HostCommandRepositoryError(
            HOST_COMMAND_INVALID,
            "nonterminal admission cursor authority differs from its surfaces",
        )
    return {
        "schema_version": _CURSOR_SCHEMA_VERSION,
        "surfaces_sha256": surfaces_sha256,
        "created_at_ms": created_at_ms,
        "command_id": command_id,
    }


def _build_cursor(
    record: dict[str, Any],
    *,
    surfaces_sha256: str,
) -> dict[str, Any]:
    return {
        "schema_version": _CURSOR_SCHEMA_VERSION,
        "surfaces_sha256": surfaces_sha256,
        "created_at_ms": required_int(
            record.get("created_at_ms"),
            field_name="nonterminal admission created_at_ms",
            minimum=0,
        ),
        "command_id": required_text(
            record.get("command_id"),
            field_name="nonterminal admission command_id",
            max_length=512,
        ),
    }


__all__ = ["HostCommandRecoveryInventoryMixin"]
