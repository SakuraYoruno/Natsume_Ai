"""浏览器自动化 Skill 执行层（Playwright 适配）。

将 browser_registry.py 的归一化 payload 转化为真正的 Playwright 操作。

依赖：playwright（可选，不安装时所有操作返回 DEGRADED）
安装：pip install playwright && playwright install chromium

设计：
  - 使用单例 BrowserSession 管理 Playwright 实例（同步模式）
  - 每次调用自动复用已有 page，避免重复启动浏览器
  - 所有操作均有超时保护
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from host_brain.core.ids import new_event_id, now_ms

logger = logging.getLogger(__name__)

STATUS_EXECUTED = "EXECUTED"
STATUS_REJECTED = "REJECTED"
STATUS_DEGRADED = "DEGRADED"

BROWSER_EXEC_ERROR = "BROWSER_EXEC_ERROR"
BROWSER_UNAVAILABLE = "BROWSER_UNAVAILABLE"
BROWSER_TIMEOUT = "BROWSER_TIMEOUT"


def _reject(
    *,
    event_id: str,
    reason_code: str,
    detail: str,
    ts_ms: int,
    request_id: str | None = None,
) -> dict[str, Any]:
    return {
        "status": STATUS_REJECTED,
        "event_id": event_id,
        "reason_code": reason_code,
        "detail": detail,
        "manifest_ref": None,
        "request_id": request_id,
        "ts_ms": ts_ms,
    }


def _degraded(
    *,
    event_id: str,
    reason_code: str,
    detail: str,
    ts_ms: int,
    request_id: str | None = None,
) -> dict[str, Any]:
    return {
        "status": STATUS_DEGRADED,
        "event_id": event_id,
        "reason_code": reason_code,
        "detail": detail,
        "manifest_ref": None,
        "request_id": request_id,
        "ts_ms": ts_ms,
    }


# ---------------------------------------------------------------------------
# BrowserSession 单例（Playwright 同步 API）
# ---------------------------------------------------------------------------

class BrowserSession:
    """Playwright 浏览器会话单例（同步模式）。"""

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._page = None

    def _ensure_started(self) -> None:
        """确保 Playwright 和浏览器已启动。"""
        from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
        if self._playwright is None:
            self._playwright = sync_playwright().start()
        if self._browser is None or not self._browser.is_connected():
            self._browser = self._playwright.chromium.launch(headless=False)
        if self._page is None or self._page.is_closed():
            self._page = self._browser.new_page()

    @property
    def page(self):
        self._ensure_started()
        return self._page

    def close(self) -> None:
        """关闭浏览器会话。"""
        try:
            if self._page and not self._page.is_closed():
                self._page.close()
        except Exception:
            pass
        try:
            if self._browser and self._browser.is_connected():
                self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        self._page = None
        self._browser = None
        self._playwright = None


_session = BrowserSession()


def _get_session() -> BrowserSession:
    return _session


# ---------------------------------------------------------------------------
# 各操作执行函数
# ---------------------------------------------------------------------------

def execute_browser_navigate(payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_ms()
    event_id = new_event_id()
    url = payload.get("url") or ""
    wait_until = payload.get("wait_until") or "load"
    timeout_ms = int(payload.get("timeout_ms") or 30000)
    request_id = payload.get("session_id") or payload.get("request_id")

    if not url:
        return _reject(event_id=event_id, reason_code="BROWSER_URL_MISSING",
                       detail="url is required", ts_ms=ts, request_id=request_id)
    try:
        page = _get_session().page
        page.goto(url, wait_until=wait_until, timeout=timeout_ms)
        return {
            "status": STATUS_EXECUTED,
            "event_id": event_id,
            "manifest_ref": payload.get("manifest_ref") or f"browser_navigate::{url[:80]}",
            "url": page.url,
            "title": page.title(),
            "request_id": request_id,
            "ts_ms": ts,
        }
    except ImportError:
        return _degraded(event_id=event_id, reason_code=BROWSER_UNAVAILABLE,
                         detail="playwright not installed", ts_ms=ts, request_id=request_id)
    except Exception as exc:
        logger.error("browser_navigate failed: %s", exc)
        return _reject(event_id=event_id, reason_code=BROWSER_EXEC_ERROR,
                       detail=str(exc), ts_ms=ts, request_id=request_id)


def execute_browser_click(payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_ms()
    event_id = new_event_id()
    selector = payload.get("selector") or ""
    button = payload.get("button") or "left"
    timeout_ms = int(payload.get("timeout_ms") or 5000)
    request_id = payload.get("request_id")

    if not selector:
        return _reject(event_id=event_id, reason_code="BROWSER_SELECTOR_MISSING",
                       detail="selector is required", ts_ms=ts, request_id=request_id)
    try:
        page = _get_session().page
        page.click(selector, button=button, timeout=timeout_ms)
        return {
            "status": STATUS_EXECUTED,
            "event_id": event_id,
            "manifest_ref": payload.get("manifest_ref") or f"browser_click::{selector[:60]}",
            "selector": selector,
            "request_id": request_id,
            "ts_ms": ts,
        }
    except ImportError:
        return _degraded(event_id=event_id, reason_code=BROWSER_UNAVAILABLE,
                         detail="playwright not installed", ts_ms=ts, request_id=request_id)
    except Exception as exc:
        logger.error("browser_click failed: %s", exc)
        return _reject(event_id=event_id, reason_code=BROWSER_EXEC_ERROR,
                       detail=str(exc), ts_ms=ts, request_id=request_id)


def execute_browser_type(payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_ms()
    event_id = new_event_id()
    selector = payload.get("selector") or ""
    text = payload.get("text") or ""
    delay_ms = int(payload.get("delay_ms") or 50)
    clear_first = bool(payload.get("clear_first", True))
    request_id = payload.get("request_id")

    if not selector:
        return _reject(event_id=event_id, reason_code="BROWSER_SELECTOR_MISSING",
                       detail="selector is required", ts_ms=ts, request_id=request_id)
    try:
        page = _get_session().page
        if clear_first:
            page.fill(selector, "")
        page.type(selector, text, delay=delay_ms)
        return {
            "status": STATUS_EXECUTED,
            "event_id": event_id,
            "manifest_ref": payload.get("manifest_ref") or f"browser_type::{selector[:60]}",
            "selector": selector,
            "text_len": len(text),
            "request_id": request_id,
            "ts_ms": ts,
        }
    except ImportError:
        return _degraded(event_id=event_id, reason_code=BROWSER_UNAVAILABLE,
                         detail="playwright not installed", ts_ms=ts, request_id=request_id)
    except Exception as exc:
        logger.error("browser_type failed: %s", exc)
        return _reject(event_id=event_id, reason_code=BROWSER_EXEC_ERROR,
                       detail=str(exc), ts_ms=ts, request_id=request_id)


def execute_browser_screenshot(payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_ms()
    event_id = new_event_id()
    save_path = payload.get("save_path") or None
    selector = payload.get("selector") or None
    full_page = bool(payload.get("full_page", False))
    return_base64 = bool(payload.get("return_base64", save_path is None))
    request_id = payload.get("session_id") or payload.get("request_id")

    try:
        page = _get_session().page
        kwargs: dict[str, Any] = {"full_page": full_page}
        if save_path:
            kwargs["path"] = save_path

        if selector:
            element = page.query_selector(selector)
            if element is None:
                return _reject(event_id=event_id, reason_code="BROWSER_ELEMENT_NOT_FOUND",
                               detail=f"element not found: {selector}", ts_ms=ts, request_id=request_id)
            screenshot_bytes = element.screenshot(**kwargs)
        else:
            screenshot_bytes = page.screenshot(**kwargs)

        result: dict[str, Any] = {
            "status": STATUS_EXECUTED,
            "event_id": event_id,
            "manifest_ref": payload.get("manifest_ref") or "browser_screenshot",
            "save_path": save_path,
            "request_id": request_id,
            "ts_ms": ts,
        }
        if return_base64 and screenshot_bytes:
            result["image_b64"] = base64.b64encode(screenshot_bytes).decode("ascii")
            result["image_mime"] = "image/png"
        return result
    except ImportError:
        return _degraded(event_id=event_id, reason_code=BROWSER_UNAVAILABLE,
                         detail="playwright not installed", ts_ms=ts, request_id=request_id)
    except Exception as exc:
        logger.error("browser_screenshot failed: %s", exc)
        return _reject(event_id=event_id, reason_code=BROWSER_EXEC_ERROR,
                       detail=str(exc), ts_ms=ts, request_id=request_id)


def execute_browser_execute_script(payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_ms()
    event_id = new_event_id()
    script = payload.get("script") or ""
    script_args = payload.get("script_args") or []
    request_id = payload.get("request_id")

    if not script:
        return _reject(event_id=event_id, reason_code="BROWSER_SCRIPT_MISSING",
                       detail="script is required", ts_ms=ts, request_id=request_id)
    try:
        page = _get_session().page
        result_value = page.evaluate(script, *script_args)
        return {
            "status": STATUS_EXECUTED,
            "event_id": event_id,
            "manifest_ref": payload.get("manifest_ref") or "browser_execute_script",
            "result": result_value,
            "request_id": request_id,
            "ts_ms": ts,
        }
    except ImportError:
        return _degraded(event_id=event_id, reason_code=BROWSER_UNAVAILABLE,
                         detail="playwright not installed", ts_ms=ts, request_id=request_id)
    except Exception as exc:
        logger.error("browser_execute_script failed: %s", exc)
        return _reject(event_id=event_id, reason_code=BROWSER_EXEC_ERROR,
                       detail=str(exc), ts_ms=ts, request_id=request_id)


def execute_browser_wait_for(payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_ms()
    event_id = new_event_id()
    condition = payload.get("condition") or "selector"
    value = payload.get("value") or ""
    timeout_ms = int(payload.get("timeout_ms") or 10000)
    state = payload.get("state") or "visible"
    request_id = payload.get("session_id") or payload.get("request_id")

    try:
        page = _get_session().page
        if condition == "selector":
            page.wait_for_selector(value, state=state, timeout=timeout_ms)
        elif condition == "url":
            page.wait_for_url(value, timeout=timeout_ms)
        elif condition == "function":
            page.wait_for_function(value, timeout=timeout_ms)
        elif condition == "timeout":
            page.wait_for_timeout(min(timeout_ms, 30000))

        return {
            "status": STATUS_EXECUTED,
            "event_id": event_id,
            "manifest_ref": payload.get("manifest_ref") or f"browser_wait_for::{condition}",
            "condition": condition,
            "value": value,
            "request_id": request_id,
            "ts_ms": ts,
        }
    except ImportError:
        return _degraded(event_id=event_id, reason_code=BROWSER_UNAVAILABLE,
                         detail="playwright not installed", ts_ms=ts, request_id=request_id)
    except Exception as exc:
        logger.error("browser_wait_for failed: %s", exc)
        return _reject(event_id=event_id, reason_code=BROWSER_EXEC_ERROR,
                       detail=str(exc), ts_ms=ts, request_id=request_id)


def execute_browser_scrape(payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_ms()
    event_id = new_event_id()
    selector = payload.get("selector") or "body"
    extract = payload.get("extract") or "text"
    attribute = payload.get("attribute") or None
    limit = int(payload.get("limit") or 50)
    request_id = payload.get("session_id") or payload.get("request_id")

    try:
        page = _get_session().page
        elements = page.query_selector_all(selector)[:limit]

        results = []
        for el in elements:
            if extract == "text":
                results.append(el.inner_text())
            elif extract == "html":
                results.append(el.inner_html())
            elif extract == "attribute" and attribute:
                results.append(el.get_attribute(attribute))
            elif extract == "all":
                results.append({
                    "text": el.inner_text(),
                    "html": el.inner_html(),
                })

        return {
            "status": STATUS_EXECUTED,
            "event_id": event_id,
            "manifest_ref": payload.get("manifest_ref") or f"browser_scrape::{selector[:60]}",
            "selector": selector,
            "extract": extract,
            "results": results,
            "count": len(results),
            "request_id": request_id,
            "ts_ms": ts,
        }
    except ImportError:
        return _degraded(event_id=event_id, reason_code=BROWSER_UNAVAILABLE,
                         detail="playwright not installed", ts_ms=ts, request_id=request_id)
    except Exception as exc:
        logger.error("browser_scrape failed: %s", exc)
        return _reject(event_id=event_id, reason_code=BROWSER_EXEC_ERROR,
                       detail=str(exc), ts_ms=ts, request_id=request_id)


def execute_browser_close(payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_ms()
    event_id = new_event_id()
    target = payload.get("target") or "page"
    request_id = payload.get("request_id")

    try:
        session = _get_session()
        if target == "browser":
            session.close()
        else:
            # 只关闭当前 page，保留 browser 实例
            if session._page and not session._page.is_closed():
                session._page.close()
                session._page = None

        return {
            "status": STATUS_EXECUTED,
            "event_id": event_id,
            "manifest_ref": payload.get("manifest_ref") or f"browser_close::{target}",
            "target": target,
            "request_id": request_id,
            "ts_ms": ts,
        }
    except ImportError:
        return _degraded(event_id=event_id, reason_code=BROWSER_UNAVAILABLE,
                         detail="playwright not installed", ts_ms=ts, request_id=request_id)
    except Exception as exc:
        logger.error("browser_close failed: %s", exc)
        return _reject(event_id=event_id, reason_code=BROWSER_EXEC_ERROR,
                       detail=str(exc), ts_ms=ts, request_id=request_id)


# 统一分发入口
_ACTION_MAP = {
    "navigate": execute_browser_navigate,
    "click": execute_browser_click,
    "type": execute_browser_type,
    "screenshot": execute_browser_screenshot,
    "execute_script": execute_browser_execute_script,
    "wait_for": execute_browser_wait_for,
    "scrape": execute_browser_scrape,
    "close": execute_browser_close,
}


def execute_browser_skill(payload: dict[str, Any]) -> dict[str, Any]:
    """根据 payload.action 分发到对应的浏览器执行函数。"""
    action = str(payload.get("action") or "").strip().lower()
    handler = _ACTION_MAP.get(action)
    if handler is None:
        return {
            "status": STATUS_REJECTED,
            "event_id": new_event_id(),
            "reason_code": "BROWSER_UNKNOWN_ACTION",
            "detail": f"unknown browser action: {action!r}",
            "manifest_ref": None,
            "ts_ms": now_ms(),
        }
    return handler(payload)
