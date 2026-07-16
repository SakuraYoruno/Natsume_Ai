from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from host_brain.app.router_registry import register_host_routers
from host_brain.app.input_runtime_composition import build_input_runtime_application_ports
from host_brain.app.host_route_ports import HostRoutePorts


def test_both_scheduler_prefixes_use_one_service_with_explicit_authority() -> None:
    authority = SimpleNamespace(
        admissible=True,
        duplicate=False,
        reason_code="SECURE_INGRESS_ALLOWED",
        request_id="scheduler-route-request",
    )

    class Ingress:
        def evaluate_http(self, **_kwargs):
            return authority

    class Service:
        def __init__(self) -> None:
            self.calls = []

        async def trigger_manual(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "status": "SUCCEEDED",
                "task_id": kwargs["task_id"],
                "authority_bound": kwargs["authority"] is authority,
            }

    service = Service()
    app = FastAPI()
    register_host_routers(
        app,
        input_runtime_ports=build_input_runtime_application_ports(),
        route_ports=HostRoutePorts(),
        scheduler_command_ingress_provider=Ingress,
        scheduler_command_service_provider=lambda: service,
    )

    with TestClient(app) as client:
        for prefix in ("/scheduler", "/host-scheduler"):
            response = client.post(
                f"{prefix}/tasks/task.route/trigger",
                json={"reason": "operator"},
            )
            assert response.status_code == 200
            assert response.json()["authority_bound"] is True

    assert len(service.calls) == 2
    assert all(call["authority"] is authority for call in service.calls)
    assert all(call["payload"] == {"reason": "operator"} for call in service.calls)
