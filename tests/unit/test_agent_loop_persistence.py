"""Agent Loop 持久化单元测试。"""

from __future__ import annotations

import json

import anyio
import pytest

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.app.vision.agent_loop import (
    MockClawdbotClient,
    run_agent_loop,
)


def _make_ledger() -> SQLiteLedger:
    """创建内存 SQLiteLedger（测试专用）。"""
    return SQLiteLedger(db_path=":memory:", check_same_thread=False)


# ---------------------------------------------------------------------------
# 表结构测试
# ---------------------------------------------------------------------------

class TestAgentLoopLogSchema:
    def test_table_exists(self):
        """agent_loop_log 表应存在。"""
        ledger = _make_ledger()
        rows = ledger._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_loop_log'"
        ).fetchall()
        assert len(rows) == 1

    def test_required_columns_exist(self):
        """必要字段应存在。"""
        ledger = _make_ledger()
        cols = {
            row[1]
            for row in ledger._conn.execute("PRAGMA table_info(agent_loop_log)").fetchall()
        }
        required = {
            "id", "request_id", "session_id", "goal", "round", "phase",
            "status", "goal_achieved", "action_plan_json", "report_json",
            "action_count", "success_count", "failed_count", "latency_ms",
            "error", "extra_json", "ts_ms",
        }
        assert required.issubset(cols)

    def test_indexes_exist(self):
        """三个索引应存在。"""
        ledger = _make_ledger()
        indexes = {
            row[1]
            for row in ledger._conn.execute(
                "SELECT * FROM sqlite_master WHERE type='index' AND tbl_name='agent_loop_log'"
            ).fetchall()
        }
        assert "idx_agent_loop_request_id" in indexes
        assert "idx_agent_loop_session_id" in indexes
        assert "idx_agent_loop_ts_ms" in indexes


# ---------------------------------------------------------------------------
# log_agent_loop 方法测试
# ---------------------------------------------------------------------------

class TestLogAgentLoop:
    def test_write_and_read_basic(self):
        """写入一行后可以读回。"""
        ledger = _make_ledger()
        ledger.log_agent_loop({
            "request_id": "req-001",
            "session_id": "sess-001",
            "goal": "点击登录按钮",
            "round": 1,
            "phase": "PLAN_GENERATED",
            "status": "OK",
            "goal_achieved": False,
            "action_count": 3,
            "ts_ms": 1700000000000,
        })
        rows = ledger.agent_loop_log
        assert len(rows) == 1
        row = rows[0]
        assert row["request_id"] == "req-001"
        assert row["session_id"] == "sess-001"
        assert row["goal"] == "点击登录按钮"
        assert row["round"] == 1
        assert row["phase"] == "PLAN_GENERATED"
        assert row["goal_achieved"] == 0  # 存为整数

    def test_goal_achieved_true_stored_as_1(self):
        """goal_achieved=True 应存为 1。"""
        ledger = _make_ledger()
        ledger.log_agent_loop({
            "request_id": "req-002",
            "session_id": "sess-002",
            "goal": "test",
            "phase": "LOOP_DONE",
            "goal_achieved": True,
            "ts_ms": 1700000001000,
        })
        row = ledger.agent_loop_log[0]
        assert row["goal_achieved"] == 1

    def test_round_defaults_to_0(self):
        """未传 round 时默认为 0。"""
        ledger = _make_ledger()
        ledger.log_agent_loop({
            "request_id": "req-003",
            "session_id": "sess-003",
            "goal": "test",
            "phase": "LOOP_START",
            "ts_ms": 1700000002000,
        })
        row = ledger.agent_loop_log[0]
        assert row["round"] == 0

    def test_action_plan_json_stored(self):
        """action_plan_json 字段应正确存储。"""
        ledger = _make_ledger()
        plan_json = json.dumps({"actions": [{"type": "click"}]})
        ledger.log_agent_loop({
            "request_id": "req-004",
            "session_id": "sess-004",
            "goal": "test",
            "phase": "PLAN_GENERATED",
            "action_plan_json": plan_json,
            "ts_ms": 1700000003000,
        })
        row = ledger.agent_loop_log[0]
        assert row["action_plan_json"] == plan_json

    def test_multiple_rounds_stored(self):
        """多轮数据应全部存储。"""
        ledger = _make_ledger()
        for i in range(1, 4):
            ledger.log_agent_loop({
                "request_id": "req-005",
                "session_id": "sess-005",
                "goal": "多轮测试",
                "round": i,
                "phase": "EXECUTE_DONE",
                "status": "SUCCESS" if i == 3 else "CONTINUE",
                "goal_achieved": i == 3,
                "ts_ms": 1700000000000 + i * 1000,
            })
        rows = ledger.agent_loop_log
        assert len(rows) == 3
        assert rows[-1]["round"] == 3
        assert rows[-1]["goal_achieved"] == 1


