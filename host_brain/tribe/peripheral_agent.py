from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from host_brain.tribe.base_agent import TribeAgentDescriptor
from host_brain.tribe.tribe_state import TribeStateRegistry, get_global_registry


@dataclass(slots=True)
class PeripheralAgent:
    """T-110：外周神经系统 Agent 骨架。

    负责接收情绪状态变化（EMOTION_STATE_CHANGED）和免疫拦截（IMMUNE_BLOCK）信号，
    将外周神经系统状态写入快照，供 edge_client 侧的输出通道（语音/表情/动作）参考。

    Phase 6（edge_client 接入）时将扩展为完整实现，驱动实际的外设输出。
    """

    descriptor: TribeAgentDescriptor = field(
        default_factory=lambda: TribeAgentDescriptor(
            agent_id="peripheral_agent",
            family="peripheral",
            execution_side="host",
            owner_domain="brain",
            capabilities=("peripheral_output", "emotion_expression", "motor_signal"),
        )
    )
    state_registry: TribeStateRegistry = field(default_factory=get_global_registry, repr=False)

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()

    def get_peripheral_state(self) -> dict[str, Any]:
        """返回当前外周神经系统状态快照。"""
        try:
            snap = self.state_registry.get_state("peripheral_agent") or {}
            return dict(snap.get("state") or {})
        except Exception:
            return {}

    # ── T-110：TribeEventBus 订阅注册 ────────────────────────────────────────

    def register_subscriptions(self) -> None:
        """T-110：向 TribeEventBus 注册事件订阅。

        订阅：
        - EMOTION_STATE_CHANGED：情绪变化时更新外周表达状态（表情/语调建议）
        - IMMUNE_BLOCK：免疫拦截时触发外周抑制信号（降低输出活跃度）
        """
        try:
            from host_brain.app.internal_tribe_routes import get_tribe_event_bus  # noqa: PLC0415

            bus = get_tribe_event_bus()
            bus.subscribe("EMOTION_STATE_CHANGED", self._on_emotion_state_changed)
            bus.subscribe("IMMUNE_BLOCK", self._on_immune_block)
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.peripheral_agent").warning(
                "PeripheralAgent 订阅注册失败（降级）: %s", exc
            )

    async def _on_emotion_state_changed(self, event: Any) -> None:
        """处理 EMOTION_STATE_CHANGED 事件：更新外周表达状态。

        T-110：情绪变化时，根据 emotion_tag 生成外周表达建议
        （表情强度、语调建议、动作幅度），供 edge_client 驱动外设输出。
        """
        try:
            payload = dict(getattr(event, "payload", None) or {})
            emotion_tag = str(payload.get("emotion_tag") or "NEUTRAL").strip().upper()
            intensity = max(0.0, min(1.0, float(payload.get("intensity") or 0.5)))
            ts_ms = int(getattr(event, "ts_ms", 0) or 0)

            # 情绪 → 外周表达映射（Phase 6 扩展为完整驱动指令）
            _expression_map: dict[str, dict[str, Any]] = {
                "JOY":     {"facial_expr": "smile",   "voice_tone": "bright",  "motion_scale": 0.8},
                "HAPPY":   {"facial_expr": "smile",   "voice_tone": "warm",    "motion_scale": 0.6},
                "EXCITED": {"facial_expr": "bright",  "voice_tone": "energetic","motion_scale": 1.0},
                "SAD":     {"facial_expr": "soft",    "voice_tone": "gentle",  "motion_scale": 0.2},
                "LONELY":  {"facial_expr": "soft",    "voice_tone": "caring",  "motion_scale": 0.3},
                "ANXIOUS": {"facial_expr": "neutral", "voice_tone": "calm",    "motion_scale": 0.4},
                "ANGRY":   {"facial_expr": "tense",   "voice_tone": "measured","motion_scale": 0.5},
                "NEUTRAL": {"facial_expr": "neutral", "voice_tone": "balanced","motion_scale": 0.5},
            }
            expr = _expression_map.get(emotion_tag, _expression_map["NEUTRAL"])

            prev = self.state_registry.get_state("peripheral_agent") or {}
            prev_state = dict(prev.get("state") or {})
            prev_state.update({
                "current_emotion_tag": emotion_tag,
                "current_intensity": round(intensity, 4),
                "facial_expr": expr["facial_expr"],
                "voice_tone": expr["voice_tone"],
                "motion_scale": round(float(expr["motion_scale"]) * intensity, 4),
                "last_emotion_ts_ms": ts_ms,
                # 免疫抑制标记（IMMUNE_BLOCK 时置 True）
                "suppressed": prev_state.get("suppressed", False),
            })
            self.state_registry.set_state("peripheral_agent", prev_state, writer="peripheral_agent")
        except Exception:
            pass

    async def _on_immune_block(self, event: Any) -> None:
        """处理 IMMUNE_BLOCK 事件：触发外周抑制信号。

        T-110：免疫拦截时，将外周输出置为抑制状态，
        降低表情/动作活跃度，避免在有害内容被拦截时仍表现出积极外周反应。
        """
        try:
            payload = dict(getattr(event, "payload", None) or {})
            risk_level = str(payload.get("risk_level") or "low").strip().lower()
            ts_ms = int(getattr(event, "ts_ms", 0) or 0)

            # 仅中等及以上风险触发外周抑制
            if risk_level not in ("medium", "high", "critical"):
                return

            prev = self.state_registry.get_state("peripheral_agent") or {}
            prev_state = dict(prev.get("state") or {})
            prev_state.update({
                "suppressed": True,
                "suppressed_risk_level": risk_level,
                "suppressed_ts_ms": ts_ms,
                "facial_expr": "neutral",
                "voice_tone": "measured",
                "motion_scale": 0.1,
            })
            self.state_registry.set_state("peripheral_agent", prev_state, writer="peripheral_agent")
        except Exception:
            pass


_PERIPHERAL_AGENT: PeripheralAgent | None = None


def get_peripheral_agent() -> PeripheralAgent:
    global _PERIPHERAL_AGENT
    if _PERIPHERAL_AGENT is None:
        _PERIPHERAL_AGENT = PeripheralAgent()
    return _PERIPHERAL_AGENT


def set_peripheral_agent(agent: PeripheralAgent | None) -> None:
    global _PERIPHERAL_AGENT
    _PERIPHERAL_AGENT = agent


__all__ = ["PeripheralAgent", "get_peripheral_agent", "set_peripheral_agent"]
