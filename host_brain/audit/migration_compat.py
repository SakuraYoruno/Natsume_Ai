"""Checksum-bound compatibility policy for the frozen SQLite catalog debt.

The SQL directory remains the only migration catalog.  This module does not
invent replacement versions or rewrite historical assets.  It describes only
the frozen catalog anomalies and exact legacy-inline-schema overlaps required
to replay that catalog against databases created before the migration runner
became the production schema authority.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import sqlite3
from typing import Any, Iterable


LEGACY_DUPLICATE_VERSION = 9
LEGACY_DUPLICATE_ASSETS: dict[str, str] = {
    "0009_agent_loop_log.sql": "244dc97e2ca1e4060dd65dcdf3244a4ae03b000527534ca28cdffd1f0d02924e",
    "0009_service_health_schema.sql": "6d2409ed28016cf260e56d137dd7912cc12b0395f7eae8b7d838837678706bdf",
}
LEGACY_DUPLICATE_PRIMARY = "0009_agent_loop_log.sql"

LEGACY_GAP_VERSIONS = frozenset(range(23, 39))
LEGACY_GAP_ANCHORS: dict[str, str] = {
    "0022_llm_route_log.sql": "f1f0a653f584ec889ab54d7a3af381b51c50a7050a6ecdd382c94323497c0128",
    "0039_llm_route_log.sql": "839d7077ca3ea3532d1b17bd857c8e42b63fbb9c10a48162e2e2912e29aed3c9",
}

LEGACY_FTS_ASSET = "0021_episodic_fts5.sql"
LEGACY_FTS_CHECKSUM = "25c938ba7eef54c52642ecb39e17be0bd4caf04b39925212cfcb59c620f778fa"
LEGACY_FTS_ERROR = "virtual tables may not be indexed"

LEGACY_INLINE_SCHEMA_ASSETS: dict[
    str,
    tuple[str, str, dict[str, tuple[str, bool]]],
] = {
    "0041_canon_change_log_body.sql": (
        "663fc6a8c069e8d7749cc580f347edc18ec89fb685f602f6e614e5c32524b06f",
        "canon_change_log",
        {
            "before_body": ("TEXT", False),
            "after_body": ("TEXT", False),
        },
    ),
    "0043_canon_proposal_content.sql": (
        "cefd91006687803d58817a1965eee369a9334c1f0558d8fd8eb804b105c8487f",
        "canon_proposal_log",
        {
            "proposed_title": ("TEXT", False),
            "proposed_tags": ("TEXT", False),
            "proposed_body": ("TEXT", False),
        },
    ),
    "0069_life_episode_retention.sql": (
        "277869c81031da2d7a1b3b5e1be084674fddb64ab478017fb4e524bd3a9e158c",
        "life_episode",
        {
            "actor_id": ("TEXT", False),
            "intent": ("TEXT", False),
            "action": ("TEXT", False),
            "result": ("TEXT", False),
            "base_salience": ("REAL", True),
            "memory_strength": ("REAL", True),
            "decay_profile_id": ("TEXT", True),
            "retention_state": ("TEXT", True),
            "last_recalled_ts_ms": ("INTEGER", False),
            "recall_count": ("INTEGER", True),
            "reinforcement_count": ("INTEGER", True),
            "compressed_into_episode_id": ("TEXT", False),
            "retention_until_ts_ms": ("INTEGER", False),
            "forget_reason": ("TEXT", False),
        },
    ),
}

# 0089 contains an ALTER TABLE followed by an index creation inside one
# explicit transaction.  The SQL transaction may commit before the runner can
# persist its migration receipt.  On the next startup SQLite then reports a
# duplicate column even though the complete physical migration is present.
# Repair is allowed only for this exact checksum and only after validating the
# column constraint plus the exact graph-scoped index shape.
CHECKSUM_BOUND_SCHEMA_REPLAY_ASSETS: dict[
    str,
    tuple[
        str,
        str,
        dict[str, tuple[str, bool]],
        dict[str, tuple[str, tuple[str, ...]]],
        tuple[str, ...],
    ],
] = {
    "0089_task_runtime_dag_scheduling.sql": (
        "b322b4d98b0fa05eb0774a91530818c88d17dbd5168f821dacecef67ec3d1663",
        "task_runtime_node",
        {"retry_not_before_ms": ("INTEGER", False)},
        {
            "idx_task_runtime_node_retry_due": (
                "task_runtime_node",
                (
                    "graph_id",
                    "graph_revision",
                    "status",
                    "retry_not_before_ms",
                    "node_id",
                ),
            )
        },
        (
            "check(retry_not_before_msisnullorretry_not_before_ms>=0)",
        ),
    )
}

# A draft of 0072 reached one workspace database before the physical asset was
# sealed.  The draft created the final tables and journals but omitted one
# sealing trigger.  Compatibility is permitted only when both the final 0072
# asset and its one-shot 0075 repair are present with these exact checksums.
PLATFORM_REGISTRY_ASSET = "0072_platform_registry_persistence.sql"
PLATFORM_REGISTRY_MIGRATION_ID = 72
PLATFORM_REGISTRY_NAME = "platform_registry_persistence"
PLATFORM_REGISTRY_DRAFT_CHECKSUM = (
    "01fea4a36c2fcbad3f0e1b33ead86e9f0bf7fa0293176c34408fd28020f0fb0f"
)
PLATFORM_REGISTRY_FINAL_CHECKSUM = (
    "21dfa7958d43c9f7543ceefe1d4d4601fef1a366d360509a3fb5502e26b782b5"
)
PLATFORM_REGISTRY_REPAIR_ASSET = "0075_platform_registry_catalog_repair.sql"
PLATFORM_REGISTRY_REPAIR_CHECKSUM = (
    "a2685586a1ff884acf5e035fa05cba42766973f2b4877a48c01aa6441d72987a"
)

# These two already-applied AW-03 assets predate the explicit-transaction
# catalog rule.  Their bytes/checksums are frozen, so the runner supplies the
# missing BEGIN IMMEDIATE/COMMIT boundary only for these exact assets.
CHECKSUM_BOUND_ATOMIC_ENVELOPE_ASSETS: dict[str, str] = {
    "0067_canon_command_spine.sql": (
        "efa1fb7bca9735cbe5fd81c027bbd38a1b63f874b9afaba9487e95ca3b244116"
    ),
    "0071_life_runtime_uow.sql": (
        "55abc3eb3372ca973f3fef2cec41b1660102c0f4da44c68eba7db54c15dc8b16"
    ),
}
EXPLICIT_TRANSACTION_REQUIRED_FROM = 66

_PLATFORM_REGISTRY_REQUIRED_COLUMNS: dict[str, dict[str, str]] = {
    "vts_expression_mapping": {
        "id": "INTEGER",
        "version": "TEXT",
        "emotion_tag": "TEXT",
        "expression_id": "TEXT",
        "created_ts_ms": "INTEGER",
    },
    "vts_mapping_change_journal": {
        "journal_id": "INTEGER",
        "operation_ref": "TEXT",
        "version": "TEXT",
        "prev_version": "TEXT",
        "prev_hash": "TEXT",
        "new_hash": "TEXT",
        "changed_by": "TEXT",
        "request_json": "TEXT",
        "request_digest": "TEXT",
        "result_json": "TEXT",
        "result_digest": "TEXT",
        "created_at_ms": "INTEGER",
    },
    "mcp_tool_registry": {
        "id": "INTEGER",
        "provider_id": "TEXT",
        "tool_id": "TEXT",
        "name": "TEXT",
        "description": "TEXT",
        "access_level": "TEXT",
        "risk_level": "TEXT",
        "reversible": "INTEGER",
        "version": "TEXT",
        "schema_hash": "TEXT",
        "domain_tags": "TEXT",
        "emotion_requirement": "TEXT",
        "updated_ts_ms": "INTEGER",
    },
    "mcp_tool_sync_journal": {
        "journal_id": "INTEGER",
        "operation_ref": "TEXT",
        "provider_id": "TEXT",
        "tool_id": "TEXT",
        "tool_version": "TEXT",
        "change_kind": "TEXT",
        "request_json": "TEXT",
        "request_digest": "TEXT",
        "before_json": "TEXT",
        "after_json": "TEXT",
        "diff_json": "TEXT",
        "result_json": "TEXT",
        "result_digest": "TEXT",
        "created_at_ms": "INTEGER",
    },
}

_PLATFORM_REGISTRY_REQUIRED_INDEXES: dict[str, tuple[str, tuple[str, ...]]] = {
    "idx_vts_mapping_version": (
        "vts_expression_mapping",
        ("version", "emotion_tag"),
    ),
    "idx_vts_mapping_journal_created": (
        "vts_mapping_change_journal",
        ("created_at_ms", "journal_id"),
    ),
    "idx_mcp_registry_provider": (
        "mcp_tool_registry",
        ("provider_id", "tool_id"),
    ),
    "idx_mcp_registry_tool": (
        "mcp_tool_registry",
        ("tool_id", "provider_id"),
    ),
    "idx_mcp_tool_sync_provider": (
        "mcp_tool_sync_journal",
        ("provider_id", "tool_id", "journal_id"),
    ),
    "idx_mcp_tool_sync_created": (
        "mcp_tool_sync_journal",
        ("created_at_ms", "journal_id"),
    ),
}

PLATFORM_REGISTRY_REPAIR_TRIGGER = (
    "trg_vts_expression_mapping_no_insert_after_journal"
)
_PLATFORM_REGISTRY_REQUIRED_TRIGGERS: dict[str, tuple[str, str, str]] = {
    PLATFORM_REGISTRY_REPAIR_TRIGGER: (
        "vts_expression_mapping",
        "before insert",
        "vts_expression_mapping_version_sealed",
    ),
    "trg_vts_expression_mapping_no_update": (
        "vts_expression_mapping",
        "before update",
        "vts_expression_mapping_immutable",
    ),
    "trg_vts_expression_mapping_no_delete": (
        "vts_expression_mapping",
        "before delete",
        "vts_expression_mapping_immutable",
    ),
    "trg_vts_mapping_journal_no_update": (
        "vts_mapping_change_journal",
        "before update",
        "vts_mapping_change_journal_immutable",
    ),
    "trg_vts_mapping_journal_no_delete": (
        "vts_mapping_change_journal",
        "before delete",
        "vts_mapping_change_journal_immutable",
    ),
    "trg_mcp_tool_sync_journal_no_update": (
        "mcp_tool_sync_journal",
        "before update",
        "mcp_tool_sync_journal_immutable",
    ),
    "trg_mcp_tool_sync_journal_no_delete": (
        "mcp_tool_sync_journal",
        "before delete",
        "mcp_tool_sync_journal_immutable",
    ),
}


@dataclass(frozen=True)
class CatalogCompatibility:
    """Validated exceptions that may be used for one catalog discovery."""

    accepted_duplicate_versions: frozenset[int] = frozenset()
    accepted_gap_versions: frozenset[int] = frozenset()
    issues: tuple[str, ...] = ()


def asset_filename(asset: Any) -> str:
    path = getattr(asset, "path", None)
    if path is not None:
        return str(getattr(path, "name", path))
    migration_id = int(getattr(asset, "migration_id"))
    name = str(getattr(asset, "name"))
    return f"{migration_id:04d}_{name}.sql"


def physical_asset_key(asset: Any) -> str:
    """Return the stable physical key used by the application ledger."""

    return f"{int(getattr(asset, 'migration_id')):04d}#{str(getattr(asset, 'name'))}"


def is_legacy_duplicate_primary(asset: Any) -> bool:
    return asset_filename(asset) == LEGACY_DUPLICATE_PRIMARY


def is_exact_platform_registry_draft_record(
    record: Any,
    asset: Any,
    *,
    catalog_assets: Iterable[Any],
    repair_applied: bool,
) -> bool:
    """Admit only the frozen 0072 draft until the exact 0075 repair runs."""

    if repair_applied:
        return False
    filenames = {
        asset_filename(candidate): str(getattr(candidate, "checksum"))
        for candidate in catalog_assets
    }
    return bool(
        asset_filename(asset) == PLATFORM_REGISTRY_ASSET
        and int(getattr(asset, "migration_id")) == PLATFORM_REGISTRY_MIGRATION_ID
        and str(getattr(asset, "name")) == PLATFORM_REGISTRY_NAME
        and str(getattr(asset, "checksum")) == PLATFORM_REGISTRY_FINAL_CHECKSUM
        and filenames.get(PLATFORM_REGISTRY_REPAIR_ASSET)
        == PLATFORM_REGISTRY_REPAIR_CHECKSUM
        and str(record["name"]) == PLATFORM_REGISTRY_NAME
        and str(record["checksum"]) == PLATFORM_REGISTRY_DRAFT_CHECKSUM
    )


def is_exact_platform_registry_repair_asset(asset: Any) -> bool:
    return bool(
        asset_filename(asset) == PLATFORM_REGISTRY_REPAIR_ASSET
        and int(getattr(asset, "migration_id")) == 75
        and str(getattr(asset, "name")) == "platform_registry_catalog_repair"
        and str(getattr(asset, "checksum")) == PLATFORM_REGISTRY_REPAIR_CHECKSUM
    )


def uses_checksum_bound_atomic_envelope(asset: Any) -> bool:
    """Return whether a frozen pre-policy asset receives a runner envelope."""

    expected = CHECKSUM_BOUND_ATOMIC_ENVELOPE_ASSETS.get(asset_filename(asset))
    return expected is not None and str(getattr(asset, "checksum")) == expected


def transaction_policy_issues(assets: Iterable[Any]) -> list[str]:
    """Enforce one shared transaction policy for guard and runner."""

    issues: list[str] = []
    begin_pattern = re.compile(r"\bBEGIN\s+IMMEDIATE\s*;", re.IGNORECASE)
    commit_pattern = re.compile(r"\bCOMMIT\s*;", re.IGNORECASE)
    for asset in assets:
        if int(getattr(asset, "migration_id")) < EXPLICIT_TRANSACTION_REQUIRED_FROM:
            continue
        if uses_checksum_bound_atomic_envelope(asset):
            continue
        sql = getattr(asset, "path").read_text(encoding="utf-8-sig")
        without_comments = re.sub(
            r"/\*.*?\*/|--[^\r\n]*",
            "",
            sql,
            flags=re.DOTALL,
        )
        if not begin_pattern.search(without_comments) or not commit_pattern.search(
            without_comments
        ):
            issues.append(
                f"{asset_filename(asset)}: migrations >= "
                f"{EXPLICIT_TRANSACTION_REQUIRED_FROM:04d} require explicit "
                "BEGIN IMMEDIATE/COMMIT or a checksum-bound runner envelope"
            )
    return issues


def platform_registry_schema_postcondition_holds(
    conn: Any,
    *,
    allow_missing_repair_trigger: bool,
) -> bool:
    """Verify the complete checksum-bound 0072 physical schema.

    The only tolerated draft difference is absence of the known sealing
    trigger that 0075 creates.  A same-name but malformed trigger is never
    treated as missing.
    """

    try:
        for table_name, expected_columns in _PLATFORM_REGISTRY_REQUIRED_COLUMNS.items():
            rows = conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
            actual_columns = {
                str(row[1]): str(row[2] or "").upper()
                for row in rows
            }
            if any(
                actual_columns.get(column_name) != column_type
                for column_name, column_type in expected_columns.items()
            ):
                return False

        for index_name, (table_name, expected_columns) in (
            _PLATFORM_REGISTRY_REQUIRED_INDEXES.items()
        ):
            index = conn.execute(
                """
                SELECT tbl_name FROM sqlite_master
                WHERE type = 'index' AND name = ?
                """,
                (index_name,),
            ).fetchone()
            if index is None or str(index[0]) != table_name:
                return False
            columns = tuple(
                str(row[2])
                for row in conn.execute(
                    f'PRAGMA index_info("{index_name}")'
                ).fetchall()
            )
            if columns != expected_columns:
                return False

        for trigger_name, (table_name, event_fragment, raise_fragment) in (
            _PLATFORM_REGISTRY_REQUIRED_TRIGGERS.items()
        ):
            trigger = conn.execute(
                """
                SELECT tbl_name, sql FROM sqlite_master
                WHERE type = 'trigger' AND name = ?
                """,
                (trigger_name,),
            ).fetchone()
            if trigger is None:
                if (
                    allow_missing_repair_trigger
                    and trigger_name == PLATFORM_REGISTRY_REPAIR_TRIGGER
                ):
                    continue
                return False
            sql = " ".join(str(trigger[1] or "").lower().split())
            if (
                str(trigger[0]) != table_name
                or event_fragment not in sql
                or raise_fragment not in sql
            ):
                return False
    except (IndexError, sqlite3.DatabaseError):
        return False
    return True


def evaluate_catalog_compatibility(assets: Iterable[Any]) -> CatalogCompatibility:
    """Validate the frozen duplicate/gap against exact names and checksums.

    Compatibility is deliberately catalog-shaped.  Small fixture catalogs keep
    the ordinary strict behavior; a catalog that crosses versions 22 -> 39 is
    considered the historical central catalog and must match both boundary
    anchors as well as both physical version-0009 assets.
    """

    rows = list(assets)
    filenames = {asset_filename(asset): str(getattr(asset, "checksum")) for asset in rows}
    by_version: dict[int, list[Any]] = {}
    for asset in rows:
        by_version.setdefault(int(getattr(asset, "migration_id")), []).append(asset)

    issues: list[str] = []
    accepted_duplicates: set[int] = set()
    accepted_gaps: set[int] = set()

    version_nine = by_version.get(LEGACY_DUPLICATE_VERSION, [])
    if len(version_nine) > 1:
        actual_names = {asset_filename(asset) for asset in version_nine}
        expected_names = set(LEGACY_DUPLICATE_ASSETS)
        if actual_names != expected_names:
            issues.append(
                "version 0009 physical assets do not match the frozen compatibility set"
            )
        else:
            drifted = [
                name
                for name, expected in LEGACY_DUPLICATE_ASSETS.items()
                if filenames.get(name) != expected
            ]
            if drifted:
                issues.append(
                    f"version 0009 compatibility checksum drift: {sorted(drifted)}"
                )
            else:
                accepted_duplicates.add(LEGACY_DUPLICATE_VERSION)

    versions = set(by_version)
    crosses_legacy_gap = 22 in versions and any(version >= 39 for version in versions)
    if crosses_legacy_gap:
        populated = sorted(versions.intersection(LEGACY_GAP_VERSIONS))
        if populated:
            issues.append(
                f"frozen compatibility gap 0023-0038 was populated: {populated}"
            )

        anchor_drift = [
            name
            for name, expected in LEGACY_GAP_ANCHORS.items()
            if filenames.get(name) != expected
        ]
        if anchor_drift:
            issues.append(
                f"legacy gap compatibility anchor drift: {sorted(anchor_drift)}"
            )

        actual_nine_names = {asset_filename(asset) for asset in version_nine}
        if actual_nine_names != set(LEGACY_DUPLICATE_ASSETS):
            issues.append(
                "central legacy catalog must retain both frozen version 0009 assets"
            )

        if not populated and not anchor_drift:
            accepted_gaps.update(LEGACY_GAP_VERSIONS)

    return CatalogCompatibility(
        accepted_duplicate_versions=frozenset(accepted_duplicates),
        accepted_gap_versions=frozenset(accepted_gaps),
        issues=tuple(dict.fromkeys(issues)),
    )


def is_exact_legacy_fts_failure(asset: Any, exc: BaseException) -> bool:
    """Return True only for the immutable 0021 FTS5 index failure."""

    return (
        asset_filename(asset) == LEGACY_FTS_ASSET
        and str(getattr(asset, "checksum")) == LEGACY_FTS_CHECKSUM
        and str(exc) == LEGACY_FTS_ERROR
    )


def legacy_fts_postcondition_holds(conn: Any) -> bool:
    """Verify that the useful FTS5 table exists despite the invalid index."""

    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'episodic_fts'"
    ).fetchone()
    if row is None or row[0] is None or "using fts5" not in str(row[0]).lower():
        return False
    try:
        conn.execute("SELECT rowid, memory_id, content FROM episodic_fts LIMIT 0").fetchall()
    except Exception:
        return False
    return True


def is_exact_legacy_inline_schema_failure(asset: Any, exc: BaseException) -> bool:
    """Recognize only checksum-bound duplicate columns from old inline DDL."""

    frozen = LEGACY_INLINE_SCHEMA_ASSETS.get(asset_filename(asset))
    return bool(
        frozen is not None
        and str(getattr(asset, "checksum")) == frozen[0]
        and str(exc).lower().startswith("duplicate column name:")
    )


def is_exact_checksum_bound_schema_replay_failure(
    asset: Any,
    exc: BaseException,
) -> bool:
    """Recognize only a receipt-loss replay of a frozen non-idempotent asset."""

    frozen = CHECKSUM_BOUND_SCHEMA_REPLAY_ASSETS.get(asset_filename(asset))
    return bool(
        frozen is not None
        and str(getattr(asset, "checksum")) == frozen[0]
        and str(exc).lower().startswith("duplicate column name:")
    )


def checksum_bound_schema_replay_postcondition_holds(conn: Any, asset: Any) -> bool:
    """Prove that a non-idempotent migration committed before receipt loss."""

    frozen = CHECKSUM_BOUND_SCHEMA_REPLAY_ASSETS.get(asset_filename(asset))
    if frozen is None:
        return False
    _checksum, table, required_columns, required_indexes, sql_fragments = frozen
    try:
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
