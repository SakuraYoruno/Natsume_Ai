from __future__ import annotations

import ast
from pathlib import Path

from natsume_contracts.edge.embodiment_v1 import build_embodiment_command
from natsume_contracts.edge.transport_v1 import (
    EDGE_SESSION_LEASE_SCHEMA_VERSION,
    EdgeSessionLease,
    bind_command_to_lease,
    parse_edge_session_lease,
    stable_lease_id,
)


ROOT = Path(__file__).resolve().parents[2]


def test_session_lease_wire_contract_and_command_fence_binding() -> None:
    lease = EdgeSessionLease(
        session_id="host-session",
        lease_id=stable_lease_id(
            session_id="host-session", owner_id="host", fencing_token=3
        ),
        owner_id="host",
        fencing_token=3,
        issued_at_ms=1_000,
        expires_at_ms=31_000,
    )
    assert lease.schema_version == EDGE_SESSION_LEASE_SCHEMA_VERSION
    assert parse_edge_session_lease(lease.as_dict()) == lease

    command = build_embodiment_command(
        kind="voice",
        action="speak",
        payload={"text": "hello"},
        authority_ref="authority://host/device",
        grant_ref="grant://host/device/1",
        deadline_ms=20_000,
        idempotency_key="contract-command",
        request_id="request-contract",
        issued_at_ms=1_000,
    )
    bound = bind_command_to_lease(command, lease)
    assert bound.session_id == lease.session_id
    assert bound.lease_id == lease.lease_id
    assert bound.fencing_token == lease.fencing_token
    assert bound.as_dict()["fencing_token"] == 3


def test_edge_transport_runtime_is_host_independent_and_owns_durable_semantics() -> None:
    path = ROOT / "edge_client/gateway/embodiment_runtime.py"
    source = path.read_text(encoding="utf-8")
    persistence_source = (
        ROOT / "edge_client/persistence/sqlite.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(str(node.module or ""))

    assert not any(name == "host_brain" or name.startswith("host_brain.") for name in imports)
    assert "BEGIN IMMEDIATE" in persistence_source
    assert "sqlite3.connect" not in source
    assert "EdgeSqliteDatabase(" in source
    assert "DISPATCHING" in source
    assert "IN_DOUBT" in source
    assert "fencing_token" in source
    assert "receipt_sha256" in source
    assert "replay denied" in source


def test_production_endpoint_and_host_port_use_session_fenced_durable_runtime() -> None:
    endpoint = (ROOT / "edge_client/app/fastapi_app.py").read_text(encoding="utf-8")
    migration = (ROOT / "edge_client/gateway/migration_runner.py").read_text(encoding="utf-8")
    runtime = (ROOT / "edge_client/gateway/embodiment_runtime.py").read_text(encoding="utf-8")
    host_port = (ROOT / "host_brain/channels/embodiment.py").read_text(encoding="utf-8")

    assert '@app.post("/v1/edge/embodiment/session")' in endpoint
    assert "SQLiteEdgeEmbodimentStore(" in endpoint
    assert "apply_edge_gateway_migrations(" in endpoint
    assert "CREATE TABLE" not in runtime
    assert "executescript" in migration
    assert "DurableEmbodimentRuntime(" in endpoint
    assert "transport_runtime.execute(" in endpoint
    assert "embodiment_receipts" not in endpoint
    assert "receipts_lock" not in endpoint
    assert 'path="/v1/edge/embodiment/session"' in host_port
    assert "parse_edge_session_lease" in host_port
    assert "bind_command_to_lease(command, lease)" in host_port
    assert "leased_command.as_dict()" in host_port
