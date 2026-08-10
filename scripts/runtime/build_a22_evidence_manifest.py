"""Build a deterministic, secret-free index for an A22 acceptance run."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, Mapping, Sequence


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_output(repo_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def _json_summary(path: Path) -> dict[str, Any]:
    if path.suffix.lower() != ".json":
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"json_parseable": False}
    if not isinstance(payload, Mapping):
        return {"json_parseable": True, "json_object": False}
    summary: dict[str, Any] = {"json_parseable": True, "json_object": True}
    for key in (
        "schema_version",
        "status",
        "repository_commit",
        "case_count",
        "passed_case_count",
        "report_sha256",
    ):
        if key in payload:
            summary[key] = payload[key]
    return summary


def build_manifest(
    *,
    repo_root: Path,
    run_root: Path,
    evidence_paths: Sequence[Path],
    migration_head: int,
) -> dict[str, Any]:
    repository_commit = _git_output(repo_root, "rev-parse", "HEAD")
    tracked_status = _git_output(
        repo_root,
        "status",
        "--porcelain",
        "--untracked-files=no",
    )
    root = run_root.resolve()
    rows: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for raw_path in evidence_paths:
        path = raw_path.resolve()
        if path in seen:
            continue
        seen.add(path)
        if not path.is_file():
            raise FileNotFoundError(f"evidence file does not exist: {path}")
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError as exc:
            raise ValueError(
                f"evidence must be inside the isolated run root: {path}"
            ) from exc
        row = {
            "path": relative,
            "size_bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
        }
        summary = _json_summary(path)
        if summary:
            row["summary"] = summary
        rows.append(row)
    rows.sort(key=lambda row: str(row["path"]))

    mismatched_reports = [
        str(row["path"])
        for row in rows
        if str(dict(row.get("summary") or {}).get("repository_commit") or "")
        not in {"", repository_commit}
    ]
    return {
        "schema_version": "natsume.a22.evidence-manifest.v1",
        "status": (
            "PASS"
            if not tracked_status and not mismatched_reports and rows
            else "FAIL"
        ),
        "repository_commit": repository_commit,
        "tracked_worktree_clean": not tracked_status,
        "migration_head": int(migration_head),
        "isolated_run_root": str(root),
        "evidence_count": len(rows),
        "repository_commit_mismatches": mismatched_reports,
        "evidence": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--migration-head", type=int, required=True)
    parser.add_argument(
        "--evidence",
        type=Path,
        action="append",
        required=True,
        help="Evidence file inside --run-root; repeat for every indexed file.",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    output = args.output.resolve()
    manifest = build_manifest(
        repo_root=args.repo_root.resolve(),
        run_root=args.run_root.resolve(),
        evidence_paths=args.evidence,
        migration_head=args.migration_head,
    )
    rendered = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if manifest["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
