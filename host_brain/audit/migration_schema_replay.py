"""Receipt-loss replay checks for checksum-bound SQLite migrations.

The frozen catalog data and generic compatibility predicates remain in
``migration_compat``.  This module owns only the expensive physical schema
postcondition checks and is re-exported by the compatibility facade.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from host_brain.audit.migration_compat import (
    CHECKSUM_BOUND_SCHEMA_REPLAY_ASSETS,
    HOST_EMBODIMENT_RUNTIME_AUTHORITY_REPLAY_ASSET,
    LEGACY_INLINE_SCHEMA_ASSETS,
    _0098_EFFECT_AWARE_REVISION_TRIGGERS,
    _host_embodiment_runtime_authority_postcondition_holds,
    _is_exact_host_embodiment_runtime_authority_asset,
    asset_filename,
)

def is_exact_checksum_bound_schema_replay_failure(
    asset: Any,
    exc: BaseException,
) -> bool:
    """Recognize only a receipt-loss replay of a frozen non-idempotent asset."""

    if asset_filename(asset) == HOST_EMBODIMENT_RUNTIME_AUTHORITY_REPLAY_ASSET:
        return bool(
            _is_exact_host_embodiment_runtime_authority_asset(asset)
            and re.fullmatch(
                r"duplicate column name:\s*runtime_scope_id",
                str(exc).strip(),
                flags=re.IGNORECASE,
            )
        )
    filename = asset_filename(asset)
    frozen = CHECKSUM_BOUND_SCHEMA_REPLAY_ASSETS.get(filename)
    normalized_error = str(exc).strip().lower()
    recognized_physical_collision = normalized_error.startswith(
        "duplicate column name:"
    ) or (
        filename == "0149_task_runtime_chat_delivery.sql"
        and normalized_error
        == "trigger trg_task_runtime_chat_delivery_identity_guard already exists"
    )
    return bool(
        frozen is not None
        and str(getattr(asset, "checksum")) == frozen[0]
        and recognized_physical_collision
    )


def checksum_bound_schema_replay_postcondition_holds(conn: Any, asset: Any) -> bool:
    """Prove that a non-idempotent migration committed before receipt loss."""

    if asset_filename(asset) == HOST_EMBODIMENT_RUNTIME_AUTHORITY_REPLAY_ASSET:
        return _host_embodiment_runtime_authority_postcondition_holds(conn, asset)
    frozen = CHECKSUM_BOUND_SCHEMA_REPLAY_ASSETS.get(asset_filename(asset))
    if frozen is None:
        return False
    (
        _checksum,
        table,
        required_columns,
        required_indexes,
        sql_fragments,
        required_triggers,
    ) = frozen
    try:
        filename = asset_filename(asset)
        effect_reconciliation_schema = False
        if filename == "0095_task_runtime_revisioned_graph_outbox.sql":
            effect_reconciliation_schema = conn.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table'
                  AND name = 'task_runtime_effect_reconciliation_case'
                """
            ).fetchone() is not None
            if effect_reconciliation_schema:
                required_triggers = _0098_EFFECT_AWARE_REVISION_TRIGGERS
        table_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        if table_row is None or table_row[0] is None:
            return False
        normalized_sql = re.sub(r"\s+", "", str(table_row[0]).lower())
        if any(fragment not in normalized_sql for fragment in sql_fragments):
            return False

        rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        columns = {
            str(row[1]): (str(row[2] or "").upper(), bool(row[3]))
            for row in rows
        }
        if not all(
            column in columns
            and columns[column][0] == expected_type
            and (not requires_not_null or columns[column][1])
            for column, (expected_type, requires_not_null) in required_columns.items()
        ):
            return False

        for index_name, (index_table, expected_index_columns) in required_indexes.items():
            index_row = conn.execute(
                "SELECT tbl_name FROM sqlite_master WHERE type = 'index' AND name = ?",
                (index_name,),
            ).fetchone()
            if index_row is None or str(index_row[0]) != index_table:
                return False
            actual_index_columns = tuple(
                str(row[2])
                for row in conn.execute(
                    f'PRAGMA index_info("{index_name}")'
                ).fetchall()
            )
            if actual_index_columns != expected_index_columns:
                return False

        for trigger_name, (trigger_table, expected_fragments) in (
            required_triggers.items()
        ):
            trigger_row = conn.execute(
                """
                SELECT tbl_name, sql FROM sqlite_master
                WHERE type = 'trigger' AND name = ?
                """,
                (trigger_name,),
            ).fetchone()
            if (
                trigger_row is None
                or str(trigger_row[0]) != trigger_table
                or trigger_row[1] is None
            ):
                return False
            normalized_trigger_sql = re.sub(
                r"\s+", "", str(trigger_row[1]).lower()
            )
            if any(
                fragment not in normalized_trigger_sql
                for fragment in expected_fragments
            ):
                return False
        if filename == "0095_task_runtime_revisioned_graph_outbox.sql":
            revision_columns = {
                str(row[1]): row
                for row in conn.execute(
                    'PRAGMA table_info("task_runtime_outbox")'
                ).fetchall()
            }
            revision_column = revision_columns.get("graph_revision")
            if (
                revision_column is None
                or str(revision_column[2] or "").strip().upper() != "INTEGER"
                or int(revision_column[3]) != 0
                or revision_column[4] is not None
                or int(revision_column[5]) != 0
            ):
                return False
            source_index = conn.execute(
                """
                SELECT tbl_name, sql FROM sqlite_master
                WHERE type = 'index'
                  AND name = 'uq_runtime_event_task_outbox_source'
                """
            ).fetchone()
            source_index_meta = {
                str(row[1]): row
                for row in conn.execute(
                    "PRAGMA index_list(runtime_event_timeline)"
                ).fetchall()
            }.get("uq_runtime_event_task_outbox_source")
            normalized_source_index = re.sub(
                r"\s+",
                "",
                str(source_index[1] if source_index is not None else "").lower(),
            )
            duplicate_source = conn.execute(
                """
                SELECT source_ref FROM runtime_event_timeline
                WHERE source_kind = 'TASK_STORE_OUTBOX'
                GROUP BY source_ref
                HAVING COUNT(*) <> 1
                LIMIT 1
                """
            ).fetchone()
            if (
                source_index is None
                or str(source_index[0]) != "runtime_event_timeline"
                or normalized_source_index
                != (
                    "createuniqueindexuq_runtime_event_task_outbox_source"
                    "onruntime_event_timeline(source_ref)"
                    "wheresource_kind='task_store_outbox'"
                )
                or source_index_meta is None
                or int(source_index_meta[2]) != 1
                or int(source_index_meta[4]) != 1
                or duplicate_source is not None
            ):
                return False
            if effect_reconciliation_schema:
                invalid_revision = conn.execute(
                    """
                    SELECT outbox.outbox_id
                    FROM task_runtime_outbox AS outbox
                    WHERE
                        (
                            outbox.aggregate_type = 'TASK_GRAPH'
                            AND (
                                outbox.graph_revision IS NULL
                                OR typeof(outbox.graph_revision) <> 'integer'
                                OR outbox.graph_revision < 1
                                OR outbox.payload_ref <> (
                                    'taskstore://event/TASK_GRAPH/'
                                    || outbox.aggregate_id || '@'
                                    || outbox.graph_revision || '/'
                                    || outbox.aggregate_version
                                )
                                OR NOT EXISTS (
                                    SELECT 1 FROM task_runtime_graph AS graph
                                    WHERE graph.graph_id = outbox.aggregate_id
                                      AND graph.revision = outbox.graph_revision
                                )
                            )
                        )
                        OR (
                            outbox.aggregate_type =
                                'GRAPH_CANCELLATION_EFFECT_RECONCILIATION'
                            AND (
                                outbox.graph_revision IS NULL
                                OR typeof(outbox.graph_revision) <> 'integer'
                                OR outbox.graph_revision < 1
                                OR outbox.payload_ref <> (
                                    'taskstore://event/'
                                    || 'GRAPH_CANCELLATION_EFFECT_RECONCILIATION/'
                                    || outbox.aggregate_id || '@'
                                    || outbox.graph_revision || '/'
                                    || outbox.aggregate_version
                                )
                                OR NOT EXISTS (
                                    SELECT 1
                                    FROM task_runtime_effect_reconciliation_case AS item
                                    WHERE item.case_id = outbox.aggregate_id
                                      AND item.graph_revision = outbox.graph_revision
                                )
                            )
                        )
                        OR (
                            outbox.aggregate_type NOT IN (
                                'TASK_GRAPH',
                                'GRAPH_CANCELLATION_EFFECT_RECONCILIATION'
                            )
                            AND outbox.graph_revision IS NOT NULL
                        )
                    LIMIT 1
                    """
                ).fetchone()
            else:
                invalid_revision = conn.execute(
                    """
                    SELECT outbox.outbox_id
                    FROM task_runtime_outbox AS outbox
                    WHERE
                        (
                            outbox.aggregate_type = 'TASK_GRAPH'
                            AND (
                                outbox.graph_revision IS NULL
                                OR typeof(outbox.graph_revision) <> 'integer'
                                OR outbox.graph_revision < 1
                                OR NOT EXISTS (
                                    SELECT 1 FROM task_runtime_graph AS graph
                                    WHERE graph.graph_id = outbox.aggregate_id
                                      AND graph.revision = outbox.graph_revision
                                )
                            )
                        )
                        OR (
                            outbox.aggregate_type <> 'TASK_GRAPH'
                            AND outbox.graph_revision IS NOT NULL
                        )
                    LIMIT 1
                    """
                ).fetchone()
            if invalid_revision is not None:
                return False
    except (IndexError, sqlite3.DatabaseError):
        return False
    return True


def legacy_inline_schema_postcondition_holds(conn: Any, asset: Any) -> bool:
    """Require every column from the frozen ALTER asset to already be TEXT."""

    frozen = LEGACY_INLINE_SCHEMA_ASSETS.get(asset_filename(asset))
    if frozen is None:
        return False
    _checksum, table, required_columns = frozen
    try:
        rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    except Exception:
        return False
    columns = {
        str(row[1]): (str(row[2] or "").upper(), bool(row[3]))
        for row in rows
    }
    return all(
        column in columns
        and columns[column][0] == expected_type
        and (not requires_not_null or columns[column][1])
        for column, (expected_type, requires_not_null) in required_columns.items()
    )
