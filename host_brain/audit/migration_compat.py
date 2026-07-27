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

# 0089, 0093, 0095 and 0131 contain ALTER TABLE statements followed by
# indexes/triggers in
# one explicit transaction.  SQL may commit before the runner persists its
# migration receipt; the next startup then sees a duplicate column although
# the complete physical migration exists.  Repair is allowed only for exact
# checksums and only after every column constraint, index and trigger required
# by the corresponding frozen asset has been validated.
CHECKSUM_BOUND_SCHEMA_REPLAY_ASSETS: dict[
    str,
    tuple[
        str,
        str,
        dict[str, tuple[str, bool]],
        dict[str, tuple[str, tuple[str, ...]]],
        tuple[str, ...],
        dict[str, tuple[str, tuple[str, ...]]],
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
        {},
    ),
    "0093_task_runtime_outbox_claim.sql": (
        "5ac7145decc5bcc9cea9bb8378d9cdf93c0196adcb7b8c3dccecf8cff77b16be",
        "task_runtime_outbox",
        {
            "claim_owner": ("TEXT", False),
            "claim_token": ("TEXT", False),
            "claim_expires_ts_ms": ("INTEGER", False),
            "claim_fencing_token": ("INTEGER", True),
            "attempt_count": ("INTEGER", True),
            "last_error": ("TEXT", False),
        },
        {
            "idx_task_runtime_outbox_delivery_ready": (
                "task_runtime_outbox",
                (
                    "status",
                    "available_ts_ms",
                    "claim_expires_ts_ms",
                    "created_ts_ms",
                    "outbox_id",
                ),
            ),
            "idx_task_runtime_outbox_aggregate_delivery_order": (
                "task_runtime_outbox",
                (
                    "aggregate_type",
                    "aggregate_id",
                    "status",
                    "aggregate_version",
                    "created_ts_ms",
                    "outbox_id",
                ),
            ),
        },
        (
            "check(claim_ownerisnullortypeof(claim_owner)='text')",
            "check(claim_tokenisnullortypeof(claim_token)='text')",
            "check(claim_expires_ts_msisnullor(typeof(claim_expires_ts_ms)='integer'andclaim_expires_ts_ms>=0))",
            "check(typeof(claim_fencing_token)='integer'andclaim_fencing_token>=0)",
            "check(typeof(attempt_count)='integer'andattempt_count>=0andattempt_count=claim_fencing_token)",
            "check(last_errorisnullor(typeof(last_error)='text'andlength(trim(last_error))>0andlength(last_error)<=2000))",
        ),
        {
            "trg_task_runtime_outbox_claim_insert_guard": (
                "task_runtime_outbox",
                (
                    "beforeinsertontask_runtime_outbox",
                    "new.claim_ownerisnotnull",
                    "new.claim_tokenisnotnull",
                    "new.claim_expires_ts_msisnotnull",
                    "typeof(new.claim_owner)='text'",
                    "typeof(new.claim_token)='text'",
                    "typeof(new.claim_expires_ts_ms)='integer'",
                    "new.claim_expires_ts_ms>=0",
                    "typeof(new.claim_fencing_token)='integer'",
                    "typeof(new.attempt_count)='integer'",
                    "new.claim_fencing_token>0",
                    "new.attempt_count>0",
                    "new.claim_fencing_token=new.attempt_count",
                    "raise(abort,'task_runtime_outboxclaimstateisinvalid')",
                ),
            ),
            "trg_task_runtime_outbox_claim_update_guard": (
                "task_runtime_outbox",
                (
                    "beforeupdateofstatus,claim_owner,claim_token,claim_expires_ts_ms,claim_fencing_token,attempt_countontask_runtime_outbox",
                    "new.claim_ownerisnotnull",
                    "new.claim_tokenisnotnull",
                    "new.claim_expires_ts_msisnotnull",
                    "typeof(new.claim_owner)='text'",
                    "typeof(new.claim_token)='text'",
                    "typeof(new.claim_expires_ts_ms)='integer'",
                    "new.claim_expires_ts_ms>=0",
                    "typeof(new.claim_fencing_token)='integer'",
                    "typeof(new.attempt_count)='integer'",
                    "new.claim_fencing_token>0",
                    "new.attempt_count>0",
                    "new.claim_fencing_token=new.attempt_count",
                    "raise(abort,'task_runtime_outboxclaimstateisinvalid')",
                ),
            ),
        },
    ),
    "0095_task_runtime_revisioned_graph_outbox.sql": (
        "db39c2af0c497322c18d767b74139b5cba635c33e947cba5191f440aad534fa1",
        "task_runtime_outbox",
        {
            "graph_revision": ("INTEGER", False),
        },
        {
            "idx_task_runtime_outbox_revision_delivery_order": (
                "task_runtime_outbox",
                (
                    "aggregate_type",
                    "aggregate_id",
                    "graph_revision",
                    "status",
                    "aggregate_version",
                    "created_ts_ms",
                    "outbox_id",
                ),
            ),
            "uq_runtime_event_task_outbox_source": (
                "runtime_event_timeline",
                ("source_ref",),
            ),
        },
        (
            "graph_revisioninteger",
            "check(graph_revisionisnullor(typeof(graph_revision)='integer'andgraph_revision>=1))",
        ),
        {
            "trg_task_runtime_outbox_graph_revision_insert_guard": (
                "task_runtime_outbox",
                (
                    "beforeinsertontask_runtime_outbox",
                    "new.aggregate_type='task_graph'",
                    "new.graph_revisionisnotnull",
                    "typeof(new.graph_revision)='integer'",
                    "new.graph_revision>=1",
                    "new.payload_ref=('taskstore://event/task_graph/'||new.aggregate_id||'@'||new.graph_revision||'/'||new.aggregate_version)",
                    "graph.graph_id=new.aggregate_id",
                    "graph.revision=new.graph_revision",
                    "new.aggregate_type<>'task_graph'",
                    "new.graph_revisionisnull",
                    "raise(abort,'task_runtime_outboxgraphrevisionisinvalid')",
                ),
            ),
            "trg_task_runtime_outbox_graph_revision_update_guard": (
                "task_runtime_outbox",
                (
                    "beforeupdateofaggregate_type,aggregate_id,graph_revision,aggregate_version,payload_refontask_runtime_outbox",
                    "new.aggregate_type='task_graph'",
                    "new.graph_revisionisnotnull",
                    "typeof(new.graph_revision)='integer'",
                    "new.graph_revision>=1",
                    "new.payload_ref=('taskstore://event/task_graph/'||new.aggregate_id||'@'||new.graph_revision||'/'||new.aggregate_version)",
                    "graph.graph_id=new.aggregate_id",
                    "graph.revision=new.graph_revision",
                    "new.aggregate_type<>'task_graph'",
                    "new.graph_revisionisnull",
                    "raise(abort,'task_runtime_outboxgraphrevisionisinvalid')",
                ),
            ),
        },
    ),
    "0131_task_operation_recovery_hold_binding.sql": (
        "379a5a7ae11c8962f95f9de1994b3cf602bec7bdf2277884761ffbc0b1407891",
        "task_runtime_operation_reconciliation_evidence",
        {
            "recovery_case_ref": ("TEXT", False),
            "recovery_case_subject_sha256": ("TEXT", False),
        },
        {
            "idx_task_operation_reconciliation_evidence_recovery_case": (
                "task_runtime_operation_reconciliation_evidence",
                ("recovery_case_ref",),
            ),
        },
        (
            "recovery_case_reftext",
            "check(recovery_case_refisnullor(length(recovery_case_ref)=97",
            "recovery_case_subject_sha256text",
            "check(recovery_case_subject_sha256isnullor(length(recovery_case_subject_sha256)=71",
        ),
        {
            "trg_task_operation_reconciliation_evidence_recovery_case": (
                "task_runtime_operation_reconciliation_evidence",
                (
                    "beforeinsertontask_runtime_operation_reconciliation_evidence",
                    "new.recovery_case_refisnull",
                    "new.recovery_case_subject_sha256isnotnull",
                    "hold.case_ref=new.recovery_case_ref",
                    "hold.subject_sha256=new.recovery_case_subject_sha256",
                    "hold.reservation_id=new.reservation_id",
                    "hold.operation_status_version=new.expected_version",
                    "operation.status_version=hold.operation_status_version",
                    "'signed_operation_reconciliation_required'",
                    "'signed_cancellation_reconciliation_required'",
                    "new.actionin('commit','release','reacquire')",
                    "new.actionin('commit','release')",
                    "raise(abort,'task_operation_reconciliation_recovery_case_invalid')",
                ),
            ),
        },
    ),
}

