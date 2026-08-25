from __future__ import annotations

import pytest

from host_brain.contracts.planning_search.calibration import (
    CalibrationContractError,
    CalibrationOutcome,
    CalibrationPrediction,
)


def _prediction(**overrides):
    values = {
        "domain": "pentest",
        "scope_key": "engagement:contract",
        "request_id": "request-contract-1",
        "state_schema": "pentest.virtual-state.v1",
        "state_digest": "a" * 64,
        "baseline_candidate_id": "baseline",
        "executed_candidate_id": "baseline",
        "executed_path_candidate_ids": ("baseline",),
        "shadow_recommended_candidate_id": "shadow",
        "shadow_path_candidate_ids": ("shadow", "follow-up"),
        "search_trace_digest": "b" * 64,
        "search_budget": {"max_rollouts": 4},
        "search_cost": {"measurement_kind": "VIRTUAL_SEARCH_COUNTERS", "rollouts": 4},
        "adapter_revision": "pentest-planning-shadow-v2",
        "predicted_reward": 0.5,
    }
    values.update(overrides)
    return CalibrationPrediction(**values)


def test_prediction_id_binds_all_immutable_shadow_evidence() -> None:
    prediction = _prediction()
    same = CalibrationPrediction.from_dict(prediction.to_dict())

    assert prediction.prediction_id.startswith("calpred_")
    assert same == prediction
    assert _prediction(search_cost={"measurement_kind": "VIRTUAL_SEARCH_COUNTERS", "rollouts": 5}).prediction_id != prediction.prediction_id
    assert _prediction(state_digest="c" * 64).prediction_id != prediction.prediction_id

    with pytest.raises(CalibrationContractError, match="influence"):
        _prediction(influence_enabled=True)
    with pytest.raises(CalibrationContractError, match="SHADOW_ONLY"):
        _prediction(mode="PROMOTED")


def test_outcome_is_canonical_and_bound_to_calibration_prediction() -> None:
    prediction = _prediction()
    outcome = CalibrationOutcome(
        prediction_id=prediction.prediction_id,
        executed_candidate_id="baseline",
        actual_reward=0.8,
        outcome_ref="fixture://outcome/1",
        verifier_refs=("fixture://verifier/1", "fixture://verifier/1"),
        outcome_snapshot={"status": "OK", "evidence_refs": ["evidence://1"]},
    )

    assert outcome.verifier_refs == ("fixture://verifier/1",)
    assert CalibrationOutcome.from_dict(outcome.to_dict()) == outcome
    with pytest.raises(CalibrationContractError, match=r"within \[0, 1\]"):
        CalibrationOutcome(
            prediction_id=prediction.prediction_id,
            executed_candidate_id="baseline",
            actual_reward=1.1,
            outcome_ref="fixture://outcome/1",
            verifier_refs=(),
            outcome_snapshot={},
        )


def test_paths_and_verifier_refs_reject_text_or_unbounded_sequences() -> None:
    prediction = _prediction()

    with pytest.raises(CalibrationContractError, match="sequence, not text"):
        _prediction(executed_path_candidate_ids="baseline")
    with pytest.raises(CalibrationContractError, match="exceeds 64 items"):
        _prediction(
            shadow_path_candidate_ids=tuple(f"candidate-{index}" for index in range(65))
        )
    with pytest.raises(CalibrationContractError, match="sequence, not text"):
        CalibrationOutcome(
            prediction_id=prediction.prediction_id,
            executed_candidate_id="baseline",
            actual_reward=0.5,
            outcome_ref="fixture://outcome/text-ref",
            verifier_refs="fixture://verifier/text-ref",
            outcome_snapshot={},
        )
