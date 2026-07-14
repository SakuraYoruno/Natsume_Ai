from __future__ import annotations

from host_brain.app import cortex_orchestrator
from host_brain.agent_runtime.cortex_runtime import _cortex_orchestrator_sse_generator_impl


def test_cortex_orchestrator_calls_canonical_runtime_impl() -> None:
    """The public entry binds the one composed port set to the canonical runtime."""
    assert cortex_orchestrator._cortex_orchestrator_sse_generator_impl is _cortex_orchestrator_sse_generator_impl
    assert callable(cortex_orchestrator.cortex_orchestrator_sse_generator)
