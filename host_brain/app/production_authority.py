"""Application adapter for validating canonical production Task authority."""

from __future__ import annotations

from typing import Mapping

from host_brain.contracts.agent_runtime.work_order import AgentWorkOrder
from host_brain.core.ids import now_ms

def verify_durable_production_authority(
    authority: Mapping[str, str],
    order: AgentWorkOrder,
) -> str | None:
    """Re-resolve the Host command, running Task attempt and Governor grant."""

    try:
        from host_brain.composition.process_services import get_ledger  # noqa: PLC0415
        from host_brain.orchestration import DurableGovernorGrantResolver  # noqa: PLC0415
        from host_brain.persistence.host_command_repository import HostCommandRepository  # noqa: PLC0415
        from host_brain.persistence.task_repository import DurableTaskStore  # noqa: PLC0415

        ledger = get_ledger()
        db_path = str(getattr(ledger, "canonical_db_path", "") or "").strip()
        if not db_path or db_path == ":memory:":
            return "AGENT_PRODUCTION_DURABLE_AUTHORITY_UNAVAILABLE"
        command = HostCommandRepository(db_path).get(str(authority.get("command_id") or ""))
        if command is None:
            return "AGENT_PRODUCTION_DURABLE_COMMAND_MISSING"
        expected_request_id = str(order.metadata.get("request_id") or order.work_order_id).strip()
        expected_target_id = str(order.metadata.get("skill_id") or "").strip()
        if (
            str(command.get("request_id") or "") != expected_request_id
            or str(command.get("request_id") or "") != str(authority.get("request_id") or "")
            or str(command.get("principal_id") or "") != str(authority.get("principal_id") or "")
            or str(command.get("target_id") or "") != expected_target_id
            or str(command.get("target_id") or "") != str(authority.get("target_id") or "")
            or str(command.get("root_work_order_id") or "") != order.work_order_id
            or str(command.get("root_work_order_id") or "")
            != str(authority.get("work_order_id") or "")
            or str(command.get("graph_id") or "") != str(authority.get("graph_id") or "")
            or int(command.get("graph_revision") or 0)
            != int(authority.get("graph_revision") or 0)
            or str(command.get("route_type") or "").strip().lower() != "skill"
        ):
            return "AGENT_PRODUCTION_DURABLE_COMMAND_BINDING_MISMATCH"
        task_store = DurableTaskStore(db_path)
        attempt = task_store.get_attempt(str(authority.get("attempt_id") or ""))
        if (
            str(attempt.get("graph_id") or "") != str(authority.get("graph_id") or "")
            or int(attempt.get("graph_revision") or 0)
            != int(authority.get("graph_revision") or 0)
            or str(attempt.get("node_id") or "") != str(authority.get("node_id") or "")
            or str(attempt.get("status") or "").strip().upper() != "RUNNING"
        ):
            return "AGENT_PRODUCTION_DURABLE_ATTEMPT_BINDING_MISMATCH"
        node = task_store.get_node(
            str(authority.get("graph_id") or ""),
            int(authority.get("graph_revision") or 0),
            str(authority.get("node_id") or ""),
        )
        if (
            str(node.get("graph_id") or "") != str(authority.get("graph_id") or "")
            or int(node.get("graph_revision") or 0)
            != int(authority.get("graph_revision") or 0)
            or str(node.get("node_id") or "") != str(authority.get("node_id") or "")
            or str(node.get("status") or "").strip().upper() != "RUNNING"
        ):
            return "AGENT_PRODUCTION_DURABLE_NODE_BINDING_MISMATCH"
        lease = task_store.get_active_lease(str(authority.get("attempt_id") or ""))
        if (
            not isinstance(lease, Mapping)
            or str(lease.get("lease_id") or "") != str(authority.get("lease_id") or "")
            or str(lease.get("attempt_id") or "") != str(authority.get("attempt_id") or "")
            or int(lease.get("fencing_token") or 0)
            != int(authority.get("fencing_token") or 0)
            or str(lease.get("status") or "").strip().upper() != "ACTIVE"
            or int(lease.get("expires_ts_ms") or 0) <= now_ms()
        ):
            return "AGENT_PRODUCTION_DURABLE_LEASE_BINDING_MISMATCH"
        DurableGovernorGrantResolver(ledger).require(
            request_id=expected_request_id,
            expected_ref=str(command.get("governor_grant_ref") or "").strip(),
        )
    except Exception:
        return "AGENT_PRODUCTION_DURABLE_AUTHORITY_INVALID"
    return None

__all__ = ["verify_durable_production_authority"]
