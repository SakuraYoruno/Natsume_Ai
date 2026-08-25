"""NE-01: prompt intelligence may describe plans but never execute them."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_prompt_intelligence_legacy_runtime_is_physically_retired() -> None:
    """The deleted compatibility module must not return as an execution path."""

    assert not (ROOT / "host_brain" / "app" / "intelligent_runtime.py").exists()
