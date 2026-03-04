"""T-DIARY-01~05：DiaryWriter Skill 单元测试。

覆盖：
  T-DIARY-01  每日触发，日记文件创建成功，Episodic 写入成功
  T-DIARY-02  无当日对话 → 输出"无内容"，不伪造日记（SCHED_NO_CONTENT）
  T-DIARY-03  文件系统不可用 → SKILL_EXEC_FAILED（降级），不伪造
  T-DIARY-04  Episodic 不可用 → DEPENDENCY_UNAVAILABLE，文件仍可写入（降级）
  T-DIARY-05  日记含设定漂移建议时，生成 canon_proposal 并写入 canon_proposal_log
"""

from __future__ import annotations

import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from host_brain.skills.impl.diary_writer import (
    execute_diary_writer,
    DIARY_CATEGORY,
    STATUS_EXECUTED,
    STATUS_REJECTED,
    STATUS_DEGRADED,
)
from host_brain.core.errors import SCHED_NO_CONTENT


# ── 辅助工厂 ──

def _ctx(**kwargs) -> dict:
    return {
        "request_id": "req-diary",
        "intent_id": "intent-diary",
        **kwargs,
    }


def _make_audit_reader(records: list[dict]) -> callable:
    def _reader(start_ts: int, end_ts: int) -> list[dict]:
        return records
    return _reader


def _make_episodic_writer() -> tuple[callable, list[dict]]:
    written: list[dict] = []
    def _writer(entry: dict) -> dict:
        written.append(entry)
        return {"status": "OK", "entry_id": "ep-diary-001"}
    return _writer, written


def _make_llm_generator(narrative: str = "今天是美好的一天", drift: dict | None = None) -> callable:
    def _gen(inp: dict) -> dict:
        result: dict = {"narrative": narrative}
        if drift:
            result["drift_suggestion"] = drift
        return result
    return _gen


def _today() -> str:
    return datetime.date.today().isoformat()


# ── T-DIARY-01 ──

class TestDiaryGenerate:
    """T-DIARY-01：每日触发，日记文件创建成功，Episodic 写入成功。"""

    def test_diary_file_and_episodic_written(self, tmp_path: Path) -> None:
        writer, written = _make_episodic_writer()
        result = execute_diary_writer(
            args={"base_dir": str(tmp_path), "date": _today()},
            context=_ctx(
                audit_reader=_make_audit_reader([
                    {"summary": "今天聊了很多", "detail": "对话记录"}
                ]),
                episodic_writer=writer,
                llm_generator=_make_llm_generator("今天是美好的一天"),
            ),
        )
        assert result["status"] == STATUS_EXECUTED
        assert result["reason_code"] is None

        # 文件写入
        diary_file = tmp_path / "diary" / f"{_today()}.md"
        assert diary_file.exists()
        assert "今天是美好的一天" in diary_file.read_text(encoding="utf-8")

        # Episodic 写入
        assert len(written) == 1
        assert written[0]["category"] == DIARY_CATEGORY
        assert written[0]["date"] == _today()

    def test_diary_manifest_fields(self, tmp_path: Path) -> None:
        writer, _ = _make_episodic_writer()
        result = execute_diary_writer(
            args={"base_dir": str(tmp_path), "date": _today()},
            context=_ctx(
                audit_reader=_make_audit_reader([{"summary": "对话"}]),
                episodic_writer=writer,
                llm_generator=_make_llm_generator("日记内容"),
            ),
        )
        assert result["status"] == STATUS_EXECUTED
        m = result["manifest"]
        assert m["date"] == _today()
        assert m["category"] == DIARY_CATEGORY
        assert m["narrative_length"] > 0
        assert "hash" in m
        assert "file_path" in m


# ── T-DIARY-02 ──

