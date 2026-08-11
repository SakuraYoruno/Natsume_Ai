from __future__ import annotations

import pytest

from host_brain.contracts.agent_runtime.internal_precheckpoint_cancellation_claim_result import (
    INTERNAL_PRECHECKPOINT_CANCELLATION_RESULT_REF_PREFIX,
    build_internal_precheckpoint_cancellation_result_ref,
    normalize_internal_precheckpoint_cancellation_terminal,
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


def test_no_effect_terminal_keeps_the_v1_digest_for_durable_replay() -> None:
    values = _values()

    legacy = build_internal_precheckpoint_cancellation_result_ref(**values)
    explicit = build_internal_precheckpoint_cancellation_result_ref(
        **values,
        reconciliation_action="RELEASE",
        reconciliation_outcome="NO_SIDE_EFFECT",
        downstream_receipt_ref=None,
    )

    assert explicit == legacy


def test_confirmed_side_effect_terminal_binds_receipt_and_outcome() -> None:
    values = _values()
    receipt = "provider-receipt://internal/cancelled-side-effect"
    result = build_internal_precheckpoint_cancellation_result_ref(
        **values,
        reconciliation_action="COMMIT",
        reconciliation_outcome="SIDE_EFFECT_CONFIRMED",
        downstream_receipt_ref=receipt,
    )

    assert result.startswith(INTERNAL_PRECHECKPOINT_CANCELLATION_RESULT_REF_PREFIX)
    assert result != build_internal_precheckpoint_cancellation_result_ref(**values)
    assert result != build_internal_precheckpoint_cancellation_result_ref(
        **values,
        reconciliation_action="COMMIT",
        reconciliation_outcome="SIDE_EFFECT_CONFIRMED",
        downstream_receipt_ref=receipt + "/other",
    )


@pytest.mark.parametrize(
    ("status", "action", "outcome", "receipt"),
    (
        ("RELEASED", "RELEASE", "NO_SIDE_EFFECT", None),
        (
            "COMMITTED",
            "COMMIT",
            "SIDE_EFFECT_CONFIRMED",
            "provider-receipt://internal/effect",
        ),
    ),
)
def test_shared_terminal_contract_accepts_only_exact_status_matrix(
    status: str,
    action: str,
    outcome: str,
    receipt: str | None,
) -> None:
    terminal = normalize_internal_precheckpoint_cancellation_terminal(
        operation_status=status,
        reconciliation_action=action,
        reconciliation_outcome=outcome,
        downstream_receipt_ref=receipt,
    )

    assert terminal.operation_status == status
    assert terminal.reconciliation_action == action
    assert terminal.reconciliation_outcome == outcome
    assert terminal.downstream_receipt_ref == receipt


@pytest.mark.parametrize(
    ("status", "action", "outcome", "receipt"),
    (
        ("COMMITTED", "RELEASE", "NO_SIDE_EFFECT", None),
        (
            "RELEASED",
            "COMMIT",
            "SIDE_EFFECT_CONFIRMED",
            "provider-receipt://internal/effect",
        ),
    ),
)
def test_shared_terminal_contract_rejects_status_action_drift(
    status: str,
    action: str,
    outcome: str,
    receipt: str | None,
) -> None:
    with pytest.raises(ValueError):
        normalize_internal_precheckpoint_cancellation_terminal(
            operation_status=status,
            reconciliation_action=action,
            reconciliation_outcome=outcome,
            downstream_receipt_ref=receipt,
        )


@pytest.mark.parametrize(
    ("action", "outcome", "receipt"),
    (
        ("COMMIT", "SIDE_EFFECT_CONFIRMED", None),
        ("COMMIT", "NO_SIDE_EFFECT", "provider-receipt://internal/effect"),
        ("RELEASE", "NO_SIDE_EFFECT", "provider-receipt://internal/effect"),
        ("RELEASE", "SIDE_EFFECT_CONFIRMED", None),
    ),
)
def test_result_ref_rejects_inconsistent_reconciliation_terminal(
    action: str,
    outcome: str,
    receipt: str | None,
) -> None:
    with pytest.raises(ValueError):
        build_internal_precheckpoint_cancellation_result_ref(
            **_values(),
            reconciliation_action=action,
            reconciliation_outcome=outcome,
            downstream_receipt_ref=receipt,
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
