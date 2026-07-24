from __future__ import annotations

import pytest

from host_brain.contracts.agent_runtime.internal_budget_claim_result import (
    INTERNAL_BUDGET_REJECTION_RESULT_REF_PREFIX,
    build_internal_budget_rejection_result_ref,
)


def _result_ref(**overrides: object) -> str:
    values: dict[str, object] = {
        "command_id": "command-1",
        "ingress_claim_ref": "ingress-claim://internal/1",
        "graph_id": "graph-1",
        "graph_revision": 1,
        "node_id": "node-1",
        "attempt_id": "attempt-1",
        "reservation_id": "reservation-1",
        "budget_id": "budget-1",
        "usage_id": "usage-1",
    }
    values.update(overrides)
    return build_internal_budget_rejection_result_ref(**values)  # type: ignore[arg-type]


def test_internal_budget_rejection_result_ref_is_stable_and_bound_to_lineage() -> None:
    first = _result_ref()
    duplicate = _result_ref()
    changed_attempt = _result_ref(attempt_id="attempt-2")

    assert first.startswith(INTERNAL_BUDGET_REJECTION_RESULT_REF_PREFIX)
    assert first == duplicate
    assert changed_attempt != first


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("command_id", ""),
        ("ingress_claim_ref", "\nclaim"),
        ("graph_revision", 0),
        ("graph_revision", True),
        ("usage_id", None),
    ],
)
def test_internal_budget_rejection_result_ref_rejects_invalid_authority(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValueError):
        _result_ref(**{field: value})
