"""SkillForgeStudio WorkOrder builder."""

from __future__ import annotations

from typing import Any

from host_brain.contracts.agent_runtime.work_order import build_work_order_id


def build_skill_forge_work_order(args: dict[str, Any]) -> dict[str, Any]:
    objective = str(args.get("objective") or args.get("task") or args.get("skill_request") or "").strip()
    return {
        "work_order_id": str(args.get("work_order_id") or build_work_order_id("skill_forge")),
        "domain": "skill",
        "run_mode": "worker",
        "objective": objective,
        "workspace": dict(args.get("workspace") or {"strategy": "runtime_repo"}),
        "input_pack": {"user_request": objective, "context": str(args.get("context") or "")},
        "allowed_actions": ["read_files", "write_files", "run_tests", "generate_report"],
        "forbidden_actions": ["git_push", "modify_secret_files", "bypass_immune_gate"],
        "acceptance_criteria": ["Generate an installation plan before writing runtime registration changes."],
        "verification_plan": dict(args.get("verification_plan") or {}),
        "approval_policy": {"require_confirm": True, "confirmed": bool(args.get("confirmed") or args.get("approval_confirmed"))},
        "review_policy": {"review_required": True},
        "metadata": {"skill_id": "agent_skill_forge", "request_id": args.get("request_id")},
    }


__all__ = ["build_skill_forge_work_order"]
