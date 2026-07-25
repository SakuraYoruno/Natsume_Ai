from __future__ import annotations

import pytest

from host_brain.contracts.agent_runtime.internal_nonacceptance_claim_result import (
    INTERNAL_NONACCEPTANCE_RESULT_REF_PREFIX,
    build_internal_nonacceptance_result_ref,
)


def _build(**overrides: object) -> str:
    values: dict[str, object] = {
        "command_id": "command-internal",
        "ingress_claim_ref": "ingress-claim://internal/nonacceptance",
        "graph_id": "graph-internal",
        "graph_revision": 2,
        "node_id": "node-agent",
        "attempt_id": "attempt-agent",
        "historical_lease_id": "lease-agent",
        "completion_lease_id": "lease-agent-takeover",
        "reservation_id": "reservation-agent",
        "projection_id": "worker-projection://internal/agent",
        "terminal_reason": "TERMINAL_VERIFICATION_FAILED",
    }
    values.update(overrides)
    return build_internal_nonacceptance_result_ref(**values)  # type: ignore[arg-type]


def test_nonacceptance_result_is_deterministic_and_terminal_bound() -> None:
    first = _build()
    assert first == _build()
    assert first.startswith(INTERNAL_NONACCEPTANCE_RESULT_REF_PREFIX)
    assert first != _build(terminal_reason="TERMINAL_POST_REVIEW_CANCELLED")
    assert first != _build(completion_lease_id="lease-agent")


@pytest.mark.parametrize(
    "field,value",
    (
        ("graph_revision", 0),
        ("terminal_reason", "TERMINAL_REVIEW_REJECTED"),
        ("attempt_id", "\n"),
        ("projection_id", ""),
    ),
)
def test_nonacceptance_result_rejects_unreopenable_identity(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValueError):
        _build(**{field: value})
