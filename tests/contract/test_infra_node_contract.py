import json
import unittest
from pathlib import Path

from host_brain.core.errors import (
    INFRA_NODE_MISSING_SERVICE,
    INFRA_NODE_PROBE_TIMEOUT,
    INFRA_NODE_ROLE_CONFLICT,
)
from host_brain.core.services import (
    S_INFRA_NODE_CATALOG,
    S_INFRA_NODE_PROBE,
    build_node_deployment_report,
    validate_node_inventory,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "services"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _find_node_row(rows: list[dict], node_id: str) -> dict:
    matched = [row for row in rows if row["node_id"] == node_id]
    if len(matched) != 1:
        raise AssertionError(f"node row not unique for {node_id}: {matched}")
    return matched[0]


class TestInfraNodeContract(unittest.TestCase):
    def test_t_infra_01_nine_node_inventory_is_deterministic(self) -> None:
        fixture = _load_json(FIXTURES / "good" / "node_inventory_9.json")
        report = build_node_deployment_report(
            nodes=fixture["nodes"],
            required_services=fixture["required_services"],
            expected_node_count=fixture["expected_node_count"],
            runtime_statuses=fixture["runtime_statuses"],
            request_id=fixture["request_id"],
            ts_ms=1_800_110_000_000,
        )

        self.assertEqual(S_INFRA_NODE_CATALOG, report["stage"])
        self.assertEqual("ACCEPTANCE_PENDING_EXTERNAL", report["status"])
        self.assertEqual(
            fixture["expected_node_count"],
            report["summary"]["node_not_tested"],
        )
        self.assertEqual("ACCEPTANCE_PENDING_EXTERNAL", report["reason_code"])
        self.assertEqual(8, report["summary"]["node_count"])
        self.assertEqual(8, len(report["node_catalog"]))
        self.assertEqual(0, report["validation"]["summary"]["issue_count"])

        mapping = report["service_mapping"]
        self.assertEqual("natsu-node-01", mapping["host.integrations.llm_gateway"]["primary"])
        self.assertEqual(["natsu-node-03", "natsu-node-09"], mapping["host.integrations.llm_gateway"]["secondary"])
        self.assertEqual("natsu-node-06", mapping["edge.rpc.gateway"]["primary"])
        self.assertEqual(["natsu-node-09"], mapping["edge.rpc.gateway"]["secondary"])

        for row in report["node_catalog"]:
            self.assertIn("node_id", row)
            self.assertIn("region", row)
            self.assertIn("role", row)
            self.assertIn("services", row)
            self.assertIn("health_endpoint", row)
            self.assertIn("key_profile_id", row)
            self.assertIsInstance(row["services"], list)

        json.loads(json.dumps(report, ensure_ascii=False))

    def test_t_infra_01_probe_timeout_reason_and_stage_are_stable(self) -> None:
        fixture = _load_json(FIXTURES / "bad" / "node_probe_timeout.json")

        report_a = build_node_deployment_report(
            nodes=fixture["nodes"],
            required_services=fixture["required_services"],
            expected_node_count=fixture["expected_node_count"],
            runtime_statuses=fixture["runtime_statuses"],
            request_id=fixture["request_id"],
            ts_ms=1_800_110_000_001,
        )
        report_b = build_node_deployment_report(
            nodes=fixture["nodes"],
            required_services=fixture["required_services"],
            expected_node_count=fixture["expected_node_count"],
            runtime_statuses=fixture["runtime_statuses"],
            request_id="req_infra_004_repeat",
            ts_ms=1_800_110_000_002,
        )

        for report in (report_a, report_b):
            self.assertEqual("DEGRADED", report["status"])
            self.assertEqual(fixture["expected_reason_code"], report["reason_code"])
            self.assertEqual(1, report["node_probe"]["summary"]["node_down"])
            row = _find_node_row(report["node_probe"]["nodes"], "natsu-node-03")
            self.assertEqual("DOWN", row["node_status"])
            self.assertEqual(INFRA_NODE_PROBE_TIMEOUT, row["reason_code"])
            self.assertEqual(S_INFRA_NODE_PROBE, row["stage"])

        self.assertEqual(report_a["reason_code"], report_b["reason_code"])

    def test_t_infra_01_missing_service_and_role_conflict_are_rejected(self) -> None:
        missing_fixture = _load_json(FIXTURES / "bad" / "node_missing_service.json")
        role_fixture = _load_json(FIXTURES / "bad" / "node_role_conflict.json")

        missing = validate_node_inventory(
            nodes=missing_fixture["nodes"],
            required_services=missing_fixture["required_services"],
            expected_node_count=missing_fixture["expected_node_count"],
        )
        self.assertEqual("FAILED", missing["status"])
        self.assertEqual(INFRA_NODE_MISSING_SERVICE, missing["reason_code"])
        self.assertTrue(any(row["reason_code"] == INFRA_NODE_MISSING_SERVICE for row in missing["issues"]))

        role_conflict = validate_node_inventory(
            nodes=role_fixture["nodes"],
            required_services=role_fixture["required_services"],
            expected_node_count=role_fixture["expected_node_count"],
        )
        self.assertEqual("FAILED", role_conflict["status"])
        self.assertEqual(INFRA_NODE_ROLE_CONFLICT, role_conflict["reason_code"])
        self.assertTrue(any(row["reason_code"] == INFRA_NODE_ROLE_CONFLICT for row in role_conflict["issues"]))


if __name__ == "__main__":
    unittest.main()
