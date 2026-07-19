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


def test_rollout_authority_retains_only_guardian_adapter_and_contract_exports() -> None:
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
        source = path.read_text(encoding="utf-8")
        assert "host_brain.composition.service_catalog_startup" not in source
        assert "host_brain.service_catalog.startup_coordinator" in source


def test_composition_module_is_only_a_compatibility_reexport() -> None:
    tree = _tree(COMPOSITION_COMPATIBILITY)
    assert not any(isinstance(node, (ast.ClassDef, ast.FunctionDef)) for node in tree.body)
    source = COMPOSITION_COMPATIBILITY.read_text(encoding="utf-8")
    assert "host_brain.service_catalog.startup_coordinator" in source
    assert STARTUP_COORDINATOR.exists()


def test_legacy_exports_preserve_contract_object_identity() -> None:
    from host_brain.composition.service_catalog_startup import (
        ActivationResult as CompatibilityActivationResult,
        PreparedServiceStart as CompatibilityPreparedStart,
        ServiceCatalogStartupCoordinator as CompatibilityCoordinator,
    )
    from host_brain.contracts.service_catalog import (
        CatalogCoordinatorAuthorityV1 as ContractAuthority,
        CatalogMode as ContractMode,
        CatalogRolloutOperationV1 as ContractOperation,
        ServiceCatalogRolloutError as ContractError,
        ServiceCatalogRolloutRepository as ContractRepository,
    )
    from host_brain.service_catalog.models import CatalogMode as ModelMode
    from host_brain.service_catalog.rollout_authority import (
        CatalogCoordinatorAuthorityV1 as LegacyAuthority,
        CatalogRolloutOperationV1 as LegacyOperation,
        ServiceCatalogRolloutError as LegacyError,
        ServiceCatalogRolloutRepository as LegacyRepository,
    )
    from host_brain.service_catalog.startup_coordinator import (
        ActivationResult as ApplicationActivationResult,
        PreparedServiceStart as ApplicationPreparedStart,
        ServiceCatalogStartupCoordinator as ApplicationCoordinator,
    )

    assert ModelMode is ContractMode
    assert LegacyAuthority is ContractAuthority
    assert LegacyOperation is ContractOperation
    assert LegacyError is ContractError
    assert LegacyRepository is ContractRepository
    assert CompatibilityActivationResult is ApplicationActivationResult
    assert CompatibilityPreparedStart is ApplicationPreparedStart
    assert CompatibilityCoordinator is ApplicationCoordinator
