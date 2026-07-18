from __future__ import annotations

import pytest

from host_brain.evolution_lab import (
    EvolutionLabPortSet,
    EvolutionLabRunner,
    EvolutionLabRunnerError,
    ExperimentRunReceiptV1,
    ExperimentSpecV1,
)


class _Dataset:
    def read(self, *, dataset_digest: str):
        return {"digest": dataset_digest, "items": ["one"]}


class _Model:
    def run(self, *, variant: str, dataset, seed: int, budget):
        return {"variant": variant, "seed": seed, "cost": {"tokens": 3}}


class _Verifier:
    def verify(self, *, variant: str, dataset, model_output):
        return {
            "status": "PASS",
            "outcome": {"quality": 1.0},
            "risk": {"policy_errors": 0},
            "evidence_digest": "a" * 64,
        }


class _Repository:
    def __init__(self):
        self.receipts: list[ExperimentRunReceiptV1] = []

    def append_run(self, receipt: ExperimentRunReceiptV1) -> str:
        self.receipts.append(receipt)
        return receipt.run_id


def _spec() -> ExperimentSpecV1:
    return ExperimentSpecV1(
        experiment_id="exp-runner",
        dataset_digest="1" * 64,
        seed=3,
        budget={"runs": 2, "tokens": 20},
        variants=("baseline", "alternative"),
        verifier_ref="verifier://ne04",
        policy_version="policy.v1",
    )


def test_ne04_runner_emits_only_durable_proposal_evidence() -> None:
    repository = _Repository()
    runner = EvolutionLabRunner(
        ports=EvolutionLabPortSet(
            dataset_reader=_Dataset(), model=_Model(), verifier=_Verifier(), repository=repository
        )
    )
    receipt = runner.run_variant(spec=_spec(), variant="alternative")
    assert receipt.status == "PASS"
    assert receipt.production_side_effects is False
    assert repository.receipts == [receipt]


def test_ne04_runner_rejects_forbidden_production_port() -> None:
    class _LeakyModel(_Model):
        capability_broker = object()

    with pytest.raises(EvolutionLabRunnerError):
        EvolutionLabRunner(
            ports=EvolutionLabPortSet(
                dataset_reader=_Dataset(), model=_LeakyModel(), verifier=_Verifier(), repository=_Repository()
            )
        )
