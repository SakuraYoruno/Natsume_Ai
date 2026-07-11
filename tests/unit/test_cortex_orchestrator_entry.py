from __future__ import annotations

from host_brain.app import agent_runtime_shared, cortex_orchestrator


def test_cortex_orchestrator_calls_canonical_runtime_impl() -> None:
    """The public facade is an intentional direct alias, not a second runtime."""
    assert (
        cortex_orchestrator.cortex_orchestrator_sse_generator
        is agent_runtime_shared._cortex_orchestrator_sse_generator_impl
    )
