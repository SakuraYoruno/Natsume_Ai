"""Explicit application-local composition for NE-06 V2 authority."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from host_brain.persistence.evolution_governance_ledger import (
    EvolutionGovernanceLedgerError,
    SQLiteEvolutionGovernanceLedger,
)
from host_brain.persistence.evolution_activation_v2_evidence import (
    EvolutionActivationV2Error,
)
from host_brain.persistence.evolution_activation_v2_repository import (
    SQLiteEvolutionActivationV2Repository,
)
from host_brain.release.identity_activation import (
    IdentityReleaseActivationService,
)

from .version_activation import EvolutionVersionActivationService


@dataclass(frozen=True, slots=True)
class EvolutionGovernanceComposition:
    """V2 activation owner plus a read-only V1 history projection."""

    enabled: bool
    reason_code: str
    ledger: SQLiteEvolutionGovernanceLedger | None = None
    repository: SQLiteEvolutionActivationV2Repository | None = None
    activation_service: EvolutionVersionActivationService | None = None
    identity_release_service: IdentityReleaseActivationService | None = None

    def close(self) -> None:
        # The repository opens short-lived SQLite connections per operation.
        return None


def build_evolution_governance_composition(
    *,
    db_path: str | Path,
) -> EvolutionGovernanceComposition:
    """Construct V2 only when 0142 and the V1 read-only fence are ready."""

    try:
        repository = SQLiteEvolutionActivationV2Repository(db_path)
        activation_service = EvolutionVersionActivationService(repository)
        identity_release_service = IdentityReleaseActivationService(
            version_activation=activation_service,
        )
        legacy_query = SQLiteEvolutionGovernanceLedger(db_path)
    except (
        EvolutionActivationV2Error,
        EvolutionGovernanceLedgerError,
    ) as exc:
        return EvolutionGovernanceComposition(
            enabled=False,
            reason_code=exc.args[0] if exc.args else "EVOLUTION_GOVERNANCE_COMPOSITION_DISABLED",
        )
    except Exception:
        return EvolutionGovernanceComposition(
            enabled=False,
            reason_code="EVOLUTION_GOVERNANCE_COMPOSITION_CONSTRUCTION_FAILED",
        )
    return EvolutionGovernanceComposition(
        enabled=True,
        reason_code="EVOLUTION_GOVERNANCE_V2_COMPOSITION_ENFORCE",
        ledger=legacy_query,
        repository=repository,
        activation_service=activation_service,
        identity_release_service=identity_release_service,
    )


__all__ = [
    "EvolutionGovernanceComposition",
    "build_evolution_governance_composition",
]