class TestDiaryNoContent:
    """T-DIARY-02：无当日对话 → SCHED_NO_CONTENT，不伪造日记。"""

    def test_no_audit_no_episodic_rejected(self, tmp_path: Path) -> None:
        result = execute_diary_writer(
            args={"base_dir": str(tmp_path), "date": _today()},
            context=_ctx(
                audit_reader=_make_audit_reader([]),
                episodic_writer=None,
                llm_generator=_make_llm_generator("不应生成"),
            ),
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == SCHED_NO_CONTENT

        # 文件不应被创建
        diary_file = tmp_path / "diary" / f"{_today()}.md"
        assert not diary_file.exists()

    def test_no_audit_reader_rejected(self, tmp_path: Path) -> None:
        result = execute_diary_writer(
            args={"base_dir": str(tmp_path), "date": _today()},
            context=_ctx(
                audit_reader=None,
                episodic_writer=None,
                llm_generator=_make_llm_generator("不应生成"),
            ),
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == SCHED_NO_CONTENT


# ── T-DIARY-03 ──

class TestDiaryFileSystemUnavailable:
    """T-DIARY-03：文件系统不可用 → 文件写入失败，但不伪造（降级处理）。"""

    def test_io_error_degrades_gracefully(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """通过 monkeypatch 模拟 IO 错误（跨平台，不依赖 chmod）。"""
        from pathlib import Path as _Path
        original_write = _Path.write_text

        def _bad_write(self, *args, **kwargs):
            raise PermissionError("Permission denied (simulated)")

        monkeypatch.setattr(_Path, "write_text", _bad_write)
        writer, written = _make_episodic_writer()
        result = execute_diary_writer(
            args={"base_dir": str(tmp_path), "date": _today()},
            context=_ctx(
                audit_reader=_make_audit_reader([{"summary": "对话"}]),
                episodic_writer=writer,
                llm_generator=_make_llm_generator("日记内容"),
            ),
        )
        # 文件写入失败时降级，整体不应崩溃
        # file_result 中应有 error 字段
        assert result["status"] == STATUS_EXECUTED
        assert result["file_result"] is not None
        assert "error" in result["file_result"]
        # Episodic 仍应写入（降级策略）
        assert len(written) == 1


# ── T-DIARY-04 ──

class TestDiaryEpisodicUnavailable:
    """T-DIARY-04：Episodic 不可用 → 文件仍可写入（降级）。"""

    def test_episodic_writer_exception_degrades(self, tmp_path: Path) -> None:
        def _bad_writer(entry: dict) -> dict:
            raise RuntimeError("Episodic DB unavailable")

        result = execute_diary_writer(
            args={"base_dir": str(tmp_path), "date": _today()},
            context=_ctx(
                audit_reader=_make_audit_reader([{"summary": "对话"}]),
                episodic_writer=_bad_writer,
                llm_generator=_make_llm_generator("日记内容"),
            ),
        )
        # Episodic 写入失败时降级，文件仍应写入
        assert result["status"] == STATUS_EXECUTED
        diary_file = tmp_path / "diary" / f"{_today()}.md"
        assert diary_file.exists()

    def test_no_episodic_writer_still_writes_file(self, tmp_path: Path) -> None:
        result = execute_diary_writer(
            args={"base_dir": str(tmp_path), "date": _today()},
            context=_ctx(
                audit_reader=_make_audit_reader([{"summary": "对话"}]),
                episodic_writer=None,
                llm_generator=_make_llm_generator("日记内容"),
            ),
        )
        assert result["status"] == STATUS_EXECUTED
        diary_file = tmp_path / "diary" / f"{_today()}.md"
        assert diary_file.exists()


# ── T-DIARY-05 ──

class TestDiaryEvolutionProposal:
    """T-DIARY-05：日记含设定漂移建议时，生成 canon_proposal 并写入 canon_proposal_log。"""

    def test_drift_suggestion_generates_proposal(self, tmp_path: Path) -> None:
        drift = {
            "doc_id": "char-natsume-001",
            "suggested_body": "夏目喜欢在夜晚看星星",
            "reason": "多次对话中提到了星空",
        }
        mock_store = MagicMock()
        mock_store.submit_proposal.return_value = {
            "proposal_id": "diary-intent-diary-char-natsume-001",
            "status": "pending",
            "doc_id": "char-natsume-001",
        }

        writer, _ = _make_episodic_writer()
        result = execute_diary_writer(
            args={"base_dir": str(tmp_path), "date": _today()},
            context=_ctx(
                audit_reader=_make_audit_reader([{"summary": "对话"}]),
                episodic_writer=writer,
                llm_generator=_make_llm_generator("日记内容", drift=drift),
                canon_world_store=mock_store,
            ),
        )
        assert result["status"] == STATUS_EXECUTED
        assert result["evolution_proposal"] is not None
        assert result["evolution_proposal"]["doc_id"] == "char-natsume-001"
        mock_store.submit_proposal.assert_called_once()

    def test_no_drift_suggestion_no_proposal(self, tmp_path: Path) -> None:
        writer, _ = _make_episodic_writer()
        result = execute_diary_writer(
            args={"base_dir": str(tmp_path), "date": _today()},
            context=_ctx(
                audit_reader=_make_audit_reader([{"summary": "对话"}]),
                episodic_writer=writer,
                llm_generator=_make_llm_generator("日记内容"),  # 无 drift
            ),
        )
        assert result["status"] == STATUS_EXECUTED
        assert result["evolution_proposal"] is None

    def test_drift_with_execution_claim_rejected(self, tmp_path: Path) -> None:
        """drift_suggestion 含伪执行句时，提案被 QA 拦截，不写入。"""
        drift = {
            "doc_id": "char-001",
            "suggested_body": "任务已完成，夏目已执行了所有操作",
            "reason": "测试",
        }
        mock_store = MagicMock()
        writer, _ = _make_episodic_writer()
        result = execute_diary_writer(
            args={"base_dir": str(tmp_path), "date": _today()},
            context=_ctx(
                audit_reader=_make_audit_reader([{"summary": "对话"}]),
                episodic_writer=writer,
                llm_generator=_make_llm_generator("日记内容", drift=drift),
                canon_world_store=mock_store,
            ),
        )
        assert result["status"] == STATUS_EXECUTED
        # 含伪执行句的提案被拦截，evolution_proposal 为 None
        assert result["evolution_proposal"] is None
        mock_store.submit_proposal.assert_not_called()
