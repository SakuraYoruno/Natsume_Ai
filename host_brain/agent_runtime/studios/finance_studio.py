"""FinanceStudio WorkOrder builder."""

from __future__ import annotations

from typing import Any

from host_brain.agent_runtime.work_order import build_work_order_id


def build_finance_work_order(args: dict[str, Any]) -> dict[str, Any]:
    objective = str(args.get("objective") or args.get("task") or args.get("query") or "").strip()
    return {
        "work_order_id": str(args.get("work_order_id") or build_work_order_id("finance")),
        "domain": "finance",
        "run_mode": "audit",
        "objective": objective,
        "workspace": dict(args.get("workspace") or {"strategy": "temp_workspace"}),
        "input_pack": {"user_request": objective, "context": str(args.get("context") or "")},
        "allowed_actions": ["read_files", "run_sandbox", "generate_report"],
        "forbidden_actions": ["write_files", "git_push", "provide_trading_advice", "modify_secret_files"],
        "acceptance_criteria": ["Return audit JSON plus readable explanation.", "Do not provide direct trading advice."],
        "verification_plan": dict(args.get("verification_plan") or {}),
        "approval_policy": {"require_confirm": False, "confirmed": True},
        "review_policy": {"review_required": False},
        "metadata": {"skill_id": "agent_sql_audit", "request_id": args.get("request_id")},
    }


__all__ = ["build_finance_work_order"]
