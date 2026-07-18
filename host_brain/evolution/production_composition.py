"""Explicit application-local composition for NE-06 governance writes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from host_brain.persistence.evolution_governance_ledger import (
    EvolutionGovernanceLedgerError,
    SQLiteEvolutionGovernanceLedger,
)


@dataclass(frozen=True, slots=True)
class EvolutionGovernanceComposition:
    """A durable proposal owner; no direct Persona/Knowledge/Identity writer."""

    enabled: bool
    reason_code: str
    ledger: SQLiteEvolutionGovernanceLedger | None = None

    def close(self) -> None:
        # The repository opens short-lived SQLite connections per operation.
        return None


def build_evolution_governance_composition(
    *,
    db_path: str | Path,
) -> EvolutionGovernanceComposition:
    """Construct the NE-06 ledger only when migration 0110 is ready."""

    try:
        ledger = SQLiteEvolutionGovernanceLedger(db_path)
    except EvolutionGovernanceLedgerError as exc:
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
        reason_code="EVOLUTION_GOVERNANCE_COMPOSITION_ENFORCE",
        ledger=ledger,
    )


__all__ = [
    "EvolutionGovernanceComposition",
    "build_evolution_governance_composition",
]
