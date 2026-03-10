"""
tool_meta.py —— Skill/MCP 工具元数据与权限模型

定义工具注册规范和权限守卫（需求 7）：
- ToolMeta 必填字段声明（需求 7.3）
- PermissionGuard：SCHEDULER 来源严格限制（需求 7.1、7.2）
- HotLoader：加载前元数据完整性校验（需求 7.5）
- requires_human_approval 工具的人工确认流程（需求 7.4）
"""
from __future__ import annotations

import importlib.util
import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from ..schemas.memory_types import ProposerClass, RiskLevel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 枚举：工具访问级别
# ---------------------------------------------------------------------------

class AccessLevel(str, Enum):
    """工具的访问级别。"""
    PUBLIC = "PUBLIC"
    """所有来源均可调用"""
    INTERNAL = "INTERNAL"
    """仅内部系统调用"""
    RESTRICTED = "RESTRICTED"
    """需要明确授权才能调用"""


# ---------------------------------------------------------------------------
# 数据类：工具元数据
# ---------------------------------------------------------------------------

@dataclass
class ToolMeta:
    """
    Skill/MCP 工具元数据。

    所有工具注册时必须声明以下字段（需求 7.3）。
    """
    name: str
    """工具唯一名称"""
    description: str
    """工具功能描述"""
    access_level: AccessLevel
    """访问级别（必填）"""
    risk_level: RiskLevel
    """风险等级（必填）"""
    reversible: bool
    """操作是否可逆（必填）"""
    requires_human_approval: bool
    """执行前是否需要人工确认（必填）"""
    scheduler_allowed: bool
    """是否允许 SCHEDULER 来源调用（必填）"""

    # 可选字段
    version: str = "1.0.0"
    """工具版本"""
    tags: List[str] = field(default_factory=list)
    """工具标签"""
    max_calls_per_cycle: int = 10
    """单次循环最大调用次数"""
    metadata: Dict[str, Any] = field(default_factory=dict)
    """附加元数据"""

    # 必填字段列表（用于完整性校验）
    REQUIRED_FIELDS: List[str] = field(
        default_factory=lambda: [
            "name", "description", "access_level",
            "risk_level", "reversible",
            "requires_human_approval", "scheduler_allowed",
        ],
        init=False,
        repr=False,
    )

    def validate(self) -> List[str]:
        """
        校验元数据完整性。

        Returns:
            缺失字段列表，空列表表示校验通过。
        """
        missing = []
        for f in ["name", "description", "access_level",
                  "risk_level", "reversible",
                  "requires_human_approval", "scheduler_allowed"]:
            val = getattr(self, f, None)
            if val is None or val == "":
                missing.append(f)
        return missing


# ---------------------------------------------------------------------------
# 权限守卫
# ---------------------------------------------------------------------------

class PermissionGuardError(Exception):
    """权限守卫拒绝操作时抛出的异常。"""

    def __init__(self, reason: str, tool_name: str = "") -> None:
        self.reason = reason
        self.tool_name = tool_name
        super().__init__(f"PermissionGuard 拒绝 [{tool_name}]: {reason}")


class PermissionGuard:
    """
    Skill/MCP 权限守卫。

    对 SCHEDULER 来源应用严格限制（需求 7.1、7.2）：
    - max_tool_calls_per_cycle ≤ 3
    - 禁止访问 HIGH risk_level 工具
    - 禁止触发 ClawdUIAction
    """

    # SCHEDULER 来源的严格限制
    SCHEDULER_MAX_TOOL_CALLS: int = 3
    SCHEDULER_FORBIDDEN_TOOLS: List[str] = ["ClawdUIAction"]

    def __init__(
        self,
        human_approval_requester: Optional[Callable] = None,
    ) -> None:
        """
        Args:
            human_approval_requester: 人工确认请求回调，
                签名为 async (tool_name: str, action_desc: str) -> bool。
                返回 True 表示用户批准，False 表示拒绝。
        """
        self._human_approval_requester = human_approval_requester
        # SCHEDULER 调用计数器：session_id → call_count
        self._scheduler_call_counts: Dict[str, int] = {}

    def check(
        self,
        tool_meta: ToolMeta,
        proposer_class: ProposerClass,
        session_id: Optional[str] = None,
    ) -> None:
        """
        校验工具调用权限。

        Args:
            tool_meta: 工具元数据
            proposer_class: 发起方类别
            session_id: 会话 ID（用于 SCHEDULER 调用计数）

        Raises:
            PermissionGuardError: 权限不足时抛出。
        """
        if proposer_class == ProposerClass.SCHEDULER:
            self._check_scheduler_permissions(tool_meta, session_id)
        else:
            self._check_user_permissions(tool_meta)

    def _check_scheduler_permissions(
        self,
        tool_meta: ToolMeta,
        session_id: Optional[str],
    ) -> None:
        """校验 SCHEDULER 来源的权限（需求 7.2）。"""

        # 检查 scheduler_allowed 标志
        if not tool_meta.scheduler_allowed:
            raise PermissionGuardError(
                f"工具 {tool_meta.name} 不允许 SCHEDULER 来源调用（scheduler_allowed=False）",
                tool_meta.name,
            )

        # 禁止访问 HIGH risk_level 工具（需求 7.2）
        if tool_meta.risk_level == RiskLevel.HIGH:
            raise PermissionGuardError(
                f"SCHEDULER 禁止访问 HIGH risk_level 工具",
                tool_meta.name,
            )

        # 禁止触发 ClawdUIAction（需求 7.2）
        if tool_meta.name in self.SCHEDULER_FORBIDDEN_TOOLS:
            raise PermissionGuardError(
                f"SCHEDULER 禁止触发 {tool_meta.name}",
                tool_meta.name,
            )

        # 检查调用次数限制（需求 7.2）
        if session_id:
            count = self._scheduler_call_counts.get(session_id, 0)
            if count >= self.SCHEDULER_MAX_TOOL_CALLS:
                raise PermissionGuardError(
                    f"SCHEDULER 本周期调用次数 {count} 已达上限 {self.SCHEDULER_MAX_TOOL_CALLS}",
                    tool_meta.name,
                )
            self._scheduler_call_counts[session_id] = count + 1

        logger.debug(
            "SCHEDULER 权限校验通过: tool=%s, session=%s",
            tool_meta.name, session_id,
        )

    def _check_user_permissions(self, tool_meta: ToolMeta) -> None:
        """校验 USER 来源的权限。"""
        if tool_meta.access_level == AccessLevel.RESTRICTED:
            logger.warning(
                "USER 尝试调用 RESTRICTED 工具: %s",
                tool_meta.name,
            )
            # RESTRICTED 工具需要额外授权，此处记录警告，具体授权逻辑由业务层处理
        logger.debug("USER 权限校验通过: tool=%s", tool_meta.name)

    async def request_human_approval(
        self,
        tool_meta: ToolMeta,
        action_description: str,
    ) -> bool:
        """
        对 requires_human_approval=True 的工具请求人工确认（需求 7.4）。

        Args:
            tool_meta: 工具元数据
            action_description: 操作描述

        Returns:
            True 表示用户批准，False 表示拒绝。
        """
        if not tool_meta.requires_human_approval:
            return True

        if self._human_approval_requester is None:
            logger.warning(
                "工具 %s 需要人工确认，但未配置 human_approval_requester，默认拒绝",
                tool_meta.name,
            )
            return False

        try:
            approved = await self._human_approval_requester(
                tool_meta.name, action_description
            )
            logger.info(
                "人工确认结果: tool=%s, approved=%s",
                tool_meta.name, approved,
            )
            return approved
        except Exception as exc:
            logger.error("人工确认请求失败: %s", exc)
            return False

    def reset_scheduler_count(self, session_id: str) -> None:
        """重置指定 session 的 SCHEDULER 调用计数（周期结束时调用）。"""
        self._scheduler_call_counts.pop(session_id, None)


