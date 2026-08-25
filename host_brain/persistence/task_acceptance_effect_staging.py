"""Acceptance-effect normalization and atomic staging transactions.

The mixin borrows the owning ``DurableTaskStore`` connection and transaction
primitives.  It owns neither a database path nor an independent connection.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Mapping, Sequence

from host_brain.contracts.agent_runtime.task_graph import TaskGraphStatus, TaskNodeStatus
from host_brain.persistence.task_record_codec import (
    _ACCEPTANCE_EFFECT_TYPE_PATTERN,
    _MAX_ACCEPTANCE_EFFECT_PAYLOAD_BYTES,
    _SHA256_DIGEST_PATTERN,
    _acceptance_effect_record,
    _acceptance_effect_text,
    _dumps,
    _graph_record,
    _loads_object,
    _parallel_success_decision_record,
    build_acceptance_effect_payload_digest,
)
from host_brain.persistence.task_acceptance_effect_staging_authority import (
    canonical_task_acceptance_effect_staging_scope,
)
from host_brain.persistence.task_store_types import (
    AcceptanceEffectCommitScope,
    AcceptanceEffectIntentSpec,
    AttemptStatus,
    OutboxMessage,
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_INVALID,
    TASK_STORE_OUTBOX_CONFLICT,
    TaskStoreError,
    _DURABLE_REFERENCE_PATTERN,
)


class TaskAcceptanceEffectStagingMixin:
    """Normalize and stage acceptance effects inside owner transactions."""

    def transition_graph_and_stage_acceptance_effects(
        self,
        graph_id: str,
        revision: int,
        *,
        expected_version: int,
        outbox: OutboxMessage,
        effect_intents: Sequence[AcceptanceEffectIntentSpec | Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Commit ``TaskGraph.COMPLETED`` and GRAPH effects atomically.

        Completion is acceptance-gated here: the graph must contain at least
        one node and every durable node must already be ACCEPTED.  Each effect
        must bind a successful attempt of one of those accepted nodes.
        """

        self.assert_acceptance_effect_intent_ready()
        specs = self._normalize_acceptance_effect_intent_specs(effect_intents)
        graph_ref = str(graph_id or "").strip()
        graph_revision = int(revision)
        for spec in specs:
            if (
                spec.graph_id != graph_ref
                or spec.graph_revision != graph_revision
                or spec.commit_scope is not AcceptanceEffectCommitScope.GRAPH
            ):
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "atomic graph acceptance effects must bind the exact graph with GRAPH scope",
                )
        ts_ms = self._now()
        with self._transaction() as conn:
            self._assert_graph_not_cancelling(
                conn,
                graph_id=graph_ref,
                graph_revision=graph_revision,
            )
            node_rows = conn.execute(
                """
                SELECT * FROM task_runtime_node
                WHERE graph_id = ? AND graph_revision = ?
                ORDER BY node_id
                """,
                (graph_ref, graph_revision),
            ).fetchall()
            if not node_rows or any(
                str(row["status"]) != TaskNodeStatus.ACCEPTED.value
                for row in node_rows
            ):
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "TaskGraph COMPLETED with acceptance effects requires all nodes ACCEPTED",
                )
            current_graph = self._require_graph(conn, graph_ref, graph_revision)
            if (
                int(current_graph["status_version"]) == int(expected_version) + 1
                and str(current_graph["status"]) == TaskGraphStatus.COMPLETED.value
            ):
                self._assert_mutation_outbox_replay(
                    conn,
                    outbox,
                    expected_aggregate=(
                        "TASK_GRAPH",
                        graph_ref,
                        int(expected_version) + 1,
                    ),
                    expected_event_type="TASK_GRAPH_COMPLETED",
                    expected_attempt_id=None,
                    expected_graph_revision=graph_revision,
                )
                staged = tuple(
                    self._stage_acceptance_effect_intent_in_transaction(
                        conn,
                        spec,
                        ts_ms=ts_ms,
                        require_succeeded_attempt=True,
                        allow_insert=False,
                    )
                    for spec in specs
                )
                return {
                    "graph": _graph_record(current_graph, node_rows),
                    "effect_intents": staged,
                    "replayed": True,
                }
            graph_record = self._transition_graph_in_transaction(
                conn,
                graph_id=graph_ref,
                revision=graph_revision,
                expected_version=expected_version,
                target_status=TaskGraphStatus.COMPLETED,
                outbox=outbox,
                ts_ms=ts_ms,
            )
            staged = tuple(
                self._stage_acceptance_effect_intent_in_transaction(
                    conn,
                    spec,
                    ts_ms=ts_ms,
                    require_succeeded_attempt=True,
                    allow_terminal_graph_insert=True,
                )
                for spec in specs
            )
            return {
                "graph": graph_record,
                "effect_intents": staged,
                "replayed": False,
            }

    def _normalize_acceptance_effect_intent_specs(
        self,
        values: Sequence[AcceptanceEffectIntentSpec | Mapping[str, Any]],
    ) -> tuple[AcceptanceEffectIntentSpec, ...]:
        if isinstance(values, (str, bytes, bytearray, Mapping)):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "effect_intents must be a non-empty sequence",
            )
        try:
            normalized = tuple(
                self._normalize_acceptance_effect_intent_spec(value)
                for value in values
            )
        except TypeError as exc:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "effect_intents must be a non-empty sequence",
            ) from exc
        if not normalized:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "atomic acceptance commit requires at least one effect intent",
            )
        return normalized

    @staticmethod
    def _normalize_acceptance_effect_intent_spec(
        value: AcceptanceEffectIntentSpec | Mapping[str, Any],
    ) -> AcceptanceEffectIntentSpec:
        raw = (
            value
            if isinstance(value, AcceptanceEffectIntentSpec)
            else AcceptanceEffectIntentSpec.from_mapping(value)
        )
        effect_ref = _acceptance_effect_text(
            raw.effect_id,
            field_name="effect_id",
            maximum=512,
        )
        if not _DURABLE_REFERENCE_PATTERN.fullmatch(effect_ref):
            raise TaskStoreError(TASK_STORE_INVALID, "effect_id must be a durable reference")
        graph_ref = _acceptance_effect_text(
            raw.graph_id,
            field_name="graph_id",
            maximum=255,
        )
        node_ref = _acceptance_effect_text(
            raw.node_id,
            field_name="node_id",
            maximum=255,
        )
        attempt_ref = _acceptance_effect_text(
            raw.attempt_id,
            field_name="attempt_id",
            maximum=255,
        )
        effect_type = str(raw.effect_type or "").strip()
        if (
            len(effect_type) > 128
            or not _ACCEPTANCE_EFFECT_TYPE_PATTERN.fullmatch(effect_type)
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "effect_type must be a lowercase dotted namespace of at most 128 characters",
            )
        payload_ref = _acceptance_effect_text(
            raw.payload_ref,
            field_name="payload_ref",
            maximum=512,
        )
        if not _DURABLE_REFERENCE_PATTERN.fullmatch(payload_ref):
            raise TaskStoreError(TASK_STORE_INVALID, "payload_ref must be a durable reference")
        if not isinstance(raw.payload, Mapping):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "acceptance effect payload must be a JSON object",
            )
        payload_json = _dumps(dict(raw.payload))
        payload_size = len(payload_json.encode("utf-8"))
        if payload_size < 2 or payload_size > _MAX_ACCEPTANCE_EFFECT_PAYLOAD_BYTES:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "acceptance effect payload exceeds the canonical size limit",
            )
        canonical_payload = _loads_object(
            payload_json,
            field_name="acceptance_effect.payload",
        )
        digest = str(raw.payload_sha256 or "").strip()
        if not _SHA256_DIGEST_PATTERN.fullmatch(digest):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "payload_sha256 must be a canonical lowercase sha256 digest",
            )
        computed_digest = build_acceptance_effect_payload_digest(canonical_payload)
        if digest != computed_digest:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "payload_sha256 does not match the canonical acceptance effect payload",
            )
        idempotency_key = _acceptance_effect_text(
            raw.idempotency_key,
            field_name="idempotency_key",
            maximum=512,
        )
        if (
            isinstance(raw.graph_revision, bool)
            or not isinstance(raw.graph_revision, int)
            or raw.graph_revision < 1
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "graph_revision must be a positive integer",
            )
        try:
            scope = AcceptanceEffectCommitScope(
                str(raw.commit_scope or "").strip().upper()
            )
        except ValueError as exc:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "commit_scope must be NODE or GRAPH",
            ) from exc
        return AcceptanceEffectIntentSpec(
            effect_id=effect_ref,
            graph_id=graph_ref,
            graph_revision=raw.graph_revision,
            node_id=node_ref,
            attempt_id=attempt_ref,
            commit_scope=scope,
            effect_type=effect_type,
            payload_ref=payload_ref,
            payload=canonical_payload,
            payload_sha256=digest,
            idempotency_key=idempotency_key,
        )

    def _stage_acceptance_effect_intent_in_transaction(
        self,
        conn: sqlite3.Connection,
        spec: AcceptanceEffectIntentSpec,
        *,
        ts_ms: int,
        require_succeeded_attempt: bool = False,
        allow_insert: bool = True,
        allow_terminal_graph_insert: bool = False,
    ) -> dict[str, Any]:
        payload_json = _dumps(dict(spec.payload))
        graph = self._require_graph(conn, spec.graph_id, spec.graph_revision)
        node = self._require_node(
            conn,
            spec.graph_id,
            spec.graph_revision,
            spec.node_id,
        )
        attempt = self._require_attempt(conn, spec.attempt_id)
        if (
            str(attempt["graph_id"]) != spec.graph_id
            or int(attempt["graph_revision"]) != spec.graph_revision
            or str(attempt["node_id"]) != spec.node_id
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "effect intent attempt is outside its graph/node binding",
            )
        if require_succeeded_attempt and str(attempt["status"]) != AttemptStatus.SUCCEEDED.value:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "atomic acceptance effect requires a SUCCEEDED bound attempt",
            )
        if (
            require_succeeded_attempt
            and spec.commit_scope is AcceptanceEffectCommitScope.GRAPH
            and str(node["status"]) != TaskNodeStatus.ACCEPTED.value
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "graph acceptance effect must bind an ACCEPTED node",
            )

        frozen_by_success = False
        if spec.commit_scope is AcceptanceEffectCommitScope.GRAPH:
            success_schema_exists = conn.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table'
                  AND name = 'task_runtime_parallel_success_decision'
                """
            ).fetchone()
            success_row = (
                conn.execute(
                    """
                    SELECT * FROM task_runtime_parallel_success_decision
                    WHERE graph_id = ? AND graph_revision = ?
                    """,
                    (spec.graph_id, spec.graph_revision),
                ).fetchone()
                if success_schema_exists is not None
                else None
            )
            if success_row is not None:
                success = _parallel_success_decision_record(success_row)
                expected = next(
                    (
                        item
                        for item in success["payload"]["effect_intents"]
                        if str(item["effect_id"]) == spec.effect_id
                    ),
                    None,
                )
                actual_summary = {
                    "effect_id": spec.effect_id,
                    "node_id": spec.node_id,
                    "attempt_id": spec.attempt_id,
                    "commit_scope": spec.commit_scope.value,
                    "effect_type": spec.effect_type,
                    "payload_ref": spec.payload_ref,
                    "payload_sha256": spec.payload_sha256,
                    "idempotency_key": spec.idempotency_key,
                }
                if expected != actual_summary:
                    raise TaskStoreError(
                        TASK_STORE_OUTBOX_CONFLICT,
                        "parallel success permanently freezes its Graph effect set",
                    )
                frozen_by_success = True

        by_effect = conn.execute(
            "SELECT * FROM task_runtime_acceptance_effect_intent WHERE effect_id = ?",
            (spec.effect_id,),
        ).fetchone()
        by_key = conn.execute(
            "SELECT * FROM task_runtime_acceptance_effect_intent WHERE idempotency_key = ?",
            (spec.idempotency_key,),
        ).fetchone()
        if (
            by_effect is not None
            and by_key is not None
            and str(by_effect["effect_id"]) != str(by_key["effect_id"])
        ):
            raise TaskStoreError(
                TASK_STORE_OUTBOX_CONFLICT,
                "effect_id and idempotency_key identify different durable intents",
            )
        existing = by_effect if by_effect is not None else by_key
        if existing is not None:
            drift = (
                str(existing["effect_id"]) != spec.effect_id
                or str(existing["graph_id"]) != spec.graph_id
                or int(existing["graph_revision"]) != spec.graph_revision
                or str(existing["node_id"]) != spec.node_id
                or str(existing["attempt_id"]) != spec.attempt_id
                or str(existing["commit_scope"]) != spec.commit_scope.value
                or str(existing["effect_type"]) != spec.effect_type
                or str(existing["payload_ref"]) != spec.payload_ref
                or str(existing["payload_json"]) != payload_json
                or str(existing["payload_sha256"]) != spec.payload_sha256
                or str(existing["idempotency_key"]) != spec.idempotency_key
            )
            if drift:
                raise TaskStoreError(
                    TASK_STORE_OUTBOX_CONFLICT,
                    f"acceptance effect replay drift: {spec.effect_id}",
                )
            replay = _acceptance_effect_record(existing)
            replay["replayed"] = True
            return replay

        if not allow_insert:
            raise TaskStoreError(
                TASK_STORE_OUTBOX_CONFLICT,
                f"atomic acceptance replay is missing effect intent: {spec.effect_id}",
            )
        artifact_schema = conn.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'task_runtime_node_artifact'
            """
        ).fetchone()
        if (
            artifact_schema is not None
            and spec.commit_scope is AcceptanceEffectCommitScope.NODE
            and str(node["status"]) == TaskNodeStatus.ACCEPTED.value
        ):
            raise TaskStoreError(
                TASK_STORE_OUTBOX_CONFLICT,
                "a NODE effect cannot be added after its TaskNode was ACCEPTED",
            )
        if (
            artifact_schema is not None
            and spec.commit_scope is AcceptanceEffectCommitScope.GRAPH
            and str(graph["status"]) == TaskGraphStatus.COMPLETED.value
            and not allow_terminal_graph_insert
        ):
            raise TaskStoreError(
                TASK_STORE_OUTBOX_CONFLICT,
                "a GRAPH effect cannot be added after its TaskGraph was COMPLETED",
            )
        if frozen_by_success:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                f"parallel success is missing its frozen effect intent: {spec.effect_id}",
            )

        with canonical_task_acceptance_effect_staging_scope(
            effect_id=spec.effect_id,
            graph_id=spec.graph_id,
            graph_revision=spec.graph_revision,
            node_id=spec.node_id,
            attempt_id=spec.attempt_id,
            commit_scope=spec.commit_scope.value,
            idempotency_key=spec.idempotency_key,
        ):
            conn.execute(
                """
                INSERT INTO task_runtime_acceptance_effect_intent(
                    effect_id, graph_id, graph_revision, node_id, attempt_id,
                    commit_scope, effect_type, payload_ref, payload_json,
                    payload_sha256, idempotency_key,
                    status, available_ts_ms, fencing_token, attempt_count,
                    status_version, created_ts_ms, updated_ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, 0, 0, 0, ?, ?)
                """,
                (
                    spec.effect_id,
                    spec.graph_id,
                    spec.graph_revision,
                    spec.node_id,
                    spec.attempt_id,
                    spec.commit_scope.value,
                    spec.effect_type,
                    spec.payload_ref,
                    payload_json,
                    spec.payload_sha256,
                    spec.idempotency_key,
                    int(ts_ms),
                    int(ts_ms),
                    int(ts_ms),
                ),
            )
        created = _acceptance_effect_record(
            self._require_acceptance_effect_intent(conn, spec.effect_id)
        )
        created["replayed"] = False
        return created
