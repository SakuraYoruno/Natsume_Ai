"""Repository boundary contracts for TASK-0492 Service Catalog."""

from __future__ import annotations

from pathlib import Path

import pytest

from host_brain.service_catalog import (
    AuthKind,
    CatalogMode,
    EndpointKind,
    build_local_catalog,
    load_catalog_mode,
    validate_catalog,
)
from host_brain.service_catalog.preflight import (
    ISSUE_LISTENER_CONFLICT,
    ISSUE_PUBLIC_AUTH_REQUIRED,
)


ROOT = Path(__file__).resolve().parents[2]
CATALOG_ROOTS = (
    ROOT / "host_brain" / "service_catalog",
    ROOT / "host_brain" / "composition",
)


def _source_under(*roots: Path) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for root in roots
        for path in sorted(root.rglob("*.py"))
    )


def _issue_codes(snapshot: object) -> set[str]:
    return {issue.reason_code for issue in validate_catalog(snapshot)}


def test_catalog_mode_is_audit_by_default_and_invalid_values_are_explicit() -> None:
    default = load_catalog_mode({})
    invalid = load_catalog_mode({"HOST_SERVICE_CATALOG_MODE": "silent-auto-fix"})

    assert default.mode is CatalogMode.AUDIT
    assert default.valid is True
    assert invalid.mode is CatalogMode.AUDIT
    assert invalid.valid is False
    assert invalid.reason_code == "SERVICE_CATALOG_MODE_INVALID"
    assert "HOST_SERVICE_CATALOG_MODE=AUDIT" in (
        ROOT / ".env.example"
    ).read_text(encoding="utf-8")


def test_static_catalog_needs_no_empty_durable_migration() -> None:
    assert not (
        ROOT
        / "host_brain"
        / "audit"
        / "migrations"
        / "0062_service_catalog.sql"
    ).exists()


def test_catalog_and_composition_do_not_import_runtime_owners() -> None:
    source = _source_under(*CATALOG_ROOTS)
    forbidden = (
        "from edge_client",
        "import edge_client",
        "from desktop_pet",
        "import desktop_pet",
        "from tools.mateengine_bridge_server",
        "from host_brain.app",
        "import host_brain.app",
    )

    assert all(token not in source for token in forbidden)


def test_default_inventory_has_unique_identity_protocol_and_health_contracts() -> None:
    snapshot = build_local_catalog({"EDGE_AVATAR_BACKEND": "vts"})
    endpoint_ids = [endpoint.endpoint_id for endpoint in snapshot.endpoints]

    assert len(endpoint_ids) == len(set(endpoint_ids))
    assert _issue_codes(snapshot) == set()
    for endpoint in snapshot.endpoints:
        assert endpoint.owner_id
        assert endpoint.protocols
        assert endpoint.health.expected_owner_id == endpoint.owner_id
        assert (
            endpoint.health.expected_protocol,
            endpoint.health.expected_protocol_version,
        ) in {(item.name, item.version) for item in endpoint.protocols}


def test_18770_has_one_default_owner_and_vrm_collision_is_rejected() -> None:
    default = build_local_catalog({"EDGE_AVATAR_BACKEND": "vts"})
    default_claims = {
        endpoint.endpoint_id
        for endpoint in default.endpoints
        if endpoint.enabled
        and endpoint.kind is EndpointKind.LISTENER
        and endpoint.port == 18770
    }
    assert default_claims == set()

    with pytest.raises(ValueError, match="SERVICE_CATALOG_EMBODIMENT_OWNER_REQUIRED"):
        build_local_catalog(
            {
                "EDGE_AVATAR_BACKEND": "vrm",
                "HOST_MATEENGINE_BRIDGE_ENABLED": "true",
            }
        )

    selected = build_local_catalog(
        {
            "EDGE_AVATAR_BACKEND": "vrm",
            "HOST_MATEENGINE_BRIDGE_ENABLED": "true",
            "NATSUME_EMBODIMENT_BRIDGE_OWNER": "mateengine",
        }
    )
    selected_claims = {
        endpoint.endpoint_id
        for endpoint in selected.endpoints
        if endpoint.enabled
        and endpoint.kind is EndpointKind.LISTENER
        and endpoint.port == 18770
    }
    assert selected_claims == {"host.mateengine.bridge.http"}
    assert ISSUE_LISTENER_CONFLICT not in _issue_codes(selected)


def test_safe_vts_profile_has_no_static_catalog_issues() -> None:
    snapshot = build_local_catalog(
        {
            "EDGE_AVATAR_BACKEND": "vts",
            "MATEENGINE_BRIDGE_UDP_HOST": "127.0.0.1",
        }
    )

    assert _issue_codes(snapshot) == set()


def test_dependencies_disabled_endpoints_and_protocols_do_not_claim_listeners() -> None:
    snapshot = build_local_catalog({"EDGE_AVATAR_BACKEND": "vrm"})
    dependency = snapshot.by_id()["edge.avatar.vrm.dependency"]
    disabled = snapshot.by_id()["desktop.pet.http"]

    assert dependency.enabled is True
    assert dependency.kind is EndpointKind.DEPENDENCY
    assert disabled.enabled is False
    assert ISSUE_LISTENER_CONFLICT not in _issue_codes(snapshot)
    assert {
        endpoint.endpoint_id
        for endpoint in snapshot.endpoints
        if endpoint.enabled
        and endpoint.kind is EndpointKind.LISTENER
        and endpoint.port == 18770
    } == {"edge.unity.avatar.bridge.http"}

    no_owner_collision = build_local_catalog(
        {
            "EDGE_AVATAR_BACKEND": "vrm",
            "HOST_MATEENGINE_BRIDGE_ENABLED": "false",
        }
    )
    assert ISSUE_LISTENER_CONFLICT not in _issue_codes(no_owner_collision)


def test_public_or_wildcard_listener_requires_explicit_auth_policy() -> None:
    insecure = build_local_catalog(
        {
            "EDGE_RPC_HOST": "0.0.0.0",
            "EDGE_ALLOW_INSECURE_AUTH": "true",
            "MATEENGINE_BRIDGE_UDP_HOST": "127.0.0.1",
        }
    )
    secured = build_local_catalog(
        {
            "EDGE_RPC_HOST": "0.0.0.0",
            "EDGE_ALLOW_INSECURE_AUTH": "false",
            "MATEENGINE_BRIDGE_UDP_HOST": "127.0.0.1",
        }
    )

    assert insecure.by_id()["edge.rpc.http"].auth_kind is AuthKind.NONE
    assert ISSUE_PUBLIC_AUTH_REQUIRED in _issue_codes(insecure)
    assert ISSUE_PUBLIC_AUTH_REQUIRED not in _issue_codes(secured)


def test_unique_integration_uses_injected_probes_not_real_side_effects() -> None:
    source = (
        ROOT / "tests" / "integration" / "test_task0492_service_catalog_integration.py"
    ).read_text(encoding="utf-8")
    forbidden = (
        "import socket",
        "from socket",
        "subprocess",
        "Popen(",
        "os.system(",
        "ReadOnlySocketBindProbe",
    )

    assert all(token not in source for token in forbidden)