# 0104 is intentionally handled outside the generic ALTER/index/trigger
# descriptor above.  In addition to the non-idempotent outbox ALTER it
# creates two durable authority tables.  A duplicate ``runtime_scope_id`` is
# therefore evidence of a lost receipt only when the complete authority
# fence, including every constraint-bearing table, is already physical.
HOST_EMBODIMENT_RUNTIME_AUTHORITY_REPLAY_ASSET = (
    "0104_host_embodiment_runtime_authority.sql"
)
HOST_EMBODIMENT_RUNTIME_AUTHORITY_REPLAY_CHECKSUM = (
    "bc8158311e5db0f59e07930250f6334d345831dab34e52103412aab1af989cc5"
)

_HOST_EMBODIMENT_RUNTIME_AUTHORITY_OUTBOX_COLUMN = (
    "runtime_scope_id",
    "TEXT",
    False,
    None,
    0,
)

_HOST_EMBODIMENT_RUNTIME_AUTHORITY_TABLE_COLUMNS: dict[
    str,
    tuple[tuple[str, str, bool, str | None, int], ...],
] = {
    "host_embodiment_runtime_lease": (
        ("runtime_scope_id", "TEXT", True, None, 1),
        ("owner_id", "TEXT", True, None, 0),
        ("lease_id", "TEXT", True, None, 0),
        ("fencing_token", "INTEGER", True, None, 0),
        ("issued_at_ms", "INTEGER", True, None, 0),
        ("expires_at_ms", "INTEGER", True, None, 0),
        ("released_at_ms", "INTEGER", False, None, 0),
    ),
    "host_embodiment_edge_session_lease": (
        ("runtime_scope_id", "TEXT", True, None, 1),
        ("session_id", "TEXT", True, None, 0),
        ("owner_id", "TEXT", True, None, 0),
        ("lease_id", "TEXT", True, None, 0),
        ("fencing_token", "INTEGER", True, None, 0),
        ("issued_at_ms", "INTEGER", True, None, 0),
        ("expires_at_ms", "INTEGER", True, None, 0),
        ("runtime_lease_id", "TEXT", True, None, 0),
        ("runtime_fencing_token", "INTEGER", True, None, 0),
        ("updated_at_ms", "INTEGER", True, None, 0),
        ("released_at_ms", "INTEGER", False, None, 0),
    ),
}

