"""LangGraph SqliteSaver 迁移适配层（FIX-5.1）。

这是从自研 CheckpointManager 迁移到 LangGraph 原生 SqliteSaver 的过渡模块。

迁移策略（FIX-PLAN §5.1）：
- 保留三个核心机制不变：committed_route_token / Evidence Plane / AIRLOCK
- 用 LangGraph SqliteSaver 替换自研 CheckpointManager
- resume_graph() 改为使用 LangGraph 原生 thread_id 恢复（不重新执行整张图）
- 通过 LANGGRAPH_CHECKPOINT_ENABLED 环境变量控制是否启用（默认 False，渐进迁移）

使用方式：
    # 启用 LangGraph checkpoint（设置环境变量）
    export LANGGRAPH_CHECKPOINT_ENABLED=true
    export HOST_CHECKPOINT_DB=host_brain/data/checkpoints.db

    # 代码中
    from host_brain.integrations.graph_langgraph_checkpoint import (
        get_langgraph_checkpointer,
        compile_with_langgraph_checkpoint,
        is_langgraph_checkpoint_enabled,
    )
"""

from __future__ import annotations

import logging
import os
from typing import Any

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 环境变量控制
# ---------------------------------------------------------------------------

_ENV_ENABLED = "LANGGRAPH_CHECKPOINT_ENABLED"
_ENV_DB_PATH = "HOST_CHECKPOINT_DB"
_DEFAULT_DB_PATH = "host_brain/data/checkpoints.db"


