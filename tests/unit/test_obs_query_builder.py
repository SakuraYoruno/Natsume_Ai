"""ObsQueryBuilder 契约单测（OPT-9 一期）.

覆盖：
  T-OPT9-01：NamedQueryRegistry 能从 YAML 加载已有 named query。
  T-OPT9-02：获取不存在的 query 抛 KeyError。
  T-OPT9-03：NamedQuery.validate_args 对缺参/多参/类型非法抛 ValueError。
  T-OPT9-04：ObsQueryBuilder.run_named 能在内存 sqlite 上正确执行 session_summary，
            并返回 list[dict]。
  T-OPT9-05：YAML 目录中 name 重复时 registry 加载报错（防止静默覆盖）。
  T-OPT9-06：params 类型非法时 YAML 加载报错。
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from host_brain.observability.query_builder import (
    NamedQuery,
    NamedQueryRegistry,
    ObsQueryBuilder,
)


class TestNamedQueryRegistry(unittest.TestCase):
    def test_t_opt9_01_loads_session_summary_from_default_dir(self) -> None:
        registry = NamedQueryRegistry()
        self.assertIn("session_summary", registry.names())
        nq = registry.get("session_summary")
        self.assertIsInstance(nq, NamedQuery)
        self.assertIn("start", nq.params)
        self.assertIn("end", nq.params)
        self.assertIn("limit", nq.params)
        self.assertEqual(nq.params["start"], "datetime_ms")
        self.assertEqual(nq.params["limit"], "int")

    def test_t_opt9_02_unknown_name_raises(self) -> None:
        registry = NamedQueryRegistry()
        with self.assertRaises(KeyError):
            registry.get("__does_not_exist__")

    def test_t_opt9_03_validate_args_rejects_bad_inputs(self) -> None:
        nq = NamedQuery(
            name="_unit_test_q",
            sql="SELECT 1",
            params={"a": "int", "b": "str"},
        )
        # missing
        with self.assertRaises(ValueError):
            nq.validate_args({"a": 1})
        # extra
        with self.assertRaises(ValueError):
            nq.validate_args({"a": 1, "b": "x", "c": 9})
        # wrong type（int 不能强转的字符串）
        with self.assertRaises(ValueError):
            nq.validate_args({"a": "not_a_number", "b": "x"})

    def test_t_opt9_05_duplicate_name_in_yaml_dir_raises(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="opt9_dup_"))
        try:
            (tmp / "a.yaml").write_text(
                "name: dup_q\nsql: 'SELECT 1'\nparams: {}\n",
                encoding="utf-8",
            )
            (tmp / "b.yaml").write_text(
                "name: dup_q\nsql: 'SELECT 2'\nparams: {}\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError) as cm:
                NamedQueryRegistry(queries_dir=tmp)
            self.assertIn("重复", str(cm.exception))
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    def test_t_opt9_06_invalid_param_type_in_yaml_raises(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="opt9_bad_type_"))
        try:
            (tmp / "bad.yaml").write_text(
                "name: bad_q\nsql: 'SELECT :x'\nparams: {x: totally_not_a_type}\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                NamedQueryRegistry(queries_dir=tmp)
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)


class TestObsQueryBuilderRunNamed(unittest.TestCase):
    def _make_conn_with_chat_events(self) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE chat_events (
                session_id TEXT NOT NULL,
                ts INTEGER NOT NULL,
                latency_ms INTEGER NOT NULL
            )
            """
        )
        rows = [
            ("s1", 1000, 50),
            ("s1", 1200, 70),
            ("s2", 1500, 100),
            ("s3", 9999, 20),   # out of window
        ]
        conn.executemany(
            "INSERT INTO chat_events (session_id, ts, latency_ms) VALUES (?, ?, ?)",
            rows,
        )
        conn.commit()
        return conn

    def test_t_opt9_04_run_named_session_summary(self) -> None:
        conn = self._make_conn_with_chat_events()
        try:
            builder = ObsQueryBuilder()
            result = builder.run_named(
                conn,
                "session_summary",
                start=1000,
                end=2000,
                limit=10,
            )
            self.assertIsInstance(result, list)
            # 窗口内 s1 (2条) 和 s2 (1条) 应入选；s3 越界应被排除
            session_ids = {row["session_id"] for row in result}
            self.assertEqual({"s1", "s2"}, session_ids)
            s1 = next(r for r in result if r["session_id"] == "s1")
            self.assertEqual(s1["event_count"], 2)
            self.assertAlmostEqual(s1["avg_latency_ms"], 60.0, places=1)
        finally:
            conn.close()

    def test_t_opt9_04b_run_named_rejects_missing_param(self) -> None:
        conn = self._make_conn_with_chat_events()
        try:
            builder = ObsQueryBuilder()
            with self.assertRaises(ValueError):
                builder.run_named(conn, "session_summary", start=0, end=1)  # 缺 limit
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
