"""Skill registry with enforced metadata contract."""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


SkillHandler = Callable[[dict[str, Any], dict[str, Any] | None], dict[str, Any]]

ALLOWED_CLEARANCE = {"LOW", "MEDIUM", "HIGH"}
ALLOWED_SCOPE = {"host", "memory", "system"}

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 枚举：访问级别（原 tool_meta.AccessLevel）
# ---------------------------------------------------------------------------

class AccessLevel(str, Enum):
    """工具的访问级别。"""
    PUBLIC = "PUBLIC"
    """所有来源均可调用"""
    INTERNAL = "INTERNAL"
    """仅内部系统调用"""
    RESTRICTED = "RESTRICTED"
    """需要明确授权才能调用"""


@dataclass(frozen=True)
class SkillPermissionModel:
    clearance: str
    scope: str
    require_confirm: bool


@dataclass(frozen=True)
class SkillResult:
    status: str
    result_code: str
    payload: dict[str, Any]
    detail: str | None = None


@dataclass(frozen=True)
class SkillMeta:
    skill_name: str
    clearance: str
    risk_level: int
    scope: str
    reversible: bool
    handler: SkillHandler
    require_confirm: bool = False
    description: str = ""
    family: str = "general"
    capability_tags: tuple[str, ...] = field(default_factory=tuple)
    parallelizable: bool = False
    fallback_policy: str = "chat_fallback"
    # 原 ToolMeta 字段（合并自 app/skills/tool_meta.py）
    scheduler_allowed: bool = True
    """是否允许 SCHEDULER 来源调用"""
    access_level: AccessLevel = AccessLevel.PUBLIC
    """访问级别"""

    def validate(self) -> None:
        if not self.skill_name or not isinstance(self.skill_name, str):
            raise ValueError("skill_name must be non-empty string")
        if self.clearance not in ALLOWED_CLEARANCE:
            raise ValueError(f"clearance must be one of {sorted(ALLOWED_CLEARANCE)}")
        if not isinstance(self.risk_level, int) or self.risk_level < 1 or self.risk_level > 5:
            raise ValueError("risk_level must be int in [1,5]")
        if self.scope not in ALLOWED_SCOPE:
            raise ValueError(f"scope must be one of {sorted(ALLOWED_SCOPE)}")
        if not isinstance(self.reversible, bool):
            raise ValueError("reversible must be bool")
        if not callable(self.handler):
            raise ValueError("handler must be callable")
        if self.risk_level >= 4 and not self.require_confirm:
            raise ValueError("high-risk skill must set require_confirm=True")
        if self.description and not isinstance(self.description, str):
            raise ValueError("description must be string")
        normalized_family = str(self.family or "").strip().lower()
        if not normalized_family:
            raise ValueError("family must be non-empty string")
        if not re.match(r"^[a-z][a-z0-9_\\-]{1,63}$", normalized_family):
            raise ValueError("family must match ^[a-z][a-z0-9_-]{1,63}$")
        if not isinstance(self.capability_tags, tuple):
            raise ValueError("capability_tags must be tuple[str, ...]")
        for tag in self.capability_tags:
            normalized_tag = str(tag or "").strip().lower()
            if not normalized_tag:
                raise ValueError("capability_tags cannot contain empty items")
            if not re.match(r"^[a-z][a-z0-9_\\-]{1,63}$", normalized_tag):
                raise ValueError("capability_tags items must match ^[a-z][a-z0-9_-]{1,63}$")
        if not isinstance(self.parallelizable, bool):
            raise ValueError("parallelizable must be bool")
        fallback_policy = str(self.fallback_policy or "").strip().lower()
        if fallback_policy not in {"chat_fallback", "same_family", "strict_fail", "retry_once"}:
            raise ValueError("fallback_policy must be one of {'chat_fallback','same_family','strict_fail','retry_once'}")

    @property
    def permission_model(self) -> SkillPermissionModel:
        return SkillPermissionModel(
            clearance=self.clearance,
            scope=self.scope,
            require_confirm=self.require_confirm,
        )

    def to_manifest_entry(self) -> dict:
        """生成供 LLM 读取的 skill 描述条目（用于 tool_planner 节点的 prompt 注入）。"""
        return {
            "name": self.skill_name,
            "description": self.description or f"执行 {self.skill_name} 操作",
            "clearance": self.clearance,
            "scope": self.scope,
            "require_confirm": self.require_confirm,
            "risk_level": self.risk_level,
            "family": self.family,
            "capability_tags": list(self.capability_tags),
            "parallelizable": self.parallelizable,
            "fallback_policy": self.fallback_policy,
        }


