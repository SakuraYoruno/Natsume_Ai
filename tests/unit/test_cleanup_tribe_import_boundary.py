from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_tribe_root_keeps_real_submodules_lazy_and_importable() -> None:
    probe = """
import importlib
import sys

import host_brain.tribe as tribe

assert tribe.__all__ == ()
assert "host_brain.tribe.thalamus_agent" not in sys.modules
assert "host_brain.model_runtime" not in sys.modules

from host_brain.tribe import application_ports

direct = importlib.import_module("host_brain.tribe.application_ports")
assert application_ports is direct
assert application_ports.__name__ == "host_brain.tribe.application_ports"
assert "host_brain.tribe.thalamus_agent" not in sys.modules
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
