from __future__ import annotations

import asyncio
from pathlib import Path

from edge_client.channels.input_reporter import EdgeInputReporter
from edge_client.app.schemas import EdgeRpcEnvelope
from edge_client.rpc.auth import NonceReplayCache, validate_edge_rpc_auth
from edge_client.gateway.delivery_runtime import (
    GatewayDeliveryRuntime,
    GatewayTransportMode,
    load_gateway_transport_mode,
)
from host_brain.channels.edge_transport import HttpEdgeTransportPort, build_edge_envelope


ROOT = Path(__file__).resolve().parents[2]


def test_gateway_mode_converges_every_value_on_durable() -> None:
    for env in (
        {},
        {"EDGE_GATEWAY_TRANSPORT_MODE": "DURABLE"},
        {"EDGE_GATEWAY_TRANSPORT_MODE": "LEGACY_DRAIN"},
        {"EDGE_GATEWAY_TRANSPORT_MODE": "SHADOW"},
        {"EDGE_GATEWAY_TRANSPORT_MODE": "invalid"},
    ):
        assert load_gateway_transport_mode(env) is GatewayTransportMode.DURABLE

    runtime = GatewayDeliveryRuntime(
        mode="SHADOW",
        store=object(),
        reporter=object(),
        outbound=object(),
    )
    assert runtime.mode is GatewayTransportMode.DURABLE
    assert runtime.verification_enabled is True
    assert runtime.delivery_enabled is True


def test_input_reporter_has_no_second_queue_or_compat_entrypoint() -> None:
    reporter = EdgeInputReporter(
        host_url="http://127.0.0.1:8000",
        hmac_key="test-key",
    )
    assert not hasattr(reporter, "report_gateway_payload")
    assert not hasattr(reporter, "start_retry_loop")
    assert not hasattr(reporter, "stop_retry_loop")
    assert not hasattr(reporter, "cache_size")
    stats = reporter.stats()
    assert stats["transport_owner"] == "gateway_durable_store"
    assert not any("cache" in key or "dlq" in key for key in stats)


def test_typed_edge_port_fails_closed_without_hmac_key() -> None:
    result = asyncio.run(
        HttpEdgeTransportPort(
            base_url="http://127.0.0.1:17800",
            shared_key="",
        ).submit(
            path="/v1/edge/health",
            payload={},
            request_id="req-aw08",
            timeout_ms=200,
        )
    )
    assert result.success is False
    assert result.reason_code == "EDGE_RPC_AUTH_UNAVAILABLE"


def test_typed_edge_envelope_matches_edge_hmac_contract() -> None:
    envelope_dict = build_edge_envelope(
        request_id="req-aw08-auth",
        payload={"action": "status", "value": 1},
        shared_key="aw08-shared-key",
        ts_ms=1_000,
        nonce="nonce-aw08",
    )
    result = validate_edge_rpc_auth(
        envelope=EdgeRpcEnvelope(**envelope_dict),
        shared_key="aw08-shared-key",
        allow_insecure_auth=False,
        auth_window_ms=100,
        replay_cache=NonceReplayCache(window_ms=100),
        now_fn=lambda: 1_000,
    )
    assert result.success is True
    assert result.insecure_auth is False


def test_retired_edge_gateway_production_symbols_are_absent() -> None:
    runtime_source = (
        ROOT / "edge_client/gateway/delivery_runtime.py"
    ).read_text(encoding="utf-8")
    bootstrap_source = (
        ROOT / "edge_client/gateway/bootstrap.py"
    ).read_text(encoding="utf-8")
    reporter_source = (
        ROOT / "edge_client/channels/input_reporter.py"
    ).read_text(encoding="utf-8")
    assert not (ROOT / "host_brain/channels/edge_rpc_client.py").exists()

    for retired in ("LEGACY_DRAIN", "SHADOW"):
        assert retired not in runtime_source
        assert retired not in bootstrap_source
    for retired in (
        "_PendingReport",
        "report_gateway_payload",
        "start_retry_loop",
        "stop_retry_loop",
        "legacy_dlq",
        "cache_file",
    ):
        assert retired not in reporter_source


def test_deleted_gateway_router_has_no_production_import() -> None:
    assert not (ROOT / "edge_client/gateway/router.py").exists()
    sources = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for base in (ROOT / "edge_client", ROOT / "host_brain")
        for path in base.rglob("*.py")
    )
    assert "edge_client.gateway.router" not in sources
