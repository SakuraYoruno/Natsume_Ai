from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "static_scan" / "nle_baseline_inventory.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("nle_baseline_inventory", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load inventory module: {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)
    return proc.stdout.strip()


def _init_repo(path: Path) -> None:
    _git(path, "init")
    _git(path, "config", "user.email", "task0461@example.invalid")
    _git(path, "config", "user.name", "TASK-0461 Test")
    (path / "tracked.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(path, "add", "tracked.py")
    _git(path, "commit", "-m", "baseline")


def test_inventory_classifies_modified_staged_and_untracked_files(tmp_path: Path) -> None:
    inventory = _load_module()
    _init_repo(tmp_path)
    (tmp_path / "tracked.py").write_text("VALUE = 2\n", encoding="utf-8")
    (tmp_path / "staged.py").write_text("STAGED = True\n", encoding="utf-8")
    (tmp_path / "untracked file.txt").write_text("evidence\n", encoding="utf-8")
    _git(tmp_path, "add", "staged.py")

    policy = {
        "version": 1,
        "categories": [
            {
                "name": "known",
                "owner": "test",
                "preservation": "keep",
                "paths": ["tracked.py", "staged.py", "untracked file.txt"],
                "patterns": [],
            }
        ],
    }
    manifest = inventory.build_manifest(
        repo_root=tmp_path,
        policy=policy,
        generated_at="2026-07-10T00:00:00+00:00",
    )

    assert manifest["summary"]["entry_count"] == 3
    assert manifest["summary"]["category_counts"] == {"known": 3}
    assert manifest["unclassified_paths"] == []
    by_path = {row["path"]: row for row in manifest["entries"]}
    assert by_path["tracked.py"]["git_status"] == " M"
    assert by_path["staged.py"]["git_status"] == "A "
    assert by_path["untracked file.txt"]["git_status"] == "??"
    assert all(row["sha256"] and len(row["sha256"]) == 64 for row in by_path.values())


def test_inventory_reports_unclassified_and_overlapping_categories(tmp_path: Path) -> None:
    inventory = _load_module()
    _init_repo(tmp_path)
    (tmp_path / "unknown.txt").write_text("unknown\n", encoding="utf-8")
    policy = {
        "version": 1,
        "categories": [
            {
                "name": "known",
                "owner": "test",
                "preservation": "keep",
                "paths": ["tracked.py"],
                "patterns": [],
            }
        ],
    }

    manifest = inventory.build_manifest(repo_root=tmp_path, policy=policy)
    assert manifest["unclassified_paths"] == ["unknown.txt"]

    overlapping = {
        "version": 1,
        "categories": [
            {"name": "one", "paths": ["unknown.txt"], "patterns": []},
            {"name": "two", "paths": [], "patterns": ["*.txt"]},
        ],
    }
    with pytest.raises(ValueError, match="multiple categories"):
        inventory.build_manifest(repo_root=tmp_path, policy=overlapping)


def test_repository_inventory_policy_is_valid() -> None:
    inventory = _load_module()
    policy = inventory.load_policy(ROOT / "tools" / "static_scan" / "nle_inventory_policy.json")
    names = {row["name"] for row in policy["categories"]}
    assert names == {
        "protected_user_business_wip",
        "generated_pentest_canon_data",
        "task_0461_owned",
    }


def test_frozen_task_0461_manifest_has_complete_protected_inventory() -> None:
    manifest_path = (
        ROOT
        / "docs"
        / "35_quality"
        / "audits"
        / "NLE_WP00_BASELINE_MANIFEST_20260710.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["schema_version"] == "natsume.task0461.baseline.v1"
    assert manifest["source_head"] == "760842ab6da88a4134a94601c75b0da32416cb79"
    assert manifest["summary"] == {
        "category_counts": {
            "generated_pentest_canon_data": 11,
            "protected_user_business_wip": 15,
        },
        "entry_count": 26,
        "git_status_counts": {" M": 7, "??": 19},
        "unclassified_count": 0,
    }
    assert manifest["unclassified_paths"] == []
    assert manifest["external_backup"]["sha256"] == (
        "35083D59FA9451D65B9B29A038789787D35128EC7630BB32FD875CD9E574A894"
    )
    assert all(
        row["exists"] is True
        and isinstance(row["size_bytes"], int)
        and len(row["sha256"]) == 64
        for row in manifest["entries"]
    )
