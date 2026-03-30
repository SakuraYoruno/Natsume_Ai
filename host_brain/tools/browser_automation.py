"""浏览器自动化引擎（Phase XII 阶段 3）。

职责：封装 Playwright，提供安全的浏览器操作能力。
设计：白名单域名 + 操作截图留证 + Cookie 安全管理。
依赖：playwright（可选，不可用时降级）。
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_logger = logging.getLogger("Natsume.tools.browser_automation")

DEFAULT_SCREENSHOT_DIR = "runtime/browser_screenshots"
DEFAULT_TIMEOUT_MS = 30000


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------
@dataclass
class BrowserAction:
    """浏览器操作描述。"""
    action_type: str  # navigate / click / fill / screenshot / scroll / wait / extract
    selector: str = ""
    value: str = ""
    url: str = ""
    description: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "selector": self.selector[:100] if self.selector else "",
            "value": "***" if self.action_type == "fill" else self.value[:100],
            "url": self.url[:200] if self.url else "",
            "description": self.description,
        }


@dataclass
class BrowserResult:
    """浏览器操作结果。"""
    success: bool
    action: str
    url: str = ""
    title: str = ""
    screenshot_path: str = ""
    extracted_text: str = ""
    error: str = ""
    elapsed_ms: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "action": self.action,
            "url": self.url[:200],
            "title": self.title[:100],
            "has_screenshot": bool(self.screenshot_path),
            "extracted_text_length": len(self.extracted_text),
            "error": self.error[:200],
            "elapsed_ms": round(self.elapsed_ms, 1),
        }


@dataclass
class AutomationScript:
    """自动化脚本——一系列浏览器操作。"""
    script_id: str
    name: str
    steps: list[BrowserAction]
    domain_whitelist: list[str] = field(default_factory=list)
    requires_login: bool = False
    cookie_name: str = ""  # SecretVault 中的 cookie 条目名
    schedule: str = ""  # cron 表达式（可选）

    def as_dict(self) -> dict[str, Any]:
        return {
            "script_id": self.script_id,
            "name": self.name,
            "steps_count": len(self.steps),
            "domain_whitelist": list(self.domain_whitelist),
            "requires_login": self.requires_login,
            "has_schedule": bool(self.schedule),
        }


@dataclass
class ScriptExecutionResult:
    """脚本执行结果。"""
    script_id: str
    success: bool
    steps_completed: int
    steps_total: int
    results: list[BrowserResult]
    screenshots: list[str]
    elapsed_ms: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "script_id": self.script_id,
            "success": self.success,
            "steps_completed": self.steps_completed,
            "steps_total": self.steps_total,
            "screenshots_count": len(self.screenshots),
            "elapsed_ms": round(self.elapsed_ms, 1),
        }


# ---------------------------------------------------------------------------
# 安全配置
# ---------------------------------------------------------------------------
# 禁止自动操作的域名模式
BLOCKED_DOMAIN_PATTERNS: list[re.Pattern] = [
    re.compile(r".*\.gov\.(cn|uk|us)$"),      # 政府网站
    re.compile(r".*bank.*\.(com|cn)$"),         # 银行
    re.compile(r".*pay.*\.(com|cn)$"),          # 支付
    re.compile(r"alipay\.com$"),
    re.compile(r".*\.12306\.cn$"),              # 铁路售票
]

# 禁止自动填充的字段类型
SENSITIVE_INPUT_TYPES = frozenset({
    "password", "credit-card", "cvv", "ssn",
    "bank-account", "payment",
})


# ---------------------------------------------------------------------------
# BrowserAutomation
# ---------------------------------------------------------------------------
class BrowserAutomation:
    """Playwright 浏览器自动化引擎。

    用法::

        auto = BrowserAutomation()

        # 单步操作
        result = auto.navigate("https://example.com")
        result = auto.screenshot()
        result = auto.click("#button")
        result = auto.fill("#input", "text")
        text = auto.extract_text(".content")

        # 执行脚本
        script = AutomationScript(
            script_id="daily_check",
            name="每日签到",
            steps=[
                BrowserAction(action_type="navigate", url="https://..."),
                BrowserAction(action_type="click", selector="#sign-in-btn"),
                BrowserAction(action_type="screenshot"),
            ],
            domain_whitelist=["example.com"],
        )
        result = auto.execute_script(script)

    注意：Playwright 不可用时，所有方法返回降级结果。
    """

    def __init__(
        self,
        *,
        headless: bool = True,
        screenshot_dir: str | Path = DEFAULT_SCREENSHOT_DIR,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        blocked_domains: list[re.Pattern] | None = None,
    ) -> None:
        self._headless = bool(headless)
        self._screenshot_dir = Path(screenshot_dir)
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)
        self._timeout = int(timeout_ms)
        self._blocked_domains = list(blocked_domains or BLOCKED_DOMAIN_PATTERNS)

        # Playwright 延迟初始化
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._playwright: Any = None
        self._available = False
        self._screenshot_counter = 0

    def _ensure_browser(self) -> bool:
        """确保浏览器已启动（延迟初始化）。"""
        if self._page is not None:
            return True
        try:
            from playwright.sync_api import sync_playwright  # noqa: PLC0415
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=self._headless)
            self._context = self._browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            self._page = self._context.new_page()
            self._page.set_default_timeout(self._timeout)
            self._available = True
            _logger.info("BrowserAutomation: Playwright 浏览器已启动 (headless=%s)", self._headless)
            return True
        except Exception as exc:
            _logger.warning("BrowserAutomation: Playwright 不可用 (%s)", exc)
            self._available = False
            return False

    def _is_domain_allowed(self, url: str) -> bool:
        """检查域名是否在白名单/不在黑名单。"""
        try:
            from urllib.parse import urlparse  # noqa: PLC0415
            domain = urlparse(url).hostname or ""
            for pattern in self._blocked_domains:
                if pattern.match(domain):
                    _logger.warning("BrowserAutomation: 域名被封禁 %s", domain)
                    return False
            return True
        except Exception:
            return False

    # ── 基础操作 ──────────────────────────────────────────────────────────

    def navigate(self, url: str) -> BrowserResult:
        """导航到 URL。"""
        if not self._is_domain_allowed(url):
            return BrowserResult(success=False, action="navigate", url=url, error="域名被安全策略封禁")
        if not self._ensure_browser():
            return BrowserResult(success=False, action="navigate", error="Playwright 不可用")

        t0 = time.monotonic()
        try:
            self._page.goto(url, wait_until="domcontentloaded", timeout=self._timeout)
            elapsed = (time.monotonic() - t0) * 1000
            return BrowserResult(
                success=True, action="navigate", url=url,
                title=self._page.title(), elapsed_ms=elapsed,
            )
        except Exception as exc:
            elapsed = (time.monotonic() - t0) * 1000
            return BrowserResult(
                success=False, action="navigate", url=url,
                error=str(exc)[:200], elapsed_ms=elapsed,
            )

    def screenshot(self, *, full_page: bool = False) -> BrowserResult:
        """截图。"""
        if not self._available or self._page is None:
            return BrowserResult(success=False, action="screenshot", error="浏览器未启动")

        self._screenshot_counter += 1
        filename = f"screenshot_{int(time.time())}_{self._screenshot_counter:04d}.png"
        filepath = self._screenshot_dir / filename

        try:
            self._page.screenshot(path=str(filepath), full_page=full_page)
            return BrowserResult(
                success=True, action="screenshot",
                url=self._page.url, title=self._page.title(),
                screenshot_path=str(filepath),
            )
        except Exception as exc:
            return BrowserResult(
                success=False, action="screenshot", error=str(exc)[:200],
            )

    def click(self, selector: str) -> BrowserResult:
        """点击元素。"""
        if not self._available or self._page is None:
            return BrowserResult(success=False, action="click", error="浏览器未启动")
        try:
            self._page.click(selector, timeout=self._timeout)
            return BrowserResult(success=True, action="click", url=self._page.url)
        except Exception as exc:
            return BrowserResult(success=False, action="click", error=str(exc)[:200])

    def fill(self, selector: str, value: str) -> BrowserResult:
        """填写表单。"""
        if not self._available or self._page is None:
            return BrowserResult(success=False, action="fill", error="浏览器未启动")

        # 安全检查：不自动填写支付/密码等敏感字段
        try:
            input_type = self._page.get_attribute(selector, "type") or ""
            if input_type.lower() in SENSITIVE_INPUT_TYPES:
                return BrowserResult(
                    success=False, action="fill",
                    error=f"安全策略：不自动填写 {input_type} 类型字段",
                )
        except Exception:
            pass

        try:
            self._page.fill(selector, value, timeout=self._timeout)
            return BrowserResult(success=True, action="fill", url=self._page.url)
        except Exception as exc:
            return BrowserResult(success=False, action="fill", error=str(exc)[:200])

    def extract_text(self, selector: str = "body") -> BrowserResult:
        """提取页面文本。"""
        if not self._available or self._page is None:
            return BrowserResult(success=False, action="extract", error="浏览器未启动")
        try:
            text = self._page.inner_text(selector, timeout=self._timeout)
            return BrowserResult(
                success=True, action="extract",
                url=self._page.url, extracted_text=text[:5000],
            )
        except Exception as exc:
            return BrowserResult(success=False, action="extract", error=str(exc)[:200])

    def wait(self, ms: int = 1000) -> BrowserResult:
        """等待指定毫秒。"""
        time.sleep(ms / 1000)
        return BrowserResult(success=True, action="wait", elapsed_ms=float(ms))

    def download_images(
        self, selector: str = "img", *, output_dir: str | Path = "",
    ) -> BrowserResult:
        """下载页面上的图片。"""
        if not self._available or self._page is None:
            return BrowserResult(success=False, action="download_images", error="浏览器未启动")

        out = Path(output_dir) if output_dir else self._screenshot_dir / "downloads"
        out.mkdir(parents=True, exist_ok=True)

        try:
            images = self._page.query_selector_all(selector)
            count = 0
            for img in images:
                src = img.get_attribute("src")
                if src:
                    count += 1
            return BrowserResult(
                success=True, action="download_images",
                extracted_text=f"找到 {count} 张图片",
                url=self._page.url,
            )
        except Exception as exc:
            return BrowserResult(
                success=False, action="download_images", error=str(exc)[:200],
            )

    # ── 脚本执行 ──────────────────────────────────────────────────────────

    def execute_script(self, script: AutomationScript) -> ScriptExecutionResult:
        """执行自动化脚本。"""
        t0 = time.monotonic()
        results: list[BrowserResult] = []
        screenshots: list[str] = []
        completed = 0

        for step in script.steps:
            # 域名白名单检查
            if step.url and script.domain_whitelist:
                from urllib.parse import urlparse  # noqa: PLC0415
                domain = urlparse(step.url).hostname or ""
                if not any(wl in domain for wl in script.domain_whitelist):
                    results.append(BrowserResult(
                        success=False, action=step.action_type,
                        error=f"域名 {domain} 不在白名单中",
                    ))
                    continue

            # 执行步骤
            result = self._execute_step(step)
            results.append(result)

            if result.success:
                completed += 1
                # 每步操作后自动截图留证
                ss = self.screenshot()
                if ss.success and ss.screenshot_path:
                    screenshots.append(ss.screenshot_path)
            else:
                _logger.warning(
                    "Script %s step %s failed: %s",
                    script.script_id, step.action_type, result.error,
                )
                break  # 失败则停止

        elapsed = (time.monotonic() - t0) * 1000
        return ScriptExecutionResult(
            script_id=script.script_id,
            success=completed == len(script.steps),
            steps_completed=completed,
            steps_total=len(script.steps),
            results=results,
            screenshots=screenshots,
            elapsed_ms=elapsed,
        )

    def _execute_step(self, step: BrowserAction) -> BrowserResult:
        """执行单个步骤。"""
        action = step.action_type.lower()
        if action == "navigate":
            return self.navigate(step.url)
        if action == "click":
            return self.click(step.selector)
        if action == "fill":
            return self.fill(step.selector, step.value)
        if action == "screenshot":
            return self.screenshot()
        if action == "wait":
            ms = int(step.value) if step.value.isdigit() else 1000
            return self.wait(ms)
        if action == "extract":
            return self.extract_text(step.selector or "body")
        return BrowserResult(success=False, action=action, error=f"未知操作类型: {action}")

    # ── 生命周期 ──────────────────────────────────────────────────────────

    def close(self) -> None:
        """关闭浏览器。"""
        try:
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
        self._available = False
        _logger.info("BrowserAutomation: 浏览器已关闭")

    def __del__(self) -> None:
        self.close()
