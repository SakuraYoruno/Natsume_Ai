"""领域层与应用适配器共用的稳定状态写入域契约。

写入域用于让持久化、审计和授权层区分人格、运行、记忆及执行状态；它只分类
写入意图，不授予任何具体仓储或副作用能力。
"""

from __future__ import annotations

from enum import Enum


class StateScope(str, Enum):
    """受治理状态的四个写入命名空间，避免不同生命周期数据互相伪装。"""
    SOUL_STATE = "soul_state"
    RUNTIME_STATE = "runtime_state"
    MEMORY_STATE = "memory_state"
    EXECUTION_STATE = "execution_state"


__all__ = ["StateScope"]
