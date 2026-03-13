"""浏览器自动化 Skill 包（agent-browser / Playwright / Puppeteer 适配层）。

所有 handler 均为轻量归一化层，返回标准化 payload，
由上层运行时（BrowserAgent / PlaywrightRunner）负责实际执行。

风险分级说明：
  risk_level=2  只读操作（导航、截图、抓取、等待）
  risk_level=3  写操作（点击、输入、脚本执行）
  risk_level=4  破坏性操作（关闭浏览器），require_confirm=True
"""

from __future__ import annotations

from typing import Any

from host_brain.skills.registry import SkillRegistry, skill


class BrowserSkillProvider:
    """浏览器自动化 Skill 集合。

    所有方法通过 @skill 装饰器注册元数据，
    由 build_browser_skill_registry() 统一扫描注册。
    """

    @skill(
        skill_name="browser_navigate",
        clearance="LOW",
        risk_level=2,
        scope="system",
        reversible=True,
        description="在浏览器中导航到指定 URL（只读，不修改页面状态）。",
    )
    def browser_navigate(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """导航到指定 URL。

        Args:
            url (str): 目标 URL（必填）
            wait_until (str): 等待条件，可选 load / domcontentloaded / networkidle（默认 load）
            timeout_ms (int): 超时毫秒数（默认 30000）
        """
        url = str((args or {}).get("url") or "").strip()
        wait_until = str((args or {}).get("wait_until") or "load").strip().lower()
        if wait_until not in {"load", "domcontentloaded", "networkidle"}:
            wait_until = "load"
        try:
            timeout_ms = int((args or {}).get("timeout_ms") or 30000)
        except (TypeError, ValueError):
            timeout_ms = 30000
        return {
            "status": "OK",
            "tool": "browser",
            "action": "navigate",
            "url": url,
            "wait_until": wait_until,
            "timeout_ms": timeout_ms,
            "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
        }

    @skill(
        skill_name="browser_click",
        clearance="MEDIUM",
        risk_level=3,
        scope="system",
        reversible=True,
        description="点击页面上的指定元素（CSS 选择器或 XPath）。",
    )
    def browser_click(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """点击页面元素。

        Args:
            selector (str): CSS 选择器或 XPath（必填）
            selector_type (str): 选择器类型，css / xpath（默认 css）
            button (str): 鼠标按键，left / right / middle（默认 left）
            timeout_ms (int): 超时毫秒数（默认 5000）
        """
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
            "status": "OK",
            "tool": "browser",
            "action": "click",
            "selector": selector,
            "selector_type": selector_type,
            "button": button,
            "timeout_ms": timeout_ms,
            "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
        }

    @skill(
        skill_name="browser_type",
        clearance="MEDIUM",
        risk_level=3,
        scope="system",
        reversible=True,
        description="在指定输入框中输入文本（模拟键盘逐字输入）。",
    )
    def browser_type(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """在输入框中输入文本。

        Args:
            selector (str): 目标输入框的 CSS 选择器（必填）
            text (str): 要输入的文本（必填）
            delay_ms (int): 每次按键间隔毫秒数（默认 50，模拟人工输入）
            clear_first (bool): 输入前是否先清空（默认 True）
        """
        selector = str((args or {}).get("selector") or "").strip()
        text = str((args or {}).get("text") or "")
        try:
            delay_ms = int((args or {}).get("delay_ms") or 50)
        except (TypeError, ValueError):
            delay_ms = 50
        clear_first_raw = (args or {}).get("clear_first")
        clear_first = bool(clear_first_raw) if clear_first_raw is not None else True
        return {
            "status": "OK",
            "tool": "browser",
            "action": "type",
            "selector": selector,
            "text": text,
            "delay_ms": max(0, delay_ms),
            "clear_first": clear_first,
            "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
        }

    @skill(
        skill_name="browser_screenshot",
        clearance="LOW",
        risk_level=2,
        scope="system",
        reversible=True,
        description="对当前页面或指定元素截图，返回 base64 编码图像或保存路径。",
    )
    def browser_screenshot(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """截取页面截图。

        Args:
            save_path (str): 截图保存路径（可选，不填则返回 base64）
            selector (str): 仅截取指定元素（可选，不填则截全页）
            full_page (bool): 是否截取完整页面（默认 False）
        """
        save_path = str((args or {}).get("save_path") or "").strip() or None
        selector = str((args or {}).get("selector") or "").strip() or None
        full_page_raw = (args or {}).get("full_page")
        full_page = bool(full_page_raw) if full_page_raw is not None else False
        return {
            "status": "OK",
            "tool": "browser",
            "action": "screenshot",
            "save_path": save_path,
            "selector": selector,
            "full_page": full_page,
            "return_base64": save_path is None,
            "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
        }

    @skill(
        skill_name="browser_execute_script",
        clearance="MEDIUM",
        risk_level=3,
        scope="system",
        reversible=False,
        description="在浏览器页面上下文中执行任意 JavaScript 脚本。",
    )
    def browser_execute_script(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """执行 JavaScript 脚本。

        Args:
            script (str): 要执行的 JS 代码（必填）
            args (list): 传递给脚本的参数列表（可选）
        """
        script = str((args or {}).get("script") or "").strip()
        script_args = (args or {}).get("args") or []
        if not isinstance(script_args, list):
            script_args = []
        return {
            "status": "OK",
            "tool": "browser",
            "action": "execute_script",
            "script": script,
            "script_args": script_args,
            "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
        }

    @skill(
        skill_name="browser_wait_for",
        clearance="LOW",
        risk_level=2,
        scope="system",
        reversible=True,
        description="等待页面元素出现、消失或满足指定条件。",
    )
    def browser_wait_for(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """等待页面条件满足。

        Args:
            condition (str): 等待条件类型：selector / url / function / timeout（必填）
            value (str): 条件值（selector 字符串 / URL 模式 / JS 函数体）
            timeout_ms (int): 超时毫秒数（默认 10000）
            state (str): 元素状态，visible / hidden / attached / detached（默认 visible）
        """
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
            "status": "OK",
            "tool": "browser",
            "action": "wait_for",
            "condition": condition,
            "value": value,
            "timeout_ms": timeout_ms,
            "state": state,
            "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
        }

    @skill(
        skill_name="browser_scrape",
        clearance="LOW",
        risk_level=2,
        scope="system",
        reversible=True,
        description="抓取页面内容：提取文本、属性或结构化数据（只读）。",
    )
    def browser_scrape(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """抓取页面内容。

        Args:
            selector (str): 目标元素 CSS 选择器（不填则抓取整页 body 文本）
            extract (str): 提取类型：text / html / attribute / all（默认 text）
            attribute (str): 当 extract=attribute 时指定属性名（如 href / src）
            limit (int): 最多返回元素数量（默认 50）
        """
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
            "status": "OK",
            "tool": "browser",
            "action": "scrape",
            "selector": selector,
            "extract": extract,
            "attribute": attribute,
            "limit": limit,
            "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
        }

    @skill(
        skill_name="browser_close",
        clearance="MEDIUM",
        risk_level=4,
        scope="system",
        reversible=False,
        require_confirm=True,
        description="关闭当前浏览器页面或整个浏览器实例（不可逆，需确认）。",
    )
    def browser_close(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """关闭浏览器页面或实例。

        Args:
            target (str): 关闭目标：page（当前页面）/ browser（整个实例，默认 page）
            force (bool): 是否强制关闭（默认 False）
        """
        target = str((args or {}).get("target") or "page").strip().lower()
        if target not in {"page", "browser"}:
            target = "page"
        force_raw = (args or {}).get("force")
        force = bool(force_raw) if force_raw is not None else False
        return {
            "status": "OK",
            "tool": "browser",
            "action": "close",
            "target": target,
            "force": force,
            "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
        }


def build_browser_skill_registry(provider: BrowserSkillProvider | None = None) -> SkillRegistry:
    """构建包含浏览器自动化 Skills 的 SkillRegistry。

    浏览器 Skill 列表：
    - browser_navigate：导航到 URL（risk=2，只读）
    - browser_click：点击元素（risk=3，写操作）
    - browser_type：输入文本（risk=3，写操作）
    - browser_screenshot：截图（risk=2，只读）
    - browser_execute_script：执行 JS（risk=3，写操作）
    - browser_wait_for：等待条件（risk=2，只读）
    - browser_scrape：抓取内容（risk=2，只读）
    - browser_close：关闭浏览器（risk=4，不可逆，需确认）

    Returns:
        已注册 8 个浏览器 Skills 的 SkillRegistry 实例
    """
    registry = SkillRegistry()
    registry.register_decorated(provider or BrowserSkillProvider())
    return registry