def skill(
    *,
    skill_name: str | None = None,
    clearance: str = "LOW",
    risk_level: int = 1,
    scope: str = "host",
    reversible: bool = True,
    require_confirm: bool = False,
    description: str = "",
    family: str = "general",
    capability_tags: tuple[str, ...] = (),
    parallelizable: bool = False,
    fallback_policy: str = "chat_fallback",
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to attach SkillMeta config on handler callables."""

    def _decorator(handler: Callable[..., Any]) -> Callable[..., Any]:
        setattr(
            handler,
            "__skill_meta_config__",
            {
                "skill_name": str(skill_name or handler.__name__).strip(),
                "clearance": str(clearance).strip().upper(),
                "risk_level": int(risk_level),
                "scope": str(scope).strip().lower(),
                "reversible": bool(reversible),
                "require_confirm": bool(require_confirm),
                "description": str(description or "").strip(),
                "family": str(family or "general").strip().lower() or "general",
                "capability_tags": tuple(
                    str(tag or "").strip().lower()
                    for tag in capability_tags
                    if str(tag or "").strip()
                ),
                "parallelizable": bool(parallelizable),
                "fallback_policy": str(fallback_policy or "chat_fallback").strip().lower() or "chat_fallback",
            },
        )
        return handler

    return _decorator


class SkillRegistry:
    """Single source of truth for skill metadata."""

    def __init__(self) -> None:
        self._skills: dict[str, SkillMeta] = {}

    def register(self, meta: SkillMeta) -> None:
        meta.validate()
        if meta.skill_name in self._skills:
            raise ValueError(f"skill already registered: {meta.skill_name}")
        self._skills[meta.skill_name] = meta

    def get(self, skill_name: str) -> SkillMeta | None:
        return self._skills.get(skill_name)

    def unregister(self, skill_name: str) -> bool:
        """移除已注册的 Skill。

        Args:
            skill_name: 要移除的 Skill 名称

        Returns:
            True 如果成功移除，False 如果不存在
        """
        return self._skills.pop(skill_name, None) is not None

    def require(self, skill_name: str) -> SkillMeta:
        meta = self.get(skill_name)
        if meta is None:
            raise KeyError(f"skill not found: {skill_name}")
        return meta

    def all(self) -> list[SkillMeta]:
        return list(self._skills.values())

    def list_skill_names(self) -> list[str]:
        """返回所有已注册 Skill 的名称列表（M42：用于动态 action 白名单）。"""
        return list(self._skills.keys())

    def build_skill_manifest(
        self,
        *,
        scopes: list[str] | None = None,
        max_risk_level: int = 5,
        exclude_names: list[str] | None = None,
    ) -> list[dict]:
        """构建供 LLM 读取的 skill manifest 列表。

        Args:
            scopes: 允许的 scope 列表（None 表示不过滤）
            max_risk_level: 最大风险等级（含），超过此值的 skill 不暴露给 LLM
            exclude_names: 明确排除的 skill 名称列表

        Returns:
            按 skill_name 排序的 manifest 条目列表
        """
        excluded = set(exclude_names or [])
        result: list[dict] = []
        for meta in sorted(self._skills.values(), key=lambda m: m.skill_name):
            if meta.skill_name in excluded:
                continue
            if scopes is not None and meta.scope not in scopes:
                continue
            if meta.risk_level > max_risk_level:
                continue
            result.append(meta.to_manifest_entry())
        return result

    def register_decorated(self, provider: Any) -> int:
        """Scan provider object and register all `@skill` handlers."""
        declarative_entries = getattr(provider, "__skill_meta_entries__", None)
        if callable(declarative_entries):
            count = 0
            for entry in declarative_entries():
                if not isinstance(entry, SkillMeta):
                    raise TypeError("declarative skill entries must be SkillMeta instances")
                self.register(entry)
                count += 1
            return count

        count = 0
        for attr_name in sorted(dir(provider)):
            candidate = getattr(provider, attr_name)
            cfg = getattr(candidate, "__skill_meta_config__", None)
            if not isinstance(cfg, dict):
                continue
            meta = SkillMeta(
                skill_name=str(cfg.get("skill_name") or attr_name).strip(),
                clearance=str(cfg.get("clearance") or "LOW").strip().upper(),
                risk_level=int(cfg.get("risk_level") or 1),
                scope=str(cfg.get("scope") or "host").strip().lower(),
                reversible=bool(cfg.get("reversible", True)),
                handler=candidate,  # bound method or function
                require_confirm=bool(cfg.get("require_confirm", False)),
                description=str(cfg.get("description") or "").strip(),
                family=str(cfg.get("family") or "general").strip().lower() or "general",
                capability_tags=tuple(
                    str(tag or "").strip().lower()
                    for tag in (cfg.get("capability_tags") or ())
                    if str(tag or "").strip()
                ),
                parallelizable=bool(cfg.get("parallelizable", False)),
                fallback_policy=str(cfg.get("fallback_policy") or "chat_fallback").strip().lower() or "chat_fallback",
            )
            self.register(meta)
            count += 1
        return count


# ---------------------------------------------------------------------------
# 权限守卫（原 app/skills/tool_meta.PermissionGuard，迁移至此）
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

    对 SCHEDULER 来源应用严格限制：
    - max_tool_calls_per_cycle ≤ 3
    - 禁止访问 risk_level >= 4（HIGH）的工具
    - 禁止触发 ClawdUIAction
    """

    SCHEDULER_MAX_TOOL_CALLS: int = 3
    SCHEDULER_FORBIDDEN_TOOLS: List[str] = ["ClawdUIAction"]

    def __init__(self) -> None:
        # SCHEDULER 调用计数器：session_id → call_count
        self._scheduler_call_counts: Dict[str, int] = {}
        self._scheduler_count_timestamps: Dict[str, float] = {}
        self._count_ttl_seconds: float = 3600.0
        # 线程安全锁：保护 _scheduler_call_counts 和 _scheduler_count_timestamps 的并发读写
        self._lock = threading.Lock()

    def check(
        self,
        skill_meta: SkillMeta,
        proposer_class: Any,
        session_id: Optional[str] = None,
    ) -> None:
        """
        校验工具调用权限。

        Args:
            skill_meta: Skill 元数据
            proposer_class: 发起方类别（ProposerClass 枚举或字符串）
            session_id: 会话 ID（用于 SCHEDULER 调用计数）

        Raises:
            PermissionGuardError: 权限不足时抛出。
        """
        proposer_val = proposer_class.value if hasattr(proposer_class, "value") else str(proposer_class)
        if proposer_val == "SCHEDULER":
            self._check_scheduler_permissions(skill_meta, session_id)
        else:
            self._check_user_permissions(skill_meta)

    def _check_scheduler_permissions(
        self,
        skill_meta: SkillMeta,
        session_id: Optional[str],
    ) -> None:
        """校验 SCHEDULER 来源的权限。"""
        if not skill_meta.scheduler_allowed:
            raise PermissionGuardError(
                f"工具 {skill_meta.skill_name} 不允许 SCHEDULER 来源调用（scheduler_allowed=False）",
                skill_meta.skill_name,
            )

        # 禁止访问 HIGH risk_level（risk_level >= 4）工具
        if skill_meta.risk_level >= 4:
            raise PermissionGuardError(
                "SCHEDULER 禁止访问 HIGH risk_level 工具",
                skill_meta.skill_name,
            )

        if skill_meta.skill_name in self.SCHEDULER_FORBIDDEN_TOOLS:
            raise PermissionGuardError(
                f"SCHEDULER 禁止触发 {skill_meta.skill_name}",
                skill_meta.skill_name,
            )

        import time as _time
        count_key = session_id if session_id else "_global_"
        now = _time.monotonic()

        with self._lock:
            last_ts = self._scheduler_count_timestamps.get(count_key, 0.0)
            if now - last_ts > self._count_ttl_seconds:
                self._scheduler_call_counts.pop(count_key, None)
                self._scheduler_count_timestamps.pop(count_key, None)

            count = self._scheduler_call_counts.get(count_key, 0)
            if count >= self.SCHEDULER_MAX_TOOL_CALLS:
                raise PermissionGuardError(
                    f"SCHEDULER 本周期调用次数 {count} 已达上限 {self.SCHEDULER_MAX_TOOL_CALLS}",
                    skill_meta.skill_name,
                )
            self._scheduler_call_counts[count_key] = count + 1
            self._scheduler_count_timestamps[count_key] = now

        logger.debug(
            "SCHEDULER 权限校验通过: skill=%s, session=%s",
            skill_meta.skill_name, session_id,
        )

    def _check_user_permissions(self, skill_meta: SkillMeta) -> None:
        """校验 USER 来源的权限。"""
        if skill_meta.access_level == AccessLevel.RESTRICTED:
            logger.warning(
                "USER 尝试调用 RESTRICTED 工具: %s",
                skill_meta.skill_name,
            )
        logger.debug("USER 权限校验通过: skill=%s", skill_meta.skill_name)

    def reset_scheduler_count(self, session_id: str) -> None:
        """重置指定 session 的 SCHEDULER 调用计数（周期结束时调用）。"""
        with self._lock:
            self._scheduler_call_counts.pop(session_id, None)
