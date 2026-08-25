"""Static scan for cognitive-runtime rule/template degrade patterns."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


RULE_ID = "LLM-RULE-DEGRADE-01"
RULE_NAME = "NO_RULE_DEGRADE"

_VIOLATION_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "USE_LLM_DISABLED",
        re.compile(r"""["']?use_llm["']?\s*[:=]\s*False\b"""),
        "Hard-coded LLM disable detected in cognitive/runtime code.",
    ),
    (
        "DEGRADED_TO_RULE_TRUE",
        re.compile(r"""["']?degraded_to_rule["']?\s*[:=]\s*True\b"""),
        "Cognitive/runtime audit must not mark degraded_to_rule=true.",
    ),
)


def scan_file(file_path: str | Path) -> list[dict[str, Any]]:
    path = Path(file_path)
    if path.suffix.lower() != ".py":
        return []

    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return []

    normalized = _normalize(path)
    violations: list[dict[str, Any]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for reason_code, pattern, message in _VIOLATION_PATTERNS:
            if not pattern.search(line):
                continue
            violations.append(
                {
                    "rule_id": RULE_ID,
                    "rule_name": RULE_NAME,
                    "reason_code": reason_code,
                    "path": normalized,
                    "line": line_no,
                    "column": 0,
                    "message": message,
                }
            )
    return violations


def scan_repo(root: str | Path) -> list[dict[str, Any]]:
    repo_root = Path(root)
    if not repo_root.exists():
        return []

    violations: list[dict[str, Any]] = []
    for path in repo_root.rglob("*.py"):
        normalized = _normalize(path)
        if _skip_path(normalized):
            continue
        violations.extend(scan_file(path))
    return violations


def main(argv: list[str] | None = None) -> int:
    args = list(argv or [])
    root = Path(args[0]).resolve() if args else Path.cwd()
    violations = scan_repo(root)
    print(json.dumps({"rule_id": RULE_ID, "violations": violations}, ensure_ascii=False, indent=2))
    return 1 if violations else 0


def _normalize(path: str | Path) -> str:
    return str(Path(path)).replace("\\", "/")


def _skip_path(path: str) -> bool:
    if "/.venv/" in path or "/venv/" in path or "/__pycache__/" in path:
        return True
    if "/tests/" in path or "/docs/" in path:
        return True
    if "/host_brain/persona/self_model.py" in path:
        return True
    if "/host_brain/bond/bond_narrative.py" in path:
        return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
