"""T-DESK-01~08：DesktopNote Skill 单元测试。

覆盖：
  T-DESK-01  create 成功，manifest 含 before(None)/after/path/hash
  T-DESK-02  undo_token 调用后文件恢复原状态
  T-DESK-03  路径越界（含 ..）→ SCOPE_VIOLATION
  T-DESK-04  Gaming 模式 → SYSMODE_FORBIDDEN
  T-DESK-05  文件系统不可写 → DESKTOP_NOTE_FILE_ERROR + reason_code
  T-DESK-06  内容含伪执行句 → QA_CONTENT_BLOCKED
  T-DESK-07  delete 默认 move-to-quarantine，undo 可恢复
  T-DESK-08  写入审计可追溯原始草案与最终内容
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from host_brain.skills.impl.desktop_note import (
    execute_desktop_note,
    undo_desktop_note,
    SCOPE_VIOLATION,
    QA_CONTENT_BLOCKED,
    DESKTOP_NOTE_FILE_ERROR,
    STATUS_EXECUTED,
    STATUS_REJECTED,
)
from host_brain.core.errors import SYSMODE_FORBIDDEN


# ── 辅助工厂 ──

def _ctx(sys_mode: str = "normal", **kwargs) -> dict:
    return {"sys_mode": sys_mode, "request_id": "req-test", "intent_id": "intent-test", **kwargs}


def _args(action: str, filename: str, base_dir: str, content: str | None = None) -> dict:
    a: dict = {"action": action, "filename": filename, "base_dir": base_dir}
    if content is not None:
        a["content"] = content
    return a


# ── T-DESK-01 ──

class TestDesktopNoteCreate:
    """T-DESK-01：create 成功，manifest 含 before(None)/after/path/hash。"""

    def test_create_success_manifest_fields(self, tmp_path: Path) -> None:
        result = execute_desktop_note(
            _args("create", "hello.txt", str(tmp_path), "你好，夏目"),
            _ctx(),
        )
        assert result["status"] == STATUS_EXECUTED
        m = result["manifest"]
        assert m["action"] == "create"
        assert m["before_hash"] is None          # 文件原本不存在
        assert m["after_hash"] is not None       # 写入后有 hash
        assert m["path"].endswith("hello.txt")
        assert Path(m["path"]).read_text(encoding="utf-8-sig") == "你好，夏目"

    def test_create_overwrites_existing(self, tmp_path: Path) -> None:
        (tmp_path / "note.txt").write_text("旧内容", encoding="utf-8-sig")
        result = execute_desktop_note(
            _args("create", "note.txt", str(tmp_path), "新内容"),
            _ctx(),
        )
        assert result["status"] == STATUS_EXECUTED
        m = result["manifest"]
        assert m["before_hash"] is not None      # 原文件存在，有 before_hash
        assert m["after_hash"] is not None


# ── T-DESK-02 ──

class TestDesktopNoteUndo:
    """T-DESK-02：undo_token 调用后文件恢复原状态（create → 删除）。"""

    def test_undo_create_removes_file(self, tmp_path: Path) -> None:
        result = execute_desktop_note(
            _args("create", "undo_me.txt", str(tmp_path), "临时内容"),
            _ctx(),
        )
        assert result["status"] == STATUS_EXECUTED
        manifest = result["manifest"]
        assert Path(manifest["path"]).exists()

        undo_result = undo_desktop_note(manifest)
        assert undo_result["status"] == "UNDO_COMPLETED"
        assert not Path(manifest["path"]).exists()

    def test_undo_update_restores_content(self, tmp_path: Path) -> None:
        original = "原始内容"
        (tmp_path / "update_me.txt").write_text(original, encoding="utf-8-sig")

        result = execute_desktop_note(
            _args("update", "update_me.txt", str(tmp_path), "更新后内容"),
            _ctx(),
        )
        assert result["status"] == STATUS_EXECUTED
        manifest = result["manifest"]

        undo_result = undo_desktop_note(manifest, before_content=original)
        assert undo_result["status"] == "UNDO_COMPLETED"
        assert Path(manifest["path"]).read_text(encoding="utf-8-sig") == original


# ── T-DESK-03 ──

class TestDesktopNotePathTraversal:
    """T-DESK-03：路径越界（含 ..）→ SCOPE_VIOLATION。"""

    def test_dotdot_in_filename(self, tmp_path: Path) -> None:
        result = execute_desktop_note(
            _args("create", "../evil.txt", str(tmp_path), "恶意内容"),
            _ctx(),
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == SCOPE_VIOLATION

    def test_absolute_path_as_filename(self, tmp_path: Path) -> None:
        # 绝对路径作为 filename 会被路径穿越检测拦截（含 / 或 \）
        result = execute_desktop_note(
            _args("create", "/etc/passwd", str(tmp_path), "恶意内容"),
            _ctx(),
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == SCOPE_VIOLATION

    def test_nested_dotdot(self, tmp_path: Path) -> None:
        result = execute_desktop_note(
            _args("create", "subdir/../../evil.txt", str(tmp_path), "恶意内容"),
            _ctx(),
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == SCOPE_VIOLATION


# ── T-DESK-04 ──

class TestDesktopNoteGamingBlock:
    """T-DESK-04：Gaming 模式 → SYSMODE_FORBIDDEN。"""

    def test_gaming_blocks_create(self, tmp_path: Path) -> None:
        result = execute_desktop_note(
            _args("create", "note.txt", str(tmp_path), "内容"),
            _ctx(sys_mode="gaming"),
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == SYSMODE_FORBIDDEN

    def test_normal_mode_allowed(self, tmp_path: Path) -> None:
        result = execute_desktop_note(
            _args("create", "note.txt", str(tmp_path), "内容"),
            _ctx(sys_mode="normal"),
        )
        assert result["status"] == STATUS_EXECUTED


# ── T-DESK-05 ──

class TestDesktopNoteFileError:
    """T-DESK-05：文件系统不可写 → DESKTOP_NOTE_FILE_ERROR + reason_code。"""

    def test_io_error_triggers_file_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """通过 monkeypatch 模拟 IO 错误（跨平台，不依赖 chmod）。"""
        from pathlib import Path as _Path
        original_write = _Path.write_text

        def _bad_write(self, *args, **kwargs):
            raise PermissionError("Permission denied (simulated)")

        monkeypatch.setattr(_Path, "write_text", _bad_write)
        result = execute_desktop_note(
            _args("create", "note.txt", str(tmp_path), "内容"),
            _ctx(),
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == DESKTOP_NOTE_FILE_ERROR

    def test_update_nonexistent_file(self, tmp_path: Path) -> None:
        result = execute_desktop_note(
            _args("update", "nonexistent.txt", str(tmp_path), "内容"),
            _ctx(),
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == "DESKTOP_NOTE_FILE_NOT_FOUND"


# ── T-DESK-06 ──

class TestDesktopNoteQABlock:
    """T-DESK-06：内容含伪执行句 → QA_CONTENT_BLOCKED。"""

    def test_qa_checker_blocks_content(self, tmp_path: Path) -> None:
        def _qa_checker(content: str) -> dict:
            if "执行" in content or "已完成" in content:
                return {"passed": False, "detail": "content contains execution claim"}
            return {"passed": True}

        result = execute_desktop_note(
            _args("create", "note.txt", str(tmp_path), "任务已完成，请确认"),
            _ctx(qa_checker=_qa_checker),
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == QA_CONTENT_BLOCKED

    def test_qa_checker_passes_clean_content(self, tmp_path: Path) -> None:
        def _qa_checker(content: str) -> dict:
            return {"passed": True}

        result = execute_desktop_note(
            _args("create", "note.txt", str(tmp_path), "今天天气不错"),
            _ctx(qa_checker=_qa_checker),
        )
        assert result["status"] == STATUS_EXECUTED


# ── T-DESK-07 ──

class TestDesktopNoteDeleteQuarantine:
    """T-DESK-07：delete 默认 move-to-quarantine，undo 可恢复。"""

    def test_delete_moves_to_quarantine(self, tmp_path: Path) -> None:
        (tmp_path / "del_me.txt").write_text("要删除的内容", encoding="utf-8-sig")
        result = execute_desktop_note(
            _args("delete", "del_me.txt", str(tmp_path)),
            _ctx(),
        )
        assert result["status"] == STATUS_EXECUTED
        # 原文件不存在
        assert not (tmp_path / "del_me.txt").exists()
        # quarantine 目录存在
        quarantine_dir = tmp_path / ".quarantine"
        assert quarantine_dir.exists()
        # quarantine 中有文件
        quarantine_files = list(quarantine_dir.glob("del_me.txt.*"))
        assert len(quarantine_files) == 1

    def test_delete_undo_restores_file(self, tmp_path: Path) -> None:
        original_content = "可恢复的内容"
        (tmp_path / "restore_me.txt").write_text(original_content, encoding="utf-8-sig")

        result = execute_desktop_note(
            _args("delete", "restore_me.txt", str(tmp_path)),
            _ctx(),
        )
        assert result["status"] == STATUS_EXECUTED
        manifest = result["manifest"]

        undo_result = undo_desktop_note(manifest)
        assert undo_result["status"] == "UNDO_COMPLETED"
        restored = tmp_path / "restore_me.txt"
        assert restored.exists()
        assert restored.read_text(encoding="utf-8-sig") == original_content


# ── T-DESK-08 ──

class TestDesktopNoteAuditTrail:
    """T-DESK-08：写入审计可追溯原始草案与最终内容（manifest 字段完整）。"""

    def test_manifest_contains_audit_fields(self, tmp_path: Path) -> None:
        result = execute_desktop_note(
            _args("create", "audit.txt", str(tmp_path), "审计内容"),
            _ctx(request_id="req-audit-001", intent_id="intent-audit-001"),
        )
        assert result["status"] == STATUS_EXECUTED
        assert result["request_id"] == "req-audit-001"
        assert result["intent_id"] == "intent-audit-001"
        assert result["event_id"] is not None
        assert result["ts_ms"] > 0
        m = result["manifest"]
        assert "before_hash" in m
        assert "after_hash" in m
        assert "path" in m
        assert "action" in m

    def test_manifest_before_after_hash_differ(self, tmp_path: Path) -> None:
        (tmp_path / "track.txt").write_text("原始内容", encoding="utf-8-sig")
        result = execute_desktop_note(
            _args("update", "track.txt", str(tmp_path), "更新内容"),
            _ctx(),
        )
        assert result["status"] == STATUS_EXECUTED
        m = result["manifest"]
        assert m["before_hash"] != m["after_hash"]
