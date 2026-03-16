"""Multi-Agent 模块入口。

提供 agent 注册表、artifact contract 数据结构，
以及 Supervisor 分派所需的公共符号。
"""

from host_brain.app.agents.agent_registry import (
    AGENT_ALLOWLIST,
    AGENT_ID_CHAT,
    AGENT_ID_CODE,
    AGENT_ID_TRANSLATE,
    AGENT_ID_INTENT,
    AGENT_ID_SAFETY,
    AGENT_ID_JUDGE,
    AGENT_ID_RENDERER,
    is_agent_allowed,
    get_agent_meta,
)
from host_brain.app.agents.agent_contract import (
    AgentArtifact,
    AgentRuntime,
    SupervisorProposal,
    build_agent_runtime_default,
    build_artifact,
    validate_supervisor_proposal,
)

__all__ = [
    # registry
    "AGENT_ALLOWLIST",
    "AGENT_ID_CHAT",
    "AGENT_ID_CODE",
    "AGENT_ID_TRANSLATE",
    "AGENT_ID_INTENT",
    "AGENT_ID_SAFETY",
    "AGENT_ID_JUDGE",
    "AGENT_ID_RENDERER",
    "is_agent_allowed",
    "get_agent_meta",
    # contract
    "AgentArtifact",
    "AgentRuntime",
    "SupervisorProposal",
    "build_agent_runtime_default",
    "build_artifact",
    "validate_supervisor_proposal",
]
