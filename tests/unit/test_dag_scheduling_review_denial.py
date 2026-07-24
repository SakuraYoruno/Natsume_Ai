"""Fail-closed unit coverage for denied Review scheduler re-entry."""

from __future__ import annotations

import pytest

from host_brain.task_runtime.dag_scheduling_review_denial import (
    assert_denied_review_gate,
    assert_denied_review_waiting_node,
    normalize_denied_review_node,
)
from host_brain.task_runtime.errors import (
    GOVERNED_DURABLE_STATE_CONFLICT,
    GovernedRuntimeError,
)


def test_denied_review_target_accepts_only_an_admitted_node_without_grants() -> None:
    assert (
        normalize_denied_review_node(
            " node-a ", {"node-a", "node-b"}, {}
        )
        == "node-a"
    )
    assert normalize_denied_review_node(None, {"node-a"}, {}) is None


@pytest.mark.parametrize(
    ("node_id", "grant_refs"),
    [
        ("unknown", {}),
        ("node-a", {"node-a": "node-grant://review/node-a"}),
    ],
)
def test_denied_review_target_rejects_drift_or_worker_grant(
    node_id: str,
    grant_refs: dict[str, str],
) -> None:
    with pytest.raises(GovernedRuntimeError) as exc_info:
        normalize_denied_review_node(node_id, {"node-a"}, grant_refs)

    assert exc_info.value.reason_code == GOVERNED_DURABLE_STATE_CONFLICT


@pytest.mark.parametrize("status", ["WAITING_APPROVAL", "WAITING_ACCEPTANCE_REVIEW"])
def test_denied_review_requires_the_exact_waiting_node(status: str) -> None:
    assert_denied_review_waiting_node("node-a", statuses={"node-a": status})


@pytest.mark.parametrize("status", ["READY", "ACCEPTED", "FAILED", "CANCELLED"])
def test_denied_review_rejects_a_node_that_left_the_waiting_checkpoint(
    status: str,
) -> None:
    with pytest.raises(GovernedRuntimeError) as exc_info:
        assert_denied_review_waiting_node("node-a", statuses={"node-a": status})

    assert exc_info.value.reason_code == GOVERNED_DURABLE_STATE_CONFLICT


@pytest.mark.parametrize(
    "sibling_status", ["LEASED", "RUNNING", "EXECUTED", "VERIFYING", "VERIFIED"]
)
def test_denied_review_rejects_when_a_sibling_is_still_executing(
    sibling_status: str,
) -> None:
    with pytest.raises(GovernedRuntimeError) as exc_info:
        assert_denied_review_waiting_node(
            "node-a",
            statuses={
                "node-a": "WAITING_ACCEPTANCE_REVIEW",
                "node-b": sibling_status,
            },
        )

    assert exc_info.value.reason_code == GOVERNED_DURABLE_STATE_CONFLICT


def test_denied_review_requires_the_store_to_still_report_denial() -> None:
    assert_denied_review_gate(
        "node-a", disposition="REVIEW_DENIED", phase="acceptance"
    )

    with pytest.raises(GovernedRuntimeError) as exc_info:
        assert_denied_review_gate(
            "node-a", disposition="REVIEW_REQUIRED", phase="acceptance"
        )

    assert exc_info.value.reason_code == GOVERNED_DURABLE_STATE_CONFLICT
