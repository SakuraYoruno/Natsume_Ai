"""OPT-9 二期 named queries 单测.

覆盖新增的 3 条 named queries：
  T-OPT9-10：governor_decisions —— 按 decision × reason_code 聚合 governor_log。
  T-OPT9-11：skill_execution_stats —— 按 skill_name × result_status × result_code 聚合 skill_exec_log。
  T-OPT9-12：error_summary —— 按 stage × reason_code 聚合 error_log。

每个测试：
  1) 在内存 sqlite 上建最小 fixture 表（字段与 host_brain/audit/ledger_schema.py 一致）；
  2) 插入若干行；
  3) 以 `ObsQueryBuilder.run_named(...)` 执行；
  4) 断言窗口过滤、分组聚合、排序、LIMIT 行为都正确。
"""

from __future__ import annotations

import sqlite3
import unittest

from host_brain.observability.query_builder import ObsQueryBuilder


def _dict_row_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


class TestGovernorDecisionsQuery(unittest.TestCase):
    def _make_conn(self) -> sqlite3.Connection:
        conn = _dict_row_conn()
        conn.execute(
            """
            CREATE TABLE governor_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT,
                thread_id TEXT,
                turn_id TEXT,
                intent_id TEXT,
                decision TEXT,
                reason_code TEXT,
                rule_trace TEXT,
                tokens TEXT,
                snapshot_refs TEXT,
                stage TEXT,
                ts_ms INTEGER
            )
            """
        )
        rows = [
            ("APPROVE", None, 1000),
            ("APPROVE", None, 1100),
            ("REJECT", "RATE_LIMITED", 1200),
            ("REJECT", "RATE_LIMITED", 1300),
            ("REJECT", "SAFETY_BLOCKED", 1400),
            ("APPROVE", None, 99999),  # out of window
        ]
        conn.executemany(
            "INSERT INTO governor_log (decision, reason_code, ts_ms) VALUES (?, ?, ?)",
            rows,
        )
        conn.commit()
        return conn

    def test_t_opt9_10_governor_decisions_aggregation(self) -> None:
        conn = self._make_conn()
        try:
            builder = ObsQueryBuilder()
            result = builder.run_named(
                conn,
                "governor_decisions",
                start=1000,
                end=2000,
                limit=10,
            )
            self.assertIsInstance(result, list)
            # 窗口内：APPROVE/'' ×2、REJECT/RATE_LIMITED ×2、REJECT/SAFETY_BLOCKED ×1
            key_to_count = {
                (row["decision"], row["reason_code"]): row["decision_count"]
                for row in result
            }
            self.assertEqual(key_to_count.get(("APPROVE", "")), 2)
            self.assertEqual(key_to_count.get(("REJECT", "RATE_LIMITED")), 2)
            self.assertEqual(key_to_count.get(("REJECT", "SAFETY_BLOCKED")), 1)
            self.assertNotIn(("APPROVE", "OUT_OF_WINDOW"), key_to_count)
            # 排序：decision_count DESC
            counts = [row["decision_count"] for row in result]
            self.assertEqual(counts, sorted(counts, reverse=True))
        finally:
            conn.close()

    def test_t_opt9_10b_limit_enforced(self) -> None:
        conn = self._make_conn()
        try:
            builder = ObsQueryBuilder()
            result = builder.run_named(
                conn, "governor_decisions", start=0, end=10_000, limit=1
            )
            self.assertEqual(len(result), 1)
        finally:
            conn.close()


