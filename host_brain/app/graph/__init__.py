"""
graph 包 —— 图配置和类型定义

Phase XIV v4 重构：MainChain/SceneRouter/chain_nodes/input_source_strategy 已废弃删除。
所有场景统一由 top_agent.py 处理。

保留：
  - chain_types.py: ChainContext / TypedDict / ChainNode / NODE_TIMEOUT（框架级类型）
"""
from .chain_types import ChainContext, ChainNode, NODE_TIMEOUT

__all__ = [
    "ChainContext",
    "ChainNode",
    "NODE_TIMEOUT",
]
