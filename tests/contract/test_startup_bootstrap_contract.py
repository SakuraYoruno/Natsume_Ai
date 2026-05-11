import json
import unittest
from pathlib import Path

from host_brain.app.main import build_startup_report
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    CONFIG_ENV_INVALID,
    SERVICE_UNAVAILABLE_COLD_START,
    SERVICE_UNAVAILABLE_DEPENDENCY,
    SERVICE_UNAVAILABLE_EDGE_RPC,
)
from host_brain.core.services import BOOT_DEGRADED, S_SERVICE_BOOTSTRAP, S_SERVICE_HEALTH


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "services"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _pick_service(report: dict, service_id: str) -> dict:
    rows = [row for row in report["services"] if row["service_id"] == service_id]
    if len(rows) != 1:
        raise AssertionError(f"service row not unique: {service_id}, rows={rows}")
    return rows[0]


class TestStartupBootstrapContract(unittest.TestCase):
    def test_t_boot_01_probe_matrix_and_boot_snapshot_are_deterministic(self) -> None:
        fixture = _load_json(FIXTURES / "good" / "startup_probe_matrix.json")
        ledger = SQLiteLedger(db_path=":memory:")

        report = build_startup_report(
            request_id=fixture["request_id"],
            host_env=fixture["host_env"],
            edge_env=fixture["edge_env"],
            dependency_probes=fixture["dependency_probes"],
            ledger=ledger,
            ts_ms=1_800_030_000_000,
        )

        self.assertEqual(S_SERVICE_BOOTSTRAP, report["stage"])
        self.assertEqual(BOOT_DEGRADED, report["summary"]["boot_status"])
        self.assertEqual(0, report["summary"]["issue_count"])
        self.assertEqual(2, report["summary"]["probe_total"])
        self.assertEqual(2, report["summary"]["probe_unavailable"])
        self.assertEqual([], report["startup_issues"])
        self.assertEqual(2, len(report["dependency_probes"]))

        llm_row = _pick_service(report, "host.integrations.llm_gateway")
        self.assertEqual(SERVICE_UNAVAILABLE_COLD_START, llm_row["reason_code"])
        self.assertEqual(S_SERVICE_HEALTH, llm_row["stage"])

        edge_row = _pick_service(report, "edge.rpc.gateway")
        self.assertEqual(SERVICE_UNAVAILABLE_EDGE_RPC, edge_row["reason_code"])
        self.assertEqual(S_SERVICE_HEALTH, edge_row["stage"])

        retr_row = _pick_service(report, "host.retrieval.recall_fusion")
        self.assertEqual(SERVICE_UNAVAILABLE_DEPENDENCY, retr_row["reason_code"])
        self.assertEqual(S_SERVICE_HEALTH, retr_row["stage"])

        for row in report["services"]:
            self.assertIn("service_id", row)
            self.assertIn("owner", row)
            self.assertIn("required", row)
            self.assertIn("status", row)
            self.assertIn("reason_code", row)
            self.assertIn("stage", row)

        self.assertEqual(len(report["services"]), len(ledger.service_health_log))
        self.assertEqual({fixture["request_id"]}, {row["request_id"] for row in ledger.service_health_log})
        json.loads(json.dumps(report, ensure_ascii=False))

        ledger.close()

    def test_t_boot_01_invalid_host_config_is_stable_and_auditable(self) -> None:
        fixture = _load_json(FIXTURES / "bad" / "startup_invalid_host_config.json")
        ledger = SQLiteLedger(db_path=":memory:")

        report = build_startup_report(
            request_id=fixture["request_id"],
            host_env=fixture["host_env"],
            edge_env=fixture["edge_env"],
            dependency_probes=fixture["dependency_probes"],
            ledger=ledger,
            ts_ms=1_800_030_000_001,
        )

        self.assertEqual(S_SERVICE_BOOTSTRAP, report["stage"])
        self.assertEqual(CONFIG_ENV_INVALID, report["reason_code"])
        self.assertEqual(BOOT_DEGRADED, report["summary"]["boot_status"])
        self.assertEqual(1, report["summary"]["issue_count"])
        self.assertEqual("host_config", report["startup_issues"][0]["scope"])
        self.assertEqual(CONFIG_ENV_INVALID, report["startup_issues"][0]["reason_code"])

        host_audit = _pick_service(report, "host.audit.sqlite_ledger")
        self.assertEqual(SERVICE_UNAVAILABLE_DEPENDENCY, host_audit["reason_code"])
        self.assertEqual(S_SERVICE_HEALTH, host_audit["stage"])

        self.assertEqual(len(report["services"]), len(ledger.service_health_log))
        self.assertTrue(
            any(
                row["request_id"] == fixture["request_id"]
                and row["service_id"] == "host.audit.sqlite_ledger"
                and row["reason_code"] == SERVICE_UNAVAILABLE_DEPENDENCY
                for row in ledger.service_health_log
            ),
            msg=ledger.service_health_log,
        )
        json.loads(json.dumps(report, ensure_ascii=False))

        ledger.close()


if __name__ == "__main__":
    unittest.main()
