import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from edge_client.actuators.vts_driver import VtsDriverResult, VtsState
from edge_client.app.fastapi_app import create_edge_fastapi_app
from edge_client.config import load_edge_config
from edge_client.rpc.auth import build_edge_rpc_signature
from edge_client.runtime import build_edge_runtime
from host_brain.core.errors import (
    EDGE_RPC_AUTH_INVALID,
    EDGE_RPC_SCHEMA_INVALID,
    SERVICE_UNAVAILABLE_DEPENDENCY,
    VTS_EXPRESSION_TIMEOUT,
)

try:
    from fastapi.testclient import TestClient

    _HAS_TEST_CLIENT = True
except Exception:
    _HAS_TEST_CLIENT = False

requires_test_client = pytest.mark.skipif(
    not _HAS_TEST_CLIENT,
    reason="fastapi.testclient not available",
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "edge" / "gateway"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _build_signed_envelope(payload: dict, *, shared_key: str) -> dict:
    row = dict(payload)
    row["signature"] = build_edge_rpc_signature(
        request_id=row["request_id"],
        ts_ms=row["ts_ms"],
        nonce=row["nonce"],
        payload=row["payload"],
        shared_key=shared_key,
    )
    return row


@requires_test_client
class TestEdgeRpcGatewayContract(unittest.TestCase):
    def _build_client(self, *, tts_enabled: bool = True) -> TestClient:
        config_result = load_edge_config(
            env={
                "Natsume_ENV": "test",
                "EDGE_ALLOW_INSECURE_AUTH": "false",
                "EDGE_HMAC_SHARED_KEY": "edge-shared-key",
                "EDGE_RPC_GATEWAY_ENABLED": "true",
                "EDGE_TTS_ENABLED": ("true" if tts_enabled else "false"),
                "EDGE_BARGE_IN_ENABLED": "true",
                "EDGE_FALLBACK_LLM_ENABLED": "false",
            }
        )
        self.assertTrue(config_result.success, msg=config_result.as_dict())
        assert config_result.config is not None
        runtime = build_edge_runtime(config_result.config)
        app = create_edge_fastapi_app(
            config=config_result.config,
            runtime=runtime,
            now_fn=lambda: 1_800_101_000_000,
        )
        return TestClient(app)

    def test_t_edge_rpc_02_speak_accepts_valid_envelope(self) -> None:
        fixture = _load_json(FIXTURES / "good" / "speak_request_accept.json")
        payload = _build_signed_envelope(fixture, shared_key="edge-shared-key")
        client = self._build_client()

        response = client.post("/v1/edge/speak", json=payload)
        self.assertEqual(200, response.status_code)
        body = response.json()

        self.assertEqual(
            {"success", "status", "reason_code", "detail", "event", "ts_ms"},
            set(body.keys()),
        )
        # 验证入队事件存在（TTS_QUEUED），不要求合成成功（测试环境无 TTS 模型）
        event = body["event"]
        queue_event = event.get("queue_event") or event
        self.assertEqual("TTS_QUEUED", queue_event.get("event_type"))

    def test_t_edge_rpc_02_schema_invalid_rejected(self) -> None:
        fixture = _load_json(FIXTURES / "bad" / "missing_payload_reject.json")
        client = self._build_client()

        response = client.post("/v1/edge/speak", json=fixture)
        self.assertEqual(200, response.status_code)
        body = response.json()

        self.assertFalse(body["success"])
        self.assertEqual("REJECTED", body["status"])
        self.assertEqual(EDGE_RPC_SCHEMA_INVALID, body["reason_code"])

    def test_t_edge_rpc_02_signature_mismatch_rejected(self) -> None:
        fixture = _load_json(FIXTURES / "bad" / "speak_signature_mismatch_reject.json")
        client = self._build_client()

        response = client.post("/v1/edge/speak", json=fixture)
        self.assertEqual(200, response.status_code)
        body = response.json()

        self.assertFalse(body["success"])
        self.assertEqual("REJECTED", body["status"])
        self.assertEqual(EDGE_RPC_AUTH_INVALID, body["reason_code"])

    def test_t_edge_rpc_02_disabled_tts_rejected_with_stable_reason(self) -> None:
        fixture = _load_json(FIXTURES / "good" / "speak_request_accept.json")
        payload = _build_signed_envelope(fixture, shared_key="edge-shared-key")
        client = self._build_client(tts_enabled=False)

        response = client.post("/v1/edge/speak", json=payload)
        self.assertEqual(200, response.status_code)
        body = response.json()

        self.assertFalse(body["success"])
        self.assertEqual("REJECTED", body["status"])
        self.assertEqual(SERVICE_UNAVAILABLE_DEPENDENCY, body["reason_code"])

    def test_t_edge_rpc_02_vts_timeout_degrades_to_accepted(self) -> None:
        client = self._build_client()
        runtime = client.app.state.edge_runtime
        self.assertIsNotNone(runtime.vts_driver)
        runtime.vts_driver._state = VtsState.AUTHENTICATED
        runtime.vts_driver._vts = object()

        timeout_result = VtsDriverResult(
            success=False,
            reason_code=VTS_EXPRESSION_TIMEOUT,
            detail="expression timeout: simulated",
            action="set_expression",
            expression_id="exp_neutral_01",
            emotion_tag="NEUTRAL",
        )
        runtime.vts_driver.set_expression = MagicMock(return_value=timeout_result)
        runtime.vts_driver.trigger_reconnect = MagicMock(return_value=VtsDriverResult(
            success=True,
            reason_code=None,
            detail="reconnect started",
            action="trigger_reconnect",
        ))

        payload = _build_signed_envelope(
            {
                "request_id": "req-edge-vts-timeout-001",
                "ts_ms": 1_800_101_000_000,
                "nonce": "nonce-edge-vts-timeout-001",
                "payload": {
                    "action": "set_expression",
                    "emotion_tag": "NEUTRAL",
                },
            },
            shared_key="edge-shared-key",
        )

        response = client.post("/v1/edge/vts", json=payload)
        self.assertEqual(200, response.status_code)
        body = response.json()

        self.assertTrue(body["success"])
        self.assertEqual("ACCEPTED", body["status"])
        self.assertEqual(VTS_EXPRESSION_TIMEOUT, body["reason_code"])
        self.assertEqual("set_expression", body["event"]["action"])
        self.assertTrue(body["event"]["degraded"])
        runtime.vts_driver.trigger_reconnect.assert_called_once()

    def test_t_edge_rpc_02_vts_body_action_does_not_require_speech(self) -> None:
        client = self._build_client()
        runtime = client.app.state.edge_runtime
        runtime.voice_pipeline.perform_body_action = MagicMock(return_value={
            "success": True,
            "state": "finished",
            "detail": "body action wave finished",
            "action_id": "wave",
            "emotion_tag": "PLAYFUL",
            "speech_required": False,
            "frames_sent": 12,
            "error_count": 0,
        })

        payload = _build_signed_envelope(
            {
                "request_id": "req-edge-vts-body-action-001",
                "ts_ms": 1_800_101_000_000,
                "nonce": "nonce-edge-vts-body-action-001",
                "payload": {
                    "action": "body_action",
                    "action_id": "wave",
                    "emotion_tag": "PLAYFUL",
                    "emotion_intensity": 0.8,
                    "duration_s": 1.2,
                },
            },
            shared_key="edge-shared-key",
        )

        response = client.post("/v1/edge/vts", json=payload)
        self.assertEqual(200, response.status_code)
        body = response.json()

        self.assertTrue(body["success"])
        self.assertEqual("ACCEPTED", body["status"])
        self.assertFalse(body["event"]["speech_required"])
        self.assertEqual("wave", body["event"]["action_id"])
        runtime.voice_pipeline.perform_body_action.assert_called_once_with(
            action_id="wave",
            emotion_tag="PLAYFUL",
            intensity=0.8,
            duration_s=1.2,
        )


if __name__ == "__main__":
    unittest.main()
