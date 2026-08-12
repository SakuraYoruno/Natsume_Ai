"""Contracts for Acceptance-gated governed Agent Workspace artifacts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


AGENT_WORKSPACE_ARTIFACT_EFFECT_TYPE = "agent.workspace_artifact_promote"
AGENT_WORKSPACE_ARTIFACT_PROPOSAL_SCHEMA = (
    "Natsume.agent.workspace_artifact_proposal.v1"
)
AGENT_WORKSPACE_ARTIFACT_EFFECT_SCHEMA = (
    "Natsume.agent.accepted_workspace_artifact.v1"
)
AGENT_WORKSPACE_ACTION_PREVIEW_CONTEXT_SCHEMA = (
    "Natsume.agent.workspace_action_preview_context.v1"
)


def canonical_workspace_artifact_digest(value: Mapping[str, Any]) -> str:
    # 效果载荷以规范 JSON 摘要绑定；验收、派发和审计可引用同一事实而无需复制工作区内容。
    encoded = json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = [
    "AGENT_WORKSPACE_ACTION_PREVIEW_CONTEXT_SCHEMA",
    "AGENT_WORKSPACE_ARTIFACT_EFFECT_SCHEMA",
    "AGENT_WORKSPACE_ARTIFACT_EFFECT_TYPE",
    "AGENT_WORKSPACE_ARTIFACT_PROPOSAL_SCHEMA",
    "canonical_workspace_artifact_digest",
]
