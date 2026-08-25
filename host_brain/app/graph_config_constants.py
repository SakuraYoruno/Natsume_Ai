"""图配置的稳定标识常量：节点、处理器、场景和路由名称。

此模块只声明拓扑词汇，实际图构造、路由准入、模型选择与执行逻辑均属于相应的 builder
和运行时；调用方不能仅凭常量名称推断某个节点已装配、已授权或可调用。
"""

from __future__ import annotations

from host_brain.model_runtime.prompt_builder_utils import (
    CHAT_DIRECT_NODE_ID as _CHAT_DIRECT_NODE_ID,
    SCENE_CHAT as _SCENE_CHAT,
    SCENE_EXEC_INTENT as _SCENE_EXEC_INTENT,
    SCENE_VISION_UI as _SCENE_VISION_UI,
    SCENE_WORK_CODE as _SCENE_WORK_CODE,
    SCENE_WORK_TRANSLATE as _SCENE_WORK_TRANSLATE,
)


# ---------------------------------------------------------------------------
# Node ordering
# ---------------------------------------------------------------------------

CHAT_GRAPH_NODE_ORDER: list[str] = [
    _CHAT_DIRECT_NODE_ID,
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
SCENE_TYPE_AUTO = "AUTO"

# M-MA1: Supervisor dispatch node constants
SUPERVISOR_DISPATCH_NODE_ID = "supervisor_dispatch"
SUPERVISOR_DISPATCH_HANDLER_ID = "supervisor_dispatch"

# M-MA5: Agent Handler node constants
SURFACE_REPLY_HANDLER_ID = "surface_reply"
TRANSLATE_AGENT_HANDLER_ID = "translate_agent"
INTENT_AGENT_HANDLER_ID = "intent_agent"
# P1/P4 intent graph entry node: transparent subgraph
INTENT_AGENT_ENTRY_NODE_ID = "intent_agent_entry"
INTENT_AGENT_ENTRY_HANDLER_ID = "intent_agent_entry"
JUDGE_AGENT_NODE_ID = "judge_agent_node"
JUDGE_AGENT_HANDLER_ID = "judge_agent"
RETRIEVAL_AGENT_HANDLER_ID = "retrieval_agent"
REPAIR_AGENT_HANDLER_ID = "repair_agent"
PENTEST_AGENT_HANDLER_ID = "pentest_agent"
# P16: dynamic routing virtual node
AGENT_DISPATCH_NODE_ID = "agent_dispatch_node"

# ---------------------------------------------------------------------------
# Scene types
# ---------------------------------------------------------------------------

ALLOWED_EXPLICIT_SCENE_TYPES: frozenset[str] = frozenset({
    _SCENE_CHAT,
    _SCENE_WORK_CODE,
    _SCENE_WORK_TRANSLATE,
    _SCENE_EXEC_INTENT,
    _SCENE_VISION_UI,
    SCENE_TYPE_AUTO,
})

# ---------------------------------------------------------------------------
# Route constants
# ---------------------------------------------------------------------------

HOST_CHAT_ROUTE_HOST_14B = "host_14b"
HOST_CHAT_ROUTE_CLOUD = "cloud"

# ---------------------------------------------------------------------------
# LLM defaults
# ---------------------------------------------------------------------------
