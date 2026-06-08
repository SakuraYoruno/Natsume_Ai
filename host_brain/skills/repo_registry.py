"""Repository doctor skill pack."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from host_brain.skills.declarative import DeclarativeSkillProvider, DeclarativeSkillSpec
from host_brain.skills.registry import SkillRegistry


def _normalize_repo_root(args: dict[str, Any], ctx: dict[str, Any] | None) -> str:
    raw = str(
        (args or {}).get("repo_root")
        or (args or {}).get("path")
        or (args or {}).get("cwd")
        or (ctx or {}).get("repo_root")
        or ""
    ).strip()
    if raw:
        return str(Path(raw).expanduser())
    return str(Path.cwd())


def _normalize_repo_health_scan(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    try:
        max_files = max(1, min(20000, int((args or {}).get("max_files") or 5000)))
    except (TypeError, ValueError):
        max_files = 5000
    run_tests_raw = (args or {}).get("run_tests")
    run_tests = bool(run_tests_raw) if run_tests_raw is not None else False
    timeout_raw = (args or {}).get("timeout_ms")
    try:
        timeout_ms = max(1000, min(300000, int(timeout_raw or 30000)))
    except (TypeError, ValueError):
        timeout_ms = 30000

    repo_root = _normalize_repo_root(args or {}, ctx)
    return {
        "repo_root": repo_root,
        "include_git": bool((args or {}).get("include_git", True)),
        "include_tests": bool((args or {}).get("include_tests", True)),
        "include_python": bool((args or {}).get("include_python", True)),
        "run_tests": run_tests,
        "test_command": str((args or {}).get("test_command") or "pytest -q tests/unit").strip(),
        "timeout_ms": timeout_ms,
        "max_files": max_files,
        "manifest_ref": f"repo_health_scan::{repo_root}",
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
        "_context": ctx or {},
    }


def _normalize_test_failure_triage(args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
    output = str(
        (args or {}).get("output")
        or (args or {}).get("test_output")
        or (args or {}).get("log")
        or (args or {}).get("text")
        or (args or {}).get("user_message")
        or ""
    )
    try:
        max_failures = max(1, min(100, int((args or {}).get("max_failures") or 12)))
    except (TypeError, ValueError):
        max_failures = 12
    return {
        "output": output,
        "command": str((args or {}).get("command") or "").strip() or None,
        "max_failures": max_failures,
        "manifest_ref": f"test_failure_triage::{len(output)}",
        "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
        "_context": ctx or {},
    }


class RepoSkillProvider(DeclarativeSkillProvider):
    skill_specs = (
        DeclarativeSkillSpec(
            skill_name="repo_health_scan",
            clearance="LOW",
            risk_level=1,
            scope="system",
            reversible=True,
            description="只读扫描仓库健康：git 状态、关键配置、测试布局和可选测试命令摘要。",
            tool="repo",
            action="health_scan",
            normalize_args=_normalize_repo_health_scan,
            family="code",
            capability_tags=("repo", "health", "tests"),
        ),
        DeclarativeSkillSpec(
            skill_name="test_failure_triage",
            clearance="LOW",
            risk_level=1,
            scope="system",
            reversible=True,
            description="从 pytest/unittest 输出中提取失败测试、错误类型和下一步修复线索。",
            tool="repo",
            action="test_failure_triage",
            normalize_args=_normalize_test_failure_triage,
            family="code",
            capability_tags=("tests", "triage", "failure-analysis"),
        ),
    )


def build_repo_skill_registry(provider: RepoSkillProvider | None = None) -> SkillRegistry:
    registry = SkillRegistry()
    registry.register_decorated(provider or RepoSkillProvider())
    return registry
