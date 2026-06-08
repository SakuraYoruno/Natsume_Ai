"""Repository doctor executor."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
import shlex
import subprocess
from typing import Any


STATUS_OK = "OK"
STATUS_FAILED = "FAILED"
_PYTEST_FAILED_RE = re.compile(r"FAILED\s+(?P<test_id>[^\s]+)")
_PYTEST_ERROR_RE = re.compile(r"ERROR\s+(?P<test_id>[^\s]+)")
_PYTEST_ERROR_TYPE_RE = re.compile(r"^\s*E\s+(?P<error>[A-Za-z_][\w.]*Error|AssertionError|Exception|RuntimeError)(?::|\s|$)")
_SUMMARY_ITEM_RE = re.compile(r"(?P<count>\d+)\s+(?P<kind>failed|passed|error|errors|skipped|xfailed|xpassed|warning|warnings)")
_TRACEBACK_FILE_RE = re.compile(r"(?P<path>[\w./\\-]+\.py):(?P<line>\d+):")


def execute_repo_skill(payload: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    _ = context
    action = str((payload or {}).get("action") or "").strip().lower()
    manifest_ref = str((payload or {}).get("manifest_ref") or f"repo::{action}")
    try:
        if action == "health_scan":
            detail = _execute_health_scan(payload or {})
        elif action == "test_failure_triage":
            detail = _execute_test_failure_triage(payload or {})
        else:
            detail = {"issue": "unknown repo action", "action": action}
        return {
            "status": STATUS_OK,
            "manifest_ref": manifest_ref,
            "action": action,
            "detail": detail,
        }
    except Exception as exc:
        return {
            "status": STATUS_FAILED,
            "manifest_ref": manifest_ref,
            "action": action,
            "error": str(exc),
        }


def _run_command(command: list[str], *, cwd: Path, timeout_ms: int) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1.0, timeout_ms / 1000.0),
            check=False,
        )
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "stdout": (completed.stdout or "").strip(),
            "stderr": (completed.stderr or "").strip(),
        }
    except FileNotFoundError as exc:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": str(exc)}
    except subprocess.TimeoutExpired:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": "command timed out"}


def _count_paths(root: Path, patterns: tuple[str, ...], *, max_files: int) -> int:
    count = 0
    for pattern in patterns:
        for _path in root.rglob(pattern):
            count += 1
            if count >= max_files:
                return count
    return count


def _has_any(root: Path, names: tuple[str, ...]) -> bool:
    return any((root / name).exists() for name in names)


def _git_snapshot(root: Path, timeout_ms: int) -> dict[str, Any]:
    inside = _run_command(["git", "rev-parse", "--is-inside-work-tree"], cwd=root, timeout_ms=timeout_ms)
    if not inside.get("ok") or str(inside.get("stdout") or "").lower() != "true":
        return {"inside_work_tree": False, "status_short": [], "dirty": False}
    status = _run_command(["git", "status", "--short"], cwd=root, timeout_ms=timeout_ms)
    branch = _run_command(["git", "branch", "--show-current"], cwd=root, timeout_ms=timeout_ms)
    last_commit = _run_command(["git", "log", "-1", "--oneline"], cwd=root, timeout_ms=timeout_ms)
    status_lines = [line for line in str(status.get("stdout") or "").splitlines() if line.strip()]
    return {
        "inside_work_tree": True,
        "branch": str(branch.get("stdout") or "").strip(),
        "last_commit": str(last_commit.get("stdout") or "").strip(),
        "dirty": bool(status_lines),
        "status_count": len(status_lines),
        "status_short": status_lines[:80],
    }


def _maybe_run_tests(payload: dict[str, Any], root: Path, timeout_ms: int) -> dict[str, Any] | None:
    if not bool(payload.get("run_tests")):
        return None
    command_text = str(payload.get("test_command") or "").strip()
    if not command_text:
        return None
    parts = shlex.split(command_text, posix=False)
    if not parts:
        return None
    allowed_prefixes = {"pytest", "python", "python3"}
    if parts[0].lower() not in allowed_prefixes:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": "test command prefix is not allowlisted"}
    return _run_command(parts, cwd=root, timeout_ms=timeout_ms)


def _execute_health_scan(payload: dict[str, Any]) -> dict[str, Any]:
    root = Path(str(payload.get("repo_root") or Path.cwd())).expanduser().resolve()
    timeout_ms = int(payload.get("timeout_ms") or 30000)
    max_files = int(payload.get("max_files") or 5000)
    issues: list[dict[str, str]] = []
    if not root.exists() or not root.is_dir():
        return {
            "health_status": "FAILED",
            "repo_root": str(root),
            "issues": [{"code": "REPO_ROOT_MISSING", "severity": "error", "message": "repo root does not exist"}],
        }

    detail: dict[str, Any] = {
        "repo_root": str(root),
        "files": {
            "has_readme": _has_any(root, ("README.md", "README.rst", "README.txt")),
            "has_pytest_ini": (root / "pytest.ini").exists(),
            "has_pyproject": (root / "pyproject.toml").exists(),
            "has_requirements": (root / "requirements.txt").exists(),
        },
    }

    if bool(payload.get("include_git", True)):
        git = _git_snapshot(root, timeout_ms)
        detail["git"] = git
        if not git.get("inside_work_tree"):
            issues.append({"code": "GIT_NOT_DETECTED", "severity": "warning", "message": "not inside a git work tree"})
        elif git.get("dirty"):
            issues.append({"code": "DIRTY_WORKTREE", "severity": "info", "message": f"{git.get('status_count')} changed paths"})

    if bool(payload.get("include_python", True)):
        py_count = _count_paths(root, ("*.py",), max_files=max_files)
        detail["python"] = {
            "python_file_count": py_count,
            "has_package_metadata": bool(detail["files"]["has_pyproject"] or detail["files"]["has_requirements"]),
        }
        if py_count == 0:
            issues.append({"code": "NO_PYTHON_FILES", "severity": "warning", "message": "no Python files detected"})

    if bool(payload.get("include_tests", True)):
        test_count = _count_paths(root, ("test_*.py", "*_test.py"), max_files=max_files)
        detail["tests"] = {
            "test_file_count": test_count,
            "has_pytest_config": bool(detail["files"]["has_pytest_ini"] or detail["files"]["has_pyproject"]),
        }
        if test_count == 0:
            issues.append({"code": "NO_TEST_FILES", "severity": "warning", "message": "no test files detected"})
        if not detail["tests"]["has_pytest_config"]:
            issues.append({"code": "NO_PYTEST_CONFIG", "severity": "info", "message": "pytest config not detected"})

    test_run = _maybe_run_tests(payload, root, timeout_ms)
    if test_run is not None:
        triage = _execute_test_failure_triage({"output": f"{test_run.get('stdout', '')}\n{test_run.get('stderr', '')}"})
        detail["test_run"] = {
            "ok": bool(test_run.get("ok")),
            "returncode": test_run.get("returncode"),
            "triage": triage,
        }
        if not test_run.get("ok"):
            issues.append({"code": "TEST_COMMAND_FAILED", "severity": "error", "message": "test command returned non-zero"})

    severity_rank = {"error": 3, "warning": 2, "info": 1}
    max_severity = max((severity_rank.get(item.get("severity", ""), 0) for item in issues), default=0)
    detail["issues"] = issues
    detail["health_status"] = "FAIL" if max_severity >= 3 else "CHECK" if max_severity >= 1 else "PASS"
    return detail


def _summary_counts(output: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for match in _SUMMARY_ITEM_RE.finditer(output):
        kind = match.group("kind")
        if kind.endswith("s"):
            kind = kind[:-1]
        counts[kind] += int(match.group("count"))
    return dict(counts)


def _extract_failed_tests(output: str, *, max_failures: int) -> list[str]:
    tests: list[str] = []
    seen: set[str] = set()
    for pattern in (_PYTEST_FAILED_RE, _PYTEST_ERROR_RE):
        for match in pattern.finditer(output):
            test_id = match.group("test_id").strip()
            if test_id and test_id not in seen:
                tests.append(test_id)
                seen.add(test_id)
            if len(tests) >= max_failures:
                return tests
    return tests


def _extract_error_types(output: str) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for line in output.splitlines():
        match = _PYTEST_ERROR_TYPE_RE.search(line)
        if match:
            error_type = match.group("error")
            if error_type not in seen:
                ordered.append(error_type)
                seen.add(error_type)
    return ordered


def _extract_file_hotspots(output: str, *, limit: int = 8) -> list[dict[str, Any]]:
    counter: Counter[str] = Counter()
    for match in _TRACEBACK_FILE_RE.finditer(output):
        counter[match.group("path").replace("\\", "/")] += 1
    return [{"path": path, "hits": hits} for path, hits in counter.most_common(limit)]


def _build_triage_recommendations(*, failed_tests: list[str], error_types: list[str], counts: dict[str, int]) -> list[str]:
    recommendations: list[str] = []
    if failed_tests:
        recommendations.append("Start with the first failed test and inspect the nearest assertion or traceback frame.")
    if "AssertionError" in error_types:
        recommendations.append("AssertionError suggests behavior or expected-output drift; compare fixture intent before changing code.")
    if any(error in error_types for error in ("ImportError", "ModuleNotFoundError")):
        recommendations.append("Import failure suggests dependency, module path, or renamed symbol drift.")
    if any(error in error_types for error in ("TypeError", "AttributeError")):
        recommendations.append("Type/attribute failure suggests an API contract changed without all call sites updating.")
    if counts.get("error", 0) and not failed_tests:
        recommendations.append("Collection/setup errors occurred before normal test assertions; inspect fixtures and imports first.")
    if not recommendations:
        recommendations.append("No explicit failure signature found; rerun the focused command with verbose traceback.")
    return recommendations


def _execute_test_failure_triage(payload: dict[str, Any]) -> dict[str, Any]:
    output = str(payload.get("output") or "")
    max_failures = int(payload.get("max_failures") or 12)
    failed_tests = _extract_failed_tests(output, max_failures=max_failures)
    error_types = _extract_error_types(output)
    counts = _summary_counts(output)
    hotspots = _extract_file_hotspots(output)
    return {
        "command": payload.get("command"),
        "summary_counts": counts,
        "failed_tests": failed_tests,
        "error_types": error_types,
        "file_hotspots": hotspots,
        "recommendations": _build_triage_recommendations(
            failed_tests=failed_tests,
            error_types=error_types,
            counts=counts,
        ),
        "has_failures": bool(failed_tests or error_types or counts.get("failed") or counts.get("error")),
    }
