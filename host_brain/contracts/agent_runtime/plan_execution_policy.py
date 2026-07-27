"""Immutable production policy for one capability-planning TaskGraph.

The PlanningSearch trace proves how a path was selected.  This contract proves
what the selected path is allowed to execute after admission and on every
restart/replay.  It deliberately contains no handler, planner or bearer token.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import re
from typing import Any


PLAN_EXECUTION_POLICY_SCHEMA_VERSION = (
    "Natsume.host.capability_plan_execution_policy.v1"
)
PLAN_EXECUTION_POLICY_METADATA_KEY = "capability_plan_execution_policy"
PLAN_SPECULATIVE_EFFECT_MODE = "SPECULATIVE_EFFECT_FREE"
PLAN_NODE_WORKSPACE_EFFECT_MODE = "GRAPH_ACCEPTANCE_NODE_WORKSPACE"
PLAN_RESULT_EFFECT_MODE = "GRAPH_ACCEPTANCE_RESULT_ONLY"
PLAN_CHAT_EXECUTION_POLICY = "TASK_RUNTIME_CHAT"
PLAN_CHAT_BUDGET_POLICY = "MODELATTEMPT_MEASURED"
PLAN_EXECUTION_POLICY_INVALID = "PLAN_EXECUTION_POLICY_INVALID"

_POLICY_REF_RE = re.compile(
    r"^capability-plan-policy://sha256/(?P<digest>[0-9a-f]{64})$"
)
_SHA256_REF_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_BASE_KEYS = frozenset(
    {
        "schema_version",
        "graph_id",
        "graph_revision",
        "result_node_id",
        "completion_policy",
        "execution_steps",
        "work_order_budget_digest",
        "nodes",
    }
)
_NODE_KEYS = frozenset(
    {
        "node_id",
        "candidate_id",
        "route",
        "target_id",
        "depends_on_node_ids",
        "risk_level",
        "review_required",
        "execution_policy",
        "budget_policy",
        "effect_mode",
    }
)


class PlanExecutionPolicyError(ValueError):
    """Stable fail-closed error for a missing or drifted Plan policy."""

    def __init__(self, detail: str) -> None:
        self.reason_code = PLAN_EXECUTION_POLICY_INVALID
        self.detail = str(detail or "capability Plan execution policy is invalid")
        super().__init__(f"{self.reason_code}: {self.detail}")


def freeze_plan_execution_policy(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and content-address one secret-free policy payload."""

    base = _normalized_base(payload)
    policy_ref = "capability-plan-policy://sha256/" + _sha256_json(base)
    return {**base, "policy_ref": policy_ref}


