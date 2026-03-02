import json
import tempfile
import unittest
from pathlib import Path

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.bond.migration_runner import BondMigrationRunner, S_MIGRATION_BOND
from host_brain.bond.neo4j_client import (
    NEO4J_STATUS_DOWN,
    NEO4J_STATUS_UP,
    S_NEO4J_CONFIG,
    S_NEO4J_PROBE,
    build_neo4j_dependency_status,
    build_neo4j_probe_report,
    load_neo4j_runtime_config,
)
from host_brain.core.errors import (
    CONFIG_REQUIRED_MISSING,
    NEO4J_AUTH_FAILED,
    NEO4J_LEADER_UNAVAILABLE,
    NEO4J_SCHEMA_DRIFT,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "bond" / "neo4j"
MIGRATION_FIXTURES = ROOT / "tests" / "fixtures" / "migrations" / "bond" / "good"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TestNeo4jDeployContract(unittest.TestCase):
    def test_t_neo4j_01_runtime_config_is_parseable(self) -> None:
        fixture = _load_json(FIXTURES / "good" / "runtime_config.json")
        result = load_neo4j_runtime_config(env=fixture["env"])
        self.assertTrue(result.success, msg=result.as_dict())
        self.assertEqual(S_NEO4J_CONFIG, result.stage)
        assert result.config is not None
        self.assertTrue(result.config.enabled)
        self.assertEqual("neo4j+s://bond-cluster.Nastume.ai:7687", result.config.uri)
        self.assertEqual("bond", result.config.database)
        self.assertEqual("sec://neo4j/bond-admin", result.config.auth_ref)
        self.assertEqual(64, result.config.pool_size)
        self.assertTrue(result.config.tls_enabled)

    def test_t_neo4j_01_missing_auth_ref_rejected(self) -> None:
        fixture = _load_json(FIXTURES / "bad" / "runtime_missing_auth_ref.json")
        result = load_neo4j_runtime_config(env=fixture["env"])
        self.assertFalse(result.success)
        self.assertEqual(S_NEO4J_CONFIG, result.stage)
        self.assertEqual(CONFIG_REQUIRED_MISSING, result.reason_code)
        self.assertTrue(any(issue.field == "BOND_NEO4J_AUTH_REF" for issue in result.issues))

    def test_t_neo4j_01_probe_failure_reason_mapping_is_stable(self) -> None:
        config = load_neo4j_runtime_config(env=_load_json(FIXTURES / "good" / "runtime_config.json")["env"]).config
        assert config is not None
        auth_fixture = _load_json(FIXTURES / "bad" / "probe_auth_failed.json")
        schema_fixture = _load_json(FIXTURES / "bad" / "probe_schema_drift.json")
        leader_fixture = _load_json(FIXTURES / "bad" / "probe_leader_unavailable.json")

        auth = build_neo4j_probe_report(
            config=config,
            request_id="req_neo4j_001",
            probe=auth_fixture["probe"],
            ts_ms=1_800_130_000_000,
        )
        schema = build_neo4j_probe_report(
            config=config,
            request_id="req_neo4j_002",
            probe=schema_fixture["probe"],
            ts_ms=1_800_130_000_001,
        )
        leader = build_neo4j_probe_report(
            config=config,
            request_id="req_neo4j_003",
            probe=leader_fixture["probe"],
            ts_ms=1_800_130_000_002,
        )

        self.assertEqual(S_NEO4J_PROBE, auth["stage"])
        self.assertEqual(NEO4J_STATUS_DOWN, auth["status"])
        self.assertEqual(NEO4J_AUTH_FAILED, auth["reason_code"])
        self.assertEqual("auth_failed", auth["failure_class"])

        self.assertEqual(NEO4J_STATUS_DOWN, schema["status"])
        self.assertEqual(NEO4J_SCHEMA_DRIFT, schema["reason_code"])
        self.assertEqual("schema_drift", schema["failure_class"])

        self.assertEqual(NEO4J_STATUS_DOWN, leader["status"])
        self.assertEqual(NEO4J_LEADER_UNAVAILABLE, leader["reason_code"])
        self.assertEqual("leader_unavailable", leader["failure_class"])

    def test_t_neo4j_01_probe_to_migration_audit_linkage(self) -> None:
        config = load_neo4j_runtime_config(env=_load_json(FIXTURES / "good" / "runtime_config.json")["env"]).config
        assert config is not None
        probe = build_neo4j_probe_report(
            config=config,
            request_id="req_neo4j_004",
            probe=_load_json(FIXTURES / "bad" / "probe_auth_failed.json")["probe"],
            ts_ms=1_800_130_000_010,
        )
        self.assertEqual(NEO4J_AUTH_FAILED, probe["reason_code"])
        dep_status = build_neo4j_dependency_status(probe)
        self.assertFalse(dep_status["available"])
        self.assertEqual("dependency", dep_status["unavailable_kind"])

        ledger = SQLiteLedger(db_path=":memory:")
        try:
            runner = BondMigrationRunner(migrations_dir=MIGRATION_FIXTURES, ledger=ledger)
            result = runner.apply_migrations_with_probe(
                probe_report=probe,
                request_id="req_neo4j_004",
                ts_ms=1_800_130_000_011,
            )
            self.assertFalse(result.success)
            self.assertEqual(S_MIGRATION_BOND, result.stage)
            self.assertEqual(NEO4J_AUTH_FAILED, result.reason_code)
            self.assertTrue(any(row["migration_id"] == "neo4j_probe" for row in runner.read_migration_log()))
            self.assertTrue(
                any(
                    row["request_id"] == "req_neo4j_004"
                    and row["migration_id"] == "neo4j_probe"
                    and row["reason_code"] == NEO4J_AUTH_FAILED
                    for row in ledger.migration_log
                ),
                msg=ledger.migration_log,
            )
        finally:
            ledger.close()

    def test_t_neo4j_01_up_probe_allows_migration_runner(self) -> None:
        config = load_neo4j_runtime_config(env=_load_json(FIXTURES / "good" / "runtime_config.json")["env"]).config
        assert config is not None
        probe = build_neo4j_probe_report(
            config=config,
            request_id="req_neo4j_005",
            probe={"reachable": True, "auth_ok": True, "schema_ok": True, "leader_ok": True},
            ts_ms=1_800_130_000_020,
        )
        self.assertEqual(NEO4J_STATUS_UP, probe["status"])

        with tempfile.TemporaryDirectory(prefix="neo4j_mig_") as tmp:
            db_path = Path(tmp) / "audit.sqlite3"
            ledger = SQLiteLedger(db_path=str(db_path))
            try:
                runner = BondMigrationRunner(migrations_dir=MIGRATION_FIXTURES, ledger=ledger)
                result = runner.apply_migrations_with_probe(
                    probe_report=probe,
                    request_id="req_neo4j_005",
                    ts_ms=1_800_130_000_021,
                )
                self.assertTrue(result.success)
                self.assertIsNone(result.reason_code)
            finally:
                ledger.close()


if __name__ == "__main__":
    unittest.main()
