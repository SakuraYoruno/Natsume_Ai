import json
import unittest
from pathlib import Path

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import SERVICE_UNAVAILABLE_COLD_START, SERVICE_UNAVAILABLE_EDGE_RPC
from host_brain.core.services import (
    HEALTH_DEGRADED,
    S_SERVICE_HEALTH,
    ServiceHealthReporter,
    build_default_service_registry,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "services"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


class TestServiceHealthContract(unittest.TestCase):
    def test_t_svc_01_unavailable_reason_mapping_output_and_audit_are_stable(self) -> None:
        fixture = _load_json(FIXTURES / "good" / "service_unavailable_matrix.json")

        ledger = SQLiteLedger(db_path=":memory:")
        reporter = ServiceHealthReporter(
            registry=build_default_service_registry(),
            ledger=ledger,
        )

        report_a = reporter.build_report(
            request_id=fixture["request_id"],
            runtime_statuses=fixture["runtime_statuses"],
            ts_ms=1_800_000_300_000,
        )
        report_b = reporter.build_report(
            request_id="req_svc_002",
            runtime_statuses=fixture["runtime_statuses"],
            ts_ms=1_800_000_300_001,
        )

        for report in (report_a, report_b):
            self.assertEqual(S_SERVICE_HEALTH, report["stage"])
            self.assertEqual(HEALTH_DEGRADED, report["summary"]["overall_status"])
            self.assertIn("required_total", report["summary"])
            self.assertIn("required_unavailable", report["summary"])
            self.assertIsInstance(report["services"], list)
            self.assertGreater(len(report["services"]), 0)
            self.assertEqual(report["reason_code"], SERVICE_UNAVAILABLE_COLD_START)
            json.loads(json.dumps(report, ensure_ascii=False))

        def _row(report: dict, service_id: str) -> dict:
            matches = [row for row in report["services"] if row["service_id"] == service_id]
            self.assertEqual(1, len(matches), msg=report["services"])
            return matches[0]

        llm_a = _row(report_a, "host.integrations.llm_gateway")
        llm_b = _row(report_b, "host.integrations.llm_gateway")
        self.assertEqual(SERVICE_UNAVAILABLE_COLD_START, llm_a["reason_code"])
        self.assertEqual(llm_a["reason_code"], llm_b["reason_code"])

        edge_a = _row(report_a, "edge.rpc.gateway")
        edge_b = _row(report_b, "edge.rpc.gateway")
        self.assertEqual(SERVICE_UNAVAILABLE_EDGE_RPC, edge_a["reason_code"])
        self.assertEqual(edge_a["reason_code"], edge_b["reason_code"])

        expected_rows = len(report_a["services"]) + len(report_b["services"])
        self.assertEqual(expected_rows, len(ledger.service_health_log))
        request_ids = {row["request_id"] for row in ledger.service_health_log}
        self.assertEqual({"req_svc_001", "req_svc_002"}, request_ids)
        self.assertTrue(
            any(
                row["request_id"] == "req_svc_001"
                and row["service_id"] == "host.integrations.llm_gateway"
                and row["reason_code"] == SERVICE_UNAVAILABLE_COLD_START
                for row in ledger.service_health_log
            ),
            msg=ledger.service_health_log,
        )

        ledger.close()


if __name__ == "__main__":
    unittest.main()
