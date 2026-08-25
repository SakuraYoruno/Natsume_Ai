import importlib.util
import json
import unittest
from pathlib import Path
from unittest import mock

from host_brain.core.services import S_ENV_READY


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"
TOOL_PATH = ROOT / "scripts" / "runtime" / "generate_env_readiness_report.py"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _load_tool_module():
    spec = importlib.util.spec_from_file_location("generate_env_readiness_report", TOOL_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load env readiness tool module: {TOOL_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestEnvReadinessReportToolContract(unittest.TestCase):
    def setUp(self) -> None:
        self.tool = _load_tool_module()

    def test_t_envready_01_tool_output_schema_is_stable(self) -> None:
        fixture = _load_json(FIXTURES / "services" / "good" / "env_readiness_partial.json")
        node_inventory = _load_json(ROOT / fixture["node_inventory_ref"])
        graph_payload = _load_json(FIXTURES / "orchestration" / "good" / "graph_main.json")

        env_values = {}
        env_values.update(fixture["host_env"])
        env_values.update(fixture["edge_env"])

        key_profiles_payload = {
            "current_epoch": 20260301,
            "host_node_id": "natsu-node-01",
            "edge_node_id": "natsu-node-06",
            "profiles": fixture["key_profiles"],
        }
        secret_store = {
            "sec://openai/node01": "sk-tool-probe",
            "sec://neo4j/node02": "neo4j:password",
            "sec://chroma/node04": "chroma-token",
        }

        dependency_probes = {
            "host.bond.neo4j": {"available": True, "detail": "neo4j dependency healthy"},
            "host.memory.chroma": {"available": True, "detail": "chroma dependency healthy"},
            "host.retrieval.recall_fusion": {"available": True, "detail": "surface recall dependency healthy"},
        }
        drill_rows = [
            {
                "scenario_id": "drill.dependency.provider_down",
                "stage": "S_LLM_GATEWAY_PROVIDER",
                "status": "DEGRADED",
                "reason_code_chain": ["SERVICE_UNAVAILABLE_DEPENDENCY"],
                "replay_ref": "request::req_drill_001",
                "model_profile_id": "mp.mock.v1",
            }
        ]

        with mock.patch.object(
            self.tool,
            "_build_dependency_probe_matrix",
            return_value=dependency_probes,
        ), mock.patch.object(
            self.tool,
            "_build_drill_summary_rows",
            return_value=drill_rows,
        ):
            report_a = self.tool.build_runtime_env_readiness_report(
                env_values=env_values,
                node_inventory_payload=node_inventory,
                key_profiles_payload=key_profiles_payload,
                secret_store=secret_store,
                graph_payload=graph_payload,
                request_id="req_envready_tool_001",
                ts_ms=1_800_170_000_100,
            )
            report_b = self.tool.build_runtime_env_readiness_report(
                env_values=env_values,
                node_inventory_payload=node_inventory,
                key_profiles_payload=key_profiles_payload,
                secret_store=secret_store,
                graph_payload=graph_payload,
                request_id="req_envready_tool_001",
                ts_ms=1_800_170_000_100,
            )

        for report in (report_a, report_b):
            self.assertEqual(S_ENV_READY, report["stage"])
            self.assertEqual("dev", report["env"])
            self.assertEqual(9, report["node_count"])
            self.assertIn(
                report["readiness_status"],
                {
                    "READY",
                    "DEGRADED",
                    "FAILED",
                    "ACCEPTANCE_PENDING_EXTERNAL",
                },
            )
            self.assertIn("dependency_matrix", report)
            self.assertIn("dependency_summary", report)
            self.assertIn("graph_runtime", report)
            self.assertIn("drill_summary", report)
            self.assertIn("orchestration_control_coverage", report)
            self.assertEqual("PARTIAL", report["orchestration_control_coverage"]["status"])
            self.assertEqual("req_envready_tool_001", report["request_id"])
            self.assertEqual(1_800_170_000_100, report["ts_ms"])
            json.loads(json.dumps(report, ensure_ascii=False))

        self.assertEqual(report_a["readiness_status"], report_b["readiness_status"])
        self.assertEqual(report_a["reason_code"], report_b["reason_code"])
        self.assertEqual(report_a["dependency_summary"], report_b["dependency_summary"])


if __name__ == "__main__":
    unittest.main()
