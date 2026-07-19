from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
AUTHORITY = ROOT / "host_brain" / "service_catalog" / "rollout_authority.py"
REPOSITORY = (
    ROOT
    / "host_brain"
    / "persistence"
    / "service_catalog_rollout_repository.py"
)
RUNNER = ROOT / "host_brain" / "service_catalog" / "rollout_runtime.py"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_rollout_authority_is_persistence_free_and_does_not_reexport_concrete() -> None:
    tree = _tree(AUTHORITY)
    imported_roots = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_modules = {
        str(node.module or "")
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    class_names = {
        node.name for node in tree.body if isinstance(node, ast.ClassDef)
    }

    assert "sqlite3" not in imported_roots
    assert "pathlib" not in imported_modules
    assert not any(module.startswith("host_brain.persistence") for module in imported_modules)
    assert "ServiceCatalogRolloutRepository" in class_names
    assert "ServiceCatalogRolloutStore" not in class_names
    assert "SQLiteServiceCatalogRolloutRepository" not in AUTHORITY.read_text(
        encoding="utf-8"
    )


def test_sqlite_rollout_repository_is_owned_by_persistence_layer() -> None:
    tree = _tree(REPOSITORY)
    class_names = {
        node.name for node in tree.body if isinstance(node, ast.ClassDef)
    }
    assert "SQLiteServiceCatalogRolloutRepository" in class_names
    assert any(
        isinstance(node, ast.Import)
        and any(alias.name == "sqlite3" for alias in node.names)
        for node in tree.body
    )


def test_rollout_runner_depends_on_protocol_not_sqlite_repository() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    assert "ServiceCatalogRolloutRepository" in source
    assert "SQLiteServiceCatalogRolloutRepository" not in source
    assert "host_brain.persistence.service_catalog_rollout_repository" not in source
