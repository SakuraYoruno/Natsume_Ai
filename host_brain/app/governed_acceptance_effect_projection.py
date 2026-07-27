"""Project post-Acceptance Effect dispatch state into Host audit refs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


async def dispatch_accepted_effects(
    event: Mapping[str, Any],
    dispatcher: Any,
) -> dict[str, Any]:
    projected = dict(event)
    receipt = projected.get("execution_receipt")
    if dispatcher is None or not isinstance(receipt, Mapping):
        return projected
    if (
        not bool(receipt.get("accepted"))
        or str(receipt.get("status") or "") != "ACCEPTED"
    ):
        return projected
    graph_id = str(receipt.get("graph_id") or "").strip()
    graph_revision = int(receipt.get("graph_revision") or 0)
    if not graph_id or graph_revision < 1:
        return projected
    results = await dispatcher.dispatch_graph(
        graph_id=graph_id,
        graph_revision=graph_revision,
    )
    statuses = [str(row.get("status") or "") for row in results]
    recovery_pending = sum(
        bool(row.get("recovery_pending"))
        or str(row.get("dispatch_state") or "") == "RECOVERY_PENDING"
        for row in results
    )
    audit_refs = dict(projected.get("audit_refs") or {})
    audit_refs["acceptance_effects"] = {
        "count": len(results),
        "committed": statuses.count("COMMITTED"),
        "failed": statuses.count("FAILED"),
        "pending": sum(
            status not in {"COMMITTED", "FAILED"} for status in statuses
        ),
        "recovery_pending": recovery_pending,
        "dispatch_status": (
            "FAILED"
            if "FAILED" in statuses
            else "RECOVERY_PENDING"
            if recovery_pending
            else "COMMITTED"
            if results and all(status == "COMMITTED" for status in statuses)
            else "NO_EFFECTS"
        ),
    }
    projected["audit_refs"] = audit_refs
    return projected


__all__ = ["dispatch_accepted_effects"]
