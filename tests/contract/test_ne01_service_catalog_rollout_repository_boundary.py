from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
AUTHORITY = ROOT / "host_brain" / "service_catalog" / "rollout_authority.py"
CONTRACTS = ROOT / "host_brain" / "contracts" / "service_catalog.py"
REPOSITORY = (
    ROOT
    / "host_brain"
    / "persistence"
    / "service_catalog_rollout_repository.py"
)
RUNNER = ROOT / "host_brain" / "service_catalog" / "rollout_runtime.py"
HOST_STARTUP = ROOT / "host_brain" / "service_catalog" / "host_startup.py"
STARTUP_COORDINATOR = (
    ROOT / "host_brain" / "service_catalog" / "startup_coordinator.py"
)
COMPOSITION_COMPATIBILITY = (
    ROOT / "host_brain" / "composition" / "service_catalog_startup.py"
)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_rollout_authority_retains_only_guardian_adapter() -> None:
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
    assert class_names == {"ServiceCatalogRolloutGuardianObserver"}
    assert "ServiceCatalogRolloutStore" not in class_names
    assert "SQLiteServiceCatalogRolloutRepository" not in AUTHORITY.read_text(
        encoding="utf-8"
    )


def test_rollout_contracts_are_runtime_independent_and_own_stable_types() -> None:
    tree = _tree(CONTRACTS)
    class_names = {
        node.name for node in tree.body if isinstance(node, ast.ClassDef)
    }
    imported_modules = {
        str(node.module or "")
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }

    assert {
        "CatalogMode",
        "CatalogCoordinatorAuthorityV1",
        "CatalogRolloutOperationV1",
        "ServiceCatalogRolloutError",
        "ServiceCatalogRolloutRepository",
    }.issubset(class_names)
    assert not any(
        module.startswith(
            (
                "host_brain.service_catalog",
                "host_brain.composition",
                "host_brain.persistence",
            )
        )
        for module in imported_modules
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
    source = REPOSITORY.read_text(encoding="utf-8")
    assert "host_brain.contracts.service_catalog" in source
    assert "host_brain.service_catalog.rollout_authority" not in source


def test_rollout_runner_depends_on_protocol_not_sqlite_repository() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    assert "ServiceCatalogRolloutRepository" in source
    assert "SQLiteServiceCatalogRolloutRepository" not in source
    assert "host_brain.persistence.service_catalog_rollout_repository" not in source


def test_application_startup_paths_do_not_import_composition() -> None:
    for path in (HOST_STARTUP, RUNNER):
        tree = _tree(path)
        imported_modules = {
            str(node.module or "")
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        assert not any(
            module.startswith("host_brain.composition")
            for module in imported_modules
        )
        assert "host_brain.service_catalog.startup_coordinator" in path.read_text(
            encoding="utf-8"
        )


def test_composition_compatibility_module_is_removed() -> None:
    assert not COMPOSITION_COMPATIBILITY.exists()
    assert STARTUP_COORDINATOR.exists()


def test_application_uses_canonical_contract_object_identity() -> None:
    from host_brain.contracts.service_catalog import (
        CatalogCoordinatorAuthorityV1 as ContractAuthority,
        CatalogMode as ContractMode,
        CatalogRolloutOperationV1 as ContractOperation,
        ServiceCatalogRolloutError as ContractError,
        ServiceCatalogRolloutRepository as ContractRepository,
    )
    from host_brain.service_catalog.models import CatalogMode as ModelMode
    import host_brain.service_catalog.rollout_authority as rollout_authority
    from host_brain.service_catalog.startup_coordinator import (
        ActivationResult as ApplicationActivationResult,
        PreparedServiceStart as ApplicationPreparedStart,
        ServiceCatalogStartupCoordinator as ApplicationCoordinator,
    )

    assert ModelMode is ContractMode
    assert all(
        item.__module__ == "host_brain.contracts.service_catalog"
        for item in (
            ContractAuthority,
            ContractOperation,
            ContractError,
            ContractRepository,
        )
    )
    assert not hasattr(rollout_authority, "CatalogCoordinatorAuthorityV1")
    assert not hasattr(rollout_authority, "CatalogRolloutOperationV1")
    assert not hasattr(rollout_authority, "ServiceCatalogRolloutError")
    assert not hasattr(rollout_authority, "ServiceCatalogRolloutRepository")
    assert ApplicationActivationResult.__module__ == (
        "host_brain.service_catalog.startup_coordinator"
    )
    assert ApplicationPreparedStart.__module__ == (
        "host_brain.service_catalog.startup_coordinator"
    )
    assert ApplicationCoordinator.__module__ == (
        "host_brain.service_catalog.startup_coordinator"
    )
