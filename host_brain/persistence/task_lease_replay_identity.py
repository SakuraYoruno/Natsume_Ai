"""Pure identity and row-binding checks for Lease acquisition replay."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from host_brain.persistence.task_store_types import AttemptStatus, LeaseStatus


def build_lease_acquisition_identity(
    *,
    attempt_id: str,
    owner_id: str,
    ttl_ms: int,
    expected_attempt_version: int,
    absent_operation_reservation_id: str | None,
    existing_operation_reservation_id: str | None,
) -> tuple[str, str]:
    """Bind every acquisition parameter into deterministic row identities."""

    material = json.dumps(
        {
            "schema_version": "lease-acquisition-request/v1",
            "attempt_id": str(attempt_id),
            "owner_id": str(owner_id),
            "ttl_ms": int(ttl_ms),
            "expected_attempt_version": int(expected_attempt_version),
            "absent_operation_reservation_id": str(
                absent_operation_reservation_id or ""
            ),
            "existing_operation_reservation_id": str(
                existing_operation_reservation_id or ""
            ),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    digest = hashlib.sha256(material).hexdigest()
    return f"lease::{digest}", f"heartbeat::{digest}:0"


def lease_acquisition_replay_binding_is_valid(
    *,
    attempt: Any,
    lease: Any,
    heartbeat: Any,
    attempt_id: str,
    owner_id: str,
    expected_lease_id: str,
    expected_heartbeat_id: str,
    ttl_ms: int,
    now_ts_ms: int,
    previous_fencing_token: int,
) -> bool:
    """Validate the immutable acquisition rows without owning persistence."""

    try:
        acquired_ts_ms = int(lease["acquired_ts_ms"])
        expires_ts_ms = int(lease["expires_ts_ms"])
        fencing_token = int(lease["fencing_token"])
        return (
            str(attempt["status"]) == AttemptStatus.LEASED.value
            and str(attempt["owner_id"] or "") == str(owner_id)
            and int(attempt["acquired_ts_ms"]) == acquired_ts_ms
            and str(lease["lease_id"]) == str(expected_lease_id)
            and str(lease["attempt_id"]) == str(attempt_id)
            and str(lease["owner_id"] or "") == str(owner_id)
            and str(lease["status"]) == LeaseStatus.ACTIVE.value
            and int(lease["status_version"]) == 0
            and lease["released_ts_ms"] is None
            and int(lease["heartbeat_ts_ms"]) == acquired_ts_ms
            and expires_ts_ms == acquired_ts_ms + int(ttl_ms)
            and expires_ts_ms > int(now_ts_ms)
            and fencing_token == int(previous_fencing_token) + 1
            and str(heartbeat["heartbeat_id"]) == str(expected_heartbeat_id)
            and str(heartbeat["lease_id"]) == str(expected_lease_id)
            and str(heartbeat["attempt_id"]) == str(attempt_id)
            and str(heartbeat["owner_id"]) == str(owner_id)
            and int(heartbeat["fencing_token"]) == fencing_token
            and int(heartbeat["observed_ts_ms"]) == acquired_ts_ms
            and int(heartbeat["expires_ts_ms"]) == expires_ts_ms
        )
    except (KeyError, TypeError, ValueError):
        return False


__all__ = [
    "build_lease_acquisition_identity",
    "lease_acquisition_replay_binding_is_valid",
]
