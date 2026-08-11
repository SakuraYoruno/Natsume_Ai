from __future__ import annotations

import host_brain.tribe.glymphatic_agent as glymphatic_mod
import host_brain.tribe.interoception_agent as interoception_mod
import host_brain.tribe.limbic_agent as limbic_mod
import pytest
from host_brain.tribe.event_bus import get_tribe_event_bus
from host_brain.tribe.registry import get_host_tribe_registry
from host_brain.tribe.tribe_state import get_global_registry


@pytest.mark.parametrize(
    ("module", "backing_name", "getter_name"),
    (
        (limbic_mod, "_LIMBIC_AGENT", "get_limbic_agent"),
        (
            interoception_mod,
            "_INTEROCEPTION_AGENT",
            "get_interoception_agent",
        ),
        (
            glymphatic_mod,
            "_GLYMPHATIC_AGENT",
            "get_glymphatic_agent",
        ),
    ),
)
def test_legacy_agent_getter_has_no_process_singleton_fallback(
    module,
    backing_name: str,
    getter_name: str,
) -> None:
    assert not hasattr(module, backing_name)
    assert not hasattr(
        module,
        getter_name.replace("get_", "set_", 1),
    )
    with pytest.raises(
        RuntimeError,
        match="TRIBE_RUNTIME_CONTEXT_NOT_BOUND",
    ):
        getattr(module, getter_name)()


@pytest.mark.parametrize(
    "getter",
    (
        get_global_registry,
        get_host_tribe_registry,
        get_tribe_event_bus,
    ),
)
def test_shared_tribe_runtime_getter_has_no_process_fallback(getter) -> None:
    with pytest.raises(
        RuntimeError,
        match="TRIBE_RUNTIME_CONTEXT_NOT_BOUND",
    ):
        getter()
