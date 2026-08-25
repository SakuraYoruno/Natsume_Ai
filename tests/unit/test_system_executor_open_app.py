from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from host_brain.skills.impl import system_executor
from host_brain.skills.registry import SkillRegistry
from host_brain.skills.system_registry import SystemSkillProvider


def test_system_skill_registry_contains_open_app() -> None:
    registry = SkillRegistry()
    registry.register_decorated(SystemSkillProvider())

    meta = registry.require("open_app")

    assert meta.skill_name == "open_app"
    assert meta.family == "system"
    assert "desktop" in meta.capability_tags


def test_execute_open_app_launches_explicit_path(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "酷狗音乐.lnk"
    target.write_text("shortcut", encoding="utf-8")
    launched: list[str] = []

    monkeypatch.setattr(system_executor.os, "startfile", lambda value: launched.append(str(value)), raising=False)

    result = system_executor.execute_system_skill(
        {
            "action": "open_app",
            "path": str(target),
            "app_name": "酷狗音乐",
        }
    )

    assert result["status"] == "OK"
    assert result["detail"]["success"] is True
    assert result["detail"]["launch_mode"] == "path"
    assert launched == [str(target)]


def test_execute_open_app_falls_back_to_app_id(monkeypatch) -> None:
    launches: list[list[str]] = []

    monkeypatch.setattr(system_executor, "_resolve_open_app_target", lambda payload: ("app_id", "kugou"))
    monkeypatch.setattr(
        system_executor.subprocess,
        "Popen",
        lambda command, **kwargs: launches.append(list(command)) or SimpleNamespace(pid=3210),
    )

    result = system_executor.execute_system_skill(
        {
            "action": "open_app",
            "app_name": "酷狗音乐",
        }
    )

    assert result["status"] == "OK"
    assert result["detail"]["success"] is True
    assert result["detail"]["launch_mode"] == "app_id"
    assert result["detail"]["target"] == "kugou"
    assert launches
    assert "shell:AppsFolder\\\\kugou" in launches[0][-1]


def test_execute_open_app_resolves_fuzzy_kugou_phrase(monkeypatch) -> None:
    launches: list[str] = []

    monkeypatch.setattr(system_executor.os, "startfile", lambda value: launches.append(str(value)), raising=False)
    monkeypatch.setattr(
        system_executor,
        "_START_MENU_DIRS",
        (),
    )
    monkeypatch.setattr(
        system_executor,
        "_launch_windows_shell_app_id",
        lambda app_id: {"success": True, "launch_mode": "app_id", "target": app_id, "message": "已经替你打开了。"},
    )

    result = system_executor.execute_system_skill(
        {
            "action": "open_app",
            "app_name": "打开酷狗音乐",
            "query": "可以帮我打开本地的酷狗音乐吗",
        }
    )

    assert result["status"] == "OK"
    assert result["detail"]["target"] == "kugou"
    assert result["detail"]["app_name"] == "酷狗音乐"


def test_execute_open_app_path_uses_short_display_name_when_app_name_missing(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "酷狗音乐.lnk"
    target.write_text("shortcut", encoding="utf-8")
    launched: list[str] = []

    monkeypatch.setattr(system_executor.os, "startfile", lambda value: launched.append(str(value)), raising=False)

    result = system_executor.execute_system_skill(
        {
            "action": "open_app",
            "path": str(target),
            "query": "可以帮我打开本地的酷狗音乐吗",
        }
    )

    assert result["status"] == "OK"
    assert result["detail"]["success"] is True
    assert result["detail"]["app_name"] == "酷狗音乐"
    assert launched == [str(target)]


def test_execute_open_app_failure_surfaces_failed_status(monkeypatch) -> None:
    monkeypatch.setattr(system_executor, "_resolve_open_app_target", lambda payload: (_ for _ in ()).throw(FileNotFoundError("missing")))

    result = system_executor.execute_system_skill(
        {
            "action": "open_app",
            "app_name": "不存在的应用",
        }
    )

    assert result["status"] == "FAILED"
    assert result["reason_code"] == "APP_LAUNCH_FAILED"
    assert result["detail"]["success"] is False
