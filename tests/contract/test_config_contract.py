import json
import unittest
from pathlib import Path

from edge_client.config import (
    S_EDGE_CONFIG,
    build_edge_runtime_statuses,
    export_edge_config_snapshot,
    load_edge_config,
)
from host_brain.app.config import (
    S_HOST_CONFIG,
    build_host_runtime_statuses,
    export_host_config_snapshot,
    load_host_config,
)
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.config_snapshot import (
    S_CONFIG_SNAPSHOT,
    build_config_snapshot_audit_row,
    diff_config_snapshots,
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

    def test_t_config_01_host_prod_missing_deepseek_key_rejected(self) -> None:
        result = load_host_config(
            env={
                "Nastume_ENV": "prod",
                "HOST_LLM_PROVIDER": "deepseek",
                "HOST_COLD_START_ALLOWED": "false",
            }
        )
        self.assertFalse(result.success)
        self.assertEqual(S_HOST_CONFIG, result.stage)
        self.assertEqual(CONFIG_REQUIRED_MISSING, result.reason_code)
        self.assertTrue(any(issue.field == "DEEPSEEK_API_KEY" for issue in result.issues))

    def test_t_config_01_host_prod_missing_deepseek_or_gemini_key_rejected(self) -> None:
        for provider, expected_field in (("deepseek", "DEEPSEEK_API_KEY"), ("gemini", "GEMINI_API_KEY")):
            result = load_host_config(
                env={
                    "Nastume_ENV": "prod",
                    "HOST_LLM_PROVIDER": provider,
                    "HOST_COLD_START_ALLOWED": "false",
                }
            )
            self.assertFalse(result.success)
            self.assertEqual(S_HOST_CONFIG, result.stage)
            self.assertEqual(CONFIG_REQUIRED_MISSING, result.reason_code)
            self.assertTrue(any(issue.field == expected_field for issue in result.issues))

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

    def test_t_config_02_snapshot_export_is_redacted_and_digest_stable(self) -> None:
        host_fixture = _load_json(FIXTURES / "good" / "host_prod_with_key_env.json")
        edge_fixture = _load_json(FIXTURES / "good" / "edge_prod_with_hmac_env.json")

        host_a = export_host_config_snapshot(env=host_fixture["env"])
        host_b = export_host_config_snapshot(env=host_fixture["env"])
        edge_a = export_edge_config_snapshot(env=edge_fixture["env"])
        edge_b = export_edge_config_snapshot(env=edge_fixture["env"])

        self.assertTrue(host_a.success, msg=host_a.as_dict())
        self.assertTrue(host_b.success, msg=host_b.as_dict())
        self.assertTrue(edge_a.success, msg=edge_a.as_dict())
        self.assertTrue(edge_b.success, msg=edge_b.as_dict())
        assert host_a.snapshot is not None
        assert host_b.snapshot is not None
        assert edge_a.snapshot is not None
        assert edge_b.snapshot is not None

        self.assertEqual(S_CONFIG_SNAPSHOT, host_a.stage)
        self.assertEqual("host", host_a.snapshot["component"])
        self.assertEqual("prod", host_a.snapshot["env"])
        self.assertEqual(["OPENAI_API_KEY"], host_a.snapshot["redacted_keys"])
        self.assertEqual("<redacted>", host_a.snapshot["values"]["openai_api_key"])
        self.assertEqual("local_overrides", export_host_config_snapshot(
            env=host_fixture["env"],
            local_overrides={"HOST_LLM_PROVIDER": "mock"},
        ).snapshot["override_sources"]["llm_provider"])
        self.assertEqual(host_a.snapshot["digest"], host_b.snapshot["digest"])

        self.assertEqual(S_CONFIG_SNAPSHOT, edge_a.stage)
        self.assertEqual("edge", edge_a.snapshot["component"])
        self.assertEqual("prod", edge_a.snapshot["env"])
        self.assertEqual(["EDGE_HMAC_SHARED_KEY"], edge_a.snapshot["redacted_keys"])
        self.assertEqual("<redacted>", edge_a.snapshot["values"]["hmac_shared_key"])
        self.assertEqual(edge_a.snapshot["digest"], edge_b.snapshot["digest"])

    def test_t_config_02_snapshot_diff_and_audit_log_are_replayable(self) -> None:
        host_dev = _load_json(FIXTURES / "good" / "host_dev_no_key_env.json")
        host_prod = _load_json(FIXTURES / "good" / "host_prod_with_key_env.json")
        expected = _load_json(FIXTURES / "good" / "host_snapshot_expected_diff.json")

        before_result = export_host_config_snapshot(env=host_dev["env"])
        after_result = export_host_config_snapshot(env=host_prod["env"])
        self.assertTrue(before_result.success, msg=before_result.as_dict())
        self.assertTrue(after_result.success, msg=after_result.as_dict())
        assert before_result.snapshot is not None
        assert after_result.snapshot is not None

        diff = diff_config_snapshots(
            before_snapshot=before_result.snapshot,
            after_snapshot=after_result.snapshot,
        )
        self.assertEqual(S_CONFIG_SNAPSHOT, diff["stage"])
        self.assertEqual(expected["changed_keys"], [row["key"] for row in diff["changed"]])
        self.assertEqual([], diff["added"])
        self.assertEqual([], diff["removed"])
        self.assertEqual(len(expected["changed_keys"]), diff["summary"]["changed_count"])

        ledger = SQLiteLedger(db_path=":memory:")
        try:
            row = build_config_snapshot_audit_row(
                request_id="req_config_snapshot_001",
                snapshot=after_result.snapshot,
                diff=diff,
                detail="prod snapshot exported",
                ts_ms=1_800_070_000_000,
            )
            ledger.log_config_snapshot(row)
            self.assertEqual(1, len(ledger.config_snapshot_log))
            log_row = ledger.config_snapshot_log[0]
            self.assertEqual("req_config_snapshot_001", log_row["request_id"])
            self.assertEqual("host", log_row["component"])
            self.assertEqual("prod", log_row["env"])
            self.assertEqual(S_CONFIG_SNAPSHOT, log_row["stage"])
            self.assertEqual(after_result.snapshot["digest"], log_row["digest"])
            self.assertEqual(["OPENAI_API_KEY"], log_row["redacted_keys"])
            self.assertEqual(diff["digest"], log_row["diff_json"]["digest"])
            self.assertEqual(expected["changed_keys"], [row["key"] for row in log_row["diff_json"]["changed"]])
            json.loads(json.dumps(log_row, ensure_ascii=False))
        finally:
            ledger.close()


if __name__ == "__main__":
    unittest.main()