_HOST_EMBODIMENT_RUNTIME_AUTHORITY_TABLE_SQL = {
    "host_embodiment_runtime_lease": """
        CREATE TABLE host_embodiment_runtime_lease (
            runtime_scope_id TEXT NOT NULL PRIMARY KEY
                CHECK(length(trim(runtime_scope_id)) BETWEEN 1 AND 512),
            owner_id TEXT NOT NULL
                CHECK(length(trim(owner_id)) BETWEEN 1 AND 512),
            lease_id TEXT NOT NULL UNIQUE
                CHECK(length(trim(lease_id)) BETWEEN 1 AND 512),
            fencing_token INTEGER NOT NULL
                CHECK(typeof(fencing_token) = 'integer' AND fencing_token >= 1),
            issued_at_ms INTEGER NOT NULL
                CHECK(typeof(issued_at_ms) = 'integer' AND issued_at_ms >= 0),
            expires_at_ms INTEGER NOT NULL
                CHECK(typeof(expires_at_ms) = 'integer' AND expires_at_ms > issued_at_ms),
            released_at_ms INTEGER
                CHECK(released_at_ms IS NULL OR (
                    typeof(released_at_ms) = 'integer' AND released_at_ms >= issued_at_ms
                ))
        )
    """,
    "host_embodiment_edge_session_lease": """
        CREATE TABLE host_embodiment_edge_session_lease (
            runtime_scope_id TEXT NOT NULL PRIMARY KEY
                REFERENCES host_embodiment_runtime_lease(runtime_scope_id) ON DELETE RESTRICT,
            session_id TEXT NOT NULL
                CHECK(length(trim(session_id)) BETWEEN 1 AND 512),
            owner_id TEXT NOT NULL
                CHECK(length(trim(owner_id)) BETWEEN 1 AND 512),
            lease_id TEXT NOT NULL
                CHECK(length(trim(lease_id)) BETWEEN 1 AND 512),
            fencing_token INTEGER NOT NULL
                CHECK(typeof(fencing_token) = 'integer' AND fencing_token >= 1),
            issued_at_ms INTEGER NOT NULL
                CHECK(typeof(issued_at_ms) = 'integer' AND issued_at_ms >= 0),
            expires_at_ms INTEGER NOT NULL
                CHECK(typeof(expires_at_ms) = 'integer' AND expires_at_ms > issued_at_ms),
            runtime_lease_id TEXT NOT NULL
                CHECK(length(trim(runtime_lease_id)) BETWEEN 1 AND 512),
            runtime_fencing_token INTEGER NOT NULL
                CHECK(typeof(runtime_fencing_token) = 'integer' AND runtime_fencing_token >= 1),
            updated_at_ms INTEGER NOT NULL
                CHECK(typeof(updated_at_ms) = 'integer' AND updated_at_ms >= 0),
            released_at_ms INTEGER
                CHECK(released_at_ms IS NULL OR (
                    typeof(released_at_ms) = 'integer' AND released_at_ms >= issued_at_ms
                )),
            UNIQUE(runtime_scope_id, session_id, owner_id, lease_id, fencing_token)
        )
    """,
}

