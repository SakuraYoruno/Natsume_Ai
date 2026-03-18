"""T-DREAM-01~05：DreamGenerator Skill 单元测试。

覆盖：
  T-DREAM-01  Sleep 模式触发，梦境写入 Episodic，category=dream
  T-DREAM-02  Normal 模式无空闲 → SCHED_CONDITION_UNMET
  T-DREAM-03  Episodic 不可用 → 降级（不伪造，但 bond_summary 可补救）
  T-DREAM-04  召回梦境时 evidence_level=NARRATIVE_ONLY 标注存在
  T-DREAM-05  Gaming 模式 → SCHED_CONDITION_UNMET（非 sleep/idle）
"""

from __future__ import annotations

import pytest

from host_brain.skills.impl.dream_generator import (
    execute_dream_generator,
    DREAM_CATEGORY,
    EVIDENCE_LEVEL,
    STATUS_EXECUTED,
    STATUS_REJECTED,
    STATUS_DEGRADED,
)
from host_brain.core.errors import SCHED_CONDITION_UNMET, SCHED_NO_CONTENT


# ── 辅助工厂 ──

def _ctx(sys_mode: str = "sleep", idle: bool = True, **kwargs) -> dict:
    return {
        "sys_mode": sys_mode,
        "idle": idle,
        "request_id": "req-dream",
        "intent_id": "intent-dream",
        **kwargs,
    }


def _make_episodic_reader(entries: list[dict]) -> callable:
    def _reader(start_ts: int, end_ts: int, top_k: int) -> list[dict]:
        return entries[:top_k]
    return _reader


def _make_episodic_writer() -> tuple[callable, list[dict]]:
    written: list[dict] = []
    def _writer(entry: dict) -> dict:
        written.append(entry)
        return {"status": "OK", "entry_id": "ep-001"}
    return _writer, written


def _make_llm_generator(narrative: str = "今晚梦见了星空") -> callable:
    def _gen(inp: dict) -> dict:
        return {"narrative": narrative}
    return _gen


# ── T-DREAM-01 ──

class TestDreamSleepTrigger:
    """T-DREAM-01：Sleep 模式触发，梦境写入 Episodic，category=dream。"""

    def test_sleep_mode_generates_dream(self) -> None:
        writer, written = _make_episodic_writer()
        result = execute_dream_generator(
            args={},
            context=_ctx(
                sys_mode="sleep",
                episodic_reader=_make_episodic_reader([
                    {"summary": "今天和主人聊了很多"},
                    {"summary": "一起看了电影"},
                ]),
                episodic_writer=writer,
                llm_generator=_make_llm_generator("梦见了星空和主人"),
            ),
        )
        assert result["status"] == STATUS_EXECUTED
        assert result["reason_code"] is None
        # 写入 2 条：梦境条目 + interaction_hint
        assert len(written) == 2
        entry = written[0]
        assert entry["category"] == DREAM_CATEGORY
        assert entry["evidence_level"] == EVIDENCE_LEVEL
        assert written[1]["category"] == "interaction_hint"

    def test_dream_manifest_fields(self) -> None:
        writer, _ = _make_episodic_writer()
        result = execute_dream_generator(
            args={},
            context=_ctx(
                episodic_reader=_make_episodic_reader([{"summary": "今天很开心"}]),
                episodic_writer=writer,
                llm_generator=_make_llm_generator("梦见了花园"),
            ),
        )
        assert result["status"] == STATUS_EXECUTED
        m = result["manifest"]
        assert m["category"] == DREAM_CATEGORY
        assert m["evidence_level"] == EVIDENCE_LEVEL
        assert m["narrative_length"] > 0
        assert "hash" in m


# ── T-DREAM-02 ──