def is_langgraph_checkpoint_enabled() -> bool:
    """检查是否启用 LangGraph 原生 checkpoint（通过环境变量控制）。

    Returns:
        True 表示启用 LangGraph SqliteSaver；False 表示使用自研 CheckpointManager（默认）
    """
    raw = str(os.environ.get(_ENV_ENABLED, "false")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def get_checkpoint_db_path() -> str:
    """获取 checkpoint 数据库路径（从环境变量读取，默认 host_brain/data/checkpoints.db）。"""
    return str(os.environ.get(_ENV_DB_PATH, _DEFAULT_DB_PATH)).strip() or _DEFAULT_DB_PATH


# ---------------------------------------------------------------------------
# FIX-5.1：LangGraph SqliteSaver 适配器
# ---------------------------------------------------------------------------

class LangGraphCheckpointer:
    """LangGraph SqliteSaver 的薄包装层，提供与自研 CheckpointManager 兼容的接口。

    设计原则（FIX-PLAN §5.1）：
    - 保留 Host 路由主权（committed_route_token 不变）
    - 使用 LangGraph 原生 thread_id 作为 checkpoint 标识
    - resume 时不重新执行整张图（真正的断点恢复）
    - GraphState 必须完全可序列化（当前已满足）

    风险说明：
    - LangGraph checkpoint 格式与版本绑定，升级时需要迁移脚本
    - 首次启用时需要运行数据库迁移（_ensure_db_dir）
    """

    def __init__(self, *, db_path: str | None = None) -> None:
        self._db_path = db_path or get_checkpoint_db_path()
        self._checkpointer: Any = None
        self._initialized = False

    def _ensure_initialized(self) -> bool:
        """懒初始化 SqliteSaver（首次使用时初始化，避免启动时阻塞）。

        Returns:
            True 表示初始化成功；False 表示 LangGraph SqliteSaver 不可用（降级）
        """
        if self._initialized:
            return self._checkpointer is not None
        self._initialized = True
        try:
            # 确保数据库目录存在
            import pathlib
            pathlib.Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

            from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: PLC0415
            self._checkpointer = SqliteSaver.from_conn_string(self._db_path)
            _logger.info(
                "LangGraphCheckpointer: SqliteSaver 初始化成功，db_path=%s", self._db_path
            )
            return True
        except ImportError:
            _logger.warning(
                "LangGraphCheckpointer: langgraph.checkpoint.sqlite 不可用，"
                "请安装 langgraph[sqlite]；降级到自研 CheckpointManager"
            )
            return False
        except Exception as exc:
            _logger.warning(
                "LangGraphCheckpointer: SqliteSaver 初始化失败（%s），"
                "降级到自研 CheckpointManager", exc
            )
            return False

    @property
    def checkpointer(self) -> Any:
        """获取底层 SqliteSaver 实例（可能为 None）。"""
        self._ensure_initialized()
        return self._checkpointer

    def compile_graph(self, graph: Any) -> Any:
        """编译 LangGraph StateGraph，注入 SqliteSaver checkpointer。

        Args:
            graph: 未编译的 StateGraph 实例

        Returns:
            编译后的图（注入了 checkpointer）；若 SqliteSaver 不可用则不注入（降级）
        """
        if self._ensure_initialized() and self._checkpointer is not None:
            return graph.compile(checkpointer=self._checkpointer)
        # 降级：不注入 checkpointer（与原有行为一致）
        return graph.compile()

    def resume_from_thread(
        self,
        compiled_graph: Any,
        *,
        thread_id: str,
        input_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """FIX-5.1：使用 LangGraph 原生 thread_id 恢复图执行（真正的断点恢复）。

        与自研 resume_graph() 的区别：
        - 自研：从 pending_node_id 重新执行整张图（有副作用节点可能重复执行）
        - LangGraph：从上次中断的节点继续执行（不重新执行已完成节点）

        Args:
            compiled_graph: 已编译的 LangGraph 图（必须注入了 checkpointer）
            thread_id: LangGraph thread_id（对应自研的 checkpoint_id）
            input_override: 可选的输入覆盖（None 表示从 checkpoint 恢复，不传新 state）

        Returns:
            最终 GraphState 字典

        Raises:
            RuntimeError: SqliteSaver 不可用时抛出（调用方应降级到自研 CheckpointManager）
        """
        if not self._ensure_initialized() or self._checkpointer is None:
            raise RuntimeError(
                "LangGraph SqliteSaver 不可用，请降级到自研 CheckpointManager"
            )
        config = {"configurable": {"thread_id": thread_id}}
        # input_override=None 时传 None，LangGraph 从 checkpoint 恢复
        result = compiled_graph.invoke(input_override, config=config)
        return dict(result) if result else {}


# ---------------------------------------------------------------------------
# 模块级单例
# ---------------------------------------------------------------------------

_langgraph_checkpointer: LangGraphCheckpointer | None = None


def get_langgraph_checkpointer() -> LangGraphCheckpointer:
    """获取模块级 LangGraphCheckpointer 单例（懒初始化）。"""
    global _langgraph_checkpointer
    if _langgraph_checkpointer is None:
        _langgraph_checkpointer = LangGraphCheckpointer()
    return _langgraph_checkpointer


def compile_with_langgraph_checkpoint(graph: Any) -> Any:
    """FIX-5.1：编译 StateGraph 并注入 LangGraph SqliteSaver（若已启用）。

    这是 graph_executor.py 中 compile_runtime_graph() 的替代入口。
    通过 LANGGRAPH_CHECKPOINT_ENABLED 环境变量控制是否启用。

    使用方式（graph_executor.py 中）：
        from host_brain.integrations.graph_langgraph_checkpoint import (
            compile_with_langgraph_checkpoint,
            is_langgraph_checkpoint_enabled,
        )

        def compile_runtime_graph(self) -> Any:
            graph = StateGraph(GraphState)
            # ... 添加节点和边 ...
            if is_langgraph_checkpoint_enabled():
                return compile_with_langgraph_checkpoint(graph)
            return graph.compile()  # 原有行为

    Args:
        graph: 未编译的 StateGraph 实例

    Returns:
        编译后的图（已注入 SqliteSaver 或降级为无 checkpointer）
    """
    return get_langgraph_checkpointer().compile_graph(graph)


__all__ = [
    "LangGraphCheckpointer",
    "get_langgraph_checkpointer",
    "compile_with_langgraph_checkpoint",
    "is_langgraph_checkpoint_enabled",
    "get_checkpoint_db_path",
]
