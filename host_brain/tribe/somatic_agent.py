from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from host_brain.tribe.base_agent import TribeAgentDescriptor
from host_brain.tribe.tribe_state import TribeStateRegistry, get_global_registry


@dataclass(slots=True)
class SomaticAgent:
    """T-109：躯体感知 Agent 骨架。

    负责接收来自 edge_client 的躯体感知信号（BODY_SENSE_UPDATED），
    以及孤独感信号（LONELINESS_SIGNAL），将躯体状态写入快照，
    供 chat_context_builder 在构建 prompt 时参考身体感知状态。

    Phase 6（edge_client 接入）时将扩展为完整实现。
    """

    descriptor: TribeAgentDescriptor = field(
        default_factory=lambda: TribeAgentDescriptor(
            agent_id="somatic_agent",
            family="somatic",
            execution_side="host",
            owner_domain="brain",
            capabilities=("body_sense", "somatic_state", "physical_feedback"),
        )
    )
    state_registry: TribeStateRegistry = field(default_factory=get_global_registry, repr=False)

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()

    def get_somatic_state(self) -> dict[str, Any]:
        """返回当前躯体感知状态快照。"""
        try:
            snap = self.state_registry.get_state("somatic_agent") or {}
            return dict(snap.get("state") or {})
        except Exception:
            return {}

    # ── T-109：TribeEventBus 订阅注册 ────────────────────────────────────────

    def register_subscriptions(self) -> None:
        """T-109：向 TribeEventBus 注册事件订阅。

        订阅：
        - BODY_SENSE_UPDATED：躯体感知更新时写入快照（来自 edge_client）
        - LONELINESS_SIGNAL：孤独感信号时记录躯体孤独感状态
        """
        try:
            from host_brain.app.internal_tribe_routes import get_tribe_event_bus  # noqa: PLC0415

            bus = get_tribe_event_bus()
            bus.subscribe("BODY_SENSE_UPDATED", self._on_body_sense_updated)
            bus.subscribe("LONELINESS_SIGNAL", self._on_loneliness_signal)
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.somatic_agent").warning(
                "SomaticAgent 订阅注册失败（降级）: %s", exc
            )

    async def _on_body_sense_updated(self, event: Any) -> None:
        """处理 BODY_SENSE_UPDATED 事件：将躯体感知数据写入 somatic_agent 快照。

        T-109：记录最近一次躯体感知信号的各项指标，
        Phase 6 接入 edge_client 后将携带真实传感器数据。
        """
        try:
            payload = dict(getattr(event, "payload", None) or {})
            ts_ms = int(getattr(event, "ts_ms", 0) or 0)
            # 躯体感知字段（Phase 6 扩展）
            heart_rate = payload.get("heart_rate")
            skin_conductance = payload.get("skin_conductance")
            body_temp = payload.get("body_temp")
            motion_level = max(0.0, min(1.0, float(payload.get("motion_level") or 0.0)))
            fatigue_level = max(0.0, min(1.0, float(payload.get("fatigue_level") or 0.0)))

            state: dict[str, Any] = {
                "last_body_sense_ts_ms": ts_ms,
                "motion_level": round(motion_level, 4),
                "fatigue_level": round(fatigue_level, 4),
            }
            if heart_rate is not None:
                try:
                    state["heart_rate"] = float(heart_rate)
                except (TypeError, ValueError):
                    pass
            if skin_conductance is not None:
                try:
                    state["skin_conductance"] = float(skin_conductance)
                except (TypeError, ValueError):
                    pass
            if body_temp is not None:
                try:
                    state["body_temp"] = float(body_temp)
                except (TypeError, ValueError):
                    pass

            prev = self.state_registry.get_state("somatic_agent") or {}
            prev_state = dict(prev.get("state") or {})
            prev_state.update(state)
            self.state_registry.set_state("somatic_agent", prev_state, writer="somatic_agent")
        except Exception:
            pass

    async def _on_loneliness_signal(self, event: Any) -> None:
        """处理 LONELINESS_SIGNAL 事件：记录躯体孤独感状态。

        T-109：孤独感信号到达时，将孤独感强度写入 somatic_agent 快照，
        供躯体反馈系统（Phase 6）参考（如触觉反馈、体温调节等）。
        """
        try:
            payload = dict(getattr(event, "payload", None) or {})
            loneliness = max(0.0, min(1.0, float(payload.get("loneliness") or 0.0)))
            ts_ms = int(getattr(event, "ts_ms", 0) or 0)
            prev = self.state_registry.get_state("somatic_agent") or {}
            prev_state = dict(prev.get("state") or {})
            prev_state.update({
                "last_loneliness_level": round(loneliness, 4),
                "last_loneliness_ts_ms": ts_ms,
                # 高孤独感时标记需要躯体安抚反馈
                "somatic_comfort_needed": loneliness > 0.65,
            })
            self.state_registry.set_state("somatic_agent", prev_state, writer="somatic_agent")
        except Exception:
            pass


_SOMATIC_AGENT: SomaticAgent | None = None


def get_somatic_agent() -> SomaticAgent:
    global _SOMATIC_AGENT
    if _SOMATIC_AGENT is None:
        _SOMATIC_AGENT = SomaticAgent()
    return _SOMATIC_AGENT


def set_somatic_agent(agent: SomaticAgent | None) -> None:
    global _SOMATIC_AGENT
    _SOMATIC_AGENT = agent


__all__ = ["SomaticAgent", "get_somatic_agent", "set_somatic_agent"]