class TestDreamNormalNoIdle:
    """T-DREAM-02：Normal 模式无空闲 → SCHED_CONDITION_UNMET。"""

    def test_normal_not_idle_rejected(self) -> None:
        result = execute_dream_generator(
            args={},
            context=_ctx(sys_mode="normal", idle=False),
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == SCHED_CONDITION_UNMET

    def test_normal_with_idle_allowed(self) -> None:
        writer, _ = _make_episodic_writer()
        result = execute_dream_generator(
            args={},
            context=_ctx(
                sys_mode="normal",
                idle=True,
                episodic_reader=_make_episodic_reader([{"summary": "空闲时的记忆"}]),
                episodic_writer=writer,
                llm_generator=_make_llm_generator("空闲梦境"),
            ),
        )
        assert result["status"] == STATUS_EXECUTED


# ── T-DREAM-03 ──

class TestDreamEpisodicUnavailable:
    """T-DREAM-03：Episodic 不可用时，bond_summary 可补救；无任何素材则 SCHED_NO_CONTENT。"""

    def test_no_episodic_no_bond_rejected(self) -> None:
        result = execute_dream_generator(
            args={},
            context=_ctx(
                episodic_reader=None,
                episodic_writer=None,
                llm_generator=_make_llm_generator("梦境"),
            ),
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == SCHED_NO_CONTENT

    def test_bond_summary_rescues_no_episodic(self) -> None:
        writer, written = _make_episodic_writer()
        result = execute_dream_generator(
            args={},
            context=_ctx(
                episodic_reader=None,
                episodic_writer=writer,
                llm_generator=_make_llm_generator("Bond 梦境"),
                bond_summary={"stage": "CLOSE", "summary": "关系很好"},
            ),
        )
        assert result["status"] == STATUS_EXECUTED
        # 写入 2 条：梦境条目 + interaction_hint
        assert len(written) == 2
        assert written[0]["category"] == DREAM_CATEGORY

    def test_episodic_reader_exception_degraded_gracefully(self) -> None:
        def _bad_reader(s, e, k):
            raise RuntimeError("DB unavailable")

        writer, written = _make_episodic_writer()
        result = execute_dream_generator(
            args={},
            context=_ctx(
                episodic_reader=_bad_reader,
                episodic_writer=writer,
                llm_generator=_make_llm_generator("降级梦境"),
                bond_summary={"stage": "CLOSE"},
            ),
        )
        # episodic_reader 异常时降级，但 bond_summary 存在，仍可生成
        assert result["status"] == STATUS_EXECUTED


# ── T-DREAM-04 ──

class TestDreamEvidenceLevel:
    """T-DREAM-04：召回梦境时 evidence_level=NARRATIVE_ONLY 标注存在。"""

    def test_dream_entry_has_narrative_only_evidence(self) -> None:
        writer, written = _make_episodic_writer()
        execute_dream_generator(
            args={},
            context=_ctx(
                episodic_reader=_make_episodic_reader([{"summary": "记忆片段"}]),
                episodic_writer=writer,
                llm_generator=_make_llm_generator("叙事梦境"),
            ),
        )
        # 写入 2 条：梦境条目 + interaction_hint
        assert len(written) == 2
        assert written[0]["evidence_level"] == "NARRATIVE_ONLY"

    def test_dream_manifest_evidence_level(self) -> None:
        writer, _ = _make_episodic_writer()
        result = execute_dream_generator(
            args={},
            context=_ctx(
                episodic_reader=_make_episodic_reader([{"summary": "记忆"}]),
                episodic_writer=writer,
                llm_generator=_make_llm_generator("梦境"),
            ),
        )
        assert result["manifest"]["evidence_level"] == "NARRATIVE_ONLY"


# ── T-DREAM-05 ──

class TestDreamGamingBlock:
    """T-DREAM-05：Gaming 模式 → SCHED_CONDITION_UNMET（非 sleep/idle）。"""

    def test_gaming_not_idle_rejected(self) -> None:
        result = execute_dream_generator(
            args={},
            context=_ctx(sys_mode="gaming", idle=False),
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == SCHED_CONDITION_UNMET

    def test_gaming_with_idle_allowed(self) -> None:
        """Gaming 模式但 idle=True 时，条件满足（idle 优先）。"""
        writer, _ = _make_episodic_writer()
        result = execute_dream_generator(
            args={},
            context=_ctx(
                sys_mode="gaming",
                idle=True,
                episodic_reader=_make_episodic_reader([{"summary": "记忆"}]),
                episodic_writer=writer,
                llm_generator=_make_llm_generator("梦境"),
            ),
        )
        assert result["status"] == STATUS_EXECUTED
