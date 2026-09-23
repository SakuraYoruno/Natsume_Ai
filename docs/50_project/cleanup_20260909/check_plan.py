"""Validate cleanup metadata/diff scope without executing or approving tasks."""
from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys


PLAN_DIR = Path(__file__).resolve().parent
ROOT = PLAN_DIR.parents[2]
PLAN_PREFIX = PLAN_DIR.relative_to(ROOT).as_posix() + "/"
STATES = {"READY", "EVIDENCE_ONLY", "HOLD"}


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT).decode("utf-8")


def safe_path(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and not any(
        token in value for token in ("\\", ":", "*", "?", "[", "]")
    ) and ".." not in path.parts


def validate(plan: dict) -> list[str]:
    errors = []
    tasks = plan.get("tasks", [])
    ids = [task.get("id") for task in tasks]
    if len(ids) != len(set(ids)):
        errors.append("duplicate task IDs")
    planned_new = {path for task in tasks for path in task.get("create", [])}
    by_id = {task["id"]: task for task in tasks}
    for task in tasks:
        name = task["id"]
        for key in ("state", "kind", "title", "depends_on", "read", "edit", "create", "delete", "steps", "invariants", "tests", "stop", "max_changed_files"):
            if key not in task:
                errors.append(f"{name}: missing {key}")
        if task.get("state") not in STATES:
            errors.append(f"{name}: invalid state")
        if not task.get("steps") or not task.get("stop"):
            errors.append(f"{name}: steps and stop conditions required")
        if task.get("state") == "HOLD" and not task.get("unlock"):
            errors.append(f"{name}: HOLD requires an explicit unlock decision")
        if task.get("state") != "READY" and any(task.get(k) for k in ("edit", "create", "delete")):
            errors.append(f"{name}: non-READY task cannot authorize code changes")
        for dep in task.get("depends_on", []):
            if dep not in by_id:
                errors.append(f"{name}: unknown dependency {dep}")
        for field in ("read", "edit", "create", "delete", "candidate_edit", "tests"):
            for value in task.get(field, []):
                path = value.split("::", 1)[0]
                if not safe_path(path):
                    errors.append(f"{name}: invalid path {path}")
                elif field != "create" and path not in planned_new and not (ROOT / path).is_file():
                    errors.append(f"{name}: missing {field} file {path}")
        for path in task.get("edit", []) + task.get("create", []) + task.get("delete", []):
            if path.endswith(".sql") or any(
                path.startswith(p) if p.endswith("/") else path == p
                for p in plan.get("protected", [])
            ):
                errors.append(f"{name}: protected mutation path {path}")
    visiting, visited = set(), set()

    def visit(name: str) -> None:
        if name in visiting:
            errors.append(f"dependency cycle at {name}")
            return
        if name in visited or name not in by_id:
            return
        visiting.add(name)
        for dep in by_id[name].get("depends_on", []):
            visit(dep)
        visiting.remove(name)
        visited.add(name)

    for name in ids:
        visit(name)
    return errors


def changed_paths(base: str) -> list[tuple[str, str]]:
    git("rev-parse", "--verify", base + "^{commit}")
    fields = git("diff", "--name-status", "--no-renames", "-z", base, "--").split("\0")
    pairs = [(fields[i], fields[i + 1]) for i in range(0, len(fields) - 1, 2)]
    known = {path for _, path in pairs}
    pairs += [("?", path) for path in git("ls-files", "--others", "--exclude-standard", "-z").split("\0") if path and path not in known]
    return pairs


def check_scope(task: dict, changes: list[tuple[str, str]]) -> list[str]:
    errors = []
    business = [(status, path) for status, path in changes if not path.startswith(PLAN_PREFIX)]
    allowed = set(task["edit"] + task["create"] + task["delete"])
    for status, path in business:
        if path not in allowed:
            errors.append(f"OUT_OF_SCOPE {status} {path}")
        elif status == "D" and path not in task["delete"]:
            errors.append(f"DELETE_NOT_AUTHORIZED {path}")
        elif status in {"A", "?"} and path not in task["create"]:
            errors.append(f"CREATE_NOT_AUTHORIZED {path}")
    if len(business) > task["max_changed_files"]:
        errors.append(f"TOO_MANY_FILES {len(business)} > {task['max_changed_files']}")
    if task["state"] != "READY" and business:
        errors.append("TASK_HAS_NO_CODE_MUTATION_AUTHORITY")
    return errors


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task")
    parser.add_argument("--check-diff", action="store_true")
    parser.add_argument("--base", default="HEAD", help="Commit before this task; HEAD only checks uncommitted changes.")
    args = parser.parse_args()
    plan = json.loads((PLAN_DIR / "tasks.json").read_text(encoding="utf-8"))
    errors = validate(plan)
    if errors:
        print("\n".join(errors))
        return 1
    print(f"PLAN_VALID: {len(plan['tasks'])} tasks; metadata only, NOT implementation/test approval")
    head = git("rev-parse", "HEAD").strip()
    if head != plan["baseline_head"]:
        print(f"BASELINE_DRIFT: HEAD={head}; re-read evidence before implementing")
    if args.check_diff and not args.task:
        parser.error("--check-diff requires --task")
    if args.task:
        task = next((t for t in plan["tasks"] if t["id"] == args.task), None)
        if task is None:
            parser.error("unknown task ID")
        print(json.dumps(task, ensure_ascii=False, indent=2))
        if task["tests"]:
            print("TEST_COMMAND (run only after preconditions): py -3 -B -m pytest -p no:cacheprovider -q " + " ".join(task["tests"]))
        for command in task.get("extra_commands", []):
            print("ADDITIONAL_COMMAND (review fixtures first): " + command)
        print("DEPENDENCIES_AND_UNLOCK_REQUIRE_REVIEW: this tool never auto-promotes tasks")
        if args.check_diff:
            errors = check_scope(task, changed_paths(args.base))
            if errors:
                print("\n".join(errors))
                return 2
            print("SCOPE_VALID: no out-of-scope diff; empty diff is NOT completion")
    return 0


if __name__ == "__main__":
    sys.exit(main())
