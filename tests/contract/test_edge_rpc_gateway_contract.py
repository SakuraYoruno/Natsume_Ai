import json
import unittest
from pathlib import Path

import pytest

from edge_client.app.fastapi_app import create_edge_fastapi_app
from edge_client.config import load_edge_config
from edge_client.rpc.auth import build_edge_rpc_signature
from edge_client.runtime import build_edge_runtime
from host_brain.core.errors import (
    EDGE_RPC_AUTH_INVALID,
    EDGE_RPC_SCHEMA_INVALID,
    SERVICE_UNAVAILABLE_DEPENDENCY,
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


if __name__ == "__main__":
    unittest.main()
