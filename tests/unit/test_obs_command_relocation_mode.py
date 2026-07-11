from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from host_brain.observability.command_relocation import (
    DEFAULT_OBS_COMMAND_RELOCATION_MODE,
    FAIL_CLOSED_OBS_COMMAND_RELOCATION_MODE,
    OBS_COMMAND_RELOCATION_MODE_ENV,
    ObsCommandRelocationConfig,
    ObsCommandRelocationMode,
    load_obs_command_relocation_config,
    load_obs_command_relocation_mode,
)


def test_obs_command_relocation_mode_contract_is_frozen() -> None:
    assert tuple(mode.value for mode in ObsCommandRelocationMode) == (
        "LEGACY_AUDIT",
        "SHADOW",
        "QUERY_ONLY",
    )
    assert DEFAULT_OBS_COMMAND_RELOCATION_MODE is ObsCommandRelocationMode.LEGACY_AUDIT
    assert FAIL_CLOSED_OBS_COMMAND_RELOCATION_MODE is ObsCommandRelocationMode.QUERY_ONLY


def test_obs_command_relocation_mode_defaults_only_when_variable_is_absent() -> None:
    assert load_obs_command_relocation_mode({}) is ObsCommandRelocationMode.LEGACY_AUDIT

    config = load_obs_command_relocation_config({})
    assert config == ObsCommandRelocationConfig(
        mode=ObsCommandRelocationMode.LEGACY_AUDIT,
        configured=False,
        valid=True,
        raw=None,
    )


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("LEGACY_AUDIT", ObsCommandRelocationMode.LEGACY_AUDIT),
        (" shadow ", ObsCommandRelocationMode.SHADOW),
        ("query_only", ObsCommandRelocationMode.QUERY_ONLY),
    ],
)
def test_obs_command_relocation_mode_accepts_only_registered_values(
    configured: str,
    expected: ObsCommandRelocationMode,
) -> None:
    env = {OBS_COMMAND_RELOCATION_MODE_ENV: configured}
    assert load_obs_command_relocation_mode(env) is expected
    assert load_obs_command_relocation_config(env) == ObsCommandRelocationConfig(
        mode=expected,
        configured=True,
        valid=True,
        raw=configured,
    )


@pytest.mark.parametrize("configured", ["", "   ", "legacy", "off", "enforce", "invalid"])
def test_obs_command_relocation_mode_invalid_value_fails_closed_to_query_only(
    configured: str,
) -> None:
    env = {OBS_COMMAND_RELOCATION_MODE_ENV: configured}
    assert load_obs_command_relocation_mode(env) is ObsCommandRelocationMode.QUERY_ONLY
    assert load_obs_command_relocation_config(env) == ObsCommandRelocationConfig(
        mode=ObsCommandRelocationMode.QUERY_ONLY,
        configured=True,
        valid=False,
        raw=configured,
    )


def test_obs_command_relocation_config_is_frozen() -> None:
    config = load_obs_command_relocation_config(
        {OBS_COMMAND_RELOCATION_MODE_ENV: "SHADOW"}
    )

    with pytest.raises(FrozenInstanceError):
        config.valid = False  # type: ignore[misc]
