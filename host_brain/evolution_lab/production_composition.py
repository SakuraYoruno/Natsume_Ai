"""Typed, app-local composition for promotion-eligible Evolution Lab runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from host_brain.evolution_lab.docker_executor import (
    TrustedDockerSandboxExecutor,
)
from host_brain.evolution_lab.golden_datasets import (
    BuiltInGoldenDatasetReader,
)
from host_brain.evolution_lab.golden_experiments import (
    GoldenExperimentService,
)
from host_brain.evolution_lab.golden_verifier import (
    GoldenExperimentVerifier,
)
from host_brain.evolution_lab.trusted_runner import (
    TrustedEvolutionLabPorts,
    TrustedEvolutionLabRunner,
)
from host_brain.persistence.evolution_lab_trusted_repository import (
    SQLiteTrustedEvolutionLabLedger,
)
from host_brain.persistence.evolution_lab_v2_repository import (
    SQLiteEvolutionLabV2Ledger,
)


@dataclass(frozen=True, slots=True)
class TrustedEvolutionLabComposition:
    """Closed Lab graph; it exposes no production writer or activation port."""

    dataset_reader: BuiltInGoldenDatasetReader
    executor: TrustedDockerSandboxExecutor
    verifier: GoldenExperimentVerifier
    spec_registry: SQLiteEvolutionLabV2Ledger
    evidence_ledger: SQLiteTrustedEvolutionLabLedger
    runner: TrustedEvolutionLabRunner
    golden_experiments: GoldenExperimentService

    def close(self) -> None:
        # Repositories use short-lived SQLite connections per operation.
        return None


def build_trusted_evolution_lab_composition(
    *,
    db_path: str | Path,
    runtime_root: str | Path,
    image_reference: str,
) -> TrustedEvolutionLabComposition:
    """Construct only after the central migration runner made 0122/0138 ready."""

    dataset_reader = BuiltInGoldenDatasetReader()
    executor = TrustedDockerSandboxExecutor(
        runtime_root=runtime_root,
        image_reference=image_reference,
    )
    verifier = GoldenExperimentVerifier(dataset_reader=dataset_reader)
    spec_registry = SQLiteEvolutionLabV2Ledger(db_path)
    evidence_ledger = SQLiteTrustedEvolutionLabLedger(db_path)
    runner = TrustedEvolutionLabRunner(
        ports=TrustedEvolutionLabPorts(
            dataset_reader=dataset_reader,
            executor=executor,
            verifier=verifier,
            spec_registry=spec_registry,
            operation_ledger=evidence_ledger,
        )
    )
    golden = GoldenExperimentService(
        runner=runner,
        executor=executor,
        verifier=verifier,
        ledger=evidence_ledger,
    )
    return TrustedEvolutionLabComposition(
        dataset_reader=dataset_reader,
        executor=executor,
        verifier=verifier,
        spec_registry=spec_registry,
        evidence_ledger=evidence_ledger,
        runner=runner,
        golden_experiments=golden,
    )


__all__ = [
    "TrustedEvolutionLabComposition",
    "build_trusted_evolution_lab_composition",
]
