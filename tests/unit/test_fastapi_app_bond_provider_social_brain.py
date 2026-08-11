from __future__ import annotations

import asyncio
import inspect
import json

import host_brain.app.asgi as asgi
import host_brain.app.bond_routes as bond_routes
import host_brain.app.host_runtime_shutdown as host_runtime_shutdown
import host_brain.app.http_hooks as http_hooks
import host_brain.app.tribe_composition as tribe_composition
from host_brain.app.host_route_ports import HostRoutePorts
from host_brain.composition import host_application
from host_brain.contracts import host_lifecycle
from host_brain.tribe import application_ports as tribe_application_ports


def test_production_asgi_does_not_compose_legacy_bond_runtime() -> None:
    production_sources = "\n".join(
        (
            inspect.getsource(asgi.create_app),
            inspect.getsource(host_application),
            inspect.getsource(http_hooks),
            inspect.getsource(host_runtime_shutdown),
            inspect.getsource(tribe_composition),
            inspect.getsource(host_lifecycle),
            inspect.getsource(tribe_application_ports),
        )
    )

    for retired_name in (
        "bond_engine_provider",
        "HostBondRuntimeOwner",
        "BondDynamicsWriter",
        "BondUpdateQueue",
        "bond_dynamics_writer",
        "begin_bond_runtime",
        "close_bond_runtime",
        "bond_update_queue",
        "bond_stage_engine",
    ):
        assert retired_name not in production_sources


def test_missing_legacy_bond_provider_is_stable_retired_response() -> None:
    response = asyncio.run(
        bond_routes.get_bond_summary(
            "user-001",
            _route_ports=HostRoutePorts(),
        )
    )

    assert response.status_code == 410
    assert response.headers["x-reason-code"] == "LEGACY_BOND_READ_RETIRED"
    payload = json.loads(response.body)
    assert payload["status"] == "RETIRED"
    assert payload["reason_code"] == "LEGACY_BOND_READ_RETIRED"
