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
class GlymphaticAgent:
    descriptor: TribeAgentDescriptor = field(
        default_factory=lambda: TribeAgentDescriptor(
            agent_id="glymphatic_agent",
            family="glymphatic",
            execution_side="host",
            owner_domain="brain",
            capabilities=("recovery_cycle", "memory_consolidation", "noise_decay"),
        )
    )
    state_registry: TribeStateRegistry = field(default_factory=get_global_registry, repr=False)

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()

    def register_subscriptions(self) -> None:
        try:
            from host_brain.app.internal_tribe_routes import get_tribe_event_bus  # noqa: PLC0415

            bus = get_tribe_event_bus()
            bus.subscribe("CIRCADIAN_PHASE_UPDATED", self._on_circadian_phase_updated)
            bus.subscribe("CONVERSATION_ENDED", self._on_conversation_ended)
            bus.subscribe("DEFAULT_MODE_TICKED", self._on_default_mode_ticked)
        except Exception:
            pass

    def run_recovery_cycle(
        self,
        *,
        source: str = "runtime",
        force: bool = False,
        now_ts_ms: int | None = None,
    ) -> dict[str, Any]:
        resolved_ts = int(now_ts_ms or now_ms())
        vital_signs = self.state_registry.aggregate_vital_signs()
        phase = str(vital_signs.get("circadian_phase") or "day").strip().lower()
        sleep_pressure = _clamp(float(vital_signs.get("sleep_pressure") or 0.2))
        recovery_window = force or phase in {"night", "dusk"} or sleep_pressure >= 0.62
        homeostasis = self.state_registry.get_homeostasis_report(now_ts_ms=resolved_ts)
        status = str(homeostasis.get("status") or "stable").strip().lower()
        try:
            from host_brain.tribe.hippocampus_agent import get_hippocampus_agent  # noqa: PLC0415

            memory_layers = get_hippocampus_agent().resolve_memory_layer_snapshot()
        except Exception:
            memory_layers = {}
        try:
            from host_brain.tribe.default_mode_agent import get_default_mode_agent  # noqa: PLC0415

            default_mode_state = get_default_mode_agent().export_runtime_state()
        except Exception:
            default_mode_state = {}

        continuity = _clamp(float(default_mode_state.get("continuity_score") or vital_signs.get("continuity_hint") or 0.2))
        stress = _clamp(float(vital_signs.get("stress_index") or 0.0))
        load = _clamp(float(vital_signs.get("cognitive_load_index") or 0.2))
        loneliness = _clamp(float(vital_signs.get("loneliness_level") or 0.0))

        consolidation_score = _clamp((continuity * 0.36) + ((1.0 - stress) * 0.28) + ((1.0 - load) * 0.24) + ((1.0 - loneliness) * 0.12))
        noise_decay_factor = _clamp((sleep_pressure * 0.45) + (0.25 if status != "stable" else 0.08) + (load * 0.15))
        result = {
            "executed": bool(recovery_window),
            "reason_code": "RECOVERY_WINDOW_OPEN" if recovery_window else "RECOVERY_WINDOW_CLOSED",
            "phase": phase,
            "sleep_pressure": round(sleep_pressure, 4),
            "consolidation_score": round(consolidation_score if recovery_window else 0.0, 4),
            "noise_decay_factor": round(noise_decay_factor if recovery_window else 0.0, 4),
            "memory_layers_available": bool(memory_layers),
            "memory_write_type": str(memory_layers.get("last_write_type") or ""),
            "homeostasis_status": status,
            "source": str(source or "runtime"),
            "ts_ms": resolved_ts,
        }

        # T-202：恢复窗口期且巩固分数达标时，实际写入语义记忆
        memory_written = False
        if recovery_window and consolidation_score >= 0.45:
            try:
                from host_brain.tribe.hippocampus_agent import get_hippocampus_agent  # noqa: PLC0415
                hippocampus = get_hippocampus_agent()
                if hasattr(hippocampus, "write_semantic_memory_fact"):
                    # T-214：修复：补充 person_id 参数，confidence 通过 source_refs 携带
                    hippocampus.write_semantic_memory_fact(
                        person_id="system",
                        fact=f"glymphatic_consolidation:{resolved_ts}:score={round(consolidation_score, 4)}",
                        source_refs=[f"consolidation_score:{round(consolidation_score, 4)}"],
                    )
                    memory_written = True
            except Exception:
                pass
        result["memory_written"] = memory_written
        prev = self.state_registry.get_state("glymphatic_agent") or {}
        prev_state = dict(prev.get("state") or {})
        prev_state["last_cycle"] = result
        prev_state["cycle_count"] = int(prev_state.get("cycle_count") or 0) + 1
        self.state_registry.set_state("glymphatic_agent", prev_state, writer="glymphatic_agent")
        emit_tribe_event(
            event_type="GLYMPHATIC_CYCLE_COMPLETED",
            source_agent="glymphatic_agent",
            payload=result,
        )

        # T-290：恢复周期完成且巩固分数达标时，联动 LimbicAgent 触发轻度正向情绪偏移（恢复感）
        if recovery_window and consolidation_score >= 0.45:
            try:
                from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415
                _limbic = get_limbic_agent()
                if hasattr(_limbic, "drive_emotion_metabolism"):
                    _recovery_warmth = min(0.18, consolidation_score * 0.20)
                    _limbic.drive_emotion_metabolism(
                        valence=round(_recovery_warmth * 0.65, 4),
                        arousal=round(-_recovery_warmth * 0.30, 4),  # 恢复后略微降低唤醒
                        dominance=0.04,
                        blend_weight=0.12,
                        event_kind=f"glymphatic_recovery:score={round(consolidation_score, 3)}",
                        source="glymphatic_agent",
                    )
            except Exception:
                pass

            # T-302：恢复周期完成时联动 InteroceptionAgent 降低 recovery_urgency（身体压力缓解）
            try:
                from host_brain.tribe.interoception_agent import get_interoception_agent  # noqa: PLC0415
                _intero = get_interoception_agent()
                if hasattr(_intero, "reduce_recovery_urgency"):
                    _relief = min(0.60, consolidation_score * 0.65)
                    _intero.reduce_recovery_urgency(
                        source=f"glymphatic_recovery:score={round(consolidation_score, 3)}",
                        relief=round(_relief, 4),
                        ts_ms=resolved_ts,
                    )
            except Exception:
                pass

            # T-339：恢复周期完成且巩固分数达标时，联动 PainRewardAgent 记录深度清理奖励
            # 深度清理是最强的正向强化之一（类似睡眠后的清醒感）
            try:
                from host_brain.tribe.pain_reward_agent import get_pain_reward_agent  # noqa: PLC0415
                _pr = get_pain_reward_agent()
                if hasattr(_pr, "record_reward"):
                    _reward_intensity = round(min(0.55, consolidation_score * 0.60), 4)
                    _pr.record_reward(
                        source=f"glymphatic_deep_clean:score={round(consolidation_score, 3)}",
                        intensity=_reward_intensity,
                        metadata={
                            "consolidation_score": round(consolidation_score, 4),
                            "noise_decay_factor": round(noise_decay_factor, 4),
                            "phase": phase,
                            "memory_written": memory_written,
                        },
                    )
            except Exception:
                pass

        return result

    def export_runtime_state(self) -> dict[str, Any]:
        """T-241：读取 state_registry 快照，不触发 run_recovery_cycle（避免副作用）。"""
        prev = self.state_registry.get_state("glymphatic_agent") or {}
        prev_state = dict(prev.get("state") or {})
        last_cycle = dict(prev_state.get("last_cycle") or {})
        return {
            "executed": bool(last_cycle.get("executed", False)),
            "reason_code": str(last_cycle.get("reason_code") or "RECOVERY_WINDOW_CLOSED"),
            "phase": str(last_cycle.get("phase") or "day"),
            "sleep_pressure": float(last_cycle.get("sleep_pressure") or 0.0),
            "consolidation_score": float(last_cycle.get("consolidation_score") or 0.0),
            "noise_decay_factor": float(last_cycle.get("noise_decay_factor") or 0.0),
            "homeostasis_status": str(last_cycle.get("homeostasis_status") or "stable"),
            "memory_written": bool(last_cycle.get("memory_written", False)),
            "cycle_count": int(prev_state.get("cycle_count") or 0),
            "last_updated_ts_ms": int(last_cycle.get("ts_ms") or 0),
        }

    async def _on_circadian_phase_updated(self, event: Any) -> None:
        self.run_recovery_cycle(
            source="circadian_phase_updated",
            now_ts_ms=int(getattr(event, "ts_ms", 0) or now_ms()),
        )

    async def _on_conversation_ended(self, event: Any) -> None:
        self.run_recovery_cycle(
            source="conversation_ended",
            now_ts_ms=int(getattr(event, "ts_ms", 0) or now_ms()),
        )

    async def _on_default_mode_ticked(self, event: Any) -> None:
        self.run_recovery_cycle(
            source="default_mode_ticked",
            now_ts_ms=int(getattr(event, "ts_ms", 0) or now_ms()),
        )


_GLYMPHATIC_AGENT: GlymphaticAgent | None = None


def get_glymphatic_agent() -> GlymphaticAgent:
    global _GLYMPHATIC_AGENT
    if _GLYMPHATIC_AGENT is None:
        _GLYMPHATIC_AGENT = GlymphaticAgent()
    return _GLYMPHATIC_AGENT


def set_glymphatic_agent(agent: GlymphaticAgent | None) -> None:
    global _GLYMPHATIC_AGENT
    _GLYMPHATIC_AGENT = agent


__all__ = ["GlymphaticAgent", "get_glymphatic_agent", "set_glymphatic_agent"]