class TestSkillExecutionStatsQuery(unittest.TestCase):
    def _make_conn(self) -> sqlite3.Connection:
        conn = _dict_row_conn()
        conn.execute(
            """
            CREATE TABLE skill_exec_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT,
                thread_id TEXT,
                turn_id TEXT,
                intent_id TEXT,
                event_id TEXT,
                skill_name TEXT,
                approval_token_ref TEXT,
                confirm_token_ref TEXT,
                undo_token_ref TEXT,
                result_status TEXT,
                result_code TEXT,
                result_digest TEXT,
                evidence_ref TEXT,
                manifest_ref TEXT,
                ts_ms INTEGER
            )
            """
        )
        rows = [
            ("translate", "OK", None, 1000),
            ("translate", "OK", None, 1100),
            ("translate", "FAILED", "LLM_UNAVAILABLE", 1200),
            ("desktop_note", "OK", None, 1300),
            ("desktop_note", "DEGRADED", "CTX_COMPRESS_LLM_UNAVAILABLE", 1400),
            ("translate", "OK", None, 99999),  # out of window
        ]
        conn.executemany(
            """
            INSERT INTO skill_exec_log
                (skill_name, result_status, result_code, ts_ms)
            VALUES (?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        return conn

    def test_t_opt9_11_skill_execution_aggregation(self) -> None:
        conn = self._make_conn()
        try:
            builder = ObsQueryBuilder()
            result = builder.run_named(
                conn,
                "skill_execution_stats",
                start=1000,
                end=2000,
                limit=20,
            )
            self.assertIsInstance(result, list)
            key_to_count = {
                (row["skill_name"], row["result_status"], row["result_code"]): row["exec_count"]
                for row in result
            }
            self.assertEqual(key_to_count.get(("translate", "OK", "")), 2)
            self.assertEqual(key_to_count.get(("translate", "FAILED", "LLM_UNAVAILABLE")), 1)
            self.assertEqual(key_to_count.get(("desktop_note", "OK", "")), 1)
            self.assertEqual(
                key_to_count.get(("desktop_note", "DEGRADED", "CTX_COMPRESS_LLM_UNAVAILABLE")),
                1,
            )
            # 窗口外的一条不应出现
            self.assertNotIn(("translate", "OUT_OF_WINDOW", ""), key_to_count)
        finally:
            conn.close()


class TestErrorSummaryQuery(unittest.TestCase):
    def _make_conn(self) -> sqlite3.Connection:
        conn = _dict_row_conn()
        conn.execute(
            """
            CREATE TABLE error_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT,
                stage TEXT,
                reason_code TEXT,
                detail TEXT,
                ts_ms INTEGER
            )
            """
        )
        rows = [
            ("chat_agent.pre_commit", "LLM_UNAVAILABLE", 1000),
            ("chat_agent.pre_commit", "LLM_UNAVAILABLE", 1100),
            ("chat_agent.pre_commit", "EMPTY_NARRATIVE", 1200),
            ("hippocampus.recall", "RETR_EMBED_TIMEOUT", 1300),
            ("hippocampus.recall", "RETR_EMBED_TIMEOUT", 1400),
            ("hippocampus.recall", "RETR_EMBED_TIMEOUT", 1500),
            ("chat_agent.pre_commit", "LLM_UNAVAILABLE", 99999),  # out of window
        ]
        conn.executemany(
            "INSERT INTO error_log (stage, reason_code, ts_ms) VALUES (?, ?, ?)",
            rows,
        )
        conn.commit()
        return conn

    def test_t_opt9_12_error_summary_aggregation(self) -> None:
        conn = self._make_conn()
        try:
            builder = ObsQueryBuilder()
            result = builder.run_named(
                conn,
                "error_summary",
                start=1000,
                end=2000,
                limit=20,
            )
            self.assertIsInstance(result, list)
            key_to_count = {
                (row["stage"], row["reason_code"]): row["error_count"]
                for row in result
            }
            self.assertEqual(
                key_to_count.get(("hippocampus.recall", "RETR_EMBED_TIMEOUT")), 3
            )
            self.assertEqual(
                key_to_count.get(("chat_agent.pre_commit", "LLM_UNAVAILABLE")), 2
            )
            self.assertEqual(
                key_to_count.get(("chat_agent.pre_commit", "EMPTY_NARRATIVE")), 1
            )
            # 排序：error_count DESC
            first = result[0]
            self.assertEqual(first["stage"], "hippocampus.recall")
            self.assertEqual(first["reason_code"], "RETR_EMBED_TIMEOUT")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
