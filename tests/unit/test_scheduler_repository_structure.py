from __future__ import annotations

import ast
import json
from pathlib import Path

from host_brain.persistence.scheduler_clock_retry_store import (
    SchedulerClockRetryStoreMixin,
)
from host_brain.persistence.scheduler_repository import (
    SchedulerDefinitionRepository,
    SchedulerRepositoryError,
)
from host_brain.persistence.scheduler_repository_primitives import (
    SchedulerRepositoryError as PrimitiveSchedulerRepositoryError,
)


ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_PATH = ROOT / "host_brain" / "persistence" / "scheduler_repository.py"
RETRY_MIXIN_PATH = (
    ROOT / "host_brain" / "persistence" / "scheduler_clock_retry_store.py"
)
PRIMITIVES_PATH = (
    ROOT / "host_brain" / "persistence" / "scheduler_repository_primitives.py"
)
POLICY_PATH = ROOT / "configs" / "release" / "core_architecture_policy.json"

RETRY_METHODS = (
    "claim_clock_retry",
    "renew_clock_retry",
    "defer_clock_retry",
    "clear_clock_retry",
    "clock_retry_summary",
    "_clock_retry_record",
    "_transfer_due_retry_claim",
)


def _source_lines(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def _class_node(path: Path, name: str) -> ast.ClassDef:
    module = ast.parse(path.read_text(encoding="utf-8"))
    return next(
        node
        for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == name
    )


def _method(class_node: ast.ClassDef, name: str) -> ast.FunctionDef:
    return next(
        node
        for node in class_node.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def test_scheduler_repository_stays_below_ceiling_with_margin() -> None:
    assert _source_lines(REPOSITORY_PATH) <= 3_050
    assert _source_lines(RETRY_MIXIN_PATH) <= 700
    assert _source_lines(PRIMITIVES_PATH) <= 200

    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8-sig"))
    scheduler_entries = [
        item
        for item in policy["cohesive_large_files"]
        if item["path"] == "host_brain/persistence/scheduler_repository.py"
    ]
    assert len(scheduler_entries) == 1
    assert scheduler_entries[0]["max_lines"] == 3_200
    approved_paths = {item["path"] for item in policy["cohesive_large_files"]}
    assert "host_brain/persistence/scheduler_clock_retry_store.py" not in approved_paths
    assert "host_brain/persistence/scheduler_repository_primitives.py" not in approved_paths


def test_clock_retry_mixin_preserves_one_repository_and_method_owner() -> None:
    assert SchedulerRepositoryError is PrimitiveSchedulerRepositoryError
    assert SchedulerDefinitionRepository.__mro__[1] is SchedulerClockRetryStoreMixin
    for name in RETRY_METHODS:
        assert getattr(SchedulerDefinitionRepository, name) is getattr(
            SchedulerClockRetryStoreMixin,
            name,
        )

    concrete = _class_node(REPOSITORY_PATH, "SchedulerDefinitionRepository")
    concrete_methods = {
        node.name for node in concrete.body if isinstance(node, ast.FunctionDef)
    }
    assert not concrete_methods.intersection(RETRY_METHODS)


def test_clock_retry_mixin_borrows_canonical_connection_and_admission_uow() -> None:
    mixin = _class_node(RETRY_MIXIN_PATH, "SchedulerClockRetryStoreMixin")
    method_names = {
        node.name for node in mixin.body if isinstance(node, ast.FunctionDef)
    }
    assert method_names == set(RETRY_METHODS)
    assert "__init__" not in method_names
    assert "_connect" not in method_names

    source = RETRY_MIXIN_PATH.read_text(encoding="utf-8")
    assert "sqlite3.connect" not in source

    transfer = _method(mixin, "_transfer_due_retry_claim")
    assert [argument.arg for argument in transfer.args.args[:2]] == ["self", "conn"]
    transfer_connect_calls = [
        node
        for node in ast.walk(transfer)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_connect"
    ]
    assert transfer_connect_calls == []

    repository = _class_node(REPOSITORY_PATH, "_SchedulerSQLiteRepository")
    assert sum(
        isinstance(node, ast.FunctionDef) and node.name == "_connect"
        for node in repository.body
    ) == 1
