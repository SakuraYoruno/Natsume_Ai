"""ImmunePlane WorkOrder builders."""

from __future__ import annotations

from typing import Any

from host_brain.agent_runtime.work_order import build_work_order_id


def _status_from_report(report: dict[str, Any], source: str) -> str:
    if source == "readiness":
        return str(report.get("readiness_status") or report.get("status") or "UNKNOWN").upper()
    return str(report.get("status") or "UNKNOWN").upper()


def _affected_components(report: dict[str, Any]) -> list[str]:
    components: list[str] = []
    services = report.get("services")
    rows = services.get("rows") if isinstance(services, dict) else services
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            status = str(row.get("status") or "").upper()
            available = row.get("available")
            if status in {"DOWN", "DEGRADED", "FAILED"} or available is False:
                service_id = str(row.get("service_id") or row.get("component_id") or "").strip()
                if service_id:
                    components.append(service_id)
    return list(dict.fromkeys(components))


def build_immune_proposal_from_report(report: dict[str, Any], *, source: str = "health") -> dict[str, Any]:
    source_key = "readiness" if str(source or "").strip().lower() in {"ready", "readiness"} else "health"
    payload = dict(report or {})
    status = _status_from_report(payload, source_key)
    triggered = status not in {"OK", "UP", "READY", "HEALTHY"}
    reason_code = str(payload.get("reason_code") or "NO_REASON").strip() or "NO_REASON"
    components = _affected_components(payload)
    objective = (
        f"Diagnose {source_key} degradation {reason_code}; affected components: {', '.join(components) or 'unknown'}."
        if triggered
        else f"{source_key} is healthy; no immune WorkOrder is required."
    )
    work_order = None
    if triggered:
        work_order = {
            "work_order_id": build_work_order_id("immune"),
            "domain": "immune",
            "run_mode": "self_heal",
            "objective": objective,
            "workspace": {"strategy": "runtime_repo"},
            "input_pack": {
                "user_request": objective,
                "runtime_logs": [],
                "evidence_refs": [f"runtime:{source_key}", f"reason:{reason_code}"],
                "constraints": ["Do not repair pure external outages by editing host code."],
                "context": str(payload)[:12_000],
            },
            "allowed_actions": ["read_files", "write_files", "run_tests", "generate_report"],
            "forbidden_actions": [
                "git_push",
                "delete_untracked_large_dirs",
                "modify_secret_files",
                "change_personality_core_without_review",
                "bypass_immune_gate",
            ],
            "acceptance_criteria": [
                "Root cause is described.",
                "Any code change is minimal and bounded.",
                "Verification status is recorded.",
            ],
            "verification_plan": {"status_required": True},
            "approval_policy": {"require_confirm": True, "confirmed": False},
            "review_policy": {"review_required": True},
            "metadata": {"source": source_key, "reason_code": reason_code},
        }
    return {
        "triggered": bool(triggered),
        "source": source_key,
        "status": status,
        "reason_code": reason_code,
        "affected_components": components,
        "summary": objective,
        "work_order": work_order,
    }


__all__ = ["build_immune_proposal_from_report"]
