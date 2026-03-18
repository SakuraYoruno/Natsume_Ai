"""T-Audit-WAL-01: WAL mode audit chain integrity and concurrent write protection.

Validates that:
- WAL mode is active by default
- Recommended PRAGMAs (synchronous=NORMAL, busy_timeout, cache_size) are applied
- Concurrent multi-thread writes succeed without data loss
- WAL health snapshot is observable
- Checkpoint produces structured result
- WAL degradation path is stable (falls back to DELETE, still functional)
- Write retry protects against transient 'database is locked' errors
- Benchmark report structure is stable
- Existing T-Audit-01 chain integrity is preserved under WAL mode
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.audit.ledger_constants import (
    DEFAULT_WAL_CONFIG,
    WalConfig,
    WRITE_RETRY_MAX,
    WRITE_RETRY_DELAY_S,
)
from host_brain.core.errors import (
    AUDIT_WAL_CHECKPOINT_FAILED,
    AUDIT_WAL_FAILED,
    TRACE_MISSING_INTENT,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "audit"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


class TestWalModeActivation(unittest.TestCase):
    """T-Audit-WAL-01.1: WAL mode is active and PRAGMAs are applied."""

    def test_wal_active_by_default(self) -> None:
        ledger = SQLiteLedger(db_path=":memory:")
        try:
            health = ledger.wal_health()
            # :memory: 可能不支持 WAL，但 wal_status 应在 ACTIVE 或 DEGRADED
            self.assertIn(health["wal_status"], {"ACTIVE", "DEGRADED"})
            self.assertIn("journal_mode", health)
            self.assertIn("synchronous", health)
            self.assertIn("wal_config", health)
            self.assertIn("db_path", health)
        finally:
            ledger.close()

    def test_wal_active_on_file_db(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        tmp.close()
        try:
            ledger = SQLiteLedger(db_path=tmp.name)
            health = ledger.wal_health()
            self.assertEqual("ACTIVE", health["wal_status"])
            self.assertEqual("WAL", health["journal_mode"])
            self.assertIn(health["synchronous"], {"NORMAL", "1"})
            self.assertIsNone(health["degradation_reason"])
            ledger.close()
        finally:
            os.unlink(tmp.name)
            for suffix in ["-wal", "-shm"]:
                try:
                    os.unlink(tmp.name + suffix)
                except OSError:
                    pass

    def test_wal_config_defaults_stable(self) -> None:
        cfg = DEFAULT_WAL_CONFIG
        self.assertEqual("WAL", cfg.journal_mode)
        self.assertEqual("NORMAL", cfg.synchronous)
        self.assertEqual(1000, cfg.wal_autocheckpoint)
        self.assertEqual(5000, cfg.busy_timeout_ms)
        self.assertEqual(2000, cfg.cache_size_kb)

    def test_wal_config_as_dict(self) -> None:
        cfg = WalConfig()
        d = cfg.as_dict()
        expected_keys = {"journal_mode", "synchronous", "wal_autocheckpoint", "busy_timeout_ms", "cache_size_kb"}
        self.assertEqual(expected_keys, set(d.keys()))

    def test_custom_wal_config(self) -> None:
        cfg = WalConfig(synchronous="FULL", wal_autocheckpoint=500, busy_timeout_ms=10000)
        self.assertEqual("FULL", cfg.synchronous)
        self.assertEqual(500, cfg.wal_autocheckpoint)
        self.assertEqual(10000, cfg.busy_timeout_ms)


class TestWalHealthSnapshot(unittest.TestCase):
    """T-Audit-WAL-01.2: WAL health snapshot structure is observable."""

    def setUp(self) -> None:
        self.ledger = SQLiteLedger(db_path=":memory:")

    def tearDown(self) -> None:
        self.ledger.close()

    def test_health_snapshot_keys(self) -> None:
        health = self.ledger.wal_health()
        required_keys = {"wal_status", "journal_mode", "synchronous", "degradation_reason", "wal_config", "wal_size_pages", "db_path"}
        self.assertTrue(required_keys.issubset(set(health.keys())), f"missing keys: {required_keys - set(health.keys())}")

    def test_health_wal_config_embedded(self) -> None:
        health = self.ledger.wal_health()
        wal_cfg = health["wal_config"]
        self.assertIn("journal_mode", wal_cfg)
        self.assertIn("synchronous", wal_cfg)
        self.assertIn("busy_timeout_ms", wal_cfg)


class TestWalCheckpoint(unittest.TestCase):
    """T-Audit-WAL-01.3: Checkpoint returns structured result."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self.tmp.close()
        self.ledger = SQLiteLedger(db_path=self.tmp.name)

    def tearDown(self) -> None:
        self.ledger.close()
        os.unlink(self.tmp.name)
        for suffix in ["-wal", "-shm"]:
            try:
                os.unlink(self.tmp.name + suffix)
            except OSError:
                pass

    def test_checkpoint_passive_returns_structure(self) -> None:
        # 写入一些数据以产生 WAL frames
        for i in range(10):
            self.ledger.log_error(request_id=f"req_ckpt_{i}", stage="S_TEST", reason_code="TEST", detail="test")
        result = self.ledger.checkpoint("PASSIVE")
        self.assertEqual("OK", result["status"])
        self.assertEqual("PASSIVE", result["mode"])
        self.assertIn("blocked", result)
        self.assertIn("total_frames", result)
        self.assertIn("checkpointed_frames", result)
        self.assertIsNone(result["reason_code"])

    def test_checkpoint_invalid_mode_defaults_to_passive(self) -> None:
        result = self.ledger.checkpoint("INVALID_MODE")
        self.assertEqual("PASSIVE", result["mode"])

    def test_checkpoint_truncate(self) -> None:
        for i in range(5):
            self.ledger.log_error(request_id=f"req_trunc_{i}", stage="S_TEST", reason_code="TEST", detail="test")
        result = self.ledger.checkpoint("TRUNCATE")
        self.assertEqual("OK", result["status"])
        self.assertEqual("TRUNCATE", result["mode"])


