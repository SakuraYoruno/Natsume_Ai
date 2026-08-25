"""Playwright-only browser skill execution layer.

Host browser automation has a single official backend now: Playwright.
The previous legacy browser adapters were removed from this runtime to
avoid split sessions, hidden Chrome debug ports, and backend-dependent behavior.
"""

from __future__ import annotations

import base64
import asyncio
import json
import logging
import os
from pathlib import Path
import threading
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

BACKEND_PLAYWRIGHT = "playwright"
PLAYWRIGHT_DEFAULT_PROFILE_DIR = "runtime/browser/playwright-profile"
DEFAULT_TIMEOUT_MS = 30_000


def _env_bool(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = str(raw).strip().lower()
    if not value:
        return default
    if value in {"1", "true", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "no", "n", "off"}:
        return False
    return default


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
        "backend": BACKEND_PLAYWRIGHT,
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
        "backend": BACKEND_PLAYWRIGHT,
        "ts_ms": ts_ms,
    }


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except TypeError:
        return str(value)


def _ensure_playwright_subprocess_policy() -> None:
    """Ensure Playwright can spawn its Node driver on Windows.

    The Host may run with a selector event-loop policy for other services. On
    Windows that policy does not implement subprocess transports, which breaks
    Playwright startup with NotImplementedError. Existing running loops are not
    replaced; this only affects new loops created by Playwright's sync bridge.
    """
    if os.name != "nt":
        return
    policy_cls = getattr(asyncio, "WindowsProactorEventLoopPolicy", None)
    if policy_cls is None:
        return
    current = asyncio.get_event_loop_policy()
    if not isinstance(current, policy_cls):
        asyncio.set_event_loop_policy(policy_cls())


class _PlaywrightSession:
    def __init__(self, *, session_id: str = "default", profile_dir: str | None = None) -> None:
        self.session_id = str(session_id or "default").strip() or "default"
        self._profile_dir_override = str(profile_dir or "").strip()
        self._lock = threading.RLock()
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None

    @staticmethod
    def _root() -> Path:
        return Path(__file__).resolve().parents[3]

    @staticmethod
    def _timeout(payload: dict[str, Any] | None = None) -> int:
        raw = (payload or {}).get("timeout_ms") if payload else None
        try:
            return max(1_000, int(raw or DEFAULT_TIMEOUT_MS))
        except (TypeError, ValueError):
            return DEFAULT_TIMEOUT_MS

    @staticmethod
    def _headless() -> bool:
        return _env_bool("HOST_BROWSER_PLAYWRIGHT_HEADLESS", default=False)

    @staticmethod
    def _channel() -> str:
        return str(os.environ.get("HOST_BROWSER_PLAYWRIGHT_CHANNEL") or "chrome").strip()

    def _profile_dir(self) -> Path:
        raw = self._profile_dir_override or str(os.environ.get("HOST_BROWSER_PLAYWRIGHT_PROFILE_DIR") or "").strip()
        path = Path(raw) if raw else self._root() / PLAYWRIGHT_DEFAULT_PROFILE_DIR
        if not path.is_absolute():
            path = self._root() / path
        return path

    def _ensure(self, payload: dict[str, Any] | None = None):
        with self._lock:
            if self._page is not None and not self._page.is_closed():
                self._page.set_default_timeout(self._timeout(payload))
                return self._page
            try:
                from playwright.sync_api import sync_playwright  # noqa: PLC0415
            except ImportError as exc:
                raise RuntimeError("playwright not installed") from exc

            if self._playwright is None:
                _ensure_playwright_subprocess_policy()
                self._playwright = sync_playwright().start()

            viewport = {"width": 1920, "height": 1080}
            user_agent = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
            persistent = _env_bool("HOST_BROWSER_PLAYWRIGHT_PERSISTENT_PROFILE_ENABLED", default=True)
            channel = self._channel()
            channel_kwargs = {"channel": channel} if channel else {}
            if persistent:
                profile_dir = self._profile_dir()
                profile_dir.mkdir(parents=True, exist_ok=True)
                self._context = self._playwright.chromium.launch_persistent_context(
                    str(profile_dir),
                    headless=self._headless(),
                    viewport=viewport,
                    user_agent=user_agent,
                    accept_downloads=True,
                    **channel_kwargs,
                )
                self._browser = None
                pages = list(self._context.pages or [])
                self._page = pages[0] if pages else self._context.new_page()
            else:
                self._browser = self._playwright.chromium.launch(headless=self._headless(), **channel_kwargs)
                self._context = self._browser.new_context(
                    viewport=viewport,
                    user_agent=user_agent,
                    accept_downloads=True,
                )
                self._page = self._context.new_page()
            self._page.set_default_timeout(self._timeout(payload))
            logger.info(
                "Browser executor started Playwright session session_id=%s profile=%s persistent=%s headless=%s channel=%s",
                self.session_id,
                str(self._profile_dir()) if persistent else "",
                persistent,
                self._headless(),
                channel or "bundled",
            )
            return self._page

    def locator(self, selector: str, selector_type: str = "css"):
        page = self._ensure()
        normalized_type = str(selector_type or "css").strip().lower()
        if normalized_type == "xpath" or selector.startswith("//") or selector.startswith("(//"):
            return page.locator(f"xpath={selector}")
        return page.locator(selector)

    def close(self, *, browser: bool = False) -> None:
        with self._lock:
            if not browser:
                try:
                    if self._page is not None and not self._page.is_closed():
                        self._page.close()
                finally:
                    self._page = None
                return
            try:
                if self._context is not None:
                    self._context.close()
            finally:
                self._context = None
                self._page = None
            try:
                if self._browser is not None:
                    self._browser.close()
            finally:
                self._browser = None
            try:
                if self._playwright is not None:
                    self._playwright.stop()
            finally:
                self._playwright = None


