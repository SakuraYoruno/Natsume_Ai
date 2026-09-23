from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_runtime_root_keeps_real_submodules_lazy_and_importable() -> None:
    probe = """
import importlib
import sys

import host_brain.runtime as runtime

assert runtime.__all__ == ()
assert "host_brain.runtime.scene_resolver" not in sys.modules
assert "host_brain.integrations" not in sys.modules

from host_brain.runtime import intent_admission

direct = importlib.import_module("host_brain.runtime.intent_admission")
assert intent_admission is direct
assert intent_admission.__name__ == "host_brain.runtime.intent_admission"
assert "host_brain.runtime.scene_resolver" not in sys.modules
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
