from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_compatibility_registry_import_avoids_identity_runtime() -> None:
    probe = """
import sys
from pathlib import Path

from host_brain.release.compatibility_registry import (
    ACTIVE_COMPATIBILITY_FACADES,
    RETIRED_CORE_PATHS,
    validate_registry,
    validate_retired_paths_absent,
)

root = Path.cwd()
assert ACTIVE_COMPATIBILITY_FACADES == ()
assert validate_registry(root, require_empty=True)["status"] == "PASS"
assert validate_retired_paths_absent(root, RETIRED_CORE_PATHS)["status"] == "PASS"
assert "host_brain.release.identity_snapshot" not in sys.modules
assert "host_brain.persona" not in sys.modules
assert "host_brain.runtime" not in sys.modules
assert "host_brain.integrations" not in sys.modules
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", probe],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, (
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
