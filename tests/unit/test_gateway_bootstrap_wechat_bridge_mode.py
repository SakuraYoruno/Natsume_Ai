from __future__ import annotations

from edge_client.gateway.bootstrap import GatewayConfig, _wechat_bridge_backend, start_gateway


class _FakeReporter:
    def __init__(self, host_url: str, hmac_key: str | None) -> None:
        self.host_url = host_url
        self.hmac_key = hmac_key

    def start_retry_loop(self) -> None:
        return None

    def stop_retry_loop(self) -> None:
        return None


class _FakeOutboundDispatcher:
    def __init__(self) -> None:
        self.adapters = []

    def register(self, adapter) -> None:
        self.adapters.append(adapter)


class _FakeThread:
    def __init__(self, target, name: str, daemon: bool) -> None:
        self.target = target
        self.name = name
        self.daemon = daemon
        self.started = False

    def start(self) -> None:
        self.started = True


def test_start_gateway_registers_wechat_in_wxautox4_bridge_mode(monkeypatch) -> None:
    monkeypatch.setenv("EDGE_WECHAT_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("EDGE_WECHAT_BRIDGE_BACKEND", "wxautox4")

    captured: dict[str, object] = {}

    monkeypatch.setattr("edge_client.channels.input_reporter.EdgeInputReporter", _FakeReporter)
    monkeypatch.setattr("edge_client.gateway.normalizer.MessageNormalizer", lambda: object())
    monkeypatch.setattr("edge_client.gateway.router.GatewayRouter", lambda reporter: object())
    monkeypatch.setattr("edge_client.gateway.outbound.OutboundDispatcher", _FakeOutboundDispatcher)
    monkeypatch.setattr(
        "edge_client.gateway.server.create_gateway_app",
        lambda *, router, normalizer, adapters, outbound: captured.update({
            "router": router,
            "normalizer": normalizer,
            "adapters": adapters,
            "outbound": outbound,
        }) or object(),
    )
    monkeypatch.setattr("edge_client.gateway.bootstrap.threading.Thread", _FakeThread)

    instance = start_gateway(
        GatewayConfig(
            host_url="http://127.0.0.1:18080",
            gateway_port=19000,
            enabled_platforms=("wechat",),
            wx_decrypt_output_dir="",
        )
    )

    adapters = captured["adapters"]
    assert "wechat" in adapters
    assert instance.watcher is None
    assert instance.refresher is None


def test_legacy_uia_backend_falls_back_to_wxautox4(monkeypatch, caplog) -> None:
    monkeypatch.setenv("EDGE_WECHAT_BRIDGE_BACKEND", "uia")

    caplog.set_level("WARNING")

    assert _wechat_bridge_backend() == "wxautox4"
    assert "no longer supported" in caplog.text
