from __future__ import annotations

import ast
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from host_brain.app.host_route_ports import HostRoutePorts
from host_brain.app.mateengine_routes import create_mateengine_router
from host_brain.channels.embodiment import EmbodimentOutboxAck


ROOT = Path(__file__).resolve().parents[2]


class _Runtime:
    """Narrow app-owned runtime double; it cannot perform a physical effect."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.commands: list[object] = []

    def enqueue(self, command):  # noqa: ANN001, ANN201
        self.commands.append(command)
        return EmbodimentOutboxAck(
            outbox_id=f"outbox-{self.name}-{len(self.commands)}",
            command_id=command.command_id,
            idempotency_key=command.idempotency_key,
        )


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


def test_mateengine_router_uses_its_own_explicit_runtime_provider() -> None:
    runtime_a = _Runtime("a")
    runtime_b = _Runtime("b")

    with TestClient(_app(runtime_a)) as client_a, TestClient(_app(runtime_b)) as client_b:
        response_a = client_a.post(
            "/v1/mateengine/action",
            json={"action": "state", "state": "thinking", "request_id": "d8-a"},
        )
        response_b = client_b.post(
            "/v1/mateengine/action",
            json={"action": "idle", "request_id": "d8-b"},
        )

    assert response_a.status_code == 200
    assert response_b.status_code == 200
    assert len(runtime_a.commands) == 1
    assert len(runtime_b.commands) == 1
    assert runtime_a.commands[0].request_id == "d8-a"
    assert runtime_b.commands[0].request_id == "d8-b"
    assert runtime_a.commands[0].payload == {
        "command": {"type": "expression", "preset": "NEUTRAL", "emotion_tag": "NEUTRAL", "ttl": 8.0}
    }
    assert runtime_b.commands[0].payload == {"command": {"type": "reset"}}


def test_mateengine_admission_is_pending_not_a_physical_success() -> None:
    runtime = _Runtime("pending")

    with TestClient(_app(runtime)) as client:
        response = client.post(
            "/v1/mateengine/action",
            json={"action": "say", "text": "只入队", "request_id": "d8-pending"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "PENDING"
    assert payload["accepted"] is True
    assert payload["success"] is True
    assert payload["delivery_state"] == "DURABLY_QUEUED"
    assert payload["physical_effect_verified"] is False
    assert payload["event"] == {"outbox_id": "outbox-pending-1", "durable": True}
    assert len(runtime.commands) == 1


def test_mateengine_router_fails_closed_when_its_app_runtime_is_unavailable() -> None:
    with TestClient(_app(None)) as client:
        health = client.get("/v1/mateengine/health")
        action = client.post("/v1/mateengine/action", json={"action": "idle"})

    assert health.status_code == 503
    assert action.status_code == 503
    assert health.json() == {"detail": "EMBODIMENT_RUNTIME_UNAVAILABLE"}
    assert action.json() == {"detail": "EMBODIMENT_RUNTIME_UNAVAILABLE"}


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
