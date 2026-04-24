"""Policies for Codex self-evolve experiments."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EvolutionBranchPolicy:
    allowed_prefixes: tuple[str, ...] = ("codex/evolve-", "evolution/")

    def validate(self, branch_name: str) -> tuple[bool, str | None]:
        normalized = str(branch_name or "").strip()
        if not normalized:
            return False, "EVOLUTION_BRANCH_REQUIRED"
        if any(normalized.startswith(prefix) for prefix in self.allowed_prefixes):
            return True, None
        return False, "EVOLUTION_EXPERIMENT_BRANCH_REQUIRED"


def validate_evolution_branch(branch_name: str, *, policy: EvolutionBranchPolicy | None = None) -> tuple[bool, str | None]:
    return (policy or EvolutionBranchPolicy()).validate(branch_name)
