from __future__ import annotations

import sqlite3

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.evolution_lab.companion_expression_experiment import (
    BoundCompanionExpressionDatasetReader,
    CompanionExpressionExperimentError,
    CompanionExpressionVerifier,
    build_companion_expression_spec,
)
from host_brain.evolution_lab.companion_expression_worker import run
from host_brain.persistence.evolution_activation_v2_evidence import (
    EvolutionActivationV2Error,
)
from host_brain.persistence.evolution_activation_v2_repository import (
    SQLiteEvolutionActivationV2Repository,
)


BASELINE = {
    "schema": "natsume.expression-preference-policy.v1",
    "positive_feedback_count": 0,
    "negative_feedback_count": 0,
    "tag_scores": {},
}

TARGET = {
    "schema": "natsume.expression-preference-policy.v1",
    "positive_feedback_count": 0,
    "negative_feedback_count": 1,
    "tag_scores": {"ai_tone": -1, "template_like": -1},
}


def _runtime():
    provisional_dataset, provisional_spec = build_companion_expression_spec(
        workspace_id="host-default",
        code_version="a" * 40,
        isolation_profile_digest="b" * 64,
        verifier_digest="c" * 64,
        baseline_version="expression-baseline",
        target_version="expression-candidate",
        baseline_policy=BASELINE,
        target_policy=TARGET,
    )
    reader = BoundCompanionExpressionDatasetReader(provisional_dataset)
    verifier = CompanionExpressionVerifier(dataset_reader=reader)
    dataset, spec = build_companion_expression_spec(
        workspace_id="host-default",
        code_version="a" * 40,
        isolation_profile_digest="b" * 64,
        verifier_digest=verifier.verifier_digest,
        baseline_version="expression-baseline",
        target_version="expression-candidate",
        baseline_policy=BASELINE,
        target_policy=TARGET,
    )
    reader = BoundCompanionExpressionDatasetReader(dataset)
    verifier = CompanionExpressionVerifier(dataset_reader=reader)
    return dataset, spec, verifier


def test_companion_expression_worker_and_verifier_measure_target_gain() -> None:
    dataset, spec, verifier = _runtime()
    outputs = {}
    qualities = {}
    for variant_id in ("baseline", "candidate"):
        execution_id = f"execution-{variant_id}"
        output = run(
            dataset_payload=dataset.to_dict(),
            experiment_kind="companion_expression",
            variant_id=variant_id,
            execution_id=execution_id,
            seed=spec.seed,
            expected_dataset_digest=dataset.dataset_digest,
        )
        receipt = verifier.verify(
            execution_id=execution_id,
            experiment_kind="companion_expression",
            spec=spec,
            variant=spec.variant(variant_id),
            model_output=output,
        )
        outputs[variant_id] = output
        qualities[variant_id] = receipt.outcome["quality"]

    assert outputs["baseline"]["hints"] == []
    assert outputs["candidate"]["hints"] == [
        "避免机械 AI 腔",
        "避免模板化分段",
    ]
    assert qualities == {"baseline": 0.0, "candidate": 1.0}


def test_companion_expression_rollback_target_is_measured_in_reverse() -> None:
    dataset, spec = build_companion_expression_spec(
        workspace_id="host-default",
        code_version="d" * 40,
        isolation_profile_digest="e" * 64,
        verifier_digest="f" * 64,
        baseline_version="expression-candidate",
        target_version="expression-baseline",
        baseline_policy=TARGET,
        target_policy=BASELINE,
    )
    reader = BoundCompanionExpressionDatasetReader(dataset)
    verifier = CompanionExpressionVerifier(dataset_reader=reader)
    _, spec = build_companion_expression_spec(
        workspace_id="host-default",
        code_version="d" * 40,
        isolation_profile_digest="e" * 64,
        verifier_digest=verifier.verifier_digest,
        baseline_version="expression-candidate",
        target_version="expression-baseline",
        baseline_policy=TARGET,
        target_policy=BASELINE,
    )
    baseline_output = run(
        dataset_payload=dataset.to_dict(),
        experiment_kind="companion_expression",
        variant_id="baseline",
        execution_id="rollback-baseline",
        seed=spec.seed,
        expected_dataset_digest=dataset.dataset_digest,
    )
    candidate_output = run(
        dataset_payload=dataset.to_dict(),
        experiment_kind="companion_expression",
        variant_id="candidate",
        execution_id="rollback-candidate",
        seed=spec.seed,
        expected_dataset_digest=dataset.dataset_digest,
    )
    baseline = verifier.verify(
        execution_id="rollback-baseline",
        experiment_kind="companion_expression",
        spec=spec,
        variant=spec.variant("baseline"),
        model_output=baseline_output,
    )
    candidate = verifier.verify(
        execution_id="rollback-candidate",
        experiment_kind="companion_expression",
        spec=spec,
        variant=spec.variant("candidate"),
        model_output=candidate_output,
    )
    assert baseline.outcome["quality"] == 0.0
    assert candidate.outcome["quality"] == 1.0
    assert candidate_output["hints"] == []


def test_companion_expression_rejects_no_change_target() -> None:
    try:
        build_companion_expression_spec(
            workspace_id="host-default",
            code_version="a" * 40,
            isolation_profile_digest="b" * 64,
            verifier_digest="c" * 64,
            baseline_version="same-a",
            target_version="same-b",
            baseline_policy=BASELINE,
            target_policy=BASELINE,
        )
    except CompanionExpressionExperimentError as exc:
        assert "changed target" in str(exc)
    else:
        raise AssertionError("no-change expression experiment must fail")


def test_content_addressed_policy_version_reuses_first_provenance(
    tmp_path,
) -> None:
    db_path = tmp_path / "audit.sqlite3"
    result = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id="ne06-expression-content-addressed-version"
    )
    assert result.success
    repository = SQLiteEvolutionActivationV2Repository(db_path)
    first = repository.register_policy_version(
        workspace_id="host-default",
        subject_id="natsume_ai",
        policy_kind="EXPRESSION_PREFERENCES",
        version="expression-content-addressed",
        payload=TARGET,
        provenance_ref="response-feedback://sha256/" + "1" * 64,
    )
    replay = repository.register_policy_version(
        workspace_id="host-default",
        subject_id="natsume_ai",
        policy_kind="EXPRESSION_PREFERENCES",
        version="expression-content-addressed",
        payload=TARGET,
        provenance_ref="response-feedback://sha256/" + "2" * 64,
    )
    assert replay == first
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*), provenance_ref
            FROM evolution_policy_version_v2
            WHERE workspace_id = 'host-default'
              AND subject_id = 'natsume_ai'
              AND policy_kind = 'EXPRESSION_PREFERENCES'
              AND version = 'expression-content-addressed'
            """
        ).fetchone()
    assert row == (1, "response-feedback://sha256/" + "1" * 64)
    with pytest.raises(
        EvolutionActivationV2Error,
        match="EVOLUTION_POLICY_VERSION_REPLAY_DRIFT",
    ):
        repository.register_policy_version(
            workspace_id="host-default",
            subject_id="natsume_ai",
            policy_kind="EXPRESSION_PREFERENCES",
            version="expression-content-addressed",
            payload={**TARGET, "negative_feedback_count": 2},
            provenance_ref="response-feedback://sha256/" + "3" * 64,
        )
