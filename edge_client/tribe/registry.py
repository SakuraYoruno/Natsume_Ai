from __future__ import annotations

from edge_client.tribe.edge_tribe_bridge import EdgeTribeBridge


def build_edge_tribe_bridge() -> EdgeTribeBridge:
    return EdgeTribeBridge.from_env()


__all__ = ["build_edge_tribe_bridge"]
