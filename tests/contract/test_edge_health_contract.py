import json
import unittest
from pathlib import Path

import pytest

from edge_client.app.fastapi_app import create_edge_fastapi_app
from edge_client.config import load_edge_config
from edge_client.runtime import S_EDGE_HEALTH, build_edge_runtime

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
FIXTURES = ROOT / "tests" / "fixtures" / "edge" / "health"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


@requires_test_client
class TestEdgeHealthContract(unittest.TestCase):
    def test_t_edge_health_01_capability_matrix_degraded_snapshot_is_stable(self) -> None:
        fixture = _load_json(FIXTURES / "good" / "capabilities_disabled_degraded.json")
        config_result = load_edge_config(env=fixture["env"])
        self.assertTrue(config_result.success, msg=config_result.as_dict())
        assert config_result.config is not None

        runtime = build_edge_runtime(config_result.config)
        app = create_edge_fastapi_app(
            config=config_result.config,
            runtime=runtime,
            now_fn=lambda: 1_800_102_000_000,
        )
        client = TestClient(app)

        response = client.get("/v1/edge/health")
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertTrue(body["success"], msg=body)
        self.assertEqual("ACCEPTED", body["status"])
        self.assertIsNone(body["reason_code"])

        event = body["event"]
        self.assertEqual(S_EDGE_HEALTH, event["stage"])
        self.assertIn("services", event)
        self.assertIn("resources", event)
        self.assertIn("components", event)
        self.assertIn("config_snapshot", event)
        self.assertEqual(1_800_102_000_000, event["ts_ms"])

        self.assertFalse(event["services"]["edge.rpc.asr"]["available"])
        self.assertFalse(event["services"]["edge.rpc.tts"]["available"])
        self.assertFalse(event["services"]["edge.rpc.fallback_llm"]["available"])

        resources = event["resources"]
        self.assertIn("vram_total_mb", resources)
        self.assertIn("vram_used_mb", resources)
        self.assertIn("queue_depth", resources)
        self.assertEqual(0, resources["queue_depth"])

        components = event["components"]
        self.assertFalse(components["asr"]["enabled"])
        self.assertFalse(components["tts"]["enabled"])
        self.assertFalse(components["fallback"]["enabled"])
        self.assertFalse(components["resource_gate"]["enabled"])

        config_snapshot = event["config_snapshot"]
        self.assertIn("hmac_shared_key_set", config_snapshot)
        self.assertNotIn("hmac_shared_key", config_snapshot)
        self.assertEqual("test", config_snapshot["env"])

    def test_t_edge_health_01_capability_matrix_enabled_snapshot_is_stable(self) -> None:
        fixture = _load_json(FIXTURES / "good" / "capabilities_enabled_accept.json")
        config_result = load_edge_config(env=fixture["env"])
        self.assertTrue(config_result.success, msg=config_result.as_dict())
        assert config_result.config is not None

        runtime = build_edge_runtime(config_result.config)
        app = create_edge_fastapi_app(
            config=config_result.config,
            runtime=runtime,
            now_fn=lambda: 1_800_102_000_001,
        )
        client = TestClient(app)

        response = client.get("/v1/edge/health")
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertTrue(body["success"], msg=body)
        self.assertEqual("ACCEPTED", body["status"])
        self.assertIsNone(body["reason_code"])

        event = body["event"]
        self.assertEqual(S_EDGE_HEALTH, event["stage"])
        self.assertTrue(event["services"]["edge.rpc.asr"]["available"])
        self.assertTrue(event["services"]["edge.rpc.tts"]["available"])
        self.assertTrue(event["services"]["edge.rpc.fallback_llm"]["available"])
        self.assertTrue(event["components"]["asr"]["enabled"])
        self.assertTrue(event["components"]["tts"]["enabled"])
        self.assertTrue(event["components"]["fallback"]["enabled"])


if __name__ == "__main__":
    unittest.main()
