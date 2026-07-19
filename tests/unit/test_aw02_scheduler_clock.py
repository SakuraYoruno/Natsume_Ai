from __future__ import annotations

import hashlib
import json
import asyncio
from datetime import datetime, timezone

import pytest

from host_brain.scheduler.clock import SchedulerClock, _next_scheduled_at_ms
from host_brain.scheduler.contracts import SchedulerDefinitionSnapshot
from host_brain.scheduler.governed_runtime import (
    SCHEDULER_RUNTIME_MODE_BLOCKED,
    GovernedSchedulerRuntimeError,
)
from host_brain.secure_ingress import SecureIngressAuthority


NOW = 1_800_000_000_000


class _RetryStateRepository:
    def __init__(self) -> None:
        self.retry: dict[str, dict] = {}

    def claim_clock_retry(self, **kwargs):
        key = kwargs["retry_key"]
        current = self.retry.get(key)
        if current is not None and (
            current["next_retry_at_ms"] > kwargs["at_ms"]
            or (
                current.get("lease_owner_id") is not None
                and int(current.get("lease_expires_at_ms") or 0) > kwargs["at_ms"]
            )
        ):
            return {**current, "claimed": False}
        fence = int((current or {}).get("lease_fencing_token") or 0) + 1
        value = {
            **dict(current or {}),
            **kwargs,
            "failure_count": int((current or {}).get("failure_count") or 0),
            "next_retry_at_ms": int((current or {}).get("next_retry_at_ms") or 0),
            "lease_owner_id": kwargs["owner_id"],
            "lease_fencing_token": fence,
            "lease_expires_at_ms": kwargs["at_ms"] + kwargs["lease_duration_ms"],
            "claimed": True,
        }
        self.retry[key] = value
        return value

    def renew_clock_retry(self, **kwargs):
        value = self.retry[kwargs["retry_key"]]
        assert value["lease_owner_id"] == kwargs["owner_id"]
        assert value["lease_fencing_token"] == kwargs["fencing_token"]
        assert value["lease_expires_at_ms"] > kwargs["at_ms"]
        value.update(
            lease_expires_at_ms=kwargs["at_ms"] + kwargs["lease_duration_ms"],
            at_ms=kwargs["at_ms"],
        )
        return dict(value)

    def defer_clock_retry(self, **kwargs):
        value = self.retry[kwargs["retry_key"]]
        assert value["lease_owner_id"] == kwargs["owner_id"]
        assert value["lease_fencing_token"] == kwargs["fencing_token"]
        assert value["lease_expires_at_ms"] > kwargs["at_ms"]
        value.update(
            failure_count=int(value["failure_count"]) + 1,
            next_retry_at_ms=kwargs["next_retry_at_ms"],
            lease_owner_id=None,
            lease_expires_at_ms=None,
            last_reason_code=kwargs["reason_code"],
        )
        return dict(value)

    def clear_clock_retry(self, **kwargs):
        value = self.retry[kwargs["retry_key"]]
        assert value["lease_owner_id"] == kwargs["owner_id"]
        assert value["lease_fencing_token"] == kwargs["fencing_token"]
        assert value["lease_expires_at_ms"] > kwargs["at_ms"]
        value.update(
            failure_count=0,
            next_retry_at_ms=0,
            lease_owner_id=None,
            lease_expires_at_ms=None,
            last_reason_code=None,
        )
        return True

    def transfer_due_retry(self, kwargs, occurrence):
        due = self.retry.pop(kwargs["due_retry_key"])
        assert due["retry_kind"] == "DUE"
        assert due["lease_owner_id"] == kwargs["lease_owner_id"]
        assert due["lease_fencing_token"] == kwargs["lease_fencing_token"]
        occurrence_id = occurrence["occurrence_id"]
        claim = {
            **due,
            "retry_key": occurrence_id,
            "retry_kind": "OCCURRENCE",
            "occurrence_id": occurrence_id,
            "lease_expires_at_ms": due["at_ms"] + kwargs["lease_duration_ms"],
            "claimed": True,
        }
        self.retry[occurrence_id] = claim
        return {**occurrence, "clock_retry_claim": dict(claim)}

    def clock_retry_summary(self):
        failed = [row for row in self.retry.values() if row["failure_count"] > 0]
        return {
            "retry_count": len(failed),
            "next_retry_at_ms": min(
                (row["next_retry_at_ms"] for row in failed),
                default=None,
            ),
        }


