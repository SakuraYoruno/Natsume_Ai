from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.skills import custom_provider_installer as installer


def test_normalize_provider_filename_appends_suffix() -> None:
    assert installer.normalize_provider_filename("demo") == "demo_provider.py"
    assert installer.normalize_provider_filename("demo_provider.py") == "demo_provider.py"


def test_validate_provider_source_requires_export() -> None:
    try:
        installer.validate_provider_source("x = 1", filename="demo_provider.py")
    except ValueError as exc:
        assert "SKILL_PROVIDER_CLASS" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_list_custom_provider_infos_reads_declared_skills(tmp_path: Path, monkeypatch) -> None:
    package_dir = tmp_path / "temp_skill_pkg"
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    provider_file = package_dir / "demo_provider.py"
    provider_file.write_text(
        "\n".join(
            [
                "from host_brain.skills.declarative import DeclarativeSkillProvider, DeclarativeSkillSpec",
                "",
                "class DemoProvider(DeclarativeSkillProvider):",
                "    skill_specs = (",
                "        DeclarativeSkillSpec(",
                "            skill_name='demo_custom_skill',",
                "            clearance='LOW',",
                "            risk_level=1,",
                "            scope='host',",
                "            reversible=True,",
                "            description='demo custom skill',",
                "            tool='demo_custom',",
                "            action='run',",
                "        ),",
                "    )",
                "",
                "SKILL_PROVIDER_CLASS = DemoProvider",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(installer, "CUSTOM_PROVIDER_DIR", package_dir)
    monkeypatch.setattr(installer, "CUSTOM_PROVIDER_PACKAGE", "temp_skill_pkg")
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()

    infos = installer.list_custom_provider_infos(active_skill_names={"demo_custom_skill"})

    assert len(infos) == 1
    assert infos[0].filename == "demo_provider.py"
    assert infos[0].declared_skill_names == ("demo_custom_skill",)
    assert infos[0].active_skill_names == ("demo_custom_skill",)
    assert infos[0].status == "ACTIVE"