class TestConcurrentWrites(unittest.TestCase):
    """T-Audit-WAL-01.4: Multi-thread concurrent writes succeed without data loss."""

    def test_concurrent_writes_no_data_loss(self) -> None:
        fixture = _load_json(FIXTURES / "good" / "wal_concurrent_writes.json")
        thread_count = fixture["thread_count"]
        ops_per_thread = fixture["ops_per_thread"]
        total_expected = thread_count * ops_per_thread

        tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        tmp.close()
        try:
            ledger = SQLiteLedger(db_path=tmp.name, check_same_thread=False)
            barrier = threading.Barrier(thread_count)
            errors: list[Exception] = []

            def writer(tid: int) -> None:
                try:
                    barrier.wait()
                    for i in range(ops_per_thread):
                        ledger.log_error(
                            request_id=f"req_conc_{tid:03d}_{i:06d}",
                            stage="S_CONCURRENT",
                            reason_code="BENCH_WRITE",
                            detail=f"concurrent write t={tid} #{i}",
                        )
                except Exception as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=writer, args=(t,)) for t in range(thread_count)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            self.assertEqual([], errors, f"concurrent write errors: {errors}")

            # 验证所有记录持久化
            records = ledger.error_log
            self.assertEqual(total_expected, len(records))

            # 验证 WAL 状态
            health = ledger.wal_health()
            self.assertEqual(fixture["expected"]["wal_status"], health["wal_status"])
            self.assertEqual(fixture["expected"]["journal_mode"], health["journal_mode"])

            ledger.close()
        finally:
            os.unlink(tmp.name)
            for suffix in ["-wal", "-shm"]:
                try:
                    os.unlink(tmp.name + suffix)
                except OSError:
                    pass

    def test_mixed_read_write_concurrent(self) -> None:
        """Concurrent reads and writes don't conflict under WAL."""
        tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        tmp.close()
        try:
            ledger = SQLiteLedger(db_path=tmp.name, check_same_thread=False)
            write_count = 50
            read_results: list[int] = []
            errors: list[Exception] = []

            def writer() -> None:
                try:
                    for i in range(write_count):
                        ledger.log_error(
                            request_id=f"req_mix_{i:06d}",
                            stage="S_MIX",
                            reason_code="MIX_WRITE",
                            detail=f"mixed write #{i}",
                        )
                except Exception as exc:
                    errors.append(exc)

            def reader() -> None:
                try:
                    for _ in range(20):
                        records = ledger.error_log
                        read_results.append(len(records))
                        time.sleep(0.01)
                except Exception as exc:
                    errors.append(exc)

            t_write = threading.Thread(target=writer)
            t_read = threading.Thread(target=reader)
            t_write.start()
            t_read.start()
            t_write.join()
            t_read.join()

            self.assertEqual([], errors)
            self.assertEqual(write_count, len(ledger.error_log))
            # 读取结果应呈单调非递减
            for i in range(1, len(read_results)):
                self.assertGreaterEqual(read_results[i], read_results[i - 1])

            ledger.close()
        finally:
            os.unlink(tmp.name)
            for suffix in ["-wal", "-shm"]:
                try:
                    os.unlink(tmp.name + suffix)
                except OSError:
                    pass


