"""Fail-closed compatibility facade for retired external Skill auto-repair.

The legacy implementation installed attacker-controlled Python/npm dependency
material directly into the Host environment and inherited Host secrets,
network and process authority.  TASK-0490 keeps the callable for compatibility
but removes every subprocess/package-manager path.  A future sandbox worker may
provide an explicitly governed replacement; Host must never repair bundles in
process.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def repair_skill_loading_failure(
    *,
    skill_dir: Path,
    exc: BaseException | str,
) -> dict[str, Any]:
    """Return a stable rejection without reading or executing bundle files."""

    del skill_dir, exc
    return {
        "success": False,
        "actions": [],
        "missing_modules": [],
        "requirements_used": False,
        "package_json_used": False,
        "reason_code": "SKILL_SUPPLY_CHAIN_AUTO_REPAIR_DISABLED",
    }


__all__ = ["repair_skill_loading_failure"]
