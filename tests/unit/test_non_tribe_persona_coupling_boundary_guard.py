from __future__ import annotations

import ast
from pathlib import Path


def test_non_tribe_layers_have_no_direct_persona_imports() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    host_root = repo_root / "host_brain"
    violations: list[str] = []

    for file_path in host_root.rglob("*.py"):
        rel = file_path.relative_to(repo_root)
        if rel.parts[:2] in {("host_brain", "persona"), ("host_brain", "tribe")}:
            continue

        source = file_path.read_text(encoding="utf-8", errors="ignore")
        try:
            module = ast.parse(source)
        except SyntaxError:
            continue

        for node in ast.walk(module):
            if isinstance(node, ast.ImportFrom):
                module_name = str(node.module or "")
                if module_name.startswith("host_brain.persona"):
                    violations.append(f"{rel}:{node.lineno} from {module_name} import ...")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    module_name = str(alias.name or "")
                    if module_name.startswith("host_brain.persona"):
                        violations.append(f"{rel}:{node.lineno} import {module_name}")

    assert not violations, "Non-tribe layers must not directly import host_brain.persona.*\n" + "\n".join(violations)
