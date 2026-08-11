"""Explicit composition authority for the Edge Tribe transport graph."""

from __future__ import annotations

from dataclasses import dataclass

from edge_client.tribe.edge_tribe_bridge import EdgeTribeBridge
from edge_client.tribe.registry import EdgeTribeRegistry


@dataclass(frozen=True, slots=True)
class EdgeTribeComposition:
    """The one bridge and its process-local Agent registry."""

    bridge: EdgeTribeBridge
    registry: EdgeTribeRegistry


def compose_edge_tribe() -> EdgeTribeComposition:
    """Construct the Edge Tribe object graph exactly once per Edge runtime."""

    bridge = EdgeTribeBridge.from_env()
    return EdgeTribeComposition(
        bridge=bridge,
        registry=EdgeTribeRegistry(bridge),
    )


__all__ = ["EdgeTribeComposition", "compose_edge_tribe"]
