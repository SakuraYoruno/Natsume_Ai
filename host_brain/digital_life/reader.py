"""Pure read facade for Digital Life snapshots and journals."""

from __future__ import annotations

from natsume_contracts.life import (
    LifeCommandReceipt,
    LifeDeliveryReceipt,
    LifeEvent,
    LifeOutboxMessage,
    LifeProjectionIntent,
    LifeProjectionReceipt,
    LifeSnapshot,
)
from host_brain.digital_life.repository_port import LifeCommandRepositoryPort


class LifeSnapshotReader:
    def __init__(self, repository: LifeCommandRepositoryPort) -> None:
        self.repository = repository

    def get_snapshot(self, person_id: str) -> LifeSnapshot:
        return self.repository.get_snapshot(person_id)

    def list_events(
        self,
        person_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> list[LifeEvent]:
        return self.repository.list_events(
            person_id,
            after_sequence=after_sequence,
            limit=limit,
        )

    def list_affective_event_heads(
        self,
        person_id: str,
        *,
        after_created_at_ms: int = 0,
        after_event_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, object]]:
        """Read the bounded DL-01 projection without becoming a writer."""

        return self.repository.list_affective_event_heads(
            person_id,
            after_created_at_ms=after_created_at_ms,
            after_event_id=after_event_id,
            limit=limit,
        )

    def list_relationship_event_heads(
        self,
        person_id: str,
        *,
        after_created_at_ms: int = 0,
        after_event_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, object]]:
        """Read the DL-03 relationship projection without becoming a writer."""

        return self.repository.list_relationship_event_heads(
            person_id,
            after_created_at_ms=after_created_at_ms,
            after_event_id=after_event_id,
            limit=limit,
        )

    def list_companion_profile_event_heads(
        self,
        person_id: str,
        *,
        after_created_at_ms: int = 0,
        after_event_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, object]]:
        """Read the DL-06 profile lifecycle projection without text or media."""

        return self.repository.list_companion_profile_event_heads(
            person_id,
            after_created_at_ms=after_created_at_ms,
            after_event_id=after_event_id,
            limit=limit,
        )

    def list_affective_turn_delivery_event_heads(
        self,
        person_id: str,
        *,
        after_created_at_ms: int = 0,
        after_event_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, object]]:
        """Read privacy-minimised DL-04 plan/sentence delivery receipts."""

        return self.repository.list_affective_turn_delivery_event_heads(
            person_id,
            after_created_at_ms=after_created_at_ms,
            after_event_id=after_event_id,
            limit=limit,
        )

    def list_affective_expression_delivery_event_heads(
        self,
        person_id: str,
        *,
        after_created_at_ms: int = 0,
        after_event_id: str = "",
        after_delivery_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, object]]:
        """Read privacy-minimised DL-05 Host expression delivery heads."""

        return self.repository.list_affective_expression_delivery_event_heads(
            person_id,
            after_created_at_ms=after_created_at_ms,
            after_event_id=after_event_id,
            after_delivery_id=after_delivery_id,
            limit=limit,
        )

    def last_affective_expression_delivery_head(
        self,
        person_id: str,
        *,
        workspace_id: str,
        relationship_subject_id: str,
        session_id: str,
    ) -> dict[str, object] | None:
        """Return the exact-scope durable Host avatar-transition head.

        The row records protocol staging, not physical device application.
        """

        return self.repository.last_affective_expression_delivery_head(
            person_id,
            workspace_id=workspace_id,
            relationship_subject_id=relationship_subject_id,
            session_id=session_id,
        )

    def list_companion_proactive_targets(
        self,
        *,
        limit: int = 1_000,
    ) -> list[dict[str, object]]:
        """List currently eligible, scope-valid canonical companion targets.

        The registry is reconstructed from durable Life projections rather
        than an in-process queue.  This read has no authority to create,
        alter or infer a target or relationship permission.
        """

        return self.repository.list_companion_proactive_targets(limit=limit)

    def get_receipt(self, command_id: str) -> LifeCommandReceipt | None:
        return self.repository.get_receipt(command_id)

    def get_delivery_receipt(
        self,
        outbox_id: str,
    ) -> LifeDeliveryReceipt | None:
        return self.repository.get_delivery_receipt(outbox_id)

    def list_completed_companion_proactive_messages(
        self,
        *,
        limit: int = 100,
    ) -> list[LifeOutboxMessage]:
        """Read only receipt-backed companion messages missing a relation event."""

        return self.repository.list_completed_companion_proactive_messages(
            limit=limit
        )

    def list_projection_intents(
        self,
        command_id: str,
    ) -> list[LifeProjectionIntent]:
        return self.repository.list_projection_intents(command_id)

    def get_projection_receipt(
        self,
        intent_id: str,
    ) -> LifeProjectionReceipt | None:
        return self.repository.get_projection_receipt(intent_id)

    def list_unadopted_projection_person_ids(self, *, limit: int = 1_000) -> list[str]:
        return self.repository.list_unadopted_projection_person_ids(limit=limit)


__all__ = ["LifeSnapshotReader"]
