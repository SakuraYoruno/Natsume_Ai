"""浏览器自动化 Skill 执行层（agent-browser 优先，Playwright 回退）。

将 browser_registry.py 的归一化 payload 转化为真实浏览器操作。

后端选择：
  - HOST_BROWSER_BACKEND=auto：优先 agent-browser，不可用时回退 Playwright
  - HOST_BROWSER_BACKEND=agent-browser：强制使用 agent-browser CLI
  - HOST_BROWSER_BACKEND=playwright：强制使用 Playwright
"""

from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
from typing import Any

from host_brain.core.ids import new_event_id, now_ms

logger = logging.getLogger(__name__)

STATUS_EXECUTED = "EXECUTED"
STATUS_REJECTED = "REJECTED"
STATUS_DEGRADED = "DEGRADED"

BROWSER_EXEC_ERROR = "BROWSER_EXEC_ERROR"
BROWSER_UNAVAILABLE = "BROWSER_UNAVAILABLE"
BROWSER_TIMEOUT = "BROWSER_TIMEOUT"
BROWSER_BACKEND_UNSUPPORTED = "BROWSER_BACKEND_UNSUPPORTED"

BACKEND_AUTO = "auto"
BACKEND_AGENT_BROWSER = "agent-browser"
BACKEND_PLAYWRIGHT = "playwright"
BACKEND_OS_SHELL = "os-shell"
BACKEND_DESKTOP_FALLBACK = "desktop-fallback"

AGENT_BROWSER_DEFAULT_COMMAND = "agent-browser"
AGENT_BROWSER_DEFAULT_SESSION = "natsume"


class AgentBrowserCommandError(RuntimeError):
    """agent-browser CLI 执行失败。"""


class AgentBrowserUnsupported(RuntimeError):
    """当前 payload 超出 agent-browser 适配层能力。"""


def _payload_request_id(payload: dict[str, Any]) -> str | None:
    request_id = str(payload.get("request_id") or "").strip()
    if request_id:
        return request_id
    session_id = str(payload.get("session_id") or "").strip()
    return session_id or None


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


def _backend_preference() -> str:
    raw = str(os.environ.get("HOST_BROWSER_BACKEND") or BACKEND_AUTO).strip().lower()
    if raw in {BACKEND_AUTO, BACKEND_AGENT_BROWSER, BACKEND_PLAYWRIGHT}:
        return raw
    return BACKEND_AUTO


def _agent_browser_command_parts() -> list[str]:
    raw = str(os.environ.get("HOST_BROWSER_AGENT_COMMAND") or AGENT_BROWSER_DEFAULT_COMMAND).strip()
    if not raw:
        raw = AGENT_BROWSER_DEFAULT_COMMAND
    return shlex.split(raw, posix=(os.name != "nt"))


def _agent_browser_available() -> bool:
    command = _agent_browser_command_parts()
    return bool(command) and shutil.which(command[0]) is not None


def _agent_browser_install_hint() -> str:
    return "install via `npm i -g agent-browser && agent-browser install`, or set `HOST_BROWSER_BACKEND=playwright`"


def _agent_browser_session_name(payload: dict[str, Any]) -> str:
    session_id = str(payload.get("session_id") or "").strip()
    if session_id:
        return session_id
    fallback = str(os.environ.get("HOST_BROWSER_AGENT_SESSION") or AGENT_BROWSER_DEFAULT_SESSION).strip()
    return fallback or AGENT_BROWSER_DEFAULT_SESSION


def _agent_browser_timeout_seconds(timeout_ms: int | None) -> float:
    if timeout_ms is None:
        return 35.0
    return max(5.0, (max(1, timeout_ms) / 1000.0) + 5.0)


def _build_agent_browser_command(payload: dict[str, Any], *args: str) -> list[str]:
    return [
        *_agent_browser_command_parts(),
        "--session",
        _agent_browser_session_name(payload),
        *args,
    ]


def _run_agent_browser(
    payload: dict[str, Any],
    *args: str,
    timeout_ms: int | None = None,
    stdin_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            _build_agent_browser_command(payload, *args),
            input=stdin_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_agent_browser_timeout_seconds(timeout_ms),
            check=False,
        )
    except FileNotFoundError as exc:
        raise FileNotFoundError(_agent_browser_install_hint()) from exc
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"agent-browser timed out after {timeout_ms or 'default'} ms") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "agent-browser command failed").strip()
        raise AgentBrowserCommandError(detail)
    return completed


