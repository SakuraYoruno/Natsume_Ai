from __future__ import annotations

from host_brain.app.codex_self_heal import (
    build_codex_self_heal_proposal_from_health_report,
    build_codex_self_heal_proposal_from_readiness_report,
)


def test_health_report_degraded_compiles_into_codex_self_heal_proposal() -> None:
    report = {
        "request_id": "req_health_self_heal_001",
        "ts_ms": 1_800_200_000_001,
        "status": "DEGRADED",
        "reason_code": "SERVICE_UNAVAILABLE_EDGE_RPC",
        "summary": {
            "overall_status": "DEGRADED",
            "required_unavailable": 2,
        },
        "services": [
            {
                "service_id": "edge.rpc.gateway",
                "status": "DEGRADED",
                "available": False,
                "reason_code": "SERVICE_UNAVAILABLE_EDGE_RPC",
            },
            {
                "service_id": "host.integrations.llm_gateway",
                "status": "UP",
                "available": True,
                "reason_code": None,
            },
        ],
    }

    proposal = build_codex_self_heal_proposal_from_health_report(report, include_dependency_probes=False)

    assert proposal["triggered"] is True
    assert proposal["source"] == "health"
    assert proposal["severity"] == "medium"
    assert proposal["reason_code"] == "SERVICE_UNAVAILABLE_EDGE_RPC"
    assert proposal["dispatch_allowed"] is False
    assert proposal["dispatch_blocked_reason"] == "EXTERNAL_EDGE_RPC_OUTAGE"
    assert "observe-only mode" in proposal["summary"]
    assert "disable edge RPC probing for host-only runs" in proposal["summary"]
    assert "edge.rpc.gateway" in proposal["affected_components"]

    intent = proposal["intent_draft"]
    assert intent is not None
    assert intent["route"] == "skill"
    assert intent["target_id"] == "codex_codegen"
    assert intent["args"]["mode"] == "analysis"
    assert intent["args"]["run_mode"] == "self_heal"
    assert intent["args"]["workspace_strategy"] == "runtime_repo"
    assert intent["args"]["include_dependency_probes"] is False
    assert "runtime:health" in intent["args"]["evidence_refs"]
    assert "component:edge.rpc.gateway" in intent["args"]["evidence_refs"]
    assert "Treat unreachable external dependencies as a valid root cause." in intent["args"]["context"]
    assert "Prefer runtime files under host_brain/app, host_brain/integrations, host_brain/channels, and scripts/runtime" in intent["args"]["context"]
    assert "host_brain/channels/edge_rpc_client.py" in intent["args"]["context"]
    assert "host_brain/app/health_routes.py" in intent["args"]["context"]
    assert "host_brain/app/internal_codex_routes.py" in intent["args"]["context"]
    assert "Priority file excerpts:" in intent["args"]["context"]
    assert "collect_dependency_probes" in intent["args"]["context"]


def test_readiness_report_ready_does_not_emit_self_heal_intent() -> None:
    report = {
        "request_id": "req_ready_self_heal_001",
        "ts_ms": 1_800_200_000_002,
        "readiness_status": "READY",
        "reason_code": None,
        "services": {
            "rows": [
                {
                    "service_id": "edge.rpc.gateway",
                    "status": "UP",
                    "available": True,
                    "reason_code": None,
                }
            ]
        },
    }

    proposal = build_codex_self_heal_proposal_from_readiness_report(report)

    assert proposal["triggered"] is False
    assert proposal["source"] == "readiness"
    assert proposal["severity"] == "none"
    assert proposal["intent_draft"] is None
    assert proposal["summary"] == "The readiness report is healthy; no self-heal proposal is needed."


