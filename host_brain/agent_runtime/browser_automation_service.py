"""Shared Playwright browser automation service for production workers.

This facade is the only official Host browser runtime. It keeps one
process-local Playwright session/profile hot and exposes the small open / wait /
eval surface required by production tasks.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import threading
from typing import Any


def _env_bool(name: str, *, default: bool) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


@dataclass(frozen=True)
class BrowserAutomationResult:
    stdout: str = ""


class BrowserAutomationService:
    """Single-process facade over the shared Playwright session."""

    def __init__(self, *, session_id: str, auto_connect: bool = True) -> None:
        self.session_id = str(session_id or "natsume_browser").strip() or "natsume_browser"
        self.auto_connect = bool(auto_connect)
        self._lock = threading.RLock()

    def payload(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "browser_session": self.session_id,
            "backend": "playwright",
            "profile_dir": self._profile_dir(),
        }

    def _profile_dir(self) -> str:
        if self.session_id == "natsume_lightnovel":
            return (
                str(os.environ.get("HOST_LIGHTNOVEL_BROWSER_PROFILE_DIR") or "").strip()
                or "runtime/browser/lightnovel-profile"
            )
        return str(os.environ.get(f"HOST_BROWSER_PLAYWRIGHT_PROFILE_DIR_{self.session_id.upper()}") or "").strip()

    @staticmethod
    def _require_ok(result: dict[str, Any], *, action: str) -> dict[str, Any]:
        status = str(result.get("status") or "").strip().upper()
        if status != "EXECUTED":
            detail = str(result.get("detail") or result.get("reason_code") or f"{action} failed")
            raise RuntimeError(detail)
        return result

    def open(self, url: str, *, timeout_ms: int = 20_000) -> None:
        from host_brain.skills.impl.browser_executor import execute_browser_skill  # noqa: PLC0415

        with self._lock:
            result = execute_browser_skill({**self.payload(), "action": "navigate", "url": str(url), "timeout_ms": timeout_ms})
            self._require_ok(result, action="open")

    def wait_fn(self, expression: str, *, timeout_ms: int = 15_000) -> None:
        from host_brain.skills.impl.browser_executor import execute_browser_skill  # noqa: PLC0415

        with self._lock:
            result = execute_browser_skill(
                {**self.payload(), "action": "wait_for", "condition": "function", "value": str(expression), "timeout_ms": timeout_ms}
            )
            self._require_ok(result, action="wait_fn")

    def eval(self, script: str, *, timeout_ms: int = 15_000) -> str:
        from host_brain.skills.impl.browser_executor import execute_browser_skill  # noqa: PLC0415

        with self._lock:
            result = execute_browser_skill({**self.payload(), "action": "execute_script", "script": str(script), "timeout_ms": timeout_ms})
            self._require_ok(result, action="eval")
            value = result.get("result")
            return value if isinstance(value, str) else str(value)

    def close(self, *, target: str = "browser") -> None:
        from host_brain.skills.impl.browser_executor import execute_browser_skill  # noqa: PLC0415

        with self._lock:
            result = execute_browser_skill({**self.payload(), "action": "close", "target": str(target or "browser")})
            self._require_ok(result, action="close")

    def open_wait_eval(
        self,
        url: str,
        script: str,
        *,
        wait_expression: str = 'document.readyState === "complete"',
        open_timeout_ms: int = 20_000,
        wait_timeout_ms: int = 15_000,
        eval_timeout_ms: int = 15_000,
    ) -> str:
        with self._lock:
            self.open(url, timeout_ms=open_timeout_ms)
            if wait_expression:
                self.wait_fn(wait_expression, timeout_ms=wait_timeout_ms)
            return self.eval(script, timeout_ms=eval_timeout_ms)


_SERVICES: dict[str, BrowserAutomationService] = {}
_SERVICES_LOCK = threading.RLock()


def get_browser_automation_service(
    *,
    session_id: str = "natsume_lightnovel",
    auto_connect: bool | None = None,
) -> BrowserAutomationService:
    resolved_auto_connect = _env_bool("HOST_BROWSER_AUTO_CONNECT", default=True) if auto_connect is None else bool(auto_connect)
    key = f"{session_id}:{int(resolved_auto_connect)}"
    with _SERVICES_LOCK:
        service = _SERVICES.get(key)
        if service is None:
            service = BrowserAutomationService(session_id=session_id, auto_connect=resolved_auto_connect)
            _SERVICES[key] = service
        return service


__all__ = [
    "BrowserAutomationResult",
    "BrowserAutomationService",
    "get_browser_automation_service",
]
