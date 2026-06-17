from __future__ import annotations

import base64
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.skills.browser_registry import BrowserSkillProvider
from host_brain.skills.impl import browser_executor


class _FakePage:
    def __init__(self) -> None:
        self.url = "about:blank"
        self.goto_calls: list[dict[str, object]] = []

    def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
        self.goto_calls.append({"url": url, "wait_until": wait_until, "timeout": timeout})
        self.url = url.rstrip("/") + "/"

    def title(self) -> str:
        return "Example Domain"

    def screenshot(self, **_kwargs):
        return b"fake-png"


class _FakeLocator:
    @property
    def first(self):
        return self

    def screenshot(self, **_kwargs):
        return b"fake-element-png"


class _FakeSession:
    def __init__(self) -> None:
        self.page = _FakePage()

    def _ensure(self, _payload=None):
        return self.page

    def _timeout(self, _payload=None) -> int:
        return 1200

    def locator(self, _selector: str, _selector_type: str = "css"):
        return _FakeLocator()


def test_browser_registry_preserves_session_id_for_stateful_actions() -> None:
    provider = BrowserSkillProvider()
    context = {"session_id": "sess-browser", "request_id": "req-browser"}

    navigate_payload = provider.browser_navigate(
        {
            "url": "https://example.com",
            "browser_session": "lightnovel.fun",
            "browser_state_name": "lightnovel-state",
            "browser_profile": "D:/Natsume_Ai/runtime/browser/lightnovel-profile",
        },
        context,
    )
    click_payload = provider.browser_click({"selector": "#submit"}, context)
    type_payload = provider.browser_type({"selector": "#email", "text": "a@test.com"}, context)
    script_payload = provider.browser_execute_script({"script": "document.title"}, context)
    close_payload = provider.browser_close({"target": "page"}, context)

    assert navigate_payload["browser_session"] == "lightnovel.fun"
    assert navigate_payload["browser_state_name"] == "lightnovel-state"
    assert navigate_payload["browser_profile"] == "D:/Natsume_Ai/runtime/browser/lightnovel-profile"
    assert navigate_payload["session_id"] == "sess-browser"
    assert click_payload["session_id"] == "sess-browser"
    assert type_payload["session_id"] == "sess-browser"
    assert script_payload["session_id"] == "sess-browser"
    assert close_payload["session_id"] == "sess-browser"

    assert navigate_payload["request_id"] == "req-browser"
    assert click_payload["request_id"] == "req-browser"
    assert type_payload["request_id"] == "req-browser"
    assert script_payload["request_id"] == "req-browser"
    assert close_payload["request_id"] == "req-browser"


def test_playwright_profile_dir_uses_runtime_default_and_env_override(monkeypatch) -> None:
    monkeypatch.delenv("HOST_BROWSER_PLAYWRIGHT_PROFILE_DIR", raising=False)
    session = browser_executor._PlaywrightSession()

    assert session._profile_dir() == ROOT / "runtime" / "browser" / "playwright-profile"

    monkeypatch.setenv("HOST_BROWSER_PLAYWRIGHT_PROFILE_DIR", "runtime/browser/custom-profile")

    assert session._profile_dir() == ROOT / "runtime" / "browser" / "custom-profile"


def test_playwright_navigate_returns_current_page_metadata(monkeypatch) -> None:
    fake_session = _FakeSession()
    monkeypatch.setattr(browser_executor, "_session_for_payload", lambda _payload: fake_session)

    result = browser_executor.execute_browser_navigate(
        {
            "url": "https://example.com",
            "wait_until": "domcontentloaded",
            "timeout_ms": 1200,
            "session_id": "sess-1",
            "request_id": "req-1",
        }
    )

    assert result["status"] == browser_executor.STATUS_EXECUTED
    assert result["backend"] == browser_executor.BACKEND_PLAYWRIGHT
    assert result["url"] == "https://example.com/"
    assert result["title"] == "Example Domain"
    assert fake_session.page.goto_calls == [
        {"url": "https://example.com", "wait_until": "domcontentloaded", "timeout": 1200}
    ]


def test_playwright_screenshot_returns_base64(monkeypatch) -> None:
    fake_session = _FakeSession()
    monkeypatch.setattr(browser_executor, "_session_for_payload", lambda _payload: fake_session)

    result = browser_executor.execute_browser_screenshot(
        {
            "selector": "#hero",
            "return_base64": True,
            "request_id": "req-shot",
        }
    )

    assert result["status"] == browser_executor.STATUS_EXECUTED
    assert result["backend"] == browser_executor.BACKEND_PLAYWRIGHT
    assert result["image_base64"] == base64.b64encode(b"fake-element-png").decode("ascii")
