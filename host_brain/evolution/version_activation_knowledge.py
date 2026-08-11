"""Knowledge OS activation operations for the NE-06 public service."""

from __future__ import annotations

from typing import Any

from host_brain.contracts.evolution import (
    DecisionRecordV1,
    KnowledgeEntryV2,
)


class EvolutionVersionKnowledgeMixin:
    """Expose governed Knowledge registration, cutover and retirement."""

    def register_knowledge(self, entry: KnowledgeEntryV2) -> str:
        return self._mutate(
            workspace_id=entry.workspace_id,
            subject_kind="KNOWLEDGE",
            subject_id=entry.entry_id,
            operation="REGISTER_KNOWLEDGE",
            callback=lambda: self._repository.register_knowledge(entry),
        )

    def register_decision_record(
        self,
        record: DecisionRecordV1,
    ) -> dict[str, str]:
        return self._mutate(
            workspace_id=record.workspace_id,
            subject_kind="KNOWLEDGE",
            subject_id=record.decision_id,
            operation="REGISTER_DECISION_RECORD",
            callback=lambda: (
                self._repository.register_decision_record(record)
            ),
        )

    def decision_record(
        self,
        decision_id: str,
    ) -> dict[str, Any] | None:
        return self._repository.decision_record(decision_id)

    def activate_knowledge(
        self,
        *,
        workspace_id: str,
        entry_id: str,
        lab_report_digest: str,
        review_id: str,
        review_status_version: int,
        expected_generation: int,
        idempotency_key: str,
    ):
        return self._mutate(
            workspace_id=workspace_id,
            subject_kind="KNOWLEDGE",
            subject_id=entry_id,
            operation="ACTIVATE_KNOWLEDGE",
            callback=lambda: self._repository.activate_knowledge(
                entry_id=entry_id,
                lab_report_digest=lab_report_digest,
                review_id=review_id,
                review_status_version=review_status_version,
                expected_generation=expected_generation,
                idempotency_key=idempotency_key,
            ),
        )

    def backfill_knowledge(
        self,
        *,
        workspace_id: str,
        entry_id: str,
        migration_ref: str,
        idempotency_key: str,
    ):
        return self._mutate(
            workspace_id=workspace_id,
            subject_kind="KNOWLEDGE",
            subject_id=entry_id,
            operation="BACKFILL_KNOWLEDGE",
            callback=lambda: (
                self._repository.backfill_knowledge_projection(
                    entry_id=entry_id,
                    migration_ref=migration_ref,
                    idempotency_key=idempotency_key,
                )
            ),
        )

    def deprecate_knowledge(
        self,
        *,
        workspace_id: str,
        entry_id: str,
        lab_report_digest: str,
        review_id: str,
        review_status_version: int,
        expected_generation: int,
        idempotency_key: str,
    ):
        return self._mutate(
            workspace_id=workspace_id,
            subject_kind="KNOWLEDGE",
            subject_id=entry_id,
            operation="DEPRECATE_KNOWLEDGE",
            callback=lambda: self._repository.deprecate_knowledge(
                entry_id=entry_id,
                lab_report_digest=lab_report_digest,
                review_id=review_id,
                review_status_version=review_status_version,
                expected_generation=expected_generation,
                idempotency_key=idempotency_key,
            ),
        )

    def active_knowledge_page(self, **kwargs: Any) -> dict[str, Any]:
        return self._repository.active_knowledge_page(**kwargs)

    def search_active_knowledge(
        self,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        return self._repository.search_active_knowledge(**kwargs)


__all__ = ["EvolutionVersionKnowledgeMixin"]
