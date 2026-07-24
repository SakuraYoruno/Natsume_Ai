"""Deterministic claim results for sealed Internal post-checkpoint rejections.

These references only identify a canonical Internal Agent checkpoint that was
executed but did not become accepted.  They do not represent an Agent result,
an acceptance effect, or a generic worker failure.  The TaskStore must reopen
the exact committed projection, reservation, historical lease and final
terminal state before a Secure Ingress claim may use one.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


INTERNAL_NONACCEPTANCE_RESULT_REF_PREFIX = (
    "task-terminal://internal-nonacceptance/sha256/"
)
INTERNAL_NONACCEPTANCE_TERMINAL_REASONS = frozenset(
    {
        "TERMINAL_VERIFICATION_FAILED",
        "TERMINAL_POST_REVIEW_CANCELLED",
    }
)
_SCHEMA_VERSION = "Natsume.internal_nonacceptance_claim_result.v1"


def build_internal_nonacceptance_result_ref(
    *,
    command_id: str,
    ingress_claim_ref: str,
    graph_id: str,
    graph_revision: int,
    node_id: str,
    attempt_id: str,
    historical_lease_id: str,
    completion_lease_id: str,
    reservation_id: str,
    projection_id: str,
    terminal_reason: str,
) -> str:
    """Build one replay-safe result for an exact non-accepted checkpoint.

    Every field is re-opened from durable Task authority.  In particular, the
    historical and completion leases distinguish an ordinary completion from a
    recovery takeover without trusting a worker-supplied terminal event.
    """

    normalized_reason = _required_terminal_reason(terminal_reason)
    payload = {
        "schema_version": _SCHEMA_VERSION,
        "command_id": _required_text(command_id, field_name="command_id"),
        "ingress_claim_ref": _required_text(
            ingress_claim_ref,
            field_name="ingress_claim_ref",
        ),
        "graph_id": _required_text(graph_id, field_name="graph_id"),
        "graph_revision": _positive_int(graph_revision, field_name="graph_revision"),
        "node_id": _required_text(node_id, field_name="node_id"),
        "attempt_id": _required_text(attempt_id, field_name="attempt_id"),
        "historical_lease_id": _required_text(
            historical_lease_id,
            field_name="historical_lease_id",
        ),
        "completion_lease_id": _required_text(
            completion_lease_id,
            field_name="completion_lease_id",
        ),
        "reservation_id": _required_text(
            reservation_id,
            field_name="reservation_id",
        ),
        "projection_id": _required_text(projection_id, field_name="projection_id"),
        "terminal_reason": normalized_reason,
    }
    raw = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return INTERNAL_NONACCEPTANCE_RESULT_REF_PREFIX + hashlib.sha256(raw).hexdigest()


def _required_terminal_reason(value: Any) -> str:
    text = _required_text(value, field_name="terminal_reason")
    if text not in INTERNAL_NONACCEPTANCE_TERMINAL_REASONS:
        raise ValueError("terminal_reason is not a supported non-acceptance terminal")
    return text


def _required_text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or any(
        ord(char) < 32 or ord(char) == 127 for char in value
    ):
        raise ValueError(f"{field_name} is invalid")
    text = value.strip()
    if not text or len(text) > 1_024:
        raise ValueError(f"{field_name} is invalid")
    return text


def _positive_int(value: Any, *, field_name: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


__all__ = [
    "INTERNAL_NONACCEPTANCE_RESULT_REF_PREFIX",
    "INTERNAL_NONACCEPTANCE_TERMINAL_REASONS",
    "build_internal_nonacceptance_result_ref",
]
