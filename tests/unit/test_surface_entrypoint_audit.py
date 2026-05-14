from __future__ import annotations

import re
from pathlib import Path

from tools.surface_tool_contract import OFFLINE_FIXTURE_EVAL_LAYER, surface_tool_contract_payload


ROOT = Path(__file__).resolve().parents[2]
HOST_BRAIN = ROOT / "host_brain"
TOOLS = ROOT / "tools"

_LOW_LEVEL_SURFACE_FILES = {
    Path("host_brain/persona/isolated_surface_harness.py"),
    Path("host_brain/persona/isolated_surface_continuity.py"),
    Path("host_brain/persona/surface_packet_compiler.py"),
    Path("host_brain/persona/surface_runtime.py"),
}
_OFFLINE_FIXTURE_TOOL_FILES = {
    Path("tools/isolated_surface_harness.py"),
    Path("tools/retry_isolated_surface_failures.py"),
}
_SURFACE_RUNTIME_FACADE_FILES = {
    Path("host_brain/persona/surface_main_chain.py"),
}
_SURFACE_CONTEXT_PACKET_CALLER_FILES = {
    Path("host_brain/app/surface_main_chain_adapter.py"),
}
_BUILD_INPUT_CALL_RE = re.compile(r"\bbuild_isolated_surface_input\s*\(")
_PACKET_CONSTRUCTOR_RE = re.compile(r"\bReplyControlPacket\s*\(")
_SURFACE_RUNTIME_CONSTRUCTOR_RE = re.compile(r"\bSurfaceRuntime\s*\(")
_SURFACE_CONTEXT_PACKET_CALL_RE = re.compile(r"(?<!def )\bbuild_surface_context_packet\s*\(")


def _python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _direct_call_files(root: Path, pattern: re.Pattern[str]) -> set[Path]:
    files: set[Path] = set()
    for path in _python_files(root):
        text = path.read_text(encoding="utf-8")
        if pattern.search(text):
            files.add(path.relative_to(ROOT))
    return files


def test_surface_main_chain_direct_low_level_input_builders_are_allowlisted() -> None:
    direct_files = _direct_call_files(HOST_BRAIN, _BUILD_INPUT_CALL_RE)

    assert direct_files <= _LOW_LEVEL_SURFACE_FILES


def test_surface_main_chain_direct_reply_control_packet_constructors_are_allowlisted() -> None:
    direct_files = _direct_call_files(HOST_BRAIN, _PACKET_CONSTRUCTOR_RE)

    assert direct_files <= _LOW_LEVEL_SURFACE_FILES


def test_surface_runtime_constructor_is_only_used_by_main_chain_facade() -> None:
    direct_files = _direct_call_files(HOST_BRAIN, _SURFACE_RUNTIME_CONSTRUCTOR_RE)

    assert direct_files <= _SURFACE_RUNTIME_FACADE_FILES


def test_surface_context_packet_builder_is_only_called_by_app_adapter() -> None:
    direct_files = _direct_call_files(HOST_BRAIN, _SURFACE_CONTEXT_PACKET_CALL_RE)

    assert direct_files <= _SURFACE_CONTEXT_PACKET_CALLER_FILES


def test_tools_direct_low_level_surface_builders_are_explicit_fixture_tools() -> None:
    direct_files = _direct_call_files(TOOLS, _BUILD_INPUT_CALL_RE)

    assert direct_files <= _OFFLINE_FIXTURE_TOOL_FILES
    for relative_path in direct_files:
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "SURFACE_TOOL_LAYER = OFFLINE_FIXTURE_EVAL_LAYER" in text
        assert "SURFACE_LOW_LEVEL_ENTRYPOINT_ALLOWED = _SURFACE_LOW_LEVEL_ENTRYPOINT_ALLOWED" in text


def test_tools_do_not_construct_reply_control_packets_directly() -> None:
    direct_files = _direct_call_files(TOOLS, _PACKET_CONSTRUCTOR_RE)

    assert direct_files == set()


def test_surface_tool_contract_payload_marks_non_main_chain() -> None:
    payload = surface_tool_contract_payload("probe")

    assert payload["surface_tool"] == "probe"
    assert payload["surface_tool_layer"] == OFFLINE_FIXTURE_EVAL_LAYER
    assert payload["surface_main_chain"] is False
    assert payload["surface_low_level_builder_allowed"] is True
    assert payload["surface_runtime_boundary"] == "fixture_payload_only"
