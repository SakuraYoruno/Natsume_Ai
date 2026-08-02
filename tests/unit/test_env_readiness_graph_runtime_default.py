from __future__ import annotations

import json
from pathlib import Path

from host_brain.app.main import build_env_readiness_report


ROOT = Path(__file__).resolve().parents[2]
NODE_INVENTORY_FIXTURE = ROOT / "tests" / "fixtures" / "services" / "good" / "node_inventory_9.json"


def _load_node_inventory() -> tuple[list[dict], int]:
    payload = json.loads(NODE_INVENTORY_FIXTURE.read_text(encoding="utf-8-sig"))
    return list(payload.get("nodes") or []), int(payload.get("expected_node_count") or 0)


def _host_env() -> dict[str, str]:
    return {
        "Natsume_ENV": "dev",
        "HOST_LLM_PROVIDER": "mock",
        "HOST_ENABLE_RETRIEVAL": "true",
        "HOST_ENABLE_VISION": "true",
        "HOST_ENABLE_EDGE_RPC": "true",
    }


def _edge_env() -> dict[str, str]:
    return {
        "Natsume_ENV": "dev",
        "EDGE_RPC_GATEWAY_ENABLED": "true",
        "EDGE_ASR_ENABLED": "true",
        "EDGE_TTS_ENABLED": "true",
        "EDGE_BARGE_IN_ENABLED": "true",
        "EDGE_FALLBACK_LLM_ENABLED": "true",
        "EDGE_RESOURCE_GATE_ENABLED": "true",
    }


def test_graph_runtime_missing_stays_skipped_without_hiding_unprobed_nodes() -> None:
    nodes, expected_count = _load_node_inventory()
    report = build_env_readiness_report(
        env="dev",
        node_inventory=nodes,
        expected_node_count=expected_count,
        host_env=_host_env(),
        edge_env=_edge_env(),
        graph_runtime=None,
        request_id="req_graph_runtime_missing",
        ts_ms=1_800_170_100_000,
    )

    assert report["graph_runtime"]["status"] == "SKIPPED"
    graph_row = next(row for row in report["dependency_matrix"] if row["component_id"] == "graph_runtime")
    assert graph_row["available"] is True
    assert report["readiness_status"] == "ACCEPTANCE_PENDING_EXTERNAL"
    assert report["reason_code"] == "ACCEPTANCE_PENDING_EXTERNAL"
    assert report["node_deployment"]["summary"]["node_not_tested"] == expected_count


def test_graph_runtime_failed_still_forces_failed_readiness() -> None:
    nodes, expected_count = _load_node_inventory()
    report = build_env_readiness_report(
        env="dev",
        node_inventory=nodes,
        expected_node_count=expected_count,
        host_env=_host_env(),
        edge_env=_edge_env(),
        graph_runtime={
            "status": "FAILED",
            "reason_code": "ORCH_NODE_MISSING",
            "detail": "graph failed",
        },
        request_id="req_graph_runtime_failed",
        ts_ms=1_800_170_100_001,
    )

    assert report["graph_runtime"]["status"] == "FAILED"
    graph_row = next(row for row in report["dependency_matrix"] if row["component_id"] == "graph_runtime")
    assert graph_row["available"] is False
    assert report["readiness_status"] == "FAILED"
    assert report["reason_code"] == "ORCH_NODE_MISSING"