_SESSIONS: dict[str, _PlaywrightSession] = {}
_SESSIONS_LOCK = threading.RLock()


def _payload_session_id(payload: dict[str, Any] | None) -> str:
    data = dict(payload or {})
    return str(data.get("browser_session") or data.get("session_id") or "default").strip() or "default"


def _payload_profile_dir(payload: dict[str, Any] | None) -> str:
    return str(dict(payload or {}).get("profile_dir") or "").strip()


def _session_key(payload: dict[str, Any] | None) -> str:
    session_id = _payload_session_id(payload)
    profile_dir = _payload_profile_dir(payload)
    return f"{session_id}|{profile_dir}" if profile_dir else session_id


def _session_for_payload(payload: dict[str, Any] | None, *, create: bool = True) -> _PlaywrightSession | None:
    key = _session_key(payload)
    with _SESSIONS_LOCK:
        session = _SESSIONS.get(key)
        if session is None and create:
            session = _PlaywrightSession(session_id=_payload_session_id(payload), profile_dir=_payload_profile_dir(payload))
            _SESSIONS[key] = session
        return session


def _forget_session(payload: dict[str, Any] | None) -> None:
    with _SESSIONS_LOCK:
        _SESSIONS.pop(_session_key(payload), None)


def _normalize_wait_until(value: Any) -> str:
    wait_until = str(value or "load").strip().lower()
    if wait_until in {"load", "domcontentloaded", "networkidle", "commit"}:
        return wait_until
    return "load"


