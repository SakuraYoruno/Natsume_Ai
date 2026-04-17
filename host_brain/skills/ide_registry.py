"""IDE skill pack: Cursor / PyCharm / Codex wrappers."""

from __future__ import annotations

from typing import Any

from host_brain.skills.declarative import DeclarativeSkillProvider, DeclarativeSkillSpec
from host_brain.skills.registry import SkillRegistry


def _coalesce_codex_request(args: dict[str, Any], ctx: dict[str, Any] | None) -> str:
    merged = dict(args or {})
    if isinstance(ctx, dict):
        merged.setdefault("task_text", ctx.get("task_text"))
        merged.setdefault("user_message", ctx.get("user_message"))
        merged.setdefault("query", ctx.get("query"))
    for key in ("task", "task_text", "text", "user_message", "query"):
        candidate = str(merged.get(key) or "").strip()
        if candidate:
            return candidate
    return ""


def _normalize_open_cursor(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "path": str((args or {}).get("path") or "").strip(),
        "workspace": str((args or {}).get("workspace") or "").strip() or None,
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
    }


def _normalize_run_pycharm_action(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "action": str((args or {}).get("action_id") or "").strip(),
        "project_path": str((args or {}).get("project_path") or "").strip() or None,
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
    }


def _normalize_codex_codegen(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    merged_args = dict(args or {})
    task = _coalesce_codex_request(merged_args, ctx) or str(merged_args.get("problem_statement") or "").strip()
    context = str(merged_args.get("context") or "").strip()
    mode = str(merged_args.get("mode") or "").strip().lower() or None
    workspace = (
        str(merged_args.get("workspace") or "").strip()
        or str((ctx or {}).get("workspace") or "").strip()
        or None
    )
    run_mode = str(merged_args.get("run_mode") or merged_args.get("execution_mode") or "").strip().lower() or None
    repo_alias = str(merged_args.get("repo_alias") or "").strip() or None
    file_path = str(merged_args.get("file_path") or merged_args.get("path") or "").strip() or None
    git_url = str(merged_args.get("git_url") or "").strip() or None
    clone_name = str(merged_args.get("clone_name") or "").strip() or None
    resume_thread_id = (
        str(merged_args.get("resume_thread_id") or merged_args.get("thread_id") or "").strip() or None
    )
    verification_code = str(merged_args.get("verification_code") or "").strip() or None
    workspace_strategy = str(merged_args.get("workspace_strategy") or "").strip() or None
    problem_statement = str(merged_args.get("problem_statement") or "").strip() or None
    run_checks = bool(merged_args.get("run_checks"))
    include_dependency_probes = bool(merged_args.get("include_dependency_probes"))
    timeout_seconds = merged_args.get("timeout_seconds")
    evidence_refs = list(merged_args.get("evidence_refs") or []) if isinstance(merged_args.get("evidence_refs"), list) else None
    return {
        "task": task,
        "context": context or None,
        "mode": mode or "codegen",
        "workspace": workspace,
        "workspace_strategy": workspace_strategy,
        "run_mode": run_mode,
        "problem_statement": problem_statement,
        "run_checks": run_checks,
        "include_dependency_probes": include_dependency_probes,
        "timeout_seconds": timeout_seconds,
        "evidence_refs": evidence_refs,
        "repo_alias": repo_alias,
        "file_path": file_path,
        "git_url": git_url,
        "clone_name": clone_name,
        "resume_thread_id": resume_thread_id,
        "verification_code": verification_code,
        "allow_temp_workspace": bool(merged_args.get("allow_temp_workspace")),
        "create_workspace": bool(merged_args.get("create_workspace")),
        "repo_alias_file": str(merged_args.get("repo_alias_file") or "").strip() or None,
        "clone_root": str(merged_args.get("clone_root") or "").strip() or None,
        "runs_root": str(merged_args.get("runs_root") or "").strip() or None,
        "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
    }


def _normalize_open_file(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    path = str((args or {}).get("path") or "").strip()
    editor = str((args or {}).get("editor") or "default").strip().lower()
    line = (args or {}).get("line")
    column = (args or {}).get("column")
    try:
        line_num = int(line) if line is not None else None
    except (TypeError, ValueError):
        line_num = None
    try:
        col_num = int(column) if column is not None else None
    except (TypeError, ValueError):
        col_num = None
    return {
        "path": path,
        "editor": editor,
        "line": line_num,
        "column": col_num,
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
    }


class IDESkillProvider(DeclarativeSkillProvider):
    skill_specs = (
        DeclarativeSkillSpec(
            skill_name="open_cursor",
            method_name="open_cursor",
            clearance="LOW",
            risk_level=2,
            scope="system",
            reversible=True,
            description="Open a file or project in Cursor.",
            tool="cursor",
            action="open",
            normalize_args=_normalize_open_cursor,
        ),
        DeclarativeSkillSpec(
            skill_name="run_pycharm_action",
            method_name="run_pycharm_action",
            clearance="MEDIUM",
            risk_level=3,
            scope="system",
            reversible=True,
            description="Trigger a PyCharm action by action_id.",
            tool="pycharm",
            action=None,
            normalize_args=_normalize_run_pycharm_action,
        ),
        DeclarativeSkillSpec(
            skill_name="codex_codegen",
            method_name="codex_codegen",
            clearance="MEDIUM",
            risk_level=3,
            scope="host",
            reversible=True,
            description="Generate or repair code by calling the local Codex CLI in inspect or worker mode.",
            tool="codex",
            action=None,
            normalize_args=_normalize_codex_codegen,
            family="code",
            capability_tags=("coding", "tooling", "codex"),
            fallback_policy="strict_fail",
        ),
        DeclarativeSkillSpec(
            skill_name="open_file_in_editor",
            method_name="open_file_in_editor",
            clearance="LOW",
            risk_level=2,
            scope="system",
            reversible=True,
            description="Open a file in the preferred editor (auto-detected or specified via 'editor' arg).",
            tool="open_file_in_editor",
            action=None,
            normalize_args=_normalize_open_file,
        ),
    )


def build_ide_skill_registry(provider: IDESkillProvider | None = None) -> SkillRegistry:
    registry = SkillRegistry()
    registry.register_decorated(provider or IDESkillProvider())
    return registry
