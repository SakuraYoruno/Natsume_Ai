"""Knowledge OS registration, activation and read projection for NE-06."""

from __future__ import annotations

import json
from typing import Any, Mapping

from host_brain.contracts.evolution import (
    EvolutionTransitionReceiptV1,
    KnowledgeEntryV2,
    VersionActivationDecisionV1,
)

from .evolution_activation_v2_evidence import (
    bounded_text,
    canonical_json,
    knowledge_subject_ref,
    knowledge_target_kind,
    payload_digest,
    sha256_hex,
)


class EvolutionActivationKnowledgeMixin:
    """Own Knowledge entry lifecycle and active-winner queries."""

    def register_knowledge(self, entry: KnowledgeEntryV2) -> str:
        if not isinstance(entry, KnowledgeEntryV2):
            self._invalid("EVOLUTION_KNOWLEDGE_ENTRY_V2_INVALID")
        encoded = canonical_json(entry.to_dict())
        with self._transaction() as conn:
            existing = conn.execute(
                """
                SELECT *
                FROM evolution_knowledge_entry_v2
                WHERE entry_id = ?
                """,
                (entry.entry_id,),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["entry_digest"]) != entry.entry_digest
                    or str(existing["entry_json"]) != encoded
                ):
                    self._invalid(
                        "EVOLUTION_KNOWLEDGE_ENTRY_V2_REPLAY_DRIFT"
                    )
                return entry.entry_digest
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
            related = set(entry.conflict_refs)
            if entry.supersedes:
                related.add(entry.supersedes)
            for related_id in related:
                row = conn.execute(
                    """
                    SELECT workspace_id, namespace, logical_key
                    FROM evolution_knowledge_entry_v2
                    WHERE entry_id = ?
                    """,
                    (related_id,),
                ).fetchone()
                if (
                    row is None
                    or str(row["workspace_id"]) != entry.workspace_id
                    or str(row["namespace"]) != entry.namespace
                    or str(row["logical_key"]) != entry.logical_key
                ):
                    self._invalid(
                        "EVOLUTION_KNOWLEDGE_RELATED_ENTRY_DRIFT"
                    )
            conn.execute(
                """
                INSERT INTO evolution_knowledge_entry_v2(
                    entry_id, entry_digest, namespace, logical_key,
                    workspace_id, owner_id, content_summary, content_digest,
                    provenance_ref, version, validity, confidence,
                    expires_at_ms,
                    conflict_refs_json, decision_refs_json, supersedes,
                    deprecated, entry_json, created_at_ms
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
                    encoded,
                    int(self._clock()),
                ),
            )
        return entry.entry_digest

    def activate_knowledge(
        self,
        *,
        entry_id: str,
        lab_report_digest: str,
        review_id: str,
        review_status_version: int,
        expected_generation: int,
        idempotency_key: str,
    ) -> EvolutionTransitionReceiptV1:
        request = {
            "operation": "KNOWLEDGE_ACTIVATE",
            "entry_id": entry_id,
            "lab_report_digest": lab_report_digest,
            "review_id": review_id,
            "review_status_version": review_status_version,
            "expected_generation": expected_generation,
        }
        with self._transaction() as conn:
            replay = self._replay(conn, idempotency_key, request)
            if replay is not None:
                return replay
            entry = self._knowledge_entry(conn, entry_id)
            if (
                int(entry["deprecated"])
                or str(entry["validity"]) == "STALE"
                or (
                    int(entry["expires_at_ms"]) > 0
                    and int(entry["expires_at_ms"])
                    <= int(self._clock())
                )
            ):
                self._invalid("EVOLUTION_KNOWLEDGE_ENTRY_NOT_ACTIVATABLE")
            active = self._active_knowledge(conn, entry)
            generation = self._generation(
                active, expected_generation=expected_generation
            )
            previous_version = ""
            previous_digest = ""
            if active is not None:
                previous = self._knowledge_entry(
                    conn, str(active["entry_id"])
                )
                previous_version = str(previous["version"])
                previous_digest = str(previous["entry_digest"])
                if int(entry["version"]) <= int(previous["version"]):
                    self._invalid(
                        "EVOLUTION_KNOWLEDGE_VERSION_NOT_MONOTONIC"
                    )
                conflict_refs = set(
                    json.loads(str(entry["conflict_refs_json"]))
                )
                if (
                    str(entry["supersedes"]) != str(previous["entry_id"])
                    and str(previous["entry_id"]) not in conflict_refs
                ):
                    self._invalid(
                        "EVOLUTION_KNOWLEDGE_CONFLICT_UNRESOLVED"
                    )
            subject_ref = knowledge_subject_ref(
                entry_id=str(entry["entry_id"]),
                entry_digest=str(entry["entry_digest"]),
            )
            report, proposal, _review = self._resolve_lab_review(
                conn,
                workspace_id=str(entry["workspace_id"]),
                target_kind=knowledge_target_kind(
                    entry_id=str(entry["entry_id"])
                ),
                target_version=str(entry["version"]),
                lab_report_digest=lab_report_digest,
                review_id=review_id,
                review_status_version=review_status_version,
                review_subject_ref=subject_ref,
                review_subject_version=int(entry["version"]),
                required_evidence_refs={
                    (
                        "evolution-knowledge-entry://sha256/"
                        f"{str(entry['entry_digest'])}"
                    ),
                    str(entry["provenance_ref"]),
                    *set(json.loads(str(entry["decision_refs_json"]))),
                },
                now_ms=int(self._clock()),
            )
            operation_id, decision_id, receipt_id = self._identities(
                idempotency_key
            )
            projection = {
                "workspace_id": str(entry["workspace_id"]),
                "namespace": str(entry["namespace"]),
                "logical_key": str(entry["logical_key"]),
                "entry_id": str(entry["entry_id"]),
                "entry_digest": str(entry["entry_digest"]),
                "activation_generation": generation,
            }
            projection_digest = payload_digest(projection)
            conn.execute(
                """
                INSERT INTO evolution_knowledge_active_projection_v2(
                    workspace_id, namespace, logical_key, entry_id,
                    entry_digest, activation_generation, projection_digest,
                    updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workspace_id, namespace, logical_key) DO UPDATE SET
                    entry_id = excluded.entry_id,
                    entry_digest = excluded.entry_digest,
                    activation_generation = excluded.activation_generation,
                    projection_digest = excluded.projection_digest,
                    updated_at_ms = excluded.updated_at_ms
                WHERE evolution_knowledge_active_projection_v2.activation_generation
                    = excluded.activation_generation - 1
                """,
                (
                    entry["workspace_id"],
                    entry["namespace"],
                    entry["logical_key"],
                    entry["entry_id"],
                    entry["entry_digest"],
                    generation,
                    projection_digest,
                    int(self._clock()),
                ),
            )
            if conn.execute("SELECT changes()").fetchone()[0] != 1:
                self._invalid(
                    "EVOLUTION_KNOWLEDGE_ACTIVE_PROJECTION_CAS_FAILED"
                )
            decision = VersionActivationDecisionV1(
                decision_id=decision_id,
                operation_id=operation_id,
                workspace_id=str(entry["workspace_id"]),
                subject_kind="KNOWLEDGE",
                subject_id=str(entry["entry_id"]),
                action="ACTIVATE",
                target_version=str(entry["version"]),
                target_digest=str(entry["entry_digest"]),
                previous_version=previous_version,
                previous_digest=previous_digest,
                authority_refs=(
                    f"evolution-lab-report://sha256/{report['report_digest']}",
                    f"evolution-proposal://sha256/{proposal['proposal_digest']}",
                    f"review://{review_id}",
                    (
                        "evolution-knowledge-entry://sha256/"
                        f"{entry['entry_digest']}"
                    ),
                ),
                expected_generation=generation - 1,
            )
            return self._append_terminal(
                conn,
                decision=decision,
                request=request,
                idempotency_key=idempotency_key,
                receipt_id=receipt_id,
                event="ACTIVATED",
                generation=generation,
                projection_digest=projection_digest,
            )

    def backfill_knowledge_projection(
        self,
        *,
        entry_id: str,
        migration_ref: str,
        idempotency_key: str,
    ) -> EvolutionTransitionReceiptV1:
        """Promote one referenced legacy entry during controlled migration."""

        request = {
            "operation": "KNOWLEDGE_BACKFILL",
            "entry_id": entry_id,
            "migration_ref": migration_ref,
        }
        with self._transaction() as conn:
            replay = self._replay(conn, idempotency_key, request)
            if replay is not None:
                return replay
            entry = self._knowledge_entry(conn, entry_id)
            if (
                int(entry["deprecated"])
                or str(entry["validity"]) not in {"VALID", "PROPOSED"}
                or (
                    int(entry["expires_at_ms"]) > 0
                    and int(entry["expires_at_ms"])
                    <= int(self._clock())
                )
            ):
                self._invalid(
                    "EVOLUTION_KNOWLEDGE_BACKFILL_ENTRY_INVALID"
                )
            active = self._active_knowledge(conn, entry)
            if active is not None:
                self._invalid(
                    "EVOLUTION_KNOWLEDGE_BACKFILL_ALREADY_EXISTS"
                )
            generation = 1
            projection = {
                "workspace_id": str(entry["workspace_id"]),
                "namespace": str(entry["namespace"]),
                "logical_key": str(entry["logical_key"]),
                "entry_id": str(entry["entry_id"]),
                "entry_digest": str(entry["entry_digest"]),
                "activation_generation": generation,
            }
            projection_digest = payload_digest(projection)
            conn.execute(
                """
                INSERT INTO evolution_knowledge_active_projection_v2(
                    workspace_id, namespace, logical_key, entry_id,
                    entry_digest, activation_generation, projection_digest,
                    updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry["workspace_id"],
                    entry["namespace"],
                    entry["logical_key"],
                    entry["entry_id"],
                    entry["entry_digest"],
                    generation,
                    projection_digest,
                    int(self._clock()),
                ),
            )
            operation_id, decision_id, receipt_id = self._identities(
                idempotency_key
            )
            decision = VersionActivationDecisionV1(
                decision_id=decision_id,
                operation_id=operation_id,
                workspace_id=str(entry["workspace_id"]),
                subject_kind="KNOWLEDGE",
                subject_id=str(entry["entry_id"]),
                action="ACTIVATE",
                target_version=str(entry["version"]),
                target_digest=str(entry["entry_digest"]),
                authority_refs=(
                    bounded_text(
                        migration_ref,
                        "migration_ref",
                        1_024,
                    ),
                    str(entry["provenance_ref"]),
                    (
                        "evolution-knowledge-entry://sha256/"
                        f"{entry['entry_digest']}"
                    ),
                ),
                expected_generation=0,
                reason_code="EVOLUTION_KNOWLEDGE_BACKFILL",
            )
            return self._append_terminal(
                conn,
                decision=decision,
                request=request,
                idempotency_key=idempotency_key,
                receipt_id=receipt_id,
                event="ACTIVATED",
                generation=generation,
                projection_digest=projection_digest,
            )

    def deprecate_knowledge(
        self,
        *,
        entry_id: str,
        lab_report_digest: str,
        review_id: str,
        review_status_version: int,
        expected_generation: int,
        idempotency_key: str,
    ) -> EvolutionTransitionReceiptV1:
        request = {
            "operation": "KNOWLEDGE_DEPRECATE",
            "entry_id": entry_id,
            "lab_report_digest": lab_report_digest,
            "review_id": review_id,
            "review_status_version": review_status_version,
            "expected_generation": expected_generation,
        }
        with self._transaction() as conn:
            replay = self._replay(conn, idempotency_key, request)
            if replay is not None:
                return replay
            entry = self._knowledge_entry(conn, entry_id)
            active = self._active_knowledge(conn, entry)
            generation = self._generation(
                active, expected_generation=expected_generation
            )
            if active is None or str(active["entry_id"]) != str(entry_id):
                self._invalid(
                    "EVOLUTION_KNOWLEDGE_DEPRECATION_SOURCE_DRIFT"
                )
            report, proposal, _review = self._resolve_lab_review(
                conn,
                workspace_id=str(entry["workspace_id"]),
                target_kind=knowledge_target_kind(
                    entry_id=str(entry["entry_id"])
                ),
                target_version=f"deprecate-{entry['version']}",
                lab_report_digest=lab_report_digest,
                review_id=review_id,
                review_status_version=review_status_version,
                review_subject_ref=knowledge_subject_ref(
                    entry_id=str(entry["entry_id"]),
                    entry_digest=str(entry["entry_digest"]),
                ),
                review_subject_version=generation,
                required_evidence_refs={
                    (
                        "evolution-knowledge-entry://sha256/"
                        f"{entry['entry_digest']}"
                    ),
                },
                now_ms=int(self._clock()),
            )
            tombstone = {
                "workspace_id": str(entry["workspace_id"]),
                "namespace": str(entry["namespace"]),
                "logical_key": str(entry["logical_key"]),
                "entry_id": str(entry["entry_id"]),
                "event": "DEPRECATED",
                "activation_generation": generation,
            }
            projection_digest = payload_digest(tombstone)
            conn.execute(
                """
                DELETE FROM evolution_knowledge_active_projection_v2
                WHERE workspace_id = ? AND namespace = ? AND logical_key = ?
                  AND entry_id = ? AND activation_generation = ?
                """,
                (
                    entry["workspace_id"],
                    entry["namespace"],
                    entry["logical_key"],
                    entry["entry_id"],
                    generation - 1,
                ),
            )
            if conn.execute("SELECT changes()").fetchone()[0] != 1:
                self._invalid(
                    "EVOLUTION_KNOWLEDGE_ACTIVE_PROJECTION_CAS_FAILED"
                )
            operation_id, decision_id, receipt_id = self._identities(
                idempotency_key
            )
            decision = VersionActivationDecisionV1(
                decision_id=decision_id,
                operation_id=operation_id,
                workspace_id=str(entry["workspace_id"]),
                subject_kind="KNOWLEDGE",
                subject_id=str(entry["entry_id"]),
                action="DEPRECATE",
                target_version=str(entry["version"]),
                target_digest=str(entry["entry_digest"]),
                previous_version=str(entry["version"]),
                previous_digest=str(entry["entry_digest"]),
                authority_refs=(
                    f"evolution-lab-report://sha256/{report['report_digest']}",
                    f"evolution-proposal://sha256/{proposal['proposal_digest']}",
                    f"review://{review_id}",
                    (
                        "evolution-knowledge-entry://sha256/"
                        f"{entry['entry_digest']}"
                    ),
                ),
                expected_generation=generation - 1,
            )
            return self._append_terminal(
                conn,
                decision=decision,
                request=request,
                idempotency_key=idempotency_key,
                receipt_id=receipt_id,
                event="DEPRECATED",
                generation=generation,
                projection_digest=projection_digest,
            )

    def active_knowledge_page(
        self,
        *,
        workspace_id: str,
        namespace: str,
        limit: int = 100,
        cursor: str = "",
    ) -> dict[str, Any]:
        workspace = bounded_text(workspace_id, "workspace_id", 128)
        scope = bounded_text(namespace, "namespace", 512)
        bounded_limit = int(limit)
        if bounded_limit < 1 or bounded_limit > 500:
            self._invalid("EVOLUTION_KNOWLEDGE_CURSOR_LIMIT_INVALID")
        scope_digest = sha256_hex(f"{workspace}|{scope}")
        after = self._decode_cursor(
            cursor, expected_scope_digest=scope_digest
        )
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT entry.entry_json, projection.logical_key,
                       projection.activation_generation,
                       projection.projection_digest
                FROM evolution_knowledge_active_projection_v2 AS projection
                JOIN evolution_knowledge_entry_v2 AS entry
                  ON entry.entry_id = projection.entry_id
                WHERE projection.workspace_id = ?
                  AND projection.namespace = ?
                  AND projection.logical_key > ?
                  AND (
                    entry.expires_at_ms = 0
                    OR entry.expires_at_ms > ?
                  )
                ORDER BY projection.logical_key ASC
                LIMIT ?
                """,
                (
                    workspace,
                    scope,
                    after,
                    int(self._clock()),
                    bounded_limit + 1,
                ),
            ).fetchall()
        has_more = len(rows) > bounded_limit
        selected = rows[:bounded_limit]
        items = [json.loads(str(row["entry_json"])) for row in selected]
        next_cursor = ""
        if has_more and selected:
            next_cursor = self._encode_cursor(
                scope_digest=scope_digest,
                last_key=str(selected[-1]["logical_key"]),
            )
        source = [
            {
                "entry_digest": item["entry_digest"],
                "logical_key": item["logical_key"],
            }
            for item in items
        ]
        return {
            "schema": "natsume.knowledge-active-page.v2",
            "workspace_id": workspace,
            "namespace": scope,
            "items": items,
            "next_cursor": next_cursor,
            "source_digest": payload_digest(source),
        }

    def search_active_knowledge(
        self,
        *,
        workspace_id: str,
        namespace: str,
        query: str,
        top_k: int,
        threshold: float,
    ) -> list[dict[str, Any]]:
        page = self.active_knowledge_page(
            workspace_id=workspace_id,
            namespace=namespace,
            limit=500,
        )
        terms = {
            term
            for term in bounded_text(query, "query", 4_000).lower().split()
            if term
        }
        results: list[dict[str, Any]] = []
        for item in page["items"]:
            haystack = (
                f"{item['logical_key']} {item['content_summary']}"
            ).lower()
            matched = sum(1 for term in terms if term in haystack)
            score = (
                1.0 if not terms and haystack else matched / max(1, len(terms))
            )
            if score < float(threshold):
                continue
            results.append(
                {
                    "doc_id": item["entry_id"],
                    "content": item["content_summary"],
                    "score": round(score, 4),
                    "source": "knowledge_os_v2",
                    "metadata": {
                        "namespace": item["namespace"],
                        "workspace_id": item["workspace_id"],
                        "provenance_ref": item["provenance_ref"],
                        "entry_digest": item["entry_digest"],
                    },
                }
            )
        results.sort(key=lambda row: (-float(row["score"]), row["doc_id"]))
        return results[: max(1, min(int(top_k), 100))]

    def record_knowledge_shadow_compare(
        self,
        *,
        workspace_id: str,
        namespace: str,
        query: str,
        active_results: list[Mapping[str, Any]],
        legacy_results: list[Mapping[str, Any]],
    ) -> str:
        timestamp = int(self._clock())
        active_digest = payload_digest(active_results)
        legacy_digest = payload_digest(legacy_results)
        payload = {
            "schema": "natsume.knowledge-shadow-compare.v1",
            "workspace_id": bounded_text(
                workspace_id, "workspace_id", 128
            ),
            "namespace": bounded_text(namespace, "namespace", 512),
            "query_digest": sha256_hex(str(query or "")),
            "active_result_digest": active_digest,
            "legacy_result_digest": legacy_digest,
            "active_count": len(active_results),
            "legacy_count": len(legacy_results),
            "created_at_ms": timestamp,
        }
        compare_id = (
            "knowledge-shadow://sha256/" + payload_digest(payload)
        )
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO evolution_knowledge_shadow_compare_v1(
                    compare_id, workspace_id, namespace, query_digest,
                    active_result_digest, legacy_result_digest,
                    active_count, legacy_count, compare_json, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    compare_id,
                    payload["workspace_id"],
                    payload["namespace"],
                    payload["query_digest"],
                    active_digest,
                    legacy_digest,
                    len(active_results),
                    len(legacy_results),
                    canonical_json(payload),
                    timestamp,
                ),
            )
        return compare_id

__all__ = ["EvolutionActivationKnowledgeMixin"]
