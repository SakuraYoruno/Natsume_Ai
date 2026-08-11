from __future__ import annotations

from dataclasses import replace

import pytest

from host_brain.task_runtime.errors import (
    GOVERNED_ADMISSION_INVALID,
    GOVERNED_DURABLE_STATE_CONFLICT,
    GovernedRuntimeError,
)
from host_brain.task_runtime.node_execution_preparation import (
    bind_preoperation_recovery_attempt,
    preverify_node_claim_authority,
)
from host_brain.task_runtime.preoperation_attempt_recovery import (
    PreOperationRecoveryCandidate,
)


def _candidate() -> PreOperationRecoveryCandidate:
    return PreOperationRecoveryCandidate(
        graph_id="graph-1",
        graph_revision=1,
        node_id="node-1",
        node_status="RUNNING",
        attempt_id="attempt-1",
        attempt_no=1,
        attempt_status="RUNNING",
        attempt_version=2,
        previous_lease_id="lease-1",
        previous_fencing_token=1,
    )


@pytest.mark.asyncio
async def test_explicit_grant_ref_requires_a_verifier_before_any_mutation() -> None:
    with pytest.raises(GovernedRuntimeError) as raised:
        await preverify_node_claim_authority(
            verifier=None,
            admitted=None,  # type: ignore[arg-type]
            command=None,  # type: ignore[arg-type]
            work_order=None,
            node_grant_ref="grant://node-1",
        )

    assert raised.value.reason_code == GOVERNED_ADMISSION_INVALID


@pytest.mark.asyncio
async def test_legacy_missing_grant_ref_performs_no_verifier_call() -> None:
    assert (
        await preverify_node_claim_authority(
            verifier=None,
            admitted=None,  # type: ignore[arg-type]
            command=None,  # type: ignore[arg-type]
            work_order=None,
            node_grant_ref=None,
        )
        is None
    )


def test_candidate_binds_the_exact_latest_attempt_snapshot() -> None:
    attempt = {
        "attempt_id": "attempt-1",
        "attempt_no": 1,
        "status_version": 2,
        "status": "RUNNING",
    }

    assert bind_preoperation_recovery_attempt(_candidate(), (attempt,)) == attempt


@pytest.mark.parametrize(
    ("candidate", "attempt"),
    (
        (replace(_candidate(), attempt_id="attempt-2"), {"attempt_id": "attempt-1", "attempt_no": 1, "status_version": 2}),
        (replace(_candidate(), attempt_no=2), {"attempt_id": "attempt-1", "attempt_no": 1, "status_version": 2}),
        (replace(_candidate(), attempt_version=3), {"attempt_id": "attempt-1", "attempt_no": 1, "status_version": 2}),
    ),
)
def test_candidate_attempt_drift_fails_closed(candidate, attempt) -> None:
    with pytest.raises(GovernedRuntimeError) as raised:
        bind_preoperation_recovery_attempt(candidate, (attempt,))

    assert raised.value.reason_code == GOVERNED_DURABLE_STATE_CONFLICT
