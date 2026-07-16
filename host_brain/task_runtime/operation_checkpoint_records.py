"""Pure recovery execution-context rendering for committed checkpoints."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from natsume_contracts.task.v1 import HostCommand

from .admission import AdmittedExecution
from .coordinator_records import _node_input_refs, _text
from .operation_recovery_runtime import CommittedOperationCheckpoint
from .worker_registry import GovernedWorkerRoute


def build_checkpoint_execution_context(
    *,
    admitted: AdmittedExecution,
    command: HostCommand,
    budget: Any,
    node: Mapping[str, Any],
    checkpoint: CommittedOperationCheckpoint,
    node_grant_ref: str | None,
    owner_id: str,
) -> dict[str, Any]:
    if node_grant_ref is None:
        recovery_target_id = command.target_id
    else:
        frozen_route = GovernedWorkerRoute.parse(admitted.worker_route)
        recovery_target_id = frozen_route.target_id or command.target_id
    active_lease = checkpoint.lease
    context = {
        "execution_mode": "governed_task_runtime",
        "command_id": admitted.command_id,
        "request_id": command.request_id,
        "principal_id": command.principal_id,
        "target_id": recovery_target_id,
        "command_target_id": command.target_id,
        "work_order_id": admitted.work_order_id,
        "assignment_id": admitted.assignment_id,
        "executor": admitted.executor,
        "worker_route": admitted.worker_route,
        "graph_id": admitted.graph_id,
        "graph_revision": admitted.graph_revision,
        "node_id": admitted.node_id,
        "attempt_id": checkpoint.attempt_id,
        # Verification runs only after the takeover Attempt is SUCCEEDED and
        # its lease released. Lease bindings below are evidence, not authority.
        "lease_id": None,
        "lease_owner_id": None,
        "fencing_token": None,
        "reservation_id": admitted.reservation_id,
        "business_scope": admitted.business_scope,
        "operation_fingerprint": admitted.operation_fingerprint,
        "parent_grant_ref": str(command.governor_grant_ref or "").strip(),
        "budget": budget.execution_payload(),
        "node_input_refs": list(_node_input_refs(node)),
        "node_acceptance_criteria": list(admitted.active_node.acceptance_criteria),
        "recovery": {
            "mode": "COMMITTED_CHECKPOINT_ONLINE_TAKEOVER",
            "worker_replayed": False,
            "recovery_owner_id": owner_id,
            "execution_authority_active": False,
            "takeover_lease_acquired": active_lease is not None,
            "checkpoint_payload_digest": checkpoint.payload_digest,
            "checkpoint_payload_digest_source": checkpoint.payload_digest_source,
            "takeover_lease": (
                {
                    "lease_id": _text(active_lease.get("lease_id")),
                    "owner_id": _text(active_lease.get("owner_id")),
                    "fencing_token": int(active_lease.get("fencing_token") or 0),
                }
                if active_lease
                else None
            ),
            "checkpoint_lease": {
                "lease_id": _text(checkpoint.reservation.get("lease_id")) or None,
                "owner_id": _text(checkpoint.reservation.get("owner_id")) or None,
                "fencing_token": int(
                    checkpoint.reservation.get("fencing_token") or 0
                ),
            },
        },
    }
    if node_grant_ref is not None:
        context["node_grant_ref"] = node_grant_ref
    return context


__all__ = ["build_checkpoint_execution_context"]
