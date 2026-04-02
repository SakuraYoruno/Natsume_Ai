from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from host_brain.skills.hub import hub_loader
from host_brain.skills.registry import SkillMeta, SkillRegistry


def test_load_executor_function_auto_repairs_and_retries(monkeypatch, tmp_path: Path) -> None:
    skill_dir = tmp_path / "demo-skill"
    skill_dir.mkdir()
    executor_file = skill_dir / "executor.py"
    executor_file.write_text("def execute(payload):\n    return payload\n", encoding="utf-8")

    calls = {"count": 0}

    class _FakeLoader:
        def exec_module(self, module):
            calls["count"] += 1
            if calls["count"] == 1:
                raise ModuleNotFoundError("No module named 'demo_dep'")
            module.execute = lambda payload: {"ok": True, **payload}

    monkeypatch.setenv("HOST_SKILL_AUTO_REPAIR_ENABLED", "true")
    monkeypatch.setattr(hub_loader.importlib.util, "spec_from_file_location", lambda module_name, file_path: SimpleNamespace(loader=_FakeLoader()))
    monkeypatch.setattr(hub_loader.importlib.util, "module_from_spec", lambda spec: SimpleNamespace())
    monkeypatch.setattr(hub_loader, "repair_skill_loading_failure", lambda skill_dir, exc: {"success": True, "actions": [{"ok": True}]})

    fn = hub_loader._load_custom_executor(skill_dir, tool_name="demo_tool")

    assert callable(fn)
    assert calls["count"] == 2
    assert fn({"x": 1})["ok"] is True


def test_load_hub_skill_auto_repairs_registration_failure(monkeypatch, tmp_path: Path) -> None:
    skill_dir = tmp_path / "demo-skill"
    skill_dir.mkdir()
    manifest = SimpleNamespace(
        name="demo_skill",
        version="1.0.0",
        description="demo",
        clearance="LOW",
        risk_level=1,
        scope="host",
        reversible=True,
        require_confirm=False,
        tool="demo_tool",
        action="run",
        validate=lambda: [],
    )
    registry = SkillRegistry()

    calls = {"provider": 0}

    class _Provider:
        def __skill_meta_entries__(self):
            return [
                SkillMeta(
                    skill_name="demo_skill",
                    clearance="LOW",
                    risk_level=1,
                    scope="host",
                    reversible=True,
                    handler=lambda args, ctx: {"ok": True, **dict(args or {})},
                    require_confirm=False,
                    description="demo",
                )
            ]

    def fake_load_custom_provider(_skill_dir):
        calls["provider"] += 1
        if calls["provider"] == 1:
            raise ModuleNotFoundError("No module named 'demo_dep'")
        return _Provider()

    monkeypatch.setenv("HOST_SKILL_AUTO_REPAIR_ENABLED", "true")
    monkeypatch.setattr(hub_loader, "parse_skill_md", lambda path: manifest)
    monkeypatch.setattr(hub_loader, "_load_custom_executor", lambda *args, **kwargs: None)
    monkeypatch.setattr(hub_loader, "_load_custom_provider", fake_load_custom_provider)
    monkeypatch.setattr(hub_loader, "repair_skill_loading_failure", lambda skill_dir, exc: {"success": True, "actions": [{"ok": True}]})

    info = hub_loader._load_single_skill(skill_dir=skill_dir, skill_md=skill_dir / "SKILL.md", registry=registry, executor_registry=None)

    assert info.status == "REGISTERED"
    assert calls["provider"] == 2
