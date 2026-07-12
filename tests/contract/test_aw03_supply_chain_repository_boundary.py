from __future__ import annotations

import ast
from pathlib import Path

from host_brain.persistence.supply_chain_repository import (
    BundleKind,
    BundleState,
    SKILL_SUPPLY_CHAIN_MIGRATION_ASSET_KEY,
    SKILL_SUPPLY_CHAIN_MIGRATION_SHA256,
    SQLiteSkillSupplyChainStore,
    SkillSupplyChainStoreError,
    SupplyChainAcceptance,
)
from host_brain.skills.supply_chain_store import BundleKind as CompatibilityKind
from host_brain.skills.supply_chain_store import BundleState as CompatibilityState
from host_brain.skills.supply_chain_store import (
    SQLiteSkillSupplyChainStore as CompatibilityStore,
)
from host_brain.skills.supply_chain_store import (
    SkillSupplyChainStoreError as CompatibilityError,
)
from host_brain.skills.supply_chain_store import (
    SupplyChainAcceptance as CompatibilityAcceptance,
)


ROOT = Path(__file__).resolve().parents[2]
ADAPTER_MODULE = "host_brain.persistence.supply_chain_repository"
PRODUCTION_IMPORTS = {
    "host_brain/skills/supply_chain_service.py": {
        "BundleKind",
        "SkillSupplyChainStoreError",
    },
    "host_brain/skills/signed_intake_composition.py": {
        "SQLiteSkillSupplyChainStore",
    },
}


def _imports_from(path: Path, module: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == module
        for alias in node.names
    }


def test_skills_compatibility_facade_has_no_persistence_implementation() -> None:
    source = (ROOT / "host_brain" / "skills" / "supply_chain_store.py").read_text(
        encoding="utf-8"
    )
    forbidden = (
        "sqlite3",
        "_connect",
        ".execute(",
        "BEGIN IMMEDIATE",
        "CREATE TABLE",
        "CREATE INDEX",
        "SELECT ",
        "INSERT ",
        "UPDATE ",
        "DELETE ",
    )
    for token in forbidden:
        assert token not in source, f"skills facade still contains {token}"


def test_legacy_contract_types_preserve_identity() -> None:
    assert CompatibilityStore is SQLiteSkillSupplyChainStore
    assert CompatibilityError is SkillSupplyChainStoreError
    assert CompatibilityKind is BundleKind
    assert CompatibilityState is BundleState
    assert CompatibilityAcceptance is SupplyChainAcceptance


def test_production_services_import_persistence_adapter_directly() -> None:
    for relative_path, required_names in PRODUCTION_IMPORTS.items():
        path = ROOT / relative_path
        assert required_names <= _imports_from(path, ADAPTER_MODULE), relative_path
        assert not (
            required_names
            & _imports_from(path, "host_brain.skills.supply_chain_store")
        ), relative_path


def test_adapter_retains_central_0060_authority_and_transaction_boundary() -> None:
    source = (
        ROOT / "host_brain" / "persistence" / "supply_chain_repository.py"
    ).read_text(encoding="utf-8")
    assert SKILL_SUPPLY_CHAIN_MIGRATION_ASSET_KEY == "0060#skill_supply_chain"
    assert (
        SKILL_SUPPLY_CHAIN_MIGRATION_SHA256
        == "ea7471978f063b6152f3f1699f6896fe5782bede412214b2162b3592b9a1618e"
    )
    assert "def _transaction(" in source
    assert 'conn.execute("BEGIN IMMEDIATE")' in source
    assert "schema_migration_assets" in source
