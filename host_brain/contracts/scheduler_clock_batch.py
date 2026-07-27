"""Content-addressed snapshot contract for one Scheduler clock sweep.

The clock reads pending occurrences and newly-due definitions before it
executes any worker.  The repository returns both sets from one SQLite read
transaction and binds the exact rows, order, capture time and limit to a
digest.  Runtime code revalidates the digest before using the rows, so a
handler cannot change the remainder of the current sweep through a mutable
mapping alias.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import hashlib
import json
import re
from typing import Any


SCHEDULER_CLOCK_BATCH_SCHEMA_VERSION = "natsume.scheduler.clock-batch.v1"
SCHEDULER_CLOCK_BATCH_INVALID = "SCHEDULER_CLOCK_BATCH_INVALID"

_BATCH_REF = re.compile(
    r"^scheduler-clock-batch://sha256/(?P<digest>[0-9a-f]{64})$"
)
_BASE_KEYS = frozenset(
    {
        "schema_version",
        "captured_at_ms",
        "limit",
        "pending_occurrences",
        "due_definitions",
    }
)


class SchedulerClockBatchError(ValueError):
    """Stable fail-closed error for a malformed or drifted clock batch."""

    def __init__(self, detail: str) -> None:
        self.reason_code = SCHEDULER_CLOCK_BATCH_INVALID
        self.detail = str(detail or "Scheduler clock batch is invalid")
        super().__init__(f"{self.reason_code}: {self.detail}")


def build_scheduler_clock_batch(
    *,
    captured_at_ms: int,
    limit: int,
    pending_occurrences: Sequence[Mapping[str, Any]],
    due_definitions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Freeze one bounded batch and return its content-addressed payload."""

    base = _normalized_base(
        {
            "schema_version": SCHEDULER_CLOCK_BATCH_SCHEMA_VERSION,
            "captured_at_ms": captured_at_ms,
            "limit": limit,
            "pending_occurrences": list(pending_occurrences),
            "due_definitions": list(due_definitions),
        }
    )
    return {
        **base,
        "batch_ref": "scheduler-clock-batch://sha256/" + _sha256_json(base),
    }


def validate_scheduler_clock_batch(
    payload: Mapping[str, Any],
    *,
    expected_captured_at_ms: int | None = None,
    expected_limit: int | None = None,
) -> dict[str, Any]:
    """Reopen and independently validate one repository batch snapshot."""

    if not isinstance(payload, Mapping):
        raise SchedulerClockBatchError("clock batch must be an object")
    raw = dict(payload)
    if set(raw) != {*_BASE_KEYS, "batch_ref"}:
        raise SchedulerClockBatchError(
            "clock batch has missing or unknown fields"
        )
    batch_ref = str(raw.get("batch_ref") or "").strip()
    match = _BATCH_REF.fullmatch(batch_ref)
    if match is None:
        raise SchedulerClockBatchError(
            "clock batch ref is not content-addressed"
        )
    base = _normalized_base({key: raw[key] for key in _BASE_KEYS})
    if match.group("digest") != _sha256_json(base):
        raise SchedulerClockBatchError(
            "clock batch digest does not match its rows"
        )
    if (
        expected_captured_at_ms is not None
        and base["captured_at_ms"] != expected_captured_at_ms
    ):
        raise SchedulerClockBatchError(
            "clock batch capture time differs from the current sweep"
        )
    if expected_limit is not None and base["limit"] != expected_limit:
        raise SchedulerClockBatchError(
            "clock batch limit differs from the configured bound"
        )
    return {**base, "batch_ref": batch_ref}


def _normalized_base(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or set(payload) != _BASE_KEYS:
        raise SchedulerClockBatchError(
            "clock batch payload has missing or unknown fields"
        )
    captured_at_ms = _bounded_int(
        payload.get("captured_at_ms"),
        field_name="captured_at_ms",
        minimum=0,
    )
    limit = _bounded_int(
        payload.get("limit"),
        field_name="limit",
        minimum=1,
        maximum=1_000,
    )
    pending = _row_sequence(
        payload.get("pending_occurrences"),
        field_name="pending_occurrences",
    )
    due = _row_sequence(
        payload.get("due_definitions"),
        field_name="due_definitions",
    )
    if len(pending) + len(due) > limit:
        raise SchedulerClockBatchError(
            "clock batch contains more rows than its configured limit"
        )
    _require_unique(
        (
            str(row.get("occurrence_id") or "").strip()
            for row in pending
        ),
        field_name="pending occurrence",
    )
    _require_unique(
        (
            "due:"
            + str(row.get("task_id") or "").strip()
            + ":"
            + str(row.get("definition_revision") or "")
            + ":"
            + str(row.get("next_scheduled_at_ms") or "")
            for row in due
        ),
        field_name="due definition",
    )
    return {
        "schema_version": SCHEDULER_CLOCK_BATCH_SCHEMA_VERSION,
        "captured_at_ms": captured_at_ms,
        "limit": limit,
        "pending_occurrences": pending,
        "due_definitions": due,
    }


def _row_sequence(value: Any, *, field_name: str) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        raise SchedulerClockBatchError(f"{field_name} must be an array")
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping) or not all(
            isinstance(key, str) for key in raw
        ):
            raise SchedulerClockBatchError(
                f"{field_name}[{index}] must be an object with string keys"
            )
        # JSON round-trip is the immutable hand-off.  A shallow dict copy
        # would retain aliases to nested definition/payload objects.
        rows.append(_canonical_clone(dict(raw), field_name=f"{field_name}[{index}]"))
    return rows


def _canonical_clone(value: Mapping[str, Any], *, field_name: str) -> dict[str, Any]:
    try:
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        decoded = json.loads(encoded)
    except (TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise SchedulerClockBatchError(
            f"{field_name} must contain finite JSON values"
        ) from exc
    if not isinstance(decoded, dict):
        raise SchedulerClockBatchError(f"{field_name} must remain an object")
    return deepcopy(decoded)


def _require_unique(values: Any, *, field_name: str) -> None:
    seen: set[str] = set()
    for value in values:
        normalized = str(value or "").strip()
        if not normalized:
            raise SchedulerClockBatchError(
                f"{field_name} identity is missing"
            )
        if normalized in seen:
            raise SchedulerClockBatchError(
                f"{field_name} identity is duplicated"
            )
        seen.add(normalized)


def _bounded_int(
    value: Any,
    *,
    field_name: str,
    minimum: int,
    maximum: int | None = None,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
        or (maximum is not None and value > maximum)
    ):
        upper = "" if maximum is None else f" and <= {maximum}"
        raise SchedulerClockBatchError(
            f"{field_name} must be an integer >= {minimum}{upper}"
        )
    return value


def _sha256_json(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "SCHEDULER_CLOCK_BATCH_INVALID",
    "SCHEDULER_CLOCK_BATCH_SCHEMA_VERSION",
    "SchedulerClockBatchError",
    "build_scheduler_clock_batch",
    "validate_scheduler_clock_batch",
]
