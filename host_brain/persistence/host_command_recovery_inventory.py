"""Surface-filtered durable Host recovery inventory.

This focused read-model mixin keeps startup inventory SQL out of the command
admission UoW while reusing the repository's connection and canonical record
validation.  It owns no connection factory and performs no writes.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from host_brain.persistence.host_command_repository_codec import (
    required_int,
    required_text,
)
from host_brain.persistence.host_command_repository_types import (
    HOST_COMMAND_INVALID,
    HostCommandRepositoryError,
)


class HostCommandRecoveryInventoryMixin:
    """Read one bounded, same-snapshot inventory for Host continuation."""

    def list_nonterminal_admissions(
        self,
        *,
        surfaces: Sequence[str],
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return Host admissions plus one overflow sentinel when present."""

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


__all__ = ["HostCommandRecoveryInventoryMixin"]
