from __future__ import annotations

from copy import deepcopy

import pytest

from host_brain.contracts.agent_runtime.parallel_success import (
    PARALLEL_SUCCESS_SCHEMA_VERSION,
    ParallelSuccessContractError,
    normalize_parallel_success_decision,
)


def _payload() -> dict:
    digest_a = "sha256:" + "a" * 64
    digest_b = "sha256:" + "b" * 64
    return {
        "schema_version": PARALLEL_SUCCESS_SCHEMA_VERSION,
        "graph_id": "graph-success",
        "graph_revision": 2,
        "graph_status_before": "ACTIVE",
        "graph_status_version_before": 7,
        "graph_target_status": "COMPLETED",
        "completion_policy": "ATOMIC_GRAPH_SUCCESS_V1",
        "budget_id": "budget-success",
        "budget_status_before": "ACTIVE",
        "budget_status_version_before": 4,
        "budget_target_status": "CONSUMED",
        "result_node_id": "node-b",
        "result_attempt_id": "attempt-b",
        "result_receipt_ref": "receipt://node-b",
        "node_states": {
            "node-b": {"status": "ACCEPTED", "status_version": 9},
            "node-a": {"status": "ACCEPTED", "status_version": 5},
        },
        "accepted_outcomes": [
            {
                "node_id": "node-b",
                "attempt_id": "attempt-b",
                "receipt_ref": "receipt://node-b",
                "payload_sha256": digest_b,
            },
            {
                "node_id": "node-a",
                "attempt_id": "attempt-a",
                "receipt_ref": "receipt://node-a",
                "payload_sha256": digest_a,
            },
        ],
        "effect_intents": [
            {
                "effect_id": "effect-b-2",
                "node_id": "node-b",
                "attempt_id": "attempt-b",
                "commit_scope": "GRAPH",
                "effect_type": "surface.reply_delivery",
                "payload_ref": "acceptance-payload://b",
                "payload_sha256": digest_b,
                "idempotency_key": "effect-key-b-2",
            },
            {
                "effect_id": "effect-b-1",
                "node_id": "node-b",
                "attempt_id": "attempt-b",
                "commit_scope": "GRAPH",
                "effect_type": "conversation.user_turn",
                "payload_ref": "acceptance-payload://a",
                "payload_sha256": digest_a,
                "idempotency_key": "effect-key-b-1",
            },
        ],
        "graph_outbox_id": "outbox-graph-success",
        "budget_outbox_id": "outbox-budget-success",
    }


def test_parallel_success_is_canonical_and_order_independent() -> None:
    first = normalize_parallel_success_decision(_payload())
    reordered = _payload()
    reordered["node_states"] = dict(reversed(list(reordered["node_states"].items())))
    reordered["accepted_outcomes"].reverse()
    reordered["effect_intents"].reverse()
    second = normalize_parallel_success_decision(reordered)

    assert first == second
    assert first.decision_ref.startswith("tasksuccess://sha256/")
    assert [item["node_id"] for item in first.payload["accepted_outcomes"]] == [
        "node-a",
        "node-b",
    ]
    assert [item["effect_id"] for item in first.payload["effect_intents"]] == [
        "effect-b-1",
        "effect-b-2",
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("graph_target_status", "FAILED"),
        ("budget_target_status", "RELEASED"),
        ("graph_status_before", "COMPLETED"),
        ("budget_status_before", "EXCEEDED"),
        ("completion_policy", "LEGACY_NODE_TERMINAL_V1"),
    ],
)
def test_parallel_success_rejects_non_success_terminal_states(
    field: str,
    value: str,
) -> None:
    payload = _payload()
    payload[field] = value

    with pytest.raises(ParallelSuccessContractError):
        normalize_parallel_success_decision(payload)


def test_parallel_success_requires_every_node_accepted_once() -> None:
    missing = _payload()
    missing["accepted_outcomes"].pop()
    with pytest.raises(ParallelSuccessContractError, match="every durable node"):
        normalize_parallel_success_decision(missing)

    failed = _payload()
    failed["node_states"]["node-a"]["status"] = "FAILED"
    with pytest.raises(ParallelSuccessContractError, match="requires ACCEPTED"):
        normalize_parallel_success_decision(failed)


def test_parallel_success_rejects_implicit_or_drifted_result_authority() -> None:
    payload = _payload()
    payload["result_attempt_id"] = "attempt-a"
    with pytest.raises(ParallelSuccessContractError, match="result authority"):
        normalize_parallel_success_decision(payload)

    effect_drift = _payload()
    effect_drift["effect_intents"][0]["node_id"] = "node-a"
    with pytest.raises(ParallelSuccessContractError, match="explicit result node"):
        normalize_parallel_success_decision(effect_drift)


def test_parallel_success_digest_detects_semantic_drift() -> None:
    first = normalize_parallel_success_decision(_payload())
    changed = deepcopy(_payload())
    changed["accepted_outcomes"][0]["receipt_ref"] = "receipt://node-b-v2"
    changed["result_receipt_ref"] = "receipt://node-b-v2"
    second = normalize_parallel_success_decision(changed)

    assert first.payload_sha256 != second.payload_sha256
    assert first.decision_ref != second.decision_ref


@pytest.mark.parametrize("field", ["commit_scope", "payload_ref"])
def test_parallel_success_digest_binds_complete_effect_identity(field: str) -> None:
    first = normalize_parallel_success_decision(_payload())
    changed = _payload()
    changed["effect_intents"][0][field] = (
        "NODE" if field == "commit_scope" else "acceptance-payload://changed"
    )
    if field == "commit_scope":
        with pytest.raises(ParallelSuccessContractError, match="commit_scope"):
            normalize_parallel_success_decision(changed)
    else:
        assert normalize_parallel_success_decision(changed).decision_ref != first.decision_ref


def test_parallel_success_requires_distinct_state_outboxes() -> None:
    payload = _payload()
    payload["budget_outbox_id"] = payload["graph_outbox_id"]
    with pytest.raises(ParallelSuccessContractError, match="must be distinct"):
        normalize_parallel_success_decision(payload)


def test_parallel_success_effect_count_fits_state_outbox_evidence_bound() -> None:
    payload = _payload()
    template = payload["effect_intents"][0]
    payload["effect_intents"] = [
        {
            **template,
            "effect_id": f"effect-{index}",
            "idempotency_key": f"effect-key-{index}",
        }
        for index in range(31)
    ]
    with pytest.raises(ParallelSuccessContractError, match="at most 30"):
        normalize_parallel_success_decision(payload)
