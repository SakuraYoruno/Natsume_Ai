from __future__ import annotations

import ast
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from host_brain.app.host_route_ports import HostRoutePorts
from host_brain.app.mateengine_routes import create_mateengine_router
ROOT = Path(__file__).resolve().parents[2]


class _Runtime:
    """Narrow app-owned runtime double; it cannot perform a physical effect."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.commands: list[object] = []
        self.runtime_scope_id = f"scope-{name}"
        self.closed = False
        self.authority_lease = {"lease_id": f"lease-{name}"}

    def enqueue(self, command):  # noqa: ANN001, ANN201
        self.commands.append(command)
        raise AssertionError("direct MateEngine HTTP routes must never enqueue")


def _app(runtime: _Runtime | None) -> FastAPI:
    app = FastAPI()
    app.include_router(
        create_mateengine_router(
            HostRoutePorts(
                embodiment_runtime_provider=(
                    (lambda: runtime) if runtime is not None else None
                ),
            )
        )
    )
    return app


def test_mateengine_health_uses_its_own_explicit_runtime_provider() -> None:
    runtime_a = _Runtime("a")
    runtime_b = _Runtime("b")

    with TestClient(_app(runtime_a)) as client_a, TestClient(_app(runtime_b)) as client_b:
        response_a = client_a.get("/v1/mateengine/health")
        response_b = client_b.get("/v1/mateengine/health")

    assert response_a.status_code == 200
    assert response_b.status_code == 200
    assert response_a.json()["runtime_scope_id"] == "scope-a"
    assert response_b.json()["runtime_scope_id"] == "scope-b"
    assert runtime_a.commands == []
    assert runtime_b.commands == []


def test_mateengine_health_is_read_only_and_never_enqueues_a_device_command() -> None:
    runtime = _Runtime("health")

    with TestClient(_app(runtime)) as client:
        response = client.get("/v1/mateengine/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["status"] == "REMOTE_EDGE_UNVERIFIED"
    assert payload["host_outbox_ready"] is True
    assert payload["physical_effect_verified"] is False
    assert payload["delivery_state"] == "NOT_DISPATCHED"
    assert payload["reason_code"] == "EMBODIMENT_REMOTE_HEALTH_REQUIRES_EDGE_RECEIPT"
    assert len(runtime.commands) == 0


def test_mateengine_router_fails_closed_when_its_app_runtime_is_unavailable() -> None:
    with TestClient(_app(None)) as client:
        health = client.get("/v1/mateengine/health")

    assert health.status_code == 503
    assert health.json() == {"detail": "EMBODIMENT_RUNTIME_UNAVAILABLE"}


def test_embodiment_authority_has_no_module_global_outbox_lease_or_observer_truth() -> None:
    embodiment_source = (ROOT / "host_brain/channels/embodiment.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(embodiment_source)
    module_names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            module_names.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            module_names.add(node.target.id)

    assert not {
        "_durable_outbox_authority",
        "_transport_lease",
        "_transport_lease_state_lock",
        "_receipt_observers",
    }.intersection(module_names)

    health_projection = (
        ROOT / "host_brain/app/observability_runtime/health_device_projection.py"
    ).read_text(encoding="utf-8")
    projection_runtime = (
        ROOT / "host_brain/app/observability_runtime/projection_runtime.py"
    ).read_text(encoding="utf-8")
    assert "_latest_receipts" not in health_projection
    assert "register_embodiment_receipt_observer" not in health_projection
    assert "_embodiment_observer_lock" not in projection_runtime
    assert "_embodiment_observer_unsubscribe" not in projection_runtime
