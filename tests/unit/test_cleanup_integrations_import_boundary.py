from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_integrations_root_keeps_real_submodules_lazy_and_importable() -> None:
    probe = """
import importlib
import sys

import host_brain.integrations as integrations

assert integrations.__all__ == ()
assert "host_brain.integrations.llm_gateway" not in sys.modules
assert "host_brain.audit.sqlite_ledger" not in sys.modules

from host_brain.integrations import llm_helpers

direct = importlib.import_module("host_brain.integrations.llm_helpers")
assert llm_helpers is direct
assert llm_helpers.__name__ == "host_brain.integrations.llm_helpers"
assert "host_brain.integrations.llm_gateway" not in sys.modules
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
