from __future__ import annotations

import ast
from pathlib import Path

import pytest

from host_brain.contracts.evolution_lab import (
    DatasetManifestV1,
    EffectAuditReceiptV1,
    ExperimentRunReceiptV2,
    ExperimentSpecV2,
    LabDatasetV1,
    PolicyVariantV1,
    SandboxReceiptV1,
    VerificationReceiptV1,
    canonical_evolution_lab_digest,
)
from tools.evolution_lab.v2_runner import (
    EvolutionLabV2PortSet,
    EvolutionLabV2Runner,
    EvolutionLabV2RunnerError,
    LabSandboxExecutionV1,
)


def _dataset() -> LabDatasetV1:
    return LabDatasetV1("runner-dataset", ({"case": "one", "expected": "safe"},))


def _spec() -> ExperimentSpecV2:
    dataset = _dataset()
    return ExperimentSpecV2(
        experiment_id="runner-v2",
        workspace_id="workspace-runner",
        dataset_manifest=DatasetManifestV1(
            workspace_id="workspace-runner",
            dataset_id=dataset.dataset_id,
            dataset_digest=dataset.dataset_digest,
            schema_digest=canonical_evolution_lab_digest({"fields": ["case", "expected"]}),
            record_count=len(dataset.records),
            source_kind="synthetic",
        ),
        seed=17,
        budget={"max_runs": 2, "tokens": 40},
        variants=(
            PolicyVariantV1("baseline", "p.v1", "c.v1", "1" * 64, True),
            PolicyVariantV1("candidate", "p.v2", "c.v2", "2" * 64, False),
        ),
        verifier_identity="verifier://runner-v2",
        verifier_version="2.0",
        verifier_digest="3" * 64,
        isolation_profile_digest="4" * 64,
    )


class _DatasetReader:
    def __init__(self, dataset: LabDatasetV1) -> None:
        self.dataset = dataset

    def read(self, *, manifest: DatasetManifestV1) -> LabDatasetV1:
        return self.dataset


class _Executor:
    def execute(self, *, execution_id, spec, variant, dataset) -> LabSandboxExecutionV1:
        output = {"variant": variant.variant_id, "answer": "isolated"}
        audit = EffectAuditReceiptV1(
            execution_id=execution_id,
            audit_id=f"audit-{variant.variant_id}",
            auditor_identity="sandbox-auditor://structural",
            audit_evidence_digest="5" * 64,
            effect_count=0,
        )
        sandbox = SandboxReceiptV1(
            execution_id=execution_id,
            sandbox_id=f"sandbox-{variant.variant_id}",
            sandbox_profile_digest=spec.isolation_profile_digest,
            dataset_digest=dataset.dataset_digest,
            output_digest=canonical_evolution_lab_digest({"output": output}),
            network_disabled=True,
            readonly_dataset=True,
            temporary_output=True,
            effect_audit=audit,
        )
        return LabSandboxExecutionV1(
            execution_id=execution_id,
            model_identity="structural-test-model",
            usage={"tokens": 4},
            cost={"microusd": 3},
            latency_ms=8,
            output=output,
            sandbox_receipt=sandbox,
        )


class _Verifier:
    def __init__(self, *, drift: bool = False) -> None:
        self.drift = drift

    def verify(self, *, execution_id, spec, variant, dataset, model_output) -> VerificationReceiptV1:
        return VerificationReceiptV1(
            execution_id="different-execution" if self.drift else execution_id,
            verifier_identity=spec.verifier_identity,
            verifier_version=spec.verifier_version,
            verifier_digest=spec.verifier_digest,
            status="PASS",
            evidence_digest="6" * 64,
            outcome={"quality": 1.0},
            risk={"policy_errors": 0},
            metrics={"citation_validity": 1.0},
        )


class _Repository:
    def __init__(self) -> None:
        self.specs: list[ExperimentSpecV2] = []
        self.receipts: list[ExperimentRunReceiptV2] = []

    def register_spec_v2(self, spec: ExperimentSpecV2) -> str:
        self.specs.append(spec)
        return spec.spec_digest

    def append_execution_and_run(self, receipt: ExperimentRunReceiptV2) -> str:
        self.receipts.append(receipt)
        return receipt.run_id


def test_ne04_v2_runner_recomputes_dataset_and_persists_only_bound_evidence() -> None:
    repository = _Repository()
    runner = EvolutionLabV2Runner(
        ports=EvolutionLabV2PortSet(
            dataset_reader=_DatasetReader(_dataset()),
            executor=_Executor(),
            verifier=_Verifier(),
            repository=repository,
        )
    )

    receipt = runner.run_variant(spec=_spec(), variant_id="candidate")

    assert receipt.variant_id == "candidate"
    assert receipt.production_side_effects is False
    assert receipt.benefit_eligible is False
    assert repository.specs == [_spec()]
    assert repository.receipts == [receipt]
    assert "output" not in receipt.to_dict()


def test_ne04_v2_runner_rejects_dataset_and_verifier_identity_drift() -> None:
    drifted_dataset = LabDatasetV1("runner-dataset", ({"case": "different"},))
    dataset_runner = EvolutionLabV2Runner(
        ports=EvolutionLabV2PortSet(
            dataset_reader=_DatasetReader(drifted_dataset),
            executor=_Executor(),
            verifier=_Verifier(),
            repository=_Repository(),
        )
    )
    with pytest.raises(EvolutionLabV2RunnerError, match="frozen manifest"):
        dataset_runner.run_variant(spec=_spec(), variant_id="candidate")

    verifier_runner = EvolutionLabV2Runner(
        ports=EvolutionLabV2PortSet(
            dataset_reader=_DatasetReader(_dataset()),
            executor=_Executor(),
            verifier=_Verifier(drift=True),
            repository=_Repository(),
        )
    )
    with pytest.raises(EvolutionLabV2RunnerError, match="verifier execution identity drift"):
        verifier_runner.run_variant(spec=_spec(), variant_id="candidate")


def test_ne04_v2_runner_has_no_attribute_blacklist_or_production_imports() -> None:
    root = Path(__file__).resolve().parents[2]
    source_path = root / "tools/evolution_lab/v2_runner.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    imports = {
        str(node.module or "")
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0
    }
    imports.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    forbidden_prefixes = (
        "host_brain.app",
        "host_brain.capability_plane",
        "host_brain.governor",
        "host_brain.model_runtime",
        "host_brain.secure_ingress",
        "edge_client",
    )

    assert not any(
        module == prefix or module.startswith(f"{prefix}.")
        for module in imports
        for prefix in forbidden_prefixes
    )
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "hasattr"
        for node in ast.walk(tree)
    )