_HOST_EMBODIMENT_RUNTIME_AUTHORITY_INDEXES: dict[
    str,
    tuple[str, tuple[str, ...], str],
] = {
    "idx_host_embodiment_outbox_scope_ready": (
        "host_embodiment_outbox",
        ("runtime_scope_id", "status", "created_at_ms", "outbox_id"),
        """
        CREATE INDEX idx_host_embodiment_outbox_scope_ready
            ON host_embodiment_outbox(runtime_scope_id, status, created_at_ms, outbox_id)
        """,
    ),
    "idx_host_embodiment_runtime_lease_active": (
        "host_embodiment_runtime_lease",
        ("expires_at_ms", "released_at_ms"),
        """
        CREATE INDEX idx_host_embodiment_runtime_lease_active
            ON host_embodiment_runtime_lease(expires_at_ms, released_at_ms)
        """,
    ),
    "idx_host_embodiment_edge_session_lookup": (
        "host_embodiment_edge_session_lease",
        ("runtime_scope_id", "session_id", "owner_id", "released_at_ms"),
        """
        CREATE INDEX idx_host_embodiment_edge_session_lookup
            ON host_embodiment_edge_session_lease(
                runtime_scope_id, session_id, owner_id, released_at_ms
            )
        """,
    ),
}

_HOST_EMBODIMENT_RUNTIME_AUTHORITY_TRIGGERS: dict[str, tuple[str, str]] = {
    "host_embodiment_outbox_scope_no_update": (
        "host_embodiment_outbox",
        """
        CREATE TRIGGER host_embodiment_outbox_scope_no_update
        BEFORE UPDATE OF runtime_scope_id
        ON host_embodiment_outbox
        BEGIN
            SELECT RAISE(ABORT, 'host embodiment outbox runtime scope is immutable');
        END
        """,
    ),
    "host_embodiment_runtime_lease_no_delete": (
        "host_embodiment_runtime_lease",
        """
        CREATE TRIGGER host_embodiment_runtime_lease_no_delete
        BEFORE DELETE ON host_embodiment_runtime_lease
        BEGIN
            SELECT RAISE(ABORT, 'host embodiment runtime lease is append-only');
        END
        """,
    ),
    "host_embodiment_edge_session_lease_no_delete": (
        "host_embodiment_edge_session_lease",
        """
        CREATE TRIGGER host_embodiment_edge_session_lease_no_delete
        BEFORE DELETE ON host_embodiment_edge_session_lease
        BEGIN
            SELECT RAISE(ABORT, 'host embodiment edge session lease is append-only');
        END
        """,
    ),
}

_HOST_EMBODIMENT_RUNTIME_AUTHORITY_FOREIGN_KEYS = {
    "host_embodiment_runtime_lease": (),
    "host_embodiment_edge_session_lease": (
        (
            "host_embodiment_runtime_lease",
            "runtime_scope_id",
            "runtime_scope_id",
            "NO ACTION",
            "RESTRICT",
            "NONE",
        ),
    ),
}

_HOST_EMBODIMENT_RUNTIME_AUTHORITY_UNIQUE_INDEXES = {
    "host_embodiment_runtime_lease": (("lease_id",),),
    "host_embodiment_edge_session_lease": (
        ("runtime_scope_id", "session_id", "owner_id", "lease_id", "fencing_token"),
    ),
}

