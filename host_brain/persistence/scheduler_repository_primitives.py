"""Shared validation primitives for Scheduler persistence owners."""

from __future__ import annotations

import re
from typing import Any


SCHEDULER_DEFINITION_INVALID = "SCHEDULER_DEFINITION_INVALID"
SCHEDULER_CLOCK_RETRY_INVALID = "SCHEDULER_CLOCK_RETRY_INVALID"
SCHEDULER_CLOCK_RETRY_CONFLICT = "SCHEDULER_CLOCK_RETRY_CONFLICT"

_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+-]{0,254}$")
_REFERENCE_RE = re.compile(
    r"^[a-z][a-z0-9+.-]{1,31}://[A-Za-z0-9][A-Za-z0-9._~:/@!$&'()*+,;=%-]{0,475}$"
)


class SchedulerRepositoryError(RuntimeError):
    """Fail-closed Scheduler persistence error with a stable reason code."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or SCHEDULER_DEFINITION_INVALID)
        self.detail = str(detail or "scheduler persistence failed")
        super().__init__(f"{self.reason_code}: {self.detail}")

    def to_dict(self) -> dict[str, str]:
        return {"reason_code": self.reason_code, "detail": self.detail}


def _required_task_id(value: Any) -> str:
    task_id = str(value or "").strip()
    if _TASK_ID_RE.fullmatch(task_id) is None:
        raise SchedulerRepositoryError(
            SCHEDULER_DEFINITION_INVALID,
            "task_id is invalid",
        )
    return task_id


def _required_reference(
    value: Any,
    *,
    field_name: str,
    reason_code: str,
) -> str:
    normalized = _required_text(
        value,
        field_name=field_name,
        limit=512,
        reason_code=reason_code,
    )
    if _REFERENCE_RE.fullmatch(normalized) is None:
        raise SchedulerRepositoryError(
            reason_code,
            f"{field_name} is not a durable reference",
        )
    return normalized


def _required_text(
    value: Any,
    *,
    field_name: str,
    limit: int,
    reason_code: str,
) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > limit:
        raise SchedulerRepositoryError(
            reason_code,
            f"{field_name} is missing or too long",
        )
    return normalized


def _required_int(
    value: Any,
    *,
    field_name: str,
    minimum: int,
    reason_code: str,
) -> int:
    if type(value) is not int or value < minimum:
        raise SchedulerRepositoryError(
            reason_code,
            f"{field_name} must be an integer >= {minimum}",
        )
    return value


def _clock_retry_required_text(
    value: Any,
    *,
    field_name: str,
    limit: int,
) -> str:
    return _required_text(
        value,
        field_name=field_name,
        limit=limit,
        reason_code=SCHEDULER_CLOCK_RETRY_INVALID,
    )


def _clock_retry_required_reference(value: Any, *, field_name: str) -> str:
    return _required_reference(
        value,
        field_name=field_name,
        reason_code=SCHEDULER_CLOCK_RETRY_INVALID,
    )


def _clock_retry_required_int(
    value: Any,
    *,
    field_name: str,
    minimum: int,
) -> int:
    return _required_int(
        value,
        field_name=field_name,
        minimum=minimum,
        reason_code=SCHEDULER_CLOCK_RETRY_INVALID,
    )


__all__ = [
    "SCHEDULER_CLOCK_RETRY_CONFLICT",
    "SCHEDULER_CLOCK_RETRY_INVALID",
    "SCHEDULER_DEFINITION_INVALID",
    "SchedulerRepositoryError",
]
