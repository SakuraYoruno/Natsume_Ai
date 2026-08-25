from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from tools.evolution_lab.golden_datasets import (
    BuiltInGoldenDatasetReader,
    build_golden_spec,
)
from tools.evolution_lab.golden_verifier import (
    GoldenExperimentVerifier,
)
from tools.evolution_lab.trusted_runner import (
    TrustedEvolutionLabPorts,
    TrustedEvolutionLabRunner,
    TrustedEvolutionLabRunnerError,
)
from host_brain.persistence.evolution_lab_trusted_repository import (
    SQLiteTrustedEvolutionLabLedger,
)
from host_brain.persistence.evolution_lab_v2_repository import (
    SQLiteEvolutionLabV2Ledger,
)


class _AmbiguousExecutor:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, **_kwargs):
        self.calls += 1
        raise RuntimeError("container response lost")

    def cleanup(self, **_kwargs) -> bool:
        raise AssertionError("ambiguous execution must remain for reconciliation")


def test_ne04_unknown_docker_effect_is_fenced_and_never_blindly_replayed(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "trusted-runner-recovery.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="ne04-trusted-runner-recovery",
        ts_ms=1,
    )
    assert migrated.success is True
    dataset_reader = BuiltInGoldenDatasetReader()
    verifier = GoldenExperimentVerifier(dataset_reader=dataset_reader)
    spec = build_golden_spec(
        experiment_kind="research_planning",
        workspace_id="workspace-ne04-recovery",
        code_version="c" * 40,
        isolation_profile_digest="d" * 64,
        verifier_digest=verifier.verifier_digest,
    )
    executor = _AmbiguousExecutor()
    runner = TrustedEvolutionLabRunner(
        ports=TrustedEvolutionLabPorts(
            dataset_reader=dataset_reader,
            executor=executor,
            verifier=verifier,
            spec_registry=SQLiteEvolutionLabV2Ledger(db_path),
            operation_ledger=SQLiteTrustedEvolutionLabLedger(db_path),
        )
    )

    with pytest.raises(
        TrustedEvolutionLabRunnerError,
        match="requires reconciliation",
    ):
        runner.run_variant(
            experiment_kind="research_planning",
            spec=spec,
            variant_id="candidate",
            owner_id="runner-recovery-1",
        )
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT status, fencing_token
            FROM evolution_lab_trusted_operation_head
            """
        ).fetchone()
    assert row == ("RECONCILIATION_REQUIRED", 2)

    with pytest.raises(
        TrustedEvolutionLabRunnerError,
        match="blind execution is forbidden",
    ):
        runner.run_variant(
            experiment_kind="research_planning",
            spec=spec,
            variant_id="candidate",
            owner_id="runner-recovery-2",
        )
    assert executor.calls == 1
