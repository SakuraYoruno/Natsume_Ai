"""ReAct Agent 的 proposal-only 工具目录。

该模块只保存 ToolSpec 元数据、Agent 可见性与固定失败关闭兼容入口。
旧的 Memory、Skill、Delegate、Emotion 及分析 handler 已删除；任何动作
要执行都必须由外层规划器编译为新的 immutable TaskGraph revision。
"""

from __future__ import annotations

import os
from typing import Any

from host_brain.tools.contracts import ToolSpec


REACT_TOOL_GOVERNED_ADMISSION_REQUIRED = (
    "REACT_TOOL_GOVERNED_ADMISSION_REQUIRED"
)


# ---------------------------------------------------------------------------
# 工具注册表
# ---------------------------------------------------------------------------

_TOOL_REGISTRY: dict[str, ToolSpec] = {
    "search_knowledge": ToolSpec(
        tool_id="search_knowledge",
        display_name="知识库语义检索",
        description="提议在知识库中进行语义检索并返回相关内容片段",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索查询词"},
            },
            "required": ["query"],
        },
        has_side_effects=False,
        executor=None,
    ),
    "search_by_keyword": ToolSpec(
        tool_id="search_by_keyword",
        display_name="关键词检索",
        description="基于关键词重叠率在知识库中检索，适合精确词汇匹配场景",
        parameters={
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "检索关键词"},
                "top_k": {"type": "integer", "description": "最大返回条数，默认 5，范围 1-20"},
            },
            "required": ["keyword"],
        },
        has_side_effects=False,
        executor=None,
    ),
    "search_by_vector": ToolSpec(
        tool_id="search_by_vector",
        display_name="向量语义检索",
        description="基于向量相似度在知识库中检索，适合语义相似场景，支持自定义相似度阈值",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索查询文本"},
                "top_k": {"type": "integer", "description": "最大返回条数，默认 5，范围 1-20"},
                "threshold": {"type": "number", "description": "相似度阈值，默认 0.70，范围 0.0-1.0"},
            },
            "required": ["query"],
        },
        has_side_effects=False,
        executor=None,
    ),
    "get_context": ToolSpec(
        tool_id="get_context",
        display_name="获取对话上下文",
        description="获取当前对话的运行时上下文信息，包括时间戳、知识库状态等",
        parameters={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "会话 ID（可选）"},
                "context_type": {"type": "string", "description": "上下文类型，默认 conversation"},
            },
            "required": [],
        },
        has_side_effects=False,
        executor=None,
    ),
    "list_skills": ToolSpec(
        tool_id="list_skills",
        display_name="List Skills",
        description="List available skills with family and risk metadata for planning.",
        parameters={
            "type": "object",
            "properties": {
                "family": {"type": "string", "description": "Optional skill family filter"},
                "keyword": {"type": "string", "description": "Optional fuzzy keyword filter"},
                "limit": {"type": "integer", "description": "Maximum items to return, default 30"},
            },
            "required": [],
        },
        has_side_effects=False,
        executor=None,
    ),
    "analyze_code": ToolSpec(
        tool_id="analyze_code",
        display_name="代码静态分析",
        description="对给定代码进行静态分析，返回行数、函数数、注释率、潜在问题等指标",
        parameters={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "要分析的代码"},
                "language": {"type": "string", "description": "编程语言，默认 python"},
            },
            "required": ["code"],
        },
        has_side_effects=False,
        executor=None,
    ),
    "translate_text": ToolSpec(
        tool_id="translate_text",
        display_name="文本翻译",
        description="检测文本语言并返回翻译元数据，实际翻译由 LLM 在 ReAct 循环中完成",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要翻译的文本"},
                "target_lang": {"type": "string", "description": "目标语言代码，如 en/ja/zh"},
            },
            "required": ["text"],
        },
        has_side_effects=False,
        executor=None,
    ),
    "evaluate_artifact": ToolSpec(
        tool_id="evaluate_artifact",
        display_name="工件质量评估",
        description="对单个 AgentArtifact 进行结构化质量评估，返回质量等级、评分和问题列表",
        parameters={
            "type": "object",
            "properties": {
                "artifact": {"type": "object", "description": "要评估的工件对象（含 confidence、summary、risk_flags 等字段）"},
                "agent_id": {"type": "string", "description": "工件所属 agent ID（可选，用于标识）"},
            },
            "required": ["artifact"],
        },
        has_side_effects=False,
        executor=None,
    ),
    "compare_artifacts": ToolSpec(
        tool_id="compare_artifacts",
        display_name="多工件比较",
        description="比较多个 AgentArtifact，基于置信度和风险标记推荐最优工件",
        parameters={
            "type": "object",
            "properties": {
                "artifacts": {"type": "object", "description": "工件字典，key 为 agent_id，value 为工件对象"},
            },
            "required": ["artifacts"],
        },
        has_side_effects=False,
        executor=None,
    ),
}

