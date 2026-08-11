from __future__ import annotations

import ast
import inspect
from pathlib import Path

import host_brain.app.governed_frozen_skill_runtime as frozen_skill_runtime
import host_brain.app.governed_host_admission_helpers as admission_helpers
import host_brain.app.governed_host_contracts as host_contracts
import host_brain.app.governed_task_adapter as governed_task_adapter


ROOT = Path(__file__).resolve().parents[2]


def test_governed_host_adapter_is_a_focused_application_facade() -> None:
    path = ROOT / "host_brain/app/governed_task_adapter.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    owned_names = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert len(source.splitlines()) < 1_500
    assert owned_names == {
        "GovernedHostRuntime",
        "build_governed_host_runtime",
    }


def test_admission_helpers_are_small_and_stateless() -> None:
    source = inspect.getsource(admission_helpers)
    tree = ast.parse(source)
    owned_names = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }

    assert len(source.splitlines()) < 180
    assert owned_names == {
        "resolve_direct_skill_execution_policy",
        "review_continuation_result",
        "validate_delegate_target",
    }


def test_extracted_runtime_keeps_adapter_compatibility_imports() -> None:
    assert governed_task_adapter._FrozenSkillWorker is frozen_skill_runtime._FrozenSkillWorker
    assert (
        governed_task_adapter._DurableSkillProjectionReader
        is frozen_skill_runtime._DurableSkillProjectionReader
    )
    assert governed_task_adapter._WorkerCommitVerifier is frozen_skill_runtime._WorkerCommitVerifier
    assert governed_task_adapter._WorkerCommitAcceptance is frozen_skill_runtime._WorkerCommitAcceptance
    assert governed_task_adapter._build_contracts is host_contracts._build_contracts
    assert governed_task_adapter._build_frozen_context is host_contracts._build_frozen_context


def test_contract_module_owns_no_runtime_or_repository_state() -> None:
    source = inspect.getsource(host_contracts)
    tree = ast.parse(source)
    imported_modules = {
        str(node.module or "")
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }

    assert "GovernedHostRuntime" not in source
    assert "GovernedTaskCoordinator" not in source
    assert not any("persistence" in module for module in imported_modules)
