"""内置 Skill 注册表工厂（M44-B）。

将系统内置的 4 个 Skill 注册到 SkillRegistry，并设置正确的 risk_level，
使 M42 分层治理能对这些 Skill 生效。

内置 Skill 均为只读/无副作用操作，risk_level=1（CACHE 通道），
审批延迟从 ~300ms 降至 ~0ms（Policy Cache 命中后）。
"""

from __future__ import annotations

from typing import Any

from host_brain.skills.registry import SkillMeta, SkillRegistry


def _builtin_noop_handler(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """内置 Skill 占位符 handler（实际执行由上层链路处理）。"""
    return {"status": "OK", "result": "builtin_noop"}


def build_builtin_skill_registry() -> SkillRegistry:
    """构建包含系统内置 Skill 的 SkillRegistry。

    内置 Skill 列表（与 ACTION_ALLOWLIST 对齐）：
    - narrative_only：只读叙事生成，risk_level=1（CACHE 通道）
    - memory_recall：只读记忆检索，risk_level=1（CACHE 通道）
    - noop：空操作，risk_level=1（CACHE 通道）
    - read_only_status：只读状态查询，risk_level=1（CACHE 通道）

    所有内置 Skill 均为：
    - reversible=True（可撤销）
    - clearance="LOW"（最低权限）
    - scope="host"（Host 侧执行）
    - risk_level=1 → ApprovalTier.CACHE（Policy Cache 直接放行）

    Returns:
        已注册 4 个内置 Skill 的 SkillRegistry 实例
    """
    registry = SkillRegistry()

    # narrative_only：只读叙事生成（最低风险，CACHE 通道）
    registry.register(SkillMeta(
        skill_name="narrative_only",
        clearance="LOW",
        risk_level=1,
        scope="host",
        reversible=True,
        handler=_builtin_noop_handler,
        require_confirm=False,
    ))

    # memory_recall：只读记忆检索（最低风险，CACHE 通道）
    registry.register(SkillMeta(
        skill_name="memory_recall",
        clearance="LOW",
        risk_level=1,
        scope="host",
        reversible=True,
        handler=_builtin_noop_handler,
        require_confirm=False,
    ))

    # noop：空操作（最低风险，CACHE 通道）
    registry.register(SkillMeta(
        skill_name="noop",
        clearance="LOW",
        risk_level=1,
        scope="host",
        reversible=True,
        handler=_builtin_noop_handler,
        require_confirm=False,
    ))

    # read_only_status：只读状态查询（最低风险，CACHE 通道）
    registry.register(SkillMeta(
        skill_name="read_only_status",
        clearance="LOW",
        risk_level=1,
        scope="host",
        reversible=True,
        handler=_builtin_noop_handler,
        require_confirm=False,
    ))

    return registry
