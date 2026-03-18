"""Contract tests for Desktop Note Skill (TASK-0092).

覆盖 T-Desktop-01（文件创建/更新/撤销/manifest）、T-Desktop-02（路径穿越/Gaming拒绝/QA拦截）。
"""

from __future__ import annotations

import json
import pathlib
import tempfile
import pytest

from host_brain.skills.impl.desktop_note import (
    execute_desktop_note,
    undo_desktop_note,
    ACTION_CREATE,
    ACTION_UPDATE,
    ACTION_DELETE,
    STATUS_EXECUTED,
    STATUS_REJECTED,
    SCOPE_VIOLATION,
    QA_CONTENT_BLOCKED,
)
from host_brain.core.errors import SYSMODE_FORBIDDEN

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "desktop"


def _load_fixture(name: str) -> dict:
    path = FIXTURES_DIR / name
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


# ╔══════════════════════════════════════════════════════════════╗
# ║  T-Desktop-01：文件创建/更新/撤销/manifest                    ║
# ╚══════════════════════════════════════════════════════════════╝

class TestDesktopNoteCreate:
    """T-Desktop-01：文件创建与 manifest。"""

    def test_create_file(self, tmp_path: pathlib.Path) -> None:
        result = execute_desktop_note(
            {"action": "create", "filename": "note.txt", "content": "hello world", "base_dir": str(tmp_path)},
            {"sys_mode": "normal"},
        )
        assert result["status"] == STATUS_EXECUTED
        assert result["reason_code"] is None
        assert "manifest" in result
        assert (tmp_path / "note.txt").read_text(encoding="utf-8-sig") == "hello world"
        manifest = result["manifest"]
        assert manifest["action"] == "create"
        assert manifest["before_hash"] is None
        assert manifest["after_hash"] is not None

    def test_create_in_subdirectory(self, tmp_path: pathlib.Path) -> None:
        result = execute_desktop_note(
            {"action": "create", "filename": "sub/note.txt", "content": "sub dir", "base_dir": str(tmp_path)},
            {"sys_mode": "normal"},
        )
        assert result["status"] == STATUS_EXECUTED
        assert (tmp_path / "sub" / "note.txt").read_text(encoding="utf-8-sig") == "sub dir"

    def test_update_file(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "note.txt").write_text("old content", encoding="utf-8-sig")
        result = execute_desktop_note(
            {"action": "update", "filename": "note.txt", "content": "new content", "base_dir": str(tmp_path)},
            {"sys_mode": "normal"},
        )
        assert result["status"] == STATUS_EXECUTED
        assert (tmp_path / "note.txt").read_text(encoding="utf-8-sig") == "new content"
        manifest = result["manifest"]
        assert manifest["before_hash"] is not None
        assert manifest["after_hash"] is not None

    def test_update_nonexistent_file_rejected(self, tmp_path: pathlib.Path) -> None:
        result = execute_desktop_note(
            {"action": "update", "filename": "missing.txt", "content": "x", "base_dir": str(tmp_path)},
            {"sys_mode": "normal"},
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == "DESKTOP_NOTE_FILE_NOT_FOUND"

    def test_delete_file_quarantine(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "to_delete.txt").write_text("bye", encoding="utf-8-sig")
        result = execute_desktop_note(
            {"action": "delete", "filename": "to_delete.txt", "base_dir": str(tmp_path)},
            {"sys_mode": "normal"},
        )
        assert result["status"] == STATUS_EXECUTED
        assert not (tmp_path / "to_delete.txt").exists()
        # 文件应移入 .quarantine
        quarantine = tmp_path / ".quarantine"
        assert quarantine.exists()
        assert len(list(quarantine.iterdir())) == 1

    def test_delete_nonexistent_rejected(self, tmp_path: pathlib.Path) -> None:
        result = execute_desktop_note(
            {"action": "delete", "filename": "missing.txt", "base_dir": str(tmp_path)},
            {"sys_mode": "normal"},
        )
        assert result["status"] == STATUS_REJECTED

    def test_undo_create(self, tmp_path: pathlib.Path) -> None:
        """创建后撤销 → 文件被删除。"""
        result = execute_desktop_note(
            {"action": "create", "filename": "note.txt", "content": "test", "base_dir": str(tmp_path)},
            {"sys_mode": "normal"},
        )
        assert result["status"] == STATUS_EXECUTED
        manifest = result["manifest"]
        undo_result = undo_desktop_note(manifest)
        assert undo_result["status"] == "UNDO_COMPLETED"
        assert not (tmp_path / "note.txt").exists()

    def test_undo_update(self, tmp_path: pathlib.Path) -> None:
        """更新后撤销 → 恢复原内容。"""
        (tmp_path / "note.txt").write_text("original", encoding="utf-8-sig")
        result = execute_desktop_note(
            {"action": "update", "filename": "note.txt", "content": "modified", "base_dir": str(tmp_path)},
            {"sys_mode": "normal"},
        )
        manifest = result["manifest"]
        undo_result = undo_desktop_note(manifest, before_content="original")
        assert undo_result["status"] == "UNDO_COMPLETED"
        assert (tmp_path / "note.txt").read_text(encoding="utf-8-sig") == "original"

    def test_undo_delete(self, tmp_path: pathlib.Path) -> None:
        """删除后撤销 → 文件从 quarantine 恢复。"""
        (tmp_path / "restore.txt").write_text("keep me", encoding="utf-8-sig")
        result = execute_desktop_note(
            {"action": "delete", "filename": "restore.txt", "base_dir": str(tmp_path)},
            {"sys_mode": "normal"},
        )
        manifest = result["manifest"]
        undo_result = undo_desktop_note(manifest)
        assert undo_result["status"] == "UNDO_COMPLETED"
        assert (tmp_path / "restore.txt").exists()
        assert (tmp_path / "restore.txt").read_text(encoding="utf-8-sig") == "keep me"

    def test_fixture_create_success(self, tmp_path: pathlib.Path) -> None:
        fixture = _load_fixture("create_success.json")
        args = dict(fixture["args"])
        args["base_dir"] = str(tmp_path)
        result = execute_desktop_note(args, fixture["context"])
        expected = fixture["expected"]
        assert result["status"] == expected["status"]
        assert result["reason_code"] == expected["reason_code"]


# ╔══════════════════════════════════════════════════════════════╗
# ║  T-Desktop-02：安全约束（路径穿越/Gaming/QA）                  ║
# ╚══════════════════════════════════════════════════════════════╝

class TestDesktopNoteSecurity:
    """T-Desktop-02：安全约束验证。"""

    def test_path_traversal_dotdot_rejected(self, tmp_path: pathlib.Path) -> None:
        result = execute_desktop_note(
            {"action": "create", "filename": "../../etc/passwd", "content": "x", "base_dir": str(tmp_path)},
            {"sys_mode": "normal"},
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == SCOPE_VIOLATION

    def test_path_traversal_single_dotdot(self, tmp_path: pathlib.Path) -> None:
        result = execute_desktop_note(
            {"action": "create", "filename": "../escape.txt", "content": "x", "base_dir": str(tmp_path)},
            {"sys_mode": "normal"},
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == SCOPE_VIOLATION

    def test_symlink_rejected(self, tmp_path: pathlib.Path) -> None:
        """symlink 目标被拒绝。"""
        target = tmp_path / "real.txt"
        target.write_text("real", encoding="utf-8-sig")
        link = tmp_path / "link.txt"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            pytest.skip("symlink not supported on this platform")
        result = execute_desktop_note(
            {"action": "update", "filename": "link.txt", "content": "evil", "base_dir": str(tmp_path)},
            {"sys_mode": "normal"},
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == SCOPE_VIOLATION

    def test_gaming_mode_rejected(self, tmp_path: pathlib.Path) -> None:
        result = execute_desktop_note(
            {"action": "create", "filename": "note.txt", "content": "test", "base_dir": str(tmp_path)},
            {"sys_mode": "gaming"},
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == SYSMODE_FORBIDDEN

    def test_qa_content_blocked(self, tmp_path: pathlib.Path) -> None:
        """QA 检查器拦截内容。"""
        def mock_qa(content: str) -> dict:
            if "已执行" in content:
                return {"passed": False, "detail": "伪执行句被拦截"}
            return {"passed": True}

        result = execute_desktop_note(
            {"action": "create", "filename": "note.txt", "content": "我已执行了操作", "base_dir": str(tmp_path)},
            {"sys_mode": "normal", "qa_checker": mock_qa},
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == QA_CONTENT_BLOCKED

    def test_qa_passes_clean_content(self, tmp_path: pathlib.Path) -> None:
        """QA 检查器放行正常内容。"""
        def mock_qa(content: str) -> dict:
            return {"passed": True}

        result = execute_desktop_note(
            {"action": "create", "filename": "clean.txt", "content": "正常内容", "base_dir": str(tmp_path)},
            {"sys_mode": "normal", "qa_checker": mock_qa},
        )
        assert result["status"] == STATUS_EXECUTED

    def test_missing_base_dir_rejected(self) -> None:
        result = execute_desktop_note(
            {"action": "create", "filename": "note.txt", "content": "test", "base_dir": ""},
            {"sys_mode": "normal"},
        )
        assert result["status"] == STATUS_REJECTED

    def test_invalid_action_rejected(self, tmp_path: pathlib.Path) -> None:
        result = execute_desktop_note(
            {"action": "invalid", "filename": "note.txt", "base_dir": str(tmp_path)},
            {"sys_mode": "normal"},
        )
        assert result["status"] == STATUS_REJECTED

    def test_fixture_path_traversal(self, tmp_path: pathlib.Path) -> None:
        fixture = _load_fixture("path_traversal_reject.json")
        args = dict(fixture["args"])
        args["base_dir"] = str(tmp_path)
        result = execute_desktop_note(args, fixture["context"])
        expected = fixture["expected"]
        assert result["status"] == expected["status"]
        assert result["reason_code"] == expected["reason_code"]

    def test_fixture_gaming_reject(self, tmp_path: pathlib.Path) -> None:
        fixture = _load_fixture("gaming_reject.json")
        args = dict(fixture["args"])
        args["base_dir"] = str(tmp_path)
        result = execute_desktop_note(args, fixture["context"])
        expected = fixture["expected"]
        assert result["status"] == expected["status"]
        assert result["reason_code"] == expected["reason_code"]

    def test_create_missing_content_rejected(self, tmp_path: pathlib.Path) -> None:
        result = execute_desktop_note(
            {"action": "create", "filename": "note.txt", "base_dir": str(tmp_path)},
            {"sys_mode": "normal"},
        )
        assert result["status"] == STATUS_REJECTED
