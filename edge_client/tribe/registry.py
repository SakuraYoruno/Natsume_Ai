from __future__ import annotations

from typing import Any

from edge_client.tribe.edge_tribe_bridge import EdgeTribeBridge
from edge_client.tribe.somatic_agent import SomaticAgent
from edge_client.tribe.peripheral_agent import PeripheralAgent
from edge_client.tribe.channel_agent import ChannelAgent
from edge_client.tribe.voice_agent import VoiceAgent


class EdgeTribeRegistry:
    """Process-local Edge Tribe registry with an injected bridge.

    The registry owns the four Edge agents, but it never constructs or locates
    their transport authority.  The Edge runtime composition root injects the
    one process bridge explicitly.

    统一管理 EdgeTribeBridge 和四个躯干族 Agent 的生命周期：
    - SomaticAgent（躯体感知）
    - PeripheralAgent（外周神经）
    - ChannelAgent（传入神经束）
    - VoiceAgent（声音系统）

    使用方式：
        registry = EdgeTribeRegistry(bridge)
        somatic = registry.somatic
    """

    def __init__(self, bridge: EdgeTribeBridge) -> None:
        if not isinstance(bridge, EdgeTribeBridge):
            raise TypeError("bridge must be EdgeTribeBridge")
        self._bridge = bridge
        self._somatic = SomaticAgent(bridge=bridge)
        self._peripheral = PeripheralAgent(bridge=bridge)
        self._channel = ChannelAgent(bridge=bridge)
        self._voice = VoiceAgent(bridge=bridge)

    @property
    def bridge(self) -> EdgeTribeBridge:
        return self._bridge

    @property
    def somatic(self) -> SomaticAgent:
        return self._somatic

    @property
    def peripheral(self) -> PeripheralAgent:
        return self._peripheral

    @property
    def channel(self) -> ChannelAgent:
        return self._channel

    @property
    def voice(self) -> VoiceAgent:
        return self._voice

    def describe(self) -> dict[str, Any]:
        """返回注册表状态摘要，供健康检查使用。"""
        return {
            "initialized": True,
            "bridge_enabled": bool(self._bridge.enabled),
            "bridge_host": str(self._bridge.host_base_url),
            "agents": {
                "somatic": self._somatic.describe(),
                "peripheral": self._peripheral.describe(),
                "channel": self._channel.describe(),
                "voice": self._voice.describe(),
            },
        }


__all__ = [
    "EdgeTribeRegistry",
]