def execute_browser_navigate(payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_ms()
    event_id = new_event_id()
    url = str(payload.get("url") or "").strip()
    request_id = _payload_request_id(payload)
    if not url:
        return _reject(event_id=event_id, reason_code="BROWSER_URL_MISSING", detail="url is required", ts_ms=ts, request_id=request_id)
    try:
        session = _session_for_payload(payload)
        if session is None:
            raise RuntimeError("browser session unavailable")
        page = session._ensure(payload)
        page.goto(url, wait_until=_normalize_wait_until(payload.get("wait_until")), timeout=session._timeout(payload))
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
    except TimeoutError as exc:
        return _reject(event_id=event_id, reason_code=BROWSER_TIMEOUT, detail=str(exc), ts_ms=ts, request_id=request_id)
    except RuntimeError as exc:
        return _degraded(event_id=event_id, reason_code=BROWSER_UNAVAILABLE, detail=str(exc), ts_ms=ts, request_id=request_id)
    except Exception as exc:
        logger.error("browser_navigate failed: %s", exc)
        return _reject(event_id=event_id, reason_code=BROWSER_EXEC_ERROR, detail=str(exc), ts_ms=ts, request_id=request_id)


def execute_browser_click(payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_ms()
    event_id = new_event_id()
    selector = str(payload.get("selector") or "").strip()
    selector_type = str(payload.get("selector_type") or "css").strip().lower()
    request_id = _payload_request_id(payload)
    if not selector:
        return _reject(event_id=event_id, reason_code="BROWSER_SELECTOR_MISSING", detail="selector is required", ts_ms=ts, request_id=request_id)
    try:
        session = _session_for_payload(payload)
        if session is None:
            raise RuntimeError("browser session unavailable")
        locator = session.locator(selector, selector_type)
        locator.first.click(timeout=session._timeout(payload))
        return {
            "status": STATUS_EXECUTED,
            "event_id": event_id,
            "manifest_ref": payload.get("manifest_ref") or f"browser_click::{selector[:60]}",
            "selector": selector,
            "request_id": request_id,
            "backend": BACKEND_PLAYWRIGHT,
            "ts_ms": ts,
        }
    except RuntimeError as exc:
        return _degraded(event_id=event_id, reason_code=BROWSER_UNAVAILABLE, detail=str(exc), ts_ms=ts, request_id=request_id)
    except Exception as exc:
        return _reject(event_id=event_id, reason_code=BROWSER_EXEC_ERROR, detail=str(exc), ts_ms=ts, request_id=request_id)


def execute_browser_type(payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_ms()
    event_id = new_event_id()
    selector = str(payload.get("selector") or "").strip()
    selector_type = str(payload.get("selector_type") or "css").strip().lower()
    text = str(payload.get("text") or "")
    clear_first = bool(payload.get("clear_first", True))
    request_id = _payload_request_id(payload)
    if not selector:
        return _reject(event_id=event_id, reason_code="BROWSER_SELECTOR_MISSING", detail="selector is required", ts_ms=ts, request_id=request_id)
    try:
        session = _session_for_payload(payload)
        if session is None:
            raise RuntimeError("browser session unavailable")
        locator = session.locator(selector, selector_type).first
        if clear_first:
            locator.fill(text, timeout=session._timeout(payload))
        else:
            locator.type(text, timeout=session._timeout(payload))
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
    except RuntimeError as exc:
        return _degraded(event_id=event_id, reason_code=BROWSER_UNAVAILABLE, detail=str(exc), ts_ms=ts, request_id=request_id)
    except Exception as exc:
        return _reject(event_id=event_id, reason_code=BROWSER_EXEC_ERROR, detail=str(exc), ts_ms=ts, request_id=request_id)


def execute_browser_screenshot(payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_ms()
    event_id = new_event_id()
    request_id = _payload_request_id(payload)
    selector = str(payload.get("selector") or "").strip()
    selector_type = str(payload.get("selector_type") or "css").strip().lower()
    save_path = str(payload.get("save_path") or "").strip()
    full_page = bool(payload.get("full_page", False))
    return_base64 = bool(payload.get("return_base64", not save_path))
    try:
        session = _session_for_payload(payload)
        if session is None:
            raise RuntimeError("browser session unavailable")
        page = session._ensure(payload)
        target = session.locator(selector, selector_type).first if selector else page
        kwargs: dict[str, Any] = {}
        if save_path:
            out = Path(save_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            kwargs["path"] = str(out)
        if not selector:
            kwargs["full_page"] = full_page
        png_bytes = target.screenshot(**kwargs)
        result: dict[str, Any] = {
            "status": STATUS_EXECUTED,
            "event_id": event_id,
            "manifest_ref": payload.get("manifest_ref") or "browser_screenshot",
            "save_path": save_path or None,
            "request_id": request_id,
            "backend": BACKEND_PLAYWRIGHT,
            "ts_ms": ts,
        }
        if return_base64:
            image_b64 = base64.b64encode(png_bytes).decode("ascii")
            result["image_b64"] = image_b64
            result["image_base64"] = image_b64
            result["image_mime"] = "image/png"
        return result
    except RuntimeError as exc:
        return _degraded(event_id=event_id, reason_code=BROWSER_UNAVAILABLE, detail=str(exc), ts_ms=ts, request_id=request_id)
    except Exception as exc:
        return _reject(event_id=event_id, reason_code=BROWSER_EXEC_ERROR, detail=str(exc), ts_ms=ts, request_id=request_id)


def execute_browser_execute_script(payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_ms()
    event_id = new_event_id()
    request_id = _payload_request_id(payload)
    script = str(payload.get("script") or "").strip()
    script_args = payload.get("script_args")
    if not script:
        return _reject(event_id=event_id, reason_code="BROWSER_SCRIPT_MISSING", detail="script is required", ts_ms=ts, request_id=request_id)
    try:
        session = _session_for_payload(payload)
        if session is None:
            raise RuntimeError("browser session unavailable")
        page = session._ensure(payload)
        if script_args is None:
            result_value = page.evaluate(script)
        else:
            result_value = page.evaluate(script, script_args)
        return {
            "status": STATUS_EXECUTED,
            "event_id": event_id,
            "manifest_ref": payload.get("manifest_ref") or "browser_execute_script",
            "result": _json_safe(result_value),
            "request_id": request_id,
            "backend": BACKEND_PLAYWRIGHT,
            "ts_ms": ts,
        }
    except RuntimeError as exc:
        return _degraded(event_id=event_id, reason_code=BROWSER_UNAVAILABLE, detail=str(exc), ts_ms=ts, request_id=request_id)
    except Exception as exc:
        return _reject(event_id=event_id, reason_code=BROWSER_EXEC_ERROR, detail=str(exc), ts_ms=ts, request_id=request_id)


def execute_browser_wait_for(payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_ms()
    event_id = new_event_id()
    request_id = _payload_request_id(payload)
    condition = str(payload.get("condition") or "selector").strip().lower()
    value = str(payload.get("value") or "").strip()
    state = str(payload.get("state") or "visible").strip().lower()
    try:
        session = _session_for_payload(payload)
        if session is None:
            raise RuntimeError("browser session unavailable")
        page = session._ensure(payload)
        timeout = session._timeout(payload)
        if condition == "selector":
            if not value:
                return _reject(event_id=event_id, reason_code="BROWSER_SELECTOR_MISSING", detail="selector is required", ts_ms=ts, request_id=request_id)
            page.wait_for_selector(value, state=state, timeout=timeout)
        elif condition == "url":
            page.wait_for_url(value, timeout=timeout)
        elif condition == "function":
            page.wait_for_function(value, timeout=timeout)
        elif condition == "timeout":
            wait_ms = int(value or timeout)
            page.wait_for_timeout(max(1, wait_ms))
        else:
            return _reject(event_id=event_id, reason_code=BROWSER_BACKEND_UNSUPPORTED, detail=f"unsupported wait condition: {condition}", ts_ms=ts, request_id=request_id)
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
    except RuntimeError as exc:
        return _degraded(event_id=event_id, reason_code=BROWSER_UNAVAILABLE, detail=str(exc), ts_ms=ts, request_id=request_id)
    except Exception as exc:
        return _reject(event_id=event_id, reason_code=BROWSER_EXEC_ERROR, detail=str(exc), ts_ms=ts, request_id=request_id)


def execute_browser_scrape(payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_ms()
    event_id = new_event_id()
    request_id = _payload_request_id(payload)
    selector = str(payload.get("selector") or "body").strip() or "body"
    selector_type = str(payload.get("selector_type") or "css").strip().lower()
    extract = str(payload.get("extract") or "text").strip().lower()
    attribute = str(payload.get("attribute") or "").strip()
    limit = max(1, min(int(payload.get("limit") or 50), 500))
    if extract == "attribute" and not attribute:
        return _reject(event_id=event_id, reason_code="BROWSER_ATTRIBUTE_MISSING", detail="attribute is required when extract=attribute", ts_ms=ts, request_id=request_id)
    try:
        session = _session_for_payload(payload)
        if session is None:
            raise RuntimeError("browser session unavailable")
        locator = session.locator(selector, selector_type)
        count = min(locator.count(), limit)
        results: list[Any] = []
        timeout = session._timeout(payload)
        for index in range(count):
            item = locator.nth(index)
            if extract == "text":
                results.append(item.inner_text(timeout=timeout))
            elif extract == "html":
                results.append(item.inner_html(timeout=timeout))
            elif extract == "attribute":
                results.append(item.get_attribute(attribute, timeout=timeout))
            else:
                results.append({"text": item.inner_text(timeout=timeout), "html": item.inner_html(timeout=timeout)})
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
    except RuntimeError as exc:
        return _degraded(event_id=event_id, reason_code=BROWSER_UNAVAILABLE, detail=str(exc), ts_ms=ts, request_id=request_id)
    except Exception as exc:
        return _reject(event_id=event_id, reason_code=BROWSER_EXEC_ERROR, detail=str(exc), ts_ms=ts, request_id=request_id)


def execute_browser_close(payload: dict[str, Any]) -> dict[str, Any]:
    ts = now_ms()
    event_id = new_event_id()
    request_id = _payload_request_id(payload)
    target = str(payload.get("target") or "page").strip().lower() or "page"
    try:
        session = _session_for_payload(payload, create=False)
        if session is not None:
            session.close(browser=(target == "browser"))
        if target == "browser":
            _forget_session(payload)
        return {
            "status": STATUS_EXECUTED,
            "event_id": event_id,
            "manifest_ref": payload.get("manifest_ref") or f"browser_close::{target}",
            "target": target,
            "request_id": request_id,
            "backend": BACKEND_PLAYWRIGHT,
            "ts_ms": ts,
        }
    except Exception as exc:
        return _reject(event_id=event_id, reason_code=BROWSER_EXEC_ERROR, detail=str(exc), ts_ms=ts, request_id=request_id)


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
    action = str(payload.get("action") or "").strip().lower()
    handler = _ACTION_MAP.get(action)
    if handler is None:
        return {
            "status": STATUS_REJECTED,
            "event_id": new_event_id(),
            "reason_code": "BROWSER_UNKNOWN_ACTION",
            "detail": f"unknown browser action: {action!r}",
            "manifest_ref": None,
            "backend": BACKEND_PLAYWRIGHT,
            "ts_ms": now_ms(),
        }
    return handler(payload)
