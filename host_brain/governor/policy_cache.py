"""Governor Policy Cache — L0/L1 审批结果缓存（M42-C）。

为 ApprovalTier.CACHE 通道（risk_level=1）的 Skill 提供审批结果缓存（TTL），
相同 (skill_name, trust_level) 组合在 TTL 内复用上次审批结果，
不重复调用规则引擎，将 L0/L1 审批延迟从 ~300ms 降至 ~0ms。
"""

from __future__ import annotations

import time
from typing import Any


_DEFAULT_TTL_MS = 60_000  # 默认 TTL：1 分钟


class GovernorPolicyCache:
    """Governor 审批结果缓存。

    缓存键：(skill_name, trust_level)
    缓存值：(approved: bool, cached_at_ms: float)

    TTL 过期后自动失效，返回 None（触发重新审批）。
    """

    def __init__(self, ttl_ms: int = _DEFAULT_TTL_MS) -> None:
        self._ttl_ms = ttl_ms
        # key: (skill_name, trust_level) → (approved, cached_at_mono_ms)
        self._cache: dict[tuple[str, int], tuple[bool, float]] = {}

    def get(self, skill_name: str, trust_level: int) -> bool | None:
        """获取缓存的审批结果。

        Args:
            skill_name: Skill 名称
            trust_level: 当前会话信任等级（0-100）

        Returns:
            True/False 表示缓存的审批结果；None 表示缓存未命中或已过期
        """
        key = (skill_name, trust_level)
        entry = self._cache.get(key)
        if entry is None:
            return None
        approved, cached_at = entry
        elapsed_ms = (time.perf_counter() - cached_at) * 1000.0
        if elapsed_ms > self._ttl_ms:
            # TTL 过期，删除缓存
            del self._cache[key]
            return None
        return approved

    def put(self, skill_name: str, trust_level: int, approved: bool) -> None:
        """写入审批结果缓存。

        Args:
            skill_name: Skill 名称
            trust_level: 当前会话信任等级（0-100）
            approved: 审批结果（True=通过，False=拒绝）
        """
        key = (skill_name, trust_level)
        self._cache[key] = (approved, time.perf_counter())

    def invalidate(self, skill_name: str) -> None:
        """使指定 Skill 的所有缓存失效。

        Args:
            skill_name: 要失效的 Skill 名称
        """
        keys_to_delete = [k for k in self._cache if k[0] == skill_name]
        for k in keys_to_delete:
            del self._cache[k]

    def clear(self) -> None:
        """清空所有缓存。"""
        self._cache.clear()

    def size(self) -> int:
        """返回当前缓存条目数（包含可能已过期的条目）。"""
        return len(self._cache)
