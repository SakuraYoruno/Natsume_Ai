"""Documentation freshness gate for CI.

Checks whether key documentation files lag behind their configured source-of-truth
inputs by comparing latest git-modification timestamps.
"""

from __future__ import annotations

import argparse
from fnmatch import fnmatchcase
import json
import os
import subprocess
from pathlib import Path
from typing import Any

CHECK_ID = "doc_freshness_gate"

REASON_DOC_FRESHNESS_STALE = "DOC_FRESHNESS_STALE"
REASON_DOC_MISSING = "DOC_TARGET_MISSING"
REASON_DOC_FRESHNESS_CONFIG_INVALID = "DOC_FRESHNESS_CONFIG_INVALID"
REASON_DOC_NO_SOURCE_MATCH = "DOC_NO_SOURCE_FILES"
REASON_DOC_CHANGE_REQUIRED = "DOC_CHANGE_REQUIRED"

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "doc_freshness_policy.json"
SECONDS_PER_DAY = 86400


def build_report(
    root: str | Path,
    *,
    config: str | Path,
    changed_files: list[str | Path] | None = None,
    changed_files_list: str | Path | None = None,
    check_all: bool = False,
    max_age_days_override: int | None = None,
) -> dict[str, Any]:
    repo_root = Path(root).resolve()
    config_path = Path(config)
    if not config_path.is_absolute():
        config_path = (repo_root / config_path).resolve()

    policy = _load_config(config_path)
    default_max_age_days = (
        int(max_age_days_override)
        if max_age_days_override is not None
        else int(policy.get("default_max_age_days", 30))
    )

    changed = _collect_changed_paths(
        repo_root=repo_root,
        changed_files=changed_files,
        changed_files_list=changed_files_list,
    )
    dirty_paths = _collect_git_dirty_paths(repo_root)

    failures: list[dict[str, Any]] = []
    change_contract = _evaluate_change_contract(policy=policy, changed=changed)
    if change_contract["invalid"]:
        failures.append(
            _failure(
                reason_code=REASON_DOC_FRESHNESS_CONFIG_INVALID,
                failed_artifact="__change_contract__",
                message="Required change_contract must define non-empty sources and documentation patterns",
            )
        )
    elif change_contract["triggered"] and not change_contract["changed_docs"]:
        failures.append(
            {
                "check_id": CHECK_ID,
                "reason_code": REASON_DOC_CHANGE_REQUIRED,
                "failed_artifact": "__change_contract__",
                "line": 0,
                "message": "Production/tooling code changed without a documentation update in the same change set",
                "changed_sources": change_contract["changed_sources"][:20],
                "accepted_documentation_patterns": change_contract["documentation_patterns"],
            }
        )
    checked_rules = 0
    skipped_rules = 0

    for rule in policy.get("rules", []):
        doc_rel = str(rule.get("doc", "")).replace("\\", "/").strip()
        if not doc_rel:
            failures.append(
                _failure(
                    reason_code=REASON_DOC_FRESHNESS_CONFIG_INVALID,
                    failed_artifact="__policy__",
                    message="Rule missing required 'doc' field",
                )
            )
            continue

        source_patterns = list(rule.get("sources", []))
        if not source_patterns:
            failures.append(
                _failure(
                    reason_code=REASON_DOC_FRESHNESS_CONFIG_INVALID,
                    failed_artifact=doc_rel,
                    message="Rule missing required 'sources' list",
                )
            )
            continue

        doc_path = repo_root / doc_rel
        if not doc_path.exists():
            failures.append(
                _failure(
                    reason_code=REASON_DOC_MISSING,
                    failed_artifact=doc_rel,
                    message="Target doc file does not exist",
                )
            )
            continue

        sources = _collect_sources(
            repo_root=repo_root,
            patterns=source_patterns,
        )
        if not sources:
            failures.append(
                _failure(
                    reason_code=REASON_DOC_NO_SOURCE_MATCH,
                    failed_artifact=doc_rel,
                    message="Rule has no resolvable source files",
                )
            )
            continue

        normalized_sources = {_to_rel_lower(p, repo_root): p for p in sources}
        touched_sources = {
            rel: p for rel, p in normalized_sources.items() if rel in changed
        }
        if not check_all and not touched_sources:
            skipped_rules += 1
            continue

        checked_rules += 1
        doc_ts = _best_effort_timestamp(
            doc_path,
            repo_root,
            dirty_paths=dirty_paths,
        )
        if doc_ts is None:
            failures.append(
                _failure(
                    reason_code=REASON_DOC_MISSING,
                    failed_artifact=doc_rel,
                    message="Target doc timestamp unavailable",
                )
            )
            continue

        # PR/task-scoped runs only need to compare the documentation with the
        # source files that triggered this rule.  Walking every historical
        # source and spawning one ``git log`` per file made a focused gate take
        # minutes on this repository.  Scheduled ``--check-all`` runs retain
        # the complete baseline scan.
        timestamp_sources = (
            list(touched_sources.values())
            if touched_sources and not check_all
            else sources
        )
        source_timestamps: dict[str, float] = {}
        if check_all:
            # A scheduled full scan may resolve thousands of files. Spawning
            # ``git log`` once per file made the gate take minutes on Windows.
            # We only need the newest source change for freshness comparison,
            # so resolve it with one Git pathspec query per rule and merge any
            # dirty/untracked filesystem timestamps.
            latest_source = _latest_source_timestamp(
                repo_root=repo_root,
                patterns=source_patterns,
                sources=timestamp_sources,
                dirty_paths=dirty_paths,
            )
            if latest_source is not None:
                source_timestamps[latest_source[0]] = latest_source[1]
        else:
            for source_path in timestamp_sources:
                source_ts = _best_effort_timestamp(
                    source_path,
                    repo_root,
                    dirty_paths=dirty_paths,
                )
                if source_ts is not None:
                    source_timestamps[_to_rel_lower(source_path, repo_root)] = source_ts

        if not source_timestamps:
            failures.append(
                _failure(
                    reason_code=REASON_DOC_NO_SOURCE_MATCH,
                    failed_artifact=doc_rel,
                    message="No source timestamp available for resolved files",
                )
            )
            continue

        latest_source_ts = max(source_timestamps.values())
        max_age_days = int(rule.get("max_age_days", default_max_age_days))
        age_days = (latest_source_ts - doc_ts) / SECONDS_PER_DAY
        if age_days > max_age_days:
            top_src = max(
                source_timestamps.items(),
                key=lambda kv: kv[1],
            )
            stale_paths = (
                sorted(touched_sources.values())
                if touched_sources
                else [repo_root / rel for rel in _sample_paths(source_timestamps, 3)]
            )
            failures.append(
                {
                    "check_id": CHECK_ID,
                    "reason_code": REASON_DOC_FRESHNESS_STALE,
                    "failed_artifact": doc_rel,
                    "line": 0,
                    "message": (
                        f"Doc outdated by {age_days:.1f} days; "
                        f"allowed max age is {max_age_days} days"
                    ),
                    "latest_source": top_src[0],
                    "latest_source_ts": int(top_src[1]),
                    "doc_ts": int(doc_ts),
                    "rule_id": str(rule.get("id") or doc_rel),
                    "note": str(rule.get("note") or ""),
                    "checked_source_count": len(timestamp_sources),
                    "changed_source_count": len(touched_sources),
                    "sample_changed_or_recent_sources": [str(p) for p in stale_paths[:5]],
                    "max_age_days": max_age_days,
                }
            )

    failures.sort(
        key=lambda row: (
            str(row.get("failed_artifact") or ""),
            str(row.get("rule_id") or ""),
            str(row.get("reason_code") or ""),
        )
    )

    return {
        "check_id": CHECK_ID,
        "status": "failed" if failures else "passed",
        "reason_code": failures[0].get("reason_code") if failures else None,
        "failures": failures,
        "summary": {
            "policy": _normalize_policy_name(policy),
            "rule_count": len(policy.get("rules", [])),
            "checked_rules": checked_rules,
            "skipped_rules": skipped_rules,
            "failure_count": len(failures),
            "input_changed_files": len(changed),
            "change_contract_triggered": bool(change_contract["triggered"]),
            "change_contract_valid": not bool(change_contract["invalid"]),
            "changed_documentation_count": len(change_contract["changed_docs"]),
            "check_all": bool(check_all),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run documentation freshness checks against source-to-doc mappings."
    )
    parser.add_argument("--root", default=".", help="Repository root.")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to doc freshness policy JSON.",
    )
    parser.add_argument(
        "--changed-file",
        action="append",
        default=[],
        dest="changed_files",
        help="Changed file path to scope checks (repeatable).",
    )
    parser.add_argument(
        "--changed-files-list",
        default=None,
        help="Text file with one changed file path per line.",
    )
    parser.add_argument(
        "--check-all",
        action="store_true",
        help=(
            "Check all rules even when no changed source matches. "
            "Used by scheduled/report-only runs."
        ),
    )
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=None,
        help="Global max age override (days).",
    )
    parser.add_argument(
        "--report-file",
        default=None,
        help="Optional path to write JSON report.",
    )
    args = parser.parse_args(argv)

    report = build_report(
        root=args.root,
        config=args.config,
        changed_files=args.changed_files,
        changed_files_list=args.changed_files_list,
        check_all=args.check_all,
        max_age_days_override=args.max_age_days,
    )

    if args.report_file:
        Path(args.report_file).write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if report["status"] == "failed" else 0


