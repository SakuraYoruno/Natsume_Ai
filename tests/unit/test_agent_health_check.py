"""T-HB-01~05: AgentHealthCheck 心跳精神状态检测单元测试（TASK-0200）。"""

from __future__ import annotations

import pytest

from host_brain.scheduler.health_check_prompt import (
    DEFAULT_HEARTBEAT_PROMPT,
    HEARTBEAT_EVENT_TYPE_ALERT,
    HEARTBEAT_EVENT_TYPE_OK,
    LLM_UNAVAILABLE_RESPONSE,
    MAX_RESPONSE_CHARS,
    parse_heartbeat_response,
    truncate_response,
)
from host_brain.scheduler.scheduler import NatsumeScheduler
from host_brain.scheduler.task_registry import SchedulerTaskRegistry
from host_brain.scheduler.health import SchedulerHealthReporter


# ── 测试夹具 ──────────────────────────────────────────────────────────────────

class _MockLedger:
    """最小化 Mock Ledger，记录 scheduler_event 写入。"""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def log_scheduler_event(self, row: dict) -> None:
        self.events.append(dict(row))

    def persist_scheduler_task(self, row: dict) -> None:
        pass

    def load_persisted_scheduler_tasks(self) -> list:
        return []

    def delete_persisted_scheduler_task(self, task_id: str) -> None:
        pass


def _make_scheduler(ledger=None, llm_caller=None) -> NatsumeScheduler:
    registry = SchedulerTaskRegistry()
    health = SchedulerHealthReporter(registry)
    sched = NatsumeScheduler(
        registry=registry,
        health_reporter=health,
        ledger=ledger,
        persist_tasks=False,
    )
    return sched


# ── T-HB-01: 正常回复含 HEARTBEAT_OK 时静默丢弃 ──────────────────────────────

class TestHeartbeatOkSilentDiscard:
    """T-HB-01: 回复含 HEARTBEAT_OK 时，不写入审计记录，不触发推送。"""

    def test_ok_response_no_audit_record(self):
        ledger = _MockLedger()
        sched = _make_scheduler(ledger=ledger)
        sched._heartbeat_prompt = DEFAULT_HEARTBEAT_PROMPT
        sched._heartbeat_max_chars = MAX_RESPONSE_CHARS
        sched._heartbeat_llm_caller = lambda prompt: "系统运行正常，HEARTBEAT_OK"

        result = sched._run_agent_health_check()

        assert result["status"] == HEARTBEAT_EVENT_TYPE_OK
        # 静默丢弃：不写入任何审计事件
        assert len(ledger.events) == 0

    def test_ok_response_case_insensitive(self):
        """HEARTBEAT_OK 大小写不敏感。"""
        ledger = _MockLedger()
        sched = _make_scheduler(ledger=ledger)
        sched._heartbeat_prompt = DEFAULT_HEARTBEAT_PROMPT
        sched._heartbeat_max_chars = MAX_RESPONSE_CHARS
        sched._heartbeat_llm_caller = lambda prompt: "heartbeat_ok everything fine"

        result = sched._run_agent_health_check()
        assert result["status"] == HEARTBEAT_EVENT_TYPE_OK
        assert len(ledger.events) == 0


# ── T-HB-02: 异常回复推送 HEARTBEAT_ALERT ────────────────────────────────────

class TestHeartbeatAlertPush:
    """T-HB-02: 回复不含 HEARTBEAT_OK 时，写入 HEARTBEAT_ALERT 审计记录。"""

    def test_alert_response_writes_audit(self):
        ledger = _MockLedger()
        sched = _make_scheduler(ledger=ledger)
        sched._heartbeat_prompt = DEFAULT_HEARTBEAT_PROMPT
        sched._heartbeat_max_chars = MAX_RESPONSE_CHARS
        sched._heartbeat_llm_caller = lambda prompt: "我感觉记忆有些混乱，无法确认当前状态。"

        result = sched._run_agent_health_check()

        assert result["status"] == HEARTBEAT_EVENT_TYPE_ALERT
        assert len(ledger.events) == 1
        event = ledger.events[0]
        assert event["status"] == HEARTBEAT_EVENT_TYPE_ALERT
        assert event["reason_code"] == HEARTBEAT_EVENT_TYPE_ALERT
        assert event["task_id"] == "agent_health_check"
        assert "ts_ms" in event

    def test_alert_event_contains_raw_response(self):
        ledger = _MockLedger()
        sched = _make_scheduler(ledger=ledger)
        sched._heartbeat_prompt = DEFAULT_HEARTBEAT_PROMPT
        sched._heartbeat_max_chars = MAX_RESPONSE_CHARS
        sched._heartbeat_llm_caller = lambda prompt: "异常状态：指令冲突检测到。"

        sched._run_agent_health_check()

        event = ledger.events[0]
        assert "异常状态" in str(event.get("error_detail") or "")


