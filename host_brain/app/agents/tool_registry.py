"""工具注册表：ReAct agent 可调用的工具列表（M-MA6）。

设计原则：
- 工具分为只读工具（allow_side_effects=False）和有副作用工具（allow_side_effects=True）
- 只读工具：所有场景均可调用
- 有副作用工具：仅 SCENE_EXEC_INTENT 场景允许调用（allow_side_effects=True 时）
- 工具执行结果写入 react_scratchpad.steps[n].observation，不直接修改 GraphState 控制字段
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


# ---------------------------------------------------------------------------
# ToolSpec：工具规格定义
# ---------------------------------------------------------------------------

@dataclass
class ToolSpec:
    """工具规格定义。"""
    tool_id: str
    display_name: str
    description: str
    parameters: dict[str, Any]          # JSON Schema 格式的参数定义
    has_side_effects: bool = False       # True = 有副作用（写操作），需要 allow_side_effects=True
    executor: Callable[..., Any] | None = field(default=None, repr=False)

    def to_llm_schema(self) -> dict[str, Any]:
        """转换为 LLM function calling 格式的 schema。"""
        return {
            "name": self.tool_id,
            "description": self.description,
            "parameters": self.parameters,
        }


# ---------------------------------------------------------------------------
# 内置工具实现（只读工具，无副作用）
# ---------------------------------------------------------------------------

def _tool_search_knowledge(args: dict[str, Any]) -> dict[str, Any]:
    """知识库检索工具（只读）。"""
    query = str(args.get("query") or "").strip()
    if not query:
        return {"error": "MISSING_QUERY", "result": None}
    # 第一阶段：返回占位结果，后续接入真实检索
    return {
        "result": f"[知识库检索占位] query={query}",
        "source": "knowledge_base",
        "confidence": 0.7,
    }


def _tool_get_context(args: dict[str, Any]) -> dict[str, Any]:
    """获取当前对话上下文（只读）。"""
    return {
        "result": "当前对话上下文已获取",
        "context_type": "conversation",
    }


def _tool_analyze_code(args: dict[str, Any]) -> dict[str, Any]:
    """代码分析工具（只读）。"""
    code = str(args.get("code") or "").strip()
    language = str(args.get("language") or "python").strip()
    if not code:
        return {"error": "MISSING_CODE", "result": None}
    return {
        "result": f"[代码分析占位] language={language}, lines={len(code.splitlines())}",
        "language": language,
        "analysis_type": "static",
    }


def _tool_translate_text(args: dict[str, Any]) -> dict[str, Any]:
    """文本翻译工具（只读）。"""
    text = str(args.get("text") or "").strip()
    target_lang = str(args.get("target_lang") or "en").strip()
    if not text:
        return {"error": "MISSING_TEXT", "result": None}
    return {
        "result": f"[翻译占位] target_lang={target_lang}, text_len={len(text)}",
        "target_lang": target_lang,
    }


# ---------------------------------------------------------------------------
# 工具注册表
# ---------------------------------------------------------------------------

_TOOL_REGISTRY: dict[str, ToolSpec] = {
    "search_knowledge": ToolSpec(
        tool_id="search_knowledge",
        display_name="知识库检索",
        description="在知识库中检索相关信息，返回最相关的内容片段",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索查询词"},
            },
            "required": ["query"],
        },
        has_side_effects=False,
        executor=_tool_search_knowledge,
    ),
    "get_context": ToolSpec(
        tool_id="get_context",
        display_name="获取对话上下文",
        description="获取当前对话的历史上下文信息",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        has_side_effects=False,
        executor=_tool_get_context,
    ),
    "analyze_code": ToolSpec(
        tool_id="analyze_code",
        display_name="代码分析",
        description="对给定代码进行静态分析，返回分析结果",
        parameters={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "要分析的代码"},
                "language": {"type": "string", "description": "编程语言，默认 python"},
            },
            "required": ["code"],
        },
        has_side_effects=False,
        executor=_tool_analyze_code,
    ),
    "translate_text": ToolSpec(
        tool_id="translate_text",
        display_name="文本翻译",
        description="将文本翻译为目标语言",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要翻译的文本"},
                "target_lang": {"type": "string", "description": "目标语言代码，如 en/ja/zh"},
            },
            "required": ["text"],
        },
        has_side_effects=False,
        executor=_tool_translate_text,
    ),
}

# 各 agent 可用的工具集合（只读工具）
_AGENT_TOOLS: dict[str, list[str]] = {
    "chat_agent":      ["search_knowledge", "get_context"],
    "code_agent":      ["analyze_code", "search_knowledge"],
    "translate_agent": ["translate_text", "search_knowledge"],
    "intent_agent":    ["search_knowledge", "get_context", "analyze_code"],
    "judge_agent":     [],  # JudgeAgent 不调用工具，只裁判工件
}


def get_tools_for_agent(agent_id: str, allow_side_effects: bool = False) -> list[ToolSpec]:
    """获取指定 agent 可用的工具列表。

    Args:
        agent_id: agent ID
        allow_side_effects: 是否允许有副作用的工具（仅 EXEC_INTENT 场景为 True）

    Returns:
        可用工具列表
    """
    tool_ids = _AGENT_TOOLS.get(str(agent_id or "").strip(), [])
    tools = []
    for tool_id in tool_ids:
        spec = _TOOL_REGISTRY.get(tool_id)
        if spec is None:
            continue
        if spec.has_side_effects and not allow_side_effects:
            continue  # 有副作用工具在不允许时跳过
        tools.append(spec)
    return tools


def execute_tool(tool_id: str, args: dict[str, Any]) -> dict[str, Any]:
    """执行工具调用。

    Args:
        tool_id: 工具 ID
        args: 工具参数

    Returns:
        工具执行结果字典
    """
    spec = _TOOL_REGISTRY.get(str(tool_id or "").strip())
    if spec is None:
        return {"error": f"TOOL_NOT_FOUND:{tool_id}", "result": None}
    if spec.executor is None:
        return {"error": f"TOOL_NO_EXECUTOR:{tool_id}", "result": None}
    try:
        return spec.executor(dict(args or {}))
    except Exception as exc:
        return {"error": f"TOOL_EXEC_ERROR:{type(exc).__name__}:{exc}", "result": None}


def is_tool_allowed(tool_id: str, agent_id: str, allow_side_effects: bool = False) -> bool:
    """检查工具是否允许被指定 agent 调用。"""
    allowed_tools = get_tools_for_agent(agent_id, allow_side_effects=allow_side_effects)
    allowed_ids = {t.tool_id for t in allowed_tools}
    return str(tool_id or "").strip() in allowed_ids
