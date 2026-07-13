from __future__ import annotations

import asyncio
import inspect
from dataclasses import FrozenInstanceError

from host_brain.app import executive_runtime
from host_brain.executive_runtime import runtime
from host_brain.executive_runtime.ports import ExecutiveRuntimeApplicationPorts


def test_executive_runtime_bridges_canonical_runtime_impl() -> None:
    """The app entrypoint must delegate to the canonical runtime package."""
    app_parameters = inspect.signature(
        executive_runtime.executive_runtime_sse_generator
    ).parameters
    core_parameters = inspect.signature(runtime._executive_runtime_core_impl).parameters

    assert "ports" not in app_parameters
    assert core_parameters["ports"].default is inspect.Parameter.empty


def test_executive_runtime_app_entry_injects_frozen_typed_ports(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def _fake_core(*, ports, **kwargs):  # noqa: ANN001, ANN003
        captured["ports"] = ports
        captured["kwargs"] = dict(kwargs)
        yield "event"

    monkeypatch.setattr(executive_runtime, "_executive_runtime_core_impl", _fake_core)

    async def _collect() -> list[str]:
        return [
            event
            async for event in executive_runtime.executive_runtime_sse_generator(
                request_id="req-explicit-ports"
            )
        ]

    assert asyncio.run(_collect()) == ["event"]
    ports = captured["ports"]
    assert isinstance(ports, ExecutiveRuntimeApplicationPorts)
    assert captured["kwargs"] == {"request_id": "req-explicit-ports"}
    try:
        ports.get_ledger = lambda: None  # type: ignore[misc]
    except FrozenInstanceError:
        pass
    else:  # pragma: no cover - documents the architectural invariant
        raise AssertionError("Executive Runtime ports must be immutable")
