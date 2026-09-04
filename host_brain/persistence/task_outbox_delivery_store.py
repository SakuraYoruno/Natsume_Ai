"""Cross-process fenced delivery for the canonical Task Runtime outbox.

This mixin borrows the concrete ``DurableTaskStore`` connection, transaction,
clock, row codec, and CAS helpers.  It intentionally owns no second database or
process-local delivery authority.
"""

from __future__ import annotations

import re
import secrets
import sqlite3
from typing import Any

from host_brain.persistence.task_record_codec import _dumps, _outbox_record
from host_brain.persistence.task_store_types import (
    OutboxMessage,
    REVISIONED_OUTBOX_AGGREGATE_TYPES,
    TASK_STORE_CAS_CONFLICT,
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_INVALID,
    TASK_STORE_LEASE_CONFLICT,
    TASK_STORE_OWNERSHIP_CONFLICT,
    TASK_STORE_OUTBOX_CONFLICT,
    TASK_STORE_SCHEMA_NOT_READY,
    TaskStoreError,
    _coerced_nonnegative_task_int as _nonnegative_int,
    _required_bounded_task_text as _required_text,
    build_outbox_payload_ref,
)


_OUTBOX_DELIVERY_REQUIRED_COLUMNS = frozenset(
    {
        "claim_owner",
        "claim_token",
        "claim_expires_ts_ms",
        "claim_fencing_token",
        "attempt_count",
        "last_error",
        "status_version",
        "graph_revision",
    }
)
_OUTBOX_DELIVERY_REQUIRED_INDEXES = frozenset(
    {
        "idx_task_runtime_outbox_delivery_ready",
        "idx_task_runtime_outbox_revision_delivery_order",
    }
)
_OUTBOX_DELIVERY_INDEX_COLUMNS = {
    "idx_task_runtime_outbox_delivery_ready": (
        "status",
        "available_ts_ms",
        "claim_expires_ts_ms",
        "created_ts_ms",
        "outbox_id",
    ),
    "idx_task_runtime_outbox_revision_delivery_order": (
        "aggregate_type",
        "aggregate_id",
        "graph_revision",
        "status",
        "aggregate_version",
        "created_ts_ms",
        "outbox_id",
    ),
}
_OUTBOX_DELIVERY_REQUIRED_TRIGGERS = frozenset(
    {
        "trg_task_runtime_outbox_claim_insert_guard",
        "trg_task_runtime_outbox_claim_update_guard",
        "trg_task_runtime_outbox_graph_revision_insert_guard",
        "trg_task_runtime_outbox_graph_revision_update_guard",
    }
)
_OUTBOX_DELIVERY_TABLE_SQL_FRAGMENTS = (
    "graph_revisioninteger",
    "check(claim_ownerisnullortypeof(claim_owner)='text')",
    "check(claim_tokenisnullortypeof(claim_token)='text')",
    "check(claim_expires_ts_msisnullor(typeof(claim_expires_ts_ms)='integer'andclaim_expires_ts_ms>=0))",
    "check(typeof(claim_fencing_token)='integer'andclaim_fencing_token>=0)",
    "check(typeof(attempt_count)='integer'andattempt_count>=0andattempt_count=claim_fencing_token)",
    "check(last_errorisnullor(typeof(last_error)='text'andlength(trim(last_error))>0andlength(last_error)<=2000))",
    "check(graph_revisionisnullor(typeof(graph_revision)='integer'andgraph_revision>=1))",
)
_OUTBOX_DELIVERY_LEGACY_TRIGGER_SQL_FRAGMENTS = {
    "trg_task_runtime_outbox_claim_insert_guard": (
        "beforeinsertontask_runtime_outbox",
        "typeof(new.claim_owner)='text'",
        "typeof(new.claim_token)='text'",
        "typeof(new.claim_expires_ts_ms)='integer'",
        "new.claim_expires_ts_ms>=0",
        "typeof(new.claim_fencing_token)='integer'",
        "typeof(new.attempt_count)='integer'",
        "new.claim_fencing_token>0",
        "new.attempt_count>0",
        "new.claim_fencing_token=new.attempt_count",
    ),
    "trg_task_runtime_outbox_claim_update_guard": (
        "beforeupdateofstatus,claim_owner,claim_token,claim_expires_ts_ms,claim_fencing_token,attempt_countontask_runtime_outbox",
        "typeof(new.claim_owner)='text'",
        "typeof(new.claim_token)='text'",
        "typeof(new.claim_expires_ts_ms)='integer'",
        "new.claim_expires_ts_ms>=0",
        "typeof(new.claim_fencing_token)='integer'",
        "typeof(new.attempt_count)='integer'",
        "new.claim_fencing_token>0",
        "new.attempt_count>0",
        "new.claim_fencing_token=new.attempt_count",
    ),
    "trg_task_runtime_outbox_graph_revision_insert_guard": (
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
    "trg_task_runtime_outbox_graph_revision_update_guard": (
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
}
_OUTBOX_DELIVERY_EFFECT_TRIGGER_SQL_FRAGMENTS = {
    "trg_task_runtime_outbox_graph_revision_insert_guard": (
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
    "trg_task_runtime_outbox_graph_revision_update_guard": (
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
}

_OUTBOX_DELIVERY_MODE_UNCHECKED = "UNCHECKED"
_OUTBOX_DELIVERY_MODE_LEGACY = "LEGACY"
_OUTBOX_DELIVERY_MODE_READY = "READY"


def _normalized_sql(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").lower())


class TaskOutboxDeliveryStoreMixin:
    """Claim and settle outbox delivery with durable fencing and ordering."""

    def _insert_outbox(
        self,
        conn: sqlite3.Connection,
        message: OutboxMessage,
        *,
        ts_ms: int,
        expected_aggregate: tuple[str, str, int],
        expected_event_type: str,
        expected_attempt_id: str | None,
        expected_graph_revision: int | None = None,
    ) -> None:
        if not isinstance(message, OutboxMessage):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "every durable state mutation requires an OutboxMessage",
            )
        message.validate()
        if message.available_ts_ms is not None:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "mutation outbox availability is assigned only by the trusted store clock",
            )
        actual_aggregate = (
            message.aggregate_type,
            message.aggregate_id,
            int(message.aggregate_version),
        )
        normalized_expected = (
            str(expected_aggregate[0]),
            str(expected_aggregate[1]),
            int(expected_aggregate[2]),
        )
        if actual_aggregate != normalized_expected:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "outbox aggregate must identify the state version committed in this transaction: "
                f"expected {normalized_expected}, got {actual_aggregate}",
            )
        if message.aggregate_type in REVISIONED_OUTBOX_AGGREGATE_TYPES:
            if (
                type(expected_graph_revision) is not int
                or expected_graph_revision < 1
                or message.graph_revision != expected_graph_revision
            ):
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "revisioned outbox must bind the exact graph revision",
                )
        elif expected_graph_revision is not None:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "non-revisioned mutation cannot declare expected_graph_revision",
            )
        normalized_event_type = str(expected_event_type or "").strip()
        if not normalized_event_type or message.event_type != normalized_event_type:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "outbox event_type does not describe the committed mutation: "
                f"expected {normalized_event_type}, got {message.event_type}",
            )
        normalized_attempt_id = str(expected_attempt_id or "").strip() or None
        if message.attempt_id != normalized_attempt_id:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "outbox attempt_id does not match the committed mutation: "
                f"expected {normalized_attempt_id}, got {message.attempt_id}",
            )
        physical_payload_ref = message.payload_ref
        if (
            message.aggregate_type == "TASK_GRAPH"
            and not getattr(self, "_outbox_graph_revision_enabled", False)
        ):
            # A verified pre-0095 schema keeps its legacy physical payload
            # identity.  The caller still validates the revisioned contract;
            # migration 0095 later backfills the column under exact authority.
            physical_payload_ref = build_outbox_payload_ref(
                aggregate_type=message.aggregate_type,
                aggregate_id=message.aggregate_id,
                aggregate_version=message.aggregate_version,
            )
        try:
            if getattr(self, "_outbox_graph_revision_enabled", False):
                conn.execute(
                    """
                    INSERT INTO task_runtime_outbox(
                        outbox_id, aggregate_type, aggregate_id, aggregate_version,
                        graph_revision, event_type, payload_ref, evidence_refs_json,
                        attempt_id, idempotency_key, status, available_ts_ms,
                        created_ts_ms, published_ts_ms, status_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, NULL, 0)
                    """,
                    (
                        message.outbox_id,
                        message.aggregate_type,
                        message.aggregate_id,
                        message.aggregate_version,
                        message.graph_revision,
                        message.event_type,
                        physical_payload_ref,
                        _dumps(list(message.evidence_refs)),
                        message.attempt_id,
                        message.idempotency_key,
                        ts_ms,
                        ts_ms,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO task_runtime_outbox(
                        outbox_id, aggregate_type, aggregate_id, aggregate_version,
                        event_type, payload_ref, evidence_refs_json, attempt_id,
                        idempotency_key, status, available_ts_ms, created_ts_ms,
                        published_ts_ms, status_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, NULL, 0)
                    """,
                    (
                        message.outbox_id,
                        message.aggregate_type,
                        message.aggregate_id,
                        message.aggregate_version,
                        message.event_type,
                        physical_payload_ref,
                        _dumps(list(message.evidence_refs)),
                        message.attempt_id,
                        message.idempotency_key,
                        ts_ms,
                        ts_ms,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise TaskStoreError(
                TASK_STORE_OUTBOX_CONFLICT,
                f"outbox identity/idempotency conflict: {message.idempotency_key}",
            ) from exc

    def _assert_mutation_outbox_replay(
        self,
        conn: sqlite3.Connection,
        message: OutboxMessage,
        *,
        expected_aggregate: tuple[str, str, int],
        expected_event_type: str,
        expected_attempt_id: str | None,
        expected_graph_revision: int | None = None,
    ) -> None:
        if not isinstance(message, OutboxMessage):
            raise TaskStoreError(TASK_STORE_INVALID, "atomic replay requires an OutboxMessage")
        message.validate()
        if message.available_ts_ms is not None:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "mutation outbox availability is assigned only by the trusted store clock",
            )
        expected = (
            str(expected_aggregate[0]),
            str(expected_aggregate[1]),
            int(expected_aggregate[2]),
        )
        if message.aggregate_type in REVISIONED_OUTBOX_AGGREGATE_TYPES:
            if (
                type(expected_graph_revision) is not int
                or expected_graph_revision < 1
                or message.graph_revision != expected_graph_revision
            ):
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "revisioned replay outbox must bind the exact graph revision",
                )
        elif expected_graph_revision is not None:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "non-revisioned replay cannot declare expected_graph_revision",
            )
        if (
            (message.aggregate_type, message.aggregate_id, message.aggregate_version) != expected
            or message.event_type != str(expected_event_type)
            or message.attempt_id != (str(expected_attempt_id or "").strip() or None)
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "atomic replay outbox does not bind the committed mutation",
            )
        by_id = conn.execute(
            "SELECT * FROM task_runtime_outbox WHERE outbox_id = ?",
            (message.outbox_id,),
        ).fetchone()
        by_key = conn.execute(
            "SELECT * FROM task_runtime_outbox WHERE idempotency_key = ?",
            (message.idempotency_key,),
        ).fetchone()
        if (
            by_id is None
            or by_key is None
            or str(by_id["outbox_id"]) != str(by_key["outbox_id"])
        ):
            raise TaskStoreError(
                TASK_STORE_OUTBOX_CONFLICT,
                "atomic acceptance replay is missing its exact state outbox",
            )
        durable = _outbox_record(by_id)
        legacy_graph_payload_ref = (
            build_outbox_payload_ref(
                aggregate_type=message.aggregate_type,
                aggregate_id=message.aggregate_id,
                aggregate_version=message.aggregate_version,
            )
            if message.aggregate_type == "TASK_GRAPH"
            else None
        )
        payload_ref_matches = (
            str(durable["payload_ref"]) == message.payload_ref
            or (
                legacy_graph_payload_ref is not None
                and str(durable["payload_ref"]) == legacy_graph_payload_ref
            )
        )
        drift = (
            str(durable["outbox_id"]) != message.outbox_id
            or str(durable["aggregate_type"]) != message.aggregate_type
            or str(durable["aggregate_id"]) != message.aggregate_id
            or int(durable["aggregate_version"]) != message.aggregate_version
            or (
                getattr(self, "_outbox_graph_revision_enabled", False)
                and durable.get("graph_revision") != message.graph_revision
            )
            or str(durable["event_type"]) != message.event_type
            or not payload_ref_matches
            or str(durable["idempotency_key"]) != message.idempotency_key
            or durable["attempt_id"] != message.attempt_id
            or tuple(durable["evidence_refs"]) != tuple(message.evidence_refs)
        )
        if drift:
            raise TaskStoreError(
                TASK_STORE_OUTBOX_CONFLICT,
                "atomic acceptance state outbox replay drift",
            )

    def _initialize_outbox_delivery_capability(self) -> None:
        """Inspect delivery schema once while constructing the store.

        A completely pre-0093 database retains the legacy read/settle API for
        migration compatibility.  Any partial 0093/0095 footprint fails closed.
        Delivery hot paths consume only the resulting in-memory capability
        mode; they never repeat schema or full-table scans.
        """

        self._outbox_delivery_mode = _OUTBOX_DELIVERY_MODE_UNCHECKED
        conn = self._connect()
        try:
            column_rows = {
                str(row[1]): row
                for row in conn.execute(
                    "PRAGMA table_info(task_runtime_outbox)"
                ).fetchall()
            }
            columns = set(column_rows)
        finally:
            conn.close()
        self._outbox_graph_revision_enabled = "graph_revision" in columns
        migration_columns = _OUTBOX_DELIVERY_REQUIRED_COLUMNS - {"status_version"}
        present = migration_columns.intersection(columns)
        if not present:
            self._outbox_delivery_mode = _OUTBOX_DELIVERY_MODE_LEGACY
            return
        self.assert_outbox_delivery_ready()

    def _require_outbox_delivery_ready(self) -> None:
        if (
            getattr(self, "_outbox_delivery_mode", _OUTBOX_DELIVERY_MODE_UNCHECKED)
            != _OUTBOX_DELIVERY_MODE_READY
        ):
            raise TaskStoreError(
                TASK_STORE_SCHEMA_NOT_READY,
                "task runtime outbox claim/revision migrations 0093/0095 were not verified "
                "when this store was constructed",
            )

    def _require_legacy_outbox_settlement(self) -> None:
        mode = getattr(
            self,
            "_outbox_delivery_mode",
            _OUTBOX_DELIVERY_MODE_UNCHECKED,
        )
        if mode == _OUTBOX_DELIVERY_MODE_READY:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "migration 0093 requires complete_outbox_claim; "
                "unfenced mark_outbox_published is disabled",
            )
        if mode != _OUTBOX_DELIVERY_MODE_LEGACY:
            raise TaskStoreError(
                TASK_STORE_SCHEMA_NOT_READY,
                "task runtime outbox schema was not verified when this store "
                "was constructed",
            )

    def assert_outbox_delivery_ready(
        self,
        *,
        force_revalidate: bool = False,
    ) -> None:
        """Verify 0093/0095 once, with an explicit diagnostic revalidation escape."""

        if (
            not force_revalidate
            and getattr(
                self,
                "_outbox_delivery_mode",
                _OUTBOX_DELIVERY_MODE_UNCHECKED,
            )
            == _OUTBOX_DELIVERY_MODE_READY
        ):
            return

        self._outbox_delivery_mode = _OUTBOX_DELIVERY_MODE_UNCHECKED
        conn = self._connect()
        try:
            column_rows = {
                str(row[1]): row
                for row in conn.execute(
                    "PRAGMA table_info(task_runtime_outbox)"
                ).fetchall()
            }
            columns = set(column_rows)
            index_names = {
                str(row[0])
                for row in conn.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'index' AND tbl_name = 'task_runtime_outbox'
                    """
                ).fetchall()
            }
            trigger_rows = {
                str(row[0]): str(row[1] or "")
                for row in conn.execute(
                    """
                    SELECT name, sql FROM sqlite_master
                    WHERE type = 'trigger' AND tbl_name = 'task_runtime_outbox'
                    """
                ).fetchall()
            }
            effect_reconciliation_schema = conn.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table'
                  AND name = 'task_runtime_effect_reconciliation_case'
                """
            ).fetchone() is not None
            table_row = conn.execute(
                """
                SELECT sql FROM sqlite_master
                WHERE type = 'table' AND name = 'task_runtime_outbox'
                """
            ).fetchone()
            table_sql = _normalized_sql(table_row[0] if table_row else "")
            index_columns = {
                name: tuple(
                    str(row[2])
                    for row in conn.execute(f"PRAGMA index_info({name})").fetchall()
                )
                for name in _OUTBOX_DELIVERY_REQUIRED_INDEXES.intersection(index_names)
            }
            invalid = None
            if _OUTBOX_DELIVERY_REQUIRED_COLUMNS <= columns:
                invalid = conn.execute(
                    """
                    SELECT outbox_id
                    FROM task_runtime_outbox
                    WHERE
                        (
                            aggregate_type = 'TASK_GRAPH'
                            AND (
                                graph_revision IS NULL
                                OR typeof(graph_revision) <> 'integer'
                                OR graph_revision < 1
                                OR NOT EXISTS (
                                    SELECT 1
                                    FROM task_runtime_graph AS graph
                                    WHERE graph.graph_id = task_runtime_outbox.aggregate_id
                                      AND graph.revision = task_runtime_outbox.graph_revision
                                )
                            )
                        )
                        OR (
                            aggregate_type NOT IN (
                                'TASK_GRAPH',
                                'GRAPH_CANCELLATION_EFFECT_RECONCILIATION'
                            )
                            AND graph_revision IS NOT NULL
                        )
                        OR (
                            aggregate_type = 'GRAPH_CANCELLATION_EFFECT_RECONCILIATION'
                            AND (
                                graph_revision IS NULL
                                OR typeof(graph_revision) <> 'integer'
                                OR graph_revision < 1
                            )
                        )
                        OR
                        (
                            status = 'PENDING'
                            AND NOT (
                                (
                                    claim_owner IS NULL
                                    AND claim_token IS NULL
                                    AND claim_expires_ts_ms IS NULL
                                )
                                OR
                                (
                                    claim_owner IS NOT NULL
                                    AND typeof(claim_owner) = 'text'
                                    AND length(trim(claim_owner)) > 0
                                    AND claim_token IS NOT NULL
                                    AND typeof(claim_token) = 'text'
                                    AND length(trim(claim_token)) > 0
                                    AND claim_expires_ts_ms IS NOT NULL
                                    AND typeof(claim_expires_ts_ms) = 'integer'
                                    AND claim_expires_ts_ms >= 0
                                    AND claim_fencing_token > 0
                                    AND attempt_count > 0
                                    AND claim_fencing_token = attempt_count
                                )
                            )
                        )
                        OR
                        (
                            status <> 'PENDING'
                            AND (
                                claim_owner IS NOT NULL
                                OR claim_token IS NOT NULL
                                OR claim_expires_ts_ms IS NOT NULL
                            )
                        )
                        OR (
                            claim_owner IS NOT NULL
                            AND typeof(claim_owner) <> 'text'
                        )
                        OR (
                            claim_token IS NOT NULL
                            AND typeof(claim_token) <> 'text'
                        )
                        OR (
                            claim_expires_ts_ms IS NOT NULL
                            AND (
                                typeof(claim_expires_ts_ms) <> 'integer'
                                OR claim_expires_ts_ms < 0
                            )
                        )
                        OR typeof(claim_fencing_token) <> 'integer'
                        OR typeof(attempt_count) <> 'integer'
                        OR claim_fencing_token < 0
                        OR attempt_count < 0
                        OR claim_fencing_token <> attempt_count
                        OR (
                            last_error IS NOT NULL
                            AND (
                                typeof(last_error) <> 'text'
                                OR length(trim(last_error)) = 0
                                OR length(last_error) > 2000
                            )
                        )
                    ORDER BY created_ts_ms, outbox_id
                    LIMIT 1
                    """
                ).fetchone()
        finally:
            conn.close()
        missing = sorted(_OUTBOX_DELIVERY_REQUIRED_COLUMNS - columns)
        missing_indexes = sorted(_OUTBOX_DELIVERY_REQUIRED_INDEXES - index_names)
        missing_triggers = sorted(
            _OUTBOX_DELIVERY_REQUIRED_TRIGGERS - trigger_rows.keys()
        )
        if missing or missing_indexes or missing_triggers:
            raise TaskStoreError(
                TASK_STORE_SCHEMA_NOT_READY,
                "task runtime outbox claim/revision migrations 0093/0095 are not applied: "
                f"columns={missing}, indexes={missing_indexes}, "
                f"triggers={missing_triggers}",
            )
        graph_revision_column = column_rows.get("graph_revision")
        invalid_column_shapes = []
        if graph_revision_column is not None and (
            str(graph_revision_column[2] or "").strip().upper() != "INTEGER"
            or int(graph_revision_column[3]) != 0
            or graph_revision_column[4] is not None
            or int(graph_revision_column[5]) != 0
        ):
            invalid_column_shapes.append("graph_revision")
        invalid_indexes = sorted(
            name
            for name, expected in _OUTBOX_DELIVERY_INDEX_COLUMNS.items()
            if index_columns.get(name) != expected
        )
        missing_table_guards = sorted(
            fragment
            for fragment in _OUTBOX_DELIVERY_TABLE_SQL_FRAGMENTS
            if fragment not in table_sql
        )
        expected_trigger_fragments = (
            _OUTBOX_DELIVERY_EFFECT_TRIGGER_SQL_FRAGMENTS
            if effect_reconciliation_schema
            else _OUTBOX_DELIVERY_LEGACY_TRIGGER_SQL_FRAGMENTS
        )
        invalid_trigger_guards = sorted(
            name
            for name, fragments in expected_trigger_fragments.items()
            if any(
                fragment not in _normalized_sql(trigger_rows.get(name))
                for fragment in fragments
            )
        )
        if (
            invalid_column_shapes
            or invalid_indexes
            or missing_table_guards
            or invalid_trigger_guards
        ):
            raise TaskStoreError(
                TASK_STORE_SCHEMA_NOT_READY,
                "task runtime outbox claim schema is not the frozen 0093/0095 shape: "
                f"columns={invalid_column_shapes}, indexes={invalid_indexes}, "
                f"table_guards={missing_table_guards}, "
                f"trigger_guards={invalid_trigger_guards}",
            )
        if invalid is not None:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "task runtime outbox has an invalid durable claim state: "
                f"{invalid['outbox_id']}",
            )
        self._outbox_delivery_mode = _OUTBOX_DELIVERY_MODE_READY

    def inspect_outbox_delivery_backlog(self) -> dict[str, int | None]:
        """Return one durable liveness snapshot without claiming work.

        Only each aggregate's earliest PENDING event participates in ready and
        wake-up calculations.  Successors therefore cannot cause a publisher
        to spin while the aggregate head is leased or delayed.
        """

        self._require_outbox_delivery_ready()
        ts_ms = self._now()
        conn = self._connect()
        try:
            row = conn.execute(
                """
                WITH aggregate_heads AS (
                    SELECT candidate.available_ts_ms,
                           candidate.claim_owner,
                           candidate.claim_token,
                           candidate.claim_expires_ts_ms
                    FROM task_runtime_outbox AS candidate
                    WHERE candidate.status = 'PENDING'
                      AND NOT EXISTS (
                          SELECT 1
                          FROM task_runtime_outbox AS predecessor
                          WHERE predecessor.aggregate_type = candidate.aggregate_type
                            AND predecessor.aggregate_id = candidate.aggregate_id
                            AND predecessor.graph_revision IS candidate.graph_revision
                            AND predecessor.status = 'PENDING'
                            AND (
                                predecessor.aggregate_version < candidate.aggregate_version
                                OR (
                                    predecessor.aggregate_version = candidate.aggregate_version
                                    AND predecessor.created_ts_ms < candidate.created_ts_ms
                                )
                                OR (
                                    predecessor.aggregate_version = candidate.aggregate_version
                                    AND predecessor.created_ts_ms = candidate.created_ts_ms
                                    AND predecessor.outbox_id < candidate.outbox_id
                                )
                            )
                      )
                ), head_snapshot AS (
                    SELECT
                        COALESCE(
                            SUM(
                                CASE
                                    WHEN available_ts_ms <= ?
                                     AND (
                                         (
                                             claim_owner IS NULL
                                             AND claim_token IS NULL
                                             AND claim_expires_ts_ms IS NULL
                                         )
                                         OR claim_expires_ts_ms <= ?
                                     )
                                    THEN 1 ELSE 0
                                END
                            ),
                            0
                        ) AS delivery_ready_count,
                        MIN(
                            MAX(
                                ?,
                                available_ts_ms,
                                CASE
                                    WHEN claim_expires_ts_ms > ?
                                    THEN claim_expires_ts_ms
                                    ELSE available_ts_ms
                                END
                            )
                        ) AS next_wakeup_ts_ms
                    FROM aggregate_heads
                )
                SELECT
                    (
                        SELECT COUNT(*)
                        FROM task_runtime_outbox
                        WHERE status = 'PENDING'
                    ) AS total_pending,
                    head_snapshot.delivery_ready_count,
                    head_snapshot.next_wakeup_ts_ms
                FROM head_snapshot
                """,
                (ts_ms, ts_ms, ts_ms, ts_ms),
            ).fetchone()
        finally:
            conn.close()
        if row is None:  # pragma: no cover - aggregate SELECT always returns one row
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "task runtime outbox backlog snapshot returned no row",
            )
        return {
            "total_pending": int(row["total_pending"]),
            "delivery_ready_count": int(row["delivery_ready_count"]),
            "next_wakeup_ts_ms": (
                int(row["next_wakeup_ts_ms"])
                if row["next_wakeup_ts_ms"] is not None
                else None
            ),
        }

    def claim_pending_outbox(
        self,
        worker_id: str,
        lease_ms: int,
        limit: int = 1,
    ) -> list[dict[str, Any]]:
        """Atomically claim available events without overtaking one aggregate.

        A transaction may claim multiple aggregates, but once it claims the
        oldest unpublished event for an aggregate, later versions remain
        blocked until that event is published.  An expired claim can be taken
        over and always receives a new token and a strictly higher fence.
        """

        self._require_outbox_delivery_ready()
        owner = _required_text(worker_id, field_name="worker_id", maximum=255)
        lease = _nonnegative_int(lease_ms, field_name="lease_ms")
        if lease < 1:
            raise TaskStoreError(TASK_STORE_INVALID, "lease_ms must be positive")
        size = _nonnegative_int(limit, field_name="limit")
        if size < 1:
            raise TaskStoreError(TASK_STORE_INVALID, "limit must be positive")
        size = min(size, 1_000)
        ts_ms = self._now()
        expires_ts_ms = ts_ms + lease
        claimed: list[dict[str, Any]] = []
        with self._transaction() as conn:
            for _ in range(size):
                row = conn.execute(
                    """
                    SELECT candidate.*
                    FROM task_runtime_outbox AS candidate
                    WHERE candidate.status = 'PENDING'
                      AND candidate.available_ts_ms <= ?
                      AND (
                          (
                              candidate.claim_owner IS NULL
                              AND candidate.claim_token IS NULL
                              AND candidate.claim_expires_ts_ms IS NULL
                          )
                          OR
                          (
                              candidate.claim_owner IS NOT NULL
                              AND candidate.claim_token IS NOT NULL
                              AND candidate.claim_expires_ts_ms <= ?
                          )
                      )
                      AND NOT EXISTS (
                          SELECT 1
                          FROM task_runtime_outbox AS predecessor
                          WHERE predecessor.aggregate_type = candidate.aggregate_type
                            AND predecessor.aggregate_id = candidate.aggregate_id
                            AND predecessor.graph_revision IS candidate.graph_revision
                            AND predecessor.status = 'PENDING'
                            AND (
                                predecessor.aggregate_version < candidate.aggregate_version
                                OR (
                                    predecessor.aggregate_version = candidate.aggregate_version
                                    AND predecessor.created_ts_ms < candidate.created_ts_ms
                                )
                                OR (
                                    predecessor.aggregate_version = candidate.aggregate_version
                                    AND predecessor.created_ts_ms = candidate.created_ts_ms
                                    AND predecessor.outbox_id < candidate.outbox_id
                                )
                            )
                      )
                    ORDER BY candidate.created_ts_ms, candidate.outbox_id
                    LIMIT 1
                    """,
                    (ts_ms, ts_ms),
                ).fetchone()
                if row is None:
                    break
                claim_token = secrets.token_urlsafe(32)
                cursor = conn.execute(
                    """
                    UPDATE task_runtime_outbox
                    SET claim_owner = ?, claim_token = ?, claim_expires_ts_ms = ?,
                        claim_fencing_token = claim_fencing_token + 1,
                        attempt_count = attempt_count + 1,
                        status_version = status_version + 1,
                        last_error = NULL
                    WHERE outbox_id = ? AND status = 'PENDING'
                      AND status_version = ? AND available_ts_ms <= ?
                      AND (
                          (
                              claim_owner IS NULL
                              AND claim_token IS NULL
                              AND claim_expires_ts_ms IS NULL
                          )
                          OR
                          (
                              claim_owner IS NOT NULL
                              AND claim_token IS NOT NULL
                              AND claim_expires_ts_ms <= ?
                          )
                      )
                    """,
                    (
                        owner,
                        claim_token,
                        expires_ts_ms,
                        row["outbox_id"],
                        int(row["status_version"]),
                        ts_ms,
                        ts_ms,
                    ),
                )
                if cursor.rowcount != 1:
                    raise TaskStoreError(
                        TASK_STORE_CAS_CONFLICT,
                        f"outbox claim lost its CAS fence: {row['outbox_id']}",
                    )
                claimed.append(
                    _outbox_record(
                        self._require_outbox(conn, str(row["outbox_id"]))
                    )
                )
        return claimed

    def complete_outbox_claim(
        self,
        outbox_id: str,
        worker_id: str,
        claim_token: str,
        fencing_token: int,
        expected_version: int,
    ) -> dict[str, Any]:
        """Publish exactly the live claim identified by owner, token and fence."""

        self._require_outbox_delivery_ready()
        outbox_ref = _required_text(outbox_id, field_name="outbox_id", maximum=512)
        owner = _required_text(worker_id, field_name="worker_id", maximum=255)
        token = _required_text(claim_token, field_name="claim_token", maximum=512)
        fence = _nonnegative_int(fencing_token, field_name="fencing_token")
        version = _nonnegative_int(expected_version, field_name="expected_version")
        if fence < 1:
            raise TaskStoreError(TASK_STORE_INVALID, "fencing_token must be positive")
        ts_ms = self._now()
        with self._transaction() as conn:
            row = self._require_outbox(conn, outbox_ref)
            record = _outbox_record(row)
            self._require_version(row, version, subject=f"Outbox {outbox_ref}")
            self._assert_live_outbox_claim(
                record,
                worker_id=owner,
                claim_token=token,
                fencing_token=fence,
                now_ts_ms=ts_ms,
            )
            cursor = conn.execute(
                """
                UPDATE task_runtime_outbox
                SET status = 'PUBLISHED', published_ts_ms = ?,
                    claim_owner = NULL, claim_token = NULL,
                    claim_expires_ts_ms = NULL, last_error = NULL,
                    status_version = status_version + 1
                WHERE outbox_id = ? AND status = 'PENDING'
                  AND claim_owner = ? AND claim_token = ?
                  AND claim_fencing_token = ? AND status_version = ?
                  AND claim_expires_ts_ms > ?
                """,
                (
                    ts_ms,
                    outbox_ref,
                    owner,
                    token,
                    fence,
                    version,
                    ts_ms,
                ),
            )
            if cursor.rowcount != 1:
                raise TaskStoreError(
                    TASK_STORE_LEASE_CONFLICT,
                    f"outbox completion lost its durable claim: {outbox_ref}",
                )
            return _outbox_record(self._require_outbox(conn, outbox_ref))

    def release_outbox_claim(
        self,
        outbox_id: str,
        worker_id: str,
        claim_token: str,
        fencing_token: int,
        retry_delay_ms: int,
        last_error: str,
        expected_version: int,
    ) -> dict[str, Any]:
        """Return a live claim to PENDING with a durable retry deadline."""

        self._require_outbox_delivery_ready()
        outbox_ref = _required_text(outbox_id, field_name="outbox_id", maximum=512)
        owner = _required_text(worker_id, field_name="worker_id", maximum=255)
        token = _required_text(claim_token, field_name="claim_token", maximum=512)
        error = _required_text(last_error, field_name="last_error", maximum=2_000)
        fence = _nonnegative_int(fencing_token, field_name="fencing_token")
        version = _nonnegative_int(expected_version, field_name="expected_version")
        delay = _nonnegative_int(retry_delay_ms, field_name="retry_delay_ms")
        if fence < 1:
            raise TaskStoreError(TASK_STORE_INVALID, "fencing_token must be positive")
        ts_ms = self._now()
        available_ts_ms = ts_ms + delay
        with self._transaction() as conn:
            row = self._require_outbox(conn, outbox_ref)
            record = _outbox_record(row)
            self._require_version(row, version, subject=f"Outbox {outbox_ref}")
            self._assert_live_outbox_claim(
                record,
                worker_id=owner,
                claim_token=token,
                fencing_token=fence,
                now_ts_ms=ts_ms,
            )
            cursor = conn.execute(
                """
                UPDATE task_runtime_outbox
                SET available_ts_ms = ?, claim_owner = NULL,
                    claim_token = NULL, claim_expires_ts_ms = NULL,
                    last_error = ?, status_version = status_version + 1
                WHERE outbox_id = ? AND status = 'PENDING'
                  AND claim_owner = ? AND claim_token = ?
                  AND claim_fencing_token = ? AND status_version = ?
                  AND claim_expires_ts_ms > ?
                """,
                (
                    available_ts_ms,
                    error,
                    outbox_ref,
                    owner,
                    token,
                    fence,
                    version,
                    ts_ms,
                ),
            )
            if cursor.rowcount != 1:
                raise TaskStoreError(
                    TASK_STORE_LEASE_CONFLICT,
                    f"outbox release lost its durable claim: {outbox_ref}",
                )
            return _outbox_record(self._require_outbox(conn, outbox_ref))

    @staticmethod
    def _assert_live_outbox_claim(
        row: Any,
        *,
        worker_id: str,
        claim_token: str,
        fencing_token: int,
        now_ts_ms: int,
    ) -> None:
        outbox_id = str(row["outbox_id"])
        if str(row["status"]) != "PENDING" or row["claim_token"] is None:
            raise TaskStoreError(
                TASK_STORE_LEASE_CONFLICT,
                f"outbox has no active delivery claim: {outbox_id}",
            )
        if str(row["claim_owner"] or "") != worker_id:
            raise TaskStoreError(
                TASK_STORE_OWNERSHIP_CONFLICT,
                f"outbox is owned by another publisher: {outbox_id}",
            )
        if (
            str(row["claim_token"] or "") != claim_token
            or int(row["claim_fencing_token"]) != fencing_token
        ):
            raise TaskStoreError(
                TASK_STORE_LEASE_CONFLICT,
                f"outbox delivery fence is stale: {outbox_id}",
            )
        if int(row["claim_expires_ts_ms"] or 0) <= int(now_ts_ms):
            raise TaskStoreError(
                TASK_STORE_LEASE_CONFLICT,
                f"outbox delivery claim has expired: {outbox_id}",
            )


__all__ = ["TaskOutboxDeliveryStoreMixin"]
