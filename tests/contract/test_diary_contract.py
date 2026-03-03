"""Contract tests for Diary Writer Skill (TASK-0094).

覆盖 T-Diary-01（正常生成+双写）、T-Diary-02（无对话跳过/LLM降级/日期校验）。
"""

from __future__ import annotations

import pathlib
import pytest

from host_brain.skills.impl.diary_writer import (
    execute_diary_writer,
    STATUS_EXECUTED,
    STATUS_REJECTED,
    STATUS_DEGRADED,
    DIARY_CATEGORY,
)
from host_brain.core.errors import SCHED_NO_CONTENT


def _mock_audit_reader(start_ts, end_ts):
    return [
        {"summary": "用户打开了游戏", "ts_ms": start_ts + 1000},
        {"summary": "聊了 30 分钟", "ts_ms": start_ts + 2000},
    ]


def _mock_episodic_reader(start_ts, end_ts, top_k):
    return [
        {"summary": "一起看了动画", "ts_ms": start_ts + 3000},
    ]


def _mock_llm_generator(input_data):
    date = input_data.get("date", "")
    return {"narrative": f"# {date}\n今天是充实的一天。和主人一起做了很多事情。"}


def _mock_episodic_writer(entry):
    return {"status": "OK", "entry_id": "diary_ep_1"}


class TestDiaryWriterNormal:
    """T-Diary-01：正常生成 + 双写。"""

    def test_execute_with_all_sources(self, tmp_path: pathlib.Path) -> None:
        result = execute_diary_writer(
            {"base_dir": str(tmp_path), "date": "2026-03-03"},
            {
                "audit_reader": _mock_audit_reader,
                "episodic_reader": _mock_episodic_reader,
                "llm_generator": _mock_llm_generator,
                "episodic_writer": _mock_episodic_writer,
            },
        )
        assert result["status"] == STATUS_EXECUTED
        assert result["reason_code"] is None
        # 文件写入检查
        diary_file = tmp_path / "diary" / "2026-03-03.md"
        assert diary_file.exists()
        content = diary_file.read_text(encoding="utf-8")
        assert "2026-03-03" in content
        # Manifest 结构
        manifest = result["manifest"]
        assert manifest["date"] == "2026-03-03"
        assert manifest["category"] == DIARY_CATEGORY
        assert manifest["audit_record_count"] == 2
        assert manifest["episodic_entry_count"] == 1

    def test_execute_with_audit_only(self, tmp_path: pathlib.Path) -> None:
        result = execute_diary_writer(
            {"base_dir": str(tmp_path), "date": "2026-03-03"},
            {
                "audit_reader": _mock_audit_reader,
                "llm_generator": _mock_llm_generator,
                "episodic_writer": _mock_episodic_writer,
            },
        )
        assert result["status"] == STATUS_EXECUTED

    def test_execute_with_episodic_only(self, tmp_path: pathlib.Path) -> None:
        result = execute_diary_writer(
            {"base_dir": str(tmp_path), "date": "2026-03-03"},
            {
                "episodic_reader": _mock_episodic_reader,
                "llm_generator": _mock_llm_generator,
                "episodic_writer": _mock_episodic_writer,
            },
        )
        assert result["status"] == STATUS_EXECUTED

    def test_manifest_has_file_path(self, tmp_path: pathlib.Path) -> None:
        result = execute_diary_writer(
            {"base_dir": str(tmp_path), "date": "2026-03-03"},
            {
                "audit_reader": _mock_audit_reader,
                "llm_generator": _mock_llm_generator,
                "episodic_writer": _mock_episodic_writer,
            },
        )
        assert result["manifest"]["file_path"] is not None

    def test_episodic_entry_written(self) -> None:
        written = []
        def capturing_writer(entry):
            written.append(entry)
            return {"status": "OK"}

        execute_diary_writer(
            {"date": "2026-03-03"},
            {
                "audit_reader": _mock_audit_reader,
                "llm_generator": _mock_llm_generator,
                "episodic_writer": capturing_writer,
            },
        )
        assert len(written) == 1
        assert written[0]["category"] == DIARY_CATEGORY
        assert written[0]["source"] == "diary_writer"
        assert written[0]["date"] == "2026-03-03"


class TestDiaryWriterConditions:
    """T-Diary-02：无对话跳过/LLM降级/日期校验。"""

    def test_no_conversations_rejected(self) -> None:
        result = execute_diary_writer(
            {"date": "2026-03-03"},
            {
                "audit_reader": lambda s, e: [],
                "episodic_reader": lambda s, e, k: [],
            },
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == SCHED_NO_CONTENT

    def test_no_readers_rejected(self) -> None:
        """完全无读取源 → 无素材跳过。"""
        result = execute_diary_writer(
            {"date": "2026-03-03"},
            {},
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == SCHED_NO_CONTENT

    def test_invalid_date_rejected(self) -> None:
        result = execute_diary_writer(
            {"date": "not-a-date"},
            {},
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == "DIARY_INVALID_DATE"

    def test_llm_failure_degraded(self) -> None:
        def failing_llm(_):
            raise RuntimeError("Model not loaded")

        result = execute_diary_writer(
            {"date": "2026-03-03"},
            {
                "audit_reader": _mock_audit_reader,
                "llm_generator": failing_llm,
            },
        )
        assert result["status"] == STATUS_DEGRADED
        assert result["reason_code"] == "DIARY_LLM_UNAVAILABLE"

    def test_empty_narrative_rejected(self) -> None:
        def empty_llm(_):
            return {"narrative": "  "}

        result = execute_diary_writer(
            {"date": "2026-03-03"},
            {
                "audit_reader": _mock_audit_reader,
                "llm_generator": empty_llm,
            },
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == SCHED_NO_CONTENT

    def test_no_llm_uses_placeholder(self) -> None:
        """无 LLM → 使用占位叙事。"""
        result = execute_diary_writer(
            {"date": "2026-03-03"},
            {
                "audit_reader": _mock_audit_reader,
                "episodic_writer": _mock_episodic_writer,
            },
        )
        assert result["status"] == STATUS_EXECUTED
        assert "占位" in result["diary_entry"]["content"]

    def test_default_date_uses_today(self) -> None:
        """不传 date → 使用今天。"""
        result = execute_diary_writer(
            {},
            {
                "audit_reader": _mock_audit_reader,
                "llm_generator": _mock_llm_generator,
                "episodic_writer": _mock_episodic_writer,
            },
        )
        assert result["status"] == STATUS_EXECUTED
        import datetime
        assert result["manifest"]["date"] == datetime.date.today().isoformat()
