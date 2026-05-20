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


def build_default_tool_execution_matrix(repo_root: str | Path | None = None) -> list[ToolExecutionProbe]:
    """Return safe, high-value probes for the first tool-execution acceptance gate."""

    root = Path(repo_root).resolve() if repo_root is not None else _repo_root()
    read_target = _default_read_target(root)
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
            probe_id="system-info",
            skill_name="system_info",
            args={
                "include_cpu": False,
                "include_memory": True,
                "include_disk": False,
                "include_network": False,
                "include_gpu": False,
            },
            expected_scope="system",
            expected_tool="system",
            value_tier="system_observation",
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
