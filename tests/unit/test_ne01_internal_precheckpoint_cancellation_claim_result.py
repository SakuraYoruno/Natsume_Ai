from __future__ import annotations

import pytest

from host_brain.contracts.agent_runtime.internal_precheckpoint_cancellation_claim_result import (
    INTERNAL_PRECHECKPOINT_CANCELLATION_RESULT_REF_PREFIX,
    build_internal_precheckpoint_cancellation_result_ref,
)


def _values() -> dict[str, object]:
    return {
        "command_id": "command-internal-cancel",
        "ingress_claim_ref": "secure-ingress://claim/internal-cancel",
        "graph_id": "graph-internal-cancel",
        "graph_revision": 2,
        "node_id": "node-agent",
        "attempt_id": "attempt-agent",
        "lease_id": "lease-agent",
        "reservation_id": "reservation-agent",
        "reconciliation_proof_ref": "operation-proof://release/no-side-effect",
        "reconciliation_proof_digest": "sha256:" + ("a" * 64),
        "cancellation_intent_ref": "taskcancel-intent://sha256/" + ("b" * 64),
        "cancellation_decision_ref": (
            "taskcancel-decision://sha256/" + ("c" * 64)
        ),
    }


def test_result_ref_is_deterministic_and_binds_every_terminal_authority() -> None:
    values = _values()
    first = build_internal_precheckpoint_cancellation_result_ref(**values)
    second = build_internal_precheckpoint_cancellation_result_ref(**values)

    assert first == second
    assert first.startswith(INTERNAL_PRECHECKPOINT_CANCELLATION_RESULT_REF_PREFIX)
    assert len(first.removeprefix(
        INTERNAL_PRECHECKPOINT_CANCELLATION_RESULT_REF_PREFIX
    )) == 64

    for field_name, replacement in (
        ("graph_revision", 3),
        ("reservation_id", "reservation-other"),
        ("reconciliation_proof_ref", "operation-proof://other"),
        ("cancellation_decision_ref", "taskcancel-decision://other"),
    ):
        changed = {**values, field_name: replacement}
        assert (
            build_internal_precheckpoint_cancellation_result_ref(**changed)
            != first
        )


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    (
        ("graph_revision", 0),
        ("command_id", ""),
        ("reconciliation_proof_digest", "sha256:not-a-digest"),
        ("cancellation_intent_ref", "bad\nref"),
    ),
)
def test_result_ref_rejects_invalid_authority(
    field_name: str,
    replacement: object,
) -> None:
    values = {**_values(), field_name: replacement}

    with pytest.raises(ValueError):
        build_internal_precheckpoint_cancellation_result_ref(**values)
