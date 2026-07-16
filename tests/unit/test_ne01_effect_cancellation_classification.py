from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from host_brain.persistence.task_graph_cancellation_request import (
    _cancellation_effect_resolution,
)
from host_brain.persistence.task_repository import (
    TASK_STORE_DURABLE_DATA_INVALID,
    TaskStoreError,
)


def _pending_effect(**overrides: Any) -> dict[str, Any]:
    payload_json = json.dumps(
        {"message": "classification"},
        sort_keys=True,
        separators=(",", ":"),
    )
    record: dict[str, Any] = {
        "effect_id": "effect://classification/pending-1",
        "graph_id": "graph-classification",
        "graph_revision": 1,
        "node_id": "node-1",
        "attempt_id": "attempt-1",
        "commit_scope": "GRAPH",
        "effect_type": "surface.reply_delivery",
        "payload_ref": "effect-payload://classification/pending-1",
        "payload_json": payload_json,
        "payload_sha256": "sha256:"
        + hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
        "idempotency_key": "idempotency:classification:pending-1",
        "status": "PENDING",
        "available_ts_ms": 1,
        "lease_owner": None,
        "lease_expires_ts_ms": None,
        "fencing_token": 0,
        "attempt_count": 0,
        "status_version": 0,
        "receipt_ref": None,
        "reason_code": None,
        "last_error": None,
        "created_ts_ms": 1,
        "updated_ts_ms": 1,
        "committed_ts_ms": None,
        "failed_ts_ms": None,
    }
    record.update(overrides)
    return record


def test_only_exact_virgin_pending_is_safe_to_cancel() -> None:
    assert _cancellation_effect_resolution(_pending_effect()) == "CANCELLED"


@pytest.mark.parametrize(
    "overrides",
    (
        {
            "fencing_token": 1,
            "attempt_count": 1,
            "status_version": 2,
            "reason_code": "ACCEPTANCE_EFFECT_HANDLER_FAILED",
            "last_error": "transport response lost",
        },
        {
            "fencing_token": 7,
            "attempt_count": 5,
            "status_version": 12,
            "reason_code": "ACCEPTANCE_EFFECT_HANDLER_FAILED",
            "last_error": "retry history remains uncertain",
        },
    ),
)
def test_every_legal_retry_pending_requires_reconciliation(
    overrides: dict[str, Any],
) -> None:
    assert (
        _cancellation_effect_resolution(_pending_effect(**overrides))
        == "RECONCILIATION_REQUIRED"
    )


@pytest.mark.parametrize(
    "overrides",
    (
        {"fencing_token": 1},
        {"attempt_count": 1},
        {"status_version": 2},
        {
            "fencing_token": 1,
            "attempt_count": 1,
            "status_version": 1,
            "reason_code": "ACCEPTANCE_EFFECT_HANDLER_FAILED",
            "last_error": "too-early version",
        },
        {
            "fencing_token": 1,
            "attempt_count": 1,
            "status_version": 2,
            "last_error": "missing reason",
        },
        {
            "fencing_token": 1,
            "attempt_count": 1,
            "status_version": 2,
            "reason_code": "ACCEPTANCE_EFFECT_HANDLER_FAILED",
        },
        {"reason_code": "unexpected initial error"},
        {"last_error": "unexpected initial detail"},
    ),
)
def test_mixed_or_weakened_pending_state_fails_closed(
    overrides: dict[str, Any],
) -> None:
    with pytest.raises(TaskStoreError) as raised:
        _cancellation_effect_resolution(_pending_effect(**overrides))

    assert raised.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID
