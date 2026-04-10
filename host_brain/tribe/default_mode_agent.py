from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from host_brain.core.ids import now_ms
from host_brain.tribe.base_agent import TribeAgentDescriptor
from host_brain.tribe.events import emit_tribe_event
from host_brain.tribe.tribe_state import TribeStateRegistry, get_global_registry


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


@dataclass(slots=True)
class DefaultModeAgent:
    descriptor: TribeAgentDescriptor = field(
        default_factory=lambda: TribeAgentDescriptor(
            agent_id="default_mode_agent",
            family="default_mode",
            execution_side="host",
            owner_domain="brain",
            capabilities=("inner_activity", "idle_reflection", "continuity_stream"),
        )
    )
    state_registry: TribeStateRegistry = field(default_factory=get_global_registry, repr=False)

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()

    def register_subscriptions(self) -> None:
        try:
            from host_brain.app.internal_tribe_routes import get_tribe_event_bus  # noqa: PLC0415

            bus = get_tribe_event_bus()
            bus.subscribe("INNER_WORLD_TICKED", self._on_inner_world_ticked)
            bus.subscribe("CONVERSATION_ENDED", self._on_conversation_ended)
        except Exception:
            pass

    def tick_idle_cycle(
        self,
        *,
        person_id: str = "user_1",
        topic_phrase: str = "",
        source: str = "runtime",
        now_ts_ms: int | None = None,
        inner_state: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        from host_brain.persona.inner_activity_agent import generate_inner_activity_note  # noqa: PLC0415
        from host_brain.persona.motive_runtime import build_offline_state_from_inner_state  # noqa: PLC0415

        user_id = str(person_id or "user_1").strip() or "user_1"
        ts_ms = int(now_ts_ms or now_ms())
        resolved_inner_state = dict(inner_state or {})
        if not resolved_inner_state:
            try:
                from host_brain.persona.inner_world import get_inner_world  # noqa: PLC0415

                world = get_inner_world()
                resolved_inner_state = dict(world.get_state(user_id=user_id) or {})
            except Exception:
                resolved_inner_state = {}

        offline_state = dict(build_offline_state_from_inner_state(resolved_inner_state, now_ms=ts_ms) or {})
        absence_band = str(offline_state.get("absence_band") or "recent")
        offline_hours = float(offline_state.get("offline_hours") or 0.0)
        note = ""
        focus = "self"
        reason = ""
        llm_meta: dict[str, Any] = {}
        try:
            note, focus, reason, llm_meta = generate_inner_activity_note(
                persona_user_id=user_id,
                absence_band=absence_band,
                offline_hours=offline_hours,
                inner_state=resolved_inner_state,
                now_ms=ts_ms,
                topic_phrase=str(topic_phrase or ""),
            )
        except Exception as exc:
            reason = f"DEFAULT_MODE_NOTE_FAILED:{str(exc)[:64]}"

        if not note:
            anticipation = list(offline_state.get("anticipation_topics") or [])
            topic = str(anticipation[0] if anticipation else topic_phrase).strip()
            note = (
                f"我在整理刚才的感受，也在想{topic}。"
                if topic
                else "我在把最近的情绪和记忆慢慢整理成线索。"
            )
            focus = "plan" if topic else "self"

        loneliness = _clamp(float(offline_state.get("loneliness_level") or 0.0))
        anticipation_level = _clamp(float(offline_state.get("anticipation_level") or 0.0))
        continuity_score = _clamp(float(offline_state.get("continuity_score") or 0.5))
        proactive_candidate = bool(
            continuity_score >= 0.45 and (loneliness >= 0.55 or anticipation_level >= 0.60)
        )
        stream = list(resolved_inner_state.get("inner_stream") or [])
        stream_len = len(stream)
        result = {
            "user_id": user_id,
            "note": str(note).strip(),
            "focus": str(focus or "self").strip().lower() or "self",
            "source": str(source or "runtime").strip() or "runtime",
            "offline_state": {
                "absence_band": absence_band,
                "offline_hours": round(offline_hours, 4),
                "loneliness_level": round(loneliness, 4),
                "anticipation_level": round(anticipation_level, 4),
                "continuity_score": round(continuity_score, 4),
            },
            "proactive_voice_candidate": proactive_candidate,
            "inner_stream_size": stream_len,
            "reason_code": str(reason or ""),
            "llm_meta": dict(llm_meta or {}),
            "ts_ms": ts_ms,
        }
        prev = self.state_registry.get_state("default_mode_agent") or {}
        prev_state = dict(prev.get("state") or {})
        prev_state["last_tick"] = result
        prev_state["last_note"] = result["note"]
        prev_state["last_focus"] = result["focus"]
        prev_state["last_offline_state"] = dict(result["offline_state"])
        prev_state["proactive_voice_candidate"] = bool(result["proactive_voice_candidate"])
        prev_state["inner_stream_size"] = stream_len
        prev_state["last_tick_source"] = result["source"]
        prev_state["last_tick_ts_ms"] = ts_ms
        prev_state["tick_count"] = int(prev_state.get("tick_count") or 0) + 1
        self.state_registry.set_state("default_mode_agent", prev_state, writer="default_mode_agent")

        emit_tribe_event(
            event_type="DEFAULT_MODE_TICKED",
            source_agent="default_mode_agent",
            payload={
                "user_id": user_id,
                "focus": result["focus"],
                "continuity_score": result["offline_state"]["continuity_score"],
                "proactive_voice_candidate": proactive_candidate,
                "ts_ms": ts_ms,
            },
        )
        return result

    def export_runtime_state(self) -> dict[str, Any]:
        snap = self.state_registry.get_state("default_mode_agent") or {}
        state = dict(snap.get("state") or {})
        if not state:
            self.tick_idle_cycle(source="export_runtime_state")
            snap = self.state_registry.get_state("default_mode_agent") or {}
            state = dict(snap.get("state") or {})
        last_tick = dict(state.get("last_tick") or {})
        offline_state = dict(last_tick.get("offline_state") or state.get("last_offline_state") or {})
        return {
            "last_note": str(state.get("last_note") or ""),
            "last_focus": str(state.get("last_focus") or "self"),
            "proactive_voice_candidate": bool(state.get("proactive_voice_candidate", False)),
            "continuity_score": float(offline_state.get("continuity_score") or 0.5),
            "loneliness_level": float(offline_state.get("loneliness_level") or 0.0),
            "anticipation_level": float(offline_state.get("anticipation_level") or 0.0),
            "inner_stream_size": int(state.get("inner_stream_size") or 0),
            "tick_count": int(state.get("tick_count") or 0),
            "last_tick_ts_ms": int(state.get("last_tick_ts_ms") or now_ms()),
        }

    async def _on_inner_world_ticked(self, event: Any) -> None:
        payload = dict(getattr(event, "payload", None) or {})
        ts_ms = int(getattr(event, "ts_ms", 0) or now_ms())
        self.tick_idle_cycle(
            person_id=str(payload.get("user_id") or "user_1"),
            topic_phrase=str(payload.get("topic_id") or ""),
            source="inner_world_ticked",
            now_ts_ms=ts_ms,
        )

    async def _on_conversation_ended(self, event: Any) -> None:
        payload = dict(getattr(event, "payload", None) or {})
        ts_ms = int(getattr(event, "ts_ms", 0) or now_ms())
        self.tick_idle_cycle(
            person_id=str(payload.get("user_id") or "user_1"),
            topic_phrase="",
            source="conversation_ended",
            now_ts_ms=ts_ms,
        )


_DEFAULT_MODE_AGENT: DefaultModeAgent | None = None


def get_default_mode_agent() -> DefaultModeAgent:
    global _DEFAULT_MODE_AGENT
    if _DEFAULT_MODE_AGENT is None:
        _DEFAULT_MODE_AGENT = DefaultModeAgent()
    return _DEFAULT_MODE_AGENT


def set_default_mode_agent(agent: DefaultModeAgent | None) -> None:
    global _DEFAULT_MODE_AGENT
    _DEFAULT_MODE_AGENT = agent


__all__ = ["DefaultModeAgent", "get_default_mode_agent", "set_default_mode_agent"]
