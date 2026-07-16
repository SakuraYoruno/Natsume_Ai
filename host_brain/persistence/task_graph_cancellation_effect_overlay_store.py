"""Trusted cancellation overlays backed by signed Effect proofs."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

from host_brain.persistence.task_decision_integrity import (
    _assert_graph_cancellation_decision_bindings,
)
from host_brain.persistence.task_store_types import (
    TASK_STORE_DURABLE_DATA_INVALID,
    TaskStoreError,
)


class TaskGraphCancellationEffectOverlayMixin:
    """Resolve cancellation readiness and decisions through trusted proofs."""

    def _assert_graph_cancellation_decision_trusted(
        self,
        conn: sqlite3.Connection,
        record: Mapping[str, Any],
    ) -> None:
        """Validate terminal cancellation state and every Effect proof overlay."""

        _assert_graph_cancellation_decision_bindings(
            conn,
            record,
            evidence_refs_builder=self._graph_cancellation_evidence_refs,
        )
        self._assert_graph_cancellation_effect_reconciliation_overlay(
            conn,
            record,
        )

    def _assert_graph_cancellation_effect_reconciliation_overlay(
        self,
        conn: sqlite3.Connection,
        record: Mapping[str, Any],
    ) -> None:
        """Fail closed unless immutable Effect uncertainty has trusted proof."""

        intent_ref = str(record["intent_ref"])
        evidence_refs = set(record["payload"]["reconciliation_evidence_refs"])
        cases = self._list_effect_reconciliation_cases_in_transaction(
            conn,
            intent_ref=intent_ref,
        )
        finalized_effects: set[str] = set()
        for case in cases:
            effect_id = str(case["effect_id"])
            classification = str(case["classification"])
            if classification == "QUARANTINED_STRUCTURAL":
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "finalized cancellation has a structurally quarantined Effect case",
                )
            if classification == "REPAIRABLE_REQUESTED":
                proof = self._read_validated_effect_reconciliation_proof_in_transaction(
                    conn,
                    intent_ref=intent_ref,
                    effect_id=effect_id,
                    purpose="PENDING_CANCELLATION",
                )
                if proof is None or str(proof["proof_id"]) not in evidence_refs:
                    raise TaskStoreError(
                        TASK_STORE_DURABLE_DATA_INVALID,
                        "finalized cancellation omits its signed pending Effect proof",
                    )
                continue
            if classification != "QUARANTINED_FINALIZED":
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "finalized cancellation Effect case classification is invalid",
                )
            if (
                str(case.get("legacy_decision_ref") or "")
                != str(record["decision_ref"])
                or str(case.get("legacy_decision_payload_sha256") or "")
                != str(record["payload_sha256"])
            ):
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "legacy Effect correction no longer binds the immutable decision",
                )
            proof = self._read_validated_effect_reconciliation_proof_in_transaction(
                conn,
                intent_ref=intent_ref,
                effect_id=effect_id,
                purpose="LEGACY_FINALIZED_CORRECTION",
            )
            if proof is None:
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "legacy finalized Effect quarantine lacks signed correction proof",
                )
            if str(proof["proof_id"]) in evidence_refs:
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "legacy correction proof must not rewrite old decision evidence",
                )
            finalized_effects.add(effect_id)

        unresolved_rows = conn.execute(
            """
            SELECT effect_id
            FROM task_runtime_graph_cancellation_effect_fence
            WHERE intent_ref = ?
              AND resolution_status IN (
                'RECONCILIATION_REQUIRED', 'FAILED_EVIDENCE'
              )
            ORDER BY effect_id
            """,
            (intent_ref,),
        ).fetchall()
        for row in unresolved_rows:
            effect_id = str(row["effect_id"])
            if effect_id in finalized_effects:
                continue
            proof = self._read_validated_effect_reconciliation_proof_in_transaction(
                conn,
                intent_ref=intent_ref,
                effect_id=effect_id,
                purpose="PENDING_CANCELLATION",
            )
            if proof is None or str(proof["proof_id"]) not in evidence_refs:
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "finalized cancellation still has unresolved Effect uncertainty",
                )

    def _graph_cancellation_pending_effect_ids(
        self,
        conn: sqlite3.Connection,
        *,
        intent_ref: str,
    ) -> tuple[str, ...]:
        """Resolve one REQUESTED intent through verified PENDING proof overlays."""

        pending: set[str] = set()
        cases = self._list_effect_reconciliation_cases_in_transaction(
            conn,
            intent_ref=str(intent_ref),
        )
        for case in cases:
            effect_id = str(case["effect_id"])
            classification = str(case["classification"])
            if classification == "QUARANTINED_STRUCTURAL":
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "requested cancellation has a structurally quarantined Effect case",
                )
            if classification != "REPAIRABLE_REQUESTED":
                raise TaskStoreError(
                    TASK_STORE_DURABLE_DATA_INVALID,
                    "requested cancellation contains a finalized Effect quarantine",
                )
            proof = self._read_validated_effect_reconciliation_proof_in_transaction(
                conn,
                intent_ref=str(intent_ref),
                effect_id=effect_id,
                purpose="PENDING_CANCELLATION",
            )
            if proof is None:
                pending.add(effect_id)

        uncertain_rows = conn.execute(
            """
            SELECT effect_id
            FROM task_runtime_graph_cancellation_effect_fence
            WHERE intent_ref = ?
              AND resolution_status IN (
                'RECONCILIATION_REQUIRED', 'FAILED_EVIDENCE'
              )
            ORDER BY effect_id
            """,
            (str(intent_ref),),
        ).fetchall()
        for row in uncertain_rows:
            effect_id = str(row["effect_id"])
            proof = self._read_validated_effect_reconciliation_proof_in_transaction(
                conn,
                intent_ref=str(intent_ref),
                effect_id=effect_id,
                purpose="PENDING_CANCELLATION",
            )
            if proof is None:
                pending.add(effect_id)
        return tuple(sorted(pending))


__all__ = ["TaskGraphCancellationEffectOverlayMixin"]
