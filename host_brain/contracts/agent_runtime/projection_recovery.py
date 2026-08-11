"""Exact neutral contract for seal-bound Internal Agent continuation."""

from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any


PROJECTION_RECOVERY_SCHEMA_VERSION = "Natsume.internal.projection_recovery.v1"
WORKER_TIMING_SCHEMA_VERSION = "Natsume.internal.worker_timing.v1"

_CONTEXT_FIELDS = frozenset(
    {
        "schema_version",
        "frozen_node_input",
        "verified_node_grant",
        "action_preview",
        "buffered_pre_acceptance_events",
    }
)
_ENVELOPE_FIELDS = frozenset({*_CONTEXT_FIELDS, "worker_timing"})
_TIMING_FIELDS = frozenset(
    {"schema_version", "started_ts_ms", "finished_ts_ms", "elapsed_ms"}
)


class ProjectionRecoveryContractError(ValueError):
    pass


def validate_projection_recovery_context(value: Any) -> dict[str, Any]:
    """Validate the caller-free context before canonical timing is attached."""

    context = _json_object(value, field_name="projection recovery context")
    if set(context) != _CONTEXT_FIELDS:
        raise ProjectionRecoveryContractError(
            "projection recovery context fields are invalid"
        )
    _validate_common(context)
    return context


def validate_projection_recovery_envelope(value: Any) -> dict[str, Any]:
    """Validate one immutable, seal-covered continuation envelope."""

    envelope = _json_object(value, field_name="projection recovery envelope")
    if set(envelope) != _ENVELOPE_FIELDS:
        raise ProjectionRecoveryContractError(
            "projection recovery envelope fields are invalid"
        )
    _validate_common(envelope)
    envelope["worker_timing"] = validate_worker_timing(
        envelope.get("worker_timing")
    )
    return envelope


def validate_worker_timing(value: Any) -> dict[str, Any]:
    """Validate the executor-owned timing proof, independently of transport."""

    timing = value
    if not isinstance(timing, Mapping):
        raise ProjectionRecoveryContractError("worker timing must be an object")
    normalized_timing = _json_object(timing, field_name="worker timing")
    if (
        set(normalized_timing) != _TIMING_FIELDS
        or normalized_timing.get("schema_version")
        != WORKER_TIMING_SCHEMA_VERSION
    ):
        raise ProjectionRecoveryContractError("worker timing fields are invalid")
    started = normalized_timing.get("started_ts_ms")
    finished = normalized_timing.get("finished_ts_ms")
    elapsed = normalized_timing.get("elapsed_ms")
    if (
        type(started) is not int
        or type(finished) is not int
        or type(elapsed) is not int
        or started < 0
        or finished < started
        or elapsed <= 0
    ):
        raise ProjectionRecoveryContractError("worker timing values are invalid")
    return normalized_timing


def _validate_common(value: Mapping[str, Any]) -> None:
    if value.get("schema_version") != PROJECTION_RECOVERY_SCHEMA_VERSION:
        raise ProjectionRecoveryContractError(
            "projection recovery schema version is invalid"
        )
    if not isinstance(value.get("frozen_node_input"), Mapping):
        raise ProjectionRecoveryContractError("frozen node input must be an object")
    if not isinstance(value.get("verified_node_grant"), Mapping):
        raise ProjectionRecoveryContractError("verified node grant must be an object")
    preview = value.get("action_preview")
    if preview is not None and not isinstance(preview, Mapping):
        raise ProjectionRecoveryContractError("action preview must be an object or null")
    if value.get("buffered_pre_acceptance_events") != 0:
        raise ProjectionRecoveryContractError(
            "pre-acceptance event count must remain zero"
        )


def _json_object(value: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ProjectionRecoveryContractError(f"{field_name} must be an object")
    try:
        normalized = json.loads(
            json.dumps(
                dict(value),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ProjectionRecoveryContractError(
            f"{field_name} must be finite canonical JSON"
        ) from exc
    if not isinstance(normalized, dict):
        raise ProjectionRecoveryContractError(f"{field_name} must be an object")
    return normalized


__all__ = [
    "PROJECTION_RECOVERY_SCHEMA_VERSION",
    "WORKER_TIMING_SCHEMA_VERSION",
    "ProjectionRecoveryContractError",
    "validate_projection_recovery_context",
    "validate_projection_recovery_envelope",
    "validate_worker_timing",
]