# ---------------------------------------------------------------------------
# 热加载器
# ---------------------------------------------------------------------------

class HotLoaderError(Exception):
    """HotLoader 加载失败时抛出的异常。"""
    pass


class HotLoader:
    """
    Skill 热加载器。

    在加载新 Skill 前验证工具元数据完整性（需求 7.5），
    拒绝加载缺少必填字段的工具。
    """

    def __init__(self) -> None:
        self._loaded_tools: Dict[str, ToolMeta] = {}

    def load_from_module(self, module_path: str) -> ToolMeta:
        """
        从 Python 模块文件加载工具元数据。

        Args:
            module_path: 模块文件的绝对路径

        Returns:
            验证通过的 ToolMeta。

        Raises:
            HotLoaderError: 元数据缺失或不完整时抛出。
        """
        if not os.path.exists(module_path):
            raise HotLoaderError(f"模块文件不存在: {module_path}")

        # 动态加载模块
        spec = importlib.util.spec_from_file_location("_hot_skill", module_path)
        if spec is None or spec.loader is None:
            raise HotLoaderError(f"无法加载模块: {module_path}")

        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            raise HotLoaderError(f"模块执行失败: {exc}") from exc

        # 获取 TOOL_META 属性
        tool_meta = getattr(module, "TOOL_META", None)
        if tool_meta is None:
            raise HotLoaderError(
                f"模块 {module_path} 缺少 TOOL_META 属性，拒绝加载"
            )

        if not isinstance(tool_meta, ToolMeta):
            raise HotLoaderError(
                f"模块 {module_path} 的 TOOL_META 不是 ToolMeta 实例，拒绝加载"
            )

        # 验证元数据完整性（需求 7.5）
        missing_fields = tool_meta.validate()
        if missing_fields:
            raise HotLoaderError(
                f"工具 {tool_meta.name} 元数据不完整，缺少字段: {missing_fields}，拒绝加载"
            )

        self._loaded_tools[tool_meta.name] = tool_meta
        logger.info("热加载成功: tool=%s, version=%s", tool_meta.name, tool_meta.version)
        return tool_meta

    def load_from_meta(self, tool_meta: ToolMeta) -> ToolMeta:
        """
        直接注册 ToolMeta 对象（用于程序化注册）。

        Raises:
            HotLoaderError: 元数据不完整时抛出。
        """
        missing_fields = tool_meta.validate()
        if missing_fields:
            raise HotLoaderError(
                f"工具 {tool_meta.name} 元数据不完整，缺少字段: {missing_fields}，拒绝加载"
            )
        self._loaded_tools[tool_meta.name] = tool_meta
        logger.info("工具注册成功: tool=%s", tool_meta.name)
        return tool_meta

    def get_tool(self, name: str) -> Optional[ToolMeta]:
        """获取已加载的工具元数据。"""
        return self._loaded_tools.get(name)

    def list_tools(self) -> List[ToolMeta]:
        """列出所有已加载的工具。"""
        return list(self._loaded_tools.values())

    def unload_tool(self, name: str) -> bool:
        """卸载工具。"""
        if name in self._loaded_tools:
            del self._loaded_tools[name]
            logger.info("工具已卸载: %s", name)
            return True
        return False
