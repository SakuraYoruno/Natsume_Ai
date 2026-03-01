import json
import unittest
from pathlib import Path

from host_brain.app.main import build_env_readiness_report
from host_brain.core.errors import ORCH_NODE_MISSING, SERVICE_UNAVAILABLE_DEPENDENCY
from host_brain.core.services import S_ENV_READY


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "services"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_nodes_from_fixture(fixture: dict) -> tuple[list[dict], int]:
    inventory = _load_json(ROOT / str(fixture["node_inventory_ref"]))
    return inventory["nodes"], int(inventory["expected_node_count"])


class TestEnvReadinessE2E(unittest.TestCase):
    def test_t_envready_01_partial_ready_report_is_schema_stable(self) -> None:
        fixture = _load_json(FIXTURES / "good" / "env_readiness_partial.json")
        nodes, expected_count = _load_nodes_from_fixture(fixture)

        report = build_env_readiness_report(
            env=fixture["env"],
            node_inventory=nodes,
            expected_node_count=expected_count,
            key_profiles=fixture["key_profiles"],
            graph_runtime=fixture["graph_runtime"],
            drill_summaries=fixture["drill_summaries"],
            host_env=fixture["host_env"],
            edge_env=fixture["edge_env"],
            dependency_probes=fixture["dependency_probes"],
            request_id=fixture["request_id"],
            ts_ms=1_800_170_000_000,
        )

        self.assertEqual(S_ENV_READY, report["stage"])
        self.assertEqual("staging", report["env"])
        self.assertEqual(9, report["node_count"])
        self.assertEqual(3, report["key_profiles"]["total"])
        self.assertEqual("DEGRADED", report["readiness_status"])
        self.assertEqual(SERVICE_UNAVAILABLE_DEPENDENCY, report["reason_code"])

        self.assertIn("summary", report["services"])
        self.assertIn("rows", report["services"])
        self.assertEqual("DEGRADED", report["services"]["summary"]["boot_status"])

        self.assertEqual("DEGRADED", report["graph_runtime"]["status"])
        self.assertEqual("ORCH_DEPENDENCY_UNAVAILABLE", report["graph_runtime"]["reason_code"])

        self.assertGreaterEqual(report["dependency_summary"]["unavailable"], 1)
        self.assertIn("host.retrieval.embedding", report["dependency_summary"]["unavailable_ids"])

        self.assertEqual(1, len(report["drill_summary"]))
        self.assertEqual("drill.dependency.provider_down", report["drill_summary"][0]["scenario_id"])
        self.assertEqual("request::req_drill_001", report["drill_summary"][0]["replay_ref"])

        json.loads(json.dumps(report, ensure_ascii=False))

    def test_t_envready_01_critical_dependency_down_reports_failed(self) -> None:
        fixture = _load_json(FIXTURES / "bad" / "env_readiness_critical.json")
        nodes, expected_count = _load_nodes_from_fixture(fixture)

        report = build_env_readiness_report(
            env=fixture["env"],
            node_inventory=nodes,
            expected_node_count=expected_count,
            key_profiles=fixture["key_profiles"],
            graph_runtime=fixture["graph_runtime"],
            drill_summaries=fixture["drill_summaries"],
            host_env=fixture["host_env"],
            edge_env=fixture["edge_env"],
            dependency_probes=fixture["dependency_probes"],
            request_id=fixture["request_id"],
            ts_ms=1_800_170_000_001,
        )

        self.assertEqual(S_ENV_READY, report["stage"])
        self.assertEqual("FAILED", report["readiness_status"])
        self.assertEqual(ORCH_NODE_MISSING, report["reason_code"])
        self.assertEqual("FAILED", report["graph_runtime"]["status"])

        self.assertEqual(1, report["key_profiles"]["total"])
        self.assertTrue(any(not row["available"] for row in report["dependency_matrix"]))
        self.assertEqual("FAILED", report["drill_summary"][0]["status"])
        self.assertEqual("drill.rollback.conflict", report["drill_summary"][0]["scenario_id"])
        json.loads(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
