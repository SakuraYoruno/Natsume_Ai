"""Pure normalization for stable WorkOrderV3 replay comparisons."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from host_brain.contracts.agent_runtime.work_order import WorkOrderV3


def normalized_work_order_replay_payload(
    work_order: WorkOrderV3,
) -> dict[str, Any]:
    """Mask only rotating durable authority refs before exact comparison."""

    payload = work_order.to_dict()
    authority_refs = {
        str(work_order.approval_ref or "").strip(),
        str(work_order.review_ref or "").strip(),
    } - {""}
    payload["approval_ref"] = "<durable-authority-ref>"
    payload["review_ref"] = "<durable-authority-ref>"
    payload["input_refs"] = [
        "<durable-authority-ref>" if str(ref) in authority_refs else str(ref)
        for ref in work_order.input_refs
    ]
    approval_policy = _mapping(payload.get("approval_policy"))
    approval_policy["governor_grant_ref"] = "<durable-authority-ref>"
    payload["approval_policy"] = approval_policy
    review_policy = _mapping(payload.get("review_policy"))
    review_policy["review_ref"] = "<durable-authority-ref>"
    payload["review_policy"] = review_policy
    metadata = _mapping(payload.get("metadata"))
    lineage = _mapping(metadata.get("lineage"))
    lineage["approval_ref"] = "<durable-authority-ref>"
    lineage["review_ref"] = "<durable-authority-ref>"
    metadata["lineage"] = lineage
    planning_lineage = _mapping(metadata.get("planning_lineage"))
    planning_lineage["approval_ref"] = "<durable-authority-ref>"
    planning_lineage["review_ref"] = "<durable-authority-ref>"
    metadata["planning_lineage"] = planning_lineage
    payload["metadata"] = metadata
    _normalize_frozen_executor_payloads_for_replay(payload)
    return payload


def _normalize_frozen_executor_payloads_for_replay(
    payload: dict[str, Any],
) -> None:
    input_pack = _mapping(payload.get("input_pack"))
    raw_payloads = _mapping(input_pack.get("frozen_node_executor_payloads"))
    if not raw_payloads:
        return
    normalized_payloads: dict[str, Any] = {}
    for node_id, raw_payload in raw_payloads.items():
        frozen = _mapping(raw_payload)
        adapter_order = _mapping(frozen.get("work_order"))
        metadata = _mapping(adapter_order.get("metadata"))
        lineage = _mapping(metadata.get("governed_lineage"))
        if lineage:
            if "approval_ref" in lineage:
                lineage["approval_ref"] = "<durable-authority-ref>"
            if "review_ref" in lineage:
                lineage["review_ref"] = "<durable-authority-ref>"
            metadata["governed_lineage"] = lineage
            adapter_order["metadata"] = metadata
            frozen["work_order"] = adapter_order
            if "payload_sha256" in frozen:
                frozen["payload_sha256"] = "<frozen-payload-digest>"
        normalized_payloads[str(node_id)] = frozen
    input_pack["frozen_node_executor_payloads"] = normalized_payloads
    payload["input_pack"] = input_pack


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


__all__ = ["normalized_work_order_replay_payload"]
