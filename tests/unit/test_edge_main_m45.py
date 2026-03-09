"""M45-C: edge_client main entrypoint tests (T-EDGE-MAIN-01~03)."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import unittest

import pytest

from edge_client.app.fastapi_app import create_edge_fastapi_app
from edge_client.config import EdgeConfig, load_edge_config
from edge_client.runtime import bootstrap

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


def _load_test_config() -> EdgeConfig:
    result = load_edge_config(
        env={
            "Natsume_ENV": "test",
            "EDGE_ALLOW_INSECURE_AUTH": "true",
            "EDGE_RPC_GATEWAY_ENABLED": "true",
            "EDGE_ASR_ENABLED": "false",
            "EDGE_TTS_ENABLED": "false",
            "EDGE_BARGE_IN_ENABLED": "false",
            "EDGE_FALLBACK_LLM_ENABLED": "false",
            "EDGE_RESOURCE_GATE_ENABLED": "false",
        }
    )
    assert result.success and result.config is not None, result.as_dict()
    return result.config


class TestEdgeMainM45(unittest.TestCase):
    def test_t_edge_main_01_python_m_edge_client_help_has_no_module_not_found(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "edge_client", "--help"],
            cwd=str(ROOT),
            env=dict(os.environ),
            capture_output=True,
            text=True,
            timeout=30,
        )
        merged = f"{result.stdout}\n{result.stderr}"
        self.assertEqual(0, result.returncode, msg=merged)
        self.assertNotIn("ModuleNotFoundError", merged)

    @requires_test_client
    def test_t_edge_main_02_fastapi_lifespan_calls_startup_and_shutdown(self) -> None:
        config = _load_test_config()
        runtime = bootstrap(config)
        events: list[str] = []

        async def _startup(_runtime) -> None:
            events.append("startup")

        async def _shutdown(_runtime) -> None:
            events.append("shutdown")

        app = create_edge_fastapi_app(
            config=config,
            runtime=runtime,
            startup_hook=_startup,
            shutdown_hook=_shutdown,
        )

        with TestClient(app) as client:
            response = client.get("/v1/edge/health")
            self.assertEqual(200, response.status_code)

        self.assertEqual(["startup", "shutdown"], events)

    @requires_test_client
    def test_t_edge_main_03_health_endpoint_contains_status_field(self) -> None:
        config = _load_test_config()
        runtime = bootstrap(config)

        def _custom_collect(_runtime) -> dict[str, object]:
            return {
                "status": "degraded",
                "components": {"demo": {"status": "degraded"}},
                "resource_gate": {"status": "ok", "queue_depth": 0, "snapshot": {}},
                "timestamp": "2026-03-06T00:00:00Z",
            }

        app = create_edge_fastapi_app(
            config=config,
            runtime=runtime,
            health_collector=_custom_collect,
        )
        with TestClient(app) as client:
            response = client.get("/v1/edge/health")
            self.assertEqual(200, response.status_code)
            body = response.json()
            self.assertIn("status", body["event"])
            self.assertEqual("degraded", body["event"]["status"])


if __name__ == "__main__":
    unittest.main()