def _request_id(task_id: str, revision: int, scheduled_at_ms: int) -> str:
    material = json.dumps(
        {
            "definition_revision": revision,
            "scheduled_at_epoch_ms": scheduled_at_ms,
            "schema_version": "natsume.scheduler.clock-authority.v1",
            "task_id": task_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "scheduler.clock.request." + hashlib.sha256(material.encode()).hexdigest()[:48]


def _authority(task_id: str, revision: int, scheduled_at_ms: int) -> SecureIngressAuthority:
    suffix = hashlib.sha256(f"{task_id}:{scheduled_at_ms}".encode()).hexdigest()
    return SecureIngressAuthority(
        allowed=True,
        duplicate=False,
        reason_code="SECURE_INGRESS_ALLOWED",
        request_id=_request_id(task_id, revision, scheduled_at_ms),
        principal_id="scheduler-clock-service",
        claim_ref=f"secure-ingress-claim://clock/{suffix}",
        audit_ref=f"audit://secure-ingress/clock/{suffix}",
        body_sha256=hashlib.sha256(f"body:{suffix}".encode()).hexdigest(),
    )


def _occurrence(
    task_id: str,
    *,
    scheduled_at_ms: int,
    occurrence_id: str,
    authority: SecureIngressAuthority,
) -> dict:
    return {
        "occurrence_id": occurrence_id,
        "task_id": task_id,
        "definition_revision": 1,
        "source": "CLOCK",
        "request_id": authority.request_id,
        "principal_id": authority.principal_id,
        "ingress_claim_ref": authority.claim_ref,
        "ingress_audit_ref": authority.audit_ref,
        "body_sha256": authority.body_sha256,
        "scheduled_at_ms": scheduled_at_ms,
        "payload": {},
    }


@pytest.mark.asyncio
async def test_clock_recovers_durable_authority_then_preflights_and_admits_due() -> None:
    events: list[str] = []
    pending_authority = _authority("pending_task", 1, NOW - 1_000)
    pending = _occurrence(
        "pending_task",
        scheduled_at_ms=NOW - 1_000,
        occurrence_id="scheduleroccurrence://sha256/" + "1" * 64,
        authority=pending_authority,
    )
    due = {
        "task_id": "due_task",
        "definition_revision": 1,
        "head_version": 3,
        "enabled": True,
        "next_scheduled_at_ms": NOW,
        "definition": {
            "task_id": "due_task",
            "trigger_mode": "interval",
            "trigger_config": {"seconds": 30},
            "intent_template": {},
            "worker_key": "scheduler_production",
            "priority": "P2",
            "mode_allowlist": ["normal"],
            "resource_profile": {},
            "retry_policy": {"max_attempts": 1},
            "enabled": True,
        },
    }

    class Repository(_RetryStateRepository):
        def list_pending_occurrences(self, *, limit):
            assert limit == 10
            return [pending]

        def list_due(self, *, due_at_ms, limit):
            assert due_at_ms == NOW
            assert limit == 9
            return [due]

        def admit_due_occurrence(self, **kwargs):
            events.append("admit")
            assert kwargs["next_scheduled_at_ms"] == NOW + 30_000
            assert kwargs["authority"]["principal_id"] == "scheduler-clock-service"
            auth = SecureIngressAuthority(**kwargs["authority"])
            return self.transfer_due_retry(
                kwargs,
                _occurrence(
                    "due_task",
                    scheduled_at_ms=NOW,
                    occurrence_id="scheduleroccurrence://sha256/" + "2" * 64,
                    authority=auth,
                ),
            )

    class Ingress:
        def evaluate_clock_occurrence(self, **kwargs):
            events.append("ingress")
            return _authority(
                kwargs["task_id"],
                kwargs["definition_revision"],
                kwargs["scheduled_at_epoch_ms"],
            )

    class Service:
        def preflight_definition(self, definition):
            events.append("preflight")
            assert definition.task_id == "due_task"
            return "normal"

        async def execute_occurrence(self, *, occurrence, authority, received_at_ms):
            del authority
            events.append("execute:" + occurrence.task_id)
            assert received_at_ms == NOW
            return {"done": True}

    clock = SchedulerClock(
        definition_repository=Repository(),
        command_ingress=Ingress(),
        command_service=Service(),
        owner_id="test-clock-owner",
        now_ms=lambda: NOW,
        poll_interval_s=60,
        limit=10,
    )
    result = await clock.run_once()

    assert result == {
        "received_at_ms": NOW,
        "recovered": 1,
        "admitted": 1,
        "executed": 2,
        "deferred": 0,
        "failed": 0,
        "failures": [],
    }
    assert events == [
        "execute:pending_task",
        "preflight",
        "ingress",
        "admit",
        "execute:due_task",
    ]


@pytest.mark.asyncio
async def test_clock_policy_block_does_not_mint_authority_or_advance_head() -> None:
    calls = {"ingress": 0, "admit": 0, "execute": 0}
    due = {
        "task_id": "blocked_task",
        "definition_revision": 1,
        "head_version": 1,
        "enabled": True,
        "next_scheduled_at_ms": NOW,
        "definition": {
            "task_id": "blocked_task",
            "trigger_mode": "once",
            "trigger_config": {},
            "intent_template": {},
            "worker_key": "scheduler_production",
            "priority": "P3",
            "mode_allowlist": ["normal"],
            "resource_profile": {},
            "retry_policy": {"max_attempts": 1},
            "enabled": True,
        },
    }

    class Repository(_RetryStateRepository):
        def list_pending_occurrences(self, *, limit):
            return []

        def list_due(self, *, due_at_ms, limit):
            return [due]

        def admit_due_occurrence(self, **kwargs):
            calls["admit"] += 1
            raise AssertionError("blocked definition must not advance")

    class Ingress:
        def evaluate_clock_occurrence(self, **kwargs):
            calls["ingress"] += 1
            raise AssertionError("blocked definition must not mint authority")

    class Service:
        def preflight_definition(self, definition):
            raise GovernedSchedulerRuntimeError(
                SCHEDULER_RUNTIME_MODE_BLOCKED,
                "blocked",
            )

        async def execute_occurrence(self, **kwargs):
            calls["execute"] += 1

    clock = SchedulerClock(
        definition_repository=Repository(),
        command_ingress=Ingress(),
        command_service=Service(),
        owner_id="test-clock-owner",
        now_ms=lambda: NOW,
    )
    result = await clock.run_once()

    assert result["failed"] == 1
    assert result["failures"][0]["reason_code"] == SCHEDULER_RUNTIME_MODE_BLOCKED
    assert calls == {"ingress": 0, "admit": 0, "execute": 0}

    # The durable due identity is backed off; it is not re-evaluated every
    # poll while the server mode remains blocked.
    replay = await clock.run_once()
    assert replay["failed"] == 0
    assert replay["deferred"] == 1
    assert calls == {"ingress": 0, "admit": 0, "execute": 0}


def test_weekly_cron_scans_to_the_next_allowed_weekday() -> None:
    sunday = int(datetime(2026, 7, 12, 4, 0, tzinfo=timezone.utc).timestamp() * 1_000)
    definition = SchedulerDefinitionSnapshot.from_mapping(
        {
            "task_id": "weekly_growth",
            "definition_revision": 1,
            "head_version": 1,
            "enabled": True,
            "definition": {
                "task_id": "weekly_growth",
                "trigger_mode": "cron",
                "trigger_config": {
                    "day_of_week": 0,
                    "hour": 3,
                    "minute": 0,
                    "timezone": "UTC",
                },
                "intent_template": {},
                "worker_key": "scheduler_production",
                "priority": "LOW",
                "enabled": True,
            },
        }
    )
    next_at = _next_scheduled_at_ms(definition, scheduled_at_ms=sunday)
    assert next_at == int(
        datetime(2026, 7, 13, 3, 0, tzinfo=timezone.utc).timestamp() * 1_000
    )


@pytest.mark.asyncio
async def test_clock_shutdown_is_bounded_without_cancelling_runtime_dispatch() -> None:
    authority = _authority("slow_task", 1, NOW)
    pending = _occurrence(
        "slow_task",
        scheduled_at_ms=NOW,
        occurrence_id="scheduleroccurrence://sha256/" + "3" * 64,
        authority=authority,
    )
    started = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()
    cancelled = False

    class Repository(_RetryStateRepository):
        def list_pending_occurrences(self, *, limit):
            return [pending]

        def list_due(self, *, due_at_ms, limit):
            return []

    class Service:
        async def execute_occurrence(self, **_):
            nonlocal cancelled
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled = True
                raise
            finally:
                finished.set()

    clock = SchedulerClock(
        definition_repository=Repository(),
        command_ingress=object(),
        command_service=Service(),
        owner_id="test-clock-owner",
        now_ms=lambda: NOW,
        poll_interval_s=60,
        shutdown_timeout_s=0.05,
    )
    await clock.activate()
    await asyncio.wait_for(started.wait(), timeout=1)
    await asyncio.wait_for(clock.aclose(), timeout=0.5)

    assert cancelled is False
    assert clock.health()["dispatch_in_flight"] == 1
    release.set()
    await asyncio.wait_for(finished.wait(), timeout=1)
    for _ in range(10):
        if clock.health()["dispatch_in_flight"] == 0:
            break
        await asyncio.sleep(0)
    assert clock.health()["dispatch_in_flight"] == 0


@pytest.mark.asyncio
async def test_clock_failure_backoff_starts_from_actual_failure_time() -> None:
    current_ms = [NOW]
    authority = _authority("failed_late", 1, NOW)
    pending = _occurrence(
        "failed_late",
        scheduled_at_ms=NOW,
        occurrence_id="scheduleroccurrence://sha256/" + "4" * 64,
        authority=authority,
    )
    repository = _RetryStateRepository()
    repository.list_pending_occurrences = lambda *, limit: [pending]  # type: ignore[attr-defined]
    repository.list_due = lambda *, due_at_ms, limit: []  # type: ignore[attr-defined]

    class Service:
        async def execute_occurrence(self, **_):
            current_ms[0] = NOW + 7_000
            raise RuntimeError("late failure")

    clock = SchedulerClock(
        definition_repository=repository,
        command_ingress=object(),
        command_service=Service(),
        owner_id="failure-clock-owner",
        now_ms=lambda: current_ms[0],
        failure_backoff_initial_s=5,
        retry_lease_duration_s=60,
    )

    result = await clock.run_once()

    assert result["failed"] == 1
    retry = repository.retry[pending["occurrence_id"]]
    assert retry["failure_count"] == 1
    assert retry["next_retry_at_ms"] == NOW + 12_000
