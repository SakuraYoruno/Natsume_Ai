from __future__ import annotations

from host_brain.app import executive_router, executive_runtime


def test_executive_router_calls_canonical_runtime_entry() -> None:
    """The compatibility router must remain the canonical runtime alias."""
    assert (
        executive_router.executive_router_sse_generator
        is executive_runtime.executive_runtime_sse_generator
    )