def test_readiness_report_degraded_compiles_priority_excerpts_for_self_heal() -> None:
    report = {
        "request_id": "req_readiness_self_heal_002",
        "ts_ms": 1_800_200_000_003,
        "readiness_status": "DEGRADED",
        "reason_code": "SERVICE_UNAVAILABLE_EDGE_RPC",
        "services": {
            "rows": [
                {
                    "service_id": "edge.rpc.gateway",
                    "status": "DEGRADED",
                    "available": False,
                    "reason_code": "SERVICE_UNAVAILABLE_EDGE_RPC",
                }
            ]
        },
        "graph_runtime": {
            "status": "DEGRADED",
            "reason_code": "SERVICE_UNAVAILABLE_EDGE_RPC",
        },
    }

    proposal = build_codex_self_heal_proposal_from_readiness_report(report)

    intent = proposal["intent_draft"]
    assert intent is not None
    assert intent["args"]["mode"] == "analysis"
    assert "host_brain/app/codex_self_heal.py" in intent["args"]["context"]
    assert "build_codex_self_heal_proposal_from_readiness_report" in intent["args"]["context"]
    assert "collect_dependency_probes()" in intent["args"]["context"]
    assert "_auto_dispatch_self_heal_once" in intent["args"]["context"]
    assert "get_self_heal_monitor_status" in intent["args"]["context"]
    assert "Runtime note: `collect_dependency_probes()` now lives in `host_brain/app/startup.py`" in intent["args"]["context"]
    assert "Compatibility note: `host_brain.app.dependencies.collect_dependency_probes` is currently a re-export shim" in intent["args"]["context"]


def test_readiness_report_skips_graph_runtime_when_probe_is_skipped() -> None:
    report = {
        "request_id": "req_readiness_self_heal_002b",
        "ts_ms": 1_800_200_000_003,
        "readiness_status": "DEGRADED",
        "reason_code": "SERVICE_UNAVAILABLE_DEPENDENCY",
        "services": {
            "rows": [
                {
                    "service_id": "edge.rpc.tts",
                    "status": "DOWN",
                    "available": False,
                    "reason_code": "SERVICE_UNAVAILABLE_DEPENDENCY",
                }
            ]
        },
        "dependency_matrix": [
            {
                "component_id": "graph_runtime",
                "service_id": "graph_runtime",
                "component_type": "graph_runtime",
                "available": True,
                "reason_code": None,
                "detail": "graph runtime probe not provided",
            }
        ],
        "graph_runtime": {
            "status": "SKIPPED",
            "reason_code": None,
            "detail": "graph runtime probe not provided",
        },
    }

    proposal = build_codex_self_heal_proposal_from_readiness_report(report)

    assert proposal["affected_components"] == ["edge.rpc.tts"]
    assert "graph_runtime" not in proposal["summary"]


def test_readiness_report_with_host_failure_remains_dispatchable() -> None:
    report = {
        "request_id": "req_readiness_self_heal_003",
        "ts_ms": 1_800_200_000_004,
        "readiness_status": "DEGRADED",
        "reason_code": "SERVICE_UNAVAILABLE_EDGE_RPC",
        "services": {
            "rows": [
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
                },
            ]
        },
    }

    proposal = build_codex_self_heal_proposal_from_readiness_report(report)

    assert proposal["triggered"] is True
    assert proposal["dispatch_allowed"] is True
    assert proposal["dispatch_blocked_reason"] is None
    assert proposal["intent_draft"] is not None


def test_readiness_report_with_generic_dependency_reason_and_edge_timeout_is_blocked() -> None:
    report = {
        "request_id": "req_readiness_self_heal_004",
        "ts_ms": 1_800_200_000_005,
        "readiness_status": "DEGRADED",
        "reason_code": "SERVICE_UNAVAILABLE_DEPENDENCY",
        "services": {
            "rows": [
                {
                    "service_id": "edge.rpc.asr",
                    "status": "DOWN",
                    "available": False,
                    "reason_code": "SERVICE_UNAVAILABLE_EDGE_RPC",
                    "detail": "edge rpc unreachable: ConnectTimeout; attempted=http://192.168.1.101:17800:ConnectTimeout",
                },
                {
                    "service_id": "edge.rpc.tts",
                    "status": "DOWN",
                    "available": False,
                    "reason_code": "SERVICE_UNAVAILABLE_EDGE_RPC",
                    "detail": "edge rpc unreachable: ConnectTimeout; attempted=http://192.168.1.101:17800:ConnectTimeout",
                },
            ]
        },
        "graph_runtime": {
            "status": "DEGRADED",
            "reason_code": "SERVICE_UNAVAILABLE_DEPENDENCY",
        },
    }

    proposal = build_codex_self_heal_proposal_from_readiness_report(report)

    assert proposal["triggered"] is True
    assert proposal["dispatch_allowed"] is False
    assert proposal["dispatch_blocked_reason"] == "EXTERNAL_EDGE_RPC_OUTAGE"
