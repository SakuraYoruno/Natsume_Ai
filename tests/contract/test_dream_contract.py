"""Contract tests for Dream Generator Skill (TASK-0093).

覆盖 T-Dream-01（Sleep/Idle 触发+叙事写入）、T-Dream-02（条件不满足/无素材跳过）。
"""

from __future__ import annotations

import pytest

from host_brain.skills.impl.dream_generator import (
    execute_dream_generator,
    STATUS_EXECUTED,
    STATUS_REJECTED,
    STATUS_DEGRADED,
    DREAM_CATEGORY,
    EVIDENCE_LEVEL,
)
from host_brain.core.errors import SCHED_CONDITION_UNMET, SCHED_NO_CONTENT


def _mock_episodic_reader(start_ts, end_ts, top_k):
    """模拟 Episodic 读取，返回几条简单记录。"""
    return [
        {"summary": "和主人聊了游戏", "ts_ms": start_ts + 1000},
        {"summary": "主人表扬了我", "ts_ms": start_ts + 2000},
    ]


def _mock_llm_generator(input_data):
    """模拟 LLM 梦境生成。"""
    summaries = input_data.get("episodic_summaries", [])
    narrative = f"在梦中，{'，'.join(summaries[:3]) if summaries else '什么都没发生'}。真是美好的一天呢。"
    return {"narrative": narrative}


def _mock_episodic_writer(entry):
    return {"status": "OK", "entry_id": "test_entry_1"}


class TestDreamGeneratorTrigger:
    """T-Dream-01：Sleep/Idle 触发+叙事写入。"""

    def test_execute_in_sleep_mode(self) -> None:
        result = execute_dream_generator(
            {"max_episodic_entries": 10},
            {
                "sys_mode": "sleep",
                "idle": False,
                "episodic_reader": _mock_episodic_reader,
                "llm_generator": _mock_llm_generator,
                "episodic_writer": _mock_episodic_writer,
            },
        )
        assert result["status"] == STATUS_EXECUTED
        assert result["reason_code"] is None
        assert result["manifest"]["category"] == DREAM_CATEGORY
        assert result["manifest"]["evidence_level"] == EVIDENCE_LEVEL
        assert result["dream_entry"]["content"]
        assert "和主人" in result["dream_entry"]["content"]

    def test_execute_in_idle_state(self) -> None:
        result = execute_dream_generator(
            {},
            {
                "sys_mode": "normal",
                "idle": True,
                "episodic_reader": _mock_episodic_reader,
                "llm_generator": _mock_llm_generator,
                "episodic_writer": _mock_episodic_writer,
            },
        )
        assert result["status"] == STATUS_EXECUTED

    def test_manifest_structure(self) -> None:
        result = execute_dream_generator(
            {},
            {
                "sys_mode": "sleep",
                "episodic_reader": _mock_episodic_reader,
                "llm_generator": _mock_llm_generator,
                "episodic_writer": _mock_episodic_writer,
            },
        )
        manifest = result["manifest"]
        assert "dream_id" in manifest
        assert "category" in manifest
        assert "evidence_level" in manifest
        assert "memory_entry_count" in manifest
        assert "narrative_length" in manifest
        assert "hash" in manifest
        assert manifest["memory_entry_count"] == 2

    def test_episodic_entry_written(self) -> None:
        written = []
        def capturing_writer(entry):
            if entry.get("category") == DREAM_CATEGORY:
                written.append(entry)
            return {"status": "OK"}
            return {"status": "OK"}

        execute_dream_generator(
            {},
            {
                "sys_mode": "sleep",
                "episodic_reader": _mock_episodic_reader,
                "llm_generator": _mock_llm_generator,
                "episodic_writer": capturing_writer,
            },
        )
        assert len(written) == 1
        assert written[0]["category"] == DREAM_CATEGORY
        assert written[0]["evidence_level"] == EVIDENCE_LEVEL
        assert written[0]["source"] == "dream_generator"


class TestDreamGeneratorConditions:
    """T-Dream-02：条件不满足/无素材跳过。"""

    def test_rejected_in_normal_non_idle(self) -> None:
        result = execute_dream_generator(
            {},
            {"sys_mode": "normal", "idle": False},
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == SCHED_CONDITION_UNMET

    def test_rejected_in_gaming_non_idle(self) -> None:
        result = execute_dream_generator(
            {},
            {"sys_mode": "gaming", "idle": False},
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == SCHED_CONDITION_UNMET

    def test_no_episodic_entries_rejected(self) -> None:
        """无 Episodic 且无 Bond → SCHED_NO_CONTENT。"""
        result = execute_dream_generator(
            {},
            {
                "sys_mode": "sleep",
                "episodic_reader": lambda s, e, k: [],
            },
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == SCHED_NO_CONTENT

    def test_with_bond_summary_only(self) -> None:
        """有 Bond 但无 Episodic → 仍可生成。"""
        result = execute_dream_generator(
            {},
            {
                "sys_mode": "sleep",
                "episodic_reader": lambda s, e, k: [],
                "bond_summary": {"key_memories": ["first_meeting"]},
                "llm_generator": _mock_llm_generator,
                "episodic_writer": _mock_episodic_writer,
            },
        )
        assert result["status"] == STATUS_EXECUTED

    def test_llm_failure_degraded(self) -> None:
        def failing_llm(_):
            raise RuntimeError("GPU OOM")

        result = execute_dream_generator(
            {},
            {
                "sys_mode": "sleep",
                "episodic_reader": _mock_episodic_reader,
                "llm_generator": failing_llm,
            },
        )
        assert result["status"] == STATUS_DEGRADED
        assert result["reason_code"] == "DREAM_LLM_UNAVAILABLE"

    def test_empty_narrative_rejected(self) -> None:
        def empty_llm(_):
            return {"narrative": "   "}

        result = execute_dream_generator(
            {},
            {
                "sys_mode": "sleep",
                "episodic_reader": _mock_episodic_reader,
                "llm_generator": empty_llm,
            },
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == SCHED_NO_CONTENT

    def test_no_llm_uses_placeholder(self) -> None:
        """无 LLM → 使用占位叙事。"""
        result = execute_dream_generator(
            {},
            {
                "sys_mode": "sleep",
                "episodic_reader": _mock_episodic_reader,
                "episodic_writer": _mock_episodic_writer,
            },
        )
        assert result["status"] == STATUS_EXECUTED
        assert "占位" in result["dream_entry"]["content"]

    def test_episodic_writer_failure_degraded(self) -> None:
        def failing_writer(_):
            raise IOError("disk full")

        result = execute_dream_generator(
            {},
            {
                "sys_mode": "sleep",
                "episodic_reader": _mock_episodic_reader,
                "llm_generator": _mock_llm_generator,
                "episodic_writer": failing_writer,
            },
        )
        assert result["status"] == STATUS_DEGRADED
        assert result["reason_code"] == "DREAM_EPISODIC_WRITE_FAILED"