# 各 agent 可用的工具集合（只读工具）
_AGENT_TOOLS: dict[str, list[str]] = {
    "surface_reply":      ["search_knowledge", "get_context"],
    "translate_agent": ["translate_text", "search_knowledge"],
    "retrieval_agent": ["search_knowledge", "search_by_keyword", "search_by_vector", "get_context"],
    "intent_agent":    [
        "search_knowledge",
        "get_context",
        "list_skills",
        "analyze_code",
    ],
    "judge_agent":     ["evaluate_artifact", "compare_artifacts"],  # P14：JudgeAgent ReAct 化，使用工件评估工具
    # Pentest application dispatch is planning-only.  Effects are server-owned
    # by SecurePentestExecutionService and are never generic ReAct tools.
    "pentest_agent":   [],
}


_INTENT_AGENT_ID = "intent_agent"


def _parse_csv_env(key: str) -> list[str]:
    raw = str(os.environ.get(key, "") or "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def _resolve_intent_dynamic_tool_ids(*, allow_side_effects: bool) -> list[str]:
    """Resolve the static proposal allowlist minus the operator denylist."""
    del allow_side_effects
    base_ids = list(dict.fromkeys(_AGENT_TOOLS.get(_INTENT_AGENT_ID, [])))
    denylist = set(_parse_csv_env("INTENT_TOOL_DENYLIST"))
    return [tool_id for tool_id in base_ids if tool_id not in denylist]


def _tool_meta_to_spec(meta: "Any") -> ToolSpec:
    """将 ToolCatalog 元数据转换为 proposal-only ToolSpec。

    Args:
        meta: ToolMeta 实例

    Returns:
        ToolSpec 实例
    """
    tool_id = str(getattr(meta, "id", "") or "").strip()
    side_effect_level = str(getattr(meta, "side_effect_level", "READ") or "READ").strip().upper()
    has_side_effects = side_effect_level == "WRITE"
    return ToolSpec(
        tool_id=tool_id,
        display_name=str(getattr(meta, "name", tool_id) or tool_id),
        description=str(getattr(meta, "desc", "") or ""),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        has_side_effects=has_side_effects,
        executor=None,
    )


def _proposal_only_spec(spec: ToolSpec) -> ToolSpec:
    """Copy metadata without exposing any legacy callback."""
    return ToolSpec(
        tool_id=spec.tool_id,
        display_name=spec.display_name,
        description=spec.description,
        parameters=dict(spec.parameters or {}),
        has_side_effects=bool(spec.has_side_effects),
        executor=None,
    )


def get_tools_for_agent(
    agent_id: str,
    allow_side_effects: bool = False,
    *,
    catalog: "Any | None" = None,
) -> list[ToolSpec]:
    """获取指定 Agent 可提出的 proposal-only 工具元数据。

    catalog 和静态 registry 都只能提供描述；返回值始终移除 executor。

    Args:
        agent_id: agent ID
        allow_side_effects: 是否允许有副作用的工具（仅 EXEC_INTENT 场景为 True）
        catalog: ToolCatalog 实例（可选，仅消费元数据）

    Returns:
        可用工具列表
    """
    normalized_agent_id = str(agent_id or "").strip()

    # catalog 路径（proposal metadata only）
    if catalog is not None:
        allowlist = _AGENT_TOOLS.get(normalized_agent_id, [])
        tools: list[ToolSpec] = []
        for tool_id in allowlist:
            try:
                meta = catalog.get(tool_id)
                spec = _tool_meta_to_spec(meta)
            except (KeyError, Exception):
                # catalog 中不存在时从 _TOOL_REGISTRY 查找
                spec = _TOOL_REGISTRY.get(tool_id)
            if spec is None:
                continue
            if spec.has_side_effects and not allow_side_effects:
                continue
            tools.append(_proposal_only_spec(spec))
        return tools

    # 静态 proposal catalog 路径
    if normalized_agent_id == _INTENT_AGENT_ID:
        tool_ids = _resolve_intent_dynamic_tool_ids(allow_side_effects=allow_side_effects)
    else:
        tool_ids = _AGENT_TOOLS.get(normalized_agent_id, [])
    tools = []
    for tool_id in tool_ids:
        spec = _TOOL_REGISTRY.get(tool_id)
        if spec is None:
            continue
        if spec.has_side_effects and not allow_side_effects:
            continue
        tools.append(_proposal_only_spec(spec))
    return tools


def execute_tool(tool_id: str, args: dict[str, Any]) -> dict[str, Any]:
    """Retired raw dispatcher retained as a fail-closed compatibility API."""
    del args
    return {
        "error": REACT_TOOL_GOVERNED_ADMISSION_REQUIRED,
        "reason_code": REACT_TOOL_GOVERNED_ADMISSION_REQUIRED,
        "proposed_tool_id": str(tool_id or "").strip(),
        "result": None,
        "side_effects_executed": False,
        "required_execution_surface": "new immutable TaskGraph revision",
    }


def is_tool_allowed(tool_id: str, agent_id: str, allow_side_effects: bool = False) -> bool:
    """检查工具是否允许被指定 agent 调用。"""
    allowed_tools = get_tools_for_agent(agent_id, allow_side_effects=allow_side_effects)
    allowed_ids = {t.tool_id for t in allowed_tools}
    return str(tool_id or "").strip() in allowed_ids