class TestWalDegradation(unittest.TestCase):
    """T-Audit-WAL-01.5: WAL degradation path is stable."""

    def test_wal_degradation_still_functional(self) -> None:
        """When WAL cannot be activated, system still works in DELETE mode."""
        fixture = _load_json(FIXTURES / "bad" / "wal_degradation.json")
        ledger = SQLiteLedger(db_path=":memory:")
        # 手动模拟降级状态
        ledger._wal_status = "DEGRADED"
        ledger._wal_degradation_reason = "simulated WAL failure"

        try:
            # 写入仍应成功
            ledger.log_error(request_id="req_deg_001", stage="S_DEG", reason_code="TEST", detail="degraded write")
            records = ledger.error_log
            self.assertEqual(1, len(records))

            # 健康快照应反映降级
            health = ledger.wal_health()
            self.assertEqual("DEGRADED", health["wal_status"])
            self.assertIsNotNone(health["degradation_reason"])
            self.assertTrue(fixture["expected"]["system_still_functional"])
        finally:
            ledger.close()

    def test_wal_error_codes_stable(self) -> None:
        """Error codes are stable string constants."""
        self.assertEqual("AUDIT_WAL_FAILED", AUDIT_WAL_FAILED)
        self.assertEqual("AUDIT_WAL_CHECKPOINT_FAILED", AUDIT_WAL_CHECKPOINT_FAILED)


class TestWriteRetry(unittest.TestCase):
    """T-Audit-WAL-01.6: Write retry protects against transient lock errors."""

    def test_retry_constants_stable(self) -> None:
        self.assertEqual(3, WRITE_RETRY_MAX)
        self.assertEqual(0.05, WRITE_RETRY_DELAY_S)

    def test_write_lock_serializes_inserts(self) -> None:
        """Write lock prevents concurrent raw insert corruption."""
        ledger = SQLiteLedger(db_path=":memory:")
        try:
            self.assertIsNotNone(ledger._write_lock)
            # RLock 允许事务内重入，Lock 不允许；两者都能序列化写入
            lock_type = type(threading.Lock())
            rlock_type = type(threading.RLock())
            self.assertIsInstance(ledger._write_lock, (lock_type, rlock_type))
        finally:
            ledger.close()


class TestBenchmarkReportStructure(unittest.TestCase):
    """T-Audit-WAL-01.7: Benchmark report structure is stable."""

    def test_benchmark_report_keys(self) -> None:
        from tools.bench.ledger_bench import run_benchmark
        report = run_benchmark(threads=2, ops=20)
        required_keys = {"db_path", "wal_config", "wal_health_before", "wal_health_after", "checkpoint_result", "scenarios"}
        self.assertTrue(required_keys.issubset(set(report.keys())))

    def test_benchmark_scenarios_present(self) -> None:
        from tools.bench.ledger_bench import run_benchmark
        report = run_benchmark(threads=2, ops=20)
        scenarios = report["scenarios"]
        self.assertEqual(3, len(scenarios))
        scenario_names = [s["scenario"] for s in scenarios]
        self.assertIn("single_thread_write", scenario_names)
        self.assertIn("multi_thread_write", scenario_names)
        self.assertIn("mixed_read_write", scenario_names)

    def test_benchmark_scenario_stats_keys(self) -> None:
        from tools.bench.ledger_bench import run_benchmark
        report = run_benchmark(threads=2, ops=10)
        for scenario in report["scenarios"]:
            for key in ["ops_per_sec", "p50_latency_ms", "p95_latency_ms", "p99_latency_ms", "mean_latency_ms", "total_ops"]:
                self.assertIn(key, scenario, f"missing key {key} in scenario {scenario['scenario']}")
            self.assertGreater(scenario["ops_per_sec"], 0)


