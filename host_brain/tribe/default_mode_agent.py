from __future__ import annotations

import asyncio
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Mapping

from host_brain.core.ids import now_ms
from host_brain.tribe.base_agent import TribeAgentDescriptor
from host_brain.tribe.events import emit_tribe_event
from host_brain.tribe.tribe_state import TribeStateRegistry, get_global_registry


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _env_bool(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return bool(default)
    normalized = str(raw).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _background_tasks_enabled() -> bool:
    return _env_bool("HOST_BACKGROUND_TASKS_ENABLED", default=True)


def _resolve_foreground_chat_cooldown_ms() -> int:
    raw = str(os.environ.get("HOST_DEFAULT_MODE_FOREGROUND_CHAT_COOLDOWN_MS") or "15000").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 15_000
    return max(0, min(value, 120_000))


def _resolve_background_tick_foreground_cooldown_ms() -> int:
    raw = str(os.environ.get("HOST_BACKGROUND_TICK_FOREGROUND_CHAT_COOLDOWN_MS") or "90000").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 90_000
    return max(0, min(value, 300_000))


_FOREGROUND_INTERACTION_LOCK = threading.Lock()
_FOREGROUND_INTERACTION_CACHE: dict[str, Any] = {
    "ts_ms": 0,
}


def mark_recent_foreground_interaction(
    *,
    ts_ms: int | None = None,
) -> None:
    with _FOREGROUND_INTERACTION_LOCK:
        _FOREGROUND_INTERACTION_CACHE["ts_ms"] = int(ts_ms or now_ms())


def _resolve_recent_foreground_interaction(*, current_ts_ms: int) -> dict[str, Any]:
    cooldown_ms = _resolve_foreground_chat_cooldown_ms()
    with _FOREGROUND_INTERACTION_LOCK:
        last_ts_ms = int(_FOREGROUND_INTERACTION_CACHE.get("ts_ms") or 0)
    active = cooldown_ms > 0 and last_ts_ms > 0 and (int(current_ts_ms) - last_ts_ms) < cooldown_ms
    return {
        "active": bool(active),
        "cooldown_ms": cooldown_ms,
        "last_ts_ms": last_ts_ms,
    }


def resolve_recent_foreground_interaction_state(*, current_ts_ms: int | None = None) -> dict[str, Any]:
    return _resolve_recent_foreground_interaction(current_ts_ms=int(current_ts_ms or now_ms()))


def resolve_background_tick_guard_state(*, current_ts_ms: int | None = None) -> dict[str, Any]:
    ts_ms = int(current_ts_ms or now_ms())
    recent = _resolve_recent_foreground_interaction(current_ts_ms=ts_ms)
    cooldown_ms = _resolve_background_tick_foreground_cooldown_ms()
    last_ts_ms = int(recent.get("last_ts_ms") or 0)
    active = cooldown_ms > 0 and last_ts_ms > 0 and (ts_ms - last_ts_ms) < cooldown_ms
    remaining_ms = max(0, cooldown_ms - max(0, ts_ms - last_ts_ms)) if active else 0
    return {
        "active": bool(active),
        "cooldown_ms": cooldown_ms,
        "cooldown_remaining_ms": remaining_ms,
        "last_ts_ms": last_ts_ms,
    }


def _is_recursive_glymphatic_tick_source(source: str) -> bool:
    normalized = str(source or "").strip().lower()
    return normalized.startswith("glymphatic_deep_clean")


def _build_structural_idle_note(*, topic: str) -> str:
    # Internal state only: never render this as a user-visible chat reply.
    clean_topic = str(topic or "").strip()
    return f"tracking topic: {clean_topic[:48]}" if clean_topic else ""


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
        background_enabled = _background_tasks_enabled()
        recent_foreground = _resolve_recent_foreground_interaction(current_ts_ms=ts_ms)
        suppress_llm_note = (
            not background_enabled
            or (source in {"conversation_ended", "inner_world_ticked"} and bool(recent_foreground.get("active")))
        )
        if suppress_llm_note:
            reason = (
                "DEFAULT_MODE_BACKGROUND_TASKS_DISABLED"
                if not background_enabled
                else "DEFAULT_MODE_FOREGROUND_CHAT_COOLDOWN"
            )
            llm_meta = {
                "suppressed": True,
                "background_tasks_enabled": background_enabled,
                "foreground_cooldown_ms": int(recent_foreground.get("cooldown_ms") or 0),
            }
        else:
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
            note = _build_structural_idle_note(topic=topic)
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
        suppress_background_side_effects = (
            (not background_enabled)
            or (source in {"conversation_ended", "inner_world_ticked"} and bool(recent_foreground.get("active")))
            or _is_recursive_glymphatic_tick_source(source)
        )
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

        if suppress_background_side_effects:
            result["background_side_effects_suppressed"] = True
            if not background_enabled:
                result["reason_code"] = "DEFAULT_MODE_BACKGROUND_TASKS_DISABLED"
                result["llm_meta"].setdefault("background_tasks_enabled", False)
            if _is_recursive_glymphatic_tick_source(source):
                result["reason_code"] = "DEFAULT_MODE_GLYMPHATIC_RECURSION_SUPPRESSED"
                result["llm_meta"].setdefault("recursive_glymphatic_source", True)
            result["llm_meta"].setdefault("background_side_effects_suppressed", True)
            return result

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

        # T-218：默认模式激活且连续性分数达标时，触发 GlymphaticAgent 恢复周期
        if continuity_score >= 0.55:
            try:
                get_glymphatic_agent().run_recovery_cycle(
                    source="default_mode_activated",
                    force=False,
                    now_ts_ms=ts_ms,
                )
            except Exception:
                pass

        # T-281：高连续性(>=0.60)时联动 ANSAgent 退出 recovery_mode，提高主动发声倾向
        if continuity_score >= 0.60:
            try:
                _ans = get_ans_agent()
                if hasattr(_ans, "set_recovery_mode"):
                    _ans.set_recovery_mode(False, source="default_mode_high_continuity")
                    import logging  # noqa: PLC0415
                    logging.getLogger("Natsume.tribe.default_mode_agent").debug(
                        "DefaultModeAgent T-281: continuity_score=%.3f → ANSAgent recovery_mode=False",
                        continuity_score,
                    )
            except Exception:
                pass

        # T-310：高连续性(>=0.55)时联动 InteroceptionAgent 降低 recovery_urgency（默认模式恢复是正向信号）
        if continuity_score >= 0.55:
            try:
                _intero = get_interoception_agent()
                if hasattr(_intero, "reduce_recovery_urgency"):
                    _relief = round(min(0.35, (continuity_score - 0.55) * 0.70), 4)
                    _intero.reduce_recovery_urgency(
                        source=f"default_mode_continuity:{round(continuity_score, 3)}",
                        relief=_relief,
                        ts_ms=ts_ms,
                    )
            except Exception:
                pass

        # T-357：高连续性(>=0.70)时联动 PainRewardAgent 记录自我完整感奖励
        # 高连续性意味着自我叙事完整，是深层的正向强化（"我是完整的我"）
        if continuity_score >= 0.70:
            try:
                _pr = get_pain_reward_agent()
                if hasattr(_pr, "record_reward"):
                    _continuity_reward = round(min(0.40, (continuity_score - 0.70) * 1.33), 4)
                    _pr.record_reward(
                        source=f"default_mode_high_continuity:{round(continuity_score, 3)}",
                        intensity=_continuity_reward,
                        metadata={
                            "continuity_score": round(continuity_score, 4),
                            "user_id": user_id,
                            "proactive_candidate": proactive_candidate,
                        },
                    )
            except Exception:
                pass
            # T-365：高连续性时联动 LimbicAgent 触发温和正向情绪（自我完整感带来平静满足）
            try:
                _limbic = get_limbic_agent()
                if hasattr(_limbic, "drive_emotion_metabolism"):
                    _cont_valence = round(min(0.14, (continuity_score - 0.70) * 0.47), 4)
                    _limbic.drive_emotion_metabolism(
                        valence=_cont_valence,
                        arousal=round(-_cont_valence * 0.30, 4),  # 高连续性时略微降低唤醒（平静感）
                        dominance=0.05,
                        blend_weight=0.09,
                        event_kind=f"default_mode_continuity:{round(continuity_score, 3)}",
                        source="default_mode_agent",
                    )
            except Exception:
                pass

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
        if not _background_tasks_enabled():
            return
        payload = dict(getattr(event, "payload", None) or {})
        ts_ms = int(getattr(event, "ts_ms", 0) or now_ms())
        await asyncio.to_thread(
            self.tick_idle_cycle,
            person_id=str(payload.get("user_id") or "user_1"),
            topic_phrase=str(payload.get("topic_id") or ""),
            source="inner_world_ticked",
            now_ts_ms=ts_ms,
        )

    async def _on_conversation_ended(self, event: Any) -> None:
        payload = dict(getattr(event, "payload", None) or {})
        if not _background_tasks_enabled():
            return
        if bool(payload.get("skip_default_mode_inner_activity")):
            return
        ts_ms = int(getattr(event, "ts_ms", 0) or now_ms())
        await asyncio.to_thread(
            self.tick_idle_cycle,
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


__all__ = [
    "DefaultModeAgent",
    "get_default_mode_agent",
    "set_default_mode_agent",
    "mark_recent_foreground_interaction",
]

# ── 跨模块引用（供测试 monkeypatch 使用）────────────────────────────────────
from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: E402, F401
from host_brain.tribe.pain_reward_agent import get_pain_reward_agent  # noqa: E402, F401
from host_brain.tribe.ans_agent import get_ans_agent  # noqa: E402, F401
from host_brain.tribe.interoception_agent import get_interoception_agent  # noqa: E402, F401
from host_brain.tribe.glymphatic_agent import get_glymphatic_agent  # noqa: E402, F401
