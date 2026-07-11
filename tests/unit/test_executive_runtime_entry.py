from __future__ import annotations

from host_brain.app import executive_runtime, executive_runtime_core


def test_executive_runtime_bridges_runtime_core_impl() -> None:
    """The public entrypoint must remain the single runtime-core alias."""
    assert (
        executive_runtime.executive_runtime_sse_generator
        is executive_runtime_core._executive_runtime_core_impl
    )
