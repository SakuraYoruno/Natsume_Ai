"""Legacy relationship projection boundaries for the canonical Life runtime.

The pre-DL relationship stack exposes ``SocialBrainAgent`` snapshots keyed by
raw person IDs.  Canonical relationship aggregates are workspace- and
subject-scoped, so a caller must never fill a retired SocialBrain response with
values copied from the old process-local registry.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


_RETIRED_STATUS = "RETIRED_CANONICAL_LIFE"
_CANONICAL_AUTHORITY = "CANONICAL_LIFE_JOURNAL"
_LEGACY_VITAL_RELATIONSHIP_FIELDS = frozenset(
    {
        "bond_stage",
        "bond_vector",
        "bond_trend_hint",
        "bond_repair_needed",
    }
)


def is_retired_legacy_relationship_projection(payload: object) -> bool:
    """Return whether a response explicitly retires the old Bond authority."""

    if not isinstance(payload, Mapping):
        return False
    return (
        str(payload.get("status") or "").strip().upper() == _RETIRED_STATUS
        and str(payload.get("authority") or "").strip().upper()
        == _CANONICAL_AUTHORITY
    )


def retired_relationship_projection_marker(payload: Mapping[str, Any]) -> dict[str, str]:
    """Build the only compatibility metadata safe to expose to Cortex.

    In particular, do not carry ``bond_stage``, vectors, intimacy or repair
    values forward.  They are presentation defaults in the legacy response,
    not facts from the workspace-scoped Life aggregate.
    """

    return {
        "status": _RETIRED_STATUS,
        "authority": _CANONICAL_AUTHORITY,
        "reason_code": str(
            payload.get("reason_code")
            or "LEGACY_SOCIAL_BRAIN_RELATIONSHIP_RETIRED"
        ).strip()
        or "LEGACY_SOCIAL_BRAIN_RELATIONSHIP_RETIRED",
    }


def without_legacy_relationship_vitals(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Remove old SocialBrain relationship facts from a mixed vital snapshot.

    ``TribeStateRegistry.aggregate_vital_signs()`` is still a compatibility
    aggregator for offline mode.  Once the caller has positively identified
    canonical Life authority, its Bond-derived fields must not survive inside
    that otherwise useful health snapshot.
    """

    return {
        str(key): value
        for key, value in payload.items()
        if str(key) not in _LEGACY_VITAL_RELATIONSHIP_FIELDS
    }


__all__ = [
    "is_retired_legacy_relationship_projection",
    "retired_relationship_projection_marker",
    "without_legacy_relationship_vitals",
]
