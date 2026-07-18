from __future__ import annotations

from copy import deepcopy

import pytest

from host_brain.runtime.action_preview_authority import (
    _same_guardian_authority_after_renewal,
)


def _authority() -> dict[str, object]:
    return {
        "schema_version": "Natsume.runtime_guardian.degradation_authority.v1",
        "authority": "RUNTIME_GUARDIAN",
        "authority_status": "ACTIVE",
        "aggregate_type": "RUNTIME_GUARDIAN",
        "aggregate_id": "guardian-aggregate",
        "aggregate_ref": "runtime-guardian://sha256/" + "a" * 64,
        "runtime_scope_id": "runtime-scope-alpha",
        "runtime_owner_id": "runtime-owner-alpha",
        "runtime_lease_id": "runtime-lease-alpha",
        "fencing_token": 7,
        "lease_issued_at_ms": 1_900_000_000_000,
        "lease_expires_at_ms": 1_900_000_030_000,
        "lease_released_at_ms": None,
        "guardian_revision": 11,
        "guardian_status": "READY",
        "last_event_digest": "sha256:" + "b" * 64,
        "durable": True,
    }


def test_action_preview_accepts_only_monotonic_runtime_lease_extension() -> None:
    issued = _authority()
    renewed = deepcopy(issued)
    renewed["lease_expires_at_ms"] = 1_900_000_060_000

    assert _same_guardian_authority_after_renewal(issued, renewed) is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("lease_expires_at_ms", 1_900_000_029_999),
        ("runtime_scope_id", "runtime-scope-beta"),
        ("runtime_owner_id", "runtime-owner-beta"),
        ("runtime_lease_id", "runtime-lease-beta"),
        ("fencing_token", 8),
        ("guardian_revision", 12),
    ],
)
def test_action_preview_rejects_runtime_authority_drift(
    field: str,
    value: object,
) -> None:
    issued = _authority()
    drifted = deepcopy(issued)
    drifted[field] = value

    assert _same_guardian_authority_after_renewal(issued, drifted) is False
