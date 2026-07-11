"""TASK-0489 feature flag for relocating Obs mutations to Host commands.

The flag only describes the relocation phase.  It does not grant command
authority; execution still requires the TASK-0487 Secure Ingress decision.
Unknown configured values deliberately select the terminal query-only state
instead of falling back to the legacy surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
from typing import Mapping


OBS_COMMAND_RELOCATION_MODE_ENV = "HOST_OBS_COMMAND_RELOCATION_MODE"


class ObsCommandRelocationMode(str, Enum):
    """Registered TASK-0489 migration phases."""

    LEGACY_AUDIT = "LEGACY_AUDIT"
    SHADOW = "SHADOW"
    QUERY_ONLY = "QUERY_ONLY"


DEFAULT_OBS_COMMAND_RELOCATION_MODE = ObsCommandRelocationMode.LEGACY_AUDIT
FAIL_CLOSED_OBS_COMMAND_RELOCATION_MODE = ObsCommandRelocationMode.QUERY_ONLY


@dataclass(frozen=True, slots=True)
class ObsCommandRelocationConfig:
    """Resolved flag state retained for composition and audit decisions.

    ``configured`` distinguishes an absent variable from an explicitly
    configured value.  ``valid`` remains false when a configured value is
    forced to ``QUERY_ONLY`` so composition cannot mistake fail-closed mode
    resolution for valid authorization to enable a command execution path.
    ``raw`` preserves the exact configured text and is ``None`` only when the
    variable was absent.
    """

    mode: ObsCommandRelocationMode
    configured: bool
    valid: bool
    raw: str | None


def load_obs_command_relocation_config(
    env: Mapping[str, str] | None = None,
) -> ObsCommandRelocationConfig:
    """Load the complete relocation flag state without losing validity."""

    source = os.environ if env is None else env
    if OBS_COMMAND_RELOCATION_MODE_ENV not in source:
        return ObsCommandRelocationConfig(
            mode=DEFAULT_OBS_COMMAND_RELOCATION_MODE,
            configured=False,
            valid=True,
            raw=None,
        )

    raw = str(source.get(OBS_COMMAND_RELOCATION_MODE_ENV, ""))
    normalized = raw.strip().upper()
    try:
        mode = ObsCommandRelocationMode(normalized)
    except ValueError:
        return ObsCommandRelocationConfig(
            mode=FAIL_CLOSED_OBS_COMMAND_RELOCATION_MODE,
            configured=True,
            valid=False,
            raw=raw,
        )
    return ObsCommandRelocationConfig(
        mode=mode,
        configured=True,
        valid=True,
        raw=raw,
    )


def load_obs_command_relocation_mode(
    env: Mapping[str, str] | None = None,
) -> ObsCommandRelocationMode:
    """Load the relocation mode with a strict, fail-closed invalid-value path.

    An absent variable preserves the registered ``LEGACY_AUDIT`` rollout
    default.  Once the variable is present, an empty, malformed, or unknown
    value is configuration failure and therefore resolves to ``QUERY_ONLY``.
    Matching is case-insensitive and ignores surrounding whitespace.
    """

    return load_obs_command_relocation_config(env).mode


__all__ = [
    "DEFAULT_OBS_COMMAND_RELOCATION_MODE",
    "FAIL_CLOSED_OBS_COMMAND_RELOCATION_MODE",
    "OBS_COMMAND_RELOCATION_MODE_ENV",
    "ObsCommandRelocationConfig",
    "ObsCommandRelocationMode",
    "load_obs_command_relocation_config",
    "load_obs_command_relocation_mode",
]
