"""Historical-fence-safe Lease acquisition for Operation checkpoint takeover."""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Any

from .admission import AdmittedExecution
from .coordinator_records import _status, _text, _version
from .errors import GOVERNED_OPERATION_RECOVERY_REQUIRED, GovernedRuntimeError
from .ports import TaskStorePort
from .runtime_outbox import RuntimeOutbox


async def acquire_checkpoint_takeover_lease(
    *,
    task_store: TaskStorePort,
    runtime_outbox: RuntimeOutbox,
    admitted: AdmittedExecution,
    attempt: Mapping[str, Any],
    historical_lease: Mapping[str, Any],
    owner_id: str,
    lease_ttl_ms: int,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    attempt_record = dict(attempt)
    attempt_status = _status(attempt_record)
    if attempt_status == "SUCCEEDED":
        return attempt_record, None
    if attempt_status not in {"LEASED", "RUNNING", "RECOVERABLE"}:
        _blocked(f"committed checkpoint Attempt cannot continue from {attempt_status}")
    attempt_id = _required_text(attempt_record.get("attempt_id"), "attempt_id")
    previous = await _maybe_await(task_store.get_active_lease(attempt_id))
    expected_attempt_version = _version(attempt_record)
    lease_outbox = runtime_outbox.build(
        command_id=admitted.command_id,
        aggregate_type="TASK_ATTEMPT",
        aggregate_id=attempt_id,
        aggregate_version=expected_attempt_version + 1,
        event_type="TASK_ATTEMPT_LEASED",
        attempt_id=attempt_id,
    )

    async def _acquire() -> Any:
        return await _maybe_await(
            task_store.acquire_lease(
                attempt_id,
                owner_id=owner_id,
                ttl_ms=lease_ttl_ms,
                expected_attempt_version=expected_attempt_version,
                existing_operation_reservation_id=admitted.reservation_id,
                outbox=lease_outbox,
            )
        )

    try:
        claimed = await _acquire()
    except Exception as exc:
        if _text(getattr(exc, "reason_code", None)):
            raise GovernedRuntimeError(
                GOVERNED_OPERATION_RECOVERY_REQUIRED,
                "committed checkpoint is waiting for its former lease to expire",
            ) from exc
        try:
            claimed = await _acquire()
        except Exception as replay_exc:
            raise GovernedRuntimeError(
                GOVERNED_OPERATION_RECOVERY_REQUIRED,
                "committed checkpoint is waiting for its former lease to expire",
            ) from replay_exc
    if not isinstance(claimed, Mapping):
        _blocked("online takeover returned no durable lease")
    lease = dict(claimed)
    _assert_advanced_fence(
        lease,
        former=historical_lease,
        detail="historical owner",
    )
    if previous is not None:
        _assert_advanced_fence(lease, former=previous, detail="former owner")
    if (
        _status(lease) != "ACTIVE"
        or _text(lease.get("attempt_id")) != attempt_id
        or _text(lease.get("owner_id")) != owner_id
    ):
        _blocked("online takeover lease binding is invalid")
    _required_positive_int(lease.get("fencing_token"), "takeover fencing_token")
    refreshed = await _maybe_await(task_store.get_attempt(attempt_id))
    if not isinstance(refreshed, Mapping):
        _blocked("online takeover lost its durable Attempt")
    refreshed_attempt = dict(refreshed)
    if (
        _status(refreshed_attempt) != "LEASED"
        or _text(refreshed_attempt.get("owner_id")) != owner_id
    ):
        _blocked("online takeover Attempt binding is invalid")
    return refreshed_attempt, lease


def _assert_advanced_fence(
    lease: Mapping[str, Any],
    *,
    former: Mapping[str, Any],
    detail: str,
) -> None:
    if (
        _text(former.get("lease_id")) == _text(lease.get("lease_id"))
        or _required_positive_int(
            former.get("fencing_token"),
            f"{detail} fencing_token",
        )
        >= _required_positive_int(
            lease.get("fencing_token"),
            "takeover fencing_token",
        )
    ):
        _blocked(f"online takeover did not advance the {detail} fence")


def _required_text(value: Any, field_name: str) -> str:
    normalized = _text(value)
    if not normalized:
        _blocked(f"committed checkpoint has no {field_name}")
    return normalized


def _required_positive_int(value: Any, field_name: str) -> int:
    if type(value) is not int or value < 1:
        _blocked(f"committed checkpoint has invalid {field_name}")
    return value


def _blocked(detail: str) -> None:
    raise GovernedRuntimeError(GOVERNED_OPERATION_RECOVERY_REQUIRED, detail)


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


__all__ = ["acquire_checkpoint_takeover_lease"]
