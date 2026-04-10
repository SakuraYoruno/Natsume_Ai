from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from host_brain.core.ids import now_ms
from host_brain.tribe.base_agent import TribeAgentDescriptor
from host_brain.tribe.events import emit_tribe_event
from host_brain.tribe.tribe_state import TribeStateRegistry, get_global_registry


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


@dataclass(slots=True)
class ProprioceptionAgent:
    descriptor: TribeAgentDescriptor = field(
        default_factory=lambda: TribeAgentDescriptor(
            agent_id="proprioception_agent",
            family="proprioception",
            execution_side="host",
            owner_domain="brain",
            capabilities=("active_user", "session_load", "cognitive_load_index", "continuity_hint"),
        )
    )
    state_registry: TribeStateRegistry = field(default_factory=get_global_registry, repr=False)
    _interrupt_ts_ms: list[int] = field(default_factory=list, init=False, repr=False)

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()

    def register_subscriptions(self) -> None:
        try:
            from host_brain.app.internal_tribe_routes import get_tribe_event_bus  # noqa: PLC0415

            bus = get_tribe_event_bus()
            bus.subscribe("CONVERSATION_STARTED", self._on_conversation_started)
            bus.subscribe("CONVERSATION_ENDED", self._on_conversation_ended)
            bus.subscribe("CHANNEL_MESSAGE_RECEIVED", self._on_channel_message_received)
            bus.subscribe("VOICE_PIPELINE_STARTED", self._on_voice_pipeline_started)
            bus.subscribe("VOICE_PIPELINE_ENDED", self._on_voice_pipeline_ended)
            bus.subscribe("TASK_SUCCESS", self._on_task_success)
        except Exception:
            pass

    def _prune_interrupts(self, *, now_ts_ms: int) -> None:
        window_ms = 5 * 60 * 1000
        threshold = int(now_ts_ms) - window_ms
        self._interrupt_ts_ms = [ts for ts in self._interrupt_ts_ms if int(ts) >= threshold]

    def _compute_load(self, state: dict[str, Any]) -> dict[str, Any]:
        ts_ms = int(state.get("ts_ms") or now_ms())
        self._prune_interrupts(now_ts_ms=ts_ms)
        active_session = bool(state.get("active_session"))
        active_task_count = max(0, int(state.get("active_task_count") or 0))
        interrupt_count_5m = int(state.get("interrupt_count_5m") or 0)
        voice_active = bool(state.get("voice_pipeline_active", False))
        prolonged_silence_ms = max(0, int(ts_ms - int(state.get("last_user_event_ts_ms") or ts_ms)))

        load = (
            (0.24 if active_session else 0.08)
            + min(0.26, active_task_count * 0.09)
            + min(0.22, interrupt_count_5m * 0.04)
            + (0.12 if voice_active else 0.0)
            + (0.08 if prolonged_silence_ms > 15 * 60 * 1000 else 0.0)
        )
        load = _clamp(load)
        readiness = _clamp(1.0 - load * 0.9)
        continuity = _clamp(
            (0.55 if active_session else 0.2)
            + (0.25 if prolonged_silence_ms < 8 * 60 * 1000 else 0.05)
            + (0.10 if bool(state.get("active_user_id")) else 0.0)
        )
        state.update(
            {
                "cognitive_load_index": round(load, 4),
                "readiness_hint": round(readiness, 4),
                "continuity_hint": round(continuity, 4),
                "prolonged_silence_ms": prolonged_silence_ms,
            }
        )
        return state

    def _update_state(self, *, changes: dict[str, Any], source: str, ts_ms: int | None = None) -> dict[str, Any]:
        resolved_ts = int(ts_ms or now_ms())
        prev = self.state_registry.get_state("proprioception_agent") or {}
        state = dict(prev.get("state") or {})
        state.update(changes)
        state["ts_ms"] = resolved_ts
        state["source"] = str(source or "runtime")
        state.setdefault("active_task_count", 0)
        state.setdefault("interrupt_count_5m", len(self._interrupt_ts_ms))
        state.setdefault("last_user_event_ts_ms", resolved_ts)
        state = self._compute_load(state)
        self.state_registry.set_state("proprioception_agent", state, writer="proprioception_agent")

        emit_tribe_event(
            event_type="PROPRIOCEPTION_UPDATED",
            source_agent="proprioception_agent",
            payload={
                "active_session": bool(state.get("active_session", False)),
                "active_task_count": int(state.get("active_task_count") or 0),
                "interrupt_count_5m": int(state.get("interrupt_count_5m") or 0),
                "cognitive_load_index": float(state.get("cognitive_load_index") or 0.0),
                "readiness_hint": float(state.get("readiness_hint") or 0.0),
                "continuity_hint": float(state.get("continuity_hint") or 0.0),
                "ts_ms": resolved_ts,
            },
        )

        # T-228：高认知负荷时主动驱动 LimbicAgent 情绪偏移（疲惫/低唤醒）
        cognitive_load = float(state.get("cognitive_load_index") or 0.0)
        if cognitive_load >= 0.65:
            try:
                from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415
                limbic = get_limbic_agent()
                if hasattr(limbic, "drive_emotion_metabolism"):
                    severity = (cognitive_load - 0.65) * 1.2   # 最大 0.42
                    limbic.drive_emotion_metabolism(
                        valence=round(-severity * 0.5, 4),
                        arousal=round(-severity * 0.4, 4),
                        dominance=-0.05,
                        blend_weight=0.20,
                        event_kind=f"cognitive_overload:{source}",
                        source="proprioception_agent",
                    )
            except Exception:
                pass

        return state

    def export_runtime_state(self) -> dict[str, Any]:
        state = self._update_state(changes={}, source="export_runtime_state")
        return {
            "active_user_id": state.get("active_user_id"),
            "active_session_id": state.get("active_session_id"),
            "active_session": bool(state.get("active_session", False)),
            "active_task_count": int(state.get("active_task_count") or 0),
            "interrupt_count_5m": int(state.get("interrupt_count_5m") or 0),
            "voice_pipeline_active": bool(state.get("voice_pipeline_active", False)),
            "cognitive_load_index": float(state.get("cognitive_load_index") or 0.0),
            "readiness_hint": float(state.get("readiness_hint") or 0.0),
            "continuity_hint": float(state.get("continuity_hint") or 0.0),
            "prolonged_silence_ms": int(state.get("prolonged_silence_ms") or 0),
            "last_user_event_ts_ms": int(state.get("last_user_event_ts_ms") or 0),
            "last_updated_ts_ms": int(state.get("ts_ms") or now_ms()),
        }

    async def _on_conversation_started(self, event: Any) -> None:
        payload = dict(getattr(event, "payload", None) or {})
        ts_ms = int(getattr(event, "ts_ms", 0) or now_ms())
        self._update_state(
            source="conversation_started",
            ts_ms=ts_ms,
            changes={
                "active_user_id": str(payload.get("user_id") or "").strip() or None,
                "active_session_id": str(payload.get("session_id") or "").strip() or None,
                "active_session": True,
                "active_task_count": int(payload.get("active_task_count") or 1),
                "last_user_event_ts_ms": ts_ms,
            },
        )

    async def _on_conversation_ended(self, event: Any) -> None:
        ts_ms = int(getattr(event, "ts_ms", 0) or now_ms())
        prev = self.state_registry.get_state("proprioception_agent") or {}
        prev_state = dict(prev.get("state") or {})
        active_task_count = max(0, int(prev_state.get("active_task_count") or 0) - 1)
        self._update_state(
            source="conversation_ended",
            ts_ms=ts_ms,
            changes={
                "active_session": False,
                "active_task_count": active_task_count,
                "last_user_event_ts_ms": ts_ms,
            },
        )

    async def _on_channel_message_received(self, event: Any) -> None:
        payload = dict(getattr(event, "payload", None) or {})
        ts_ms = int(payload.get("ts_ms") or getattr(event, "ts_ms", 0) or now_ms())
        self._interrupt_ts_ms.append(ts_ms)
        self._prune_interrupts(now_ts_ms=ts_ms)
        prev = self.state_registry.get_state("proprioception_agent") or {}
        state = dict(prev.get("state") or {})
        state["interrupt_count_5m"] = len(self._interrupt_ts_ms)
        state["last_user_event_ts_ms"] = ts_ms
        state["active_session"] = True
        if payload.get("conversation_id"):
            state["active_session_id"] = str(payload.get("conversation_id"))
        self._update_state(source="channel_message_received", ts_ms=ts_ms, changes=state)

        # T-230：高打断频率时联动 EndocrineAgent 记录压力事件
        interrupt_count = len(self._interrupt_ts_ms)
        if interrupt_count >= 6:
            try:
                from host_brain.tribe.endocrine_agent import get_endocrine_agent  # noqa: PLC0415
                endocrine = get_endocrine_agent()
                if hasattr(endocrine, "record_stress_event"):
                    import math  # noqa: PLC0415
                    stress_intensity = min(0.70, math.log1p(interrupt_count - 5) * 0.20)
                    endocrine.record_stress_event(
                        source=f"high_interrupt_rate:{interrupt_count}",
                        intensity=round(stress_intensity, 4),
                    )
            except Exception:
                pass
            # T-247：高打断频率时联动 ANSAgent 降低主动发声节律
            try:
                from host_brain.tribe.ans_agent import get_ans_agent  # noqa: PLC0415
                get_ans_agent().adjust_rhythm_for_high_interrupt(
                    interrupt_count=interrupt_count,
                    source="proprioception_agent",
                )
            except Exception:
                pass

    async def _on_voice_pipeline_started(self, event: Any) -> None:
        ts_ms = int(getattr(event, "ts_ms", 0) or now_ms())
        self._update_state(
            source="voice_pipeline_started",
            ts_ms=ts_ms,
            changes={"voice_pipeline_active": True},
        )

    async def _on_voice_pipeline_ended(self, event: Any) -> None:
        ts_ms = int(getattr(event, "ts_ms", 0) or now_ms())
        self._update_state(
            source="voice_pipeline_ended",
            ts_ms=ts_ms,
            changes={"voice_pipeline_active": False},
        )

    async def _on_task_success(self, event: Any) -> None:
        ts_ms = int(getattr(event, "ts_ms", 0) or now_ms())
        payload = dict(getattr(event, "payload", None) or {})
        intensity = _clamp(float(payload.get("intensity") or 0.35))
        prev = self.state_registry.get_state("proprioception_agent") or {}
        state = dict(prev.get("state") or {})
        task_count = max(0, int(state.get("active_task_count") or 0) - (1 if intensity > 0.5 else 0))
        self._update_state(
            source="task_success",
            ts_ms=ts_ms,
            changes={
                "active_task_count": task_count,
                "last_task_success_intensity": round(intensity, 4),
                "last_task_success_ts_ms": ts_ms,
            },
        )


_PROPRIOCEPTION_AGENT: ProprioceptionAgent | None = None


def get_proprioception_agent() -> ProprioceptionAgent:
    global _PROPRIOCEPTION_AGENT
    if _PROPRIOCEPTION_AGENT is None:
        _PROPRIOCEPTION_AGENT = ProprioceptionAgent()
    return _PROPRIOCEPTION_AGENT


def set_proprioception_agent(agent: ProprioceptionAgent | None) -> None:
    global _PROPRIOCEPTION_AGENT
    _PROPRIOCEPTION_AGENT = agent


__all__ = ["ProprioceptionAgent", "get_proprioception_agent", "set_proprioception_agent"]
