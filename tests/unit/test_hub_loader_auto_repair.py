from __future__ import annotations

from pathlib import Path
from host_brain.skills.hub import hub_loader
from host_brain.skills.registry import SkillRegistry


def test_load_executor_never_imports_or_auto_repairs(monkeypatch, tmp_path: Path) -> None:
    skill_dir = tmp_path / "demo-skill"
    skill_dir.mkdir()
    executor_file = skill_dir / "executor.py"
    sentinel = tmp_path / "executed.txt"
    executor_file.write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('executed')\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("HOST_SKILL_AUTO_REPAIR_ENABLED", "true")

    fn = hub_loader._load_custom_executor(skill_dir, tool_name="demo_tool")

    assert fn is None
    assert not sentinel.exists()


def test_staged_unsigned_skill_never_registers_or_executes(monkeypatch, tmp_path: Path) -> None:
    skill_dir = tmp_path / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: demo-skill\n"
        "version: 1.0.0\n"
        "description: demo\n"
        "clearance: LOW\n"
        "risk_level: 1\n"
        "scope: host\n"
        "tool: demo_tool\n"
        "---\n",
        encoding="utf-8",
    )
    sentinel = tmp_path / "executed.txt"
    (skill_dir / "executor.py").write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('executed')\n",
        encoding="utf-8",
    )
    registry = SkillRegistry()

    monkeypatch.setenv("HOST_SUPPLY_CHAIN_ACTIVATION_MODE", "STAGED")
    monkeypatch.setenv("HOST_SKILL_AUTO_REPAIR_ENABLED", "true")
    monkeypatch.delenv("HOST_SUPPLY_CHAIN_HMAC_KEYS_JSON", raising=False)

    result = hub_loader.load_hub_skills(registry, skills_dir=tmp_path)

    assert result.registered_count == 0
    assert result.activation_eligible_count == 0
    assert result.error_count == 1
    assert result.skills[0].reason_code == "SUPPLY_CHAIN_SIGNING_KEYS_UNAVAILABLE"
    assert registry.list_skill_names() == []
    assert not sentinel.exists()


def test_builtins_only_returns_before_scan(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("HOST_SUPPLY_CHAIN_ACTIVATION_MODE", raising=False)
    monkeypatch.setattr(
        hub_loader,
        "_scan_single_dir",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not scan")),
    )

    result = hub_loader.load_hub_skills(SkillRegistry(), skills_dir=tmp_path / "missing")

    assert result.activation_mode == "BUILTINS_ONLY"
    assert result.total_dirs_scanned == 0
    assert result.registered_count == 0
    assert result.reason_code == "SUPPLY_CHAIN_EXTERNAL_BUNDLES_DISABLED"