def validate_plan_execution_policy(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Reopen one persisted policy and verify its exact content address."""

    raw = _mapping(payload, field_name="plan execution policy")
    if set(raw) != {*_BASE_KEYS, "policy_ref"}:
        raise PlanExecutionPolicyError(
            "persisted Plan policy has missing or unknown fields"
        )
    policy_ref = _text(raw.get("policy_ref"), field_name="policy_ref")
    match = _POLICY_REF_RE.fullmatch(policy_ref)
    if match is None:
        raise PlanExecutionPolicyError("policy_ref is not content-addressed")
    base = _normalized_base(
        {key: raw[key] for key in _BASE_KEYS}
    )
    if match.group("digest") != _sha256_json(base):
        raise PlanExecutionPolicyError(
            "persisted Plan policy digest does not match its payload"
        )
    return {**base, "policy_ref": policy_ref}


def require_plan_result_effect_owner(
    *,
    policy: Mapping[str, Any],
    node_id: str,
) -> dict[str, Any]:
    """Require that Acceptance Effects are compiled only from the result node."""

    normalized = validate_plan_execution_policy(policy)
    result_node_id = normalized["result_node_id"]
    if str(node_id or "").strip() != result_node_id:
        raise PlanExecutionPolicyError(
            "Acceptance Effects may be compiled only from the Plan result node"
        )
    result = normalized["nodes"][-1]
    if (
        result["node_id"] != result_node_id
        or result["effect_mode"] != PLAN_RESULT_EFFECT_MODE
    ):
        raise PlanExecutionPolicyError(
            "Plan result node has no graph-Acceptance effect authority"
        )
    return normalized


def require_plan_workspace_effect_owner(
    *,
    policy: Mapping[str, Any],
    node_id: str,
    target_id: str,
) -> dict[str, Any]:
    """Require one pre-result node's graph-Acceptance Workspace authority."""

    normalized = validate_plan_execution_policy(policy)
    node_ref = str(node_id or "").strip()
    target_ref = str(target_id or "").strip()
    matches = [
        dict(node)
        for node in normalized["nodes"][:-1]
        if node["node_id"] == node_ref
    ]
    if (
        len(matches) != 1
        or matches[0]["route"] != "skill"
        or matches[0]["target_id"] != target_ref
        or matches[0]["execution_policy"] != "POST_ACCEPTANCE_WORKSPACE"
        or matches[0]["effect_mode"] != PLAN_NODE_WORKSPACE_EFFECT_MODE
        or matches[0]["review_required"] is not True
        or matches[0]["risk_level"] < 4
    ):
        raise PlanExecutionPolicyError(
            "Plan node has no graph-Acceptance Workspace effect authority"
        )
    return matches[0]


def _normalized_base(payload: Mapping[str, Any]) -> dict[str, Any]:
    raw = _mapping(payload, field_name="Plan execution policy payload")
    if set(raw) != _BASE_KEYS:
        raise PlanExecutionPolicyError(
            "Plan execution policy payload has missing or unknown fields"
        )
    if raw.get("schema_version") != PLAN_EXECUTION_POLICY_SCHEMA_VERSION:
        raise PlanExecutionPolicyError("Plan execution policy schema is unsupported")
    graph_id = _text(raw.get("graph_id"), field_name="graph_id")
    result_node_id = _text(
        raw.get("result_node_id"),
        field_name="result_node_id",
    )
    graph_revision = _positive_int(
        raw.get("graph_revision"),
        field_name="graph_revision",
    )
    execution_steps = _positive_int(
        raw.get("execution_steps"),
        field_name="execution_steps",
    )
    completion_policy = _text(
        raw.get("completion_policy"),
        field_name="completion_policy",
    )
    if completion_policy != "ATOMIC_GRAPH_SUCCESS_V1":
        raise PlanExecutionPolicyError(
            "capability Plan must use atomic graph success"
        )
    budget_digest = _text(
        raw.get("work_order_budget_digest"),
        field_name="work_order_budget_digest",
    ).lower()
    if _SHA256_REF_RE.fullmatch(budget_digest) is None:
        raise PlanExecutionPolicyError(
            "work_order_budget_digest must be a SHA-256 reference"
        )
    raw_nodes = raw.get("nodes")
    if (
        not isinstance(raw_nodes, Sequence)
        or isinstance(raw_nodes, (str, bytes, bytearray))
        or len(raw_nodes) != execution_steps + 1
    ):
        raise PlanExecutionPolicyError(
            "Plan policy nodes must contain execution_steps plus one result node"
        )
    nodes = tuple(
        _normalized_node(value, index=index)
        for index, value in enumerate(raw_nodes)
    )
    node_ids = tuple(node["node_id"] for node in nodes)
    candidate_ids = tuple(node["candidate_id"] for node in nodes)
    if len(set(node_ids)) != len(node_ids) or len(set(candidate_ids)) != len(
        candidate_ids
    ):
        raise PlanExecutionPolicyError(
            "Plan policy node and candidate identities must be unique"
        )
    execution_nodes = nodes[:-1]
    result = nodes[-1]
    workspace_effect_nodes = 0
    for index, node in enumerate(execution_nodes):
        speculative = (
            node["effect_mode"] == PLAN_SPECULATIVE_EFFECT_MODE
            and node["execution_policy"] != "POST_ACCEPTANCE_WORKSPACE"
        )
        workspace_effect = (
            node["effect_mode"] == PLAN_NODE_WORKSPACE_EFFECT_MODE
            and node["execution_policy"] == "POST_ACCEPTANCE_WORKSPACE"
            and node["review_required"] is True
            and node["risk_level"] >= 4
        )
        if (
            node["route"] != "skill"
            or not (speculative or workspace_effect)
            or not node["execution_policy"]
            or not node["budget_policy"]
        ):
            raise PlanExecutionPolicyError(
                "every pre-result Plan node must be a governed speculative "
                "Skill or one reviewed Workspace proposal owner"
            )
        if workspace_effect:
            workspace_effect_nodes += 1
        expected_dependencies = () if index == 0 else (node_ids[index - 1],)
        if tuple(node["depends_on_node_ids"]) != expected_dependencies:
            raise PlanExecutionPolicyError(
                "capability execution nodes must retain the selected path order"
            )
    if workspace_effect_nodes > 1:
        raise PlanExecutionPolicyError(
            "one atomic Plan revision may own at most one Workspace proposal"
        )
    if (
        result["node_id"] != result_node_id
        or result["route"] != "chat"
        or result["target_id"] != "surface_reply"
        or result["execution_policy"] != PLAN_CHAT_EXECUTION_POLICY
        or result["budget_policy"] != PLAN_CHAT_BUDGET_POLICY
        or result["effect_mode"] != PLAN_RESULT_EFFECT_MODE
        or tuple(result["depends_on_node_ids"]) != node_ids[:-1]
    ):
        raise PlanExecutionPolicyError(
            "Plan result must be the unique graph-Acceptance Chat synthesis node"
        )
    return {
        "schema_version": PLAN_EXECUTION_POLICY_SCHEMA_VERSION,
        "graph_id": graph_id,
        "graph_revision": graph_revision,
        "result_node_id": result_node_id,
        "completion_policy": completion_policy,
        "execution_steps": execution_steps,
        "work_order_budget_digest": budget_digest,
        "nodes": [dict(node) for node in nodes],
    }


def _normalized_node(value: Any, *, index: int) -> dict[str, Any]:
    raw = _mapping(value, field_name=f"nodes[{index}]")
    if set(raw) != _NODE_KEYS:
        raise PlanExecutionPolicyError(
            f"nodes[{index}] has missing or unknown fields"
        )
    route = _text(raw.get("route"), field_name=f"nodes[{index}].route").lower()
    if route not in {"skill", "chat"}:
        raise PlanExecutionPolicyError(
            f"nodes[{index}].route is unsupported"
        )
    dependencies = _text_sequence(
        raw.get("depends_on_node_ids"),
        field_name=f"nodes[{index}].depends_on_node_ids",
    )
    review_required = raw.get("review_required")
    if not isinstance(review_required, bool):
        raise PlanExecutionPolicyError(
            f"nodes[{index}].review_required must be a boolean"
        )
    risk_level = _positive_int(
        raw.get("risk_level"),
        field_name=f"nodes[{index}].risk_level",
    )
    if risk_level > 5:
        raise PlanExecutionPolicyError(
            f"nodes[{index}].risk_level must not exceed 5"
        )
    return {
        "node_id": _text(raw.get("node_id"), field_name=f"nodes[{index}].node_id"),
        "candidate_id": _text(
            raw.get("candidate_id"),
            field_name=f"nodes[{index}].candidate_id",
        ),
        "route": route,
        "target_id": _text(
            raw.get("target_id"),
            field_name=f"nodes[{index}].target_id",
        ),
        "depends_on_node_ids": list(dependencies),
        "risk_level": risk_level,
        "review_required": review_required,
        "execution_policy": _text(
            raw.get("execution_policy"),
            field_name=f"nodes[{index}].execution_policy",
        ),
        "budget_policy": _text(
            raw.get("budget_policy"),
            field_name=f"nodes[{index}].budget_policy",
        ),
        "effect_mode": _text(
            raw.get("effect_mode"),
            field_name=f"nodes[{index}].effect_mode",
        ),
    }


def _mapping(value: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PlanExecutionPolicyError(f"{field_name} must be an object")
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
    except (TypeError, ValueError) as exc:
        raise PlanExecutionPolicyError(
            f"{field_name} must be JSON-safe"
        ) from exc
    if not isinstance(normalized, dict):
        raise PlanExecutionPolicyError(f"{field_name} must be an object")
    return normalized


def _text(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 1_000:
        raise PlanExecutionPolicyError(f"{field_name} is required")
    return text


def _positive_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool):
        raise PlanExecutionPolicyError(f"{field_name} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise PlanExecutionPolicyError(
            f"{field_name} must be a positive integer"
        ) from exc
    if parsed < 1:
        raise PlanExecutionPolicyError(f"{field_name} must be a positive integer")
    return parsed


def _text_sequence(value: Any, *, field_name: str) -> tuple[str, ...]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
    ):
        raise PlanExecutionPolicyError(f"{field_name} must be a list")
    normalized = tuple(
        _text(item, field_name=f"{field_name}[]")
        for item in value
    )
    if len(set(normalized)) != len(normalized):
        raise PlanExecutionPolicyError(f"{field_name} contains duplicates")
    return normalized


def _sha256_json(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "PLAN_CHAT_BUDGET_POLICY",
    "PLAN_CHAT_EXECUTION_POLICY",
    "PLAN_EXECUTION_POLICY_INVALID",
    "PLAN_EXECUTION_POLICY_METADATA_KEY",
    "PLAN_EXECUTION_POLICY_SCHEMA_VERSION",
    "PLAN_NODE_WORKSPACE_EFFECT_MODE",
    "PLAN_RESULT_EFFECT_MODE",
    "PLAN_SPECULATIVE_EFFECT_MODE",
    "PlanExecutionPolicyError",
    "freeze_plan_execution_policy",
    "require_plan_result_effect_owner",
    "require_plan_workspace_effect_owner",
    "validate_plan_execution_policy",
]
