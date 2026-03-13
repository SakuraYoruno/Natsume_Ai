"""情绪/Bond 关系 Skill 包（P1 优先级）。

提供情绪状态更新和 Bond 关系积分操作能力，
支撑 PRD S1 场景（情感联动 + 关系成长）。
与 host_brain/bond/ 模块深度集成。

风险分级说明：
  risk_level=1  只读/查询操作（emotion_get / bond_score_get）
  risk_level=2  写操作（emotion_update / bond_score_update）

所有 handler 均为轻量归一化层，返回标准化 payload，
由 impl/emotion_executor.py 路由到 bond/ 模块执行。
"""

from __future__ import annotations

from typing import Any

from host_brain.skills.registry import SkillRegistry, skill

# 支持的情感标签
_VALID_EMOTION_TAGS = frozenset({
    "HAPPY", "SAD", "ANGRY", "SURPRISED", "CALM", "NEUTRAL",
    "EXCITED", "EMBARRASSED", "SLEEPY", "THINKING", "ANXIOUS",
    "GRATEFUL", "LONELY", "PROUD", "CONFUSED",
})

# 支持的心情状态
_VALID_MOOD_STATES = frozenset({
    "steady", "rising", "falling", "volatile", "flat",
})

# 支持的 Bond 事件类型
_VALID_BOND_EVENTS = frozenset({
    "positive_interaction", "negative_interaction",
    "gift", "shared_activity", "conflict", "apology",
    "milestone", "daily_greeting", "long_absence",
    "deep_conversation", "help_given", "help_received",
})


