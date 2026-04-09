from __future__ import annotations

import ast
from pathlib import Path


def test_app_layer_has_no_direct_persona_imports() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    scan_root = repo_root / "host_brain" / "app"
    violations: list[str] = []

    for file_path in scan_root.rglob("*.py"):
        rel = file_path.relative_to(repo_root)
        source = file_path.read_text(encoding="utf-8", errors="ignore")
        try:
            module = ast.parse(source)
        except SyntaxError:
            continue

        for node in ast.walk(module):
            if isinstance(node, ast.ImportFrom):
                mod = str(node.module or "")
                if mod.startswith("host_brain.persona"):
                    violations.append(f"{rel}:{node.lineno} from {mod} import ...")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    name = str(alias.name or "")
                    if name.startswith("host_brain.persona"):
                        violations.append(f"{rel}:{node.lineno} import {name}")

    assert not violations, (
        "App-layer direct persona imports must stay in tribe wrappers/dependency proxies.\n"
        + "\n".join(violations)
    )
