from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

import host_brain.app.dependencies as deps
import host_brain.app.fastapi_app as fastapi_app
import host_brain.app.internal_codex_routes as internal_codex_routes
from host_brain.app.automation.task_scheduler import get_scheduler
from host_brain.skills.builtin_registry import build_builtin_skill_registry
from host_brain.skills.ide_registry import IDESkillProvider


@pytest.fixture(autouse=True)
def _reset_internal_codex_state() -> None:
    internal_codex_routes.stop_self_heal_auto_dispatch_monitor()
    with internal_codex_routes._self_heal_jobs_lock:
        internal_codex_routes._self_heal_jobs.clear()
    with internal_codex_routes._self_heal_auto_state_lock:
        internal_codex_routes._self_heal_auto_state.clear()
    with internal_codex_routes._self_heal_monitor_lock:
        internal_codex_routes._self_heal_monitor_state.update(
            {
                "enabled": False,
                "running": False,
                "source": "health",
                "interval_seconds": None,
                "min_consecutive_failures": None,
                "cooldown_seconds": None,
                "skip_dependency_probes": False,
                "run_mode_override": None,
                "escalation_policy": None,
                "timeout_seconds": None,
                "scheduler_managed": False,
                "last_cycle_at_ms": None,
                "last_result": None,
                "last_error": None,
                "thread_name": None,
            }
        )


def test_internal_codex_self_heal_proposal_route_builds_from_live_health_report(monkeypatch) -> None:
    async def _fake_build_live_health_report(*, skip_dependency_probes: bool = False, request_id=None, ts_ms=None):  # noqa: ANN001
        return {
            "request_id": "req_internal_codex_001",
            "ts_ms": 1_800_210_000_001,
            "status": "DEGRADED",
            "reason_code": "SERVICE_UNAVAILABLE_EDGE_RPC",
            "summary": {"overall_status": "DEGRADED"},
            "services": [
                {
                    "service_id": "edge.rpc.gateway",
                    "status": "DEGRADED",
                    "available": False,
                    "reason_code": "SERVICE_UNAVAILABLE_EDGE_RPC",
                }
            ],
        }

    monkeypatch.setattr(
        "host_brain.app.health_routes.build_live_health_report",
        _fake_build_live_health_report,
    )

    client = TestClient(fastapi_app.app, raise_server_exceptions=False)
    response = client.get("/internal/codex/self_heal/proposal?source=health&skip_dependency_probes=true")

    assert response.status_code == 200
    payload = response.json()
    assert payload["triggered"] is True
    assert payload["source"] == "health"
    assert payload["reason_code"] == "SERVICE_UNAVAILABLE_EDGE_RPC"
    assert payload["dispatch_allowed"] is False
    assert payload["dispatch_blocked_reason"] == "EXTERNAL_EDGE_RPC_OUTAGE"
    assert payload["intent_draft"]["target_id"] == "codex_codegen"
    assert payload["intent_draft"]["args"]["include_dependency_probes"] is False


def test_health_endpoint_can_attach_self_heal_proposal(monkeypatch) -> None:
    async def _fake_build_live_health_report(*, skip_dependency_probes: bool = False, request_id=None, ts_ms=None):  # noqa: ANN001
        return {
            "request_id": "req_health_attach_001",
            "ts_ms": 1_800_210_000_002,
            "status": "DEGRADED",
            "reason_code": "SERVICE_UNAVAILABLE_EDGE_RPC",
            "summary": {"overall_status": "DEGRADED"},
            "services": [
                {
                    "service_id": "edge.rpc.gateway",
                    "status": "DEGRADED",
                    "available": False,
                    "reason_code": "SERVICE_UNAVAILABLE_EDGE_RPC",
                }
            ],
        }

    monkeypatch.setattr(
        "host_brain.app.health_routes.build_live_health_report",
        _fake_build_live_health_report,
    )

    client = TestClient(fastapi_app.app, raise_server_exceptions=False)
    response = client.get("/health?include_self_heal_proposal=true")

    assert response.status_code == 200
    payload = response.json()
    proposal = payload["details"]["self_heal_proposal"]
    assert proposal["triggered"] is True
    assert proposal["source"] == "health"
    assert proposal["intent_draft"]["target_id"] == "codex_codegen"


