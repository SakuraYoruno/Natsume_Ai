"""Identity migration activation operations for the NE-06 public service."""

from __future__ import annotations

from typing import Any, Mapping

from host_brain.contracts.evolution import IdentityMigrationPlanV2
from host_brain.release.identity_snapshot import IdentitySnapshot


class EvolutionVersionIdentityMixin:
    """Expose snapshot registration, activation and exact rollback."""

    def register_identity_snapshot(
        self,
        snapshot: IdentitySnapshot | Mapping[str, Any],
        *,
        workspace_id: str,
    ) -> str:
        subject_id = (
            snapshot.snapshot_ref
            if isinstance(snapshot, IdentitySnapshot)
            else str(snapshot.get("snapshot_ref") or "identity")
        )
        return self._mutate(
            workspace_id=workspace_id,
            subject_kind="IDENTITY",
            subject_id=subject_id,
            operation="REGISTER_IDENTITY_SNAPSHOT",
            callback=lambda: self._repository.register_identity_snapshot(
                snapshot,
                workspace_id=workspace_id,
            ),
        )

    def backfill_identity(
        self,
        *,
        workspace_id: str,
        snapshot_ref: str,
        activation_version: int,
        migration_ref: str,
        idempotency_key: str,
    ):
        return self._mutate(
            workspace_id=workspace_id,
            subject_kind="IDENTITY",
            subject_id=snapshot_ref,
            operation="BACKFILL_IDENTITY",
            callback=lambda: (
                self._repository.backfill_identity_projection(
                    workspace_id=workspace_id,
                    snapshot_ref=snapshot_ref,
                    activation_version=activation_version,
                    migration_ref=migration_ref,
                    idempotency_key=idempotency_key,
                )
            ),
        )

    def register_identity_regression(
        self,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return self._mutate(
            workspace_id=str(kwargs.get("workspace_id") or ""),
            subject_kind="IDENTITY",
            subject_id=str(
                kwargs.get("proposed_snapshot_ref") or "identity"
            ),
            operation="REGISTER_IDENTITY_REGRESSION",
            callback=lambda: self._repository.register_identity_regression(
                **kwargs
            ),
        )

    def register_identity_plan(
        self,
        plan: IdentityMigrationPlanV2,
    ) -> str:
        return self._mutate(
            workspace_id=plan.workspace_id,
            subject_kind="IDENTITY",
            subject_id=plan.migration_id,
            operation="REGISTER_IDENTITY_PLAN",
            callback=lambda: self._repository.register_identity_plan(plan),
        )

    def activate_identity(
        self,
        *,
        workspace_id: str,
        migration_id: str,
        expected_generation: int,
        idempotency_key: str,
    ):
        return self._mutate(
            workspace_id=workspace_id,
            subject_kind="IDENTITY",
            subject_id=migration_id,
            operation="ACTIVATE_IDENTITY",
            callback=lambda: self._repository.activate_identity(
                migration_id=migration_id,
                expected_generation=expected_generation,
                idempotency_key=idempotency_key,
            ),
        )

    def rollback_identity(
        self,
        *,
        workspace_id: str,
        migration_id: str,
        lab_report_digest: str,
        review_id: str,
        review_status_version: int,
        expected_generation: int,
        idempotency_key: str,
    ):
        return self._mutate(
            workspace_id=workspace_id,
            subject_kind="IDENTITY",
            subject_id=migration_id,
            operation="ROLLBACK_IDENTITY",
            callback=lambda: self._repository.rollback_identity(
                migration_id=migration_id,
                lab_report_digest=lab_report_digest,
                review_id=review_id,
                review_status_version=review_status_version,
                expected_generation=expected_generation,
                idempotency_key=idempotency_key,
            ),
        )

    def active_identity(
        self,
        *,
        workspace_id: str,
    ) -> dict[str, Any] | None:
        return self._repository.active_identity(
            workspace_id=workspace_id
        )


__all__ = ["EvolutionVersionIdentityMixin"]