def _clean_cli_text(value: str) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        try:
            return str(json.loads(text))
        except json.JSONDecodeError:
            return text[1:-1]
    return text


def _parse_jsonish_output(value: str) -> Any | None:
    text = str(value or "").strip()
    if not text:
        return None
    candidates = [text]
    if len(text) >= 2 and text[0] == text[-1] == '"':
        try:
            inner = json.loads(text)
        except json.JSONDecodeError:
            inner = None
        if isinstance(inner, str):
            candidates.append(inner)
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except (TypeError, json.JSONDecodeError):
            continue
    return None


def _run_agent_browser_eval(payload: dict[str, Any], script: str, timeout_ms: int | None = None) -> str:
    completed = _run_agent_browser(payload, "eval", "--stdin", timeout_ms=timeout_ms, stdin_text=script)
    return completed.stdout or ""


def _run_agent_browser_eval_json(payload: dict[str, Any], script: str, timeout_ms: int | None = None) -> Any:
    raw = _run_agent_browser_eval(payload, script, timeout_ms=timeout_ms)
    parsed = _parse_jsonish_output(raw)
    if parsed is None:
        raise AgentBrowserCommandError(f"unexpected eval output: {_clean_cli_text(raw)}")
    return parsed


def _selector_lookup_expression(selector: str, selector_type: str) -> str:
    if selector_type == "xpath":
        selector_json = json.dumps(selector)
        return (
            "(() => {"
            f"const __selector = {selector_json};"
            "const __result = document.evaluate(__selector, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);"
            "return __result.singleNodeValue;"
            "})()"
        )
    return f"document.querySelector({json.dumps(selector)})"


def _dispatch_input_events_script(element_ref: str) -> str:
    return (
        f"{element_ref}.dispatchEvent(new Event('input', {{ bubbles: true }}));"
        f"{element_ref}.dispatchEvent(new Event('change', {{ bubbles: true }}));"
    )


def _open_url_via_system_shell(url: str) -> None:
    target = str(url or "").strip()
    if not target:
        raise ValueError("url is required")
    if hasattr(os, "startfile"):
        os.startfile(target)  # type: ignore[attr-defined]
        return
    completed = subprocess.run(["python", "-m", "webbrowser", "-t", target], capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "webbrowser open failed").strip()
        raise RuntimeError(detail)


