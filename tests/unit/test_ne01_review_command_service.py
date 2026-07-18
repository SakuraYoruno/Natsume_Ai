from __future__ import annotations

from typing import Any

import pytest

from host_brain.task_runtime.review_command_service import TaskReviewCommandService


class _ReviewStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail_resume = False

    def create_review(self, review: Any, **_: Any) -> dict[str, Any]:
        self.calls.append(("create", str(review)))
        return {"review": review}

    def approve_review(self, review_id: str, **_: Any) -> dict[str, Any]:
        self.calls.append(("approve", review_id))
        return {"review": {"review_id": review_id, "status": "APPROVED"}}

    def reject_review(self, review_id: str, **_: Any) -> dict[str, Any]:
        self.calls.append(("reject", review_id))
        return {"review_id": review_id, "status": "REJECTED"}

    def resume_review(self, review_id: str, **_: Any) -> dict[str, Any]:
        self.calls.append(("resume", review_id))
        if self.fail_resume:
            raise RuntimeError("resume failed")
        return {"review": {"review_id": review_id, "status": "CONSUMED"}}


def test_review_commands_wake_only_after_durable_terminal_mutation() -> None:
    store = _ReviewStore()
    wakes: list[str] = []
    service = TaskReviewCommandService(
        review_store=store,  # type: ignore[arg-type]
        wake_continuation=lambda: wakes.append("wake"),
    )

    service.create_review("review-new")
    service.approve_review("review-approve")
    service.reject_review("review-reject")
    service.resume_review("review-resume")

    # Approval has no continuation row yet, but waking is a harmless latency
    # hint; reject/resume are already durable before their wake calls.
    assert wakes == ["wake", "wake", "wake"]


def test_failed_review_mutation_never_wakes_worker() -> None:
    store = _ReviewStore()
    store.fail_resume = True
    wakes: list[str] = []
    service = TaskReviewCommandService(
        review_store=store,  # type: ignore[arg-type]
        wake_continuation=lambda: wakes.append("wake"),
    )

    with pytest.raises(RuntimeError, match="resume failed"):
        service.resume_review("review-failed")

    assert wakes == []
