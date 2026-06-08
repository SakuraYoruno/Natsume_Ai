from __future__ import annotations

from host_brain.skills.impl import system_executor


def test_system_info_runtime_vitals_reports_actionable_component_status(monkeypatch) -> None:
    monkeypatch.setattr(
        system_executor,
        "_collect_edge_rpc_health_snapshot",
        lambda: {
            "available": True,
            "status": "OK",
            "event": {
                "components": {
                    "asr": {
                        "enabled": False,
                        "available": False,
                        "status": "ok",
                        "detail": "edge asr disabled by config",
                    },
                    "tts": {
                        "enabled": True,
                        "available": True,
                        "status": "ok",
                        "detail": "component loaded",
                        "health": {"state": "READY"},
                    },
                    "vts": {
                        "enabled": True,
                        "available": True,
                        "status": "down",
                        "detail": "component loaded",
                        "health": {
                            "state": "ERROR",
                            "connection_error": "connect failed",
                        },
                    },
                    "voice_pipeline": {
                        "enabled": True,
                        "available": True,
                        "status": "ok",
                        "detail": "component loaded",
                    },
                    "resource_gate": {
                        "enabled": True,
                        "available": True,
                        "status": "ok",
                        "detail": "edge resource gate enabled",
                    },
                }
            },
        },
    )
    monkeypatch.setattr(
        system_executor,
        "_build_service_health_snapshot",
        lambda *, edge_probe, ts_ms: {
            "overall_status": "UP",
            "required_unavailable": 0,
            "optional_unavailable": 0,
            "services": [
                {"service_id": "host.skills.loader", "status": "UP", "required": True},
                {"service_id": "host.retrieval.recall_fusion", "status": "UP", "required": True},
                {"service_id": "edge.rpc.gateway", "status": "UP", "required": True},
                {"service_id": "edge.rpc.tts", "status": "UP", "required": True},
            ],
        },
    )
    monkeypatch.setattr(
        system_executor,
        "_collect_gateway_runtime_status",
        lambda: {
            "status": "UP",
            "detail": "gateway stats reachable",
            "qq_onebot": {"status": "UP", "online": True, "good": True},
            "outbound": {"dispatched": 3, "failed": 0},
        },
    )

    result = system_executor.execute_system_skill(
        {
            "action": "info",
            "manifest_ref": "system_info::unit",
            "include_cpu": False,
            "include_memory": False,
            "include_disk": False,
            "include_network": False,
            "include_runtime_vitals": True,
        }
    )

    detail = result["detail"]
    vitals = detail["runtime_vitals"]

    assert result["status"] == "OK"
    assert vitals["status"] == "DEGRADED"
    assert vitals["capabilities"]["qq_communication"]["status"] == "READY"
    assert vitals["capabilities"]["tool_execution"]["status"] == "READY"
    assert vitals["capabilities"]["avatar_vts"]["status"] == "DEGRADED"
    assert any(issue["component"] == "edge.vts" for issue in vitals["issues"])
    assert any("VTube Studio" in item for item in vitals["recommendations"])
