"""Fail-closed guards for a denied durable Review continuation.

The scheduler may terminalize an exact rejected/expired Review without a new
worker grant.  This module deliberately contains only identity and state
guards; it never issues authority or transitions durable state.
"""

from __future__ import annotations

from collections.abc import Mapping, Set

from .errors import GOVERNED_DURABLE_STATE_CONFLICT, GovernedRuntimeError


_WAITING_REVIEW_STATUSES = frozenset(
    {"WAITING_APPROVAL", "WAITING_ACCEPTANCE_REVIEW"}
)
_ACTIVE_EXECUTION_STATUSES = frozenset(
    {"LEASED", "RUNNING", "EXECUTED", "VERIFYING", "VERIFIED"}
)


def normalize_denied_review_node(
    value: str | None,
    expected_node_ids: Set[str],
    grant_refs: Mapping[str, str],
) -> str | None:
    """Accept only an exact no-worker-grant terminal Review target."""

    node_id = str(value or "").strip()
    if not node_id:
        return None
    if node_id not in expected_node_ids or grant_refs:
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "denied Review continuation must target one admitted node without worker grants",
        )
    return node_id


def assert_denied_review_waiting_node(
    node_id: str | None,
    *,
    statuses: Mapping[str, str],
) -> None:
    """Reject stale/re-targeted claims before any execution-path mutation."""

    if not node_id:
        return
    if (
        statuses.get(node_id) not in _WAITING_REVIEW_STATUSES
        or any(
            status in _ACTIVE_EXECUTION_STATUSES
            for candidate_id, status in statuses.items()
            if candidate_id != node_id
        )
    ):
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            "denied Review continuation is no longer an exact waiting TaskNode",
        )


def assert_denied_review_gate(
    node_id: str | None,
    *,
    disposition: str,
    phase: str,
) -> None:
    """Require the ReviewStore to still report the same terminal denial."""

    if node_id and str(disposition or "").strip() != "REVIEW_DENIED":
        raise GovernedRuntimeError(
            GOVERNED_DURABLE_STATE_CONFLICT,
            f"denied Review continuation no longer has a terminal {phase} decision",
        )


__all__ = [
    "assert_denied_review_gate",
    "assert_denied_review_waiting_node",
    "normalize_denied_review_node",
]
