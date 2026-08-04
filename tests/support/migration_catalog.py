"""Helpers for testing a historical migration against its frozen catalog."""

from __future__ import annotations

from pathlib import Path
import shutil

import host_brain.audit.migration_runner as migration_runner_module


def migration_catalog_through(tmp_path: Path, version: int) -> Path:
    """Copy the immutable SQL prefix through ``version`` into a test catalog."""

    source = Path(migration_runner_module.__file__).with_name("migrations")
    target = tmp_path / f"migrations-through-{int(version):04d}"
    target.mkdir(exist_ok=True)
    for asset in source.glob("*.sql"):
        migration_id = int(asset.name.split("_", 1)[0])
        if migration_id <= int(version):
            shutil.copy2(asset, target / asset.name)
    return target


__all__ = ["migration_catalog_through"]
