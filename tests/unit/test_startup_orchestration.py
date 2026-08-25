"""M43 启动编排收口测试（T-MIG-VER / T-MIG-GUARD / T-SNAP / T-REPLAY）。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from host_brain.app.startup_snapshot import (
    StartupHealthSnapshot,
    build_startup_snapshot,
    snapshot_to_dict,
    verify_snapshot_hash,
)
from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.core.errors import MIGRATION_ASSET_MISSING, MIGRATION_SCHEMA_DRIFT
from host_brain.core.ids import now_ms
from tools.replay.replay_report import (
    ReplayReport,
    build_replay_report,
    format_replay_report,
)


# ===========================================================================
# 辅助工厂
# ===========================================================================

def _make_migrations_dir(
    versions: list[int],
    *,
    duplicate: int | None = None,
) -> Path:
    """在临时目录中创建迁移文件，用于测试。"""
    tmp = Path(tempfile.mkdtemp())
    for v in versions:
        sql_file = tmp / f"{v:04d}_test_migration.sql"
        sql_file.write_text(f"-- Migration {v:04d}\nCREATE TABLE IF NOT EXISTS t{v} (id INTEGER);", encoding="utf-8-sig")
    if duplicate is not None:
        # 创建重复版本号的文件
        dup_file = tmp / f"{duplicate:04d}_duplicate_migration.sql"
        dup_file.write_text(f"-- Duplicate Migration {duplicate:04d}\nCREATE TABLE IF NOT EXISTS dup{duplicate} (id INTEGER);", encoding="utf-8-sig")
    return tmp


def _make_startup_report(boot_status: str = "READY") -> dict:
    return {
        "request_id": "req::test::001",
        "stage": "S_SERVICE_BOOTSTRAP",
        "reason_code": None,
        "summary": {
            "boot_status": boot_status,
            "issue_count": 0,
            "probe_total": 0,
            "probe_unavailable": 0,
            "required_total": 8,
            "required_unavailable": 0,
            "optional_unavailable": 0,
        },
        "startup_issues": [],
        "dependency_probes": [],
        "services": [],
        "ts_ms": now_ms(),
    }


def _make_observability_report(
    request_id: str = "req::test::001",
    trace_status: str = "APPROVE",
    timeline_total: int = 5,
    include_reject: bool = False,
    include_error: bool = False,
) -> dict:
    timeline = [
        {
            "event_id": "intent_log:1",
            "source": "intent_log",
            "stage": None,
            "status": "APPROVE",
            "reason_code": None,
            "intent_id": "intent::test::001",
            "ts_ms": now_ms() - 1000,
            "detail": "SKILL_INTENT",
        },
        {
            "event_id": "governor_log:1",
            "source": "governor_log",
            "stage": None,
            "status": "APPROVE" if not include_reject else "REJECT",
            "reason_code": None if not include_reject else "TRUST_LEVEL_TOO_LOW",
            "intent_id": "intent::test::001",
            "ts_ms": now_ms() - 800,
            "detail": "GOV_OK_RENDER",
        },
        {
            "event_id": "skill_exec_log:1",
            "source": "skill_exec_log",
            "stage": None,
            "status": "SUCCESS",
            "reason_code": None,
            "intent_id": "intent::test::001",
            "ts_ms": now_ms() - 500,
            "detail": "SKILL_EXECUTED",
        },
    ]
    if include_error:
        timeline.append({
            "event_id": "error_log:1",
            "source": "error_log",
            "stage": None,
            "status": "ERROR",
            "reason_code": "UNEXPECTED_EXCEPTION",
            "intent_id": "intent::test::001",
            "ts_ms": now_ms() - 200,
            "detail": "unexpected error",
        })
    return {
        "request_id": request_id,
        "trace_status": trace_status,
        "trace_stage": None,
        "trace_reason_code": None,
        "timeline_total": len(timeline),
        "sources": {row["source"]: 1 for row in timeline},
        "timeline": timeline,
    }


# ===========================================================================
# T-MIG-RUNNER-01~05：SQLiteMigrationRunner 目录边界
# ===========================================================================

class TestSQLiteMigrationRunnerCatalogM43(unittest.TestCase):
    """验证现行 SQLiteMigrationRunner 直接承担迁移目录守卫职责。"""

    def _run(self, migrations_dir: Path):
        return SQLiteMigrationRunner(
            db_path=migrations_dir.parent / f"{migrations_dir.name}.sqlite3",
            migrations_dir=migrations_dir,
        ).apply_migrations(request_id="req::migration-runner")

    def test_continuous_versions_apply_in_sorted_order(self) -> None:
        tmp = _make_migrations_dir([1, 2, 3, 4, 5])
        result = self._run(tmp)
        self.assertTrue(result.success, result.as_dict())
        self.assertEqual(5, result.summary["applied_count"])
        self.assertEqual(5, result.summary["current_version"])
        self.assertEqual(
            ["0001_test_migration", "0002_test_migration", "0003_test_migration",
             "0004_test_migration", "0005_test_migration"],
            [record.migration_id for record in result.records],
        )

    def test_runner_normalizes_physical_file_order(self) -> None:
        tmp = _make_migrations_dir([3, 1, 5, 2, 4])
        result = self._run(tmp)
        self.assertTrue(result.success, result.as_dict())
        self.assertEqual(
            [1, 2, 3, 4, 5],
            [int(record.migration_id[:4]) for record in result.records],
        )

    def test_duplicate_version_is_schema_drift(self) -> None:
        tmp = _make_migrations_dir([1, 2, 3], duplicate=2)
        result = self._run(tmp)
        self.assertFalse(result.success)
        self.assertEqual(MIGRATION_SCHEMA_DRIFT, result.reason_code)
        self.assertIn("duplicate migration versions", result.detail)

    def test_version_gap_is_missing_asset(self) -> None:
        tmp = _make_migrations_dir([1, 2, 5])
        result = self._run(tmp)
        self.assertFalse(result.success)
        self.assertEqual(MIGRATION_ASSET_MISSING, result.reason_code)
        self.assertIn("missing [3, 4]", result.detail)

    def test_missing_dir_is_missing_asset(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        result = SQLiteMigrationRunner(
            db_path=tmp / "runner.sqlite3",
            migrations_dir=tmp / "missing-migrations",
        ).apply_migrations(request_id="req::missing-migrations")
        self.assertFalse(result.success)
        self.assertEqual(MIGRATION_ASSET_MISSING, result.reason_code)


# ===========================================================================
# T-SNAP-01~06：StartupHealthSnapshot
# ===========================================================================

class TestStartupHealthSnapshotM43(unittest.TestCase):
    """T-SNAP-01~06：验证 StartupHealthSnapshot 不可变快照。"""

    def test_build_snapshot_returns_instance(self) -> None:
        """T-SNAP-01：build_startup_snapshot() 返回 StartupHealthSnapshot 实例。"""
        report = _make_startup_report()
        snapshot = build_startup_snapshot(report)
        self.assertIsInstance(snapshot, StartupHealthSnapshot)

    def test_boot_status_matches_report(self) -> None:
        """T-SNAP-02：snapshot.boot_status 与 startup_report["summary"]["boot_status"] 一致。"""
        report = _make_startup_report(boot_status="READY")
        snapshot = build_startup_snapshot(report)
        self.assertEqual("READY", snapshot.boot_status)

        report_degraded = _make_startup_report(boot_status="DEGRADED")
        snapshot_degraded = build_startup_snapshot(report_degraded)
        self.assertEqual("DEGRADED", snapshot_degraded.boot_status)

    def test_snapshot_hash_is_sha256_of_content(self) -> None:
        """T-SNAP-03：snapshot.snapshot_hash 是内容的 SHA256（不含 snapshot_hash 字段本身）。"""
        report = _make_startup_report()
        snapshot = build_startup_snapshot(report)
        self.assertTrue(verify_snapshot_hash(snapshot))

    def test_snapshot_is_frozen(self) -> None:
        """T-SNAP-04：StartupHealthSnapshot 是不可变的（frozen=True），直接赋值抛出 FrozenInstanceError。"""
        from dataclasses import FrozenInstanceError
        report = _make_startup_report()
        snapshot = build_startup_snapshot(report)
        with self.assertRaises(FrozenInstanceError):
            snapshot.boot_status = "HACKED"  # type: ignore[misc]

    def test_snapshot_contains_only_live_fields(self) -> None:
        """T-SNAP-05：快照不再复制已退役的迁移摘要字段。"""
        report = _make_startup_report()
        snapshot = build_startup_snapshot(report)
        self.assertNotIn("migration_guard_report", snapshot_to_dict(snapshot))
        self.assertNotIn("bond_migration_summary", snapshot_to_dict(snapshot))
        self.assertIsNotNone(snapshot.snapshot_hash)

    def test_snapshot_to_dict_contains_all_fields(self) -> None:
        """T-SNAP-06：snapshot_to_dict() 包含所有字段，且 snapshot_hash 可验证。"""
        report = _make_startup_report()
        snapshot = build_startup_snapshot(report)
        d = snapshot_to_dict(snapshot)
        for field in ("request_id", "ts_ms", "boot_status", "service_summary",
                      "snapshot_hash"):
            self.assertIn(field, d)
        # 验证哈希
        self.assertTrue(verify_snapshot_hash(snapshot))

# ===========================================================================
# T-REPLAY-01~06：ReplayReport
# ===========================================================================

class TestReplayReportM43(unittest.TestCase):
    """T-REPLAY-01~06：验证 ReplayReport 单键回放报告。"""

    def test_build_replay_report_returns_instance(self) -> None:
        """T-REPLAY-01：build_replay_report() 返回 ReplayReport 实例。"""
        obs_report = _make_observability_report()
        report = build_replay_report(obs_report)
        self.assertIsInstance(report, ReplayReport)

    def test_timeline_total_matches(self) -> None:
        """T-REPLAY-02：report.timeline_total 与 observability_report["timeline_total"] 一致。"""
        obs_report = _make_observability_report(timeline_total=5)
        report = build_replay_report(obs_report)
        self.assertEqual(obs_report["timeline_total"], report.timeline_total)

    def test_key_events_contains_intent_and_governor(self) -> None:
        """T-REPLAY-03：report.key_events 包含 intent_log 和 governor_log 的关键事件。"""
        obs_report = _make_observability_report()
        report = build_replay_report(obs_report)
        sources = {e["source"] for e in report.key_events}
        self.assertIn("intent_log", sources)
        self.assertIn("governor_log", sources)

    def test_anomalies_contains_reject_and_error(self) -> None:
        """T-REPLAY-04：report.anomalies 包含 REJECT 决策和 error_log 中的异常。"""
        obs_report = _make_observability_report(include_reject=True, include_error=True)
        report = build_replay_report(obs_report)
        anomaly_sources = {a["source"] for a in report.anomalies}
        self.assertIn("governor_log", anomaly_sources)  # REJECT 决策
        self.assertIn("error_log", anomaly_sources)  # error_log 异常

    def test_format_replay_report_contains_request_id(self) -> None:
        """T-REPLAY-05：format_replay_report() 返回包含 request_id 和时间线摘要的字符串。"""
        obs_report = _make_observability_report(request_id="req::format::test")
        report = build_replay_report(obs_report)
        formatted = format_replay_report(report)
        self.assertIn("req::format::test", formatted)
        self.assertIsInstance(formatted, str)
        self.assertGreater(len(formatted), 50)

    def test_report_hash_is_sha256_of_content(self) -> None:
        """T-REPLAY-06：report.report_hash 是报告内容的 SHA256（不含 report_hash 字段本身）。"""
        obs_report = _make_observability_report()
        report = build_replay_report(obs_report)
        # 重新计算哈希验证
        import hashlib
        import json
        content = {
            "request_id": report.request_id,
            "ts_ms": report.ts_ms,
            "trace_status": report.trace_status,
            "timeline_total": report.timeline_total,
            "key_events": report.key_events,
            "anomalies": report.anomalies,
            "source_summary": report.source_summary,
        }
        expected_hash = hashlib.sha256(
            json.dumps(content, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        self.assertEqual(expected_hash, report.report_hash)

    def test_no_anomalies_when_all_approved(self) -> None:
        """无异常时，report.anomalies 为空列表。"""
        obs_report = _make_observability_report(include_reject=False, include_error=False)
        report = build_replay_report(obs_report)
        self.assertEqual([], report.anomalies)

    def test_format_report_shows_no_anomalies(self) -> None:
        """无异常时，format_replay_report 显示'无异常事件'。"""
        obs_report = _make_observability_report(include_reject=False, include_error=False)
        report = build_replay_report(obs_report)
        formatted = format_replay_report(report)
        self.assertIn("无异常", formatted)


if __name__ == "__main__":
    unittest.main()
