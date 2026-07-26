"""Validate worker proposals for governed Agent Workspace promotion.

This module is deliberately pure: it owns no TaskStore, Workspace, Review,
Artifact, or Effect writer.  The canonical Acceptance compiler passes it one
already-admitted command/work order and receives at most one bounded payload.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import re
from typing import Any

from host_brain.agent_runtime.governed_skill_work_order import (
    WRITE_AGENT_RUNTIME_SKILL_IDS,
)
from host_brain.agent_runtime.workspace_artifact_effect import (
    AGENT_WORKSPACE_ARTIFACT_EFFECT_SCHEMA,
    AGENT_WORKSPACE_ARTIFACT_EFFECT_TYPE,
    AGENT_WORKSPACE_ARTIFACT_PROPOSAL_SCHEMA,
)
from host_brain.task_runtime.worker_authority_guard import (
    forbidden_worker_authority_path,
)
from natsume_contracts.task import HostCommand


class AgentWorkspaceAcceptanceEffectError(ValueError):
    """The worker proposal cannot be compiled into an accepted Effect."""


def validated_worker_effect_descriptors(
    commit: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    """Return the canonical, bounded worker-owned Effect proposals."""

    raw = commit.get("acceptance_effect_intents")
    if raw in (None, (), []):
        return ()
    if not isinstance(raw, Sequence) or isinstance(
        raw,
        (str, bytes, bytearray),
    ):
        _invalid("worker effect descriptors must be a sequence")
    descriptors: list[dict[str, Any]] = []
    for item in raw:
        descriptor = _json_mapping(
            item,
            field_name="worker effect descriptor",
        )
        if set(descriptor) != {
            "effect_type",
            "commit_scope",
            "payload",
        }:
            _invalid(
                "worker descriptor may contain only effect_type, "
                "commit_scope and payload"
            )
        effect_type = str(descriptor.get("effect_type") or "").strip()
        if (
            effect_type
            not in {
                "life.foreground_completed",
                AGENT_WORKSPACE_ARTIFACT_EFFECT_TYPE,
            }
            or str(descriptor.get("commit_scope") or "").upper() != "NODE"
        ):
            _invalid("worker proposed an unsupported effect")
        payload = _json_mapping(
            descriptor.get("payload"),
            field_name="worker effect payload",
        )
        forbidden = forbidden_worker_authority_path(payload, path="payload")
        if forbidden is not None:
            _invalid(
                "worker effect contains forbidden authority field: "
                f"{forbidden}"
            )
        descriptors.append({**descriptor, "payload": payload})
    counts: dict[str, int] = {}
    for descriptor in descriptors:
        effect_type = str(descriptor["effect_type"])
        counts[effect_type] = counts.get(effect_type, 0) + 1
    if any(count > 1 for count in counts.values()):
        _invalid("worker proposed duplicate effects")
    return tuple(descriptors)


def compile_agent_workspace_artifact_effect(
    *,
    descriptors: Sequence[Mapping[str, Any]],
    command: HostCommand,
    work_order: Any,
    node_id: str,
    attempt_id: str,
    worker_commit: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Compile one exact Agent stage proposal into a graph-owned payload."""

    candidates = tuple(
        descriptor
        for descriptor in descriptors
        if descriptor["effect_type"] == AGENT_WORKSPACE_ARTIFACT_EFFECT_TYPE
    )
    if not candidates:
        return None
    if (
        command.route_type != "skill"
        or command.target_id not in WRITE_AGENT_RUNTIME_SKILL_IDS
    ):
        _invalid(
            "Workspace artifact promotion requires a write-capable Agent Skill"
        )
    proposed = _json_mapping(
        candidates[0].get("payload"),
        field_name="Agent Workspace artifact proposal",
    )
    required = {
        "schema_version",
        "work_order_id",
        "target_id",
        "operation_fingerprint",
        "protected_artifact",
        "result_digest",
    }
    if (
        set(proposed) != required
        or proposed.get("schema_version")
        != AGENT_WORKSPACE_ARTIFACT_PROPOSAL_SCHEMA
    ):
        _invalid("Agent Workspace artifact proposal fields drifted")
    protected = _json_mapping(
        proposed.get("protected_artifact"),
        field_name="Agent protected artifact",
    )
    if (
        set(protected) != {"schema_version", "ref", "digest"}
        or protected.get("schema_version")
        != "Natsume.agent.governed_artifact_stage.v1"
        or not str(protected.get("ref") or "").startswith("agent-stage://")
        or not re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            str(protected.get("digest") or ""),
        )
    ):
        _invalid("Agent protected artifact descriptor drifted")
    result_digest = str(proposed.get("result_digest") or "").strip()
    work_order_id = str(
        getattr(work_order, "work_order_id", "") or ""
    ).strip()
    operation_fingerprint = str(
        getattr(work_order, "operation_fingerprint", "") or ""
    ).strip()
    review_policy = getattr(work_order, "review_policy", {})
    review_policy = (
        dict(review_policy) if isinstance(review_policy, Mapping) else {}
    )
    risk_level = int(getattr(work_order, "risk_level", 0) or 0)
    worker_projection_ref = str(
        worker_commit.get("receipt_ref") or ""
    ).strip()
    if (
        proposed.get("work_order_id") != work_order_id
        or proposed.get("target_id") != command.target_id
        or proposed.get("operation_fingerprint") != operation_fingerprint
        or operation_fingerprint != command.operation_fingerprint
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", result_digest)
        or str(getattr(work_order, "task_graph_id", "") or "")
        != command.graph_id
        or int(getattr(work_order, "graph_revision", 0) or 0)
        != command.graph_revision
        or str(getattr(work_order, "task_node_id", "") or "") != node_id
        or risk_level < 4
        or review_policy.get("review_required") is not True
        or review_policy.get("durable_review_required") is not True
        or not worker_projection_ref.startswith("worker-projection://")
    ):
        _invalid(
            "Agent Workspace proposal differs from admitted Task authority"
        )
    return {
        "schema_version": AGENT_WORKSPACE_ARTIFACT_EFFECT_SCHEMA,
        "command_id": command.command_id,
        "request_id": command.request_id,
        "work_order_id": work_order_id,
        "graph_id": command.graph_id,
        "graph_revision": command.graph_revision,
        "node_id": node_id,
        "attempt_id": attempt_id,
        "target_id": command.target_id,
        "operation_fingerprint": operation_fingerprint,
        "worker_projection_ref": worker_projection_ref,
        "protected_artifact": protected,
        "result_digest": result_digest,
    }


def _json_mapping(value: Any, *, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        _invalid(f"{field_name} must be a mapping")
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        loaded = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise AgentWorkspaceAcceptanceEffectError(
            f"{field_name} must be canonical JSON"
        ) from exc
    if not isinstance(loaded, dict):
        _invalid(f"{field_name} must be a mapping")
    return loaded


def _invalid(detail: str) -> None:
    raise AgentWorkspaceAcceptanceEffectError(detail)


__all__ = [
    "AgentWorkspaceAcceptanceEffectError",
    "compile_agent_workspace_artifact_effect",
    "validated_worker_effect_descriptors",
]
