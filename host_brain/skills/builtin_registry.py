"""内置 Skill 注册表工厂（M44-B）。

将系统内置的 7 个 Skill 注册到 SkillRegistry，并设置正确的 risk_level，
使 M42 分层治理能对这些 Skill 生效。

内置 Skill 均为只读/无副作用操作，risk_level=1（CACHE 通道），
审批延迟从 ~300ms 降至 ~0ms（Policy Cache 命中后）。
"""

from __future__ import annotations

from typing import Any

from host_brain.contracts.skill_execution import (
    SkillBudgetPolicy,
    SkillExecutionPolicy,
)
from host_brain.skills.registry import SkillRegistry
from host_brain.skills.registration import register_skill


def _builtin_noop_handler(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """内置 Skill 占位符 handler（实际执行由上层链路处理）。"""
    return {"status": "OK", "result": "builtin_noop"}


def _emotion_query_handler(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """查询当前会话的情感状态快照。

    从 ctx 中读取 emotion_snapshot / emotion_context，返回标准化情感摘要。
    若上下文中无情感数据，返回 NEUTRAL 默认值。
    """
    ctx = ctx or {}
    emotion_snapshot = ctx.get("emotion_snapshot") or {}
    emotion_context = ctx.get("emotion_context") or {}
    merged = {
        "mood_state": (
            emotion_snapshot.get("mood_state")
            or emotion_context.get("mood_state")
            or "NEUTRAL"
        ),
        "emotion_tag": (
            emotion_snapshot.get("emotion_tag")
            or emotion_context.get("emotion_tag")
            or "NEUTRAL"
        ),
        "emotion_intensity": (
            emotion_snapshot.get("emotion_intensity")
            or emotion_context.get("emotion_intensity")
            or 0.5
        ),
        "source": str(emotion_snapshot.get("source") or "NONE"),
        "age_ms": int(emotion_snapshot.get("age_ms") or 0),
    }
    return {"status": "OK", "result": "emotion_query", "emotion": merged}


def _bond_query_handler(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """查询当前会话的 Bond 积分和阶段。

    从 ctx 中读取 bond_stage / bond_score，返回 Bond 关系摘要。
    若上下文中无 Bond 数据，返回 FRIEND 默认值。
    """
    ctx = ctx or {}
    bond_texture = ctx.get("bond_texture") or {}
    bond_stage = str(
        bond_texture.get("stage")
        or ctx.get("bond_stage")
        or "FRIEND"
    ).upper()
    bond_score = int(
        bond_texture.get("score")
        or ctx.get("bond_score")
        or 0
    )
    return {
        "status": "OK",
        "result": "bond_query",
        "bond": {
            "stage": bond_stage,
            "score": bond_score,
            "texture": bond_texture,
        },
    }


def _session_summary_handler(args: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """生成当前会话的轻量摘要（只读，不写入任何存储）。

    从 ctx 中读取 session_id / request_id / user_message，
    返回会话元信息摘要，供 Governor 决策和 prompt 构建使用。
    """
    ctx = ctx or {}
    args = args or {}
    session_id = str(ctx.get("session_id") or args.get("session_id") or "").strip() or "UNKNOWN"
    request_id = str(ctx.get("request_id") or "").strip() or "UNKNOWN"
    user_message = str(ctx.get("user_message") or args.get("user_message") or "").strip()
    # 截断，避免摘要过长
    if len(user_message) > 200:
        user_message = user_message[:197] + "..."
    return {
        "status": "OK",
        "result": "session_summary",
        "summary": {
            "session_id": session_id,
            "request_id": request_id,
            "user_message_preview": user_message,
            "turn_count": int(ctx.get("turn_count") or 0),
        },
    }


# Common kwargs shared by all builtin noop skills.
_COMMON_SKILL_KWARGS: dict[str, Any] = {
    "clearance": "LOW",
    "risk_level": 1,
    "scope": "host",
    "reversible": False,
    "execution_policy": SkillExecutionPolicy.INLINE_READ_ONLY,
    "budget_policy": SkillBudgetPolicy.TASK_PRECHARGE,
}


def build_builtin_skill_registry() -> SkillRegistry:
    """构建包含系统内置 Skill 的 SkillRegistry。

    内置 Skill 列表（与 ACTION_ALLOWLIST 对齐）：
    - narrative_only：只读叙事生成，risk_level=1（CACHE 通道）
    - memory_recall：只读记忆检索，risk_level=1（CACHE 通道）
    - noop：空操作，risk_level=1（CACHE 通道）
    - read_only_status：只读状态查询，risk_level=1（CACHE 通道）
    - emotion_query：查询当前情感状态快照，risk_level=1（CACHE 通道）
    - bond_query：查询 Bond 积分和阶段，risk_level=1（CACHE 通道）
    - session_summary：生成会话轻量摘要，risk_level=1（CACHE 通道）

    所有内置 Skill 均为：
    - reversible=False（只读，无副作用，不可撤销）
    - clearance="LOW"（最低权限）
    - scope="host"（Host 侧执行）
    - risk_level=1 → ApprovalTier.CACHE（Policy Cache 直接放行）

    Returns:
        已注册 7 个内置 Skill 的 SkillRegistry 实例
    """
    registry = SkillRegistry()

    # narrative_only：只读叙事生成（最低风险，CACHE 通道）
    register_skill(
        skill_name="narrative_only",
        handler=_builtin_noop_handler,
        description="只读叙事生成，不执行任何副作用操作。",
        skill_registry=registry,
        **_COMMON_SKILL_KWARGS,
    )

    # memory_recall：只读记忆检索（最低风险，CACHE 通道）
    register_skill(
        skill_name="memory_recall",
        handler=_builtin_noop_handler,
        description="只读记忆检索，从 EpisodicStore 中召回历史记忆片段。",
        skill_registry=registry,
        **_COMMON_SKILL_KWARGS,
    )

    # noop：空操作（最低风险，CACHE 通道）
    register_skill(
        skill_name="noop",
        handler=_builtin_noop_handler,
        description="空操作占位符，用于测试和降级场景。",
        skill_registry=registry,
        **_COMMON_SKILL_KWARGS,
    )

    # read_only_status：只读状态查询（最低风险，CACHE 通道）
    register_skill(
        skill_name="read_only_status",
        handler=_builtin_noop_handler,
        description="只读状态查询，返回系统当前运行状态摘要。",
        skill_registry=registry,
        **_COMMON_SKILL_KWARGS,
    )

    # emotion_query：查询当前情感状态快照（最低风险，CACHE 通道）
    register_skill(
        skill_name="emotion_query",
        handler=_emotion_query_handler,
        description="查询当前会话的情感状态快照（mood_state / emotion_tag / intensity）。",
        skill_registry=registry,
        **_COMMON_SKILL_KWARGS,
    )

    # bond_query：查询 Bond 积分和阶段（最低风险，CACHE 通道）
    register_skill(
        skill_name="bond_query",
        handler=_bond_query_handler,
        description="查询当前会话的 Bond 积分和关系阶段（FRIEND / CLOSE_FRIEND / CONFIDANT / SOULMATE）。",
        skill_registry=registry,
        **_COMMON_SKILL_KWARGS,
    )

    # session_summary：生成会话轻量摘要（最低风险，CACHE 通道）
    register_skill(
        skill_name="session_summary",
        handler=_session_summary_handler,
        description="生成当前会话的轻量摘要（session_id / turn_count / user_message_preview）。",
        skill_registry=registry,
        **_COMMON_SKILL_KWARGS,
    )

    return registry
