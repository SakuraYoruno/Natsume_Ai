"""Immutable TaskNode output artifacts and dependency resolution."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from host_brain.contracts.agent_runtime.acceptance_judge import (
    AcceptanceGate,
    AcceptanceJudgement,
)
from host_brain.contracts.agent_runtime.contract_errors import ContractViolation
from host_brain.contracts.agent_runtime.node_artifact import (
    NODE_ARTIFACT_SCHEMA_VERSION,
    NodeArtifactContractError,
    artifact_digest,
    build_node_artifact_payload,
    build_task_result_artifact_ref,
    canonical_artifact_json,
    is_task_result_artifact_ref,
    normalize_output_contract,
    validate_node_artifact_payload,
)
from host_brain.contracts.agent_runtime.task_graph import TaskNodeStatus
from host_brain.persistence.task_record_codec import (
    _loads_object,
    _node_record,
    _validated_node_payload,
)
from host_brain.persistence.task_store_types import (
    AttemptStatus,
    OutboxMessage,
    TASK_STORE_DURABLE_DATA_INVALID,
    TASK_STORE_INVALID,
    TASK_STORE_NOT_FOUND,
    TASK_STORE_NODE_ARTIFACT_RECONCILIATION_REQUIRED,
    TASK_STORE_SCHEMA_NOT_READY,
    TaskStoreError,
)


_NODE_ARTIFACT_REQUIRED_COLUMNS = frozenset(
    {
        "artifact_ref",
        "schema_version",
        "graph_id",
        "graph_revision",
        "node_id",
        "attempt_id",
        "receipt_ref",
        "output_contract_json",
        "output_contract_sha256",
        "payload_json",
        "payload_sha256",
        "created_ts_ms",
    }
)
_NODE_ARTIFACT_SCHEMA_METADATA_SHA256 = (
    "38e1ffe4fd66bfec9ba4149b5aa6217767decd207378d385d80d22c5012397d6"
)
TASK_NODE_ARTIFACT_MIGRATION_ASSET_KEY = "0102#task_runtime_node_artifact"
TASK_NODE_ARTIFACT_MIGRATION_SHA256 = (
    "cd99a30d8e118d117ef4f79a08395cec17e909120487dfeba8d01ef77650f8f4"
)
_ALLOWED_MIGRATION_DISPOSITIONS = frozenset(
    {"APPLIED", "APPLIED_WITH_COMPAT", "LEGACY_BACKFILL"}
)


class TaskNodeArtifactStoreMixin:
    """Persist accepted output and resolve successor input in canonical SQLite."""

    def assert_node_artifact_ready(
        self,
        *,
        force_revalidate: bool = False,
    ) -> None:
        """Verify 0102 once per Store; explicit diagnostics may rescan it."""

        if (
            not force_revalidate
            and getattr(self, "_node_artifact_schema_ready", False)
        ):
            return
        self._node_artifact_schema_ready = False
        conn = self._connect()
        try:
            migration_asset = conn.execute(
                """
                SELECT checksum, disposition FROM schema_migration_assets
                WHERE asset_key = ?
                """,
                (TASK_NODE_ARTIFACT_MIGRATION_ASSET_KEY,),
            ).fetchone()
            table_rows = conn.execute(
                "PRAGMA table_info(task_runtime_node_artifact)"
            ).fetchall()
            columns = {str(row[1]) for row in table_rows}
            if columns >= _NODE_ARTIFACT_REQUIRED_COLUMNS:
                snapshot = self._node_artifact_schema_snapshot(conn, table_rows)
                schema_digest = hashlib.sha256(
                    json.dumps(
                        snapshot,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
            else:
                schema_digest = ""
            if schema_digest == _NODE_ARTIFACT_SCHEMA_METADATA_SHA256:
                self._assert_no_unmaterialized_active_artifacts(conn)
                self._assert_no_deliverable_effect_without_artifact(conn)
        except sqlite3.DatabaseError as exc:
            raise TaskStoreError(
                TASK_STORE_SCHEMA_NOT_READY,
                "task runtime node artifact schema cannot be verified",
            ) from exc
        finally:
            conn.close()
        missing = sorted(_NODE_ARTIFACT_REQUIRED_COLUMNS - columns)
        if missing:
            raise TaskStoreError(
                TASK_STORE_SCHEMA_NOT_READY,
                "task runtime node artifact migration 0102 is not applied: "
                f"{missing}",
            )
        if schema_digest != _NODE_ARTIFACT_SCHEMA_METADATA_SHA256:
            raise TaskStoreError(
                TASK_STORE_SCHEMA_NOT_READY,
                "task runtime node artifact schema metadata/FK/index/trigger checksum drifted",
            )
        if (
            migration_asset is None
            or str(migration_asset["checksum"])
            != TASK_NODE_ARTIFACT_MIGRATION_SHA256
            or str(migration_asset["disposition"])
            not in _ALLOWED_MIGRATION_DISPOSITIONS
        ):
            raise TaskStoreError(
                TASK_STORE_SCHEMA_NOT_READY,
                "task runtime node artifact migration asset is missing or drifted",
            )
        self._node_artifact_schema_ready = True

    def commit_node_acceptance(
        self,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        *,
        expected_version: int,
        outbox: OutboxMessage,
        acceptance_judgement: Mapping[str, Any] | Any,
        attempt_id: str,
        receipt_ref: str,
        worker_output: Mapping[str, Any],
        effect_intents: Sequence[Mapping[str, Any] | Any] = (),
    ) -> dict[str, Any]:
        """Commit the complete Node Acceptance authority in one UoW.

        The immutable artifact is mandatory.  NODE-scoped Acceptance Effects
        are optional, but when supplied their *complete* set is frozen by the
        same transaction.  Exact response-loss replay validates every durable
        lineage rather than inserting a missing part after Acceptance.
        """

        self.assert_node_artifact_ready()
        self.assert_node_acceptance_ready()
        specs = self._normalize_optional_node_acceptance_effects(effect_intents)
        if specs:
            self.assert_acceptance_effect_intent_ready()
        ts_ms = self._now()
        graph_ref = str(graph_id or "").strip()
        node_ref = str(node_id or "").strip()
        attempt_ref = str(attempt_id or "").strip()
        receipt = str(receipt_ref or "").strip()
        if (
            not graph_ref
            or type(graph_revision) is not int
            or graph_revision < 1
            or not node_ref
            or not attempt_ref
            or not receipt
            or not isinstance(worker_output, Mapping)
        ):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "node artifact acceptance identity is incomplete",
            )
        for spec in specs:
            if (
                spec.graph_id != graph_ref
                or spec.graph_revision != graph_revision
                or spec.node_id != node_ref
                or spec.attempt_id != attempt_ref
                or spec.commit_scope.value != "NODE"
            ):
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "Node Acceptance effect must bind the exact Graph, Node, Attempt and NODE scope",
                )
        with self._transaction() as conn:
            self._assert_graph_not_cancelling(
                conn,
                graph_id=graph_ref,
                graph_revision=graph_revision,
            )
            current = self._require_node(
                conn,
                graph_ref,
                graph_revision,
                node_ref,
            )
            node_payload = _validated_node_payload(current)
            output_contract = self._artifact_output_contract(node_payload)
            artifact_payload = self._artifact_payload(
                worker_output,
                receipt_ref=receipt,
                output_contract=output_contract,
            )
            self._assert_artifact_attempt_and_receipt(
                conn,
                graph_id=graph_ref,
                graph_revision=graph_revision,
                node_id=node_ref,
                attempt_id=attempt_ref,
                receipt_ref=receipt,
            )
            if (
                int(current["status_version"]) == int(expected_version) + 1
                and str(current["status"]) == TaskNodeStatus.ACCEPTED.value
            ):
                judgement = self._validate_acceptance_judgement(
                    conn,
                    graph_id=graph_ref,
                    graph_revision=graph_revision,
                    node_id=node_ref,
                    node_payload=node_payload,
                    acceptance_judgement=acceptance_judgement,
                    outbox=outbox,
                )
                if judgement.attempt_id != attempt_ref:
                    raise TaskStoreError(
                        TASK_STORE_INVALID,
                        "AcceptanceJudgement must bind the exact Attempt",
                    )
                decision_row = self._find_node_acceptance_decision(
                    conn,
                    graph_id=graph_ref,
                    graph_revision=graph_revision,
                    node_id=node_ref,
                    node_version_after=int(expected_version) + 1,
                )
                if decision_row is None:
                    raise TaskStoreError(
                        TASK_STORE_DURABLE_DATA_INVALID,
                        "ACCEPTED TaskNode has no durable Acceptance decision",
                    )
                decision = self._node_acceptance_record(conn, decision_row)
                self._assert_acceptance_replay(
                    decision,
                    judgement=judgement,
                    target=TaskNodeStatus.ACCEPTED,
                    attempt_id=attempt_ref,
                    node_outbox=outbox,
                    review_id=None,
                    graph_outbox=None,
                )
                self._assert_mutation_outbox_replay(
                    conn,
                    outbox,
                    expected_aggregate=(
                        "TASK_NODE",
                        f"{graph_ref}@{graph_revision}/{node_ref}",
                        int(expected_version) + 1,
                    ),
                    expected_event_type="TASK_NODE_ACCEPTED",
                    expected_attempt_id=attempt_ref,
                )
                artifact = self._require_valid_node_artifact(
                    conn,
                    graph_id=graph_ref,
                    graph_revision=graph_revision,
                    node_id=node_ref,
                    expected_attempt_id=attempt_ref,
                    expected_receipt_ref=receipt,
                    expected_output_contract=output_contract,
                    expected_payload=artifact_payload,
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
                self._assert_exact_node_acceptance_effect_set(
                    conn,
                    graph_id=graph_ref,
                    graph_revision=graph_revision,
                    node_id=node_ref,
                    attempt_id=attempt_ref,
                    expected_effect_ids=tuple(spec.effect_id for spec in specs),
                )
                return {
                    "node": _node_record(current),
                    "artifact": artifact,
                    "effect_intents": staged,
                    "decision": decision,
                    "replayed": True,
                }

            judgement = self._validate_acceptance_judgement(
                conn,
                graph_id=graph_ref,
                graph_revision=graph_revision,
                node_id=node_ref,
                node_payload=node_payload,
                acceptance_judgement=acceptance_judgement,
                outbox=outbox,
            )
            if judgement.attempt_id != attempt_ref:
                raise TaskStoreError(
                    TASK_STORE_INVALID,
                    "AcceptanceJudgement must bind the exact Attempt",
                )
            staged = tuple(
                self._stage_acceptance_effect_intent_in_transaction(
                    conn,
                    spec,
                    ts_ms=ts_ms,
                    require_succeeded_attempt=True,
                )
                for spec in specs
            )
            if any(str(item.get("status") or "") != "PENDING" for item in staged):
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "new Node Acceptance cannot adopt an already dispatched effect",
                )
            self._insert_node_artifact_in_transaction(
                conn,
                graph_id=graph_ref,
                graph_revision=graph_revision,
                node_id=node_ref,
                attempt_id=attempt_ref,
                receipt_ref=receipt,
                output_contract=output_contract,
                artifact_payload=artifact_payload,
                ts_ms=ts_ms,
            )
            node = self._transition_node_in_transaction(
                conn,
                graph_id=graph_ref,
                graph_revision=graph_revision,
                node_id=node_ref,
                expected_version=expected_version,
                target_status=TaskNodeStatus.ACCEPTED,
                outbox=outbox,
                verification_result=None,
                acceptance_judgement=acceptance_judgement,
                ts_ms=ts_ms,
                outbox_attempt_id=attempt_ref,
            )
            decision = self._insert_node_acceptance_decision_in_transaction(
                conn,
                graph_id=graph_ref,
                graph_revision=graph_revision,
                node_id=node_ref,
                attempt_id=attempt_ref,
                node_version_before=int(expected_version),
                target=TaskNodeStatus.ACCEPTED,
                judgement=judgement,
                node_outbox=outbox,
                review_id=None,
                graph_version_before=None,
                graph_version_after=None,
                graph_outbox_id=None,
                ts_ms=ts_ms,
            )
            artifact = self._require_valid_node_artifact(
                conn,
                graph_id=graph_ref,
                graph_revision=graph_revision,
                node_id=node_ref,
                expected_attempt_id=attempt_ref,
                expected_receipt_ref=receipt,
                expected_output_contract=output_contract,
                expected_payload=artifact_payload,
            )
            self._assert_exact_node_acceptance_effect_set(
                conn,
                graph_id=graph_ref,
                graph_revision=graph_revision,
                node_id=node_ref,
                attempt_id=attempt_ref,
                expected_effect_ids=tuple(spec.effect_id for spec in specs),
            )
            return {
                "node": node,
                "artifact": artifact,
                "effect_intents": staged,
                "decision": decision,
                "replayed": False,
            }

    def _normalize_optional_node_acceptance_effects(
        self,
        values: Sequence[Mapping[str, Any] | Any],
    ) -> tuple[Any, ...]:
        if isinstance(values, (str, bytes, bytearray, Mapping)):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "effect_intents must be a sequence",
            )
        try:
            materialized = tuple(values)
        except TypeError as exc:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "effect_intents must be a sequence",
            ) from exc
        if not materialized:
            return ()
        return tuple(self._normalize_acceptance_effect_intent_specs(materialized))

    @staticmethod
    def _insert_node_artifact_in_transaction(
        conn: sqlite3.Connection,
        *,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        attempt_id: str,
        receipt_ref: str,
        output_contract: Mapping[str, Any],
        artifact_payload: Mapping[str, Any],
        ts_ms: int,
    ) -> AcceptanceJudgement:
        artifact_ref = build_task_result_artifact_ref(
            graph_id,
            graph_revision,
            node_id,
        )
        try:
            conn.execute(
                """
                INSERT INTO task_runtime_node_artifact(
                    artifact_ref, schema_version, graph_id, graph_revision,
                    node_id, attempt_id, receipt_ref,
                    output_contract_json, output_contract_sha256,
                    payload_json, payload_sha256, created_ts_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_ref,
                    NODE_ARTIFACT_SCHEMA_VERSION,
                    graph_id,
                    graph_revision,
                    node_id,
                    attempt_id,
                    receipt_ref,
                    canonical_artifact_json(output_contract),
                    artifact_digest(output_contract),
                    canonical_artifact_json(artifact_payload),
                    artifact_digest(artifact_payload),
                    int(ts_ms),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "node artifact identity already exists with conflicting output",
            ) from exc

    @staticmethod
    def _assert_exact_node_acceptance_effect_set(
        conn: sqlite3.Connection,
        *,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        attempt_id: str,
        expected_effect_ids: Sequence[str],
    ) -> None:
        expected = tuple(str(item) for item in expected_effect_ids)
        if len(expected) != len(set(expected)):
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "Node Acceptance effect set contains duplicate effect identities",
            )
        rows = conn.execute(
            """
            SELECT effect_id, attempt_id
            FROM task_runtime_acceptance_effect_intent
            WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
              AND attempt_id = ? AND commit_scope = 'NODE'
            ORDER BY effect_id
            """,
            (graph_id, graph_revision, node_id, attempt_id),
        ).fetchall()
        actual = tuple(str(row["effect_id"]) for row in rows)
        if (
            set(actual) != set(expected)
            or len(actual) != len(expected)
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "durable Node Acceptance effect set differs from the exact Attempt lineage",
            )

    def get_node_artifact(
        self,
        graph_id: str,
        graph_revision: int,
        node_id: str,
    ) -> dict[str, Any]:
        self.assert_node_artifact_ready()
        conn = self._connect()
        try:
            return self._require_valid_node_artifact(
                conn,
                graph_id=str(graph_id),
                graph_revision=int(graph_revision),
                node_id=str(node_id),
            )
        finally:
            conn.close()

    def resolve_node_input_artifacts(
        self,
        graph_id: str,
        graph_revision: int,
        node_id: str,
    ) -> list[dict[str, Any]]:
        """Resolve only exact predecessor artifacts declared by the child."""

        self.assert_node_artifact_ready()
        graph_ref = str(graph_id or "").strip()
        child_ref = str(node_id or "").strip()
        revision = int(graph_revision)
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            child_row = self._require_node(conn, graph_ref, revision, child_ref)
            child = _validated_node_payload(child_row)
            dependencies = tuple(str(item) for item in child.get("dependencies") or ())
            input_refs = tuple(str(item) for item in child.get("input_refs") or ())
            artifact_refs = tuple(
                item for item in input_refs if is_task_result_artifact_ref(item)
            )
            if not artifact_refs:
                conn.commit()
                return []
            expected = tuple(
                build_task_result_artifact_ref(graph_ref, revision, dependency)
                for dependency in dependencies
            )
            if len(set(artifact_refs)) != len(artifact_refs) or set(artifact_refs) != set(
                expected
            ):
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "TaskNode result refs must bind every exact predecessor in the same graph revision",
                )
            resolved = [
                self._require_valid_node_artifact(
                    conn,
                    graph_id=graph_ref,
                    graph_revision=revision,
                    node_id=dependency,
                )
                for dependency in dependencies
            ]
            conn.commit()
            return resolved
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _node_artifact_schema_snapshot(
        conn: sqlite3.Connection,
        table_rows: Sequence[sqlite3.Row],
    ) -> dict[str, Any]:
        table_sql_row = conn.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'table' AND name = 'task_runtime_node_artifact'
            """
        ).fetchone()
        indexes: list[list[Any]] = []
        for row in conn.execute(
            "PRAGMA index_list(task_runtime_node_artifact)"
        ).fetchall():
            name = str(row[1])
            escaped_name = name.replace('"', '""')
            index_columns = [
                str(column[2])
                for column in conn.execute(
                    f'PRAGMA index_info("{escaped_name}")'
                ).fetchall()
            ]
            indexes.append(
                [name, int(row[2]), str(row[3]), int(row[4]), index_columns]
            )
        triggers = [
            [str(row[0]), " ".join(str(row[1] or "").split())]
            for row in conn.execute(
                """
                SELECT name, sql FROM sqlite_master
                WHERE type = 'trigger' AND tbl_name = 'task_runtime_node_artifact'
                ORDER BY name
                """
            ).fetchall()
        ]
        foreign_keys = sorted(
            [
                [
                    str(row[2]),
                    str(row[3]),
                    str(row[4]),
                    str(row[5]),
                    str(row[6]),
                    str(row[7]),
                ]
                for row in conn.execute(
                    "PRAGMA foreign_key_list(task_runtime_node_artifact)"
                ).fetchall()
            ]
        )
        return {
            "table_sql": " ".join(
                str(table_sql_row[0] if table_sql_row is not None else "").split()
            ),
            "columns": [
                [
                    str(row[1]),
                    str(row[2]),
                    int(row[3]),
                    row[4],
                    int(row[5]),
                ]
                for row in table_rows
            ],
            "foreign_keys": foreign_keys,
            "indexes": sorted(indexes),
            "triggers": triggers,
        }

    def _assert_no_unmaterialized_active_artifacts(
        self,
        conn: sqlite3.Connection,
    ) -> None:
        """Fence upgraded active Graphs that predate the artifact authority."""

        graph_rows = conn.execute(
            """
            SELECT graph_id, revision FROM task_runtime_graph
            WHERE status IN ('CREATED', 'ACTIVE', 'BLOCKED')
            ORDER BY graph_id, revision
            """
        ).fetchall()
        for graph_row in graph_rows:
            graph_id = str(graph_row["graph_id"])
            revision = int(graph_row["revision"])
            node_rows = conn.execute(
                """
                SELECT * FROM task_runtime_node
                WHERE graph_id = ? AND graph_revision = ?
                ORDER BY node_id
                """,
                (graph_id, revision),
            ).fetchall()
            for node_row in node_rows:
                payload = _validated_node_payload(node_row)
                node_status = str(node_row["status"])
                if node_status not in {
                    TaskNodeStatus.FAILED.value,
                    TaskNodeStatus.CANCEL_REQUESTED.value,
                    TaskNodeStatus.CANCELLED.value,
                }:
                    try:
                        normalize_output_contract(payload.get("output_contract"))
                    except NodeArtifactContractError as exc:
                        raise TaskStoreError(
                            TASK_STORE_NODE_ARTIFACT_RECONCILIATION_REQUIRED,
                            "active TaskGraph has a TaskNode without a canonical "
                            "output_contract and must be reconciled before execution: "
                            f"{graph_id}@{revision}/{node_row['node_id']}: {exc}",
                        ) from exc
                input_refs = tuple(str(item) for item in payload.get("input_refs") or ())
                dependencies = tuple(
                    str(item) for item in payload.get("dependencies") or ()
                )
                artifact_refs = tuple(
                    ref for ref in input_refs if is_task_result_artifact_ref(ref)
                )
                if artifact_refs:
                    expected_refs = {
                        build_task_result_artifact_ref(
                            graph_id,
                            revision,
                            dependency,
                        )
                        for dependency in dependencies
                    }
                    if (
                        len(set(artifact_refs)) != len(artifact_refs)
                        or set(artifact_refs) != expected_refs
                    ):
                        raise TaskStoreError(
                            TASK_STORE_DURABLE_DATA_INVALID,
                            "active TaskGraph contains cross-lineage result artifact refs",
                        )
                if node_status != TaskNodeStatus.ACCEPTED.value:
                    continue
                exists = conn.execute(
                    """
                    SELECT 1 FROM task_runtime_node_artifact
                    WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
                    """,
                    (graph_id, revision, str(node_row["node_id"])),
                ).fetchone()
                if exists is None:
                    raise TaskStoreError(
                        TASK_STORE_NODE_ARTIFACT_RECONCILIATION_REQUIRED,
                        "active TaskGraph has an ACCEPTED TaskNode without its 0102 artifact: "
                        f"{graph_id}@{revision}/{node_row['node_id']}",
                    )

    @staticmethod
    def _assert_no_deliverable_effect_without_artifact(
        conn: sqlite3.Connection,
    ) -> None:
        """Quarantine upgraded terminal Effect rows that 0102 cannot dispatch."""

        effect_schema = conn.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table'
              AND name = 'task_runtime_acceptance_effect_intent'
            """
        ).fetchone()
        if effect_schema is None:
            return
        orphan = conn.execute(
            """
            SELECT effect.effect_id, effect.graph_id, effect.graph_revision,
                   effect.node_id, effect.attempt_id
            FROM task_runtime_acceptance_effect_intent AS effect
            JOIN task_runtime_attempt AS attempt
              ON attempt.attempt_id = effect.attempt_id
             AND attempt.graph_id = effect.graph_id
             AND attempt.graph_revision = effect.graph_revision
             AND attempt.node_id = effect.node_id
            JOIN task_runtime_node AS node
              ON node.graph_id = effect.graph_id
             AND node.graph_revision = effect.graph_revision
             AND node.node_id = effect.node_id
            JOIN task_runtime_graph AS graph_row
              ON graph_row.graph_id = effect.graph_id
             AND graph_row.revision = effect.graph_revision
            LEFT JOIN task_runtime_node_artifact AS artifact
              ON artifact.graph_id = effect.graph_id
             AND artifact.graph_revision = effect.graph_revision
             AND artifact.node_id = effect.node_id
             AND artifact.attempt_id = effect.attempt_id
            WHERE effect.status IN ('PENDING', 'CLAIMED')
              AND attempt.status = 'SUCCEEDED'
              AND node.status = 'ACCEPTED'
              AND (
                effect.commit_scope = 'NODE'
                OR (
                  effect.commit_scope = 'GRAPH'
                  AND graph_row.status = 'COMPLETED'
                )
              )
              AND artifact.artifact_ref IS NULL
            ORDER BY effect.created_ts_ms, effect.effect_id
            LIMIT 1
            """
        ).fetchone()
        if orphan is not None:
            raise TaskStoreError(
                TASK_STORE_NODE_ARTIFACT_RECONCILIATION_REQUIRED,
                "deliverable Acceptance Effect has no exact 0102 artifact and "
                "must be reconciled before dispatch: "
                f"{orphan['effect_id']} -> {orphan['graph_id']}@"
                f"{orphan['graph_revision']}/{orphan['node_id']}/"
                f"{orphan['attempt_id']}",
            )

    @staticmethod
    def _artifact_output_contract(node_payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            return normalize_output_contract(node_payload.get("output_contract"))
        except NodeArtifactContractError as exc:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                f"accepted TaskNode has no canonical output_contract: {exc}",
            ) from exc

    @staticmethod
    def _artifact_payload(
        worker_output: Mapping[str, Any],
        *,
        receipt_ref: str,
        output_contract: Mapping[str, Any],
    ) -> dict[str, Any]:
        try:
            return build_node_artifact_payload(
                worker_output,
                receipt_ref=receipt_ref,
                output_contract=output_contract,
            )
        except NodeArtifactContractError as exc:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                f"worker output violates TaskNode.output_contract: {exc}",
            ) from exc

    def _validate_acceptance_judgement(
        self,
        conn: sqlite3.Connection,
        *,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        node_payload: Mapping[str, Any],
        acceptance_judgement: Mapping[str, Any] | Any,
        outbox: OutboxMessage,
    ) -> None:
        graph = self._require_graph(conn, graph_id, graph_revision)
        try:
            validated = AcceptanceGate.validate_node_transition(
                acceptance_judgement,
                target_status=TaskNodeStatus.ACCEPTED.value,
                graph_id=graph_id,
                node_id=node_id,
                root_work_order_id=str(graph["root_work_order_id"]),
                node_acceptance_criteria=tuple(
                    node_payload.get("acceptance_criteria") or ()
                ),
                outbox_evidence_refs=outbox.evidence_refs,
            )
        except ContractViolation as exc:
            raise TaskStoreError(exc.reason_code, exc.detail) from exc
        if validated is None:
            raise TaskStoreError(
                TASK_STORE_INVALID,
                "ACCEPTED transition lost its AcceptanceJudgement",
            )
        return validated

    @staticmethod
    def _assert_artifact_attempt_and_receipt(
        conn: sqlite3.Connection,
        *,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        attempt_id: str,
        receipt_ref: str,
    ) -> None:
        attempt = conn.execute(
            """
            SELECT * FROM task_runtime_attempt
            WHERE attempt_id = ? AND graph_id = ? AND graph_revision = ?
              AND node_id = ?
            """,
            (attempt_id, graph_id, graph_revision, node_id),
        ).fetchone()
        if attempt is None or str(attempt["status"]) != AttemptStatus.SUCCEEDED.value:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "node artifact requires the exact SUCCEEDED TaskAttempt",
            )
        reservations = conn.execute(
            """
            SELECT reservation_id FROM task_runtime_idempotency_reservation
            WHERE attempt_id = ? AND status = 'COMMITTED' AND receipt_ref = ?
            ORDER BY reservation_id
            """,
            (attempt_id, receipt_ref),
        ).fetchall()
        if len(reservations) != 1:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "node artifact requires one exact committed Operation receipt",
            )

    def _require_valid_node_artifact(
        self,
        conn: sqlite3.Connection,
        *,
        graph_id: str,
        graph_revision: int,
        node_id: str,
        expected_attempt_id: str | None = None,
        expected_receipt_ref: str | None = None,
        expected_output_contract: Mapping[str, Any] | None = None,
        expected_payload: Mapping[str, Any] | None = None,
        allow_pre_acceptance_owner: bool = False,
    ) -> dict[str, Any]:
        row = conn.execute(
            """
            SELECT * FROM task_runtime_node_artifact
            WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
            """,
            (graph_id, graph_revision, node_id),
        ).fetchone()
        if row is None:
            raise TaskStoreError(
                TASK_STORE_NOT_FOUND,
                f"TaskNode artifact not found: {graph_id}@{graph_revision}/{node_id}",
            )
        record = dict(row)
        expected_ref = build_task_result_artifact_ref(
            graph_id,
            graph_revision,
            node_id,
        )
        if (
            str(row["artifact_ref"]) != expected_ref
            or str(row["schema_version"]) != NODE_ARTIFACT_SCHEMA_VERSION
            or str(row["graph_id"]) != graph_id
            or int(row["graph_revision"]) != graph_revision
            or str(row["node_id"]) != node_id
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "TaskNode artifact identity is not canonical",
            )
        node_row = self._require_node(conn, graph_id, graph_revision, node_id)
        node_payload = _validated_node_payload(node_row)
        if (
            str(node_row["status"]) != TaskNodeStatus.ACCEPTED.value
            and not allow_pre_acceptance_owner
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "TaskNode artifact owner is not ACCEPTED",
            )
        output_contract = self._artifact_output_contract(node_payload)
        durable_contract = _loads_object(
            row["output_contract_json"],
            field_name="task_runtime_node_artifact.output_contract_json",
        )
        payload = _loads_object(
            row["payload_json"],
            field_name="task_runtime_node_artifact.payload_json",
        )
        contract_digest = artifact_digest(durable_contract)
        payload_digest = artifact_digest(payload)
        if (
            not hmac.compare_digest(
                str(row["output_contract_sha256"]),
                contract_digest,
            )
            or not hmac.compare_digest(str(row["payload_sha256"]), payload_digest)
            or canonical_artifact_json(durable_contract)
            != canonical_artifact_json(output_contract)
        ):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "TaskNode artifact contract or payload digest drifted",
            )
        try:
            payload = validate_node_artifact_payload(
                payload,
                receipt_ref=str(row["receipt_ref"]),
                output_contract=output_contract,
            )
        except NodeArtifactContractError as exc:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                f"TaskNode artifact payload is invalid: {exc}",
            ) from exc
        attempt_id = str(row["attempt_id"])
        receipt_ref = str(row["receipt_ref"])
        self._assert_artifact_attempt_and_receipt(
            conn,
            graph_id=graph_id,
            graph_revision=graph_revision,
            node_id=node_id,
            attempt_id=attempt_id,
            receipt_ref=receipt_ref,
        )
        if expected_attempt_id is not None and attempt_id != expected_attempt_id:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "TaskNode artifact Attempt differs on replay",
            )
        if expected_receipt_ref is not None and receipt_ref != expected_receipt_ref:
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "TaskNode artifact receipt differs on replay",
            )
        if expected_output_contract is not None and canonical_artifact_json(
            output_contract
        ) != canonical_artifact_json(expected_output_contract):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "TaskNode artifact output contract differs on replay",
            )
        if expected_payload is not None and canonical_artifact_json(
            payload
        ) != canonical_artifact_json(expected_payload):
            raise TaskStoreError(
                TASK_STORE_DURABLE_DATA_INVALID,
                "TaskNode artifact payload differs on replay",
            )
        record["output_contract"] = durable_contract
        record["payload"] = payload
        record.pop("output_contract_json", None)
        record.pop("payload_json", None)
        return record


__all__ = [
    "TASK_NODE_ARTIFACT_MIGRATION_ASSET_KEY",
    "TASK_NODE_ARTIFACT_MIGRATION_SHA256",
    "TaskNodeArtifactStoreMixin",
]
