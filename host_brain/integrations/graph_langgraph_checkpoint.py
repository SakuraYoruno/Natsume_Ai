"""LangGraph SqliteSaver 直接封装（FIX-5.1 彻底迁移，无兼容开关）。

此模块是 graph_checkpoint.py 的对外公共接口，
直接暴露 LangGraph SqliteSaver 的编译和恢复能力。

不再有任何环境变量开关或降级路径——SqliteSaver 是唯一 checkpoint 后端。
"""

from __future__ import annotations

from host_brain.integrations.graph_checkpoint import (
    compile_graph_with_checkpoint,
    get_checkpoint_db_path,
    get_langgraph_checkpointer,
)

# 窄适配层只转发统一检查点能力：不增加环境开关或替代后端，避免恢复语义分叉。

__all__ = [
    "compile_graph_with_checkpoint",
    "get_checkpoint_db_path",
    "get_langgraph_checkpointer",
]
