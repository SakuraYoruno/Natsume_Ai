from __future__ import annotations

import pytest

from host_brain.orchestration.failure_replan_intent import (
    FAILURE_REPLAN_SCHEMA_VERSION,
    FailureReplanIntentError,
    failure_replan_reference_from_intent,
)


def _reference() -> dict:
    return {
        "schema_version": FAILURE_REPLAN_SCHEMA_VERSION,
        "graph_id": "graph-host-failure-replan",
        "previous_revision": 2,
    }


def test_absent_failure_replan_keeps_initial_planning_path() -> None:
    assert failure_replan_reference_from_intent({"route": "plan", "args": {}}) is None


def test_explicit_reference_can_be_bound_at_top_level_or_in_args() -> None:
    top = failure_replan_reference_from_intent(
        {"route": "plan", "failure_replan": _reference()}
    )
    nested = failure_replan_reference_from_intent(
        {"route": "plan", "args": {"failure_replan": _reference()}}
    )

    assert top == nested
    assert top is not None
    assert top.to_dict() == _reference()


@pytest.mark.parametrize(
    "reference",
    [
        {**_reference(), "previous_revision": True},
        {**_reference(), "previous_revision": 0},
        {**_reference(), "graph_id": "bad graph id"},
        {**_reference(), "schema_version": "legacy"},
        {**_reference(), "failure_evidence_refs": ["caller://forged"]},
    ],
)
def test_reference_rejects_invalid_identity_and_caller_failure_evidence(
    reference: dict,
) -> None:
    with pytest.raises(FailureReplanIntentError):
        failure_replan_reference_from_intent(
            {"route": "plan", "failure_replan": reference}
        )

def test_conflicting_duplicate_references_fail_closed() -> None:
    with pytest.raises(FailureReplanIntentError, match="references differ"):
        failure_replan_reference_from_intent(
            {
                "route": "plan",
                "failure_replan": _reference(),
                "args": {
                    "failure_replan": {
                        **_reference(),
                        "previous_revision": 3,
                    }
                },
            }
        )
