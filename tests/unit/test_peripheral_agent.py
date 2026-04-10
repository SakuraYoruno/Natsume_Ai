from __future__ import annotations

import asyncio

from host_brain.tribe.peripheral_agent import PeripheralAgent
from host_brain.tribe.tribe_state import TribeStateRegistry


class _Event:
    def __init__(self, payload: dict, ts_ms: int = 0) -> None:
        self.payload = payload
        self.ts_ms = ts_ms


def _run(coro):
    return asyncio.run(coro)


def test_peripheral_agent_state_snapshot_and_export() -> None:
    agent = PeripheralAgent(state_registry=TribeStateRegistry())

    _run(
        agent._on_peripheral_state_updated(
            _Event(
                payload={
                    "connected_platforms": ["wechat", "telegram"],
                    "gate_state": {"wechat": "open"},
                    "mute_state": {"wechat": False},
                    "error": None,
                },
                ts_ms=12345,
            )
        )
    )

    exported = agent.export_runtime_state()
    assert sorted(exported["connected_platforms"]) == ["telegram", "wechat"]
    assert exported["gate_state"]["wechat"] == "open"
    assert exported["mute_state"]["wechat"] is False
    assert exported["last_edge_peripheral_report_ts_ms"] == 12345


def test_peripheral_agent_platform_connection_updates_snapshot() -> None:
    agent = PeripheralAgent(state_registry=TribeStateRegistry())

    _run(agent._on_platform_connected(_Event(payload={"platform": "wechat"}, ts_ms=101)))
    _run(agent._on_platform_connected(_Event(payload={"platform": "telegram"}, ts_ms=102)))
    _run(agent._on_platform_disconnected(_Event(payload={"platform": "wechat"}, ts_ms=103)))

    exported = agent.export_runtime_state()
    assert exported["connected_platforms"] == ["telegram"]
    assert exported["last_platform_event"] == "disconnected"
    assert exported["last_connected_ts_ms"] == 102
    assert exported["last_disconnected_ts_ms"] == 103
