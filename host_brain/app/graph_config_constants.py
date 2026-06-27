"""Graph configuration constants — node IDs, handler IDs, scene types, route constants.

Extracted from graph_config_builder.py to improve single-responsibility.
All graph topology constants live here; logic stays in graph_config_builder.py.
"""

from __future__ import annotations

from host_brain.integrations.llm_gateway import (
    PROVIDER_DEEPSEEK,
    PROVIDER_GEMINI,
    PROVIDER_OPENAI,
)
from host_brain.app.prompt_builder import (
    CHAT_DIRECT_NODE_ID,
    CHAT_RENDER_NODE_ID,
    SCENE_CHAT,
    SCENE_EXEC_INTENT,
    SCENE_VISION_UI,
    SCENE_WORK_CODE,
    SCENE_WORK_TRANSLATE,
)


# ---------------------------------------------------------------------------
# Node ordering
# ---------------------------------------------------------------------------

CHAT_GRAPH_NODE_ORDER: list[str] = [
    CHAT_DIRECT_NODE_ID,
    "core_reasoner_primary",
    "core_reasoner_secondary",
    "planner_constraints",
    "tool_planner",
    "safety_verifier",
    "consistency_verifier",
    "renderer_ja_c_style",
    "workbench_translate",
    "workbench_code_assist",
]
CHAT_INTENT_SOURCE_NODE_ID = "core_reasoner_primary"
SCENE_TYPE_AUTO = "AUTO"
_GRAPH_CODEX_MIGRATION_DEFAULT_EXCLUDED_SCENES = frozenset({SCENE_CHAT, SCENE_VISION_UI})

# M-MA1: Supervisor dispatch node constants
SUPERVISOR_DISPATCH_NODE_ID = "supervisor_dispatch"
SUPERVISOR_DISPATCH_HANDLER_ID = "supervisor_dispatch"

# M-MA5: Agent Handler node constants
SURFACE_REPLY_NODE_ID = "surface_reply_node"
SURFACE_REPLY_HANDLER_ID = "surface_reply"
TRANSLATE_AGENT_NODE_ID = "translate_agent_node"
TRANSLATE_AGENT_HANDLER_ID = "translate_agent"
INTENT_AGENT_NODE_ID = "intent_agent_node"
INTENT_AGENT_HANDLER_ID = "intent_agent"
# P1/P4 intent graph entry node: transparent subgraph
INTENT_AGENT_ENTRY_NODE_ID = "intent_agent_entry"
INTENT_AGENT_ENTRY_HANDLER_ID = "intent_agent_entry"
JUDGE_AGENT_NODE_ID = "judge_agent_node"
JUDGE_AGENT_HANDLER_ID = "judge_agent"
RETRIEVAL_AGENT_NODE_ID = "retrieval_agent_node"
RETRIEVAL_AGENT_HANDLER_ID = "retrieval_agent"
EMOTION_AGENT_NODE_ID = "emotion_agent_node"
EMOTION_AGENT_HANDLER_ID = "emotion_agent"
REPAIR_AGENT_NODE_ID = "repair_agent_node"
REPAIR_AGENT_HANDLER_ID = "repair_agent"
PENTEST_AGENT_NODE_ID = "pentest_agent_node"
PENTEST_AGENT_HANDLER_ID = "pentest_agent"
PENTEST_ATTACK_AGENT_HANDLER_ID = "pentest_attack_agent"
# P16: dynamic routing virtual node
AGENT_DISPATCH_NODE_ID = "agent_dispatch_node"
AGENT_DISPATCH_HANDLER_ID = "agent_dispatch"

# ---------------------------------------------------------------------------
# Scene types
# ---------------------------------------------------------------------------

ALLOWED_EXPLICIT_SCENE_TYPES: frozenset[str] = frozenset({
    SCENE_CHAT,
    SCENE_WORK_CODE,
    SCENE_WORK_TRANSLATE,
    SCENE_EXEC_INTENT,
    SCENE_VISION_UI,
    SCENE_TYPE_AUTO,
})

# ---------------------------------------------------------------------------
# Route constants
# ---------------------------------------------------------------------------

HOST_CHAT_ROUTE_HOST_14B = "host_14b"
HOST_CHAT_ROUTE_CLOUD = "cloud"
HOST_CHAT_ROUTE_SKILLS = "skills"

ROUTE_DECISION_VERSION = "route.v1"
ROUTE_TARGET_HOST = "host"
ROUTE_TARGET_EDGE = "edge"
ROUTE_TARGET_CLOUD = "cloud"
ROUTE_TARGET_SKILLS = "skills"

# ---------------------------------------------------------------------------
# LLM defaults
# ---------------------------------------------------------------------------

_HOST_CHAT_14B_DEFAULT_PROVIDER = PROVIDER_OPENAI
_HOST_CHAT_14B_DEFAULT_MODEL_ID = "qwen3:14b"
_HOST_CHAT_14B_FALLBACK_API_KEY = "host-local-placeholder-key"
_HOST_CHAT_14B_DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"
_HOST_CHAT_CLOUD_DEFAULT_PROVIDER = PROVIDER_GEMINI
_PROVIDERS_REQUIRING_API_KEY = frozenset({PROVIDER_OPENAI, PROVIDER_GEMINI, PROVIDER_DEEPSEEK})