def _load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise RuntimeError(f"Freshness policy not found: {path}")
    policy = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(policy, dict):
        raise RuntimeError(f"Invalid policy format: {path}")
    return policy


def _evaluate_change_contract(
    *,
    policy: dict[str, Any],
    changed: set[str],
) -> dict[str, Any]:
    contract = policy.get("change_contract")
    if not isinstance(contract, dict) or not bool(contract.get("required", False)):
        return {
            "invalid": False,
            "triggered": False,
            "changed_sources": [],
            "changed_docs": [],
            "documentation_patterns": [],
        }

    source_patterns = _normalized_patterns(contract.get("sources"))
    documentation_patterns = _normalized_patterns(contract.get("documentation"))
    if not source_patterns or not documentation_patterns:
        return {
            "invalid": True,
            "triggered": False,
            "changed_sources": [],
            "changed_docs": [],
            "documentation_patterns": documentation_patterns,
        }

    changed_sources = sorted(
        path for path in changed if _path_matches_any(path, source_patterns)
    )
    changed_docs = sorted(
        path for path in changed if _path_matches_any(path, documentation_patterns)
    )
    return {
        "invalid": False,
        "triggered": bool(changed_sources),
        "changed_sources": changed_sources,
        "changed_docs": changed_docs,
        "documentation_patterns": documentation_patterns,
    }


