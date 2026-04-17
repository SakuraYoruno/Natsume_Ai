from __future__ import annotations

import asyncio

from host_brain.app import startup


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


def test_build_edge_dependency_probe_statuses_treats_config_disabled_component_as_healthy() -> None:
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
    assert statuses["edge.rpc.asr"]["available"] is True
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
