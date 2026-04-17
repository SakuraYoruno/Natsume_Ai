"""Helpers for building Codex self-heal and self-evolve task payloads."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from host_brain.core.ids import now_ms

_RUNTIME_REPO_ROOT = Path(__file__).resolve().parents[2]
_MAX_CONTEXT_CHARS = 12_000


def _compact_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return str(value)


def _safe_build_runtime_health_snapshot(*, request_id: str, include_dependency_probes: bool) -> dict[str, Any]:
    from host_brain.app.config import build_host_runtime_statuses, load_host_config  # noqa: PLC0415
    from host_brain.app.main import build_service_health_report  # noqa: PLC0415

    runtime_statuses: dict[str, Any] = {}
    dependency_error: str | None = None

    host_result = load_host_config()
    if host_result.success and host_result.config is not None:
        runtime_statuses.update(build_host_runtime_statuses(host_result.config))

    if include_dependency_probes:
        try:
            from host_brain.app.dependencies import collect_dependency_probes_sync  # noqa: PLC0415

            runtime_statuses.update(collect_dependency_probes_sync())
        except Exception as exc:  # pragma: no cover - runtime dependent
            dependency_error = str(exc)

    health_report = build_service_health_report(
        runtime_statuses=runtime_statuses,
        request_id=request_id,
        ledger=None,
        ts_ms=int(now_ms()),
    )
    return {
        "runtime_repo_root": str(_RUNTIME_REPO_ROOT),
        "health_status": str(health_report.get("status") or "UNKNOWN"),
        "health_reason_code": str(health_report.get("reason_code") or "").strip() or None,
        "dependency_probe_error": dependency_error,
        "health_report": health_report,
    }


def build_codex_runtime_task_args(
    *,
    request_id: str,
    user_message: str,
    run_mode: str,
    existing_args: Mapping[str, Any] | None = None,
    include_dependency_probes: bool = True,
) -> dict[str, Any]:
    merged = dict(existing_args or {})
    task_text = str(
        merged.get("task")
        or merged.get("problem_statement")
        or merged.get("text")
        or merged.get("user_message")
        or user_message
        or ""
    ).strip()
    snapshot = _safe_build_runtime_health_snapshot(
        request_id=request_id,
        include_dependency_probes=include_dependency_probes,
    )
    evidence_refs = [str(item).strip() for item in list(merged.get("evidence_refs") or []) if str(item).strip()]
    evidence_refs.extend(
        [
            "runtime:service_health",
            f"request:{request_id}",
            f"runtime_repo:{snapshot['runtime_repo_root']}",
        ]
    )
    if snapshot.get("health_status"):
        evidence_refs.append(f"health_status:{snapshot['health_status']}")
    if snapshot.get("health_reason_code"):
        evidence_refs.append(f"health_reason:{snapshot['health_reason_code']}")

    context_sections = [
        f"Runtime task mode: {run_mode}",
        f"Problem statement: {task_text}",
        f"Evidence refs: {', '.join(dict.fromkeys(evidence_refs))}",
        "Runtime health snapshot:",
        _compact_json(snapshot),
    ]
    merged.setdefault("task", task_text)
    merged.setdefault("problem_statement", task_text)
    merged["run_mode"] = str(run_mode or "").strip().lower() or "inspect"
    merged.setdefault("workspace_strategy", "runtime_repo")
    merged["evidence_refs"] = list(dict.fromkeys(evidence_refs))
    merged["user_message"] = str(merged.get("user_message") or user_message or "").strip()
    merged["text"] = str(merged.get("text") or user_message or task_text).strip()
    merged["context"] = "\n\n".join(part for part in context_sections if part).strip()[:_MAX_CONTEXT_CHARS]
    return merged


__all__ = ["build_codex_runtime_task_args"]
