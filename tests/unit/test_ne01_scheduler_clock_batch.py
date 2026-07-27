from __future__ import annotations

from copy import deepcopy

import pytest

from host_brain.contracts.scheduler_clock_batch import (
    SCHEDULER_CLOCK_BATCH_INVALID,
    SchedulerClockBatchError,
    build_scheduler_clock_batch,
    validate_scheduler_clock_batch,
)


NOW = 1_800_000_000_000


def _pending(suffix: str = "1") -> dict:
    return {
        "occurrence_id": "scheduleroccurrence://sha256/" + suffix * 64,
        "task_id": f"pending-{suffix}",
        "definition_revision": 1,
        "source": "CLOCK",
        "payload": {"nested": {"value": "original"}},
    }


def _due(suffix: str = "1") -> dict:
    return {
        "task_id": f"due-{suffix}",
        "definition_revision": 1,
        "head_version": 1,
        "enabled": True,
        "next_scheduled_at_ms": NOW,
        "definition": {
            "task_id": f"due-{suffix}",
            "worker_key": "scheduler_production",
            "intent_template": {"args": {"value": "original"}},
        },
    }


def test_batch_deep_freezes_rows_and_reopens_exact_digest() -> None:
    pending = _pending()
    due = _due()
    batch = build_scheduler_clock_batch(
        captured_at_ms=NOW,
        limit=10,
        pending_occurrences=[pending],
        due_definitions=[due],
    )
    frozen = deepcopy(batch)

    pending["payload"]["nested"]["value"] = "mutated"
    due["definition"]["intent_template"]["args"]["value"] = "mutated"

    reopened = validate_scheduler_clock_batch(
        batch,
        expected_captured_at_ms=NOW,
        expected_limit=10,
    )
    assert reopened == frozen
    assert reopened["pending_occurrences"][0]["payload"]["nested"][
        "value"
    ] == "original"
    assert reopened["due_definitions"][0]["definition"]["intent_template"][
        "args"
    ]["value"] == "original"
    assert reopened["batch_ref"].startswith(
        "scheduler-clock-batch://sha256/"
    )


def test_batch_rejects_tamper_duplicate_and_overflow() -> None:
    batch = build_scheduler_clock_batch(
        captured_at_ms=NOW,
        limit=2,
        pending_occurrences=[_pending()],
        due_definitions=[_due()],
    )
    tampered = deepcopy(batch)
    tampered["due_definitions"][0]["definition"]["worker_key"] = "drifted"
    with pytest.raises(SchedulerClockBatchError) as digest_error:
        validate_scheduler_clock_batch(tampered)
    assert digest_error.value.reason_code == SCHEDULER_CLOCK_BATCH_INVALID

    with pytest.raises(SchedulerClockBatchError, match="duplicated"):
        build_scheduler_clock_batch(
            captured_at_ms=NOW,
            limit=2,
            pending_occurrences=[_pending(), _pending()],
            due_definitions=[],
        )

    with pytest.raises(SchedulerClockBatchError, match="more rows"):
        build_scheduler_clock_batch(
            captured_at_ms=NOW,
            limit=1,
            pending_occurrences=[_pending()],
            due_definitions=[_due()],
        )
