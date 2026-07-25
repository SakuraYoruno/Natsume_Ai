from __future__ import annotations

import ast
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from host_brain.app import health_routes, memory_routes
from host_brain.app.digital_life_routes import create_digital_life_router
from host_brain.app.host_route_ports import HostRoutePorts
from host_brain.app.observability_runtime import domain_command_ports


ROOT = Path(__file__).resolve().parents[2]
HOST_ROOT = ROOT / "host_brain"
LOCATOR_MODULES = frozenset(
    {
        "host_brain.composition.process_services",
        "host_brain.composition.process_runtime_services",
        "host_brain.composition.process_environment",
        "host_brain.composition.process_service_container",
    }
)
LOCATOR_EXPORTS = frozenset(
    module.rsplit(".", 1)[-1] for module in LOCATOR_MODULES
)


def _imports_process_locator(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
            alias.name in LOCATOR_MODULES for alias in node.names
        ):
            return True
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module in LOCATOR_MODULES:
            return True
        if node.module == "host_brain.composition" and any(
            alias.name in LOCATOR_EXPORTS for alias in node.names
        ):
            return True
    return False


def test_production_tree_has_no_process_locator_imports_or_modules() -> None:
    actual = {
        path.relative_to(ROOT).as_posix()
        for path in HOST_ROOT.rglob("*.py")
        if _imports_process_locator(path)
    }
    retired_files = {
        "host_brain/composition/process_services.py",
        "host_brain/composition/process_runtime_services.py",
        "host_brain/composition/process_environment.py",
        "host_brain/composition/process_service_container.py",
    }

    assert actual == set()
    assert {
        relative for relative in retired_files if (ROOT / relative).exists()
    } == set()


def test_digital_life_router_fails_closed_without_app_life_authority() -> None:
    app = FastAPI()
    app.include_router(create_digital_life_router(HostRoutePorts()))

    response = TestClient(app, raise_server_exceptions=False).get(
        "/v1/digital-life/state"
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "HOST_LIFE_ROUTE_AUTHORITY_UNAVAILABLE"


def test_memory_mutation_uses_the_bound_app_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = object()
    calls: list[tuple[object, str]] = []

    def rebuild(*, ledger: object, person_id: str) -> dict[str, object]:
        calls.append((ledger, person_id))
        return {"status": "OK"}

    monkeypatch.setattr(memory_routes, "_rebuild_memory_system", rebuild)
    app = FastAPI()
    app.include_router(
        memory_routes.create_memory_router(
            HostRoutePorts(ledger_provider=lambda: ledger)
        )
    )

    response = TestClient(app).post(
        "/v1/memory/rebuild/run",
        json={"person_id": "route-scope"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "OK"}
    assert calls == [(ledger, "route-scope")]


def test_health_compatibility_flag_cannot_reopen_process_ledger() -> None:
    with pytest.raises(
        health_routes.HealthRuntimeDependencyUnavailable,
        match="HOST_HEALTH_LEDGER_PROVIDER_UNAVAILABLE",
    ):
        health_routes._read_health_ledger(
            ledger_provider=None,
            allow_legacy_process_providers=True,
        )


def test_obs_forge_handler_resolves_only_the_bound_route_provider() -> None:
    forge = object()
    ports = HostRoutePorts(capability_forge_service_provider=lambda: forge)

    assert domain_command_ports._capability_forge_service(ports) is forge
    with pytest.raises(
        domain_command_ports.ObsDomainCommandError,
        match="OBS_CAPABILITY_FORGE_DOMAIN_UNAVAILABLE",
    ):
        domain_command_ports._capability_forge_service(HostRoutePorts())
