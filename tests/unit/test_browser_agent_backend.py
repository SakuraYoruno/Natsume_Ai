from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.skills.browser_registry import BrowserSkillProvider
from host_brain.skills.impl import browser_executor


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["agent-browser"], returncode=returncode, stdout=stdout, stderr=stderr)


def test_browser_registry_preserves_session_id_for_stateful_actions() -> None:
    provider = BrowserSkillProvider()
    context = {"session_id": "sess-browser", "request_id": "req-browser"}

    click_payload = provider.browser_click({"selector": "#submit"}, context)
    type_payload = provider.browser_type({"selector": "#email", "text": "a@test.com"}, context)
    script_payload = provider.browser_execute_script({"script": "document.title"}, context)
    close_payload = provider.browser_close({"target": "page"}, context)

    assert click_payload["session_id"] == "sess-browser"
    assert type_payload["session_id"] == "sess-browser"
    assert script_payload["session_id"] == "sess-browser"
    assert close_payload["session_id"] == "sess-browser"

    assert click_payload["request_id"] == "req-browser"
    assert type_payload["request_id"] == "req-browser"
    assert script_payload["request_id"] == "req-browser"
    assert close_payload["request_id"] == "req-browser"


def test_agent_browser_backend_handles_navigation(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(list(command))
        tail = list(command[-2:])
        if tail == ["get", "url"]:
            return _completed(stdout="https://example.com/\n")
        if tail == ["get", "title"]:
            return _completed(stdout="Example Domain\n")
        return _completed()

    monkeypatch.setenv("HOST_BROWSER_BACKEND", "agent-browser")
    monkeypatch.setattr(browser_executor.shutil, "which", lambda _: "C:/mock/agent-browser.cmd")
    monkeypatch.setattr(browser_executor.subprocess, "run", fake_run)

    result = browser_executor.execute_browser_navigate(
        {
            "action": "navigate",
            "url": "https://example.com",
            "timeout_ms": 1200,
            "session_id": "sess-1",
            "request_id": "req-1",
        }
    )

    assert result["status"] == browser_executor.STATUS_EXECUTED
    assert result["backend"] == browser_executor.BACKEND_AGENT_BROWSER
    assert result["url"] == "https://example.com/"
    assert result["title"] == "Example Domain"
    assert any("--session" in command and "sess-1" in command for command in commands)


def test_explicit_agent_browser_missing_returns_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_BROWSER_BACKEND", "agent-browser")
    monkeypatch.setattr(browser_executor.shutil, "which", lambda _: None)

    result = browser_executor.execute_browser_navigate(
        {
            "action": "navigate",
            "url": "https://example.com",
            "request_id": "req-2",
        }
    )

    assert result["status"] == browser_executor.STATUS_DEGRADED
    assert result["reason_code"] == browser_executor.BROWSER_UNAVAILABLE
    assert "agent-browser" in result["detail"]


def test_auto_backend_falls_back_for_element_screenshot(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = {
        "status": browser_executor.STATUS_EXECUTED,
        "backend": browser_executor.BACKEND_PLAYWRIGHT,
        "manifest_ref": "browser_screenshot",
    }

    monkeypatch.setenv("HOST_BROWSER_BACKEND", "auto")
    monkeypatch.setattr(browser_executor.shutil, "which", lambda _: "C:/mock/agent-browser.cmd")
    monkeypatch.setattr(
        browser_executor,
        "_playwright_execute_browser_screenshot",
        lambda payload: sentinel,
    )

    result = browser_executor.execute_browser_screenshot(
        {
            "action": "screenshot",
            "selector": "#hero",
            "request_id": "req-3",
        }
    )

    assert result is sentinel



def test_auto_backend_falls_back_to_os_shell_for_navigation_when_browser_backend_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    monkeypatch.setenv("HOST_BROWSER_BACKEND", "auto")
    monkeypatch.setattr(browser_executor.shutil, "which", lambda _: None)
    monkeypatch.setattr(browser_executor.os, "startfile", lambda url: calls.append(url), raising=False)

    result = browser_executor.execute_browser_navigate(
        {
            "action": "navigate",
            "url": "https://example.com",
            "request_id": "req-os-shell",
        }
    )

    assert result["status"] == browser_executor.STATUS_EXECUTED
    assert result["backend"] == browser_executor.BACKEND_OS_SHELL
    assert calls == ["https://example.com"]


def test_auto_backend_falls_back_to_desktop_screenshot_when_browser_backend_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_BROWSER_BACKEND", "auto")
    monkeypatch.setattr(browser_executor.shutil, "which", lambda _: None)
    monkeypatch.setattr(
        browser_executor,
        "_desktop_fallback_browser_screenshot",
        lambda payload: {
            "status": browser_executor.STATUS_EXECUTED,
            "backend": browser_executor.BACKEND_DESKTOP_FALLBACK,
            "manifest_ref": payload.get("manifest_ref") or "browser_screenshot::desktop_fallback",
        },
    )

    result = browser_executor.execute_browser_screenshot(
        {
            "action": "screenshot",
            "request_id": "req-desktop-fallback",
        }
    )

    assert result["status"] == browser_executor.STATUS_EXECUTED
    assert result["backend"] == browser_executor.BACKEND_DESKTOP_FALLBACK
