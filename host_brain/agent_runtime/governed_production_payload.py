"""Admission-time freezing for legacy production-skill adapter payloads.

The production-domain adapter still accepts a V2-shaped work order.  That
compatibility boundary must be resolved by the Host/Planning admission path,
never reconstructed by a worker after an immutable TaskGraph has been
accepted.  This module contains the pure, shared transformation used by both
direct Skill and selected Plan nodes.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import re
from typing import Any

from host_brain.agent_runtime.production_studio import (
    AGENT_PRODUCTION_SKILL_IDS,
    build_production_work_order,
)


FROZEN_NODE_EXECUTOR_PAYLOADS_KEY = "frozen_node_executor_payloads"
GOVERNED_PRODUCTION_EXECUTOR_PAYLOAD_SCHEMA = (
    "Natsume.governed.production_executor_payload.v1"
)

_FORBIDDEN_DURABLE_KEYS = re.compile(
    r"(?:^|_)(?:api_?key|authorization|bearer|credential|password|raw_?body|"
    r"secret|signature|token)(?:$|_)",
    re.IGNORECASE,
)
_MAX_DURABLE_TEXT = 32_000
_CAPABILITY_PLANNING_MODE = "CAPABILITY_SEARCH_V1"
_PLANNING_PROPOSAL_EXECUTION_POLICY = "PRE_ACCEPTANCE_PROPOSAL_ONLY"


def build_governed_production_executor_payload(
    *,
    target_id: str,
    intent_args: Mapping[str, Any],
    request_id: str,
    session_id: str,
    person_id: str,
    user_message: str,
    work_order_id: str,
    graph_id: str,
    node_id: str,
    graph_revision: int,
    business_scope: str,
    operation_fingerprint: str,
    approval_ref: str,
    review_ref: str,
) -> dict[str, Any]:
    """Build one durable production-adapter payload bound to an admitted node."""

    target = str(target_id or "").strip()
    if target not in AGENT_PRODUCTION_SKILL_IDS:
        raise ValueError("only registered production Skills can receive a frozen payload")
    args = dict(intent_args or {})
    args["work_order_id"] = str(work_order_id)
    args["request_id"] = str(request_id)
    args.setdefault("session_id", str(session_id))
    args.setdefault("person_id", str(person_id))
    args.setdefault("user_message", str(user_message))
    args.setdefault("task", str(user_message))
    args.setdefault("approval_confirmed", True)
    args.setdefault("confirmed", True)
    adapter_order = build_production_work_order(args, skill_id=target)
    metadata = dict(adapter_order.get("metadata") or {})
    if str(args.get("planning_mode") or "").strip().upper() == _CAPABILITY_PLANNING_MODE:
        # A selected capability is already one immutable TaskGraph node.  Its
        # inner Agent may contribute analysis, but it may not dynamically add
        # file/browser/tool work that has no independent node authority.
        adapter_order["allowed_actions"] = ["generate_report"]
        metadata["proposal_only"] = True
        metadata["execution_policy"] = _PLANNING_PROPOSAL_EXECUTION_POLICY
        input_pack = dict(adapter_order.get("input_pack") or {})
        input_pack["context"] = (
            "This is an effect-free planning proposal node. Produce a "
            "capability-specific, actionable plan from the supplied objective. "
            "Do not request tools and do not claim that files, browsers, URLs, "
            "tests, or external systems were inspected."
        )
        adapter_order["input_pack"] = input_pack
    metadata["governed_lineage"] = {
        "root_work_order_id": str(work_order_id),
        "task_graph_id": str(graph_id),
        "task_node_id": str(node_id),
        "graph_revision": int(graph_revision),
        "business_scope": str(business_scope),
        "operation_fingerprint": str(operation_fingerprint),
        "approval_ref": str(approval_ref),
        "review_ref": str(review_ref),
    }
    adapter_order["metadata"] = metadata
    payload: dict[str, Any] = {
        "schema_version": GOVERNED_PRODUCTION_EXECUTOR_PAYLOAD_SCHEMA,
        "target_id": target,
        "tool": "codex_production",
        "action": "enqueue",
        # Canonical execution is synchronous; this flag may never recreate
        # the retired background daemon path after graph admission.
        "start_background": False,
        "work_order": adapter_order,
    }
    approval_policy = dict(adapter_order.get("approval_policy") or {})
    if bool(approval_policy.get("require_confirm")) and not bool(
        approval_policy.get("confirmed")
    ):
        payload["blocked_until_confirmation"] = True
    for field_name in ("artifacts_root", "runs_root"):
        if field_name in args:
            payload[field_name] = args[field_name]
    safe_payload = safe_governed_durable_value(payload)
    if not isinstance(safe_payload, dict):  # defensive: mapping input always yields a dict
        raise ValueError("frozen production executor payload is not a durable mapping")
    safe_payload["payload_sha256"] = sha256_json(safe_payload)
    return safe_payload


def safe_governed_durable_value(value: Any) -> Any:
    """Strip secret-bearing keys and bound values before durable admission."""

    if isinstance(value, Mapping):
        safe: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key or "").strip()
            if not key or _FORBIDDEN_DURABLE_KEYS.search(key):
                continue
            safe[key] = safe_governed_durable_value(raw_value)
        return safe
    if isinstance(value, (list, tuple)):
        return [safe_governed_durable_value(item) for item in value[:256]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:_MAX_DURABLE_TEXT]
    return str(value)[:1_000]


def sha256_json(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        deepcopy(dict(value)),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "FROZEN_NODE_EXECUTOR_PAYLOADS_KEY",
    "GOVERNED_PRODUCTION_EXECUTOR_PAYLOAD_SCHEMA",
    "build_governed_production_executor_payload",
    "safe_governed_durable_value",
    "sha256_json",
]
