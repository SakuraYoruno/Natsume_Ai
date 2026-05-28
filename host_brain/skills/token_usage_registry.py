"""Token usage observability Skill provider."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from host_brain.observability.token_usage import build_token_usage_report
from host_brain.skills.registry import SkillRegistry, skill


def _resolve_db_path(args: dict[str, Any], ctx: dict[str, Any] | None) -> str:
    raw = (
        (args or {}).get("db_path")
        or (ctx or {}).get("audit_db_path")
        or os.environ.get("Natsume_AUDIT_DB")
        or "host_brain/data/audit.sqlite3"
    )
    return str(Path(str(raw)).expanduser())


def _int_arg(args: dict[str, Any], name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int((args or {}).get(name) or default)))
    except (TypeError, ValueError):
        return default


class TokenUsageSkillProvider:
    @skill(
        skill_name="llm_token_usage_report",
        clearance="LOW",
        risk_level=1,
        scope="system",
        reversible=True,
        require_confirm=False,
        description="Read-only LLM token usage and pool-call hotspot report from the audit ledger.",
        family="observability",
        capability_tags=("llm", "tokens", "audit", "cost"),
        parallelizable=True,
        fallback_policy="chat_fallback",
    )
    def llm_token_usage_report(self, args: dict[str, Any], ctx: dict[str, Any] | None = None) -> dict[str, Any]:
        args = dict(args or {})
        report = build_token_usage_report(
            db_path=_resolve_db_path(args, ctx),
            start_ms=args.get("start_ms"),
            end_ms=args.get("end_ms"),
            window_hours=_int_arg(args, "window_hours", _int_arg(args, "hours", 24, 1, 24 * 31), 1, 24 * 31),
            top_n=_int_arg(args, "top_n", 10, 1, 50),
        )
        return {
            "status": "OK",
            "tool": "llm_token_usage",
            "action": "report",
            "report": report,
            "manifest_ref": "llm_token_usage_report::audit",
            "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
        }


def build_token_usage_skill_registry(provider: TokenUsageSkillProvider | None = None) -> SkillRegistry:
    registry = SkillRegistry()
    registry.register_decorated(provider or TokenUsageSkillProvider())
    return registry
