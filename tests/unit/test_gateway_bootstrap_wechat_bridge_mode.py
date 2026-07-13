from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from edge_client.gateway.bootstrap import (
    GatewayConfig,
    GatewayInstance,
    _ManagedUvicornServer,
    _PersistentAsyncLoop,
    _wechat_bridge_backend,
    start_gateway,
)
from edge_client.gateway.adapters.telegram_adapter import TelegramAdapter
from edge_client.gateway.models import Platform, UnifiedReply


class _FakeReporter:
    instances: list["_FakeReporter"] = []

    def __init__(self, host_url: str, hmac_key: str | None, timeout_s: float | None = None, **kwargs) -> None:  # noqa: ANN003
        self.host_url = host_url
        self.hmac_key = hmac_key
        self.timeout_s = timeout_s
        self.kwargs = dict(kwargs)
        self.retry_started = False
        self.retry_stopped = False
        self.__class__.instances.append(self)

    def start_retry_loop(self) -> None:
        self.retry_started = True

    def stop_retry_loop(self) -> None:
        self.retry_stopped = True


class _FakeOutboundDispatcher:
    def __init__(self) -> None:
        self.adapters = []

    def register(self, adapter) -> None:
        self.adapters.append(adapter)

    def dispatch_payload(self, payload) -> bool:  # noqa: ANN001
        return True


class _FakeThread:
    created: list["_FakeThread"] = []

    def __init__(self, target, name: str, daemon: bool) -> None:
        self.target = target
        self.name = name
        self.daemon = daemon
        self.started = False
        self.joined = False
        self.__class__.created.append(self)

    def start(self) -> None:
        self.started = True

    def is_alive(self) -> bool:
        return self.started and not self.joined

    def join(self, timeout=None) -> None:  # noqa: ANN001
        self.joined = True


class _FakeManagedServer:
    instances: list["_FakeManagedServer"] = []

    def __init__(self, app, *, host: str, port: int) -> None:  # noqa: ANN001
        self.app = app
        self.host = host
        self.port = port
        self.started = False
        self.stopped = False
        self.thread = _FakeThread(lambda: None, "gateway-server", True)
        self.__class__.instances.append(self)

    def start(self, *, timeout_s: float) -> None:
        assert timeout_s > 0
        self.thread.start()
        self.started = True

    def stop(self, *, timeout_s: float = 5.0) -> None:
        assert timeout_s >= 0
        self.stopped = True
        self.thread.join(timeout=timeout_s)


class _FakeBridge:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def publish_event(self, **kwargs):  # noqa: ANN003
        self.events.append(dict(kwargs))
        return {"ok": True}


def _start_gateway(
    config: GatewayConfig,
    *,
    tribe_bridge: _FakeBridge | None = None,
) -> GatewayInstance:
    return start_gateway(
        config,
        tribe_bridge=tribe_bridge or _FakeBridge(),
    )


class _FakeQQAdapter:
    def __init__(self, api_base: str, self_id: str | None = None) -> None:
        self.api_base = api_base
        self.self_id = self_id


class _FakeTransportStore:
    instances: list["_FakeTransportStore"] = []

    def __init__(self, db_path: str, *, verify_schema: bool) -> None:
        self.db_path = db_path
        self.verify_schema = verify_schema
        self.accepted: list[dict[str, object]] = []
        self.__class__.instances.append(self)

    def assert_schema_ready(self) -> None:
        return None

    def transport_counts(self) -> dict[str, int]:
        return {
            "inbox_pending": len(self.accepted),
            "outbox_pending": 0,
            "inbox_claimed": 0,
            "outbox_claimed": 0,
            "claimed": 0,
            "inbox_dlq": 0,
            "outbox_dlq": 0,
            "reconciliation_required": 0,
        }

    def accept_inbox(self, **kwargs):  # noqa: ANN003
        self.accepted.append(dict(kwargs))
        return {
            "status": "NEW",
            "inbox_id": "inbox-non-http",
            "ack_ref": "ack-non-http",
        }