# ── T-HB-03: LLM 不可用时降级写入 HEARTBEAT_ALERT ────────────────────────────

class TestHeartbeatLLMUnavailable:
    """T-HB-03: LLM 不可用（超时/异常）时，降级写入 HEARTBEAT_ALERT。"""

    def test_llm_exception_triggers_alert(self):
        ledger = _MockLedger()
        sched = _make_scheduler(ledger=ledger)
        sched._heartbeat_prompt = DEFAULT_HEARTBEAT_PROMPT
        sched._heartbeat_max_chars = MAX_RESPONSE_CHARS

        def _failing_llm(prompt: str) -> str:
            raise ConnectionError("LLM endpoint timeout")

        sched._heartbeat_llm_caller = _failing_llm

        result = sched._run_agent_health_check()

        assert result["status"] == HEARTBEAT_EVENT_TYPE_ALERT
        assert len(ledger.events) == 1
        event = ledger.events[0]
        assert event["error_detail"] == LLM_UNAVAILABLE_RESPONSE

    def test_no_llm_caller_triggers_alert(self):
        """llm_caller=None 时，直接降级为 HEARTBEAT_ALERT。"""
        ledger = _MockLedger()
        sched = _make_scheduler(ledger=ledger)
        sched._heartbeat_prompt = DEFAULT_HEARTBEAT_PROMPT
        sched._heartbeat_max_chars = MAX_RESPONSE_CHARS
        sched._heartbeat_llm_caller = None

        result = sched._run_agent_health_check()

        assert result["status"] == HEARTBEAT_EVENT_TYPE_ALERT
        assert len(ledger.events) == 1


# ── T-HB-04: parse_heartbeat_response 大小写变体 ─────────────────────────────

class TestParseHeartbeatResponse:
    """T-HB-04: parse_heartbeat_response 对 HEARTBEAT_OK 大小写变体均返回 OK。"""

    @pytest.mark.parametrize("text,expected", [
        ("HEARTBEAT_OK", "OK"),
        ("heartbeat_ok", "OK"),
        ("HeartBeat_Ok", "OK"),
        ("系统正常 HEARTBEAT_OK 一切就绪", "OK"),
        ("", "ALERT"),
        ("   ", "ALERT"),
        ("系统异常，无法确认状态", "ALERT"),
        ("heartbeat ok", "ALERT"),   # 无下划线，不匹配
        (LLM_UNAVAILABLE_RESPONSE, "ALERT"),
    ])
    def test_parse_variants(self, text: str, expected: str):
        assert parse_heartbeat_response(text) == expected


# ── T-HB-05: list_health() 包含 agent_health_check 任务 ──────────────────────

class TestListHealth:
    """T-HB-05: 注册后可通过 list_health() 查询到 agent_health_check 任务状态。"""

    def test_health_check_task_in_list(self):
        sched = _make_scheduler()
        sched.register_agent_health_check(interval_seconds=1800)

        health_list = sched.list_health()
        task_ids = [item["task_id"] for item in health_list]
        assert "agent_health_check" in task_ids

    def test_health_check_task_fields(self):
        sched = _make_scheduler()
        sched.register_agent_health_check(interval_seconds=900)

        health_list = sched.list_health()
        hc = next(item for item in health_list if item["task_id"] == "agent_health_check")
        assert "status" in hc
        assert "error_count" in hc
        assert hc["enabled"] is True
        assert hc["trigger_mode"] == "interval"


# ── T-HB-06: 回复字符截断 ────────────────────────────────────────────────────

class TestResponseTruncation:
    """T-HB-06: 超长回复被截断至 max_chars 字符。"""

    def test_truncate_long_response(self):
        long_text = "异常" * 200  # 400 字符
        truncated = truncate_response(long_text, max_chars=300)
        assert len(truncated) <= 301  # 300 + 省略号
        assert truncated.endswith("…")

    def test_short_response_not_truncated(self):
        short_text = "正常回复"
        truncated = truncate_response(short_text, max_chars=300)
        assert truncated == short_text
