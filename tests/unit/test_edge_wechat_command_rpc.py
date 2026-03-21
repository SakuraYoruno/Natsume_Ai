import json
import os
from pathlib import Path
import tempfile
import unittest

import pytest

from edge_client.app.fastapi_app import create_edge_fastapi_app
from edge_client.config import load_edge_config
from edge_client.runtime import build_edge_runtime
from host_brain.core.errors import EDGE_RPC_SCHEMA_INVALID

try:
    from fastapi.testclient import TestClient

    _HAS_TEST_CLIENT = True
except Exception:
    _HAS_TEST_CLIENT = False


requires_test_client = pytest.mark.skipif(
    not _HAS_TEST_CLIENT,
    reason="fastapi.testclient not available",
)


@requires_test_client
class TestEdgeWechatCommandRpc(unittest.TestCase):
    _ENV_KEYS = (
        "EDGE_WECHAT_COMMAND_QUEUE_FILE",
        "EDGE_WECHAT_COMMAND_RESULT_FILE",
    )

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._saved_env = {key: os.environ.get(key) for key in self._ENV_KEYS}
        self.queue_file = Path(self._tmpdir.name) / "wechat_queue.jsonl"
        self.result_file = Path(self._tmpdir.name) / "wechat_result.jsonl"
        os.environ["EDGE_WECHAT_COMMAND_QUEUE_FILE"] = str(self.queue_file)
        os.environ["EDGE_WECHAT_COMMAND_RESULT_FILE"] = str(self.result_file)

    def tearDown(self) -> None:
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._tmpdir.cleanup()

    def _build_client(self) -> TestClient:
        config_result = load_edge_config(
            env={
                "Natsume_ENV": "test",
                "EDGE_ALLOW_INSECURE_AUTH": "true",
                "EDGE_RPC_GATEWAY_ENABLED": "true",
                "EDGE_ASR_ENABLED": "false",
                "EDGE_TTS_ENABLED": "false",
                "EDGE_BARGE_IN_ENABLED": "false",
                "EDGE_FALLBACK_LLM_ENABLED": "false",
            }
        )
        self.assertTrue(config_result.success, msg=config_result.as_dict())
        assert config_result.config is not None
        runtime = build_edge_runtime(config_result.config)
        app = create_edge_fastapi_app(
            config=config_result.config,
            runtime=runtime,
            now_fn=lambda: 1_800_200_000_000,
        )
        return TestClient(app)

    @staticmethod
    def _build_envelope(payload: dict) -> dict:
        return {
            "request_id": "req-wechat-command",
            "ts_ms": 1_800_200_000_000,
            "nonce": "nonce-wechat-command",
            "signature": "insecure",
            "payload": payload,
        }

    def test_wechat_command_queue_accepts_send_text(self) -> None:
        client = self._build_client()
        response = client.post(
            "/v1/edge/wechat/command",
            json=self._build_envelope(
                {
                    "action": "send_text",
                    "target": "Alice",
                    "content": "hello",
                    "wait_result": False,
                }
            ),
        )
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual("ACCEPTED", body["status"])
        self.assertEqual(None, body["reason_code"])

        self.assertTrue(self.queue_file.exists())
        rows = [
            json.loads(line)
            for line in self.queue_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(1, len(rows))
        self.assertEqual("send_text", rows[0]["action"])
        self.assertEqual("Alice", rows[0]["target"])
        self.assertEqual("hello", rows[0]["content"])

    def test_wechat_command_rejects_invalid_action(self) -> None:
        client = self._build_client()
        response = client.post(
            "/v1/edge/wechat/command",
            json=self._build_envelope(
                {
                    "action": "bad_action",
                    "target": "Alice",
                    "wait_result": False,
                }
            ),
        )
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertEqual("REJECTED", body["status"])
        self.assertEqual(EDGE_RPC_SCHEMA_INVALID, body["reason_code"])

    def test_wechat_command_wait_result_timeout(self) -> None:
        client = self._build_client()
        response = client.post(
            "/v1/edge/wechat/command",
            json=self._build_envelope(
                {
                    "action": "voice_call",
                    "target": "Alice",
                    "wait_result": True,
                    "timeout_ms": 350,
                }
            ),
        )
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertEqual("REJECTED", body["status"])
        self.assertEqual("WECHAT_COMMAND_TIMEOUT", body["reason_code"])

