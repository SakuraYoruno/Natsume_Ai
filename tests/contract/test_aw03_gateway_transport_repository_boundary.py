from __future__ import annotations

import ast
from pathlib import Path

from host_brain.eventing import SQLiteGatewayTransportStore as PublicStore
from host_brain.eventing.gateway_transport import (
    GatewayTransportStore as CompatibilityAlias,
)
from host_brain.eventing.gateway_transport import (
    SQLiteGatewayTransportStore as CompatibilityStore,
)
from host_brain.persistence.gateway_transport_repository import (
    GATEWAY_TRANSPORT_MIGRATION_ASSET_KEY,
    GATEWAY_TRANSPORT_MIGRATION_SHA256,
    SQLiteGatewayTransportStore,
)


ROOT = Path(__file__).resolve().parents[2]
ADAPTER_MODULE = "host_brain.persistence.gateway_transport_repository"


def _imports_from(path: Path, module: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == module
        for alias in node.names
    }


def test_eventing_compatibility_facade_has_no_persistence_implementation() -> None:
    source = (ROOT / "host_brain" / "eventing" / "gateway_transport.py").read_text(
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
        assert token not in source, f"eventing facade still contains {token}"


def test_legacy_public_and_direct_adapter_imports_preserve_type_identity() -> None:
    assert CompatibilityStore is SQLiteGatewayTransportStore
    assert CompatibilityAlias is SQLiteGatewayTransportStore
    assert PublicStore is SQLiteGatewayTransportStore


def test_edge_production_bootstrap_imports_persistence_adapter_directly() -> None:
    path = ROOT / "edge_client" / "gateway" / "bootstrap.py"
    assert "SQLiteGatewayTransportStore" in _imports_from(path, ADAPTER_MODULE)
    assert "SQLiteGatewayTransportStore" not in _imports_from(
        path,
        "host_brain.eventing.gateway_transport",
    )


def test_adapter_retains_central_0059_schema_authority_and_transaction_boundary() -> None:
    source = (
        ROOT / "host_brain" / "persistence" / "gateway_transport_repository.py"
    ).read_text(encoding="utf-8")
    assert GATEWAY_TRANSPORT_MIGRATION_ASSET_KEY == "0059#gateway_transport"
    assert (
        GATEWAY_TRANSPORT_MIGRATION_SHA256
        == "bad91de55866c6e9ffffed8a8d249abf077b146bce6b3705a5f95d11b91a8944"
    )
    assert "def _transaction(" in source
    assert 'conn.execute("BEGIN IMMEDIATE")' in source
    assert "schema_migration_assets" in source
