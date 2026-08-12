"""规范 Surface runtime 消费的应用端口。

Surface 领域/应用模块不得反向导入 HTTP 或 app composition；组合层在启动时
一次性绑定这些 callable，使回复主链可替换实现、测试或恢复，而不耦合入口。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class SurfaceRuntimePorts:
    """Surface 主链的受限能力面。

    端口只暴露读取、上下文构建和受管服务入口；具体基础设施、连接生命周期与
    授权仍由 composition 层负责，避免表层代码直接取得全局应用对象。
    """

    get_ledger: Callable[[], Any]
    get_life_command_service: Callable[[], Any]
    get_life_snapshot_reader: Callable[[], Any]
    get_relationship_lifecycle_service: Callable[[], Any]
    get_persistence_settings: Callable[[], Any]
    build_surface_context_packet: Callable[..., dict[str, Any]]
    build_user_preference_pack: Callable[..., dict[str, Any]]
    get_conversation_history: Callable[[], Any]
    build_conversation_experience_read_pack: Callable[..., dict[str, Any]]
    build_conversation_understanding_pack: Callable[..., dict[str, Any]]
    build_memory_context_debug: Callable[..., dict[str, Any]]
    resolve_surface_reply_llm_runtime: Callable[..., dict[str, Any]]
    model_attempt_service: Callable[[], Any]
    resolve_expression_preferences: Callable[..., dict[str, Any] | None] | None = None
    resolve_active_identity: Callable[..., dict[str, Any] | None] | None = None


__all__ = ["SurfaceRuntimePorts"]
