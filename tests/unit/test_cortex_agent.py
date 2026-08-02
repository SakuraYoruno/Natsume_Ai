from __future__ import annotations

import asyncio
import inspect

import pytest

from host_brain.tribe.application_ports import (
    CANONICAL_AFFECT_LIFE_JOURNAL,
    TribeApplicationPorts,
)
from host_brain.tribe.cortex_agent import CortexAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


@pytest.mark.parametrize(
    "ports",
    (
        TribeApplicationPorts(),
        TribeApplicationPorts(
            affective_life_authority=CANONICAL_AFFECT_LIFE_JOURNAL,
        ),
    ),
)
def test_cortex_bond_post_process_is_retired_in_every_composition(
    ports: TribeApplicationPorts,
) -> None:
    result = asyncio.run(
        CortexAgent(
            application_ports=ports,
            state_registry=TribeStateRegistry(),
        ).post_process_bond_update(
            request_id="req-retired-bond",
            session_id="session-retired-bond",
            user_message="今天想和你多说一会。",
            emotion_context={"emotion_tag": "HAPPY", "emotion_intensity": 0.7},
            ts_ms=789,
        )
    )

    assert result == {
        "status": "RETIRED_CANONICAL_LIFE",
        "authority": "CANONICAL_LIFE_JOURNAL",
        "reason_code": "LEGACY_BOND_WRITE_RETIRED",
    }


def test_cortex_has_no_bond_queue_or_writer_fallback_source() -> None:
    source = inspect.getsource(CortexAgent.post_process_bond_update)

    assert "BondDynamicsWriter" not in source
    assert "bond_update_queue" not in source
    assert ".enqueue(" not in source
    assert "apply_update" not in source
