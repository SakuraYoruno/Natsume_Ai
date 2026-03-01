import json
import unittest
from pathlib import Path

from edge_client.config import (
    S_EDGE_CONFIG,
    build_edge_runtime_statuses,
    load_edge_config,
)
from host_brain.app.config import (
    S_HOST_CONFIG,
    build_host_runtime_statuses,
    load_host_config,
)
from host_brain.core.errors import (
    CONFIG_ENV_INVALID,
    CONFIG_REQUIRED_MISSING,
    CONFIG_VALUE_INVALID,
    SERVICE_UNAVAILABLE_COLD_START,
)
from host_brain.core.services import S_SERVICE_HEALTH, ServiceHealthReporter, build_default_service_registry


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "config"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TestConfigContract(unittest.TestCase):
    def test_t_config_01_dev_cold_start_config_is_parseable_and_health_stable(self) -> None:
        host_fixture = _load_json(FIXTURES / "good" / "host_dev_no_key_env.json")
        edge_fixture = _load_json(FIXTURES / "good" / "edge_dev_defaults_env.json")

        host_result = load_host_config(env=host_fixture["env"])
        edge_result = load_edge_config(env=edge_fixture["env"])
        self.assertTrue(host_result.success, msg=host_result.as_dict())
        self.assertTrue(edge_result.success, msg=edge_result.as_dict())
        assert host_result.config is not None
        assert edge_result.config is not None

        statuses: dict[str, dict] = {}
        statuses.update(build_host_runtime_statuses(host_result.config))
        statuses.update(build_edge_runtime_statuses(edge_result.config))

        reporter = ServiceHealthReporter(registry=build_default_service_registry(), ledger=None)
        report = reporter.build_report(
            request_id="req_config_001",
            runtime_statuses=statuses,
            ts_ms=1_800_020_000_000,
        )
        self.assertEqual(S_SERVICE_HEALTH, report["stage"])
        self.assertEqual(SERVICE_UNAVAILABLE_COLD_START, report["reason_code"])
        self.assertTrue(
            any(
                row["service_id"] == "host.integrations.llm_gateway"
                and row["reason_code"] == SERVICE_UNAVAILABLE_COLD_START
                for row in report["services"]
            ),
            msg=report["services"],
        )
        self.assertTrue(
            any(
                row["service_id"] == "edge.rpc.fallback_llm"
                and row["reason_code"] == SERVICE_UNAVAILABLE_COLD_START
                for row in report["services"]
            ),
            msg=report["services"],
        )
        json.loads(json.dumps(report, ensure_ascii=False))

    def test_t_config_01_host_invalid_env_rejected(self) -> None:
        fixture = _load_json(FIXTURES / "bad" / "host_invalid_env.json")
        result = load_host_config(env=fixture["env"])
        self.assertFalse(result.success)
        self.assertEqual(S_HOST_CONFIG, result.stage)
        self.assertEqual(CONFIG_ENV_INVALID, result.reason_code)

    def test_t_config_01_host_prod_missing_key_rejected(self) -> None:
        fixture = _load_json(FIXTURES / "bad" / "host_prod_missing_key.json")
        result = load_host_config(env=fixture["env"])
        self.assertFalse(result.success)
        self.assertEqual(S_HOST_CONFIG, result.stage)
        self.assertEqual(CONFIG_REQUIRED_MISSING, result.reason_code)
        self.assertTrue(any(issue.field == "OPENAI_API_KEY" for issue in result.issues))

    def test_t_config_01_edge_invalid_port_rejected(self) -> None:
        fixture = _load_json(FIXTURES / "bad" / "edge_invalid_port.json")
        result = load_edge_config(env=fixture["env"])
        self.assertFalse(result.success)
        self.assertEqual(S_EDGE_CONFIG, result.stage)
        self.assertEqual(CONFIG_VALUE_INVALID, result.reason_code)
        self.assertTrue(any(issue.field == "EDGE_RPC_PORT" for issue in result.issues))

    def test_t_config_01_edge_prod_missing_hmac_rejected(self) -> None:
        fixture = _load_json(FIXTURES / "bad" / "edge_prod_missing_hmac.json")
        result = load_edge_config(env=fixture["env"])
        self.assertFalse(result.success)
        self.assertEqual(S_EDGE_CONFIG, result.stage)
        self.assertEqual(CONFIG_REQUIRED_MISSING, result.reason_code)
        self.assertTrue(any(issue.field == "EDGE_HMAC_SHARED_KEY" for issue in result.issues))


if __name__ == "__main__":
    unittest.main()
