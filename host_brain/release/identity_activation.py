"""Release-chain adapter for the NE-06 Identity activation authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from host_brain.contracts.evolution import IdentityMigrationPlanV2
from host_brain.evolution.version_activation import (
    EvolutionVersionActivationService,
)
from host_brain.release.identity_snapshot import (
    IdentityDiffSeverity,
    IdentitySnapshot,
    diff_identity_snapshots,
)


class IdentityReleaseActivationError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


@dataclass(frozen=True, slots=True)
class IdentityReleasePreparation:
    status: str
    migration_id: str
    workspace_id: str
    previous_snapshot_ref: str
    proposed_snapshot_ref: str
    diff_ref: str
    diff_digest: str
    diff_severity: str
    regression_report_digest: str
    activation_version: int
    plan_digest: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "natsume.identity-release-preparation.v1",
            "status": self.status,
            "migration_id": self.migration_id,
            "workspace_id": self.workspace_id,
            "previous_snapshot_ref": self.previous_snapshot_ref,
            "proposed_snapshot_ref": self.proposed_snapshot_ref,
            "diff_ref": self.diff_ref,
            "diff_digest": self.diff_digest,
            "diff_severity": self.diff_severity,
            "regression_report_digest": (
                self.regression_report_digest
            ),
            "activation_version": self.activation_version,
            "plan_digest": self.plan_digest,
        }


class IdentityReleaseActivationService:
    """Prepare, activate and roll back the exact release snapshot owner."""

    def __init__(
        self,
        *,
        version_activation: EvolutionVersionActivationService,
    ) -> None:
        if not isinstance(
            version_activation,
            EvolutionVersionActivationService,
        ):
            raise TypeError(
                "version_activation must be "
                "EvolutionVersionActivationService"
            )
        self._activation = version_activation

    def backfill_previous_release(
        self,
        *,
        workspace_id: str,
        snapshot: IdentitySnapshot,
        activation_version: int,
        idempotency_key: str,
    ):
        self._activation.register_identity_snapshot(
            snapshot,
            workspace_id=workspace_id,
        )
        return self._activation.backfill_identity(
            workspace_id=workspace_id,
            snapshot_ref=snapshot.snapshot_ref,
            activation_version=activation_version,
            migration_ref=(
                "evolution-migration://0142/"
                "identity-release-baseline"
            ),
            idempotency_key=idempotency_key,
        )

    def prepare_release(
        self,
        *,
        migration_id: str,
        workspace_id: str,
        previous: IdentitySnapshot,
        proposed: IdentitySnapshot,
        activation_version: int,
        regression_checks: Mapping[str, bool],
        allow_major_difference: bool = False,
        lab_report_digest: str = "",
        review_id: str = "",
        review_status_version: int = 0,
    ) -> IdentityReleasePreparation:
        previous.validate()
        proposed.validate()
        if proposed.previous_release_ref != previous.release_ref:
            raise IdentityReleaseActivationError(
                "IDENTITY_RELEASE_PREVIOUS_REF_DRIFT"
            )
        active = self._activation.active_identity(
            workspace_id=workspace_id
        )
        if (
            active is None
            or str(active["snapshot"]["snapshot_ref"])
            != previous.snapshot_ref
        ):
            raise IdentityReleaseActivationError(
                "IDENTITY_RELEASE_ACTIVE_PREVIOUS_DRIFT"
            )
        self._activation.register_identity_snapshot(
            previous,
            workspace_id=workspace_id,
        )
        self._activation.register_identity_snapshot(
            proposed,
            workspace_id=workspace_id,
        )
        diff = diff_identity_snapshots(previous, proposed)
        checks = {
            str(name): bool(value)
            for name, value in regression_checks.items()
        }
        if not checks:
            raise IdentityReleaseActivationError(
                "IDENTITY_RELEASE_REGRESSION_CHECKS_REQUIRED"
            )
        if diff.severity is IdentityDiffSeverity.MAJOR:
            checks["major_difference_approved"] = bool(
                allow_major_difference
            )
        regression = self._activation.register_identity_regression(
            report_id=f"{migration_id}:regression",
            workspace_id=workspace_id,
            previous_snapshot_ref=previous.snapshot_ref,
            proposed_snapshot_ref=proposed.snapshot_ref,
            checks=checks,
        )
        if not bool(regression["passed"]):
            return IdentityReleasePreparation(
                status="BLOCKED_MAJOR_DIFFERENCE"
                if diff.severity is IdentityDiffSeverity.MAJOR
                else "BLOCKED_REGRESSION",
                migration_id=migration_id,
                workspace_id=workspace_id,
                previous_snapshot_ref=previous.snapshot_ref,
                proposed_snapshot_ref=proposed.snapshot_ref,
                diff_ref=str(regression["diff_ref"]),
                diff_digest=str(regression["diff_digest"]),
                diff_severity=diff.severity.value,
                regression_report_digest=str(
                    regression["report_digest"]
                ),
                activation_version=activation_version,
            )
        preparation = IdentityReleasePreparation(
            status="AWAITING_LAB_REVIEW",
            migration_id=migration_id,
            workspace_id=workspace_id,
            previous_snapshot_ref=previous.snapshot_ref,
            proposed_snapshot_ref=proposed.snapshot_ref,
            diff_ref=str(regression["diff_ref"]),
            diff_digest=str(regression["diff_digest"]),
            diff_severity=diff.severity.value,
            regression_report_digest=str(
                regression["report_digest"]
            ),
            activation_version=activation_version,
        )
        supplied = (
            bool(str(lab_report_digest or "").strip()),
            bool(str(review_id or "").strip()),
            int(review_status_version) > 0,
        )
        if not any(supplied):
            return preparation
        if not all(supplied):
            raise IdentityReleaseActivationError(
                "IDENTITY_RELEASE_AUTHORITY_REFS_INCOMPLETE"
            )
        return self.bind_release_authorities(
            preparation,
            previous=previous,
            proposed=proposed,
            lab_report_digest=lab_report_digest,
            review_id=review_id,
            review_status_version=review_status_version,
        )

    def bind_release_authorities(
        self,
        preparation: IdentityReleasePreparation,
        *,
        previous: IdentitySnapshot,
        proposed: IdentitySnapshot,
        lab_report_digest: str,
        review_id: str,
        review_status_version: int,
    ) -> IdentityReleasePreparation:
        if preparation.status != "AWAITING_LAB_REVIEW":
            raise IdentityReleaseActivationError(
                "IDENTITY_RELEASE_NOT_AWAITING_AUTHORITY"
            )
        diff = diff_identity_snapshots(previous, proposed)
        if (
            preparation.previous_snapshot_ref
            != previous.snapshot_ref
            or preparation.proposed_snapshot_ref
            != proposed.snapshot_ref
            or preparation.diff_ref != diff.diff_ref
        ):
            raise IdentityReleaseActivationError(
                "IDENTITY_RELEASE_PREPARATION_DRIFT"
            )
        plan = IdentityMigrationPlanV2(
            migration_id=preparation.migration_id,
            workspace_id=preparation.workspace_id,
            previous_snapshot_ref=previous.snapshot_ref,
            proposed_snapshot_ref=proposed.snapshot_ref,
            previous_snapshot_digest=_identity_digest(previous),
            proposed_snapshot_digest=_identity_digest(proposed),
            diff_digest=preparation.diff_digest,
            lab_report_digest=lab_report_digest,
            regression_report_digest=(
                preparation.regression_report_digest
            ),
            review_id=review_id,
            review_status_version=review_status_version,
            rollback_snapshot_ref=previous.snapshot_ref,
            activation_version=preparation.activation_version,
            no_change_release=(
                previous.identity_digest == proposed.identity_digest
            ),
        )
        plan_digest = self._activation.register_identity_plan(plan)
        return IdentityReleasePreparation(
            status="READY_FOR_ACTIVATION",
            migration_id=preparation.migration_id,
            workspace_id=preparation.workspace_id,
            previous_snapshot_ref=previous.snapshot_ref,
            proposed_snapshot_ref=proposed.snapshot_ref,
            diff_ref=preparation.diff_ref,
            diff_digest=preparation.diff_digest,
            diff_severity=diff.severity.value,
            regression_report_digest=(
                preparation.regression_report_digest
            ),
            activation_version=preparation.activation_version,
            plan_digest=plan_digest,
        )

    def activate_prepared_release(
        self,
        preparation: IdentityReleasePreparation,
        *,
        expected_generation: int,
        idempotency_key: str,
    ):
        if preparation.status != "READY_FOR_ACTIVATION":
            raise IdentityReleaseActivationError(
                "IDENTITY_RELEASE_NOT_READY"
            )
        return self._activation.activate_identity(
            workspace_id=preparation.workspace_id,
            migration_id=preparation.migration_id,
            expected_generation=expected_generation,
            idempotency_key=idempotency_key,
        )

    def rollback_release(
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
        return self._activation.rollback_identity(
            workspace_id=workspace_id,
            migration_id=migration_id,
            lab_report_digest=lab_report_digest,
            review_id=review_id,
            review_status_version=review_status_version,
            expected_generation=expected_generation,
            idempotency_key=idempotency_key,
        )


def _identity_digest(snapshot: IdentitySnapshot) -> str:
    value = str(snapshot.identity_digest or "").strip().lower()
    return value.removeprefix("sha256:")


__all__ = [
    "IdentityReleaseActivationError",
    "IdentityReleaseActivationService",
    "IdentityReleasePreparation",
]
