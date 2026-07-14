from __future__ import annotations

import inspect

import pytest

from host_brain.secure_ingress import SecureIngressAuthority
from host_brain.skills.impl import notification_executor, scheduler_executor


@pytest.mark.parametrize("action", ["add", "cancel", "list"])
def test_scheduler_actions_fail_closed_without_governed_context(action: str) -> None:
    class LegacyScheduler:
        def __getattr__(self, name: str):
            raise AssertionError(f"legacy scheduler escape used: {name}")

    result = scheduler_executor.execute_scheduler_skill(
        {"action": action, "manifest_ref": f"test::{action}"},
        {"scheduler": LegacyScheduler()},
    )

    assert result["status"] == "REJECTED"
    assert result["reason_code"] == scheduler_executor.SCHEDULER_GOVERNED_CONTEXT_REQUIRED


def test_async_scheduler_service_is_blocked_in_sync_executor() -> None:
    class AsyncPort:
        async def execute_scheduler_skill(self, **_kwargs):
            return {"status": "EXECUTED"}

    authority = SecureIngressAuthority(
        allowed=True,
        duplicate=False,
        reason_code="SECURE_INGRESS_ALLOWED",
        request_id="request-scheduler-skill",
        principal_id="service.scheduler.skill",
        claim_ref="secure-ingress-claim://scheduler-skill",
        audit_ref="secure-ingress-audit://scheduler-skill",
        body_sha256="a" * 64,
    )
    result = scheduler_executor.execute_scheduler_skill(
        {"action": "list"},
        {
            "governed_scheduler_port": AsyncPort(),
            "secure_ingress_authority": authority,
        },
    )

    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == scheduler_executor.SCHEDULER_SYNC_ADAPTER_REQUIRED


def test_scheduler_and_notification_sources_have_no_private_authority_escape() -> None:
    scheduler_source = inspect.getsource(scheduler_executor)
    notification_source = inspect.getsource(notification_executor)

    assert "urllib.request" not in scheduler_source
    assert "/v1/scheduler/jobs" not in scheduler_source
    assert 'ctx.get("scheduler")' not in scheduler_source
    assert "BackgroundScheduler" not in notification_source
    assert "_REMINDER_QUEUE_FILE" not in notification_source
    assert "threading.Timer" not in notification_source
