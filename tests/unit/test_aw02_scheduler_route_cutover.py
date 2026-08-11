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


def test_scheduler_admin_writes_use_governed_runtime_for_both_prefixes() -> None:
    authority = SimpleNamespace(
        admissible=True,
        duplicate=False,
        reason_code="SECURE_INGRESS_ALLOWED",
        request_id="scheduler-admin-route-request",
    )

    class Ingress:
        def evaluate_http(self, **_kwargs):
            return authority

    class Service:
        def get_definition(self, task_id: str):
            return {
                "task_id": task_id,
                "head_version": 7,
                "next_scheduled_at_ms": 123,
                "definition": {
                    "trigger_mode": "interval",
                    "trigger_config": {"seconds": 60},
                    "intent_template": {"args": {}},
                    "worker_key": "scheduler_production",
                    "enabled": True,
                    "retry_policy": {
                        "max_attempts": 1,
                        "retry_backoff_ms": 0,
                    },
                },
            }

        async def publish_definition(self, **_kwargs):
            raise AssertionError("HTTP route bypassed governed runtime")

        async def set_enabled(self, **_kwargs):
            raise AssertionError("HTTP route bypassed governed runtime")

        async def delete_definition(self, **_kwargs):
            raise AssertionError("HTTP route bypassed governed runtime")

    class Runtime:
        def __init__(self) -> None:
            self.calls = []

        async def execute_scheduler_admin_control(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "success": True,
                "operator_command_id": kwargs["command_id"],
                "task_id": kwargs["payload"]["task_id"],
                "graph_revision": 1,
            }

    runtime = Runtime()
    app = FastAPI()
    register_host_routers(
        app,
        input_runtime_ports=build_input_runtime_application_ports(),
        route_ports=HostRoutePorts(),
        scheduler_command_ingress_provider=Ingress,
        scheduler_command_service_provider=Service,
        scheduler_admin_runtime_provider=lambda: runtime,
    )
    create_body = {
        "task_id": "task.route",
        "trigger_mode": "interval",
        "trigger_config": {"seconds": 60},
        "intent_template": {"args": {}},
    }

    with TestClient(app) as client:
        created = client.post("/scheduler/tasks", json=create_body)
        assert created.status_code == 201
        patched = client.patch(
            "/host-scheduler/tasks/task.route",
            json={
                "description": "updated",
                "expected_head_version": 7,
            },
        )
        assert patched.status_code == 200
        disabled = client.post(
            "/scheduler/tasks/task.route/disable",
            params={"expected_head_version": 7},
        )
        assert disabled.status_code == 200
        deleted = client.delete(
            "/host-scheduler/tasks/task.route",
            params={"expected_head_version": 7},
        )
        assert deleted.status_code == 200

    assert [call["command_id"] for call in runtime.calls] == [
        "scheduler.task.publish",
        "scheduler.task.publish",
        "scheduler.task.disable",
        "scheduler.task.delete",
    ]
    assert [call["method"] for call in runtime.calls] == [
        "POST",
        "PATCH",
        "POST",
        "DELETE",
    ]
    assert [call["normalized_path"] for call in runtime.calls] == [
        "/scheduler/tasks",
        "/host-scheduler/tasks/task.route",
        "/scheduler/tasks/task.route/disable",
        "/host-scheduler/tasks/task.route",
    ]
    assert all(call["authority"] is authority for call in runtime.calls)
