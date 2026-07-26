"""Task-bound usage attestation for port-free read-only Delegate adapters.

The only production Delegate currently admitted by the Host is a synchronous
Pentest handoff.  It receives no model, tool, nested-dispatch, or effect port.
This contract makes that deliberately empty runtime surface measurable instead
of treating missing usage as an implicit zero.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import re
from typing import Any


READONLY_DELEGATE_USAGE_ATTESTATION_SCHEMA = (
    "Natsume.task_runtime.readonly_delegate_usage_attestation.v1"
)
READONLY_DELEGATE_EXECUTION_MODE = "PORT_FREE_READ_ONLY"

_DURABLE_REF = re.compile(
    r"^[a-z][a-z0-9+.-]{1,31}://"
    r"[A-Za-z0-9][A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]{0,511}$"
)
_FIELDS = frozenset(
    {
        "schema_version",
        "attestation_ref",
        "command_id",
        "graph_id",
        "graph_revision",
        "node_id",
        "attempt_id",
        "node_grant_ref",
        "target_id",
        "adapter_id",
        "invocation_ref",
        "execution_mode",
        "measurements",
        "side_effects_executed",
    }
)
_MEASUREMENT_FIELDS = frozenset(
    {
        "token_count",
        "tool_call_count",
        "retry_count",
        "model_cost_micros",
    }
)


class ReadonlyDelegateUsageAttestationError(ValueError):
    """A Delegate usage claim is missing, malformed, or identity-drifted."""


def build_readonly_delegate_usage_attestation(
    *,
    command_id: str,
    graph_id: str,
    graph_revision: int,
    node_id: str,
    attempt_id: str,
    node_grant_ref: str,
    target_id: str,
    adapter_id: str,
    invocation_ref: str,
) -> dict[str, Any]:
    """Build one digest-bound proof that a port-free adapter used no runtime."""

    subject = {
        "schema_version": READONLY_DELEGATE_USAGE_ATTESTATION_SCHEMA,
        "command_id": _required_text(command_id, "command_id"),
        "graph_id": _required_text(graph_id, "graph_id"),
        "graph_revision": _positive_int(graph_revision, "graph_revision"),
        "node_id": _required_text(node_id, "node_id"),
        "attempt_id": _required_text(attempt_id, "attempt_id"),
        "node_grant_ref": _required_ref(node_grant_ref, "node_grant_ref"),
        "target_id": _required_text(target_id, "target_id"),
        "adapter_id": _required_text(adapter_id, "adapter_id"),
        "invocation_ref": _required_ref(invocation_ref, "invocation_ref"),
        "execution_mode": READONLY_DELEGATE_EXECUTION_MODE,
        "measurements": {
            "token_count": 0,
            "tool_call_count": 0,
            "retry_count": 0,
            "model_cost_micros": 0,
        },
        "side_effects_executed": False,
    }
    digest = hashlib.sha256(_canonical_json(subject).encode("utf-8")).hexdigest()
    return {
        **subject,
        "attestation_ref": f"delegate-usage://sha256/{digest}",
    }


def validate_readonly_delegate_usage_attestation(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the canonical attestation or reject any non-zero/runtime drift."""

    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise ReadonlyDelegateUsageAttestationError(
            "readonly Delegate usage attestation shape is invalid"
        )
    measurements = value.get("measurements")
    if (
        not isinstance(measurements, Mapping)
        or set(measurements) != _MEASUREMENT_FIELDS
        or any(
            isinstance(measurements.get(field), bool)
            or not isinstance(measurements.get(field), int)
            or measurements.get(field) != 0
            for field in _MEASUREMENT_FIELDS
        )
    ):
        raise ReadonlyDelegateUsageAttestationError(
            "port-free Delegate measurements must be exact integer zeros"
        )
    subject = {
        "schema_version": str(value.get("schema_version") or ""),
        "command_id": _required_text(value.get("command_id"), "command_id"),
        "graph_id": _required_text(value.get("graph_id"), "graph_id"),
        "graph_revision": _positive_int(
            value.get("graph_revision"),
            "graph_revision",
        ),
        "node_id": _required_text(value.get("node_id"), "node_id"),
        "attempt_id": _required_text(value.get("attempt_id"), "attempt_id"),
        "node_grant_ref": _required_ref(
            value.get("node_grant_ref"),
            "node_grant_ref",
        ),
        "target_id": _required_text(value.get("target_id"), "target_id"),
        "adapter_id": _required_text(value.get("adapter_id"), "adapter_id"),
        "invocation_ref": _required_ref(
            value.get("invocation_ref"),
            "invocation_ref",
        ),
        "execution_mode": str(value.get("execution_mode") or ""),
        "measurements": {
            field: int(measurements[field])
            for field in sorted(_MEASUREMENT_FIELDS)
        },
        "side_effects_executed": value.get("side_effects_executed"),
    }
    if (
        subject["schema_version"] != READONLY_DELEGATE_USAGE_ATTESTATION_SCHEMA
        or subject["execution_mode"] != READONLY_DELEGATE_EXECUTION_MODE
        or subject["side_effects_executed"] is not False
    ):
        raise ReadonlyDelegateUsageAttestationError(
            "readonly Delegate usage attestation mode is invalid"
        )
    expected_ref = "delegate-usage://sha256/" + hashlib.sha256(
        _canonical_json(subject).encode("utf-8")
    ).hexdigest()
    actual_ref = _required_ref(value.get("attestation_ref"), "attestation_ref")
    if actual_ref != expected_ref:
        raise ReadonlyDelegateUsageAttestationError(
            "readonly Delegate usage attestation digest drifted"
        )
    return {**subject, "attestation_ref": actual_ref}


def _required_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 512:
        raise ReadonlyDelegateUsageAttestationError(
            f"{field_name} must be a non-empty bounded string"
        )
    return text


def _required_ref(value: Any, field_name: str) -> str:
    text = _required_text(value, field_name)
    if _DURABLE_REF.fullmatch(text) is None:
        raise ReadonlyDelegateUsageAttestationError(
            f"{field_name} must be a durable reference"
        )
    return text


def _positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ReadonlyDelegateUsageAttestationError(
            f"{field_name} must be a positive integer"
        )
    return int(value)


def _canonical_json(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ReadonlyDelegateUsageAttestationError(
            "readonly Delegate usage attestation must be finite JSON"
        ) from exc


__all__ = [
    "READONLY_DELEGATE_EXECUTION_MODE",
    "READONLY_DELEGATE_USAGE_ATTESTATION_SCHEMA",
    "ReadonlyDelegateUsageAttestationError",
    "build_readonly_delegate_usage_attestation",
    "validate_readonly_delegate_usage_attestation",
]
