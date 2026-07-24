"""Deterministic Secure Ingress result references for exact Internal budget terminals.

The reference is deliberately derived from facts that the canonical TaskStore
can reopen: it is not supplied by a worker and it does not stand in for an
accepted Internal Agent result receipt.  It is only valid for the narrow
post-checkpoint C-boundary budget-rejection terminal.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


INTERNAL_BUDGET_REJECTION_RESULT_REF_PREFIX = (
    "task-terminal://internal-budget-rejection/sha256/"
)
_SCHEMA_VERSION = "Natsume.internal_budget_rejection_claim_result.v1"


def build_internal_budget_rejection_result_ref(
    *,
    command_id: str,
    ingress_claim_ref: str,
    graph_id: str,
    graph_revision: int,
    node_id: str,
    attempt_id: str,
    reservation_id: str,
    budget_id: str,
    usage_id: str,
) -> str:
    """Build a stable reference for one exact C-boundary terminal.

    Every identity is re-opened from the durable TaskStore before this helper
    is used in production.  The digest gives the outer Secure Ingress claim a
    replay-safe terminal identity without inventing an Agent result or an
    effect receipt for a rejected execution.
    """

    revision = _positive_int(graph_revision, field_name="graph_revision")
    payload = {
        "schema_version": _SCHEMA_VERSION,
        "command_id": _required_text(command_id, field_name="command_id"),
        "ingress_claim_ref": _required_text(
            ingress_claim_ref,
            field_name="ingress_claim_ref",
        ),
        "graph_id": _required_text(graph_id, field_name="graph_id"),
        "graph_revision": revision,
        "node_id": _required_text(node_id, field_name="node_id"),
        "attempt_id": _required_text(attempt_id, field_name="attempt_id"),
        "reservation_id": _required_text(
            reservation_id,
            field_name="reservation_id",
        ),
        "budget_id": _required_text(budget_id, field_name="budget_id"),
        "usage_id": _required_text(usage_id, field_name="usage_id"),
    }
    raw = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return INTERNAL_BUDGET_REJECTION_RESULT_REF_PREFIX + hashlib.sha256(raw).hexdigest()


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
    "INTERNAL_BUDGET_REJECTION_RESULT_REF_PREFIX",
    "build_internal_budget_rejection_result_ref",
]
