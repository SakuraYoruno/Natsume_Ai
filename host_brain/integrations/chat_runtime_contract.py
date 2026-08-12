"""版本化聊天运行时载荷的纯读取器。

该模块只把已存在的聊天段落投影为独立字典，不补默认治理数据、不写回输入，
从而让图运行时能够读取情绪/图元数据而不让聊天载荷成为控制面写入口。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _section(payload: Mapping[str, Any], name: str) -> dict[str, Any]:
    """安全复制一个映射段；标量或错误形状统一降为空字典。"""
    value = payload.get(name)
    return dict(value) if isinstance(value, Mapping) else {}


def get_chat_runtime_emotion_state(payload: Mapping[str, Any]) -> dict[str, Any]:
    """读取新版 ``emotion.state``，并仅为旧载荷提供只读的兼容回退。"""
    emotion = _section(payload, "emotion")
    state = emotion.get("state")
    if isinstance(state, Mapping):
        # 优先结构化新版状态，避免旧字段覆盖带版本语义的聊天情绪上下文。
        return dict(state)
    legacy = payload.get("emotion_context")
    return dict(legacy) if isinstance(legacy, Mapping) else {}


def get_chat_runtime_graph(payload: Mapping[str, Any]) -> dict[str, Any]:
    """读取聊天载荷声明的图段；调用方仍须在图契约层验证其可执行性。"""
    return _section(payload, "graph")


__all__ = ["get_chat_runtime_emotion_state", "get_chat_runtime_graph"]