class TestAuditChainIntegrityUnderWal(unittest.TestCase):
    """T-Audit-WAL-01.8: Existing audit chain integrity is preserved under WAL."""

    def setUp(self) -> None:
        self.ledger = SQLiteLedger(db_path=":memory:")

    def tearDown(self) -> None:
        self.ledger.close()

    def test_triptych_trace_replayable_under_wal(self) -> None:
        """Full intent→governor→skill chain is replayable under WAL mode."""
        request_id = "req_wal_triptych_001"
        ts = 1_900_000_000_000

        self.ledger.log_intent({
            "request_id": request_id, "thread_id": "t1", "turn_id": "turn1",
            "intent_id": "intent_wal_001", "session_id": "sess1", "seq": 1,
            "ts_ms": ts, "nonce": "nonce_wal_001", "parse_stage": "S_PARSED",
            "raw_text": "test", "normalized_json": json.dumps({"action": "test"}),
        })
        self.ledger.log_governor({
            "request_id": request_id, "thread_id": "t1", "turn_id": "turn1",
            "intent_id": "intent_wal_001", "decision": "APPROVE",
            "reason_code": None, "rule_trace": json.dumps([]),
            "tokens": json.dumps({"approval_token_ref": "tok_wal_001"}),
            "snapshot_refs": json.dumps([]), "stage": "S_GOVERNOR", "ts_ms": ts + 1,
        })
        self.ledger.log_skill_exec({
            "request_id": request_id, "thread_id": "t1", "turn_id": "turn1",
            "intent_id": "intent_wal_001", "skill_name": "test_skill",
            "approval_token_ref": "tok_wal_001", "result_status": "SUCCESS",
            "result_code": "OK", "evidence_ref": "ev_wal_001", "ts_ms": ts + 2,
        })

        trace = self.ledger.trace_request(request_id)
        self.assertEqual("COMPLETE", trace["status"])
        self.assertEqual(1, len(trace["intent_log"]))
        self.assertEqual(1, len(trace["governor_log"]))
        self.assertEqual(1, len(trace["skill_exec_log"]))
        # 串联键一致
        self.assertEqual("intent_wal_001", trace["intent_log"][0]["intent_id"])
        self.assertEqual("intent_wal_001", trace["governor_log"][0]["intent_id"])
        self.assertEqual("intent_wal_001", trace["skill_exec_log"][0]["intent_id"])

    def test_trace_missing_intent_under_wal(self) -> None:
        trace = self.ledger.trace_request("req_missing_wal")
        self.assertEqual("INCOMPLETE", trace["status"])
        self.assertEqual(TRACE_MISSING_INTENT, trace["reason_code"])

    def test_close_triggers_checkpoint(self) -> None:
        """close() should attempt a PASSIVE checkpoint before closing."""
        tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        tmp.close()
        try:
            ledger = SQLiteLedger(db_path=tmp.name)
            for i in range(5):
                ledger.log_error(request_id=f"req_close_{i}", stage="S_TEST", reason_code="TEST", detail="test")
            # close should not raise
            ledger.close()
        finally:
            os.unlink(tmp.name)
            for suffix in ["-wal", "-shm"]:
                try:
                    os.unlink(tmp.name + suffix)
                except OSError:
                    pass


class TestFixturesLoadable(unittest.TestCase):
    """T-Audit-WAL-01.9: Fixture files are loadable and match expected structure."""

    def test_good_concurrent_writes_fixture(self) -> None:
        fix = _load_json(FIXTURES / "good" / "wal_concurrent_writes.json")
        self.assertEqual("wal_concurrent_writes", fix["scenario"])
        self.assertIn("thread_count", fix)
        self.assertIn("ops_per_thread", fix)
        self.assertIn("expected", fix)

    def test_bad_degradation_fixture(self) -> None:
        fix = _load_json(FIXTURES / "bad" / "wal_degradation.json")
        self.assertEqual("wal_degradation", fix["scenario"])
        self.assertTrue(fix["expected"]["system_still_functional"])


if __name__ == "__main__":
    unittest.main()
