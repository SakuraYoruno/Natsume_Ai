"""Public identity-snapshot exports for the release package.

Release-program tooling is intentionally imported from its owning submodule
(``m48_seal``, ``m48b_entry_gate``, ``release_manifest`` or
``compatibility_registry``). Keeping those tools out of the package root
prevents importing an identity runtime from eagerly loading release-only
utilities.
"""

from .identity_snapshot import (
    IDENTITY_SNAPSHOT_FEATURE_FLAG,
    IDENTITY_SNAPSHOT_SCHEMA_VERSION,
    IdentityReleaseGate,
    IdentityReviewCandidate,
    IdentityRollbackPlan,
    IdentitySnapshot,
    IdentitySnapshotDiff,
    IdentitySourceRef,
    build_identity_rollback_plan,
    build_identity_snapshot,
    diff_identity_snapshots,
    evaluate_identity_release_gate,
)
__all__ = [
    "IDENTITY_SNAPSHOT_FEATURE_FLAG",
    "IDENTITY_SNAPSHOT_SCHEMA_VERSION",
    "IdentityReleaseGate",
    "IdentityReviewCandidate",
    "IdentityRollbackPlan",
    "IdentitySnapshot",
    "IdentitySnapshotDiff",
    "IdentitySourceRef",
    "build_identity_rollback_plan",
    "build_identity_snapshot",
    "diff_identity_snapshots",
    "evaluate_identity_release_gate",
]