def _normalized_patterns(raw_patterns: Any) -> list[str]:
    if not isinstance(raw_patterns, list):
        return []
    return [
        str(pattern).replace("\\", "/").strip().lower()
        for pattern in raw_patterns
        if str(pattern).strip()
    ]


def _path_matches_any(path: str, patterns: list[str]) -> bool:
    normalized = str(path).replace("\\", "/").strip().lower()
    return any(fnmatchcase(normalized, pattern) for pattern in patterns)


def _collect_changed_paths(
    *,
    repo_root: Path,
    changed_files: list[str | Path] | None,
    changed_files_list: str | Path | None,
) -> set[str]:
    paths: list[str] = []
    if changed_files:
        paths.extend(str(p) for p in changed_files if str(p).strip())
    if changed_files_list:
        list_path = Path(changed_files_list)
        if list_path.exists():
            for line in list_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    paths.append(line)
    normalized: set[str] = set()
    for raw in paths:
        normalized.add(_to_rel_lower(repo_root / raw, repo_root))
    return normalized


def _collect_sources(
    *,
    repo_root: Path,
    patterns: list[str],
) -> list[Path]:
    result: list[Path] = []
    for pattern in patterns:
        if not pattern:
            continue
        candidate = pattern.replace("\\", "/")
        if _contains_glob(candidate):
            result.extend(_resolve_glob(repo_root=repo_root, pattern=candidate))
            continue

        exact_path = (repo_root / candidate).resolve()
        if exact_path.is_file():
            result.append(exact_path)
            continue
        if exact_path.is_dir():
            result.extend(_collect_tree_files(exact_path))
            continue

    # 去重并保持稳定顺序
    return sorted(
        {path.resolve() for path in result},
        key=lambda p: p.as_posix(),
    )


def _resolve_glob(*, repo_root: Path, pattern: str) -> list[Path]:
    return [path.resolve() for path in repo_root.glob(pattern) if path.is_file()]


def _collect_tree_files(directory: Path) -> list[Path]:
    return [
        path.resolve()
        for path in directory.rglob("*")
        if path.is_file() and not _should_skip_path(path)
    ]


def _contains_glob(value: str) -> bool:
    return any(ch in value for ch in ("*", "?", "["))


def _should_skip_path(path: Path) -> bool:
    parts = set(part.lower() for part in path.parts)
    skip_dirs = {
        ".git",
        ".venv",
        "venv",
        ".pytest_cache",
        "__pycache__",
        "artifacts",
        "node_modules",
        ".tox",
    }
    if skip_dirs & parts:
        return True
    return False


