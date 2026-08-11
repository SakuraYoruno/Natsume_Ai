from __future__ import annotations

import ast
from pathlib import Path

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


def test_eventing_compatibility_facade_is_deleted() -> None:
    assert not (ROOT / "host_brain" / "eventing" / "gateway_transport.py").exists()


def test_edge_production_bootstrap_uses_contract_injected_store_factory() -> None:
    path = ROOT / "edge_client" / "gateway" / "bootstrap.py"
    source = path.read_text(encoding="utf-8")
    assert "GatewayTransportStoreFactory" in _imports_from(
        path, "natsume_contracts.edge.v1"
    )
    assert "host_brain." not in source


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
