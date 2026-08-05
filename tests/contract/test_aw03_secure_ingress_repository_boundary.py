from __future__ import annotations

import ast
from pathlib import Path

import host_brain.secure_ingress as secure_ingress
from host_brain.persistence.secure_ingress_repository import (
    SECURE_INGRESS_MIGRATION_ASSET_KEY,
    SECURE_INGRESS_MIGRATION_SHA256,
)
ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_MODULE = "host_brain.persistence.secure_ingress_repository"
PRODUCTION_IMPORTS = {
    "host_brain/composition/command_ingress.py": {"SQLiteSecureIngressStore"},
    "host_brain/app/observability_runtime/command_composition.py": {"SQLiteSecureIngressStore"},
    "host_brain/skills/signed_intake_composition.py": {"SQLiteSecureIngressStore"},
    "host_brain/pentest/production_runtime.py": {"SQLiteSecureIngressStore"},
    "host_brain/persistence/pentest_ingress_authority_repository.py": {
        "SECURE_INGRESS_MIGRATION_ASSET_KEY",
        "SECURE_INGRESS_MIGRATION_SHA256",
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


def test_secure_ingress_compatibility_entrypoint_is_retired() -> None:
    assert not (ROOT / "host_brain" / "secure_ingress" / "store.py").exists()


def test_secure_ingress_production_composition_imports_persistence_adapter() -> None:
    for relative_path, required_names in PRODUCTION_IMPORTS.items():
        path = ROOT / relative_path
        assert required_names <= _imports_from(path, REPOSITORY_MODULE), relative_path
        assert not (
            required_names
            & _imports_from(path, "host_brain.secure_ingress.store")
        ), relative_path


def test_secure_ingress_package_does_not_reexport_persistence_adapters() -> None:
    assert "SQLiteSecureIngressStore" not in secure_ingress.__all__
    assert "SecureIngressStoreError" not in secure_ingress.__all__
    assert not hasattr(secure_ingress, "SQLiteSecureIngressStore")
    assert not hasattr(secure_ingress, "SecureIngressStoreError")


def test_secure_ingress_migration_contract_is_unchanged() -> None:
    assert SECURE_INGRESS_MIGRATION_ASSET_KEY == "0058#secure_ingress"
    assert (
        SECURE_INGRESS_MIGRATION_SHA256
        == "e5b8790b6ac9058fb48ef0a2af9aa162985985eb4ee2eeab61788b233047b95b"
    )
