from __future__ import annotations

import asyncio

from host_brain.app import startup
from host_brain.core.edge_endpoint_config import (
    resolve_edge_base_url,
    resolve_edge_base_urls,
)


def test_edge_endpoint_config_is_pure_normalized_and_deterministic(
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOST_EDGE_RPC_BASE_URL", "edge.remote:17999/")
    monkeypatch.setenv("HOST_EDGE_RPC_ALLOW_LOCAL_FALLBACK", "true")

    assert resolve_edge_base_url() == "http://edge.remote:17999"
    assert resolve_edge_base_urls() == ["http://edge.remote:17999"]

    monkeypatch.delenv("HOST_EDGE_RPC_BASE_URL")
    monkeypatch.setenv("EDGE_RPC_HOST", "10.0.0.8")
    monkeypatch.setenv("EDGE_RPC_PORT", "18999")

    assert resolve_edge_base_url() == "http://10.0.0.8:18999"
    assert resolve_edge_base_urls() == [
        "http://10.0.0.8:18999",
        "http://127.0.0.1:18999",
        "http://localhost:18999",
    ]


def test_collect_dependency_probes_sync_runs_without_event_loop(monkeypatch) -> None:
    expected = {"edge.rpc.gateway": {"available": True, "detail": "ok"}}

    async def _fake_collect_dependency_probes():
        return expected

    monkeypatch.setattr(startup, "collect_dependency_probes", _fake_collect_dependency_probes)

    assert startup.collect_dependency_probes_sync() == expected


def test_collect_dependency_probes_sync_uses_worker_thread_inside_running_loop(monkeypatch) -> None:
    expected = {"edge.rpc.tts": {"available": False, "detail": "disabled"}}

    async def _fake_collect_dependency_probes():
        await asyncio.sleep(0)
        return expected

    monkeypatch.setattr(startup, "collect_dependency_probes", _fake_collect_dependency_probes)

    async def _run_inside_loop():
        return startup.collect_dependency_probes_sync()

    assert asyncio.run(_run_inside_loop()) == expected


def test_build_edge_dependency_probe_statuses_marks_config_disabled_component_not_tested() -> None:
    statuses = startup.build_edge_dependency_probe_statuses(
        {
            "available": True,
            "detail": "edge rpc reachable",
            "latency_ms": 24,
            "event": {
                "services": {
                    "edge.rpc.asr": {
                        "available": False,
                        "detail": "edge asr disabled by config",
                    },
                    "edge.rpc.tts": {
                        "available": True,
                        "detail": "edge tts enabled",
                    },
                },
                "components": {
                    "asr": {
                        "enabled": False,
                        "available": False,
                        "status": "down",
                        "detail": "component disabled by config",
                    },
                    "tts": {
                        "enabled": True,
                        "available": True,
                        "status": "ok",
                        "detail": "component loaded",
                    },
                },
            },
        }
    )

    assert statuses["edge.rpc.gateway"]["available"] is True
    assert statuses["edge.rpc.asr"]["available"] is False
    assert statuses["edge.rpc.asr"]["status"] == "NOT_TESTED"
    assert statuses["edge.rpc.asr"]["not_tested"] is True
    assert statuses["edge.rpc.asr"]["reason_code"] == "ACCEPTANCE_PENDING_EXTERNAL"
    assert "disabled by config" in statuses["edge.rpc.asr"]["detail"]


def test_build_edge_dependency_probe_statuses_keeps_enabled_but_down_component_degraded_with_health_error() -> None:
    statuses = startup.build_edge_dependency_probe_statuses(
        {
            "available": True,
            "detail": "edge rpc reachable",
            "latency_ms": 31,
            "event": {
                "services": {
                    "edge.rpc.tts": {
                        "available": True,
                        "detail": "edge tts enabled",
                    },
                },
                "components": {
                    "tts": {
                        "enabled": True,
                        "available": True,
                        "status": "down",
                        "detail": "component loaded",
                        "health": {
                            "state": "ERROR",
                            "load_error": "[Errno 22] Invalid argument",
                            "last_recovery_detail": "recovered via fresh subprocess after reload failure #1",
                        },
                    },
                },
            },
        }
    )

    assert statuses["edge.rpc.tts"]["available"] is False
    assert statuses["edge.rpc.tts"]["unavailable_kind"] == "dependency"
    assert "health state=ERROR" in statuses["edge.rpc.tts"]["detail"]
    assert "load_error=[Errno 22] Invalid argument" in statuses["edge.rpc.tts"]["detail"]
    assert "component status=down" in statuses["edge.rpc.tts"]["detail"]
