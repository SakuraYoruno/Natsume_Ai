from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from host_brain.tribe.ans_agent import ANSAgent


pytestmark = pytest.mark.usefixtures("tribe_runtime_context")


def test_ans_agent_dedupes_repeated_glymphatic_cycle_logs(monkeypatch) -> None:
    agent = ANSAgent()
    captured: list[tuple[bool, float]] = []

    monkeypatch.setattr(
        "host_brain.tribe.ans_agent.logger.info",
        lambda message, executed, score: captured.append((bool(executed), float(score))),
    )

    event = SimpleNamespace(payload={"executed": True, "consolidation_score": 0.741}, ts_ms=1_000)
    asyncio.run(agent._on_glymphatic_cycle(event))
    asyncio.run(agent._on_glymphatic_cycle(event))

    later = SimpleNamespace(payload={"executed": True, "consolidation_score": 0.741}, ts_ms=32_000)
    asyncio.run(agent._on_glymphatic_cycle(later))

    assert captured == [(True, 0.741), (True, 0.741)]