# ---------------------------------------------------------------------------
# Agent Loop 集成测试（Mock 模式 + 持久化）
# ---------------------------------------------------------------------------

class TestAgentLoopPersistence:
    def test_loop_writes_to_ledger(self):
        """Mock 模式下 Agent Loop 应写入 ledger。"""
        async def _run():
            ledger = _make_ledger()
            events = []
            async for sse_line in run_agent_loop(
                request_id="req-persist-001",
                session_id="sess-persist-001",
                goal="持久化测试",
                llm_runtime={"available": False},
                clawdbot_client=MockClawdbotClient(),
                max_rounds=2,
                round_timeout_sec=10.0,
                total_timeout_sec=30.0,
                image_upload_allowed=False,
                ledger=ledger,
            ):
                if sse_line.startswith("data: "):
                    events.append(json.loads(sse_line[6:]))

            # 验证 SSE 事件正常
            phases = [e["phase"] for e in events]
            assert "LOOP_START" in phases
            assert "LOOP_DONE" in phases

            # 验证 ledger 写入
            rows = ledger.agent_loop_log
            assert len(rows) >= 2  # 至少 LOOP_START 和 LOOP_DONE

            db_phases = {r["phase"] for r in rows}
            assert "LOOP_START" in db_phases
            assert "LOOP_DONE" in db_phases

            # 验证 request_id 一致
            for row in rows:
                assert row["request_id"] == "req-persist-001"
                assert row["session_id"] == "sess-persist-001"
                assert row["goal"] == "持久化测试"

        anyio.run(_run)

    def test_loop_without_ledger_still_works(self):
        """不传 ledger 时 Agent Loop 应正常运行（不崩溃）。"""
        async def _run():
            events = []
            async for sse_line in run_agent_loop(
                request_id="req-no-ledger",
                session_id="sess-no-ledger",
                goal="无持久化测试",
                llm_runtime={"available": False},
                clawdbot_client=MockClawdbotClient(),
                max_rounds=1,
                round_timeout_sec=10.0,
                total_timeout_sec=30.0,
                image_upload_allowed=False,
                ledger=None,
            ):
                if sse_line.startswith("data: "):
                    events.append(json.loads(sse_line[6:]))

            phases = [e["phase"] for e in events]
            assert "LOOP_DONE" in phases

        anyio.run(_run)

    def test_loop_done_row_has_extra_json(self):
        """LOOP_DONE 行应包含 extra_json（round_summaries）。"""
        async def _run():
            ledger = _make_ledger()
            async for _ in run_agent_loop(
                request_id="req-extra-json",
                session_id="sess-extra-json",
                goal="extra_json 测试",
                llm_runtime={"available": False},
                clawdbot_client=MockClawdbotClient(),
                max_rounds=1,
                round_timeout_sec=10.0,
                total_timeout_sec=30.0,
                image_upload_allowed=False,
                ledger=ledger,
            ):
                pass

            rows = ledger.agent_loop_log
            loop_done_rows = [r for r in rows if r["phase"] == "LOOP_DONE"]
            assert len(loop_done_rows) == 1
            extra = json.loads(loop_done_rows[0]["extra_json"] or "{}")
            assert "round_summaries" in extra

        anyio.run(_run)
