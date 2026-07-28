"""DecisionRecord persistence and Knowledge proposal binding for NE-06."""

from __future__ import annotations

import json
from typing import Any

from host_brain.contracts.evolution import DecisionRecordV1

from .evolution_activation_v2_evidence import (
    bounded_text,
    canonical_json,
)


class EvolutionActivationDecisionMixin:
    """Own immutable ADR registration without activating Knowledge directly."""

    def register_decision_record(
        self,
        record: DecisionRecordV1,
    ) -> dict[str, str]:
        """Atomically persist one ADR and its Knowledge OS proposal."""

        if not isinstance(record, DecisionRecordV1):
            self._invalid("EVOLUTION_DECISION_RECORD_V1_INVALID")
        encoded = canonical_json(record.to_dict())
        with self._transaction() as conn:
            existing = conn.execute(
                """
                SELECT decision_digest, record_json, knowledge_entry_id
                FROM evolution_decision_record_v1
                WHERE decision_id = ?
                """,
                (record.decision_id,),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["decision_digest"])
                    != record.decision_digest
                    or str(existing["record_json"]) != encoded
                ):
                    self._invalid(
                        "EVOLUTION_DECISION_RECORD_REPLAY_DRIFT"
                    )
                entry = conn.execute(
                    """
                    SELECT entry_digest
                    FROM evolution_knowledge_entry_v2
                    WHERE entry_id = ?
                    """,
                    (existing["knowledge_entry_id"],),
                ).fetchone()
                if entry is None:
                    self._invalid(
                        "EVOLUTION_DECISION_KNOWLEDGE_LINK_MISSING"
                    )
                return {
                    "decision_digest": record.decision_digest,
                    "entry_id": str(existing["knowledge_entry_id"]),
                    "entry_digest": str(entry["entry_digest"]),
                }

            previous_entry_id = ""
            if record.supersedes:
                previous = conn.execute(
                    """
                    SELECT workspace_id, decision_key, version,
                           knowledge_entry_id
                    FROM evolution_decision_record_v1
                    WHERE decision_id = ?
                    """,
                    (record.supersedes,),
                ).fetchone()
                if (
                    previous is None
                    or str(previous["workspace_id"])
                    != record.workspace_id
                    or str(previous["decision_key"])
                    != record.decision_key
                    or int(previous["version"]) + 1 != record.version
                ):
                    self._invalid(
                        "EVOLUTION_DECISION_PREDECESSOR_DRIFT"
                    )
                previous_entry_id = str(
                    previous["knowledge_entry_id"]
                )
            entry = record.to_knowledge_entry(
                previous_entry_id=previous_entry_id
            )
            if conn.execute(
                """
                SELECT 1 FROM evolution_knowledge_entry_v2
                WHERE entry_id = ?
                """,
                (entry.entry_id,),
            ).fetchone() is not None:
                self._invalid(
                    "EVOLUTION_DECISION_KNOWLEDGE_ID_COLLISION"
                )
            duplicate = conn.execute(
                """
                SELECT entry_id
                FROM evolution_knowledge_entry_v2
                WHERE workspace_id = ? AND namespace = ?
                  AND logical_key = ? AND content_digest = ?
                LIMIT 1
                """,
                (
                    entry.workspace_id,
                    entry.namespace,
                    entry.logical_key,
                    entry.content_digest,
                ),
            ).fetchone()
            if duplicate is not None:
                self._invalid(
                    "EVOLUTION_KNOWLEDGE_DUPLICATE_CONTENT",
                    str(duplicate["entry_id"]),
                )
            entry_json = canonical_json(entry.to_dict())
            conn.execute(
                """
                INSERT INTO evolution_knowledge_entry_v2(
                    entry_id, entry_digest, namespace, logical_key,
                    workspace_id, owner_id, content_summary,
                    content_digest, provenance_ref, version, validity,
                    confidence, expires_at_ms, conflict_refs_json,
                    decision_refs_json,
                    supersedes, deprecated, entry_json, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.entry_id,
                    entry.entry_digest,
                    entry.namespace,
                    entry.logical_key,
                    entry.workspace_id,
                    entry.owner_id,
                    entry.content_summary,
                    entry.content_digest,
                    entry.provenance_ref,
                    entry.version,
                    entry.validity,
                    entry.confidence,
                    entry.expires_at_ms,
                    canonical_json(list(entry.conflict_refs)),
                    canonical_json(list(entry.decision_refs)),
                    entry.supersedes,
                    int(entry.deprecated),
                    entry_json,
                    int(self._clock()),
                ),
            )
            conn.execute(
                """
                INSERT INTO evolution_decision_record_v1(
                    decision_id, decision_digest, workspace_id, owner_id,
                    decision_key, version, status, supersedes,
                    knowledge_entry_id, record_json, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.decision_id,
                    record.decision_digest,
                    record.workspace_id,
                    record.owner_id,
                    record.decision_key,
                    record.version,
                    record.status,
                    record.supersedes,
                    entry.entry_id,
                    encoded,
                    int(self._clock()),
                ),
            )
        return {
            "decision_digest": record.decision_digest,
            "entry_id": entry.entry_id,
            "entry_digest": entry.entry_digest,
        }

    def decision_record(
        self,
        decision_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT record_json, knowledge_entry_id
                FROM evolution_decision_record_v1
                WHERE decision_id = ?
                """,
                (bounded_text(decision_id, "decision_id", 1_024),),
            ).fetchone()
        if row is None:
            return None
        result = json.loads(str(row["record_json"]))
        result["knowledge_entry_id"] = str(
            row["knowledge_entry_id"]
        )
        return result


__all__ = ["EvolutionActivationDecisionMixin"]
