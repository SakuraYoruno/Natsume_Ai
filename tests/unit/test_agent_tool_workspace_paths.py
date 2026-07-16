from __future__ import annotations

from pathlib import Path
from typing import Any

from host_brain.agent_runtime.agent_tools import execute_agent_tool


def test_agent_file_tools_share_workspace_path_boundary(tmp_path: Path) -> None:
    workspace = tmp_path / "work"
    workspace.mkdir()

    written = execute_agent_tool(
        tool_id="agent_write_file",
        args={"path": "nested/example.py", "content": "answer = 42\n"},
        workspace=workspace,
    )
    read = execute_agent_tool(
        tool_id="agent_read_file",
        args={"path": "nested/example.py"},
        workspace=workspace,
    )
    listed = execute_agent_tool(
        tool_id="agent_list_files",
        args={"path": "nested"},
        workspace=workspace,
    )

    assert "error" not in written
    assert read["content"] == "answer = 42\n"
    assert listed["entries"] == [
        {
            "name": "example.py",
            "type": "file",
            "size_bytes": (workspace / "nested/example.py").stat().st_size,
        },
    ]


def test_agent_file_tools_reject_same_prefix_sibling_escape(tmp_path: Path) -> None:
    workspace = tmp_path / "work"
    sibling = tmp_path / "work-escape"
    workspace.mkdir()
    sibling.mkdir()
    (sibling / "secret.py").write_text("secret = True\n", encoding="utf-8")
    escape = f"../{sibling.name}/secret.py"

    read = execute_agent_tool(
        tool_id="agent_read_file",
        args={"path": escape},
        workspace=workspace,
    )
    write = execute_agent_tool(
        tool_id="agent_write_file",
        args={"path": escape, "content": "overwritten\n"},
        workspace=workspace,
    )
    listed = execute_agent_tool(
        tool_id="agent_list_files",
        args={"path": f"../{sibling.name}"},
        workspace=workspace,
    )

    assert "error" in read
    assert "error" in write
    assert "error" in listed
    assert (sibling / "secret.py").read_text(encoding="utf-8") == "secret = True\n"


def test_browser_screenshot_rejects_unsafe_save_path_before_executor(
    monkeypatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "work"
    workspace.mkdir()
    calls: list[dict[str, Any]] = []

    def fake_execute(payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(payload)
        return {"status": "ok"}

    monkeypatch.setattr(
        "host_brain.agent_runtime.agent_tools.execute_browser_skill",
        fake_execute,
    )

    result = execute_agent_tool(
        tool_id="browser_screenshot",
        args={"save_path": str(tmp_path / "outside.png")},
        workspace=workspace,
    )

    assert "error" in result
    assert calls == []


def test_browser_screenshot_passes_canonical_workspace_path_to_executor(
    monkeypatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "work"
    workspace.mkdir()
    calls: list[dict[str, Any]] = []

    def fake_execute(payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(payload)
        return {"status": "ok"}

    monkeypatch.setattr(
        "host_brain.agent_runtime.agent_tools.execute_browser_skill",
        fake_execute,
    )

    result = execute_agent_tool(
        tool_id="browser_screenshot",
        args={"save_path": "artifacts/page.png"},
        workspace=workspace,
    )

    assert result == {"status": "ok"}
    assert calls[0]["save_path"] == str((workspace / "artifacts/page.png").resolve())
    assert (workspace / "artifacts").is_dir()
