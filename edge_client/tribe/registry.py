from __future__ import annotations

import threading
from typing import Any

from edge_client.tribe.edge_tribe_bridge import EdgeTribeBridge
from edge_client.tribe.somatic_agent import SomaticAgent
from edge_client.tribe.peripheral_agent import PeripheralAgent
from edge_client.tribe.channel_agent import ChannelAgent
from edge_client.tribe.voice_agent import VoiceAgent


class EdgeTribeRegistry:
    """躯干族 Agent 注册表（单例）。

    统一管理 EdgeTribeBridge 和四个躯干族 Agent 的生命周期：
    - SomaticAgent（躯体感知）
    - PeripheralAgent（外周神经）
    - ChannelAgent（传入神经束）
    - VoiceAgent（声音系统）

    使用方式：
        registry = EdgeTribeRegistry.get_instance()
        registry.initialize()  # 从环境变量初始化 bridge 并注入各 Agent
        somatic = registry.somatic
    """

    _instance: EdgeTribeRegistry | None = None
    _lock: threading.Lock = threading.Lock()

    def __init__(self) -> None:
        self._bridge: EdgeTribeBridge | None = None
        self._somatic: SomaticAgent = SomaticAgent()
        self._peripheral: PeripheralAgent = PeripheralAgent()
        self._channel: ChannelAgent = ChannelAgent()
        self._voice: VoiceAgent = VoiceAgent()
        self._initialized: bool = False

    @classmethod
    def get_instance(cls) -> "EdgeTribeRegistry":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def initialize(self, bridge: EdgeTribeBridge | None = None) -> None:
        """初始化 bridge 并注入到所有 Agent。

        Args:
            bridge: 可选，若不传则从环境变量自动构建（EdgeTribeBridge.from_env()）。
        """
        if self._initialized:
            return
        self._bridge = bridge or EdgeTribeBridge.from_env()
        self._somatic.set_bridge(self._bridge)
        self._peripheral.set_bridge(self._bridge)
        self._channel.set_bridge(self._bridge)
        self._voice.set_bridge(self._bridge)
        self._initialized = True

    def reinitialize(self, bridge: EdgeTribeBridge | None = None) -> None:
        """强制重新初始化（用于测试或配置热更新）。"""
        self._initialized = False
        self.initialize(bridge=bridge)

    @property
    def bridge(self) -> EdgeTribeBridge | None:
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

    @property
    def initialized(self) -> bool:
        return self._initialized

    def describe(self) -> dict[str, Any]:
        """返回注册表状态摘要，供健康检查使用。"""
        return {
            "initialized": self._initialized,
            "bridge_enabled": bool(self._bridge and self._bridge.enabled) if self._bridge else False,
            "bridge_host": str(self._bridge.host_base_url) if self._bridge else None,
            "agents": {
                "somatic": self._somatic.describe(),
                "peripheral": self._peripheral.describe(),
                "channel": self._channel.describe(),
                "voice": self._voice.describe(),
            },
        }


def build_edge_tribe_bridge() -> EdgeTribeBridge:
    """向后兼容：构建一个新的 EdgeTribeBridge 实例（不注册到单例）。"""
    return EdgeTribeBridge.from_env()


def get_edge_tribe_registry() -> EdgeTribeRegistry:
    """获取 EdgeTribeRegistry 全局单例。"""
    return EdgeTribeRegistry.get_instance()


__all__ = [
    "EdgeTribeRegistry",
    "build_edge_tribe_bridge",
    "get_edge_tribe_registry",
]
