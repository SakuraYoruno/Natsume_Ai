from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import host_brain.app.dependencies as dependencies
from host_brain.app.tool_execution_matrix import (
    build_browser_observation_matrix,
    build_default_tool_execution_matrix,
    run_memory_tool_closed_loop,
    run_tool_execution_matrix,
)
from host_brain.skills.impl import browser_executor


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


def test_browser_observation_matrix_runs_safe_browser_probe_path(monkeypatch) -> None:
    _prepare_registry(monkeypatch)

    def fake_navigate(payload):
        return {
            "status": browser_executor.STATUS_EXECUTED,
            "manifest_ref": "browser_navigate::unit",
            "backend": "unit",
            "url": payload["url"],
            "title": "Unit Browser Probe",
        }

    def fake_scrape(payload):
        return {
            "status": browser_executor.STATUS_EXECUTED,
            "manifest_ref": "browser_scrape::unit",
            "backend": "unit",
            "selector": payload["selector"],
            "results": ["Unit Browser Probe"],
            "count": 1,
        }

    monkeypatch.setitem(browser_executor._ACTION_MAP, "navigate", fake_navigate)
    monkeypatch.setitem(browser_executor._ACTION_MAP, "scrape", fake_scrape)

    try:
        summary = run_tool_execution_matrix(
            build_browser_observation_matrix("https://example.test/"),
            request_prefix="unit-browser-matrix",
            session_id="unit-browser-matrix-session",
            person_id="unit-browser-matrix-person",
        )
    finally:
        dependencies._skill_registry = None

    assert summary["status"] == "PASSED"
    assert summary["total"] == 2
    assert summary["passed"] == 2
    assert {item["skill_name"]: item.get("handler_tool") for item in summary["results"]} == {
        "browser_navigate": "browser",
        "browser_scrape": "browser",
    }


def test_memory_tool_closed_loop_stores_then_recalls_through_executive_runtime(monkeypatch) -> None:
    _prepare_registry(monkeypatch)
    writes: list[dict[str, object]] = []

    class _StoreStub:
        def write_memory(
            self,
            *,
            text: str,
            evidence_refs: list[str],
            request_id: str | None = None,
            intent_id: str | None = None,
            emotion_tag: str = "NEUTRAL",
            **_: object,
        ) -> SimpleNamespace:
            writes.append(
                {
                    "text": text,
                    "evidence_refs": list(evidence_refs),
                    "request_id": request_id,
                    "intent_id": intent_id,
                    "emotion_tag": emotion_tag,
                }
            )
            return SimpleNamespace(success=True, reason_code=None, detail="ok", memory_id="mem-unit-001")

    class _SemanticRecallStub:
        async def recall(
            self,
            query: str,
            *,
            top_k: int = 5,
            threshold: float | None = None,
            filter_metadata: dict[str, object] | None = None,
        ) -> SimpleNamespace:
            _ = query, top_k, threshold, filter_metadata
            text = str(writes[-1]["text"]) if writes else ""
            return SimpleNamespace(
                hits=[
                    SimpleNamespace(
                        doc_id="mem-unit-001",
                        content=text,
                        score=0.99,
                        metadata={"source": "unit"},
                        source="semantic_recaller",
                    )
                ]
            )

    monkeypatch.setattr(dependencies, "get_episodic_store", lambda: _StoreStub())
    monkeypatch.setattr(dependencies, "get_semantic_recaller", lambda: _SemanticRecallStub())

    try:
        summary = run_memory_tool_closed_loop(
            content="Blue remembers the tool matrix closed loop",
            query="tool matrix closed loop",
            request_prefix="unit-memory-loop",
            session_id="unit-memory-loop-session",
            person_id="unit-memory-loop-person",
        )
    finally:
        dependencies._skill_registry = None

    assert summary["status"] == "PASSED"
    assert summary["store"]["success"] is True
    assert summary["search"]["success"] is True
    assert summary["search"]["recalled"] is True
    assert writes[0]["text"] == "Blue remembers the tool matrix closed loop"
    assert "source:tool_execution_matrix" in writes[0]["evidence_refs"]
