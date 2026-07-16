from __future__ import annotations

import pytest

from host_brain.persistence.task_record_codec import (
    _graph_cancellation_continuation_record,
)
from host_brain.persistence.task_store_types import (
    TASK_STORE_DURABLE_DATA_INVALID,
    TaskStoreError,
)


def _record() -> dict[str, object]:
    return {
        "intent_ref": "taskcancel-intent://sha256/" + "a" * 64,
        "graph_id": "graph-1",
        "graph_revision": 1,
        "claim_owner": "worker-1",
        "claim_token": "token-1",
        "claim_expires_ts_ms": 200,
        "claim_fencing_token": 2,
        "attempt_count": 2,
        "available_ts_ms": 100,
        "last_error": None,
        "status_version": 3,
        "created_ts_ms": 90,
        "updated_ts_ms": 100,
    }


def test_graph_cancellation_continuation_codec_accepts_complete_claim() -> None:
    record = _record()

    assert _graph_cancellation_continuation_record(record) == record


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("claim_token", None),
        ("claim_expires_ts_ms", "200"),
        ("attempt_count", 1),
        ("status_version", True),
        ("last_error", " "),
    ],
)
def test_graph_cancellation_continuation_codec_rejects_corrupt_state(
    field: str,
    value: object,
) -> None:
    record = _record()
    record[field] = value

    with pytest.raises(TaskStoreError) as failure:
        _graph_cancellation_continuation_record(record)

    assert failure.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID


def test_graph_cancellation_continuation_codec_accepts_unclaimed_coordination() -> None:
    record = _record()
    record.update(
        claim_owner=None,
        claim_token=None,
        claim_expires_ts_ms=None,
        last_error="CANCELLATION_NOT_STABLE:RuntimeError",
    )

    assert _graph_cancellation_continuation_record(record) == record


def test_graph_cancellation_continuation_codec_rejects_extra_business_status() -> None:
    record = _record()
    record["status"] = "COMPLETED"

    with pytest.raises(TaskStoreError) as failure:
        _graph_cancellation_continuation_record(record)

    assert failure.value.reason_code == TASK_STORE_DURABLE_DATA_INVALID