def test_ready_endpoint_can_attach_self_heal_proposal(monkeypatch) -> None:
    async def _fake_build_live_readiness_report(*, request_id=None, ts_ms=None):  # noqa: ANN001
        return {
            "request_id": "req_ready_attach_001",
            "ts_ms": 1_800_210_000_003,
            "readiness_status": "FAILED",
            "reason_code": "SERVICE_UNAVAILABLE_DEPENDENCY",
            "services": {
                "rows": [
                    {
                        "service_id": "host.retrieval.embedding",
                        "status": "DEGRADED",
                        "available": False,
                        "reason_code": "SERVICE_UNAVAILABLE_DEPENDENCY",
                    }
                ]
            },
            "graph_runtime": {
                "status": "DEGRADED",
                "reason_code": "ORCH_DEPENDENCY_UNAVAILABLE",
            },
        }

    monkeypatch.setattr(
        "host_brain.app.health_routes.build_live_readiness_report",
        _fake_build_live_readiness_report,
    )

    client = TestClient(fastapi_app.app, raise_server_exceptions=False)
    response = client.get("/ready?include_self_heal_proposal=true")

    assert response.status_code == 200
    payload = response.json()
    proposal = payload["summary"]["self_heal_proposal"]
    assert proposal["triggered"] is True
    assert proposal["source"] == "readiness"
    assert proposal["severity"] == "high"
    assert proposal["intent_draft"]["target_id"] == "codex_codegen"


