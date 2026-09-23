from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_persona_root_keeps_real_submodules_lazy_and_importable() -> None:
    probe = """
import importlib
import sys

import host_brain.persona as persona

assert persona.__all__ == ()
assert "host_brain.persona.proactive_voice" not in sys.modules
assert "host_brain.model_runtime" not in sys.modules

from host_brain.persona import self_model

direct = importlib.import_module("host_brain.persona.self_model")
assert self_model is direct
assert self_model.SelfModel.__module__ == "host_brain.persona.self_model"
assert "host_brain.persona.proactive_voice" not in sys.modules
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
