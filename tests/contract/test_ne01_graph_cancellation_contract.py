from __future__ import annotations

from copy import deepcopy

import pytest

from host_brain.contracts.agent_runtime.graph_cancellation import (
    GRAPH_CANCELLATION_DECISION_SCHEMA_VERSION,
    GRAPH_CANCELLATION_INTENT_SCHEMA_VERSION,
    GraphCancellationContractError,
    normalize_graph_cancellation_decision,
    normalize_graph_cancellation_intent,
)


def _intent() -> dict:
    return {
        "schema_version": GRAPH_CANCELLATION_INTENT_SCHEMA_VERSION,
        "request_id": "request-cancel-1",
        "request_identity": "principal://user/alice",
        "source": "USER",
        "reason_code": "USER_CANCELLED_TASK",
        "graph_id": "graph-cancel",
        "graph_revision": 1,
        "graph_status_before": "ACTIVE",
        "graph_status_version_before": 3,
        "graph_target_status": "BLOCKED",
        "budget_id": "budget-cancel",
        "budget_status_before": "ACTIVE",
        "budget_status_version_before": 2,
        "node_states_before": {
            "right": {"status": "RETRY_PENDING", "status_version": 4},
            "left": {"status": "RUNNING", "status_version": 2},
        },
        "node_states_after": {
            "left": {"status": "CANCEL_REQUESTED", "status_version": 3},
            "right": {"status": "CANCELLED", "status_version": 5},
        },
        "graph_outbox_id": "outbox-cancel-request",
    }


def _decision(intent_ref: str) -> dict:
    return {
        "schema_version": GRAPH_CANCELLATION_DECISION_SCHEMA_VERSION,
        "intent_ref": intent_ref,
        "request_id": "request-cancel-1",
        "request_identity": "principal://user/alice",
        "graph_id": "graph-cancel",
        "graph_revision": 1,
        "graph_status_before": "BLOCKED",
        "graph_status_version_before": 4,
        "graph_target_status": "CANCELLED",
        "budget_id": "budget-cancel",
        "budget_status_before": "ACTIVE",
        "budget_status_version_before": 2,
        "budget_target_status": "RELEASED",
        "node_states_before": {
            "left": {"status": "CANCEL_REQUESTED", "status_version": 3},
            "right": {"status": "CANCELLED", "status_version": 5},
        },
        "node_states_after": {
            "left": {"status": "CANCELLED", "status_version": 4},
            "right": {"status": "CANCELLED", "status_version": 5},
        },
        "reconciliation_evidence_refs": [intent_ref],
        "graph_outbox_id": "outbox-cancel-final",
        "budget_outbox_id": "outbox-cancel-budget",
    }


def test_graph_cancellation_contracts_are_canonical_and_bind_authority() -> None:
    intent = normalize_graph_cancellation_intent(_intent())
    reordered = _intent()
    reordered["node_states_before"] = dict(
        reversed(list(reordered["node_states_before"].items()))
    )
    assert normalize_graph_cancellation_intent(reordered) == intent
    assert intent.intent_ref.startswith("taskcancel-intent://sha256/")

    decision = normalize_graph_cancellation_decision(_decision(intent.intent_ref))
    assert decision.decision_ref.startswith("taskcancel://sha256/")


@pytest.mark.parametrize("source", ["PROCESS_SHUTDOWN", "TRANSPORT_DISCONNECT", "SYSTEM_POLICY"])
def test_graph_cancellation_rejects_non_business_sources(source: str) -> None:
    payload = _intent()
    payload["source"] = source
    with pytest.raises(GraphCancellationContractError, match="source"):
        normalize_graph_cancellation_intent(payload)


def test_graph_cancellation_detects_snapshot_and_evidence_drift() -> None:
    invalid = _intent()
    invalid["node_states_after"]["left"]["status_version"] = 9
    with pytest.raises(GraphCancellationContractError, match="node transition"):
        normalize_graph_cancellation_intent(invalid)

    intent = normalize_graph_cancellation_intent(_intent())
    decision = deepcopy(_decision(intent.intent_ref))
    decision["reconciliation_evidence_refs"] = ["evidence://other"]
    with pytest.raises(GraphCancellationContractError, match="include the cancellation intent"):
        normalize_graph_cancellation_decision(decision)


def test_all_terminal_nodes_can_still_form_a_cancellation_decision() -> None:
    intent = normalize_graph_cancellation_intent(_intent())
    payload = _decision(intent.intent_ref)
    payload["node_states_before"] = {
        "left": {"status": "ACCEPTED", "status_version": 4},
        "right": {"status": "FAILED", "status_version": 5},
    }
    payload["node_states_after"] = deepcopy(payload["node_states_before"])
    normalize_graph_cancellation_decision(payload)