class EmotionSkillProvider:
    """情绪/Bond 关系 Skill 集合。"""

    @skill(
        skill_name="emotion_update",
        clearance="LOW",
        risk_level=2,
        scope="host",
        reversible=True,
        description="更新 Natsume 当前情绪状态（emotion_tag + mood_state + valence），触发 VTS 表情联动。",
    )
    def emotion_update(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """更新情绪状态。

        Args:
            emotion_tag (str): 情感标签（必填，如 HAPPY / SAD / CALM）
            mood_state (str): 心情趋势，steady / rising / falling / volatile / flat（默认 steady）
            valence (float): 情感效价 -1.0~1.0（负面→正面，默认 0.0）
            arousal (float): 情感唤醒度 0.0~1.0（低→高，默认 0.5）
            trigger (str): 触发原因描述（可选，用于记忆关联）
            sync_vts (bool): 是否同步触发 VTS 表情切换（默认 True）
        """
        emotion_tag = str((args or {}).get("emotion_tag") or "NEUTRAL").strip().upper()
        if emotion_tag not in _VALID_EMOTION_TAGS:
            emotion_tag = "NEUTRAL"
        mood_state = str((args or {}).get("mood_state") or "steady").strip().lower()
        if mood_state not in _VALID_MOOD_STATES:
            mood_state = "steady"
        try:
            valence = max(-1.0, min(1.0, float((args or {}).get("valence") or 0.0)))
        except (TypeError, ValueError):
            valence = 0.0
        try:
            arousal = max(0.0, min(1.0, float((args or {}).get("arousal") or 0.5)))
        except (TypeError, ValueError):
            arousal = 0.5
        trigger = str((args or {}).get("trigger") or "").strip() or None
        sync_vts_raw = (args or {}).get("sync_vts")
        sync_vts = bool(sync_vts_raw) if sync_vts_raw is not None else True

        return {
            "status": "OK",
            "tool": "emotion",
            "action": "update",
            "emotion_tag": emotion_tag,
            "mood_state": mood_state,
            "valence": valence,
            "arousal": arousal,
            "trigger": trigger,
            "sync_vts": sync_vts,
            "manifest_ref": f"emotion_update::{emotion_tag}",
            "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
            "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
            "_context": ctx or {},
        }

    @skill(
        skill_name="bond_score_update",
        clearance="LOW",
        risk_level=2,
        scope="host",
        reversible=True,
        description="更新与用户的 Bond 关系积分（基于互动事件类型自动计算增减量）。",
    )
    def bond_score_update(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """更新 Bond 关系积分。

        Args:
            event_type (str): 互动事件类型（必填，如 positive_interaction / gift / conflict）
            delta (float): 积分增减量（可选，不填则由 event_type 自动决定）
            user_id (str): 用户 ID（可选，默认当前 session 用户）
            note (str): 备注说明（可选，写入 Bond 历史）
        """
        event_type = str((args or {}).get("event_type") or "positive_interaction").strip().lower()
        if event_type not in _VALID_BOND_EVENTS:
            event_type = "positive_interaction"
        delta_raw = (args or {}).get("delta")
        try:
            delta = float(delta_raw) if delta_raw is not None else None
        except (TypeError, ValueError):
            delta = None
        user_id = str((args or {}).get("user_id") or "").strip() or None
        note = str((args or {}).get("note") or "").strip() or None

        return {
            "status": "OK",
            "tool": "emotion",
            "action": "bond_update",
            "event_type": event_type,
            "delta": delta,
            "user_id": user_id,
            "note": note,
            "manifest_ref": f"bond_score_update::{event_type}",
            "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
            "request_id": str((ctx or {}).get("request_id") or "").strip() or None,
            "_context": ctx or {},
        }

    @skill(
        skill_name="emotion_get",
        clearance="LOW",
        risk_level=1,
        scope="host",
        reversible=True,
        description="查询 Natsume 当前情绪状态快照（emotion_tag / mood_state / valence / arousal）。",
    )
    def emotion_get(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """查询当前情绪状态。

        Args:
            include_history (bool): 是否包含近期情绪历史（默认 False）
            history_limit (int): 历史条数（默认 10）
        """
        include_history_raw = (args or {}).get("include_history")
        include_history = bool(include_history_raw) if include_history_raw is not None else False
        try:
            history_limit = max(1, min(50, int((args or {}).get("history_limit") or 10)))
        except (TypeError, ValueError):
            history_limit = 10

        return {
            "status": "OK",
            "tool": "emotion",
            "action": "get",
            "include_history": include_history,
            "history_limit": history_limit,
            "manifest_ref": "emotion_get::current",
            "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
            "_context": ctx or {},
        }

    @skill(
        skill_name="bond_score_get",
        clearance="LOW",
        risk_level=1,
        scope="host",
        reversible=True,
        description="查询与用户的 Bond 关系积分和当前阶段（STRANGER / ACQUAINTANCE / FRIEND / ...）。",
    )
    def bond_score_get(self, args: dict[str, Any], ctx: dict[str, Any] | None) -> dict[str, Any]:
        """查询 Bond 关系积分。

        Args:
            user_id (str): 用户 ID（可选，默认当前 session 用户）
            include_history (bool): 是否包含积分变化历史（默认 False）
            history_limit (int): 历史条数（默认 20）
        """
        user_id = str((args or {}).get("user_id") or "").strip() or None
        include_history_raw = (args or {}).get("include_history")
        include_history = bool(include_history_raw) if include_history_raw is not None else False
        try:
            history_limit = max(1, min(100, int((args or {}).get("history_limit") or 20)))
        except (TypeError, ValueError):
            history_limit = 20

        return {
            "status": "OK",
            "tool": "emotion",
            "action": "bond_get",
            "user_id": user_id,
            "include_history": include_history,
            "history_limit": history_limit,
            "manifest_ref": "bond_score_get::current",
            "session_id": str((ctx or {}).get("session_id") or "").strip() or None,
            "_context": ctx or {},
        }


def build_emotion_skill_registry(provider: EmotionSkillProvider | None = None) -> SkillRegistry:
    """构建包含情绪/Bond 关系 Skills 的 SkillRegistry。

    Emotion Skill 列表：
    - emotion_update：更新情绪状态（risk=2，写操作）
    - bond_score_update：更新 Bond 积分（risk=2，写操作）
    - emotion_get：查询情绪状态（risk=1，只读）
    - bond_score_get：查询 Bond 积分（risk=1，只读）

    Returns:
        已注册 4 个情绪/Bond Skills 的 SkillRegistry 实例
    """
    registry = SkillRegistry()
    registry.register_decorated(provider or EmotionSkillProvider())
    return registry