def test_internal_codex_self_heal_dispatch_route_executes_proposal(monkeypatch) -> None:
    async def _fake_build_live_health_report(*, skip_dependency_probes: bool = False, request_id=None, ts_ms=None):  # noqa: ANN001
        return {
            "request_id": "req_internal_codex_dispatch_001",
            "ts_ms": 1_800_210_000_004,
            "status": "DEGRADED",
            "reason_code": "SERVICE_UNAVAILABLE_EDGE_RPC",
            "summary": {"overall_status": "DEGRADED"},
            "services": [
                {
                    "service_id": "edge.rpc.gateway",
                    "status": "DEGRADED",
                    "available": False,
                    "reason_code": "SERVICE_UNAVAILABLE_EDGE_RPC",
                }
            ],
        }

    registry = build_builtin_skill_registry()
    registry.register_decorated(IDESkillProvider())
    monkeypatch.setattr(deps, "_skill_registry", registry, raising=False)
    monkeypatch.setattr(
        "host_brain.app.health_routes.build_live_health_report",
        _fake_build_live_health_report,
    )

    captured: dict[str, object] = {}

    def _fake_dispatch_executor_payload(payload):  # noqa: ANN001
        captured["payload"] = dict(payload)
        return {
            "status": "EXECUTED",
            "detail": "codex exec completed",
            "result_text": "self-heal-dispatch-ok",
            "artifacts_dir": "D:/Natsume_Ai/artifacts/codex_runs/demo",
        }

    monkeypatch.setattr(
        "host_brain.app.internal_codex_routes.dispatch_executor_payload",
        _fake_dispatch_executor_payload,
    )

    client = TestClient(fastapi_app.app, raise_server_exceptions=False)
    response = client.post(
        "/internal/codex/self_heal/dispatch?source=health&skip_dependency_probes=true&timeout_seconds=15&run_mode_override=inspect"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["accepted"] is True
    assert payload["background"] is False
    assert payload["job_id"] is None
    assert payload["dispatch"]["skill_name"] == "codex_codegen"
    assert payload["dispatch"]["status"] == "EXECUTED"
    assert payload["dispatch"]["success"] is True
    assert payload["dispatch"]["result_text"] == "self-heal-dispatch-ok"
    assert payload["dispatch"]["normalized_payload"]["run_mode"] == "inspect"
    assert payload["dispatch"]["normalized_payload"]["timeout_seconds"] == 15
    assert payload["proposal"]["intent_draft"]["target_id"] == "codex_codegen"
    assert captured["payload"]["tool"] == "codex"


def test_internal_codex_self_heal_dispatch_route_skips_when_not_required(monkeypatch) -> None:
    async def _fake_build_live_health_report(*, skip_dependency_probes: bool = False, request_id=None, ts_ms=None):  # noqa: ANN001
        return {
            "request_id": "req_internal_codex_dispatch_002",
            "ts_ms": 1_800_210_000_005,
            "status": "UP",
            "reason_code": None,
            "summary": {"overall_status": "UP"},
            "services": [],
        }

    monkeypatch.setattr(
        "host_brain.app.health_routes.build_live_health_report",
        _fake_build_live_health_report,
    )

    client = TestClient(fastapi_app.app, raise_server_exceptions=False)
    response = client.post("/internal/codex/self_heal/dispatch?source=health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["accepted"] is False
    assert payload["executed"] is False
    assert payload["job_id"] is None
    assert payload["reason_code"] == "SELF_HEAL_NOT_REQUIRED"
    assert payload["dispatch"] is None


def test_internal_codex_self_heal_dispatch_route_can_run_in_background(monkeypatch) -> None:
    async def _fake_build_live_health_report(*, skip_dependency_probes: bool = False, request_id=None, ts_ms=None):  # noqa: ANN001
        return {
            "request_id": "req_internal_codex_dispatch_bg_001",
            "ts_ms": 1_800_210_000_006,
            "status": "DEGRADED",
            "reason_code": "SERVICE_UNAVAILABLE_EDGE_RPC",
            "summary": {"overall_status": "DEGRADED"},
            "services": [
                {
                    "service_id": "edge.rpc.gateway",
                    "status": "DEGRADED",
                    "available": False,
                    "reason_code": "SERVICE_UNAVAILABLE_EDGE_RPC",
                }
            ],
        }

    registry = build_builtin_skill_registry()
    registry.register_decorated(IDESkillProvider())
    monkeypatch.setattr(deps, "_skill_registry", registry, raising=False)
    monkeypatch.setattr(
        "host_brain.app.health_routes.build_live_health_report",
        _fake_build_live_health_report,
    )

    def _fake_dispatch_executor_payload(payload):  # noqa: ANN001
        return {
            "status": "EXECUTED",
            "detail": "codex exec completed",
            "result_text": "self-heal-background-ok",
            "artifacts_dir": "D:/Natsume_Ai/artifacts/codex_runs/demo-bg",
        }

    monkeypatch.setattr(
        "host_brain.app.internal_codex_routes.dispatch_executor_payload",
        _fake_dispatch_executor_payload,
    )
    monkeypatch.setattr(
        "host_brain.app.internal_codex_routes._start_background_self_heal_job",
        lambda job_id: internal_codex_routes._run_self_heal_job(job_id),
    )

    client = TestClient(fastapi_app.app, raise_server_exceptions=False)
    response = client.post(
        "/internal/codex/self_heal/dispatch?source=health&background=true&timeout_seconds=600&run_mode_override=self_heal"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["accepted"] is True
    assert payload["executed"] is False
    assert payload["background"] is True
    assert isinstance(payload["job_id"], str) and payload["job_id"]
    assert payload["job"]["status"] in {"QUEUED", "RUNNING", "COMPLETED"}

    job_response = client.get(f"/internal/codex/jobs/{payload['job_id']}")

    assert job_response.status_code == 200
    job_payload = job_response.json()
    assert job_payload["status"] == "COMPLETED"
    assert job_payload["executed"] is True
    assert job_payload["timeout_seconds"] == 600
    assert job_payload["run_mode_override"] == "self_heal"
    assert job_payload["dispatch"]["success"] is True
    assert job_payload["dispatch"]["result_text"] == "self-heal-background-ok"


def test_internal_codex_job_route_returns_404_for_unknown_job() -> None:
    client = TestClient(fastapi_app.app, raise_server_exceptions=False)
    response = client.get("/internal/codex/jobs/codex-self-heal::missing")

    assert response.status_code == 404
    assert response.json()["detail"] == "CODEX_JOB_NOT_FOUND"


def test_internal_codex_self_heal_auto_state_defaults_when_idle() -> None:
    client = TestClient(fastapi_app.app, raise_server_exceptions=False)
    response = client.get("/internal/codex/self_heal/auto_state?source=health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "health"
    assert payload["decision"] == "HEALTHY"
    assert payload["eligible"] is False
    assert payload["consecutive_failures"] == 0


def test_internal_codex_self_heal_auto_state_defaults_reflect_runtime_env(monkeypatch) -> None:
    monkeypatch.setenv("HOST_CODEX_SELF_HEAL_AUTODISPATCH_MIN_CONSECUTIVE", "1")
    monkeypatch.setenv("HOST_CODEX_SELF_HEAL_AUTODISPATCH_COOLDOWN_SECONDS", "0")
    monkeypatch.setenv("HOST_CODEX_SELF_HEAL_AUTODISPATCH_ESCALATION_POLICY", "inspect_only")

    client = TestClient(fastapi_app.app, raise_server_exceptions=False)
    response = client.get("/internal/codex/self_heal/auto_state?source=readiness")

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "readiness"
    assert payload["min_consecutive_failures"] == 1
    assert payload["cooldown_seconds"] == 0
    assert payload["escalation_policy"] == "inspect_only"


def test_internal_codex_self_heal_monitor_status_defaults_when_idle() -> None:
    client = TestClient(fastapi_app.app, raise_server_exceptions=False)
    response = client.get("/internal/codex/self_heal/monitor_status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is False
    assert payload["running"] is False
    assert payload["last_result"] is None
    assert payload["escalation_policy"] is None


def test_internal_codex_self_heal_monitor_status_reflects_scheduler_runtime_env(monkeypatch) -> None:
    monkeypatch.setenv("HOST_CODEX_SELF_HEAL_AUTODISPATCH_SOURCE", "readiness")
    monkeypatch.setenv("HOST_CODEX_SELF_HEAL_AUTODISPATCH_MIN_CONSECUTIVE", "1")
    monkeypatch.setenv("HOST_CODEX_SELF_HEAL_AUTODISPATCH_COOLDOWN_SECONDS", "0")
    monkeypatch.setenv("HOST_CODEX_SELF_HEAL_AUTODISPATCH_TIMEOUT_SECONDS", "180")
    monkeypatch.setenv("HOST_CODEX_SELF_HEAL_AUTODISPATCH_RUN_MODE", "inspect")
    monkeypatch.setenv("HOST_CODEX_SELF_HEAL_AUTODISPATCH_ESCALATION_POLICY", "inspect_only")
    internal_codex_routes._update_monitor_state(
        scheduler_managed=True,
        enabled=False,
        running=False,
        source="health",
        interval_seconds=None,
        min_consecutive_failures=None,
        cooldown_seconds=None,
        run_mode_override=None,
        escalation_policy=None,
        timeout_seconds=None,
    )

    client = TestClient(fastapi_app.app, raise_server_exceptions=False)
    response = client.get("/internal/codex/self_heal/monitor_status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["scheduler_managed"] is True
    assert payload["source"] == "readiness"
    assert payload["min_consecutive_failures"] == 1
    assert payload["cooldown_seconds"] == 0
    assert payload["timeout_seconds"] == 180
    assert payload["run_mode_override"] == "inspect"
    assert payload["escalation_policy"] == "inspect_only"


def test_start_self_heal_auto_dispatch_monitor_respects_disabled_env(monkeypatch) -> None:
    monkeypatch.setenv("HOST_CODEX_SELF_HEAL_AUTODISPATCH_ENABLED", "false")

    state = internal_codex_routes.start_self_heal_auto_dispatch_monitor()

    assert state["enabled"] is False
    assert state["running"] is False


def test_start_self_heal_auto_dispatch_monitor_yields_to_scheduler_mode(monkeypatch) -> None:
    monkeypatch.setenv("HOST_CODEX_SELF_HEAL_SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("HOST_CODEX_SELF_HEAL_AUTODISPATCH_ENABLED", "true")

    state = internal_codex_routes.start_self_heal_auto_dispatch_monitor()

    assert state["enabled"] is False
    assert state["running"] is False
    assert state["scheduler_managed"] is True


def test_start_self_heal_auto_dispatch_monitor_enabled_runs_stub_loop(monkeypatch) -> None:
    captured: list[str] = []

    def _fake_loop() -> None:
        captured.append("loop-ran")
        internal_codex_routes._update_monitor_state(
            enabled=True,
            running=False,
            source="health",
            interval_seconds=42,
            last_cycle_at_ms=1_800_210_000_999,
            last_result={"decision": "WAITING_THRESHOLD"},
        )

    monkeypatch.setenv("HOST_CODEX_SELF_HEAL_AUTODISPATCH_ENABLED", "true")
    monkeypatch.setattr(
        "host_brain.app.internal_codex_routes._run_auto_dispatch_monitor_loop",
        _fake_loop,
    )

    state = internal_codex_routes.start_self_heal_auto_dispatch_monitor()
    internal_codex_routes.stop_self_heal_auto_dispatch_monitor()

    assert state["enabled"] is True
    assert "loop-ran" in captured


def test_host_scheduler_lists_codex_self_heal_scheduler_task(monkeypatch) -> None:
    scheduler = get_scheduler()
    scheduler._tasks.clear()
    captured = {"count": 0}

    async def _fake_cycle():
        captured["count"] += 1
        return {
            "accepted": True,
            "dispatched": False,
            "background": False,
            "job_id": None,
            "proposal": {"reason_code": "TEST_SCHEDULER_TICK"},
            "auto_state": {"decision": "WAITING_THRESHOLD"},
            "selected_run_mode": "inspect",
        }

    monkeypatch.setenv("HOST_BACKGROUND_TASKS_ENABLED", "true")
    monkeypatch.setenv("HOST_CODEX_SELF_HEAL_SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("HOST_CODEX_SELF_HEAL_SCHEDULER_INTERVAL_SECONDS", "45")
    monkeypatch.setenv("HOST_CODEX_SELF_HEAL_AUTODISPATCH_ENABLED", "false")
    monkeypatch.setattr(
        "host_brain.app.internal_codex_routes.run_scheduled_self_heal_auto_dispatch_cycle",
        _fake_cycle,
    )

    with TestClient(fastapi_app.app, raise_server_exceptions=False) as client:
        response = client.get("/host-scheduler/tasks")
        assert response.status_code == 200
        payload = response.json()
        codex_tasks = [
            task
            for task in payload["tasks"]
            if task["task_id"] == "codex_self_heal_auto_dispatch_tick"
        ]

        assert codex_tasks
        codex_task = codex_tasks[0]
        assert codex_task["interval_s"] == 45.0
        assert codex_task["priority"] == 1

        trigger = client.post("/host-scheduler/tasks/codex_self_heal_auto_dispatch_tick/trigger")
        assert trigger.status_code == 200
        assert trigger.json()["triggered"] is True
        time.sleep(2.2)
        monitor = client.get("/internal/codex/self_heal/monitor_status")
        assert monitor.status_code == 200
        assert monitor.json()["scheduler_managed"] is True

    assert captured["count"] >= 1

    scheduler._tasks.clear()


def test_host_scheduler_trigger_runs_real_codex_auto_dispatch_flow(monkeypatch) -> None:
    async def _fake_build_live_health_report(
        *,
        skip_dependency_probes: bool = False,
        request_id=None,
        ts_ms=None,
    ):  # noqa: ANN001
        return {
            "request_id": "req_scheduler_real_auto_dispatch_001",
            "ts_ms": 1_800_210_000_010,
            "status": "DEGRADED",
            "reason_code": "SERVICE_UNAVAILABLE_EDGE_RPC",
            "summary": {"overall_status": "DEGRADED"},
            "services": [
                {
                    "service_id": "edge.rpc.gateway",
                    "status": "DEGRADED",
                    "available": False,
                    "reason_code": "SERVICE_UNAVAILABLE_EDGE_RPC",
                },
                {
                    "service_id": "host.integrations.llm_gateway",
                    "status": "DOWN",
                    "available": False,
                    "reason_code": "LLM_GATEWAY_UNAVAILABLE",
                }
            ],
        }

    registry = build_builtin_skill_registry()
    registry.register_decorated(IDESkillProvider())
    monkeypatch.setattr(deps, "_skill_registry", registry, raising=False)
    monkeypatch.setattr(
        "host_brain.app.health_routes.build_live_health_report",
        _fake_build_live_health_report,
    )
    monkeypatch.setattr(
        "host_brain.app.internal_codex_routes.dispatch_executor_payload",
        lambda payload: {  # noqa: ARG005
            "status": "EXECUTED",
            "detail": "codex exec completed",
            "result_text": "scheduler-real-auto-dispatch-ok",
            "artifacts_dir": "D:/Natsume_Ai/artifacts/codex_runs/demo-scheduler-real",
        },
    )
    monkeypatch.setattr(
        "host_brain.app.internal_codex_routes._start_background_self_heal_job",
        lambda job_id: internal_codex_routes._run_self_heal_job(job_id),
    )

    scheduler = get_scheduler()
    scheduler._tasks.clear()

    monkeypatch.setenv("HOST_BACKGROUND_TASKS_ENABLED", "true")
    monkeypatch.setenv("HOST_CODEX_SELF_HEAL_SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("HOST_CODEX_SELF_HEAL_SCHEDULER_INTERVAL_SECONDS", "45")
    monkeypatch.setenv("HOST_CODEX_SELF_HEAL_AUTODISPATCH_ENABLED", "false")
    monkeypatch.setenv("HOST_CODEX_SELF_HEAL_AUTODISPATCH_SOURCE", "health")
    monkeypatch.setenv("HOST_CODEX_SELF_HEAL_AUTODISPATCH_MIN_CONSECUTIVE", "1")
    monkeypatch.setenv("HOST_CODEX_SELF_HEAL_AUTODISPATCH_COOLDOWN_SECONDS", "0")
    monkeypatch.setenv("HOST_CODEX_SELF_HEAL_AUTODISPATCH_TIMEOUT_SECONDS", "120")
    monkeypatch.setenv("HOST_CODEX_SELF_HEAL_AUTODISPATCH_RUN_MODE", "inspect")
    monkeypatch.setenv("HOST_CODEX_SELF_HEAL_AUTODISPATCH_ESCALATION_POLICY", "inspect_only")

    with TestClient(fastapi_app.app, raise_server_exceptions=False) as client:
        trigger = client.post("/host-scheduler/tasks/codex_self_heal_auto_dispatch_tick/trigger")
        assert trigger.status_code == 200
        assert trigger.json()["triggered"] is True

        time.sleep(2.2)

        monitor = client.get("/internal/codex/self_heal/monitor_status")
        assert monitor.status_code == 200
        monitor_payload = monitor.json()
        assert monitor_payload["scheduler_managed"] is True
        assert monitor_payload["last_result"]["decision"] == "DISPATCHED"
        assert monitor_payload["last_result"]["selected_run_mode"] == "inspect"
        assert monitor_payload["last_result"]["reason_code"] == "SERVICE_UNAVAILABLE_EDGE_RPC"

        auto_state = client.get("/internal/codex/self_heal/auto_state?source=health")
        assert auto_state.status_code == 200
        auto_payload = auto_state.json()
        assert auto_payload["decision"] == "DISPATCHED"
        assert auto_payload["last_dispatch_run_mode"] == "inspect"
        assert auto_payload["last_dispatch_success"] is True
        assert auto_payload["last_job_status"] == "COMPLETED"
        assert auto_payload["last_artifacts_dir"] == "D:/Natsume_Ai/artifacts/codex_runs/demo-scheduler-real"
        assert auto_payload["last_job_id"]

        job = client.get(f"/internal/codex/jobs/{auto_payload['last_job_id']}")
        assert job.status_code == 200
        job_payload = job.json()
        assert job_payload["status"] == "COMPLETED"
        assert job_payload["dispatch"]["success"] is True
        assert job_payload["dispatch"]["result_text"] == "scheduler-real-auto-dispatch-ok"

    scheduler._tasks.clear()


def test_internal_codex_self_heal_auto_dispatch_waits_for_threshold_then_dispatches(monkeypatch) -> None:
    async def _fake_build_live_health_report(*, skip_dependency_probes: bool = False, request_id=None, ts_ms=None):  # noqa: ANN001
        return {
            "request_id": "req_internal_codex_auto_dispatch_001",
            "ts_ms": 1_800_210_000_007,
            "status": "DEGRADED",
            "reason_code": "SERVICE_UNAVAILABLE_EDGE_RPC",
            "summary": {"overall_status": "DEGRADED"},
            "services": [
                {
                    "service_id": "edge.rpc.gateway",
                    "status": "DEGRADED",
                    "available": False,
                    "reason_code": "SERVICE_UNAVAILABLE_EDGE_RPC",
                },
                {
                    "service_id": "host.integrations.llm_gateway",
                    "status": "DOWN",
                    "available": False,
                    "reason_code": "LLM_GATEWAY_UNAVAILABLE",
                }
            ],
        }

    registry = build_builtin_skill_registry()
    registry.register_decorated(IDESkillProvider())
    monkeypatch.setattr(deps, "_skill_registry", registry, raising=False)
    monkeypatch.setattr(
        "host_brain.app.health_routes.build_live_health_report",
        _fake_build_live_health_report,
    )
    monkeypatch.setattr(
        "host_brain.app.internal_codex_routes.dispatch_executor_payload",
        lambda payload: {  # noqa: ARG005
            "status": "EXECUTED",
            "detail": "codex exec completed",
            "result_text": "self-heal-auto-dispatch-ok",
            "artifacts_dir": "D:/Natsume_Ai/artifacts/codex_runs/demo-auto",
        },
    )
    monkeypatch.setattr(
        "host_brain.app.internal_codex_routes._start_background_self_heal_job",
        lambda job_id: internal_codex_routes._run_self_heal_job(job_id),
    )

    client = TestClient(fastapi_app.app, raise_server_exceptions=False)
    first = client.post(
        "/internal/codex/self_heal/auto_dispatch?source=health&min_consecutive_failures=2&cooldown_seconds=300&timeout_seconds=120&run_mode_override=inspect"
    )
    second = client.post(
        "/internal/codex/self_heal/auto_dispatch?source=health&min_consecutive_failures=2&cooldown_seconds=300&timeout_seconds=120&run_mode_override=inspect"
    )
    third = client.post(
        "/internal/codex/self_heal/auto_dispatch?source=health&min_consecutive_failures=2&cooldown_seconds=300&timeout_seconds=120&run_mode_override=inspect"
    )

    assert first.status_code == 200
    assert first.json()["dispatched"] is False
    assert first.json()["auto_state"]["decision"] == "WAITING_THRESHOLD"
    assert first.json()["auto_state"]["consecutive_failures"] == 1

    assert second.status_code == 200
    second_payload = second.json()
    assert second_payload["dispatched"] is True
    assert second_payload["background"] is True
    assert second_payload["auto_state"]["decision"] == "DISPATCHED"
    assert second_payload["auto_state"]["last_job_id"] == second_payload["job_id"]

    assert third.status_code == 200
    third_payload = third.json()
    assert third_payload["dispatched"] is False
    assert third_payload["auto_state"]["decision"] == "COOLDOWN_ACTIVE"
    assert third_payload["auto_state"]["cooldown_remaining_seconds"] >= 0


def test_internal_codex_self_heal_auto_dispatch_escalates_from_inspect_to_self_heal(monkeypatch) -> None:
    async def _fake_build_live_health_report(*, skip_dependency_probes: bool = False, request_id=None, ts_ms=None):  # noqa: ANN001
        return {
            "request_id": "req_internal_codex_auto_dispatch_003",
            "ts_ms": 1_800_210_000_009,
            "status": "DEGRADED",
            "reason_code": "SERVICE_UNAVAILABLE_EDGE_RPC",
            "summary": {"overall_status": "DEGRADED"},
            "services": [
                {
                    "service_id": "edge.rpc.gateway",
                    "status": "DEGRADED",
                    "available": False,
                    "reason_code": "SERVICE_UNAVAILABLE_EDGE_RPC",
                },
                {
                    "service_id": "host.integrations.llm_gateway",
                    "status": "DOWN",
                    "available": False,
                    "reason_code": "LLM_GATEWAY_UNAVAILABLE",
                }
            ],
        }

    registry = build_builtin_skill_registry()
    registry.register_decorated(IDESkillProvider())
    monkeypatch.setattr(deps, "_skill_registry", registry, raising=False)
    monkeypatch.setattr(
        "host_brain.app.health_routes.build_live_health_report",
        _fake_build_live_health_report,
    )
    monkeypatch.setattr(
        "host_brain.app.internal_codex_routes.dispatch_executor_payload",
        lambda payload: {  # noqa: ARG005
            "status": "EXECUTED",
            "detail": "codex exec completed",
            "result_text": "self-heal-escalation-ok",
            "artifacts_dir": "D:/Natsume_Ai/artifacts/codex_runs/demo-escalate",
        },
    )
    monkeypatch.setattr(
        "host_brain.app.internal_codex_routes._start_background_self_heal_job",
        lambda job_id: internal_codex_routes._run_self_heal_job(job_id),
    )

    client = TestClient(fastapi_app.app, raise_server_exceptions=False)
    first = client.post(
        "/internal/codex/self_heal/auto_dispatch?source=health&min_consecutive_failures=1&cooldown_seconds=0&timeout_seconds=120&run_mode_override=inspect&escalation_policy=inspect_then_self_heal"
    )
    second = client.post(
        "/internal/codex/self_heal/auto_dispatch?source=health&min_consecutive_failures=1&cooldown_seconds=0&timeout_seconds=120&run_mode_override=inspect&escalation_policy=inspect_then_self_heal"
    )

    assert first.status_code == 200
    first_payload = first.json()
    assert first_payload["dispatched"] is True
    assert first_payload["selected_run_mode"] == "inspect"
    assert first_payload["job"]["run_mode_override"] == "inspect"

    assert second.status_code == 200
    second_payload = second.json()
    assert second_payload["dispatched"] is True
    assert second_payload["selected_run_mode"] == "self_heal"
    assert second_payload["job"]["run_mode_override"] == "self_heal"
    assert second_payload["auto_state"]["last_dispatch_run_mode"] == "self_heal"
    assert second_payload["auto_state"]["last_dispatch_success"] is True


def test_internal_codex_self_heal_auto_dispatch_resets_to_healthy(monkeypatch) -> None:
    async def _fake_build_live_health_report(*, skip_dependency_probes: bool = False, request_id=None, ts_ms=None):  # noqa: ANN001
        return {
            "request_id": "req_internal_codex_auto_dispatch_002",
            "ts_ms": 1_800_210_000_008,
            "status": "UP",
            "reason_code": None,
            "summary": {"overall_status": "UP"},
            "services": [],
        }

    monkeypatch.setattr(
        "host_brain.app.health_routes.build_live_health_report",
        _fake_build_live_health_report,
    )

    client = TestClient(fastapi_app.app, raise_server_exceptions=False)
    response = client.post("/internal/codex/self_heal/auto_dispatch?source=health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["accepted"] is False
    assert payload["dispatched"] is False
    assert payload["auto_state"]["decision"] == "HEALTHY"
    assert payload["auto_state"]["consecutive_failures"] == 0


def test_internal_codex_self_heal_auto_dispatch_blocks_external_edge_outage(monkeypatch) -> None:
    async def _fake_build_live_health_report(*, skip_dependency_probes: bool = False, request_id=None, ts_ms=None):  # noqa: ANN001
        return {
            "request_id": "req_internal_codex_auto_dispatch_004",
            "ts_ms": 1_800_210_000_011,
            "status": "DEGRADED",
            "reason_code": "SERVICE_UNAVAILABLE_EDGE_RPC",
            "summary": {"overall_status": "DEGRADED"},
            "services": [
                {
                    "service_id": "edge.rpc.gateway",
                    "status": "DEGRADED",
                    "available": False,
                    "reason_code": "SERVICE_UNAVAILABLE_EDGE_RPC",
                }
            ],
        }

    monkeypatch.setattr(
        "host_brain.app.health_routes.build_live_health_report",
        _fake_build_live_health_report,
    )

    client = TestClient(fastapi_app.app, raise_server_exceptions=False)
    response = client.post(
        "/internal/codex/self_heal/auto_dispatch?source=health&min_consecutive_failures=1&cooldown_seconds=0&timeout_seconds=120&run_mode_override=inspect"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["accepted"] is True
    assert payload["dispatched"] is False
    assert payload["job_id"] is None
    assert payload["proposal"]["dispatch_allowed"] is False
    assert payload["proposal"]["dispatch_blocked_reason"] == "EXTERNAL_EDGE_RPC_OUTAGE"
    assert payload["auto_state"]["decision"] == "EXTERNAL_DEPENDENCY_BLOCKED"
    assert payload["auto_state"]["eligible"] is False
    assert payload["auto_state"]["last_dispatch_reason_code"] == "EXTERNAL_EDGE_RPC_OUTAGE"
