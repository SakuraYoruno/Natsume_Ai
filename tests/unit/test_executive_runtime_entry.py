from __future__ import annotations

import asyncio
import inspect

import pytest

from host_brain.app import executive_runtime
from host_brain.executive_runtime import runtime


def test_executive_runtime_app_entry_has_no_composition_port() -> None:
    """The compatibility entry may reject, but must not rebuild an app graph."""

    app_parameters = inspect.signature(
        executive_runtime.executive_runtime_sse_generator
    ).parameters
    core_parameters = inspect.signature(runtime._executive_runtime_core_impl).parameters

    assert "ports" not in app_parameters
    assert core_parameters["ports"].default is inspect.Parameter.empty
    source = inspect.getsource(executive_runtime)
    assert "build_executive_runtime_application_ports" not in source
    assert "runtime_port_wiring" not in source


def test_executive_runtime_app_entry_rejects_canonical_route() -> None:
    async def _collect() -> list[str]:
        return [
            event
            async for event in executive_runtime.executive_runtime_sse_generator(
                request_id="req-retired-entry",
                intent_draft={"route": "chat"},
            )
        ]

    with pytest.raises(
        PermissionError,
        match="CANONICAL_ROUTE_REQUIRES_GOVERNED_TASK_RUNTIME",
    ):
        asyncio.run(_collect())
