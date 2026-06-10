"""Chrome DevTools Protocol 轻量客户端——绕过 agent-browser eval 的兼容问题。"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)


def _fetch_json(url: str, timeout: float = 5) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _ws_send_recv(ws_url: str, payload: dict[str, Any], timeout: float = 15) -> dict[str, Any] | None:
    """Send a CDP command over WebSocket and return the matching response."""
    try:
        import websocket  # noqa: PLC0415
    except ModuleNotFoundError:
        logger.warning("cdp_client: websocket-client not installed; pip install websocket-client")
        return None

    msg_id = payload.get("id", 1)
    deadline = time.monotonic() + timeout
    try:
        ws = websocket.create_connection(ws_url, timeout=timeout)
        ws.send(json.dumps(payload))
        ws.settimeout(timeout)
        while time.monotonic() < deadline:
            msg = json.loads(ws.recv())
            if msg.get("id") == msg_id:
                return msg
            # also handle errors
            if msg.get("error"):
                logger.warning("cdp error: %s", msg["error"])
                return msg
        return None
    except Exception as exc:
        logger.warning("cdp ws error: %s", exc)
        return None
    finally:
        try:
            ws.close()
        except Exception:
            pass


def cdp_eval(expression: str, *, debug_port: int = 9222, timeout: float = 15) -> str:
    """Evaluate JavaScript in the active Chrome tab via CDP and return the result as a string.

    Automatically finds the first page tab (skips extension/service-worker tabs).
    """
    tabs: list[dict[str, Any]] = _fetch_json(f"http://127.0.0.1:{debug_port}/json", timeout=5) or []

    ws_url: str | None = None
    for t in tabs:
        if t.get("type") == "page" and not t.get("url", "").startswith("chrome-extension://"):
            ws_url = t.get("webSocketDebuggerUrl") or ""
            break
    if not ws_url:
        # fallback: any page tab
        for t in tabs:
            if t.get("type") == "page":
                ws_url = t.get("webSocketDebuggerUrl") or ""
                break
    if not ws_url:
        raise RuntimeError("cdp_eval: no page tab available on debug port")

    wrapped = (
        "JSON.stringify((function(){try{var __r=eval(arguments[0]);return typeof __r==='string'?__r:__r;}"
        "catch(e){return'__cdp_error__:'+e.message;}})())"
    )
    payload = {
        "id": 1,
        "method": "Runtime.evaluate",
        "params": {"expression": wrapped, "returnByValue": True,
                   "additional_context": json.dumps(expression)},
    }
    # Actually simpler: just wrap
    payload = {
        "id": 1,
        "method": "Runtime.evaluate",
        "params": {
            "expression": f"(function(){{ var __r = eval({json.dumps(expression)}); if(typeof __r==='string')return __r; return JSON.stringify(__r); }})()",
            "returnByValue": True,
        },
    }

    result = _ws_send_recv(ws_url, payload, timeout=timeout)
    if result is None:
        raise RuntimeError("cdp_eval: no response from Chrome")

    err = result.get("error")
    if err:
        raise RuntimeError(f"cdp_eval error: {err}")

    value = result.get("result", {}).get("result", {}).get("value")
    if value is None:
        raise RuntimeError("cdp_eval: null result")

    if isinstance(value, str) and value.startswith("__cdp_error__:"):
        raise RuntimeError(value)

    return str(value)


def cdp_navigate(url: str, *, debug_port: int = 9222, timeout: float = 30) -> dict[str, Any]:
    """Navigate the first page tab to a URL via CDP and wait for load."""
    tabs = _fetch_json(f"http://127.0.0.1:{debug_port}/json", timeout=5) or []

    ws_url = None
    for t in tabs:
        if t.get("type") == "page" and not t.get("url", "").startswith("chrome-extension://"):
            ws_url = t.get("webSocketDebuggerUrl") or ""
            break
    if not ws_url:
        for t in tabs:
            if t.get("type") == "page":
                ws_url = t.get("webSocketDebuggerUrl") or ""
                break
    if not ws_url:
        raise RuntimeError("cdp_navigate: no page tab available")

    # Navigate
    nav_result = _ws_send_recv(ws_url, {
        "id": 1, "method": "Page.navigate",
        "params": {"url": url},
    }, timeout=timeout)

    # Wait for load
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ready = _ws_send_recv(ws_url, {
            "id": 2, "method": "Runtime.evaluate",
            "params": {"expression": "document.readyState", "returnByValue": True},
        }, timeout=5)
        if ready and ready.get("result", {}).get("result", {}).get("value") == "complete":
            break
        time.sleep(0.5)

    # Get title
    title_result = _ws_send_recv(ws_url, {
        "id": 3, "method": "Runtime.evaluate",
        "params": {"expression": "document.title", "returnByValue": True},
    }, timeout=5)

    title = ""
    if title_result:
        title = title_result.get("result", {}).get("result", {}).get("value", "")

    return {"url": url, "title": title, "nav_result": nav_result}


def cdp_snapshot(selector: str = "body", *, debug_port: int = 9222, timeout: float = 10) -> str:
    """Get innerText of any element via CDP."""
    return cdp_eval(
        f"document.querySelector({json.dumps(selector)}).innerText.slice(0, 50000)",
        debug_port=debug_port, timeout=timeout,
    )


def cdp_is_alive(debug_port: int = 9222) -> bool:
    try:
        _fetch_json(f"http://127.0.0.1:{debug_port}/json/version", timeout=2)
        return True
    except Exception:
        return False