def _os_shell_execute_browser_navigate(payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_ms()
    event_id = new_event_id()
    url = str(payload.get("url") or "").strip()
    request_id = _payload_request_id(payload)
    if not url:
        return _reject(event_id=event_id, reason_code="BROWSER_URL_MISSING", detail="url is required", ts_ms=ts, request_id=request_id)
    try:
        _open_url_via_system_shell(url)
        return {
            "status": STATUS_EXECUTED,
            "event_id": event_id,
            "manifest_ref": payload.get("manifest_ref") or f"browser_navigate::{url[:80]}",
            "url": url,
            "title": None,
            "request_id": request_id,
            "backend": BACKEND_OS_SHELL,
            "detail": "opened via system default browser",
            "ts_ms": ts,
        }
    except Exception as exc:
        return _degraded(event_id=event_id, reason_code=BROWSER_UNAVAILABLE, detail=str(exc), ts_ms=ts, request_id=request_id)


def _desktop_fallback_browser_screenshot(payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_ms()
    event_id = new_event_id()
    request_id = _payload_request_id(payload)
    selector = str(payload.get("selector") or "").strip()
    if selector:
        return _degraded(
            event_id=event_id,
            reason_code=BROWSER_UNAVAILABLE,
            detail="browser automation unavailable; element screenshot requires browser backend",
            ts_ms=ts,
            request_id=request_id,
        )
    try:
        from host_brain.skills.impl.desktop_executor import execute_desktop_skill  # noqa: PLC0415
        desktop_result = execute_desktop_skill(
            {
                "action": "screenshot",
                "save_path": payload.get("save_path"),
                "monitor": 1,
                "manifest_ref": payload.get("manifest_ref") or "browser_screenshot::desktop_fallback",
            },
            payload.get("_context") if isinstance(payload.get("_context"), dict) else None,
        )
        status = str(desktop_result.get("status") or "").strip().upper()
        if status != "OK":
            return _degraded(
                event_id=event_id,
                reason_code=BROWSER_UNAVAILABLE,
                detail=str(desktop_result.get("error") or desktop_result.get("detail") or "desktop screenshot unavailable"),
                ts_ms=ts,
                request_id=request_id,
            )
        detail = dict(desktop_result.get("detail") or {}) if isinstance(desktop_result.get("detail"), dict) else {}
        return {
            "status": STATUS_EXECUTED,
            "event_id": event_id,
            "manifest_ref": payload.get("manifest_ref") or "browser_screenshot::desktop_fallback",
            "request_id": request_id,
            "backend": BACKEND_DESKTOP_FALLBACK,
            "save_path": detail.get("save_path"),
            "image_b64": detail.get("image_b64"),
            "image_mime": detail.get("image_mime"),
            "detail": "browser backend unavailable; captured desktop screenshot instead",
            "ts_ms": ts,
        }
    except Exception as exc:
        return _degraded(event_id=event_id, reason_code=BROWSER_UNAVAILABLE, detail=str(exc), ts_ms=ts, request_id=request_id)


def _dispatch_browser_backend(payload: dict[str, Any], *, agent_handler, playwright_handler) -> dict[str, Any]:
    preference = _backend_preference()
    if preference == BACKEND_PLAYWRIGHT:
        return playwright_handler(payload)

    agent_available = _agent_browser_available()
    if preference == BACKEND_AGENT_BROWSER or (preference == BACKEND_AUTO and agent_available):
        try:
            return agent_handler(payload)
        except FileNotFoundError:
            if preference == BACKEND_AUTO:
                result = playwright_handler(payload)
                if str(result.get("reason_code") or "") == BROWSER_UNAVAILABLE and str(payload.get("action") or "").strip().lower() == "navigate":
                    return _os_shell_execute_browser_navigate(payload)
                if str(result.get("reason_code") or "") == BROWSER_UNAVAILABLE and str(payload.get("action") or "").strip().lower() == "screenshot":
                    return _desktop_fallback_browser_screenshot(payload)
                return result
            return _degraded(
                event_id=new_event_id(),
                reason_code=BROWSER_UNAVAILABLE,
                detail=f"agent-browser CLI not installed; {_agent_browser_install_hint()}",
                ts_ms=now_ms(),
                request_id=_payload_request_id(payload),
            )
        except TimeoutError as exc:
            return _reject(
                event_id=new_event_id(),
                reason_code=BROWSER_TIMEOUT,
                detail=str(exc),
                ts_ms=now_ms(),
                request_id=_payload_request_id(payload),
            )
        except AgentBrowserUnsupported as exc:
            if preference == BACKEND_AUTO:
                result = playwright_handler(payload)
                if str(result.get("reason_code") or "") == BROWSER_UNAVAILABLE and str(payload.get("action") or "").strip().lower() == "navigate":
                    return _os_shell_execute_browser_navigate(payload)
                if str(result.get("reason_code") or "") == BROWSER_UNAVAILABLE and str(payload.get("action") or "").strip().lower() == "screenshot":
                    return _desktop_fallback_browser_screenshot(payload)
                return result
            return _reject(
                event_id=new_event_id(),
                reason_code=BROWSER_BACKEND_UNSUPPORTED,
                detail=str(exc),
                ts_ms=now_ms(),
                request_id=_payload_request_id(payload),
            )
        except AgentBrowserCommandError as exc:
            return _reject(
                event_id=new_event_id(),
                reason_code=BROWSER_EXEC_ERROR,
                detail=str(exc),
                ts_ms=now_ms(),
                request_id=_payload_request_id(payload),
            )

    result = playwright_handler(payload)
    if str(result.get("reason_code") or "") == BROWSER_UNAVAILABLE and str(payload.get("action") or "").strip().lower() == "navigate":
        return _os_shell_execute_browser_navigate(payload)
    if str(result.get("reason_code") or "") == BROWSER_UNAVAILABLE and str(payload.get("action") or "").strip().lower() == "screenshot":
        return _desktop_fallback_browser_screenshot(payload)
    return result


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

def _playwright_execute_browser_navigate(payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_ms()
    event_id = new_event_id()
    url = payload.get("url") or ""
    wait_until = payload.get("wait_until") or "load"
    timeout_ms = int(payload.get("timeout_ms") or 30000)
    request_id = _payload_request_id(payload)

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
            "backend": BACKEND_PLAYWRIGHT,
            "ts_ms": ts,
        }
    except ImportError:
        return _degraded(event_id=event_id, reason_code=BROWSER_UNAVAILABLE,
                         detail="playwright not installed", ts_ms=ts, request_id=request_id)
    except Exception as exc:
        logger.error("browser_navigate failed: %s", exc)
        return _reject(event_id=event_id, reason_code=BROWSER_EXEC_ERROR,
                       detail=str(exc), ts_ms=ts, request_id=request_id)


def _playwright_execute_browser_click(payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_ms()
    event_id = new_event_id()
    selector = payload.get("selector") or ""
    button = payload.get("button") or "left"
    timeout_ms = int(payload.get("timeout_ms") or 5000)
    request_id = _payload_request_id(payload)

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
            "backend": BACKEND_PLAYWRIGHT,
            "ts_ms": ts,
        }
    except ImportError:
        return _degraded(event_id=event_id, reason_code=BROWSER_UNAVAILABLE,
                         detail="playwright not installed", ts_ms=ts, request_id=request_id)
    except Exception as exc:
        logger.error("browser_click failed: %s", exc)
        return _reject(event_id=event_id, reason_code=BROWSER_EXEC_ERROR,
                       detail=str(exc), ts_ms=ts, request_id=request_id)


def _playwright_execute_browser_type(payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_ms()
    event_id = new_event_id()
    selector = payload.get("selector") or ""
    text = payload.get("text") or ""
    delay_ms = int(payload.get("delay_ms") or 50)
    clear_first = bool(payload.get("clear_first", True))
    request_id = _payload_request_id(payload)

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
            "backend": BACKEND_PLAYWRIGHT,
            "ts_ms": ts,
        }
    except ImportError:
        return _degraded(event_id=event_id, reason_code=BROWSER_UNAVAILABLE,
                         detail="playwright not installed", ts_ms=ts, request_id=request_id)
    except Exception as exc:
        logger.error("browser_type failed: %s", exc)
        return _reject(event_id=event_id, reason_code=BROWSER_EXEC_ERROR,
                       detail=str(exc), ts_ms=ts, request_id=request_id)


def _playwright_execute_browser_screenshot(payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_ms()
    event_id = new_event_id()
    save_path = payload.get("save_path") or None
    selector = payload.get("selector") or None
    full_page = bool(payload.get("full_page", False))
    return_base64 = bool(payload.get("return_base64", save_path is None))
    request_id = _payload_request_id(payload)

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
            "backend": BACKEND_PLAYWRIGHT,
            "ts_ms": ts,
        }
        if return_base64 and screenshot_bytes:
            image_b64 = base64.b64encode(screenshot_bytes).decode("ascii")
            result["image_b64"] = image_b64
            result["image_base64"] = image_b64
            result["image_mime"] = "image/png"
        return result
    except ImportError:
        return _degraded(event_id=event_id, reason_code=BROWSER_UNAVAILABLE,
                         detail="playwright not installed", ts_ms=ts, request_id=request_id)
    except Exception as exc:
        logger.error("browser_screenshot failed: %s", exc)
        return _reject(event_id=event_id, reason_code=BROWSER_EXEC_ERROR,
                       detail=str(exc), ts_ms=ts, request_id=request_id)


def _playwright_execute_browser_execute_script(payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_ms()
    event_id = new_event_id()
    script = payload.get("script") or ""
    script_args = payload.get("script_args") or []
    request_id = _payload_request_id(payload)

    if not script:
        return _reject(event_id=event_id, reason_code="BROWSER_SCRIPT_MISSING",
                       detail="script is required", ts_ms=ts, request_id=request_id)
    try:
        page = _get_session().page
        if script_args:
            result_value = page.evaluate(script, script_args)
        else:
            result_value = page.evaluate(script)
        return {
            "status": STATUS_EXECUTED,
            "event_id": event_id,
            "manifest_ref": payload.get("manifest_ref") or "browser_execute_script",
            "result": result_value,
            "request_id": request_id,
            "backend": BACKEND_PLAYWRIGHT,
            "ts_ms": ts,
        }
    except ImportError:
        return _degraded(event_id=event_id, reason_code=BROWSER_UNAVAILABLE,
                         detail="playwright not installed", ts_ms=ts, request_id=request_id)
    except Exception as exc:
        logger.error("browser_execute_script failed: %s", exc)
        return _reject(event_id=event_id, reason_code=BROWSER_EXEC_ERROR,
                       detail=str(exc), ts_ms=ts, request_id=request_id)


def _playwright_execute_browser_wait_for(payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_ms()
    event_id = new_event_id()
    condition = payload.get("condition") or "selector"
    value = payload.get("value") or ""
    timeout_ms = int(payload.get("timeout_ms") or 10000)
    state = payload.get("state") or "visible"
    request_id = _payload_request_id(payload)

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
            "backend": BACKEND_PLAYWRIGHT,
            "ts_ms": ts,
        }
    except ImportError:
        return _degraded(event_id=event_id, reason_code=BROWSER_UNAVAILABLE,
                         detail="playwright not installed", ts_ms=ts, request_id=request_id)
    except Exception as exc:
        logger.error("browser_wait_for failed: %s", exc)
        return _reject(event_id=event_id, reason_code=BROWSER_EXEC_ERROR,
                       detail=str(exc), ts_ms=ts, request_id=request_id)


def _playwright_execute_browser_scrape(payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_ms()
    event_id = new_event_id()
    selector = payload.get("selector") or "body"
    extract = payload.get("extract") or "text"
    attribute = payload.get("attribute") or None
    limit = int(payload.get("limit") or 50)
    request_id = _payload_request_id(payload)

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
            "backend": BACKEND_PLAYWRIGHT,
            "ts_ms": ts,
        }
    except ImportError:
        return _degraded(event_id=event_id, reason_code=BROWSER_UNAVAILABLE,
                         detail="playwright not installed", ts_ms=ts, request_id=request_id)
    except Exception as exc:
        logger.error("browser_scrape failed: %s", exc)
        return _reject(event_id=event_id, reason_code=BROWSER_EXEC_ERROR,
                       detail=str(exc), ts_ms=ts, request_id=request_id)


def _playwright_execute_browser_close(payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_ms()
    event_id = new_event_id()
    target = payload.get("target") or "page"
    request_id = _payload_request_id(payload)

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
            "backend": BACKEND_PLAYWRIGHT,
            "ts_ms": ts,
        }
    except ImportError:
        return _degraded(event_id=event_id, reason_code=BROWSER_UNAVAILABLE,
                         detail="playwright not installed", ts_ms=ts, request_id=request_id)
    except Exception as exc:
        logger.error("browser_close failed: %s", exc)
        return _reject(event_id=event_id, reason_code=BROWSER_EXEC_ERROR,
                       detail=str(exc), ts_ms=ts, request_id=request_id)


def _agent_execute_browser_navigate(payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_ms()
    event_id = new_event_id()
    url = str(payload.get("url") or "").strip()
    wait_until = str(payload.get("wait_until") or "load").strip().lower()
    timeout_ms = int(payload.get("timeout_ms") or 30000)
    request_id = _payload_request_id(payload)

    if not url:
        return _reject(event_id=event_id, reason_code="BROWSER_URL_MISSING",
                       detail="url is required", ts_ms=ts, request_id=request_id)

    _run_agent_browser(payload, "open", url, timeout_ms=timeout_ms)
    if wait_until == "networkidle":
        _run_agent_browser(payload, "wait", "--load", "networkidle", timeout_ms=timeout_ms)
    elif wait_until == "domcontentloaded":
        _run_agent_browser(
            payload,
            "wait",
            "--fn",
            "document.readyState === 'interactive' || document.readyState === 'complete'",
            timeout_ms=timeout_ms,
        )
    else:
        _run_agent_browser(payload, "wait", "--fn", "document.readyState === 'complete'", timeout_ms=timeout_ms)

    current_url = _clean_cli_text(_run_agent_browser(payload, "get", "url", timeout_ms=timeout_ms).stdout)
    title = _clean_cli_text(_run_agent_browser(payload, "get", "title", timeout_ms=timeout_ms).stdout)
    return {
        "status": STATUS_EXECUTED,
        "event_id": event_id,
        "manifest_ref": payload.get("manifest_ref") or f"browser_navigate::{url[:80]}",
        "url": current_url or url,
        "title": title,
        "request_id": request_id,
        "backend": BACKEND_AGENT_BROWSER,
        "ts_ms": ts,
    }


def _agent_execute_browser_click(payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_ms()
    event_id = new_event_id()
    selector = str(payload.get("selector") or "").strip()
    selector_type = str(payload.get("selector_type") or "css").strip().lower()
    button = str(payload.get("button") or "left").strip().lower()
    timeout_ms = int(payload.get("timeout_ms") or 5000)
    request_id = _payload_request_id(payload)

    if not selector:
        return _reject(event_id=event_id, reason_code="BROWSER_SELECTOR_MISSING",
                       detail="selector is required", ts_ms=ts, request_id=request_id)
    if button != "left":
        raise AgentBrowserUnsupported("agent-browser adapter currently supports left-click only")

    lookup_expression = _selector_lookup_expression(selector, selector_type)
    script = f"""
(() => {{
  const element = {lookup_expression};
  if (!element) {{
    return JSON.stringify({{ ok: false, error: "element not found" }});
  }}
  if (typeof element.scrollIntoView === "function") {{
    element.scrollIntoView({{ block: "center", inline: "center" }});
  }}
  element.click();
  return JSON.stringify({{ ok: true }});
}})()
"""
    result = _run_agent_browser_eval_json(payload, script, timeout_ms=timeout_ms)
    if not isinstance(result, dict) or not result.get("ok"):
        return _reject(event_id=event_id, reason_code="BROWSER_ELEMENT_NOT_FOUND",
                       detail="element not found", ts_ms=ts, request_id=request_id)
    return {
        "status": STATUS_EXECUTED,
        "event_id": event_id,
        "manifest_ref": payload.get("manifest_ref") or f"browser_click::{selector[:60]}",
        "selector": selector,
        "request_id": request_id,
        "backend": BACKEND_AGENT_BROWSER,
        "ts_ms": ts,
    }


def _agent_execute_browser_type(payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_ms()
    event_id = new_event_id()
    selector = str(payload.get("selector") or "").strip()
    text = str(payload.get("text") or "")
    clear_first = bool(payload.get("clear_first", True))
    timeout_ms = int(payload.get("timeout_ms") or 5000)
    request_id = _payload_request_id(payload)

    if not selector:
        return _reject(event_id=event_id, reason_code="BROWSER_SELECTOR_MISSING",
                       detail="selector is required", ts_ms=ts, request_id=request_id)

    script = f"""
(() => {{
  const element = document.querySelector({json.dumps(selector)});
  if (!element) {{
    return JSON.stringify({{ ok: false, error: "element not found" }});
  }}
  const clearFirst = {json.dumps(clear_first)};
  const nextText = {json.dumps(text)};
  if (typeof element.focus === "function") {{
    element.focus();
  }}
  if (element.isContentEditable) {{
    if (clearFirst) {{
      element.textContent = "";
    }}
    element.textContent = (element.textContent || "") + nextText;
    {_dispatch_input_events_script('element')}
    return JSON.stringify({{ ok: true }});
  }}
  if ("value" in element) {{
    const currentValue = clearFirst ? "" : String(element.value || "");
    element.value = currentValue + nextText;
    {_dispatch_input_events_script('element')}
    return JSON.stringify({{ ok: true }});
  }}
  return JSON.stringify({{ ok: false, error: "target is not an input element" }});
}})()
"""
    result = _run_agent_browser_eval_json(payload, script, timeout_ms=timeout_ms)
    if not isinstance(result, dict) or not result.get("ok"):
        return _reject(event_id=event_id, reason_code=BROWSER_EXEC_ERROR,
                       detail="browser type failed", ts_ms=ts, request_id=request_id)
    return {
        "status": STATUS_EXECUTED,
        "event_id": event_id,
        "manifest_ref": payload.get("manifest_ref") or f"browser_type::{selector[:60]}",
        "selector": selector,
        "text_len": len(text),
        "request_id": request_id,
        "backend": BACKEND_AGENT_BROWSER,
        "ts_ms": ts,
    }


def _agent_execute_browser_screenshot(payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_ms()
    event_id = new_event_id()
    save_path = str(payload.get("save_path") or "").strip() or None
    selector = str(payload.get("selector") or "").strip() or None
    full_page = bool(payload.get("full_page", False))
    return_base64 = bool(payload.get("return_base64", save_path is None))
    timeout_ms = int(payload.get("timeout_ms") or 30000)
    request_id = _payload_request_id(payload)

    if selector:
        raise AgentBrowserUnsupported("agent-browser adapter does not support element-only screenshots yet")

    screenshot_path = save_path
    temp_path: Path | None = None
    if screenshot_path is None:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
            temp_path = Path(temp_file.name)
        screenshot_path = str(temp_path)

    command_args = ["screenshot"]
    if full_page:
        command_args.append("--full")
    command_args.append(screenshot_path)
    _run_agent_browser(payload, *command_args, timeout_ms=timeout_ms)

    result = {
        "status": STATUS_EXECUTED,
        "event_id": event_id,
        "manifest_ref": payload.get("manifest_ref") or "browser_screenshot",
        "save_path": save_path,
        "request_id": request_id,
        "backend": BACKEND_AGENT_BROWSER,
        "ts_ms": ts,
    }
    if return_base64:
        png_bytes = Path(screenshot_path).read_bytes()
        image_b64 = base64.b64encode(png_bytes).decode("ascii")
        result["image_b64"] = image_b64
        result["image_base64"] = image_b64
        result["image_mime"] = "image/png"
    if temp_path is not None and temp_path.exists():
        temp_path.unlink(missing_ok=True)
    return result


def _agent_execute_browser_execute_script(payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_ms()
    event_id = new_event_id()
    script = str(payload.get("script") or "").strip()
    script_args = payload.get("script_args") or []
    timeout_ms = int(payload.get("timeout_ms") or 10000)
    request_id = _payload_request_id(payload)

    if not script:
        return _reject(event_id=event_id, reason_code="BROWSER_SCRIPT_MISSING",
                       detail="script is required", ts_ms=ts, request_id=request_id)
    if script_args:
        raise AgentBrowserUnsupported("agent-browser adapter does not support execute_script args yet")

    raw = _run_agent_browser_eval(payload, script, timeout_ms=timeout_ms)
    parsed = _parse_jsonish_output(raw)
    result_value = parsed if parsed is not None else _clean_cli_text(raw)
    return {
        "status": STATUS_EXECUTED,
        "event_id": event_id,
        "manifest_ref": payload.get("manifest_ref") or "browser_execute_script",
        "result": result_value,
        "request_id": request_id,
        "backend": BACKEND_AGENT_BROWSER,
        "ts_ms": ts,
    }


def _agent_execute_browser_wait_for(payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_ms()
    event_id = new_event_id()
    condition = str(payload.get("condition") or "selector").strip().lower()
    value = str(payload.get("value") or "").strip()
    timeout_ms = int(payload.get("timeout_ms") or 10000)
    state = str(payload.get("state") or "visible").strip().lower()
    request_id = _payload_request_id(payload)

    if condition == "selector":
        if state in {"visible", "attached"}:
            _run_agent_browser(payload, "wait", value, timeout_ms=timeout_ms)
        elif state == "hidden":
            _run_agent_browser(payload, "wait", value, "--state", "hidden", timeout_ms=timeout_ms)
        elif state == "detached":
            _run_agent_browser(payload, "wait", "--fn", f"!document.querySelector({json.dumps(value)})", timeout_ms=timeout_ms)
        else:
            raise AgentBrowserUnsupported(f"unsupported wait state: {state}")
    elif condition == "url":
        _run_agent_browser(payload, "wait", "--url", value, timeout_ms=timeout_ms)
    elif condition == "function":
        _run_agent_browser(payload, "wait", "--fn", value, timeout_ms=timeout_ms)
    elif condition == "timeout":
        wait_ms = timeout_ms
        if value:
            try:
                wait_ms = int(value)
            except ValueError:
                wait_ms = timeout_ms
        _run_agent_browser(payload, "wait", str(max(1, wait_ms)), timeout_ms=wait_ms)
    else:
        raise AgentBrowserUnsupported(f"unsupported wait condition: {condition}")

    return {
        "status": STATUS_EXECUTED,
        "event_id": event_id,
        "manifest_ref": payload.get("manifest_ref") or f"browser_wait_for::{condition}",
        "condition": condition,
        "value": value,
        "request_id": request_id,
        "backend": BACKEND_AGENT_BROWSER,
        "ts_ms": ts,
    }


def _agent_execute_browser_scrape(payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_ms()
    event_id = new_event_id()
    selector = str(payload.get("selector") or "body").strip() or "body"
    extract = str(payload.get("extract") or "text").strip().lower()
    attribute = str(payload.get("attribute") or "").strip() or None
    limit = int(payload.get("limit") or 50)
    timeout_ms = int(payload.get("timeout_ms") or 10000)
    request_id = _payload_request_id(payload)

    if extract == "attribute" and not attribute:
        return _reject(event_id=event_id, reason_code="BROWSER_ATTRIBUTE_MISSING",
                       detail="attribute is required when extract=attribute", ts_ms=ts, request_id=request_id)

    script = f"""
(() => {{
  const selector = {json.dumps(selector)};
  const extract = {json.dumps(extract)};
  const attribute = {json.dumps(attribute)};
  const limit = {json.dumps(max(1, min(limit, 500)))};
  const elements = Array.from(document.querySelectorAll(selector)).slice(0, limit);
  const results = elements.map((element) => {{
    if (extract === "text") {{
      return element.innerText;
    }}
    if (extract === "html") {{
      return element.innerHTML;
    }}
    if (extract === "attribute") {{
      return element.getAttribute(attribute);
    }}
    return {{ text: element.innerText, html: element.innerHTML }};
  }});
  return JSON.stringify({{ results, count: results.length }});
}})()
"""
    parsed = _run_agent_browser_eval_json(payload, script, timeout_ms=timeout_ms)
    if not isinstance(parsed, dict):
        raise AgentBrowserCommandError("unexpected scrape result")
    results = parsed.get("results") if isinstance(parsed.get("results"), list) else []
    return {
        "status": STATUS_EXECUTED,
        "event_id": event_id,
        "manifest_ref": payload.get("manifest_ref") or f"browser_scrape::{selector[:60]}",
        "selector": selector,
        "extract": extract,
        "results": results,
        "count": int(parsed.get("count") or len(results)),
        "request_id": request_id,
        "backend": BACKEND_AGENT_BROWSER,
        "ts_ms": ts,
    }


def _agent_execute_browser_close(payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_ms()
    event_id = new_event_id()
    target = str(payload.get("target") or "page").strip().lower() or "page"
    timeout_ms = int(payload.get("timeout_ms") or 5000)
    request_id = _payload_request_id(payload)

    if target == "browser":
        _run_agent_browser(payload, "close", timeout_ms=timeout_ms)
    else:
        _run_agent_browser(payload, "tab", "close", timeout_ms=timeout_ms)
    return {
        "status": STATUS_EXECUTED,
        "event_id": event_id,
        "manifest_ref": payload.get("manifest_ref") or f"browser_close::{target}",
        "target": target,
        "request_id": request_id,
        "backend": BACKEND_AGENT_BROWSER,
        "ts_ms": ts,
    }


def execute_browser_navigate(payload: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_browser_backend(payload, agent_handler=_agent_execute_browser_navigate,
                                     playwright_handler=_playwright_execute_browser_navigate)


def execute_browser_click(payload: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_browser_backend(payload, agent_handler=_agent_execute_browser_click,
                                     playwright_handler=_playwright_execute_browser_click)


def execute_browser_type(payload: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_browser_backend(payload, agent_handler=_agent_execute_browser_type,
                                     playwright_handler=_playwright_execute_browser_type)


def execute_browser_screenshot(payload: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_browser_backend(payload, agent_handler=_agent_execute_browser_screenshot,
                                     playwright_handler=_playwright_execute_browser_screenshot)


def execute_browser_execute_script(payload: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_browser_backend(payload, agent_handler=_agent_execute_browser_execute_script,
                                     playwright_handler=_playwright_execute_browser_execute_script)


def execute_browser_wait_for(payload: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_browser_backend(payload, agent_handler=_agent_execute_browser_wait_for,
                                     playwright_handler=_playwright_execute_browser_wait_for)


def execute_browser_scrape(payload: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_browser_backend(payload, agent_handler=_agent_execute_browser_scrape,
                                     playwright_handler=_playwright_execute_browser_scrape)


def execute_browser_close(payload: dict[str, Any]) -> dict[str, Any]:
    return _dispatch_browser_backend(payload, agent_handler=_agent_execute_browser_close,
                                     playwright_handler=_playwright_execute_browser_close)


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
