"""Build a reproducible TASK-0461 dirty-worktree inventory.

The inventory is intentionally limited to Git-visible changes. Ignored runtime
databases, models, secrets, and caches require their own backup policy and are
never interpreted as safe-to-delete by this tool.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "natsume.task0461.baseline.v1"


def load_policy(path: str | Path) -> dict[str, Any]:
    policy_path = Path(path).resolve()
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("inventory policy must be a JSON object")
    categories = payload.get("categories")
    if not isinstance(categories, list) or not categories:
        raise ValueError("inventory policy must define non-empty categories")
    names: set[str] = set()
    for category in categories:
        if not isinstance(category, dict):
            raise ValueError("each inventory category must be an object")
        name = str(category.get("name") or "").strip()
        if not name or name in names:
            raise ValueError(f"invalid or duplicate category name: {name!r}")
        names.add(name)
        paths = category.get("paths", [])
        patterns = category.get("patterns", [])
        if not isinstance(paths, list) or not isinstance(patterns, list):
            raise ValueError(f"category {name!r} paths/patterns must be lists")
        if not paths and not patterns:
            raise ValueError(f"category {name!r} has no paths or patterns")
    return payload


def collect_git_status(repo_root: str | Path) -> list[dict[str, str]]:
    root = Path(repo_root).resolve()
    proc = subprocess.run(
        [
            "git",
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--no-renames",
        ],
        cwd=str(root),
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git status failed: {stderr}")

    rows: list[dict[str, str]] = []
    for raw_record in proc.stdout.split(b"\0"):
        if not raw_record:
            continue
        record = raw_record.decode("utf-8", errors="surrogateescape")
        if len(record) < 4 or record[2] != " ":
            raise RuntimeError(f"unsupported porcelain record: {record!r}")
        path = _normalize_path(record[3:])
        rows.append(
            {
                "path": path,
                "git_status": record[:2],
                "index_status": record[0],
                "worktree_status": record[1],
            }
        )
    rows.sort(key=lambda row: row["path"])
    return rows


def build_manifest(
    *,
    repo_root: str | Path,
    policy: dict[str, Any],
    generated_at: str | None = None,
    backup_archive: str | None = None,
    backup_sha256: str | None = None,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    entries: list[dict[str, Any]] = []
    for status in collect_git_status(root):
        path = status["path"]
        category = classify_path(path, policy)
        absolute = root / Path(path)
        exists = absolute.is_file()
        entry: dict[str, Any] = {
            **status,
            "category": category["name"] if category else "unclassified",
            "owner": str(category.get("owner") or "") if category else "",
            "preservation": str(category.get("preservation") or "") if category else "",
            "exists": exists,
            "size_bytes": absolute.stat().st_size if exists else None,
            "sha256": _sha256_file(absolute) if exists else None,
        }
        entries.append(entry)

    category_counts = Counter(str(entry["category"]) for entry in entries)
    status_counts = Counter(str(entry["git_status"]) for entry in entries)
    unclassified = [str(entry["path"]) for entry in entries if entry["category"] == "unclassified"]
    timestamp = generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": timestamp,
        "source_repository": root.as_posix(),
        "source_head": _git_text(root, "rev-parse", "HEAD"),
        "source_branch": _git_text(root, "branch", "--show-current"),
        "policy_version": int(policy.get("version") or 1),
        "summary": {
            "entry_count": len(entries),
            "category_counts": dict(sorted(category_counts.items())),
            "git_status_counts": dict(sorted(status_counts.items())),
            "unclassified_count": len(unclassified),
        },
        "unclassified_paths": unclassified,
        "entries": entries,
        "limitations": [
            "Only Git-visible tracked modifications and untracked files are inventoried.",
            "Ignored databases, models, secrets, caches, and runtime evidence are not backed up by this manifest.",
            "A hash proves identity but is not a content backup.",
        ],
    }
    if backup_archive or backup_sha256:
        manifest["external_backup"] = {
            "archive": str(backup_archive or ""),
            "sha256": str(backup_sha256 or ""),
        }
    return manifest


def classify_path(path: str, policy: dict[str, Any]) -> dict[str, Any] | None:
    normalized = _normalize_path(path)
    matches: list[dict[str, Any]] = []
    for category in policy.get("categories", []):
        exact_paths = {_normalize_path(str(item)) for item in category.get("paths", [])}
        patterns = [_normalize_path(str(item)) for item in category.get("patterns", [])]
        if normalized in exact_paths or any(fnmatch.fnmatchcase(normalized, pattern) for pattern in patterns):
            matches.append(category)
    if len(matches) > 1:
        names = ", ".join(str(item.get("name")) for item in matches)
        raise ValueError(f"path {normalized!r} matches multiple categories: {names}")
    return matches[0] if matches else None


def write_manifest(path: str | Path, manifest: dict[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build TASK-0461 Git-visible baseline inventory.")
    parser.add_argument("--repo-root", default=".", help="Repository/worktree to inspect.")
    parser.add_argument(
        "--policy",
        default=str(Path(__file__).with_name("nle_inventory_policy.json")),
        help="Inventory classification policy JSON.",
    )
    parser.add_argument("--output", help="Optional JSON output path.")
    parser.add_argument("--generated-at", help="Stable ISO-8601 evidence timestamp.")
    parser.add_argument("--backup-archive", help="External content-backup archive path.")
    parser.add_argument("--backup-sha256", help="SHA-256 of the external backup archive.")
    parser.add_argument(
        "--allow-unclassified",
        action="store_true",
        help="Return success even when Git-visible paths are not classified.",
    )
    args = parser.parse_args(argv)

    policy = load_policy(args.policy)
    manifest = build_manifest(
        repo_root=args.repo_root,
        policy=policy,
        generated_at=args.generated_at,
        backup_archive=args.backup_archive,
        backup_sha256=args.backup_sha256,
    )
    if args.output:
        write_manifest(args.output, manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if manifest["unclassified_paths"] and not args.allow_unclassified else 0


def _git_text(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_path(path: str | Path) -> str:
    normalized = str(path).replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


if __name__ == "__main__":
    raise SystemExit(main())
