from __future__ import annotations

from typing import Any


OFFLINE_FIXTURE_EVAL_LAYER = "offline_fixture_eval"
SURFACE_LOW_LEVEL_ENTRYPOINT_ALLOWED = True


def surface_tool_contract_payload(tool_name: str) -> dict[str, Any]:
    return {
        "surface_tool": str(tool_name or "").strip(),
        "surface_tool_layer": OFFLINE_FIXTURE_EVAL_LAYER,
        "surface_main_chain": False,
        "surface_low_level_builder_allowed": SURFACE_LOW_LEVEL_ENTRYPOINT_ALLOWED,
        "surface_runtime_boundary": "fixture_payload_only",
    }


__all__ = [
    "OFFLINE_FIXTURE_EVAL_LAYER",
    "SURFACE_LOW_LEVEL_ENTRYPOINT_ALLOWED",
    "surface_tool_contract_payload",
]