# Migration 0098 deliberately rebuilds the two 0095 revision guards so the
# Effect reconciliation Outbox binds the same graph revision as its immutable
# case.  Receipt-loss replay of 0095 must therefore recognize exactly one of
# two physical epochs: the original 0095 TaskGraph-only guards before 0098, or
# these stricter guards when the 0098 case table is present.
_0098_EFFECT_AWARE_REVISION_TRIGGERS = {
    "trg_task_runtime_outbox_graph_revision_insert_guard": (
        "task_runtime_outbox",
        (
            "beforeinsertontask_runtime_outbox",
            "new.aggregate_type='task_graph'",
            "new.aggregate_type='graph_cancellation_effect_reconciliation'",
            "new.graph_revisionisnotnull",
            "typeof(new.graph_revision)='integer'",
            "new.graph_revision>=1",
            "new.payload_ref=('taskstore://event/task_graph/'||new.aggregate_id||'@'||new.graph_revision||'/'||new.aggregate_version)",
            "new.payload_ref=('taskstore://event/graph_cancellation_effect_reconciliation/'||new.aggregate_id||'@'||new.graph_revision||'/'||new.aggregate_version)",
            "item.case_id=new.aggregate_id",
            "item.graph_revision=new.graph_revision",
            "new.aggregate_typenotin('task_graph','graph_cancellation_effect_reconciliation')",
            "new.graph_revisionisnull",
            "raise(abort,'task_runtime_outboxgraphrevisionisinvalid')",
        ),
    ),
    "trg_task_runtime_outbox_graph_revision_update_guard": (
        "task_runtime_outbox",
        (
            "beforeupdateofaggregate_type,aggregate_id,graph_revision,aggregate_version,payload_refontask_runtime_outbox",
            "new.aggregate_type='task_graph'",
            "new.aggregate_type='graph_cancellation_effect_reconciliation'",
            "new.graph_revisionisnotnull",
            "typeof(new.graph_revision)='integer'",
            "new.graph_revision>=1",
            "new.payload_ref=('taskstore://event/task_graph/'||new.aggregate_id||'@'||new.graph_revision||'/'||new.aggregate_version)",
            "new.payload_ref=('taskstore://event/graph_cancellation_effect_reconciliation/'||new.aggregate_id||'@'||new.graph_revision||'/'||new.aggregate_version)",
            "item.case_id=new.aggregate_id",
            "item.graph_revision=new.graph_revision",
            "new.aggregate_typenotin('task_graph','graph_cancellation_effect_reconciliation')",
            "new.graph_revisionisnull",
            "raise(abort,'task_runtime_outboxgraphrevisionisinvalid')",
        ),
    ),
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


def _normalized_schema_sql(sql: Any) -> str:
    return re.sub(r"\s+", "", str(sql).lower()).rstrip(";")


def _is_exact_host_embodiment_runtime_authority_asset(asset: Any) -> bool:
    """Keep the D8 receipt-repair exception bound to its one frozen asset."""

    try:
        return bool(
            asset_filename(asset) == HOST_EMBODIMENT_RUNTIME_AUTHORITY_REPLAY_ASSET
            and int(getattr(asset, "migration_id")) == 104
            and str(getattr(asset, "name"))
            == "host_embodiment_runtime_authority"
            and str(getattr(asset, "checksum"))
            == HOST_EMBODIMENT_RUNTIME_AUTHORITY_REPLAY_CHECKSUM
        )
    except (AttributeError, TypeError, ValueError):
        return False


def _sqlite_table_columns(
    conn: Any,
    table: str,
) -> tuple[tuple[str, str, bool, str | None, int], ...]:
    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    return tuple(
        (
            str(row[1]),
            str(row[2] or "").strip().upper(),
            bool(row[3]),
            None if row[4] is None else str(row[4]),
            int(row[5]),
        )
        for row in rows
    )


def _sqlite_index_columns(conn: Any, index_name: str) -> tuple[str, ...]:
    return tuple(
        str(row[2])
        for row in conn.execute(f'PRAGMA index_info("{index_name}")').fetchall()
    )


def _host_embodiment_runtime_authority_postcondition_holds(
    conn: Any,
    asset: Any,
) -> bool:
    """Prove all 0104 physical authority objects survived receipt loss.

    Do not infer a successful migration from the duplicate ALTER alone.  The
    runner is allowed to replace its receipt only after the immutable scope
    field, named indexes/triggers, both authority tables, their constraints,
    unique keys and foreign-key edge are all exactly the 0104 shape.
    """

    if not _is_exact_host_embodiment_runtime_authority_asset(asset):
        return False
    try:
        outbox_columns = _sqlite_table_columns(conn, "host_embodiment_outbox")
        scope_columns = [
            column
            for column in outbox_columns
            if column[0] == _HOST_EMBODIMENT_RUNTIME_AUTHORITY_OUTBOX_COLUMN[0]
        ]
        if (
            len(scope_columns) != 1
            or scope_columns[0] != _HOST_EMBODIMENT_RUNTIME_AUTHORITY_OUTBOX_COLUMN
            or outbox_columns.index(scope_columns[0]) != len(outbox_columns) - 1
        ):
            return False

        for table, expected_columns in (
            _HOST_EMBODIMENT_RUNTIME_AUTHORITY_TABLE_COLUMNS.items()
        ):
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
            if (
                row is None
                or row[0] is None
                or _normalized_schema_sql(row[0])
                != _normalized_schema_sql(
                    _HOST_EMBODIMENT_RUNTIME_AUTHORITY_TABLE_SQL[table]
                )
                or _sqlite_table_columns(conn, table) != expected_columns
            ):
                return False

            expected_foreign_keys = _HOST_EMBODIMENT_RUNTIME_AUTHORITY_FOREIGN_KEYS[
                table
            ]
            actual_foreign_keys = tuple(
                (
                    str(row[2]),
                    str(row[3]),
                    str(row[4]),
                    str(row[5]).upper(),
                    str(row[6]).upper(),
                    str(row[7]).upper(),
                )
                for row in conn.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()
            )
            if actual_foreign_keys != expected_foreign_keys:
                return False

            unique_indexes: list[tuple[str, ...]] = []
            for index_row in conn.execute(f'PRAGMA index_list("{table}")').fetchall():
                if int(index_row[2]) == 1 and str(index_row[3]) == "u":
                    unique_indexes.append(
                        _sqlite_index_columns(conn, str(index_row[1]))
                    )
            if tuple(sorted(unique_indexes)) != tuple(
                sorted(_HOST_EMBODIMENT_RUNTIME_AUTHORITY_UNIQUE_INDEXES[table])
            ):
                return False

        for index_name, (
            expected_table,
            expected_columns,
            expected_sql,
        ) in _HOST_EMBODIMENT_RUNTIME_AUTHORITY_INDEXES.items():
            row = conn.execute(
                "SELECT tbl_name, sql FROM sqlite_master WHERE type = 'index' AND name = ?",
                (index_name,),
            ).fetchone()
            if (
                row is None
                or str(row[0]) != expected_table
                or row[1] is None
                or _normalized_schema_sql(row[1])
                != _normalized_schema_sql(expected_sql)
                or _sqlite_index_columns(conn, index_name) != expected_columns
            ):
                return False
            index_meta = {
                str(item[1]): item
                for item in conn.execute(
                    f'PRAGMA index_list("{expected_table}")'
                ).fetchall()
            }.get(index_name)
            if (
                index_meta is None
                or int(index_meta[2]) != 0
                or str(index_meta[3]) != "c"
                or int(index_meta[4]) != 0
            ):
                return False

        for trigger_name, (expected_table, expected_sql) in (
            _HOST_EMBODIMENT_RUNTIME_AUTHORITY_TRIGGERS.items()
        ):
            row = conn.execute(
                "SELECT tbl_name, sql FROM sqlite_master WHERE type = 'trigger' AND name = ?",
                (trigger_name,),
            ).fetchone()
            if (
                row is None
                or str(row[0]) != expected_table
                or row[1] is None
                or _normalized_schema_sql(row[1])
                != _normalized_schema_sql(expected_sql)
            ):
                return False
    except (IndexError, sqlite3.DatabaseError, TypeError, ValueError):
        return False
    return True


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
    frozen = CHECKSUM_BOUND_SCHEMA_REPLAY_ASSETS.get(asset_filename(asset))
    return bool(
        frozen is not None
        and str(getattr(asset, "checksum")) == frozen[0]
        and str(exc).lower().startswith("duplicate column name:")
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
