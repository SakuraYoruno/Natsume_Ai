from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from host_brain.tribe.base_agent import TribeAgentDescriptor
from host_brain.tribe.tribe_state import TribeStateRegistry, get_global_registry


# ── 语言风格映射表 ────────────────────────────────────────────────────────────
# bond_stage → 基础语言风格
_BOND_STYLE_MAP: dict[str, str] = {
    "stranger":   "formal",
    "acquaint":   "neutral",
    "friend":     "warm",
    "close":      "intimate",
    "partner":    "intimate",
}

# emotion_tag → 语气修饰
_EMOTION_TONE_MAP: dict[str, str] = {
    "JOY":        "playful",
    "HAPPY":      "cheerful",
    "EXCITED":    "energetic",
    "SAD":        "gentle",
    "LONELY":     "caring",
    "ANXIOUS":    "calm",
    "ANGRY":      "measured",
    "NEUTRAL":    "balanced",
}


@dataclass(slots=True)
class LanguageSystemAgent:
    """T-103/T-104：语言系统 Agent。

    负责根据当前 bond_stage / emotion_tag / loneliness_level 等状态，
    动态生成语言风格建议（language_style），供 chat_context_builder 注入 prompt。
    """

    descriptor: TribeAgentDescriptor = field(
        default_factory=lambda: TribeAgentDescriptor(
            agent_id="language_system_agent",
            family="language",
            capabilities=("language_style", "tone_adapt", "register_shift"),
        )
    )
    state_registry: TribeStateRegistry = field(default_factory=get_global_registry, repr=False)

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()

    def get_language_style(
        self,
        *,
        bond_stage: str = "stranger",
        emotion_tag: str = "NEUTRAL",
        loneliness_level: float = 0.0,
        high_loneliness_flag: bool = False,
    ) -> dict[str, Any]:
        """T-104：根据 bond_stage / emotion_tag / loneliness_level 返回语言风格建议。

        返回字段：
            style (str): "formal" | "neutral" | "warm" | "intimate"
            tone (str): "balanced" | "gentle" | "playful" | "caring" | ...
            register (str): "standard" | "casual" | "affectionate"
            loneliness_hint (str): 高孤独感时的陪伴感提示
        """
        stage = str(bond_stage or "stranger").strip().lower()
        etag = str(emotion_tag or "NEUTRAL").strip().upper()

        style = _BOND_STYLE_MAP.get(stage, "neutral")
        tone = _EMOTION_TONE_MAP.get(etag, "balanced")

        # 高孤独感时强制升温
        if high_loneliness_flag or loneliness_level > 0.7:
            if style in ("formal", "neutral"):
                style = "warm"
            tone = "caring"

        # register 由 style 推导
        register_map = {"formal": "standard", "neutral": "standard", "warm": "casual", "intimate": "affectionate"}
        register = register_map.get(style, "standard")

        loneliness_hint = ""
        if high_loneliness_flag or loneliness_level > 0.7:
            loneliness_hint = "用户当前孤独感较高，请在回复中增加陪伴感和温暖表达，避免冷漠或简短的回应。"
        elif loneliness_level > 0.4:
            loneliness_hint = "用户当前有轻微孤独感，可适当增加关怀语气。"

        result = {
            "style": style,
            "tone": tone,
            "register": register,
            "loneliness_hint": loneliness_hint,
            "bond_stage": stage,
            "emotion_tag": etag,
        }
        # 写入快照供诊断
        try:
            self.state_registry.set_state(
                "language_system_agent",
                {"last_language_style": result},
                writer="language_system_agent",
            )
        except Exception:
            pass
        return result

    # ── T-103：TribeEventBus 订阅注册 ────────────────────────────────────────

    def register_subscriptions(self) -> None:
        """T-103：向 TribeEventBus 注册事件订阅。

        订阅：
        - EMOTION_STATE_CHANGED：情绪变化时更新语言风格快照
        - BOND_STAGE_CHANGED：关系阶段跃迁时更新语言风格快照
        - LONELINESS_SIGNAL：孤独感信号时更新 loneliness_hint
        """
        try:
            from host_brain.app.internal_tribe_routes import get_tribe_event_bus  # noqa: PLC0415

            bus = get_tribe_event_bus()
            bus.subscribe("EMOTION_STATE_CHANGED", self._on_emotion_state_changed)
            bus.subscribe("BOND_STAGE_CHANGED", self._on_bond_stage_changed)
            bus.subscribe("LONELINESS_SIGNAL", self._on_loneliness_signal)
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.language_system_agent").warning(
                "LanguageSystemAgent 订阅注册失败（降级）: %s", exc
            )

    async def _on_emotion_state_changed(self, event: Any) -> None:
        """处理 EMOTION_STATE_CHANGED：情绪变化时刷新语言风格快照。"""
        try:
            payload = dict(getattr(event, "payload", None) or {})
            emotion_tag = str(payload.get("emotion_tag") or "NEUTRAL").strip().upper()
            # 读取当前 bond_stage
            prev = self.state_registry.get_state("language_system_agent") or {}
            prev_state = dict(prev.get("state") or {})
            last_style = dict(prev_state.get("last_language_style") or {})
            bond_stage = str(last_style.get("bond_stage") or "stranger").strip().lower()
            self.get_language_style(bond_stage=bond_stage, emotion_tag=emotion_tag)
        except Exception:
            pass

    async def _on_bond_stage_changed(self, event: Any) -> None:
        """处理 BOND_STAGE_CHANGED：关系阶段跃迁时刷新语言风格快照。"""
        try:
            payload = dict(getattr(event, "payload", None) or {})
            bond_stage = str(payload.get("new_stage") or payload.get("bond_stage") or "stranger").strip().lower()
            prev = self.state_registry.get_state("language_system_agent") or {}
            prev_state = dict(prev.get("state") or {})
            last_style = dict(prev_state.get("last_language_style") or {})
            emotion_tag = str(last_style.get("emotion_tag") or "NEUTRAL").strip().upper()
            self.get_language_style(bond_stage=bond_stage, emotion_tag=emotion_tag)
        except Exception:
            pass

    async def _on_loneliness_signal(self, event: Any) -> None:
        """处理 LONELINESS_SIGNAL：孤独感信号时刷新 loneliness_hint。"""
        try:
            payload = dict(getattr(event, "payload", None) or {})
            loneliness = max(0.0, min(1.0, float(payload.get("loneliness") or 0.0)))
            bond_stage = str(payload.get("bond_stage") or "stranger").strip().lower()
            prev = self.state_registry.get_state("language_system_agent") or {}
            prev_state = dict(prev.get("state") or {})
            last_style = dict(prev_state.get("last_language_style") or {})
            emotion_tag = str(last_style.get("emotion_tag") or "NEUTRAL").strip().upper()
            self.get_language_style(
                bond_stage=bond_stage,
                emotion_tag=emotion_tag,
                loneliness_level=loneliness,
                high_loneliness_flag=loneliness > 0.7,
            )
        except Exception:
            pass


_LANGUAGE_SYSTEM_AGENT: LanguageSystemAgent | None = None


def get_language_system_agent() -> LanguageSystemAgent:
    global _LANGUAGE_SYSTEM_AGENT
    if _LANGUAGE_SYSTEM_AGENT is None:
        _LANGUAGE_SYSTEM_AGENT = LanguageSystemAgent()
    return _LANGUAGE_SYSTEM_AGENT


def set_language_system_agent(agent: LanguageSystemAgent | None) -> None:
    global _LANGUAGE_SYSTEM_AGENT
    _LANGUAGE_SYSTEM_AGENT = agent


__all__ = ["LanguageSystemAgent", "get_language_system_agent", "set_language_system_agent"]
