from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HOST_BRAIN = ROOT / "host_brain"

_LOW_LEVEL_SURFACE_FILES = {
    Path("host_brain/persona/isolated_surface_harness.py"),
    Path("host_brain/persona/isolated_surface_continuity.py"),
    Path("host_brain/persona/surface_packet_compiler.py"),
    Path("host_brain/persona/surface_runtime.py"),
}
_BUILD_INPUT_CALL_RE = re.compile(r"\bbuild_isolated_surface_input\s*\(")
_PACKET_CONSTRUCTOR_RE = re.compile(r"\bReplyControlPacket\s*\(")


def _python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _direct_call_files(pattern: re.Pattern[str]) -> set[Path]:
    files: set[Path] = set()
    for path in _python_files(HOST_BRAIN):
        text = path.read_text(encoding="utf-8")
        if pattern.search(text):
            files.add(path.relative_to(ROOT))
    return files


def test_surface_main_chain_direct_low_level_input_builders_are_allowlisted() -> None:
    direct_files = _direct_call_files(_BUILD_INPUT_CALL_RE)

    assert direct_files <= _LOW_LEVEL_SURFACE_FILES


def test_surface_main_chain_direct_reply_control_packet_constructors_are_allowlisted() -> None:
    direct_files = _direct_call_files(_PACKET_CONSTRUCTOR_RE)

    assert direct_files <= _LOW_LEVEL_SURFACE_FILES
