from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from host_brain.core.ids import now_ms
from host_brain.tribe.application_ports import TribeApplicationPorts
from host_brain.tribe.base_agent import TribeAgentDescriptor
from host_brain.tribe.events import emit_tribe_event
from host_brain.tribe.tribe_state import TribeStateRegistry, get_global_registry


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


@dataclass(slots=True)
class InteroceptionAgent:
    application_ports: TribeApplicationPorts | None = field(default=None, repr=False)
    descriptor: TribeAgentDescriptor = field(
        default_factory=lambda: TribeAgentDescriptor(
            agent_id="interoception_agent",
            family="interoception",
            execution_side="host",
            owner_domain="brain",
            capabilities=("interoceptive_index", "body_budget", "recovery_urgency"),
        )
    )
    state_registry: TribeStateRegistry = field(default_factory=get_global_registry, repr=False)

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()

    def register_subscriptions(self) -> None:
        try:
            from host_brain.tribe.event_bus import get_tribe_event_bus  # noqa: PLC0415

            bus = get_tribe_event_bus()
            bus.subscribe("CIRCADIAN_PHASE_UPDATED", self._on_state_signal)
            bus.subscribe("PROPRIOCEPTION_UPDATED", self._on_state_signal)
            bus.subscribe("EMOTION_STATE_CHANGED", self._on_state_signal)
            bus.subscribe("IMMUNE_BLOCK", self._on_state_signal)
            bus.subscribe("CONVERSATION_ENDED", self._on_state_signal)
        except Exception:
            pass

    def evaluate_profile(self, *, source: str = "runtime", ts_ms: int | None = None) -> dict[str, Any]:
        resolved_ts = int(ts_ms or now_ms())
        vital_signs = self.state_registry.aggregate_vital_signs()
        stress = _clamp(float(vital_signs.get("stress_index") or 0.0))
        cognitive_load = _clamp(float(vital_signs.get("cognitive_load_index") or 0.2))
        sleep_pressure = _clamp(float(vital_signs.get("sleep_pressure") or 0.2))
        energy = _clamp(float(vital_signs.get("energy_reserve") or 0.6))
        immune_risk = str(vital_signs.get("immune_risk_level") or "none").strip().lower()
        homeostasis_ok = bool(vital_signs.get("homeostasis_ok", True))
        somatic_fatigue = 0.0
        try:

            somatic_state = get_somatic_agent().export_runtime_state()
            somatic_fatigue = _clamp(float(somatic_state.get("fatigue_level") or 0.0))
        except Exception:
            pass

        immune_penalty = 0.0
        if immune_risk == "medium":
            immune_penalty = 0.08
        elif immune_risk in {"high", "critical"}:
            immune_penalty = 0.18

        interoceptive_index = _clamp(
            stress * 0.30 + cognitive_load * 0.25 + sleep_pressure * 0.20 + somatic_fatigue * 0.25 + immune_penalty
        )
        body_budget = _clamp((energy * 0.65 + (1.0 - interoceptive_index) * 0.35), low=0.0, high=1.0)
        recovery_urgency = _clamp(
            sleep_pressure * 0.45 + stress * 0.35 + cognitive_load * 0.20 + (0.18 if not homeostasis_ok else 0.0)
        )
        state = {
            "interoceptive_index": round(interoceptive_index, 4),
            "body_budget": round(body_budget, 4),
            "recovery_urgency": round(recovery_urgency, 4),
            "stress_index": round(stress, 4),
            "cognitive_load_index": round(cognitive_load, 4),
            "sleep_pressure": round(sleep_pressure, 4),
            "energy_reserve": round(energy, 4),
            "somatic_fatigue": round(somatic_fatigue, 4),
            "immune_risk_level": immune_risk,
            "homeostasis_ok": homeostasis_ok,
            "source": str(source or "runtime"),
            "ts_ms": resolved_ts,
        }
        prev = self.state_registry.get_state("interoception_agent") or {}
        prev_state = dict(prev.get("state") or {})
        prev_state.update(state)
        prev_state["sample_count"] = int(prev_state.get("sample_count") or 0) + 1
        self.state_registry.set_state("interoception_agent", prev_state, writer="interoception_agent")
        emit_tribe_event(
            event_type="INTEROCEPTION_UPDATED",
            source_agent="interoception_agent",
            payload={
                "interoceptive_index": state["interoceptive_index"],
                "body_budget": state["body_budget"],
                "recovery_urgency": state["recovery_urgency"],
                "ts_ms": resolved_ts,
            },
        )

        # T-210：高恢复紧迫度时主动触发 GlymphaticAgent 恢复周期
        if recovery_urgency >= 0.75:
            try:
                get_glymphatic_agent().run_recovery_cycle(
                    source="interoception_urgency",
                    force=True,
                    now_ts_ms=resolved_ts,
                )
            except Exception:
                pass

        # T-383：高内感受压力（interoceptive_index>=0.70）时联动 LimbicAgent 触发负向情绪偏移
        # 身体预算严重透支时，内感受信号会直接影响情绪基调（疲惫/焦虑/不适感）
        if interoceptive_index >= 0.70:
            try:
                _limbic = get_limbic_agent()
                if hasattr(_limbic, "drive_emotion_metabolism"):
                    _intero_valence = round(-(interoceptive_index - 0.70) * 0.50, 4)
                    _intero_arousal = round((interoceptive_index - 0.70) * 0.30, 4)
                    _limbic.drive_emotion_metabolism(
                        valence=_intero_valence,
                        arousal=_intero_arousal,
                        dominance=-0.04,
                        blend_weight=0.10,
                        event_kind=f"interoception_high_load:{round(interoceptive_index, 3)}",
                        source="interoception_agent",
                    )
            except Exception:
                pass

        # T-384：高恢复紧迫度（recovery_urgency>=0.75）时联动 PainRewardAgent 记录身体预算透支痛苦
        # 极度疲惫/睡眠压力是一种持续性躯体痛苦（身体预算严重透支）
        if recovery_urgency >= 0.75:
            try:
                _pr = get_pain_reward_agent()
                if hasattr(_pr, "record_pain"):
                    _urgency_pain = round(min(0.35, (recovery_urgency - 0.75) * 1.40), 4)
                    _pr.record_pain(
                        source=f"interoception_high_urgency:{round(recovery_urgency, 3)}",
                        intensity=_urgency_pain,
                        metadata={
                            "recovery_urgency": round(recovery_urgency, 4),
                            "interoceptive_index": round(interoceptive_index, 4),
                            "body_budget": round(body_budget, 4),
                        },
                    )
            except Exception:
                pass

        # T-385：低恢复紧迫度（recovery_urgency<0.20）时联动 PainRewardAgent 记录身体舒适奖励
        # 身体预算充盈、压力极低时是一种轻度正向奖励（舒适感/活力感）
        if recovery_urgency < 0.20:
            try:
                _pr = get_pain_reward_agent()
                if hasattr(_pr, "record_reward"):
                    _comfort_reward = round(min(0.20, (0.20 - recovery_urgency) * 1.00), 4)
                    _pr.record_reward(
                        source=f"interoception_low_urgency:{round(recovery_urgency, 3)}",
                        intensity=_comfort_reward,
                        metadata={
                            "recovery_urgency": round(recovery_urgency, 4),
                            "body_budget": round(body_budget, 4),
                            "energy_reserve": round(energy, 4),
                        },
                    )
            except Exception:
                pass

        return dict(prev_state)

    # ── 内感受叙事（A 级升级）────────────────────────────────────────────────

    def generate_interoceptive_narrative(
        self,
        *,
        interoception_index: float = 0.5,
        body_budget: float = 0.5,
        recovery_urgency: float = 0.0,
    ) -> str | None:
        """将内感受指数转化为身体感受叙事，注入 Prompt Phase XIII 情绪层。

        触发条件：interoception_index < 0.3 或 recovery_urgency > 0.7 或 body_budget < 0.3。
        调用：Flash 小模型。
        开关：INTEROCEPTION_NARRATIVE_ENABLED（默认 false）。

        Returns:
            内感受描述字符串，或 None（未触发/失败）。
        """
        _significant = (
            float(interoception_index or 0.5) < 0.3
            or float(recovery_urgency or 0.0) > 0.7
            or float(body_budget or 0.5) < 0.3
        )
        if not _significant:
            return None

        import logging as _logging  # noqa: PLC0415
        _log = _logging.getLogger("Natsume.tribe.interoception_agent")
        try:
            from host_brain.model_runtime.prompt_contract import resolve_prompt_contract_json  # noqa: PLC0415
            from host_brain.tribe.model_invocation import complete_tribe_json  # noqa: PLC0415

            _tpl = resolve_prompt_contract_json("interoception_agent.interoceptive_narrative") or {}
            _system_prompt = str(_tpl.get("system_prompt") or "")
            _user_tpl = str(_tpl.get("user_prompt_template") or "")
            if not _system_prompt or not _user_tpl:
                raise ValueError("interoception_agent.interoceptive_narrative 模板未配置")

            _user_prompt = _user_tpl.format(
                interoception_index=round(float(interoception_index), 3),
                body_budget=round(float(body_budget), 3),
                recovery_urgency=round(float(recovery_urgency), 3),
            )
            _parsed = complete_tribe_json(
                attempt_service=self.application_ports.require("model_attempt_service")(),
                semantic_id="interoception_narrative",
                system_prompt=_system_prompt,
                user_prompt=_user_prompt,
            )
            _hint = str(_parsed.get("interoceptive_hint") or "").strip()[:40]
            if _hint:
                _log.debug("InteroceptionAgent.generate_interoceptive_narrative: %s", _hint)
                try:
                    self.state_registry.set_state(
                        "interoception_agent",
                        {"last_interoceptive_hint": _hint},
                        writer="interoception_agent",
                    )
                except Exception:
                    pass
            return _hint or None
        except Exception as exc:
            _log.debug("InteroceptionAgent.generate_interoceptive_narrative 失败（降级）: %s", exc)
            return None

    def export_runtime_state(self) -> dict[str, Any]:
        state = self.evaluate_profile(source="export_runtime_state")
        return {
            "interoceptive_index": float(state.get("interoceptive_index") or 0.0),
            "body_budget": float(state.get("body_budget") or 0.5),
            "recovery_urgency": float(state.get("recovery_urgency") or 0.0),
            "stress_index": float(state.get("stress_index") or 0.0),
            "cognitive_load_index": float(state.get("cognitive_load_index") or 0.0),
            "sleep_pressure": float(state.get("sleep_pressure") or 0.0),
            "energy_reserve": float(state.get("energy_reserve") or 0.6),
            "immune_risk_level": str(state.get("immune_risk_level") or "none"),
            "homeostasis_ok": bool(state.get("homeostasis_ok", True)),
            "sample_count": int(state.get("sample_count") or 0),
            "last_updated_ts_ms": int(state.get("ts_ms") or now_ms()),
        }

    async def _on_state_signal(self, event: Any) -> None:
        await asyncio.to_thread(
            self.evaluate_profile,
            source=str(getattr(event, "event_type", "") or "signal"),
            ts_ms=int(getattr(event, "ts_ms", 0) or now_ms()),
        )

    def inject_external_stress(
        self,
        *,
        source: str,
        intensity: float,
        ts_ms: int | None = None,
    ) -> None:
        """T-226：外部族注入压力信号，叠加到 recovery_urgency 上。

        由 SomaticAgent（孤独感）、ProprioceptionAgent（高打断）等外部族调用，
        将外部压力信号写入 interoception_agent 快照，下次 evaluate_profile 时生效。

        注意：intensity 仅接受正值（0.0~1.0），负值请使用 reduce_recovery_urgency()。
        """
        resolved_ts = int(ts_ms or now_ms())
        # 仅处理正向压力，负值静默忽略（负值应使用 reduce_recovery_urgency）
        raw = float(intensity)
        if raw <= 0.0:
            return
        clamped = _clamp(raw)
        prev = self.state_registry.get_state("interoception_agent") or {}
        prev_state = dict(prev.get("state") or {})
        # 叠加外部压力（最大不超过 0.85）
        current_urgency = _clamp(float(prev_state.get("recovery_urgency") or 0.0))
        new_urgency = _clamp(current_urgency + clamped * 0.35, high=0.85)
        prev_state.update({
            "recovery_urgency": round(new_urgency, 4),
            "last_external_stress_source": str(source or "unknown"),
            "last_external_stress_intensity": round(clamped, 4),
            "last_external_stress_ts_ms": resolved_ts,
        })
        self.state_registry.set_state("interoception_agent", prev_state, writer="interoception_agent")

    def reduce_recovery_urgency(
        self,
        *,
        source: str,
        relief: float,
        ts_ms: int | None = None,
    ) -> None:
        """T-296：外部族注入恢复/缓解信号，降低 recovery_urgency。

        由 PeripheralAgent（外设恢复）、GlymphaticAgent（恢复周期完成）等外部族调用，
        将恢复信号写入 interoception_agent 快照，下次 evaluate_profile 时生效。

        Args:
            source: 恢复信号来源标识（如 "peripheral_connected:qq"）。
            relief: 缓解强度（0.0~1.0），实际降幅 = relief * 0.30，最低降至 0.0。
            ts_ms: 时间戳（毫秒），默认使用当前时间。
        """
        resolved_ts = int(ts_ms or now_ms())
        clamped_relief = _clamp(float(relief))
        prev = self.state_registry.get_state("interoception_agent") or {}
        prev_state = dict(prev.get("state") or {})
        current_urgency = _clamp(float(prev_state.get("recovery_urgency") or 0.0))
        new_urgency = _clamp(current_urgency - clamped_relief * 0.30)
        prev_state.update({
            "recovery_urgency": round(new_urgency, 4),
            "last_relief_source": str(source or "unknown"),
            "last_relief_intensity": round(clamped_relief, 4),
            "last_relief_ts_ms": resolved_ts,
        })
        self.state_registry.set_state("interoception_agent", prev_state, writer="interoception_agent")

    def tick_body_budget(
        self,
        *,
        source: str = "scheduler",
        circadian_phase: str = "day",
    ) -> dict[str, Any]:
        """D7: 内感受指数持续衰减和恢复 tick——每 30s 由 NatsumeScheduler 驱动一次。

        模拟身体预算的连续动态特性：
        - 日间：身体预算缓慢消耗（活动消耗能量）
        - 夜间：身体预算缓慢恢复（睡眠补充能量）
        - 高内感受压力时自然衰减（压力不会无限累积）

        Returns:
            {"body_budget": float, "interoceptive_index": float, "recovery_urgency": float}
        """
        import logging as _logging
        _log = _logging.getLogger("Natsume.tribe.interoception_agent")
        try:
            prev = self.state_registry.get_state("interoception_agent") or {}
            prev_state = dict(prev.get("state") or {})

            cur_budget = _clamp(float(prev_state.get("body_budget") or 0.7))
            cur_intero = _clamp(float(prev_state.get("interoceptive_index") or 0.3))
            cur_urgency = _clamp(float(prev_state.get("recovery_urgency") or 0.2))

            phase = str(circadian_phase or "day").strip().lower()

            # 身体预算：日间缓慢消耗，夜间缓慢恢复
            budget_delta = {
                "day":   -0.002,   # 日间：轻微消耗
                "dusk":  -0.001,   # 黄昏：极轻微消耗
                "night":  0.004,   # 夜间：恢复
                "dawn":   0.002,   # 黎明：开始恢复
            }.get(phase, -0.001)
            new_budget = _clamp(cur_budget + budget_delta)

            # 内感受压力：高压力时自然衰减（不会无限累积）
            intero_decay = 0.003 if cur_intero > 0.5 else 0.001
            new_intero = _clamp(cur_intero - intero_decay)

            # 恢复紧迫度：随身体预算恢复而降低
            urgency_delta = -0.002 if new_budget > cur_budget else 0.001
            new_urgency = _clamp(cur_urgency + urgency_delta)

            prev_state.update({
                "body_budget": round(new_budget, 4),
                "interoceptive_index": round(new_intero, 4),
                "recovery_urgency": round(new_urgency, 4),
                "last_tick_source": source,
                "last_tick_phase": phase,
            })
            self.state_registry.set_state("interoception_agent", prev_state, writer="interoception_agent")

            _log.debug(
                "InteroceptionAgent.tick_body_budget: phase=%s budget=%.3f→%.3f "
                "intero=%.3f→%.3f urgency=%.3f→%.3f",
                phase, cur_budget, new_budget, cur_intero, new_intero, cur_urgency, new_urgency,
            )
            return {
                "body_budget": round(new_budget, 4),
                "interoceptive_index": round(new_intero, 4),
                "recovery_urgency": round(new_urgency, 4),
                "phase": phase,
                "ticked": True,
            }
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.interoception_agent").warning(
                "InteroceptionAgent.tick_body_budget 失败（降级）: %s", exc
            )
            return {"ticked": False, "error": str(exc)}


def get_interoception_agent() -> InteroceptionAgent:
    from host_brain.tribe.runtime_context import (  # noqa: PLC0415
        require_active_tribe_agent,
    )

    return require_active_tribe_agent(
        "interoception_agent",
        expected_type=InteroceptionAgent,
    )


__all__ = ["InteroceptionAgent", "get_interoception_agent"]

# ── 跨模块引用（供测试 monkeypatch 使用）────────────────────────────────────
from host_brain.tribe.glymphatic_agent import get_glymphatic_agent  # noqa: E402, F401
from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: E402, F401
from host_brain.tribe.pain_reward_agent import get_pain_reward_agent  # noqa: E402, F401
from host_brain.tribe.somatic_agent import get_somatic_agent  # noqa: E402, F401
