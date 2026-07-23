"""NE-01: prompt intelligence may describe plans but never execute them."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_prompt_intelligence_legacy_runtime_is_physically_retired() -> None:
    """The deleted compatibility module must not return as an execution path."""

    assert not (ROOT / "host_brain" / "app" / "intelligent_runtime.py").exists()


def test_skill_composer_is_planning_only_and_has_no_handler_executor() -> None:
    source = (ROOT / "host_brain" / "skills" / "skill_composer.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    method_names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert "execute" not in method_names
    assert "CompositionResult" not in source
