"""Agent 专用工具定义与权限矩阵。

每个工具使用 ToolSpec 格式（与 app/agents/tool_registry.py 兼容），
工作区写操作委托给 SandboxExecutor，文件操作限制在工作区边界内。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from host_brain.agent_runtime.workspace_paths import (
    normalize_workspace_root,
    resolve_workspace_path,
)
from host_brain.core.subprocess_utils import run as _subprocess_run

from host_brain.skills.impl.browser_executor import execute_browser_skill

from host_brain.tools.contracts import ToolSpec

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 工具执行器函数
# ---------------------------------------------------------------------------

_ALLOWED_READ_EXTENSIONS = {
    ".py", ".txt", ".md", ".yaml", ".yml", ".json", ".toml", ".cfg",
    ".ini", ".env.example", ".sh", ".ps1", ".bat", ".xml", ".html",
    ".css", ".js", ".ts", ".jsx", ".tsx", ".vue", ".sql", ".csv",
    ".log", ".rst", ".conf", ".cfg",
}

_MAX_READ_BYTES = 128 * 1024  # 128KB


def _ensure_path(value: Path | str) -> Path:
    """确保值是 Path 对象。"""
    if isinstance(value, Path):
        return value
    return Path(str(value))


def _check_path_within_workspace(
    target: Path | str,
    workspace: Path,
    *,
    must_exist: bool = False,
    allow_root: bool = False,
) -> Path:
    """兼容入口：所有工作区路径统一委托给 canonical resolver。"""

    return resolve_workspace_path(
        workspace,
        target,
        must_exist=must_exist,
        allow_root=allow_root,
    )


def _tool_agent_read_file(args: dict[str, Any], *, workspace: Path) -> dict[str, Any]:
    """读取工作区内文件内容。"""
    ws = _ensure_path(workspace)
    path = str(args.get("path") or args.get("file_path") or "").strip()
    if not path:
        return {"error": "missing path argument"}
    try:
        resolved = _check_path_within_workspace(path, ws, must_exist=True)
    except PermissionError as exc:
        return {"error": str(exc)}
    if not resolved.exists():
        return {"error": f"file not found: {path}"}
    if not resolved.is_file():
        return {"error": f"not a file: {path}"}
    suffix = resolved.suffix.lower()
    if suffix not in _ALLOWED_READ_EXTENSIONS:
        return {"error": f"file type not allowed for reading: {suffix}"}
    try:
        text = resolved.read_text(encoding="utf-8", errors="replace")
        truncated = text[:_MAX_READ_BYTES]
        return {
            "path": str(resolved),
            "content": truncated,
            "size_bytes": len(text),
            "truncated": len(text) > _MAX_READ_BYTES,
        }
    except Exception as exc:
        return {"error": str(exc)}


def _tool_agent_write_file(args: dict[str, Any], *, workspace: Path) -> dict[str, Any]:
    """在工作区内写入文件。"""
    ws = _ensure_path(workspace)
    path = str(args.get("path") or args.get("file_path") or "").strip()
    content = str(args.get("content") or "")
    if not path:
        return {"error": "missing path argument"}
    try:
        resolved = _check_path_within_workspace(path, ws)
    except PermissionError as exc:
        return {"error": str(exc)}
    # 禁止覆盖 .env、secrets、密钥文件
    filename = resolved.name.lower()
    forbidden_names = {"secrets", ".env", "secret_store", "key_profiles", "credentials"}
    if any(fn in filename for fn in forbidden_names) or filename.endswith(".key"):
        return {"error": f"forbidden file: {filename}"}
    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8", errors="replace")
        return {
            "path": str(resolved),
            "written_bytes": len(content),
            "action": "written",
        }
    except Exception as exc:
        return {"error": str(exc)}


def _tool_agent_list_files(args: dict[str, Any], *, workspace: Path) -> dict[str, Any]:
    """列出工作区目录内容。"""
    ws = _ensure_path(workspace)
    subdir = str(args.get("path") or "").strip()
    try:
        target = _check_path_within_workspace(
            subdir or ".",
            ws,
            must_exist=True,
            allow_root=not subdir,
        )
    except PermissionError as exc:
        return {"error": str(exc)}
    if not target.exists():
        return {"error": f"directory not found: {subdir}"}
    if not target.is_dir():
        return {"error": f"not a directory: {subdir}"}
    try:
        entries = []
        for entry in sorted(target.iterdir()):
            safe_entry = resolve_workspace_path(
                ws,
                entry.name,
                base=target,
                must_exist=True,
            )
            entries.append({
                "name": entry.name,
                "type": "dir" if safe_entry.is_dir() else "file",
                "size_bytes": safe_entry.stat().st_size if safe_entry.is_file() else None,
            })
        return {"path": str(target), "entries": entries[:200]}
    except Exception as exc:
        return {"error": str(exc)}


def _tool_agent_run_shell(args: dict[str, Any], *, workspace: Path, sandbox_enabled: bool = True) -> dict[str, Any]:
    """在沙箱中运行 shell 命令。"""
    command = str(args.get("command") or args.get("cmd") or "").strip()
    if not command:
        return {"error": "missing command argument"}
    timeout = min(int(args.get("timeout") or 60), 300)
    if not sandbox_enabled:
        return {"error": "shell execution requires sandbox mode"}
    try:
        from host_brain.airlock import SandboxExecutor  # noqa: PLC0415

        result = SandboxExecutor().execute(
            command,
            request_id=None,
            intent_id="agent_shell",
            language="shell",
        )
        return {
            "exit_code": result.exit_code,
            "stdout": (result.stdout or "")[:8000],
            "stderr": (result.stderr or "")[:4000],
            "status": result.status,
            "duration_ms": result.duration_ms,
        }
    except Exception as exc:
        return {"error": str(exc)}


def _tool_agent_run_python(args: dict[str, Any], *, workspace: Path, sandbox_enabled: bool = True) -> dict[str, Any]:
    """在沙箱中运行 Python 代码。"""
    code = str(args.get("code") or args.get("source") or "").strip()
    if not code:
        return {"error": "missing code argument"}
    if not sandbox_enabled:
        return {"error": "python execution requires sandbox mode"}
    try:
        from host_brain.airlock import SandboxExecutor  # noqa: PLC0415

        result = SandboxExecutor().execute(
            code,
            request_id=None,
            intent_id="agent_python",
            language="python",
        )
        return {
            "exit_code": result.exit_code,
            "stdout": (result.stdout or "")[:8000],
            "stderr": (result.stderr or "")[:4000],
            "status": result.status,
            "duration_ms": result.duration_ms,
        }
    except Exception as exc:
        return {"error": str(exc)}


def _tool_agent_web_fetch(args: dict[str, Any], *, workspace: Path) -> dict[str, Any]:
    """HTTP 获取网页内容（用于浏览器研究类任务）。"""
    import urllib.request

    url = str(args.get("url") or "").strip()
    if not url:
        return {"error": "missing url argument"}
    if not url.startswith(("http://", "https://")):
        return {"error": "url must start with http:// or https://"}
    timeout = min(int(args.get("timeout") or 30), 120)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Natsume-Agent/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")[:_MAX_READ_BYTES * 2]
            return {
                "url": url,
                "status": resp.status,
                "content_type": resp.headers.get("Content-Type", ""),
                "body": body,
                "truncated": len(body) >= _MAX_READ_BYTES * 2,
            }
    except Exception as exc:
        return {"error": str(exc)}


def _tool_agent_git_diff(args: dict[str, Any], *, workspace: Path) -> dict[str, Any]:
    """获取工作区 git diff。"""
    ws = normalize_workspace_root(workspace)
    try:
        completed = _subprocess_run(
            ["git", "-C", str(ws), "diff", "--stat"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        return {
            "diff_stat": str(completed.stdout or "")[:8000],
            "untracked": _git_untracked(ws),
        }
    except Exception as exc:
        return {"error": str(exc)}


def _git_untracked(workspace: Path) -> list[str]:
    try:
        completed = _subprocess_run(
            ["git", "-C", str(workspace), "ls-files", "--others", "--exclude-standard"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
        return [line for line in str(completed.stdout or "").splitlines() if line.strip()]
    except Exception:
        return []


def _tool_agent_finish(args: dict[str, Any], *, workspace: Path) -> dict[str, Any]:
    """标记任务完成，返回最终结果。"""
    return {
        "status": "finished",
        "summary": str(args.get("summary") or "Task completed."),
        "result": args.get("result") or "",
    }


# ---------------------------------------------------------------------------
# 浏览器自动化工具 — 委托给 execute_browser_skill()
# ---------------------------------------------------------------------------

def _tool_browser_navigate(args: dict[str, Any], *, workspace: Path) -> dict[str, Any]:
    """使用无头浏览器导航到指定 URL。"""
    url = str(args.get("url") or "").strip()
    if not url:
        return {"error": "url is required"}
    return execute_browser_skill({
        "action": "navigate",
        "url": url,
        "wait_until": str(args.get("wait_until") or "load").strip(),
        "timeout_ms": int(args.get("timeout_ms") or 30000),
    })


def _tool_browser_scrape(args: dict[str, Any], *, workspace: Path) -> dict[str, Any]:
    """从当前浏览器页面抓取内容。"""
    selector = str(args.get("selector") or "body").strip() or "body"
    extract = str(args.get("extract") or "text").strip().lower()
    limit = int(args.get("limit") or 50)
    return execute_browser_skill({
        "action": "scrape",
        "selector": selector,
        "extract": extract,
        "limit": max(1, min(limit, 200)),
        "attribute": str(args.get("attribute") or "").strip() or None,
        "timeout_ms": int(args.get("timeout_ms") or 15000),
    })


def _tool_browser_screenshot(args: dict[str, Any], *, workspace: Path) -> dict[str, Any]:
    """对当前浏览器页面截图并返回 base64 图片。"""
    save_path = str(args.get("save_path") or "").strip() or None
    full_page = bool(args.get("full_page", False))
    if save_path:
        try:
            resolved = resolve_workspace_path(workspace, save_path)
        except PermissionError as exc:
            return {"error": str(exc)}
        resolved.parent.mkdir(parents=True, exist_ok=True)
        save_path = str(resolved)
    return execute_browser_skill({
        "action": "screenshot",
        "save_path": save_path,
        "full_page": full_page,
        "return_base64": True,
        "timeout_ms": int(args.get("timeout_ms") or 30000),
    })


def _tool_browser_click(args: dict[str, Any], *, workspace: Path) -> dict[str, Any]:
    """点击浏览器页面中的元素。"""
    selector = str(args.get("selector") or "").strip()
    if not selector:
        return {"error": "selector is required"}
    return execute_browser_skill({
        "action": "click",
        "selector": selector,
        "timeout_ms": int(args.get("timeout_ms") or 10000),
    })


def _tool_browser_execute_script(args: dict[str, Any], *, workspace: Path) -> dict[str, Any]:
    """在浏览器页面中执行 JavaScript。"""
    script = str(args.get("script") or "").strip()
    if not script:
        return {"error": "script is required"}
    return execute_browser_skill({
        "action": "execute_script",
        "script": script,
        "timeout_ms": int(args.get("timeout_ms") or 15000),
    })


# ---------------------------------------------------------------------------
# 工具权限矩阵
# ---------------------------------------------------------------------------

# run_mode -> 允许的工具 ID 集合
_AGENT_TOOL_PERMISSIONS: dict[str, set[str]] = {
    "inspect":    {"agent_read_file", "agent_list_files", "agent_finish"},
    "research":   {"agent_read_file", "agent_list_files", "agent_web_fetch", "browser_navigate", "browser_scrape", "browser_screenshot", "browser_execute_script", "agent_finish"},
    "audit":      {"agent_read_file", "agent_list_files", "agent_run_shell", "agent_run_python", "agent_git_diff", "agent_finish"},
    "translate":  {"agent_read_file", "agent_list_files", "agent_write_file", "agent_finish"},
    "worker":     {"agent_read_file", "agent_write_file", "agent_list_files", "agent_run_shell", "agent_run_python", "agent_git_diff", "browser_navigate", "browser_scrape", "browser_screenshot", "browser_click", "browser_execute_script", "agent_finish"},
    "self_heal":  {"agent_read_file", "agent_write_file", "agent_list_files", "agent_run_shell", "agent_run_python", "agent_git_diff", "agent_finish"},
    "self_evolve":{"agent_read_file", "agent_write_file", "agent_list_files", "agent_run_shell", "agent_run_python", "agent_git_diff", "agent_finish"},
}


def is_tool_allowed_for_run_mode(tool_id: str, run_mode: str) -> bool:
    """检查工具是否允许在指定运行模式下使用。"""
    allowed = _AGENT_TOOL_PERMISSIONS.get(str(run_mode).strip().lower(), set())
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
        "tool_id": "agent_run_shell",
        "display_name": "运行 Shell 命令",
        "description": "在沙箱中执行 shell 命令。需要 worker/self_heal/self_evolve 模式。参数：command（命令字符串）、timeout（秒，默认60）。",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 shell 命令"},
                "timeout": {"type": "integer", "description": "超时秒数（默认60，最大300）"},
            },
            "required": ["command"],
        },
        "has_side_effects": True,
    },
    {
        "tool_id": "agent_run_python",
        "display_name": "运行 Python 代码",
        "description": "在沙箱中执行 Python 代码片段。需要 worker/self_heal/self_evolve 模式。参数：code（Python 源代码）。",
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


def build_agent_tool_specs(*, run_mode: str) -> list[ToolSpec]:
    """根据运行模式构建允许的工具列表。"""
    allowed = _AGENT_TOOL_PERMISSIONS.get(str(run_mode).strip().lower(), set())
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
            executor=None,  # 由 AgentExecutor 自行调度
        ))
    return specs


def execute_agent_tool(
    *,
    tool_id: str,
    args: dict[str, Any],
    workspace: Path,
    sandbox_enabled: bool = True,
) -> dict[str, Any]:
    """执行单个 Agent 工具并返回结果字典。"""
    tool_id_norm = str(tool_id or "").strip().lower()
    args_clean = dict(args or {})
    workspace_path = _ensure_path(workspace)

    handlers: dict[str, Any] = {
        "agent_read_file":    lambda: _tool_agent_read_file(args_clean, workspace=workspace_path),
        "agent_write_file":   lambda: _tool_agent_write_file(args_clean, workspace=workspace_path),
        "agent_list_files":   lambda: _tool_agent_list_files(args_clean, workspace=workspace_path),
        "agent_run_shell":    lambda: _tool_agent_run_shell(args_clean, workspace=workspace_path, sandbox_enabled=sandbox_enabled),
        "agent_run_python":   lambda: _tool_agent_run_python(args_clean, workspace=workspace_path, sandbox_enabled=sandbox_enabled),
        "agent_web_fetch":          lambda: _tool_agent_web_fetch(args_clean, workspace=workspace_path),
        "browser_navigate":   lambda: _tool_browser_navigate(args_clean, workspace=workspace_path),
        "browser_scrape":     lambda: _tool_browser_scrape(args_clean, workspace=workspace_path),
        "browser_screenshot": lambda: _tool_browser_screenshot(args_clean, workspace=workspace_path),
        "browser_click":          lambda: _tool_browser_click(args_clean, workspace=workspace_path),
        "browser_execute_script": lambda: _tool_browser_execute_script(args_clean, workspace=workspace_path),
        "agent_git_diff":               lambda: _tool_agent_git_diff(args_clean, workspace=workspace_path),
        "agent_finish":             lambda: _tool_agent_finish(args_clean, workspace=workspace_path),
    }

    handler = handlers.get(tool_id_norm)
    if handler is None:
        return {"error": f"unknown tool: {tool_id}"}
    try:
        return handler()
    except Exception as exc:
        logger.warning("Agent tool %s failed: %s", tool_id, exc)
        return {"error": str(exc)}


__all__ = [
    "build_agent_tool_specs",
    "execute_agent_tool",
    "is_tool_allowed_for_run_mode",
]
