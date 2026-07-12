from __future__ import annotations

import builtins
import sys
from types import SimpleNamespace

from edge_client.actuators.desktop_clipboard import (
    DESKTOP_CLIPBOARD_ACTION_INVALID,
    DESKTOP_CLIPBOARD_FAILED,
    DESKTOP_CLIPBOARD_UNAVAILABLE,
    execute_desktop_clipboard_effect,
)


def test_edge_clipboard_read_returns_text_evidence(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setitem(sys.modules, "pyperclip", SimpleNamespace(paste=lambda: "edge-owned"))

    result = execute_desktop_clipboard_effect(
        action="clipboard_read",
        payload={"content_type": "text"},
    )

    assert result.success is True
    assert result.status == "COMPLETED"
    assert result.event == {
        "content_type": "text",
        "text": "edge-owned",
        "text_length": 10,
    }


def test_edge_clipboard_write_does_not_echo_sensitive_text(monkeypatch) -> None:  # noqa: ANN001
    copied: list[str] = []
    monkeypatch.setitem(sys.modules, "pyperclip", SimpleNamespace(copy=copied.append))

    result = execute_desktop_clipboard_effect(
        action="clipboard_write",
        payload={"text": "secret clipboard value"},
    )

    assert result.success is True
    assert copied == ["secret clipboard value"]
    assert result.event == {"content_type": "text", "text_length": 22}
    assert "secret clipboard value" not in str(result.as_dict())


def test_edge_clipboard_rejects_unsupported_action_and_content_type(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setitem(sys.modules, "pyperclip", SimpleNamespace(paste=lambda: "unused"))

    unsupported_action = execute_desktop_clipboard_effect(action="click", payload={})
    unsupported_type = execute_desktop_clipboard_effect(
        action="clipboard_read",
        payload={"content_type": "image"},
    )

    assert unsupported_action.reason_code == DESKTOP_CLIPBOARD_ACTION_INVALID
    assert unsupported_type.reason_code == DESKTOP_CLIPBOARD_ACTION_INVALID


def test_edge_clipboard_reports_dependency_unavailable_without_leaking_import_error(
    monkeypatch,
) -> None:  # noqa: ANN001
    monkeypatch.delitem(sys.modules, "pyperclip", raising=False)
    original_import = builtins.__import__

    def _import(name, *args, **kwargs):  # noqa: ANN001, ANN202
        if name == "pyperclip":
            raise ModuleNotFoundError("private dependency path")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _import)

    result = execute_desktop_clipboard_effect(action="clipboard_read", payload={})

    assert result.reason_code == DESKTOP_CLIPBOARD_UNAVAILABLE
    assert "private dependency path" not in result.detail


def test_edge_clipboard_sanitizes_runtime_failure_detail(monkeypatch) -> None:  # noqa: ANN001
    def _raise() -> str:
        raise RuntimeError("clipboard secret value")

    monkeypatch.setitem(sys.modules, "pyperclip", SimpleNamespace(paste=_raise))

    result = execute_desktop_clipboard_effect(action="clipboard_read", payload={})

    assert result.reason_code == DESKTOP_CLIPBOARD_FAILED
    assert result.detail.endswith("RuntimeError")
    assert "clipboard secret value" not in result.detail
