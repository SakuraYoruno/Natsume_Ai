from __future__ import annotations

from edge_client.tribe.edge_tribe_bridge import EdgeTribeBridge


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = str(self._payload)
        self.content = b"{}"

    def json(self) -> dict:
        return dict(self._payload)


class _FakeSession:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.last_url = ""
        self.last_json = None
        self.last_headers = {}

    def post(self, url, json, headers, timeout):  # noqa: ANN001
        self.last_url = url
        self.last_json = json
        self.last_headers = headers
        return self.response


def test_edge_tribe_bridge_publish_success() -> None:
    fake_session = _FakeSession(_FakeResponse(202, {"accepted": True, "event_id": "evt_1", "ts_ms": 123}))
    bridge = EdgeTribeBridge(
        host_base_url="http://127.0.0.1:18080",
        token="token",
        enabled=True,
        session=fake_session,  # type: ignore[arg-type]
    )

    result = bridge.publish_event(
        event_type="body_sense_updated",
        source_agent="edge.somatic",
        payload={"arousal": 0.4},
    )

    assert result["ok"] is True
    assert result["event_id"] == "evt_1"
    assert fake_session.last_url.endswith("/internal/tribe_event")
    assert fake_session.last_json["event_type"] == "body_sense_updated"
    assert fake_session.last_headers["X-Internal-Token"] == "token"


def test_edge_tribe_bridge_publish_rejected() -> None:
    fake_session = _FakeSession(_FakeResponse(401, {"detail": "unauthorized"}))
    bridge = EdgeTribeBridge(
        host_base_url="http://127.0.0.1:18080",
        token="bad",
        enabled=True,
        session=fake_session,  # type: ignore[arg-type]
    )

    result = bridge.publish_event(event_type="x", source_agent="edge.channel")

    assert result["ok"] is False
    assert result["reason"] == "HTTP_REJECTED"


def test_edge_tribe_bridge_from_env_falls_back_to_rpc_host(monkeypatch) -> None:
    monkeypatch.delenv("EDGE_TRIBE_HOST_BASE_URL", raising=False)
    monkeypatch.delenv("EDGE_EVENT_REPORT_HOST_URL", raising=False)
    monkeypatch.setenv("EDGE_RPC_HOST", "10.0.0.8")
    monkeypatch.setenv("EDGE_TRIBE_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("EDGE_TRIBE_BRIDGE_TIMEOUT_MS", "3100")

    bridge = EdgeTribeBridge.from_env()

    assert bridge.enabled is True
    assert bridge.host_base_url == "http://10.0.0.8:18080"
    assert bridge.timeout_ms == 3100
