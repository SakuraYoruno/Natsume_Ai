from __future__ import annotations

import logging

import pytest

from host_brain.skills import provider_catalog
from host_brain.skills.registry import SkillRegistry

def test_arbitrary_spec_is_rejected_before_static_factory() -> None:
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
    with pytest.raises(ValueError, match="SKILL_PROVIDER_NOT_TRUSTED_BUILTIN"):
        untrusted.instantiate()


def test_frozen_builtin_provider_path_still_instantiates_statically() -> None:
    spec = provider_catalog.BUILTIN_SKILL_PROVIDER_SPECS[0]
    provider = spec.instantiate()

    assert type(provider).__name__ == "FileSkillProvider"
    assert type(provider).__module__ == "host_brain.skills.file_registry"
