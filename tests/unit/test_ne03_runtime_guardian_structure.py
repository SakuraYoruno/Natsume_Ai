from __future__ import annotations

import ast
from pathlib import Path

import host_brain.runtime.guardian_contracts as guardian_contracts
import host_brain.runtime.guardian_operations as guardian_operations
import host_brain.runtime.runtime_guardian as runtime_guardian


ROOT = Path(__file__).resolve().parents[2]


def _owned_definitions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_runtime_guardian_keeps_the_only_durable_authority() -> None:
    path = ROOT / "host_brain/runtime/runtime_guardian.py"
    source = path.read_text(encoding="utf-8")
    directly_owned = set(runtime_guardian.RuntimeGuardian.__dict__)

    assert len(source.splitlines()) < 1_500
    assert {
        "_restore_guardian_journal",
        "_reload_guardian_journal",
        "_validate_event_delta",
        "_apply_event",
        "_append",
    }.issubset(directly_owned)
    assert "_append" not in guardian_operations._RuntimeGuardianOperations.__dict__


def test_guardian_operations_are_commands_without_a_store_port() -> None:
    path = ROOT / "host_brain/runtime/guardian_operations.py"
    source = path.read_text(encoding="utf-8")
    owned = _owned_definitions(path)

    assert "_event_store" not in source
    assert "_restore_guardian_journal" not in owned
    assert "_apply_event" not in owned
    assert "_append" not in owned
    assert {
        "observe_dependency",
        "acknowledge_recovery",
        "reconcile_prepared_recoveries",
        "enter_maintenance",
        "enter_sleep",
        "record_slo_breach",
        "resolve_slo_breach",
    }.issubset(owned)


def test_guardian_contracts_are_pure_and_reexported() -> None:
    path = ROOT / "host_brain/runtime/guardian_contracts.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    imported_modules = {
        str(node.module or "")
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }

    assert not any(
        module.startswith(
            (
                "host_brain.integrations",
                "host_brain.observability",
                "host_brain.persistence",
            )
        )
        for module in imported_modules
    )
    assert runtime_guardian.GuardianEventV1 is guardian_contracts.GuardianEventV1
    assert runtime_guardian.GuardianSnapshotV1 is guardian_contracts.GuardianSnapshotV1
    assert runtime_guardian.RuntimeGuardianError is guardian_contracts.RuntimeGuardianError