def _collect_git_dirty_paths(root: Path) -> set[str]:
    """Return modified, staged, untracked, and renamed paths in one Git call."""
    proc = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=str(root),
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return set()

    records = proc.stdout.split(b"\0")
    dirty_paths: set[str] = set()
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record or len(record) < 4 or record[2:3] != b" ":
            continue

        status = record[:2].decode("ascii", errors="replace")
        dirty_paths.add(_normalize_git_status_path(os.fsdecode(record[3:]), root))

        # In porcelain v1 -z output, rename/copy entries carry the second
        # pathname in the following NUL-delimited record. Track both names so
        # either side of a rename is treated as working-tree state.
        if "R" in status or "C" in status:
            if index < len(records) and records[index]:
                dirty_paths.add(
                    _normalize_git_status_path(os.fsdecode(records[index]), root)
                )
                index += 1

    return dirty_paths


def _normalize_git_status_path(path: str, root: Path) -> str:
    candidate = Path(path)
    if candidate.is_absolute():
        return _to_rel_lower(candidate, root)
    return path.replace("\\", "/").lower()


def _best_effort_timestamp(
    path: Path,
    root: Path,
    *,
    dirty_paths: set[str],
) -> float | None:
    if _to_rel_lower(path, root) in dirty_paths:
        return _filesystem_timestamp(path)

    git_ts = _git_timestamp(path, root)
    if git_ts is not None:
        return git_ts
    return _filesystem_timestamp(path)


def _latest_source_timestamp(
    *,
    repo_root: Path,
    patterns: list[str],
    sources: list[Path],
    dirty_paths: set[str],
) -> tuple[str, float] | None:
    candidates: list[tuple[str, float]] = []
    for source in sources:
        relative = _to_rel_lower(source, repo_root)
        if relative not in dirty_paths:
            continue
        timestamp = _filesystem_timestamp(source)
        if timestamp is not None:
            candidates.append((relative, timestamp))

    git_latest = _git_latest_timestamp_for_patterns(repo_root, patterns)
    if git_latest is not None:
        candidates.append(git_latest)
    if not candidates:
        return None
    return max(candidates, key=lambda row: row[1])


def _git_latest_timestamp_for_patterns(
    root: Path,
    patterns: list[str],
) -> tuple[str, float] | None:
    pathspecs: list[str] = []
    for raw in patterns:
        normalized = str(raw).replace("\\", "/").strip()
        if not normalized:
            continue
        pathspecs.append(
            f":(glob){normalized}" if _contains_glob(normalized) else normalized
        )
    if not pathspecs:
        return None

    marker = "__DOC_FRESHNESS_TS__"
    proc = subprocess.run(
        [
            "git",
            "log",
            "-1",
            f"--format={marker}%ct",
            "--name-only",
            "--",
            *pathspecs,
        ],
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        return None

    timestamp: float | None = None
    source_path = "__git_pathspec__"
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(marker):
            try:
                timestamp = float(stripped.removeprefix(marker))
            except ValueError:
                return None
            continue
        if timestamp is not None:
            source_path = stripped.replace("\\", "/").lower()
            break
    if timestamp is None:
        return None
    return source_path, timestamp


def _filesystem_timestamp(path: Path) -> float | None:
    try:
        return float(path.stat().st_mtime)
    except OSError:
        return None


def _git_timestamp(path: Path, root: Path) -> float | None:
    rel = _to_rel(path, root)
    proc = subprocess.run(
        ["git", "log", "-1", "--format=%ct", "--", rel],
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        return None
    output = proc.stdout.strip()
    if not output:
        return None
    try:
        return float(output.splitlines()[-1].strip())
    except ValueError:
        return None


def _to_rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _to_rel_lower(path: Path, root: Path) -> str:
    return _to_rel(path, root).lower()


def _normalize_policy_name(policy: dict[str, Any]) -> str:
    return str(policy.get("name") or policy.get("policy", "") or "doc-freshness-policy")


def _sample_paths(
    source_timestamps: dict[str, float],
    count: int,
) -> list[str]:
    return [path for path, _ in sorted(source_timestamps.items(), key=lambda i: i[1], reverse=True)[:count]]


def _failure(
    *,
    reason_code: str,
    failed_artifact: str,
    message: str,
) -> dict[str, Any]:
    return {
        "check_id": CHECK_ID,
        "reason_code": reason_code,
        "failed_artifact": failed_artifact,
        "line": 0,
        "message": message,
    }


if __name__ == "__main__":
    raise SystemExit(main())
