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


def test_agent_browser_command_parts_resolves_windows_cmd(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HOST_BROWSER_AGENT_COMMAND", raising=False)
    monkeypatch.setattr(
        browser_executor.shutil,
        "which",
        lambda name: "C:/mock/agent-browser.CMD" if name == "agent-browser" else None,
    )

    assert browser_executor._agent_browser_command_parts()[0] == "C:/mock/agent-browser.CMD"


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
    monkeypatch.setenv("HOST_BROWSER_AGENT_SESSION", "natsume-account")
    monkeypatch.delenv("HOST_BROWSER_AGENT_USE_PAYLOAD_SESSION", raising=False)
    monkeypatch.delenv("HOST_BROWSER_AGENT_STATE_NAME", raising=False)
    monkeypatch.delenv("AGENT_BROWSER_SESSION", raising=False)
    monkeypatch.delenv("AGENT_BROWSER_SESSION_NAME", raising=False)
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
    assert all(command[0] == "C:/mock/agent-browser.cmd" for command in commands)
    assert any("--session" in command and "natsume-account" in command for command in commands)
    assert any("--session-name" in command and "natsume-account" in command for command in commands)


def test_agent_browser_session_defaults_to_stable_account_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_BROWSER_AGENT_SESSION", "natsume-account")
    monkeypatch.delenv("HOST_BROWSER_AGENT_USE_PAYLOAD_SESSION", raising=False)
    monkeypatch.delenv("HOST_BROWSER_AGENT_STATE_NAME", raising=False)
    monkeypatch.delenv("AGENT_BROWSER_SESSION", raising=False)
    monkeypatch.delenv("AGENT_BROWSER_SESSION_NAME", raising=False)

    payload = {"session_id": "qq_dm_3414578517", "request_id": "req-browser"}
    command = browser_executor._build_agent_browser_command(payload, "open", "https://example.com")

    assert command[command.index("--session") + 1] == "natsume-account"
    assert command[command.index("--session-name") + 1] == "natsume-account"


def test_agent_browser_payload_session_is_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_BROWSER_AGENT_USE_PAYLOAD_SESSION", "true")
    monkeypatch.delenv("HOST_BROWSER_AGENT_STATE_NAME", raising=False)

    payload = {"session_id": "qq:dm/3414578517"}
    command = browser_executor._build_agent_browser_command(payload, "open", "https://example.com")

    assert command[command.index("--session") + 1] == "qq-dm-3414578517"


def test_agent_browser_explicit_browser_session_and_profile_win(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOST_BROWSER_AGENT_SESSION", "natsume-account")
    monkeypatch.setenv("HOST_BROWSER_AGENT_PROFILE", "D:/Natsume_Ai/runtime/browser/chrome-profile")
    monkeypatch.setenv("HOST_BROWSER_AGENT_STATE_NAME", "persisted-main")

    payload = {"browser_session": "lightnovel.fun"}
    command = browser_executor._build_agent_browser_command(payload, "open", "https://example.com")

    assert command[command.index("--session") + 1] == "lightnovel.fun"
    assert command[command.index("--session-name") + 1] == "persisted-main"
    assert command[command.index("--profile") + 1] == "D:/Natsume_Ai/runtime/browser/chrome-profile"


def test_playwright_fallback_uses_runtime_profile_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HOST_BROWSER_PLAYWRIGHT_PROFILE_DIR", raising=False)
    profile_dir = browser_executor.BrowserSession()._playwright_profile_dir()

    assert profile_dir == ROOT / "runtime" / "browser" / "playwright-profile"

    monkeypatch.setenv("HOST_BROWSER_PLAYWRIGHT_PROFILE_DIR", "runtime/browser/custom-profile")
    profile_dir = browser_executor.BrowserSession()._playwright_profile_dir()

    assert profile_dir == ROOT / "runtime" / "browser" / "custom-profile"


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
