"""CodeStudio WorkOrder builder."""

from __future__ import annotations

from typing import Any

from host_brain.agent_runtime.work_order import build_work_order_id


def build_code_work_order(args: dict[str, Any], *, run_mode: str, skill_id: str, require_confirm: bool) -> dict[str, Any]:
    task = str(args.get("task") or args.get("objective") or args.get("text") or args.get("user_message") or "").strip()
    workspace = args.get("workspace") if isinstance(args.get("workspace"), dict) else None
    if workspace is None:
        raw_workspace = str(args.get("workspace_path") or args.get("path") or "").strip()
        workspace_strategy = str(args.get("workspace_strategy") or "").strip()
        if not workspace_strategy and bool(args.get("allow_temp_workspace")):
            workspace_strategy = "temp_workspace"
        if not workspace_strategy:
            workspace_strategy = "runtime_repo" if run_mode != "inspect" else "temp_workspace"
        workspace = {"path": raw_workspace} if raw_workspace else {"strategy": workspace_strategy}
    can_write = run_mode in {"worker", "self_heal", "self_evolve"}
    return {
        "work_order_id": str(args.get("work_order_id") or build_work_order_id(skill_id)),
        "domain": "code" if run_mode not in {"self_heal", "self_evolve"} else ("immune" if run_mode == "self_heal" else "evolution"),
        "run_mode": run_mode,
        "objective": task,
        "workspace": workspace,
        "input_pack": {
            "user_request": task,
            "context": str(args.get("context") or "").strip(),
            "evidence_refs": list(args.get("evidence_refs") or []) if isinstance(args.get("evidence_refs"), list) else [],
        },
        "allowed_actions": ["read_files", "generate_report"] + (["write_files", "run_tests"] if can_write else []),
        "forbidden_actions": ["git_push", "delete_untracked_large_dirs", "modify_secret_files", "bypass_immune_gate"],
        "acceptance_criteria": list(args.get("acceptance_criteria") or []) if isinstance(args.get("acceptance_criteria"), list) else [],
        "verification_plan": dict(args.get("verification_plan") or {}),
        "approval_policy": {"require_confirm": bool(require_confirm), "confirmed": bool(args.get("confirmed") or args.get("approval_confirmed"))},
        "review_policy": {"review_required": bool(can_write)},
        "metadata": {
            "skill_id": skill_id,
            "request_id": args.get("request_id"),
            "timeout_seconds": args.get("timeout_seconds"),
        },
    }


__all__ = ["build_code_work_order"]
