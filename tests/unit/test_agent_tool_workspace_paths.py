from __future__ import annotations

from pathlib import Path
from typing import Any

import host_brain.agent_runtime.agent_tools as agent_tools

from host_brain.agent_runtime.agent_tools import (
    AGENT_TOOL_GOVERNED_ADMISSION_REQUIRED,
    execute_agent_tool,
)


def _assert_retired(result: dict[str, Any], *, tool_id: str) -> None:
    assert result == {
        "error": AGENT_TOOL_GOVERNED_ADMISSION_REQUIRED,
        "reason_code": AGENT_TOOL_GOVERNED_ADMISSION_REQUIRED,
        "proposed_tool_id": tool_id,
        "result": None,
        "side_effects_executed": False,
        "required_execution_surface": "new immutable TaskGraph revision",
    }


def test_raw_agent_file_dispatcher_cannot_read_write_or_list(tmp_path: Path) -> None:
    workspace = tmp_path / "work"
    workspace.mkdir()

    for tool_id, args in (
        ("agent_write_file", {"path": "nested/example.py", "content": "answer = 42\n"}),
        ("agent_read_file", {"path": "nested/example.py"}),
        ("agent_list_files", {"path": "nested"}),
    ):
        _assert_retired(
            execute_agent_tool(tool_id=tool_id, args=args, workspace=workspace),
            tool_id=tool_id,
        )

    assert not (workspace / "nested").exists()


def test_raw_agent_dispatcher_cannot_escape_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "work"
    sibling = tmp_path / "work-escape"
    workspace.mkdir()
    sibling.mkdir()
    secret = sibling / "secret.py"
    secret.write_text("secret = True\n", encoding="utf-8")

    result = execute_agent_tool(
        tool_id="agent_write_file",
        args={"path": f"../{sibling.name}/secret.py", "content": "overwritten\n"},
        workspace=workspace,
    )

    _assert_retired(result, tool_id="agent_write_file")
    assert secret.read_text(encoding="utf-8") == "secret = True\n"


def test_raw_browser_dispatcher_has_no_provider_handler(tmp_path: Path) -> None:
    for tool_id in ("browser_screenshot", "browser_click", "browser_execute_script"):
        result = execute_agent_tool(
            tool_id=tool_id,
            args={"save_path": "artifacts/page.png"},
            workspace=tmp_path,
        )
        _assert_retired(result, tool_id=tool_id)

    assert not hasattr(agent_tools, "execute_browser_skill")
    assert not any(name.startswith("_tool_") for name in vars(agent_tools))
    assert not (tmp_path / "artifacts").exists()
