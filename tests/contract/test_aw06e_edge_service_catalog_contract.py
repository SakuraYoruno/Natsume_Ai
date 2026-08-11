from __future__ import annotations

import ast
import json
from pathlib import Path

from natsume_contracts.edge.service_catalog_v1 import (
    EDGE_HEALTH_QUERY_SCHEMA_VERSION,
    EDGE_SERVICE_CATALOG_SCHEMA_VERSION,
    EdgeHealthSnapshot,
    build_edge_service_catalog_snapshot,
    edge_required_services,
)
from host_brain.app.main import build_startup_report
from host_brain.core.errors import SERVICE_UNAVAILABLE_EDGE_RPC
from host_brain.core.services import build_default_service_registry


ROOT = Path(__file__).resolve().parents[2]
HOST_BOUNDARY_FILES = (
    ROOT / "host_brain" / "app" / "main.py",
    ROOT / "host_brain" / "app" / "health_routes.py",
    ROOT / "host_brain" / "core" / "services.py",
)


class _UnavailableEdgeHealthQuery:
    def query(self) -> EdgeHealthSnapshot:
        return EdgeHealthSnapshot(
            schema_version=EDGE_HEALTH_QUERY_SCHEMA_VERSION,
            statuses={
                "edge.rpc.gateway": {
                    "available": False,
                    "unavailable_kind": "edge_rpc",
                    "detail": "edge catalog query timeout",
                }
            },
        )


def test_public_catalog_is_the_single_versioned_service_descriptor() -> None:
    descriptors = edge_required_services()
    snapshot = build_edge_service_catalog_snapshot(descriptors)
    registry = build_default_service_registry(edge_service_catalog=descriptors)

    assert snapshot["schema_version"] == EDGE_SERVICE_CATALOG_SCHEMA_VERSION
    assert snapshot["authority"] == "edge"
    assert [item.service_id for item in descriptors] == [
        "edge.rpc.gateway",
        "edge.rpc.asr",
        "edge.rpc.tts",
        "edge.rpc.barge_in",
        "edge.rpc.resource_gate",
    ]
    assert [item.service_id for item in registry.all() if item.owner == "edge"] == [
        item.service_id for item in descriptors
    ]


def test_host_startup_consumes_injected_health_and_never_reads_edge_config() -> None:
    edge_secret = "must-not-cross-host-boundary"
    report = build_startup_report(
        host_env={
            "Natsume_ENV": "dev",
            "HOST_LLM_PROVIDER": "mock",
            "HOST_ENABLE_RETRIEVAL": "true",
            "HOST_ENABLE_VISION": "true",
        },
        edge_env={"EDGE_HMAC_SHARED_KEY": edge_secret},
        edge_health_query=_UnavailableEdgeHealthQuery(),
        request_id="req_aw06e_health_query",
        ts_ms=1_800_260_000_000,
    )

    gateway = next(
        row for row in report["services"]
        if row["service_id"] == "edge.rpc.gateway"
    )
    assert gateway["reason_code"] == SERVICE_UNAVAILABLE_EDGE_RPC
    assert report["config"]["edge"]["authority"] == "edge"
    assert edge_secret not in json.dumps(report, ensure_ascii=False)


def test_host_boundary_has_no_edge_private_implementation_imports() -> None:
    private_imports: list[tuple[str, str]] = []
    for path in HOST_BOUNDARY_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and str(node.module or "").startswith("edge_client"):
                private_imports.append((path.as_posix(), str(node.module)))
            elif isinstance(node, ast.Import):
                private_imports.extend(
                    (path.as_posix(), alias.name)
                    for alias in node.names
                    if alias.name.startswith("edge_client")
                )

    assert private_imports == []
