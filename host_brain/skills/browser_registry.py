"""浏览器自动化 Skill 包（agent-browser / Playwright / Puppeteer 适配层）。

声明式 Provider：新增浏览器 action 时，只需补一个 DeclarativeSkillSpec。
执行层仍由 browser_executor.py 负责。
"""

from __future__ import annotations

from typing import Any

from host_brain.skills.declarative import DeclarativeSkillProvider, DeclarativeSkillSpec
from host_brain.skills.registry import SkillRegistry


def _normalize_navigate_args(args: dict[str, Any]) -> dict[str, Any]:
    url = str((args or {}).get("url") or "").strip()
    wait_until = str((args or {}).get("wait_until") or "load").strip().lower()
    if wait_until not in {"load", "domcontentloaded", "networkidle"}:
        wait_until = "load"
    try:
        timeout_ms = int((args or {}).get("timeout_ms") or 30000)
    except (TypeError, ValueError):
        timeout_ms = 30000
    return {
        "url": url,
        "wait_until": wait_until,
        "timeout_ms": timeout_ms,
    }


def _normalize_click_args(args: dict[str, Any]) -> dict[str, Any]:
    selector = str((args or {}).get("selector") or "").strip()
    selector_type = str((args or {}).get("selector_type") or "css").strip().lower()
    if selector_type not in {"css", "xpath"}:
        selector_type = "css"
    button = str((args or {}).get("button") or "left").strip().lower()
    if button not in {"left", "right", "middle"}:
        button = "left"
    try:
        timeout_ms = int((args or {}).get("timeout_ms") or 5000)
    except (TypeError, ValueError):
        timeout_ms = 5000
    return {
        "selector": selector,
        "selector_type": selector_type,
        "button": button,
        "timeout_ms": timeout_ms,
    }


def _normalize_type_args(args: dict[str, Any]) -> dict[str, Any]:
    selector = str((args or {}).get("selector") or "").strip()
    text = str((args or {}).get("text") or "")
    try:
        delay_ms = int((args or {}).get("delay_ms") or 50)
    except (TypeError, ValueError):
        delay_ms = 50
    clear_first_raw = (args or {}).get("clear_first")
    clear_first = bool(clear_first_raw) if clear_first_raw is not None else True
    return {
        "selector": selector,
        "text": text,
        "delay_ms": max(0, delay_ms),
        "clear_first": clear_first,
    }


def _normalize_screenshot_args(args: dict[str, Any]) -> dict[str, Any]:
    save_path = str((args or {}).get("save_path") or "").strip() or None
    selector = str((args or {}).get("selector") or "").strip() or None
    full_page_raw = (args or {}).get("full_page")
    full_page = bool(full_page_raw) if full_page_raw is not None else False
    return {
        "save_path": save_path,
        "selector": selector,
        "full_page": full_page,
        "return_base64": save_path is None,
    }


def _normalize_execute_script_args(args: dict[str, Any]) -> dict[str, Any]:
    script = str((args or {}).get("script") or "").strip()
    script_args = (args or {}).get("args") or []
    if not isinstance(script_args, list):
        script_args = []
    return {
        "script": script,
        "script_args": script_args,
    }


def _normalize_wait_for_args(args: dict[str, Any]) -> dict[str, Any]:
    condition = str((args or {}).get("condition") or "selector").strip().lower()
    if condition not in {"selector", "url", "function", "timeout"}:
        condition = "selector"
    value = str((args or {}).get("value") or "").strip()
    try:
        timeout_ms = int((args or {}).get("timeout_ms") or 10000)
    except (TypeError, ValueError):
        timeout_ms = 10000
    state = str((args or {}).get("state") or "visible").strip().lower()
    if state not in {"visible", "hidden", "attached", "detached"}:
        state = "visible"
    return {
        "condition": condition,
        "value": value,
        "timeout_ms": timeout_ms,
        "state": state,
    }


def _normalize_scrape_args(args: dict[str, Any]) -> dict[str, Any]:
    selector = str((args or {}).get("selector") or "body").strip()
    extract = str((args or {}).get("extract") or "text").strip().lower()
    if extract not in {"text", "html", "attribute", "all"}:
        extract = "text"
    attribute = str((args or {}).get("attribute") or "").strip() or None
    try:
        limit = max(1, min(500, int((args or {}).get("limit") or 50)))
    except (TypeError, ValueError):
        limit = 50
    return {
        "selector": selector,
        "extract": extract,
        "attribute": attribute,
        "limit": limit,
    }


