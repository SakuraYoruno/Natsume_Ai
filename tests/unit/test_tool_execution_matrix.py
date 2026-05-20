from __future__ import annotations

from pathlib import Path

import host_brain.app.dependencies as dependencies
from host_brain.app.tool_execution_matrix import (
    build_default_tool_execution_matrix,
    run_tool_execution_matrix,
)


def _prepare_registry(monkeypatch) -> None:
    monkeypatch.setenv("HOST_SKILL_HUB_ENABLED", "0")
    monkeypatch.setenv("HOST_SKILL_CUSTOM_PROVIDERS_ENABLED", "0")
    monkeypatch.setenv("HOST_SKILL_MCP_BRIDGE_ENABLED", "0")
    dependencies._skill_registry = None


def test_default_tool_execution_matrix_is_safe_and_value_focused() -> None:
    probes = build_default_tool_execution_matrix(Path.cwd())
    names = {probe.skill_name for probe in probes}

    assert {
        "read_only_status",
        "session_summary",
        "emotion_query",
        "bond_query",
        "system_info",
        "process_list",
        "network_status",
        "file_list",
        "file_read",
    }.issubset(names)
    assert {"file_write", "file_append", "file_delete", "process_kill", "shell_run"}.isdisjoint(names)
    assert all(probe.side_effect_level == "read_only" for probe in probes)
    assert all(probe.requires_confirm is False for probe in probes)


def test_tool_execution_matrix_runs_safe_probes_through_executive_runtime(monkeypatch) -> None:
    _prepare_registry(monkeypatch)

    try:
        summary = run_tool_execution_matrix(
            build_default_tool_execution_matrix(Path.cwd()),
            request_prefix="unit-tool-matrix",
            session_id="unit-tool-matrix-session",
            person_id="unit-tool-matrix-person",
        )
    finally:
        dependencies._skill_registry = None

    assert summary["status"] == "PASSED"
    assert summary["total"] == 9
    assert summary["passed"] == 9
    assert summary["failed"] == 0
    assert summary["skipped"] == 0
    assert summary["critical_failed"] == 0
    assert all(item["registered"] is True for item in summary["results"])
    assert all(item["passed"] is True for item in summary["results"])
    assert {
        item["skill_name"]: item.get("handler_tool")
        for item in summary["results"]
        if item["skill_name"] in {"system_info", "process_list", "network_status", "file_list", "file_read"}
    } == {
        "system_info": "system",
        "process_list": "system",
        "network_status": "system",
        "file_list": "file",
        "file_read": "file",
    }
    assert {item["skill_name"] for item in summary["results"]} == {
        "read_only_status",
        "session_summary",
        "emotion_query",
        "bond_query",
        "system_info",
        "process_list",
        "network_status",
        "file_list",
        "file_read",
    }
