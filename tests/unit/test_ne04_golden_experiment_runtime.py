from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from host_brain.evolution_lab.golden_datasets import (
    BuiltInGoldenDatasetReader,
    GoldenDatasetError,
    build_golden_spec,
)
from host_brain.evolution_lab.golden_verifier import (
    GoldenExperimentVerifier,
    GoldenVerifierError,
)
from host_brain.evolution_lab.golden_worker import run as run_worker


def _spec(kind: str):
    reader = BuiltInGoldenDatasetReader()
    verifier = GoldenExperimentVerifier(dataset_reader=reader)
    spec = build_golden_spec(
        experiment_kind=kind,
        workspace_id="workspace-ne04-unit",
        code_version="a" * 40,
        isolation_profile_digest="b" * 64,
        verifier_digest=verifier.verifier_digest,
    )
    return reader, verifier, spec


@pytest.mark.parametrize(
    "kind",
    ("research_planning", "proactive", "memory_write"),
)
def test_ne04_golden_worker_is_reproducible_and_independently_verified(
    kind: str,
) -> None:
    reader, verifier, spec = _spec(kind)
    dataset = reader.read(manifest=spec.dataset_manifest)
    receipts = {}
    outputs = {}
    for variant_id in ("baseline", "candidate"):
        output = run_worker(
            dataset_payload=dataset.to_dict(),
            experiment_kind=kind,
            variant_id=variant_id,
            execution_id=f"unit-{kind}-{variant_id}",
            seed=spec.seed,
            expected_dataset_digest=dataset.dataset_digest,
        )
        outputs[variant_id] = output
        receipts[variant_id] = verifier.verify(
            execution_id=f"unit-{kind}-{variant_id}",
            experiment_kind=kind,
            spec=spec,
            variant=spec.variant(variant_id),
            model_output=output,
        )

    assert (
        receipts["candidate"].outcome["quality"]
        - receipts["baseline"].outcome["quality"]
        >= 0.2
    )
    assert receipts["candidate"].risk["policy_errors"] == 0
    assert receipts["candidate"].risk["severe_errors"] == 0
    assert run_worker(
        dataset_payload=dataset.to_dict(),
        experiment_kind=kind,
        variant_id="candidate",
        execution_id=f"unit-{kind}-candidate",
        seed=spec.seed,
        expected_dataset_digest=dataset.dataset_digest,
    ) == outputs["candidate"]


def test_ne04_dataset_reader_and_verifier_fail_closed_on_identity_drift() -> None:
    reader, verifier, spec = _spec("research_planning")
    dataset = reader.read(manifest=spec.dataset_manifest)
    with pytest.raises(GoldenDatasetError, match="drifts"):
        reader.read(
            manifest=replace(
                spec.dataset_manifest,
                dataset_digest="c" * 64,
            )
        )
    output = run_worker(
        dataset_payload=dataset.to_dict(),
        experiment_kind="research_planning",
        variant_id="candidate",
        execution_id="unit-drift",
        seed=spec.seed,
        expected_dataset_digest=dataset.dataset_digest,
    )
    with pytest.raises(GoldenVerifierError, match="identity drift"):
        verifier.verify(
            execution_id="different-execution",
            experiment_kind="research_planning",
            spec=spec,
            variant=spec.variant("candidate"),
            model_output=output,
        )


def test_ne04_production_composition_has_only_lab_ports() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "host_brain"
        / "evolution_lab"
        / "production_composition.py"
    ).read_text(encoding="utf-8")
    forbidden = (
        "CapabilityBroker",
        "TokenService",
        "EdgeActuator",
        "ActivationPort",
        "ProductionWriter",
        "ProcessServiceContainer",
    )
    assert not any(name in source for name in forbidden)
    assert "TrustedEvolutionLabPorts" in source
    assert "SQLiteTrustedEvolutionLabLedger" in source
    executor_source = (
        Path(__file__).resolve().parents[2]
        / "host_brain"
        / "evolution_lab"
        / "docker_executor.py"
    ).read_text(encoding="utf-8")
    assert "host_brain.airlock" not in executor_source
    assert "subprocess fallback" not in executor_source.lower()
