from __future__ import annotations

from typing import Any

from edge_client.actuators.vrm_bridge_driver import VrmBridgeConfig, VrmBridgeDriver
from edge_client.actuators.vts_driver import VtsState
from edge_client.app.fastapi_app import create_edge_fastapi_app
from edge_client.config import load_edge_config
from edge_client.runtime import build_edge_runtime

try:
    from fastapi.testclient import TestClient

    _HAS_TEST_CLIENT = True
except Exception:
    TestClient = None  # type: ignore[assignment]
    _HAS_TEST_CLIENT = False


class _FakeVrmBridgeDriver(VrmBridgeDriver):
    def __init__(self) -> None:
        super().__init__(VrmBridgeConfig(host="127.0.0.1", port=18770, api_timeout_s=0.1))
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        self.calls.append((method, path, payload))
        if path == "/health":
            return {"status": "ok", "client_count": 1, "model_available": True}, None
        if path == "/command":
            return {"success": True, "detail": "accepted"}, None
        return None, "not found"


def test_vrm_bridge_driver_mirrors_vts_driver_contract() -> None:
    driver = _FakeVrmBridgeDriver()

    assert driver.connect().success is True
    assert driver.state == VtsState.AUTHENTICATED
    assert driver.set_expression("happy", intensity=0.8).success is True
    assert driver.talk_start().success is True
    assert driver.set_mouth_open_level(
        0.6,
        _include_companions=True,
        _batch_companions=True,
        _motion_values={"FaceAngleY": 8.0, "bad": "nope"},
    ).success is True
    assert driver.move_model(position_x=0.1, position_y=0.2, rotation=3.0).success is True
    assert driver.reset_to_neutral().success is True

    command_payloads = [payload for _, path, payload in driver.calls if path == "/command"]
    assert [payload["type"] for payload in command_payloads] == [
        "expression",
        "talk",
        "mouth",
        "move_model",
        "reset",
    ]
    assert command_payloads[2]["motion_values"] == {"FaceAngleY": 8.0}
    health = driver.health()
    assert health["backend"] == "vrm"
    assert health["status"] == "ok"


def test_edge_config_loads_vrm_avatar_backend() -> None:
    result = load_edge_config(
        env={
            "Natsume_ENV": "dev",
            "EDGE_AVATAR_BACKEND": "vrm",
            "EDGE_VRM_BRIDGE_HOST": "127.0.0.1",
            "EDGE_VRM_BRIDGE_PORT": "18770",
            "EDGE_VRM_BRIDGE_TIMEOUT_S": "0.5",
            "EDGE_VRM_MODEL_PATH": "C:/models/aimis.vrm",
        }
    )

    assert result.success is True
    assert result.config is not None
    assert result.config.avatar_backend == "vrm"
    assert result.config.vrm_bridge_port == 18770
    assert result.config.vrm_model_path == "C:/models/aimis.vrm"


def test_edge_vts_rpc_endpoint_routes_to_vrm_backend(monkeypatch: Any) -> None:
    if not _HAS_TEST_CLIENT:
        return

    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def _fake_request_json(
        self: VrmBridgeDriver,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        _ = self
        calls.append((method, path, payload))
        if path == "/health":
            return {"status": "ok", "client_count": 1, "model_available": True}, None
        if path == "/command":
            return {"success": True, "detail": "accepted"}, None
        return None, "not found"

    monkeypatch.setattr(VrmBridgeDriver, "_request_json", _fake_request_json)
    config_result = load_edge_config(
        env={
            "Natsume_ENV": "test",
            "EDGE_ALLOW_INSECURE_AUTH": "true",
            "EDGE_RPC_GATEWAY_ENABLED": "true",
            "EDGE_TTS_ENABLED": "false",
            "EDGE_ASR_ENABLED": "false",
            "EDGE_RESOURCE_GATE_ENABLED": "false",
            "EDGE_AVATAR_BACKEND": "vrm",
            "EDGE_VRM_BRIDGE_HOST": "127.0.0.1",
            "EDGE_VRM_BRIDGE_PORT": "18770",
        }
    )
    assert config_result.success is True
    assert config_result.config is not None
    runtime = build_edge_runtime(config_result.config)
    app = create_edge_fastapi_app(
        config=config_result.config,
        runtime=runtime,
        now_fn=lambda: 1_800_101_000_000,
    )
    client = TestClient(app)  # type: ignore[misc]

    response = client.post(
        "/v1/edge/vts",
        json={
            "request_id": "req-vrm-expression-001",
            "ts_ms": 1_800_101_000_000,
            "nonce": "nonce-vrm-expression-001",
            "signature": "insecure",
            "payload": {"action": "set_expression", "emotion_tag": "HAPPY"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["status"] == "ACCEPTED"
    assert body["event"]["emotion_tag"] == "HAPPY"
    command_payloads = [payload for _, path, payload in calls if path == "/command"]
    assert command_payloads[-1]["type"] == "expression"
    assert command_payloads[-1]["emotion_tag"] == "HAPPY"
