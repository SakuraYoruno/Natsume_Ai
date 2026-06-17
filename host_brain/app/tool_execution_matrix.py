"""Deterministic probes for the canonical tool execution loop.

The matrix exercises Thalamus-facing skills through the same executive
runtime path used by chat/Gateway requests:

intent draft -> skill registry -> skill handler -> tool executor -> outcome.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from host_brain.app.executive_runtime_core import _execute_skill


SAFE_SUCCESS_STATUSES = frozenset({"OK", "EXECUTED", "SUCCESS"})
DEFAULT_MATRIX_SESSION_ID = "tool-execution-matrix"
DEFAULT_MATRIX_PERSON_ID = "matrix-person"
DEFAULT_MEMORY_CLOSED_LOOP_CONTENT = "tool execution matrix memory closed loop sentinel"


@dataclass(frozen=True)
class ToolExecutionProbe:
    probe_id: str
    skill_name: str
    args: dict[str, Any] = field(default_factory=dict)
    expected_family: str | None = None
    expected_scope: str | None = None
    expected_tool: str | None = None
    value_tier: str = "core"
    side_effect_level: str = "read_only"
    requires_confirm: bool = False
    critical: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "probe_id": self.probe_id,
            "skill_name": self.skill_name,
            "args": dict(self.args),
            "expected_family": self.expected_family,
            "expected_scope": self.expected_scope,
            "expected_tool": self.expected_tool,
            "value_tier": self.value_tier,
            "side_effect_level": self.side_effect_level,
            "requires_confirm": self.requires_confirm,
            "critical": self.critical,
        }


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_read_target(repo_root: Path) -> Path:
    for name in ("README.md", "Soul.md", ".env.example"):
        candidate = repo_root / name
        if candidate.exists() and candidate.is_file():
            return candidate
    return Path(__file__).resolve()


def _default_frontend_root(repo_root: Path) -> Path:
    candidate = repo_root / "host_brain" / "app" / "monitor_ui"
    return candidate if candidate.exists() else repo_root


def build_default_tool_execution_matrix(repo_root: str | Path | None = None) -> list[ToolExecutionProbe]:
    """Return safe, high-value probes for the first tool-execution acceptance gate."""

    root = Path(repo_root).resolve() if repo_root is not None else _repo_root()
    read_target = _default_read_target(root)
    frontend_root = _default_frontend_root(root)
    frontend_source_root = frontend_root / "src" if (frontend_root / "src").exists() else frontend_root
    return [
        ToolExecutionProbe(
            probe_id="builtin-read-only-status",
            skill_name="read_only_status",
            expected_scope="host",
            value_tier="liveness",
        ),
        ToolExecutionProbe(
            probe_id="builtin-session-summary",
            skill_name="session_summary",
            args={"user_message": "tool execution matrix"},
            expected_scope="host",
            value_tier="context",
        ),
        ToolExecutionProbe(
            probe_id="builtin-emotion-query",
            skill_name="emotion_query",
            expected_scope="host",
            value_tier="self_state",
        ),
        ToolExecutionProbe(
            probe_id="builtin-bond-query",
            skill_name="bond_query",
            expected_scope="host",
            value_tier="relationship",
        ),
        ToolExecutionProbe(
            probe_id="memory-preference-extract",
            skill_name="memory_preference_extract",
            args={
                "text": "我喜欢冷色系 UI，不喜欢夸张动效。以后请直接给结论。",
                "max_candidates": 5,
                "source": "tool_execution_matrix",
            },
            expected_scope="memory",
            expected_tool="memory",
            value_tier="memory_steward",
        ),
        ToolExecutionProbe(
            probe_id="memory-consolidate-candidates",
            skill_name="memory_consolidate",
            args={
                "text": "今天确认主链工具闭环通过。下一步需要扩充有价值的 Host 侧技能。",
                "max_candidates": 5,
                "source": "tool_execution_matrix",
            },
            expected_scope="memory",
            expected_tool="memory",
            value_tier="memory_steward",
        ),
        ToolExecutionProbe(
            probe_id="repo-health-scan",
            skill_name="repo_health_scan",
            args={
                "repo_root": str(root),
                "include_git": True,
                "include_tests": True,
                "include_python": True,
                "run_tests": False,
                "max_files": 5000,
            },
            expected_scope="system",
            expected_tool="repo",
            value_tier="repo_doctor",
        ),
        ToolExecutionProbe(
            probe_id="test-failure-triage",
            skill_name="test_failure_triage",
            args={
                "output": (
                    "FAILED tests/unit/test_demo.py::test_contract - AssertionError\n"
                    "E   AssertionError: expected OK\n"
                    "1 failed, 4 passed in 0.12s"
                ),
                "command": "pytest -q tests/unit/test_demo.py",
                "max_failures": 5,
            },
            expected_scope="system",
            expected_tool="repo",
            value_tier="repo_doctor",
        ),
        ToolExecutionProbe(
            probe_id="frontend-route-audit",
            skill_name="frontend_route_audit",
            args={
                "app_root": str(frontend_root),
                "source_root": str(frontend_source_root),
                "include_static": False,
                "max_files": 800,
            },
            expected_scope="system",
            expected_tool="frontend",
            value_tier="frontend_audit",
        ),
        ToolExecutionProbe(
            probe_id="frontend-component-value-audit",
            skill_name="frontend_component_value_audit",
            args={
                "source_root": str(frontend_source_root),
                "min_repeated_label_count": 5,
                "max_card_surfaces_per_file": 35,
                "max_files": 800,
            },
            expected_scope="system",
            expected_tool="frontend",
            value_tier="frontend_audit",
        ),
        ToolExecutionProbe(
            probe_id="system-info",
            skill_name="system_info",
            args={
                "include_cpu": False,
                "include_memory": True,
                "include_disk": False,
                "include_network": False,
                "include_gpu": False,
                "include_runtime_vitals": False,
            },
            expected_scope="system",
            expected_tool="system",
            value_tier="system_observation",
        ),
        ToolExecutionProbe(
            probe_id="system-resource-snapshot",
            skill_name="system_resource_snapshot",
            args={
                "include_gpu": True,
                "include_processes": True,
                "include_network": False,
                "top_n": 3,
                "sample_seconds": 0.0,
            },
            expected_scope="system",
            expected_tool="system",
            value_tier="system_resource_management",
        ),
        ToolExecutionProbe(
            probe_id="system-process-resource-top",
            skill_name="process_resource_top",
            args={"limit": 3, "sort_by": "memory", "include_gpu": True, "sample_seconds": 0.0},
            expected_scope="system",
            expected_tool="system",
            value_tier="system_resource_management",
        ),
        ToolExecutionProbe(
            probe_id="system-resource-pressure-analysis",
            skill_name="resource_pressure_analysis",
            args={"top_n": 8, "sample_seconds": 0.0, "persist": False},
            expected_scope="system",
            expected_tool="system",
            value_tier="system_resource_management",
        ),
        ToolExecutionProbe(
            probe_id="system-resource-manager-status",
            skill_name="resource_manager_status",
            args={"create_if_missing": False},
            expected_scope="system",
            expected_tool="system",
            value_tier="system_resource_management",
        ),
        ToolExecutionProbe(
            probe_id="system-process-list",
            skill_name="process_list",
            args={"limit": 5, "sort_by": "pid"},
            expected_scope="system",
            expected_tool="system",
            value_tier="system_observation",
        ),
        ToolExecutionProbe(
            probe_id="system-network-status",
            skill_name="network_status",
            args={"host": "127.0.0.1", "port": 80, "timeout_ms": 500, "get_public_ip": False},
            expected_scope="system",
            expected_tool="system",
            value_tier="connectivity",
        ),
        ToolExecutionProbe(
            probe_id="file-list-repo-root",
            skill_name="file_list",
            args={"path": str(root), "pattern": "*", "recursive": False, "include_hidden": False, "max_items": 20},
            expected_scope="system",
            expected_tool="file",
            value_tier="workspace_observation",
        ),
        ToolExecutionProbe(
            probe_id="file-read-reference",
            skill_name="file_read",
            args={"path": str(read_target), "encoding": "utf-8", "max_bytes": 4096, "offset_line": 1, "limit_lines": 80},
            expected_scope="system",
            expected_tool="file",
            value_tier="workspace_observation",
        ),
    ]


def build_browser_observation_matrix(url: str = "data:text/html,<h1>Natsume Browser Probe</h1>") -> list[ToolExecutionProbe]:
    """Return safe browser observation probes.

    These are kept out of the default matrix because real browser availability
    depends on the local desktop/Playwright/playwright setup.
    """

    return [
        ToolExecutionProbe(
            probe_id="browser-navigate",
            skill_name="browser_navigate",
            args={"url": url, "wait_until": "load", "timeout_ms": 5000},
            expected_scope="system",
            expected_tool="browser",
            value_tier="web_observation",
        ),
        ToolExecutionProbe(
            probe_id="browser-scrape-body",
            skill_name="browser_scrape",
            args={"selector": "body", "extract": "text", "limit": 5},
            expected_scope="system",
            expected_tool="browser",
            value_tier="web_observation",
        ),
    ]


def _raw_executor_result(payload: dict[str, Any]) -> dict[str, Any]:
    result = payload.get("result")
    return dict(result or {}) if isinstance(result, dict) else {}


def _is_raw_status_success(raw_result: dict[str, Any]) -> bool:
    raw_status = str(raw_result.get("status") or "").strip().upper()
    if not raw_status:
        return True
    return raw_status in SAFE_SUCCESS_STATUSES


def run_tool_execution_matrix(
    probes: Iterable[ToolExecutionProbe] | None = None,
    *,
    request_prefix: str = "tool-matrix",
    session_id: str = DEFAULT_MATRIX_SESSION_ID,
    person_id: str = DEFAULT_MATRIX_PERSON_ID,
) -> dict[str, Any]:
    """Execute probes through the canonical executive skill path."""

    from host_brain.app.dependencies import get_skill_registry  # noqa: PLC0415

    selected = list(probes) if probes is not None else build_default_tool_execution_matrix()
    registry = get_skill_registry()
    results: list[dict[str, Any]] = []

    for index, probe in enumerate(selected, start=1):
        skill_name = probe.skill_name
        request_id = f"{request_prefix}-{index}-{probe.probe_id}"
        result: dict[str, Any] = {
            **probe.to_dict(),
            "request_id": request_id,
            "registered": False,
            "passed": False,
            "status": "NOT_RUN",
            "error": "",
        }

        try:
            meta = registry.require(skill_name)
            result.update(
                {
                    "registered": True,
                    "family": str(meta.family or "general"),
                    "scope": str(meta.scope or ""),
                    "risk_level": int(meta.risk_level),
                    "requires_confirm_actual": bool(meta.require_confirm),
                }
            )
            if bool(meta.require_confirm) or probe.requires_confirm:
                result.update({"status": "SKIPPED_CONFIRM_REQUIRED", "error": "confirm required"})
                results.append(result)
                continue
            if probe.expected_scope and str(meta.scope or "") != probe.expected_scope:
                result.update({"status": "FAILED_SCOPE", "error": f"expected scope {probe.expected_scope}"})
                results.append(result)
                continue
            if probe.expected_family and str(meta.family or "general") != probe.expected_family:
                result.update({"status": "FAILED_FAMILY", "error": f"expected family {probe.expected_family}"})
                results.append(result)
                continue
            if probe.expected_tool:
                handler_preview = meta.handler(
                    dict(probe.args),
                    {
                        "request_id": request_id,
                        "session_id": session_id,
                        "person_id": person_id,
                        "user_message": f"tool execution probe {probe.probe_id}",
                    },
                )
                handler_tool = str(dict(handler_preview or {}).get("tool") or "")
                result["handler_tool"] = handler_tool
                if handler_tool != probe.expected_tool:
                    result.update({"status": "FAILED_TOOL", "error": f"expected tool {probe.expected_tool}"})
                    results.append(result)
                    continue

            outcome = _execute_skill(
                request_body={"args": dict(probe.args)},
                request_id=request_id,
                session_id=session_id,
                person_id=person_id,
                intent_id=f"intent::{request_id}",
                skill_id=skill_name,
                task_text=f"tool execution probe {probe.probe_id}",
                user_message=f"tool execution probe {probe.probe_id}",
                governor_result={"decision": "approve", "tokens": {}},
                intent_draft={"route": "skill", "target_id": skill_name, "args": dict(probe.args)},
                emotion_context={"mood_state": "NEUTRAL", "emotion_tag": "NEUTRAL"},
                render_target_lang="zh",
            )
            raw_result = _raw_executor_result(dict(outcome.payload or {}))
            raw_status_ok = _is_raw_status_success(raw_result)
            passed = bool(outcome.success) and raw_status_ok
            result.update(
                {
                    "passed": passed,
                    "status": "PASSED" if passed else "FAILED",
                    "outcome_success": bool(outcome.success),
                    "outcome_error": str(outcome.error_text or ""),
                    "latency_ms": int(outcome.latency_ms or 0),
                    "raw_status": str(raw_result.get("status") or ""),
                    "raw_reason_code": str(raw_result.get("reason_code") or ""),
                    "raw_manifest_ref": str(raw_result.get("manifest_ref") or ""),
                    "result_text_preview": str(outcome.result_text or "")[:500],
                }
            )
        except Exception as exc:
            result.update({"status": "FAILED_EXCEPTION", "error": str(exc)})
        results.append(result)

    passed_count = sum(1 for item in results if item.get("passed") is True)
    skipped_count = sum(1 for item in results if str(item.get("status") or "").startswith("SKIPPED"))
    failed_results = [item for item in results if item.get("passed") is not True and not str(item.get("status") or "").startswith("SKIPPED")]
    critical_failed = [item for item in failed_results if item.get("critical") is True]
    return {
        "status": "PASSED" if not critical_failed else "FAILED",
        "total": len(results),
        "passed": passed_count,
        "failed": len(failed_results),
        "skipped": skipped_count,
        "critical_failed": len(critical_failed),
        "results": results,
    }


def _extract_memory_results(raw_result: dict[str, Any]) -> list[dict[str, Any]]:
    detail = raw_result.get("detail")
    if not isinstance(detail, dict):
        return []
    raw_items = detail.get("results")
    if not isinstance(raw_items, list):
        return []
    return [dict(item or {}) for item in raw_items if isinstance(item, dict)]


def _extract_memory_detail(raw_result: dict[str, Any]) -> dict[str, Any]:
    detail = raw_result.get("detail")
    return dict(detail or {}) if isinstance(detail, dict) else {}


def run_memory_tool_closed_loop(
    *,
    content: str = DEFAULT_MEMORY_CLOSED_LOOP_CONTENT,
    query: str | None = None,
    request_prefix: str = "memory-tool-loop",
    session_id: str = DEFAULT_MATRIX_SESSION_ID,
    person_id: str = DEFAULT_MATRIX_PERSON_ID,
) -> dict[str, Any]:
    """Verify memory_store -> memory_search through the executive skill path.

    The function intentionally uses the runtime dependency hooks that
    _execute_skill uses in production. Tests can provide isolated in-memory
    backends by monkeypatching those hooks, while live runs use configured
    memory services.
    """

    search_query = str(query or content).strip()
    store_request_id = f"{request_prefix}-store"
    search_request_id = f"{request_prefix}-search"
    store_outcome = _execute_skill(
        request_body={
            "args": {
                "content": content,
                "memory_type": "episodic",
                "tags": ["task", "event"],
                "importance": 0.7,
                "source": "tool_execution_matrix",
            }
        },
        request_id=store_request_id,
        session_id=session_id,
        person_id=person_id,
        intent_id=f"intent::{store_request_id}",
        skill_id="memory_store",
        task_text=f"store memory probe: {content}",
        user_message=f"store memory probe: {content}",
        governor_result={"decision": "approve", "tokens": {}},
        intent_draft={
            "route": "skill",
            "target_id": "memory_store",
            "args": {
                "content": content,
                "memory_type": "episodic",
                "tags": ["task", "event"],
                "importance": 0.7,
                "source": "tool_execution_matrix",
            },
        },
        emotion_context={"mood_state": "NEUTRAL", "emotion_tag": "FOCUSED"},
        render_target_lang="zh",
    )
    search_outcome = _execute_skill(
        request_body={
            "args": {
                "query": search_query,
                "memory_type": "episodic",
                "search_scope": "recent_episode",
                "top_k": 5,
                "threshold": 0.0,
                "tags": ["task", "event"],
            }
        },
        request_id=search_request_id,
        session_id=session_id,
        person_id=person_id,
        intent_id=f"intent::{search_request_id}",
        skill_id="memory_search",
        task_text=f"search memory probe: {search_query}",
        user_message=f"search memory probe: {search_query}",
        governor_result={"decision": "approve", "tokens": {}},
        intent_draft={
            "route": "skill",
            "target_id": "memory_search",
            "args": {
                "query": search_query,
                "memory_type": "episodic",
                "search_scope": "recent_episode",
                "top_k": 5,
                "threshold": 0.0,
                "tags": ["task", "event"],
            },
        },
        emotion_context={"mood_state": "NEUTRAL", "emotion_tag": "FOCUSED"},
        render_target_lang="zh",
    )
    store_raw = _raw_executor_result(dict(store_outcome.payload or {}))
    search_raw = _raw_executor_result(dict(search_outcome.payload or {}))
    search_detail = _extract_memory_detail(search_raw)
    search_results = _extract_memory_results(search_raw)
    normalized_content = content.lower()
    recalled = any(normalized_content in str(item.get("content") or item.get("text") or "").lower() for item in search_results)
    passed = bool(store_outcome.success) and bool(search_outcome.success) and recalled
    return {
        "status": "PASSED" if passed else "FAILED",
        "passed": passed,
        "content": content,
        "query": search_query,
        "store": {
            "success": bool(store_outcome.success),
            "error": str(store_outcome.error_text or ""),
            "raw_status": str(store_raw.get("status") or ""),
            "raw_reason_code": str(store_raw.get("reason_code") or ""),
            "raw_manifest_ref": str(store_raw.get("manifest_ref") or ""),
        },
        "search": {
            "success": bool(search_outcome.success),
            "error": str(search_outcome.error_text or ""),
            "raw_status": str(search_raw.get("status") or ""),
            "raw_reason_code": str(search_raw.get("reason_code") or ""),
            "raw_manifest_ref": str(search_raw.get("manifest_ref") or ""),
            "source": str(search_detail.get("source") or ""),
            "search_scope": str(search_detail.get("search_scope") or ""),
            "result_count": len(search_results),
            "recalled": recalled,
        },
        "results": search_results,
    }

