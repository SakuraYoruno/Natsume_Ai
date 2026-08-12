"""Edge Tribe 传输图的显式组合权威。"""

from __future__ import annotations

from dataclasses import dataclass

from edge_client.tribe.edge_tribe_bridge import EdgeTribeBridge
from edge_client.tribe.registry import EdgeTribeRegistry


@dataclass(frozen=True, slots=True)
class EdgeTribeComposition:
    """唯一 bridge 及其进程内 Agent 注册表。"""

    bridge: EdgeTribeBridge
    registry: EdgeTribeRegistry


def compose_edge_tribe() -> EdgeTribeComposition:
    """每个 Edge 运行时恰好构造一次 Tribe 对象图。"""

    # bridge 是 Host/Edge 消息边界，registry 中的 agent 只共享该 bridge；禁止在
    # 单个功能模块临时创建第二条传输路径，否则会打乱事件顺序和本地状态归属。
    bridge = EdgeTribeBridge.from_env()
    return EdgeTribeComposition(
        bridge=bridge,
        registry=EdgeTribeRegistry(bridge),
    )


__all__ = ["EdgeTribeComposition", "compose_edge_tribe"]
