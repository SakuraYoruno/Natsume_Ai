from __future__ import annotations

import importlib
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.skills import provider_catalog
from host_brain.skills.registry import SkillRegistry


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_discover_custom_provider_paths_from_package_dir(tmp_path: Path, monkeypatch) -> None:
    package_dir = tmp_path / "temp_skill_pkg"
    _write_file(package_dir / "__init__.py", "")
    _write_file(
        package_dir / "demo_provider.py",
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
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()

    discovered = provider_catalog._discover_custom_provider_paths(
        logger=logging.getLogger("test_skill_provider_catalog"),
        custom_dir=package_dir,
        package_name="temp_skill_pkg",
    )

    assert discovered == ("temp_skill_pkg.demo_provider:DemoProvider",)


def test_register_configured_skill_providers_supports_extra_provider_env(tmp_path: Path, monkeypatch) -> None:
    module_path = tmp_path / "extra_provider_mod.py"
    _write_file(
        module_path,
        "\n".join(
            [
                "from host_brain.skills.declarative import DeclarativeSkillProvider, DeclarativeSkillSpec",
                "",
                "def _normalize(args):",
                "    return {'value': str((args or {}).get('value') or '').upper()}",
                "",
                "class ExtraProvider(DeclarativeSkillProvider):",
                "    skill_specs = (",
                "        DeclarativeSkillSpec(",
                "            skill_name='extra_echo',",
                "            clearance='LOW',",
                "            risk_level=1,",
                "            scope='host',",
                "            reversible=True,",
                "            description='extra echo skill',",
                "            tool='extra_echo',",
                "            action='run',",
                "            normalize_args=_normalize,",
                "        ),",
                "    )",
            ]
        ),
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    monkeypatch.setenv(provider_catalog.EXTRA_PROVIDERS_ENV, "extra_provider_mod:ExtraProvider")

    registry = SkillRegistry()

    count = provider_catalog.register_configured_skill_providers(
        registry,
        parse_bool_env=lambda name, default=True: False if name == provider_catalog.CUSTOM_PROVIDERS_ENABLED_ENV else bool(default),
        logger=logging.getLogger("test_skill_provider_catalog"),
        specs=(),
    )

    payload = registry.require("extra_echo").handler({"value": "hello"}, None)

    assert count == 1
    assert payload["tool"] == "extra_echo"
    assert payload["action"] == "run"
    assert payload["value"] == "HELLO"
