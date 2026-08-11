"""Agent 工具提案元数据与权限矩阵。

ToolSpec 只描述模型可提出的动作，不授予执行权限。旧的本进程文件、网络、
浏览器和 subprocess handler 已删除；兼容 dispatcher 固定失败关闭。
任何正向执行都必须进入新的 immutable TaskGraph revision。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from host_brain.tools.contracts import ToolSpec


AGENT_TOOL_GOVERNED_ADMISSION_REQUIRED = (
    "AGENT_TOOL_GOVERNED_ADMISSION_REQUIRED"
)

# ---------------------------------------------------------------------------
# 工具权限矩阵
# ---------------------------------------------------------------------------

# run_mode -> 允许的工具 ID 集合
_AGENT_TOOL_PERMISSIONS: dict[str, set[str]] = {
    "inspect":    {"agent_read_file", "agent_list_files", "agent_finish"},
    "research":   {"agent_read_file", "agent_list_files", "agent_web_fetch", "browser_navigate", "browser_scrape", "browser_screenshot", "browser_execute_script", "agent_finish"},
    "audit":      {"agent_read_file", "agent_list_files", "agent_run_python", "agent_git_diff", "agent_finish"},
    "translate":  {"agent_read_file", "agent_list_files", "agent_write_file", "agent_finish"},
    "worker":     {"agent_read_file", "agent_write_file", "agent_list_files", "agent_run_python", "agent_git_diff", "browser_navigate", "browser_scrape", "browser_screenshot", "browser_click", "browser_execute_script", "agent_finish"},
    "self_heal":  {"agent_read_file", "agent_write_file", "agent_list_files", "agent_run_python", "agent_git_diff", "agent_finish"},
    "self_evolve":{"agent_read_file", "agent_write_file", "agent_list_files", "agent_run_python", "agent_git_diff", "agent_finish"},
}

# Tool IDs are execution primitives while WorkOrder actions are authority
# capabilities.  A tool must be allowed by both this mapping and run_mode.
_AGENT_TOOL_ACTIONS: dict[str, frozenset[str]] = {
    "agent_read_file": frozenset({"read_files"}),
    "agent_list_files": frozenset({"read_files"}),
    "agent_write_file": frozenset({"write_files"}),
    "agent_run_python": frozenset({"run_python"}),
    "agent_web_fetch": frozenset({"read_public_web_pages", "web_fetch"}),
    "browser_navigate": frozenset({"use_playwright_browser"}),
    "browser_scrape": frozenset({"use_playwright_browser"}),
    "browser_screenshot": frozenset({"use_playwright_browser", "capture_screenshots"}),
    "browser_click": frozenset({"use_playwright_browser", "browser_click"}),
    "browser_execute_script": frozenset({"use_playwright_browser"}),
    "agent_git_diff": frozenset({"read_files", "git_diff"}),
    "agent_finish": frozenset({"generate_report"}),
}


def _normalized_action_set(actions: Any) -> set[str]:
    if not isinstance(actions, (list, tuple, set, frozenset)):
        return set()
    return {str(action).strip().lower() for action in actions if str(action).strip()}


def _container_isolation_required() -> bool:
    backend = str(os.environ.get("SANDBOX_ISOLATION_BACKEND") or "subprocess").strip().lower()
    require_container = str(os.environ.get("SANDBOX_REQUIRE_CONTAINER") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    production = str(os.environ.get("Natsume_ENV") or "").strip().lower() in {
        "prod",
        "production",
    }
    return backend in {"docker", "auto"} and (require_container or production)


def _allowed_tools_for_work_order(
    *,
    run_mode: str,
    allowed_actions: Any,
    forbidden_actions: Any,
) -> set[str]:
    mode_allowed = set(_AGENT_TOOL_PERMISSIONS.get(str(run_mode).strip().lower(), set()))
    allowed = _normalized_action_set(allowed_actions)
    forbidden = _normalized_action_set(forbidden_actions)
    selected: set[str] = set()
    for tool_id in mode_allowed:
        capabilities = _AGENT_TOOL_ACTIONS.get(tool_id, frozenset())
        authority_names = {tool_id, *capabilities}
        if authority_names & forbidden:
            continue
        # agent_finish remains available so a fail-closed tool set can still
        # terminate without forcing the model into MAX_STEPS.
        if tool_id == "agent_finish" or authority_names & allowed:
            selected.add(tool_id)
    # The default subprocess backend only changes environment variables; it is
    # not an OS filesystem boundary.  Never advertise Python as a controlled
    # workspace tool unless container isolation is fail-closed.
    if not _container_isolation_required():
        selected.discard("agent_run_python")
    return selected


def is_tool_allowed_for_run_mode(
    tool_id: str,
    run_mode: str,
    *,
    allowed_actions: Any = (),
    forbidden_actions: Any = (),
) -> bool:
    """Check the run_mode and WorkOrder authority intersection."""

    allowed = _allowed_tools_for_work_order(
        run_mode=run_mode,
        allowed_actions=allowed_actions,
        forbidden_actions=forbidden_actions,
    )
    return str(tool_id).strip().lower() in allowed


# ---------------------------------------------------------------------------
# 工具定义（ToolSpec 格式）
# ---------------------------------------------------------------------------

_AGENT_TOOL_DEFS: list[dict[str, Any]] = [
    {
        "tool_id": "agent_read_file",
        "display_name": "读取文件",
        "description": "读取工作区内的文件内容（仅限文本文件）。参数：path（相对路径）。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对于工作区的文件路径"},
            },
            "required": ["path"],
        },
        "has_side_effects": False,
    },
    {
        "tool_id": "agent_write_file",
        "display_name": "写入文件",
        "description": "在工作区内创建或覆盖文件。禁止写入 .env、secrets、密钥文件。参数：path（相对路径）、content（内容）。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对于工作区的文件路径"},
                "content": {"type": "string", "description": "要写入的文件内容"},
            },
            "required": ["path", "content"],
        },
        "has_side_effects": True,
    },
    {
        "tool_id": "agent_list_files",
        "display_name": "列出文件",
        "description": "列出工作区目录中的文件和子目录。参数：path（可选，默认工作区根目录）。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对于工作区的目录路径（可选）"},
            },
            "required": [],
        },
        "has_side_effects": False,
    },
    {
        "tool_id": "agent_run_python",
        "display_name": "运行 Python 代码",
        "description": (
            "仅在 WorkOrder 明确授权 run_python 且强制容器隔离时执行 Python；"
            "不提供受控工作区写能力。参数：code（Python 源代码）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "要执行的 Python 源代码"},
            },
            "required": ["code"],
        },
        "has_side_effects": True,
    },
    {
        "tool_id": "agent_web_fetch",
        "display_name": "获取网页内容",
        "description": "HTTP GET 获取网页内容（浏览器研究用）。参数：url（完整 URL）、timeout（秒，默认30）。",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "完整的 HTTP/HTTPS URL"},
                "timeout": {"type": "integer", "description": "超时秒数（默认30，最大120）"},
            },
            "required": ["url"],
        },
        "has_side_effects": False,
    },
    {
        "tool_id": "browser_navigate",
        "display_name": "浏览器导航",
        "description": "使用无头浏览器打开 URL。参数：url（完整 URL）、wait_until（load/domcontentloaded/networkidle，默认 load）、timeout_ms（默认 30000）。需要 research/worker 模式。",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要打开的完整 URL"},
                "wait_until": {"type": "string", "description": "等待条件：load, domcontentloaded, networkidle"},
                "timeout_ms": {"type": "integer", "description": "超时毫秒数"},
            },
            "required": ["url"],
        },
        "has_side_effects": False,
    },
    {
        "tool_id": "browser_scrape",
        "display_name": "抓取页面内容",
        "description": "从当前浏览器页面抓取元素内容。参数：selector（CSS 选择器，默认 body）、extract（text/html/attribute，默认 text）、limit（最大元素数，默认 50）、attribute（extract=attribute 时提取的属性名）。需要先 navigate 后使用。",
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS 选择器，默认 body"},
                "extract": {"type": "string", "description": "提取类型：text, html, attribute"},
                "limit": {"type": "integer", "description": "最大元素数（默认 50，最大 200）"},
                "attribute": {"type": "string", "description": "extract=attribute 时提取的属性名"},
                "timeout_ms": {"type": "integer", "description": "超时毫秒数"},
            },
            "required": [],
        },
        "has_side_effects": False,
    },
    {
        "tool_id": "browser_screenshot",
        "display_name": "页面截图",
        "description": "对当前浏览器页面截图，返回 base64 图片。参数：save_path（可选保存路径，相对工作区）、full_page（是否全页截图，默认 false）。需要先 navigate 后使用。",
        "parameters": {
            "type": "object",
            "properties": {
                "save_path": {"type": "string", "description": "可选保存路径（相对工作区）"},
                "full_page": {"type": "boolean", "description": "是否全页截图"},
                "timeout_ms": {"type": "integer", "description": "超时毫秒数"},
            },
            "required": [],
        },
        "has_side_effects": True,
    },
    {
        "tool_id": "browser_click",
        "display_name": "点击元素",
        "description": "点击浏览器页面中的元素。参数：selector（CSS 选择器）、timeout_ms（默认 10000）。需要 worker 模式且先 navigate 后使用。",
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS 选择器"},
                "timeout_ms": {"type": "integer", "description": "超时毫秒数"},
            },
            "required": ["selector"],
        },
        "has_side_effects": True,
    },
    {
        "tool_id": "browser_execute_script",
        "display_name": "执行浏览器脚本",
        "description": "在当前浏览器页面执行 JavaScript 代码，返回执行结果。用于探查 SPA 页面结构、提取动态内容。参数：script（JavaScript 代码）。需要 research/worker 模式且先 navigate 后使用。\n提示：SPA 页面应先用此工具探查 DOM 结构（如 innerText 前 1000 字符、所有链接列表），再决定如何提取数据。",
        "parameters": {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "要执行的 JavaScript 代码"},
                "timeout_ms": {"type": "integer", "description": "超时毫秒数"},
            },
            "required": ["script"],
        },
        "has_side_effects": False,
    },
    {
        "tool_id": "agent_git_diff",
        "display_name": "Git Diff",
        "description": "获取工作区的 git diff 统计和未跟踪文件列表。无需参数。",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
        "has_side_effects": False,
    },
    {
        "tool_id": "agent_finish",
        "display_name": "完成任务",
        "description": "标记任务完成并返回最终结果。参数：summary（摘要）、result（详细结果）。",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "完成摘要"},
                "result": {"type": "string", "description": "详细结果文本"},
            },
            "required": ["summary"],
        },
        "has_side_effects": False,
    },
]


def build_agent_tool_specs(
    *,
    run_mode: str,
    allowed_actions: Any = (),
    forbidden_actions: Any = (),
) -> list[ToolSpec]:
    """Build the run_mode and WorkOrder authority intersection."""

    allowed = _allowed_tools_for_work_order(
        run_mode=run_mode,
        allowed_actions=allowed_actions,
        forbidden_actions=forbidden_actions,
    )
    specs: list[ToolSpec] = []
    for tool_def in _AGENT_TOOL_DEFS:
        if tool_def["tool_id"] not in allowed:
            continue
        specs.append(ToolSpec(
            tool_id=tool_def["tool_id"],
            display_name=tool_def["display_name"],
            description=tool_def["description"],
            parameters=tool_def["parameters"],
            has_side_effects=tool_def.get("has_side_effects", False),
            executor=None,  # proposal metadata only
        ))
    return specs


def execute_agent_tool(
    *,
    tool_id: str,
    args: dict[str, Any],
    workspace: Path,
    sandbox_enabled: bool = True,
) -> dict[str, Any]:
    """Retired raw dispatcher retained only as a fail-closed compatibility API."""
    del args, workspace, sandbox_enabled
    return {
        "error": AGENT_TOOL_GOVERNED_ADMISSION_REQUIRED,
        "reason_code": AGENT_TOOL_GOVERNED_ADMISSION_REQUIRED,
        "proposed_tool_id": str(tool_id or "").strip().lower(),
        "result": None,
        "side_effects_executed": False,
        "required_execution_surface": "new immutable TaskGraph revision",
    }


__all__ = [
    "AGENT_TOOL_GOVERNED_ADMISSION_REQUIRED",
    "build_agent_tool_specs",
    "is_tool_allowed_for_run_mode",
]