def _normalize_close_args(args: dict[str, Any]) -> dict[str, Any]:
    target = str((args or {}).get("target") or "page").strip().lower()
    if target not in {"page", "browser"}:
        target = "page"
    force_raw = (args or {}).get("force")
    force = bool(force_raw) if force_raw is not None else False
    return {
        "target": target,
        "force": force,
    }


_BROWSER_CONTEXT = (("session_id", "session_id"), ("request_id", "request_id"))


class BrowserSkillProvider(DeclarativeSkillProvider):
    """浏览器自动化 Skill 集合。"""

    skill_specs = (
        DeclarativeSkillSpec(
            skill_name="browser_navigate",
            method_name="browser_navigate",
            clearance="LOW",
            risk_level=2,
            scope="system",
            reversible=True,
            description="在浏览器中导航到指定 URL（只读，不修改页面状态）。",
            tool="browser",
            action="navigate",
            normalize_args=_normalize_navigate_args,
            context_aliases=_BROWSER_CONTEXT,
        ),
        DeclarativeSkillSpec(
            skill_name="browser_click",
            method_name="browser_click",
            clearance="MEDIUM",
            risk_level=3,
            scope="system",
            reversible=True,
            description="点击页面上的指定元素（CSS 选择器或 XPath）。",
            tool="browser",
            action="click",
            normalize_args=_normalize_click_args,
            context_aliases=_BROWSER_CONTEXT,
        ),
        DeclarativeSkillSpec(
            skill_name="browser_type",
            method_name="browser_type",
            clearance="MEDIUM",
            risk_level=3,
            scope="system",
            reversible=True,
            description="在指定输入框中输入文本（模拟键盘逐字输入）。",
            tool="browser",
            action="type",
            normalize_args=_normalize_type_args,
            context_aliases=_BROWSER_CONTEXT,
        ),
        DeclarativeSkillSpec(
            skill_name="browser_screenshot",
            method_name="browser_screenshot",
            clearance="LOW",
            risk_level=2,
            scope="system",
            reversible=True,
            description="对当前页面或指定元素截图，返回 base64 编码图像或保存路径。",
            tool="browser",
            action="screenshot",
            normalize_args=_normalize_screenshot_args,
            context_aliases=_BROWSER_CONTEXT,
        ),
        DeclarativeSkillSpec(
            skill_name="browser_execute_script",
            method_name="browser_execute_script",
            clearance="MEDIUM",
            risk_level=3,
            scope="system",
            reversible=False,
            description="在浏览器页面上下文中执行任意 JavaScript 脚本。",
            tool="browser",
            action="execute_script",
            normalize_args=_normalize_execute_script_args,
            context_aliases=_BROWSER_CONTEXT,
        ),
        DeclarativeSkillSpec(
            skill_name="browser_wait_for",
            method_name="browser_wait_for",
            clearance="LOW",
            risk_level=2,
            scope="system",
            reversible=True,
            description="等待页面元素出现、消失或满足指定条件。",
            tool="browser",
            action="wait_for",
            normalize_args=_normalize_wait_for_args,
            context_aliases=_BROWSER_CONTEXT,
        ),
        DeclarativeSkillSpec(
            skill_name="browser_scrape",
            method_name="browser_scrape",
            clearance="LOW",
            risk_level=2,
            scope="system",
            reversible=True,
            description="抓取页面内容：提取文本、属性或结构化数据（只读）。",
            tool="browser",
            action="scrape",
            normalize_args=_normalize_scrape_args,
            context_aliases=_BROWSER_CONTEXT,
        ),
        DeclarativeSkillSpec(
            skill_name="browser_close",
            method_name="browser_close",
            clearance="MEDIUM",
            risk_level=4,
            scope="system",
            reversible=False,
            require_confirm=True,
            description="关闭当前浏览器页面或整个浏览器实例（不可逆，需确认）。",
            tool="browser",
            action="close",
            normalize_args=_normalize_close_args,
            context_aliases=_BROWSER_CONTEXT,
        ),
    )


def build_browser_skill_registry(provider: BrowserSkillProvider | None = None) -> SkillRegistry:
    """构建包含浏览器自动化 Skills 的 SkillRegistry。"""
    registry = SkillRegistry()
    registry.register_decorated(provider or BrowserSkillProvider())
    return registry
