from __future__ import annotations

import importlib
import logging
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from host_brain.skills import provider_catalog
from host_brain.skills.registry import SkillRegistry


def _write_import_marker_module(path: Path, marker: Path, class_name: str) -> None:
    path.write_text(
        "\n".join(
            (
                "from pathlib import Path",
                f"Path({str(marker)!r}).write_text('imported', encoding='utf-8')",
                f"class {class_name}: pass",
                f"SKILL_PROVIDER_CLASS = {class_name}",
            )
        ),
        encoding="utf-8",
    )


def test_custom_provider_discovery_is_a_no_io_no_import_compatibility_seam(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_dir = tmp_path / "temp_skill_pkg"
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    marker = tmp_path / "custom-imported.txt"
    _write_import_marker_module(
        package_dir / "demo_provider.py",
        marker,
        "DemoProvider",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()

    discovered = provider_catalog._discover_custom_provider_paths(
        logger=logging.getLogger("test_skill_provider_catalog"),
        custom_dir=package_dir,
        package_name="temp_skill_pkg",
    )

    assert discovered == ()
    assert marker.exists() is False
    assert "temp_skill_pkg.demo_provider" not in sys.modules


@pytest.mark.parametrize("activation_mode", ["BUILTINS_ONLY", "QUARANTINE", "STAGED"])
def test_external_provider_environment_is_ignored_in_every_activation_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    activation_mode: str,
) -> None:
    marker = tmp_path / f"extra-imported-{activation_mode}.txt"
    _write_import_marker_module(
        tmp_path / "extra_provider_mod.py",
        marker,
        "ExtraProvider",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("HOST_SUPPLY_CHAIN_ACTIVATION_MODE", activation_mode)
    monkeypatch.setenv(provider_catalog.CUSTOM_PROVIDERS_ENABLED_ENV, "1")
    monkeypatch.setenv(
        provider_catalog.EXTRA_PROVIDERS_ENV,
        "extra_provider_mod:ExtraProvider",
    )
    importlib.invalidate_caches()

    registry = SkillRegistry()
    count = provider_catalog.register_configured_skill_providers(
        registry,
        parse_bool_env=lambda _name, default=True: bool(default),
        logger=logging.getLogger("test_skill_provider_catalog"),
        specs=(),
    )

    assert count == 0
    assert registry.get("extra_echo") is None
    assert marker.exists() is False
    assert "extra_provider_mod" not in sys.modules


def test_arbitrary_spec_is_rejected_before_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    imported: list[str] = []

    def _unexpected_import(module_name: str):
        imported.append(module_name)
        raise AssertionError("untrusted module import reached")

    monkeypatch.setattr(provider_catalog.importlib, "import_module", _unexpected_import)
    untrusted = provider_catalog.SkillProviderSpec(
        env_flag="HOST_UNTRUSTED_ENABLED",
        provider_path="attacker.module:Provider",
        log_label="untrusted",
        log_summary="untrusted",
    )

    registry = SkillRegistry()
    count = provider_catalog.register_configured_skill_providers(
        registry,
        parse_bool_env=lambda _name, default=True: bool(default),
        logger=logging.getLogger("test_skill_provider_catalog"),
        specs=(untrusted,),
    )

    assert count == 0
    assert imported == []
    with pytest.raises(ValueError, match="SKILL_PROVIDER_NOT_TRUSTED_BUILTIN"):
        untrusted.instantiate()


def test_frozen_builtin_provider_path_still_instantiates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BuiltinProvider:
        pass

    imported: list[str] = []

    def _trusted_import(module_name: str):
        imported.append(module_name)
        return SimpleNamespace(FileSkillProvider=_BuiltinProvider)

    monkeypatch.setattr(provider_catalog.importlib, "import_module", _trusted_import)
    spec = provider_catalog.BUILTIN_SKILL_PROVIDER_SPECS[0]

    assert isinstance(spec.instantiate(), _BuiltinProvider)
    assert imported == ["host_brain.skills.file_registry"]