@pytest.fixture(autouse=True)
def _safe_gateway_env(monkeypatch):  # noqa: ANN001
    monkeypatch.setenv("EDGE_GATEWAY_TRANSPORT_MODE", "DURABLE")
    monkeypatch.delenv("EDGE_GATEWAY_ACCEPT_NEW_INTAKE", raising=False)
    monkeypatch.setenv("EDGE_GATEWAY_HOST_HMAC_SECRET", "host-ingress-secret")
    monkeypatch.setenv("EDGE_GATEWAY_REPLY_HMAC_SECRET", "reply-ingress-secret")
    monkeypatch.setenv("EDGE_WECHAT_BRIDGE_ENABLED", "false")
    _FakeThread.created.clear()
    _FakeManagedServer.instances.clear()
    _FakeTransportStore.instances.clear()
    _FakeReporter.instances.clear()
    monkeypatch.setattr(
        "edge_client.gateway.bootstrap._ManagedUvicornServer",
        _FakeManagedServer,
    )
    monkeypatch.setattr(
        "edge_client.gateway.bootstrap._TRANSPORT_STORE_FACTORY",
        _FakeTransportStore,
    )


def test_start_gateway_registers_wechat_in_wxautox4_bridge_mode(monkeypatch) -> None:
    monkeypatch.setenv("EDGE_WECHAT_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("EDGE_WECHAT_BRIDGE_BACKEND", "wxautox4")
    monkeypatch.setenv(
        "NATSUME_WECHAT_GATEWAY_CALLBACK_HMAC_SECRET",
        "dedicated-wechat-callback-secret",
    )

    captured: dict[str, object] = {}

    monkeypatch.setattr("edge_client.channels.input_reporter.EdgeInputReporter", _FakeReporter)
    monkeypatch.setattr("edge_client.gateway.normalizer.MessageNormalizer", lambda: object())
    monkeypatch.setattr("edge_client.gateway.outbound.OutboundDispatcher", _FakeOutboundDispatcher)
    monkeypatch.setattr(
        "edge_client.gateway.server.create_gateway_app",
        lambda *, normalizer, adapters, outbound, **kwargs: captured.update({
            "normalizer": normalizer,
            "adapters": adapters,
            "outbound": outbound,
            "webhook_verifiers": kwargs["webhook_verifiers"],
        }) or object(),
    )
    monkeypatch.setattr("edge_client.gateway.bootstrap.threading.Thread", _FakeThread)

    instance = _start_gateway(
        GatewayConfig(
            host_url="http://127.0.0.1:18080",
            gateway_port=19000,
            enabled_platforms=("wechat",),
            wx_decrypt_output_dir="",
            hmac_key="generic-internal-secret",
        )
    )

    adapters = captured["adapters"]
    assert "wechat" in adapters
    wechat_verifier = captured["webhook_verifiers"]._verifiers["wechat"]
    assert wechat_verifier.shared_secret == "dedicated-wechat-callback-secret"
    assert wechat_verifier.shared_secret != "generic-internal-secret"
    assert instance.watcher is None
    assert instance.refresher is None


def test_legacy_uia_backend_falls_back_to_wxautox4(monkeypatch, caplog) -> None:
    monkeypatch.setenv("EDGE_WECHAT_BRIDGE_BACKEND", "uia")

    caplog.set_level("WARNING")

    assert _wechat_bridge_backend() == "wxautox4"
    assert "no longer supported" in caplog.text


def test_start_gateway_emits_platform_connected_and_disconnected(monkeypatch) -> None:
    bridge = _FakeBridge()
    monkeypatch.setattr("edge_client.channels.input_reporter.EdgeInputReporter", _FakeReporter)
    monkeypatch.setattr("edge_client.gateway.normalizer.MessageNormalizer", lambda: object())
    monkeypatch.setattr("edge_client.gateway.outbound.OutboundDispatcher", _FakeOutboundDispatcher)
    monkeypatch.setattr("edge_client.gateway.adapters.qq_adapter.QQAdapter", _FakeQQAdapter)
    monkeypatch.setattr(
        "edge_client.gateway.server.create_gateway_app",
        lambda *, normalizer, adapters, outbound, **_kwargs: object(),
    )
    monkeypatch.setattr("edge_client.gateway.bootstrap.threading.Thread", _FakeThread)

    instance = _start_gateway(
        GatewayConfig(
            host_url="http://127.0.0.1:18080",
            gateway_port=19000,
            enabled_platforms=("qq",),
            qq_onebot_api_base="http://127.0.0.1:5700",
            hmac_key="generic-internal-secret",
        ),
        tribe_bridge=bridge,
    )

    event_types = [str(item.get("event_type")) for item in bridge.events]
    assert "platform_connected" in event_types

    instance.stop()

    event_types = [str(item.get("event_type")) for item in bridge.events]
    assert "platform_disconnected" in event_types


def test_start_gateway_emits_platform_disconnected_when_config_missing(monkeypatch) -> None:
    bridge = _FakeBridge()
    monkeypatch.setattr("edge_client.channels.input_reporter.EdgeInputReporter", _FakeReporter)
    monkeypatch.setattr("edge_client.gateway.normalizer.MessageNormalizer", lambda: object())
    monkeypatch.setattr("edge_client.gateway.outbound.OutboundDispatcher", _FakeOutboundDispatcher)
    monkeypatch.setattr(
        "edge_client.gateway.server.create_gateway_app",
        lambda *, normalizer, adapters, outbound, **_kwargs: object(),
    )
    monkeypatch.setattr("edge_client.gateway.bootstrap.threading.Thread", _FakeThread)

    instance = _start_gateway(
        GatewayConfig(
            host_url="http://127.0.0.1:18080",
            gateway_port=19000,
            enabled_platforms=("qq",),
            qq_onebot_api_base=None,
        ),
        tribe_bridge=bridge,
    )

    event_types = [str(item.get("event_type")) for item in bridge.events]
    assert "platform_disconnected" in event_types
    assert "platform_connected" not in event_types
    instance.stop()


def _patch_durable_bootstrap(monkeypatch, *, app_factory=None) -> None:  # noqa: ANN001
    monkeypatch.setenv("EDGE_GATEWAY_TRANSPORT_MODE", "DURABLE")
    monkeypatch.setenv("EDGE_GATEWAY_HOST_HMAC_SECRET", "host-ingress-secret")
    monkeypatch.setenv("EDGE_GATEWAY_REPLY_HMAC_SECRET", "reply-ingress-secret")
    monkeypatch.setenv(
        "NATSUME_WECHAT_GATEWAY_CALLBACK_HMAC_SECRET",
        "dedicated-wechat-callback-secret",
    )
    monkeypatch.setattr("edge_client.channels.input_reporter.EdgeInputReporter", _FakeReporter)
    monkeypatch.setattr("edge_client.gateway.outbound.OutboundDispatcher", _FakeOutboundDispatcher)
    monkeypatch.setattr(
        "edge_client.gateway.bootstrap._TRANSPORT_STORE_FACTORY",
        _FakeTransportStore,
    )
    monkeypatch.setattr(
        "edge_client.gateway.server.create_gateway_app",
        app_factory or (lambda **_kwargs: object()),
    )
    monkeypatch.setattr("edge_client.gateway.bootstrap.threading.Thread", _FakeThread)


def test_durable_intake_kill_switch_keeps_worker_without_legacy_drain(monkeypatch) -> None:
    _patch_durable_bootstrap(monkeypatch)
    monkeypatch.setenv("EDGE_GATEWAY_ACCEPT_NEW_INTAKE", "false")

    instance = _start_gateway(GatewayConfig(enabled_platforms=()))

    names = [thread.name for thread in _FakeThread.created if thread.started]
    assert "gateway-durable-delivery" in names
    assert "gateway-server" in names
    assert instance.delivery_runtime.delivery_enabled is True
    assert instance.delivery_runtime.intake_enabled is False
    assert "drained" not in instance.delivery_runtime.stats()
    assert _FakeReporter.instances[-1].retry_started is False
    server = _FakeManagedServer.instances[-1]
    instance.stop()
    assert server.stopped is True
    assert server.thread.joined is True


def test_verifier_preflight_fails_before_any_background_component(monkeypatch) -> None:
    _patch_durable_bootstrap(monkeypatch)
    monkeypatch.setattr("edge_client.gateway.adapters.qq_adapter.QQAdapter", _FakeQQAdapter)

    with pytest.raises(RuntimeError, match="GW_INTERNAL_WEBHOOK_SECRET_MISSING"):
        _start_gateway(GatewayConfig(
            enabled_platforms=("qq",),
            qq_onebot_api_base="http://127.0.0.1:5700",
            hmac_key=None,
        ))

    assert _FakeThread.created == []
    assert _FakeTransportStore.instances == []
    assert _FakeReporter.instances[-1].retry_started is False


def test_retired_mode_still_requires_durable_reply_secret_before_start(monkeypatch) -> None:
    _patch_durable_bootstrap(monkeypatch)
    monkeypatch.setenv("EDGE_GATEWAY_TRANSPORT_MODE", "SHADOW")
    monkeypatch.delenv("EDGE_GATEWAY_REPLY_HMAC_SECRET", raising=False)

    with pytest.raises(RuntimeError, match="GW_REPLY_INGRESS_SECRET_MISSING"):
        _start_gateway(GatewayConfig(enabled_platforms=()))

    assert _FakeThread.created == []
    assert _FakeTransportStore.instances == []
    assert _FakeReporter.instances[-1].retry_started is False


def test_schema_preflight_fails_before_any_background_component(monkeypatch) -> None:
    class _FailingStore:
        def __init__(self, *_args, **_kwargs) -> None:
            raise RuntimeError("schema drift")

    _patch_durable_bootstrap(monkeypatch)
    monkeypatch.setattr(
        "edge_client.gateway.bootstrap._TRANSPORT_STORE_FACTORY",
        _FailingStore,
    )

    with pytest.raises(RuntimeError, match="schema drift"):
        _start_gateway(GatewayConfig(enabled_platforms=()))

    assert _FakeThread.created == []
    assert _FakeReporter.instances[-1].retry_started is False


def test_startup_activation_failure_cleans_started_components(monkeypatch) -> None:
    class _ExplodingServer(_FakeManagedServer):
        def start(self, *, timeout_s: float) -> None:
            raise RuntimeError("server thread failed")

    _patch_durable_bootstrap(monkeypatch)
    monkeypatch.setattr(
        "edge_client.gateway.bootstrap._ManagedUvicornServer",
        _ExplodingServer,
    )

    with pytest.raises(RuntimeError, match="server thread failed"):
        _start_gateway(GatewayConfig(enabled_platforms=()))

    assert not any(
        thread.name == "gateway-durable-delivery"
        for thread in _FakeThread.created
    )
    assert _ExplodingServer.instances[-1].stopped is True
    assert _FakeReporter.instances[-1].retry_stopped is False


def test_wechat_db_watcher_commits_to_0059_without_legacy_reporter(monkeypatch) -> None:
    class _FakeMessage:
        platform = SimpleNamespace(value="wechat")
        msg_id = "wx-event-1"
        conversation_id = "wx-conversation-1"

        @staticmethod
        def as_report_payload() -> dict[str, object]:
            return {
                "source": "GATEWAY",
                "platform": "wechat",
                "msg_id": "wx-event-1",
                "conversation_id": "wx-conversation-1",
            }

    class _Normalizer:
        def normalize(self, message, *, deduplicate=True):  # noqa: ANN001
            assert deduplicate is False
            return message

    class _WxService:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

    class _WxAdapter:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        @staticmethod
        def convert_wx_message(_message):  # noqa: ANN001
            return _FakeMessage()

    class _Watcher:
        def __init__(self, _service, callback, **_kwargs) -> None:
            self.callback = callback

        def start(self) -> str:
            self.callback([object()])
            return "test"

        def stop(self) -> None:
            return None

    _patch_durable_bootstrap(monkeypatch)
    monkeypatch.setenv("EDGE_GATEWAY_ACCEPT_NEW_INTAKE", "true")
    monkeypatch.setattr("edge_client.gateway.normalizer.MessageNormalizer", _Normalizer)
    monkeypatch.setattr("edge_client.services.wx_db_service.WxDbService", _WxService)
    monkeypatch.setattr("edge_client.gateway.adapters.wechat_adapter.WeChatAdapter", _WxAdapter)
    monkeypatch.setattr("edge_client.services.wx_watcher.WxDbWatcher", _Watcher)

    instance = _start_gateway(GatewayConfig(
        enabled_platforms=("wechat",),
        wx_decrypt_output_dir="D:/fake-decrypt",
        hmac_key="internal-webhook-secret",
    ))

    accepted = _FakeTransportStore.instances[-1].accepted
    assert len(accepted) == 1
    assert accepted[0]["surface"] == "gateway.non-http"
    assert accepted[0]["platform_event_id"] == "wx-event-1"
    assert _FakeReporter.instances[-1].retry_started is False
    instance.stop()


def test_telegram_listener_commits_to_0059_without_legacy_reporter(monkeypatch) -> None:
    class _FakeMessage:
        platform = SimpleNamespace(value="telegram")
        msg_id = "tg-event-1"
        conversation_id = "tg-conversation-1"

        @staticmethod
        def as_report_payload() -> dict[str, object]:
            return {
                "source": "GATEWAY",
                "platform": "telegram",
                "msg_id": "tg-event-1",
                "conversation_id": "tg-conversation-1",
            }

    class _Normalizer:
        def normalize(self, message, *, deduplicate=True):  # noqa: ANN001
            assert deduplicate is False
            return message

    class _TelegramAdapter:
        def __init__(self, **_kwargs) -> None:
            self.connected = False
            self.listening = False
            self.connect_loop = None
            self.listen_loop = None
            self.disconnect_loop = None

        async def connect(self) -> bool:
            assert _FakeManagedServer.instances[-1].started is True
            self.connect_loop = asyncio.get_running_loop()
            self.connected = True
            return True

        async def start_listening(self, callback) -> None:  # noqa: ANN001
            worker = next(
                thread for thread in _FakeThread.created
                if thread.name == "gateway-durable-delivery"
            )
            assert worker.started is True
            self.listen_loop = asyncio.get_running_loop()
            self.listening = True
            callback(_FakeMessage())

        async def disconnect(self) -> None:
            self.disconnect_loop = asyncio.get_running_loop()
            self.connected = False

    _patch_durable_bootstrap(monkeypatch)
    monkeypatch.setenv("EDGE_GATEWAY_ACCEPT_NEW_INTAKE", "true")
    monkeypatch.setattr("edge_client.gateway.normalizer.MessageNormalizer", _Normalizer)
    monkeypatch.setattr(
        "edge_client.gateway.adapters.telegram_adapter.TelegramAdapter",
        _TelegramAdapter,
    )

    instance = _start_gateway(GatewayConfig(
        enabled_platforms=("telegram",),
        telegram_api_id=123,
        telegram_api_hash="api-hash",
        telegram_phone="+10000000000",
        hmac_key="internal-webhook-secret",
    ))

    accepted = _FakeTransportStore.instances[-1].accepted
    assert len(accepted) == 1
    assert accepted[0]["surface"] == "gateway.non-http"
    assert accepted[0]["platform_event_id"] == "tg-event-1"
    assert instance.telegram_adapter.listening is True
    telegram_loop = instance.telegram_adapter.connect_loop
    assert telegram_loop is instance.telegram_adapter.listen_loop
    assert telegram_loop.is_running() is True
    assert _FakeReporter.instances[-1].retry_started is False
    instance.stop()
    assert instance.telegram_adapter.disconnect_loop is telegram_loop
    assert telegram_loop.is_closed() is True


def test_telegram_durable_drain_connects_for_egress_without_listener(monkeypatch) -> None:
    class _TelegramAdapter:
        def __init__(self, **_kwargs) -> None:
            self.connected = False
            self.listening = False
            self.loop = None

        async def connect(self) -> bool:
            self.loop = asyncio.get_running_loop()
            self.connected = True
            return True

        async def start_listening(self, _callback) -> None:  # noqa: ANN001
            self.listening = True

        async def disconnect(self) -> None:
            assert asyncio.get_running_loop() is self.loop
            self.connected = False

    _patch_durable_bootstrap(monkeypatch)
    monkeypatch.setenv("EDGE_GATEWAY_ACCEPT_NEW_INTAKE", "false")
    monkeypatch.setattr(
        "edge_client.gateway.adapters.telegram_adapter.TelegramAdapter",
        _TelegramAdapter,
    )

    instance = _start_gateway(GatewayConfig(
        enabled_platforms=("telegram",),
        telegram_api_id=123,
        telegram_api_hash="api-hash",
        telegram_phone="+10000000000",
        hmac_key="internal-webhook-secret",
    ))

    assert instance.telegram_adapter.connected is True
    assert instance.telegram_adapter.listening is False
    assert instance.telegram_adapter.loop.is_running() is True
    instance.stop()
    assert instance.telegram_adapter.connected is False


def test_persistent_async_loop_runs_until_explicit_stop() -> None:
    runner = _PersistentAsyncLoop(name="test-gateway-async-loop")
    runner.start(timeout_s=1.0)

    async def _capture_loop():
        return asyncio.get_running_loop()

    loop = runner.run(_capture_loop(), timeout_s=1.0)
    assert loop is runner.loop
    assert loop.is_running() is True

    runner.stop(timeout_s=1.0)

    assert runner.thread.is_alive() is False
    assert loop.is_closed() is True


def test_persistent_async_loop_stop_timeout_is_explicit() -> None:
    class _StuckThread:
        ident = 1

        @staticmethod
        def is_alive() -> bool:
            return True

        @staticmethod
        def join(timeout=None) -> None:  # noqa: ANN001
            return None

    runner = _PersistentAsyncLoop(name="stuck-gateway-async-loop")
    runner.thread = _StuckThread()

    with pytest.raises(
        RuntimeError,
        match="GW_ASYNC_LOOP_STOP_TIMEOUT:stuck-gateway-async-loop",
    ):
        runner.stop(timeout_s=0.01)


def test_managed_uvicorn_server_hands_off_readiness_and_stops(monkeypatch) -> None:
    class _Config:
        def __init__(self, app, **kwargs) -> None:  # noqa: ANN001
            self.app = app
            self.kwargs = kwargs

    class _Server:
        def __init__(self, config) -> None:  # noqa: ANN001
            self.config = config
            self.started = False
            self.should_exit = False
            self.force_exit = False

        def run(self) -> None:
            self.started = True
            while not self.should_exit:
                import time

                time.sleep(0.001)

    monkeypatch.setattr("uvicorn.Config", _Config)
    monkeypatch.setattr("uvicorn.Server", _Server)
    managed = _ManagedUvicornServer(object(), host="127.0.0.1", port=19000)

    managed.start(timeout_s=1.0)

    assert managed.server.started is True
    assert managed.thread.is_alive() is True
    managed.stop(timeout_s=1.0)
    assert managed.server.should_exit is True
    assert managed.thread.is_alive() is False


def test_managed_uvicorn_server_propagates_bind_failure(monkeypatch) -> None:
    class _Config:
        def __init__(self, app, **kwargs) -> None:  # noqa: ANN001
            pass

    class _Server:
        started = False
        should_exit = False
        force_exit = False

        def __init__(self, _config) -> None:  # noqa: ANN001
            pass

        @staticmethod
        def run() -> None:
            raise OSError("address already in use")

    monkeypatch.setattr("uvicorn.Config", _Config)
    monkeypatch.setattr("uvicorn.Server", _Server)
    managed = _ManagedUvicornServer(object(), host="127.0.0.1", port=19000)

    with pytest.raises(RuntimeError, match="GW_SERVER_START_FAILED.*address already in use"):
        managed.start(timeout_s=1.0)

    assert managed.thread.is_alive() is False


def test_managed_uvicorn_server_startup_wait_is_bounded(monkeypatch) -> None:
    class _Config:
        def __init__(self, app, **kwargs) -> None:  # noqa: ANN001
            pass

    class _Server:
        started = False
        force_exit = False

        def __init__(self, _config) -> None:  # noqa: ANN001
            self.should_exit = False

        def run(self) -> None:
            while not self.should_exit:
                import time

                time.sleep(0.001)

    monkeypatch.setattr("uvicorn.Config", _Config)
    monkeypatch.setattr("uvicorn.Server", _Server)
    managed = _ManagedUvicornServer(object(), host="127.0.0.1", port=19000)

    with pytest.raises(TimeoutError, match="GW_SERVER_START_TIMEOUT"):
        managed.start(timeout_s=0.1)

    assert managed.server.should_exit is True
    assert managed.thread.is_alive() is False


def test_managed_uvicorn_server_stop_timeout_is_explicit(monkeypatch) -> None:
    class _Config:
        def __init__(self, app, **kwargs) -> None:  # noqa: ANN001
            pass

    class _Server:
        started = True
        force_exit = False

        def __init__(self, _config) -> None:  # noqa: ANN001
            self.should_exit = False

    class _StuckThread:
        ident = 1

        @staticmethod
        def is_alive() -> bool:
            return True

        @staticmethod
        def join(timeout=None) -> None:  # noqa: ANN001
            return None

    monkeypatch.setattr("uvicorn.Config", _Config)
    monkeypatch.setattr("uvicorn.Server", _Server)
    managed = _ManagedUvicornServer(object(), host="127.0.0.1", port=19000)
    managed.thread = _StuckThread()

    with pytest.raises(RuntimeError, match="GW_SERVER_STOP_TIMEOUT"):
        managed.stop(timeout_s=0.01)

    assert managed.server.should_exit is True
    assert managed.server.force_exit is True


def test_telegram_egress_fails_fast_without_running_owned_loop() -> None:
    adapter = TelegramAdapter(
        api_id=123,
        api_hash="api-hash",
        phone="+10000000000",
    )
    adapter._client = object()
    adapter._connected = True
    adapter._event_loop = None

    assert adapter.send_reply(UnifiedReply(
        conversation_id="tg_dm_123",
        platform=Platform.TELEGRAM,
        content="hello",
    )) is False
    assert adapter.health()["stats"]["send_errors"] == 1


def test_gateway_stop_keeps_outbound_dependencies_when_worker_does_not_stop() -> None:
    class _Runtime:
        def __init__(self) -> None:
            self.calls = 0
            self.intake_disabled = False

        def disable_intake(self) -> None:
            self.intake_disabled = True

        def stop(self, *, timeout_s: float) -> None:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("worker still alive")

    class _Adapter:
        def __init__(self) -> None:
            self.disconnected = False

        async def disconnect(self) -> None:
            self.disconnected = True

    class _Loop:
        def __init__(self) -> None:
            self.stopped = False

        @staticmethod
        def run(coroutine, *, timeout_s: float):  # noqa: ANN001
            return asyncio.run(coroutine)

        def stop(self, *, timeout_s: float) -> None:
            self.stopped = True

    class _Process:
        def __init__(self) -> None:
            self.terminated = False

        def terminate(self) -> None:
            self.terminated = True

        @staticmethod
        def wait(timeout: float) -> None:
            return None

    instance = GatewayInstance()
    instance.delivery_runtime = _Runtime()
    instance.telegram_adapter = _Adapter()
    instance.async_loop = _Loop()
    instance.wechat_bridge_process = _Process()

    with pytest.raises(RuntimeError, match="worker still alive"):
        instance.stop()

    assert instance.delivery_runtime.intake_disabled is True
    assert instance.telegram_adapter.disconnected is False
    assert instance.async_loop.stopped is False
    assert instance.wechat_bridge_process.terminated is False
    assert instance._stopped is False

    instance.stop()
    assert instance.telegram_adapter.disconnected is True
    assert instance.async_loop.stopped is True
    assert instance.wechat_bridge_process.terminated is True


def test_gateway_stop_preserves_dependencies_when_server_does_not_stop() -> None:
    class _Runtime:
        def __init__(self) -> None:
            self.intake_disabled = False
            self.stopped = False

        def disable_intake(self) -> None:
            self.intake_disabled = True

        def stop(self, *, timeout_s: float) -> None:
            self.stopped = True

    class _Server:
        @staticmethod
        def stop(*, timeout_s: float) -> None:
            raise RuntimeError("GW_SERVER_STOP_TIMEOUT")

    class _Adapter:
        def __init__(self) -> None:
            self.disconnected = False

        async def disconnect(self) -> None:
            self.disconnected = True

    instance = GatewayInstance()
    instance.delivery_runtime = _Runtime()
    instance.server = _Server()
    instance.telegram_adapter = _Adapter()

    with pytest.raises(RuntimeError, match="GW_SERVER_STOP_TIMEOUT"):
        instance.stop()

    assert instance.delivery_runtime.intake_disabled is True
    assert instance.delivery_runtime.stopped is False
    assert instance.telegram_adapter.disconnected is False
    assert instance._stopped is False


def test_gateway_stop_is_retryable_when_async_loop_does_not_stop() -> None:
    class _Runtime:
        def __init__(self) -> None:
            self.intake_disabled = False

        def disable_intake(self) -> None:
            self.intake_disabled = True

        @staticmethod
        def stop(*, timeout_s: float) -> None:
            return None

    class _Adapter:
        def __init__(self) -> None:
            self.disconnects = 0

        async def disconnect(self) -> None:
            self.disconnects += 1

    class _Loop:
        def __init__(self) -> None:
            self.stop_calls = 0

        @staticmethod
        def run(coroutine, *, timeout_s: float):  # noqa: ANN001
            return asyncio.run(coroutine)

        def stop(self, *, timeout_s: float) -> None:
            self.stop_calls += 1
            if self.stop_calls == 1:
                raise RuntimeError("GW_ASYNC_LOOP_STOP_TIMEOUT:test")

    class _Process:
        def __init__(self) -> None:
            self.terminated = False

        def terminate(self) -> None:
            self.terminated = True

        @staticmethod
        def wait(timeout: float) -> None:
            return None

    instance = GatewayInstance()
    instance.delivery_runtime = _Runtime()
    instance.telegram_adapter = _Adapter()
    instance.async_loop = _Loop()
    instance.wechat_bridge_process = _Process()

    with pytest.raises(RuntimeError, match="GW_ASYNC_LOOP_STOP_TIMEOUT:test"):
        instance.stop()

    assert instance.delivery_runtime.intake_disabled is True
    assert instance._shutdown.is_set() is False
    assert instance.wechat_bridge_process.terminated is False
    assert instance._stopped is False

    instance.stop()
    assert instance.wechat_bridge_process.terminated is True
    assert instance._stopped is True
