from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from host_brain.core.ids import now_ms
from host_brain.tribe.base_agent import TribeAgentDescriptor
from host_brain.tribe.events import emit_tribe_event
from host_brain.tribe.tribe_state import TribeStateRegistry, get_global_registry


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _phase_by_hour(hour: int) -> str:
    h = int(hour) % 24
    if 5 <= h < 8:
        return "dawn"
    if 8 <= h < 18:
        return "day"
    if 18 <= h < 22:
        return "dusk"
    return "night"


@dataclass(slots=True)
class CircadianAgent:
    descriptor: TribeAgentDescriptor = field(
        default_factory=lambda: TribeAgentDescriptor(
            agent_id="circadian_agent",
            family="circadian",
            execution_side="host",
            owner_domain="brain",
            capabilities=("circadian_phase", "proactive_budget", "recovery_window"),
        )
    )
    state_registry: TribeStateRegistry = field(default_factory=get_global_registry, repr=False)
    _last_phase: str = field(default="unknown", init=False, repr=False)

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()

    def register_subscriptions(self) -> None:
        try:
            from host_brain.app.internal_tribe_routes import get_tribe_event_bus  # noqa: PLC0415

            bus = get_tribe_event_bus()
            bus.subscribe("MODE_TRANSITION", self._on_mode_transition)
            bus.subscribe("CHANNEL_MESSAGE_RECEIVED", self._on_channel_message_received)
            bus.subscribe("VOICE_PIPELINE_STARTED", self._on_voice_pipeline_started)
            bus.subscribe("VOICE_PIPELINE_ENDED", self._on_voice_pipeline_ended)
        except Exception:
            pass

    def evaluate_profile(self, *, ts_ms: int | None = None) -> dict[str, Any]:
        resolved_ts = int(ts_ms or now_ms())
        dt = datetime.fromtimestamp(resolved_ts / 1000.0, tz=timezone.utc).astimezone()
        hour = int(dt.hour)
        phase = _phase_by_hour(hour)

        # 非最小版：完整日节律画像（主动预算 + 恢复窗口 + 延迟基调）
        phase_energy = {"dawn": 0.62, "day": 0.84, "dusk": 0.58, "night": 0.34}
        proactive_multiplier = {"dawn": 0.78, "day": 1.00, "dusk": 0.68, "night": 0.36}
        recovery_bias = {"dawn": 0.44, "day": 0.30, "dusk": 0.58, "night": 0.86}
        response_latency_ms = {"dawn": 540, "day": 360, "dusk": 650, "night": 940}
        sleep_pressure = _clamp(((hour + 2) % 24) / 24.0 if phase == "night" else (0.55 if phase == "dusk" else 0.2))

        return {
            "phase": phase,
            "local_hour": hour,
            "local_iso": dt.isoformat(),
            "phase_energy_baseline": phase_energy[phase],
            "proactive_multiplier": proactive_multiplier[phase],
            "recovery_bias": recovery_bias[phase],
            "sleep_pressure": round(sleep_pressure, 4),
            "response_latency_ms": response_latency_ms[phase],
            "ts_ms": resolved_ts,
        }

    def refresh_state(self, *, source: str = "runtime", ts_ms: int | None = None) -> dict[str, Any]:
        profile = self.evaluate_profile(ts_ms=ts_ms)
        prev = self.state_registry.get_state("circadian_agent") or {}
        prev_state = dict(prev.get("state") or {})
        previous_phase = str(prev_state.get("phase") or self._last_phase or "unknown")
        next_phase = str(profile.get("phase") or "unknown")
        prev_state.update(profile)
        prev_state["source"] = str(source or "runtime")
        self.state_registry.set_state("circadian_agent", prev_state, writer="circadian_agent")

        if previous_phase != next_phase:
            emit_tribe_event(
                event_type="CIRCADIAN_PHASE_UPDATED",
                source_agent="circadian_agent",
                payload={
                    "from_phase": previous_phase,
                    "to_phase": next_phase,
                    "local_hour": profile.get("local_hour"),
                    "proactive_multiplier": profile.get("proactive_multiplier"),
                    "sleep_pressure": profile.get("sleep_pressure"),
                    "ts_ms": profile.get("ts_ms"),
                },
            )
            # T-227：相位切换到 night 时，主动触发 GlymphaticAgent 恢复周期
            if next_phase == "night":
                try:
                    from host_brain.tribe.glymphatic_agent import get_glymphatic_agent  # noqa: PLC0415
                    get_glymphatic_agent().run_recovery_cycle(
                        source="circadian_night_phase",
                        force=False,
                        now_ts_ms=int(profile.get("ts_ms") or now_ms()),
                    )
                except Exception:
                    pass
                # T-258：夜间相位时联动 EndocrineAgent 降低 cortisol（夜间应降低压力激素）
                try:
                    from host_brain.tribe.endocrine_agent import get_endocrine_agent  # noqa: PLC0415
                    _endo = get_endocrine_agent()
                    if hasattr(_endo, "_bond_cortisol_delta"):
                        # 注入负 delta（降低 cortisol），通过 _bond_cortisol_delta 缓冲消费
                        with _endo._bond_lock:
                            _endo._bond_cortisol_delta = max(
                                -0.08,
                                float(_endo._bond_cortisol_delta) - 0.06,
                            )
                except Exception:
                    pass

            # T-292：日间/黎明相位切换时联动 ANSAgent 恢复 proactive_multiplier
            if next_phase in {"day", "dawn"}:
                try:
                    from host_brain.tribe.ans_agent import get_ans_agent  # noqa: PLC0415

                    _ans = get_ans_agent()
                    if hasattr(_ans, "set_recovery_mode"):
                        _ans.set_recovery_mode(False, source="circadian_day_phase")
                        import logging  # noqa: PLC0415

                        logging.getLogger("Natsume.tribe.circadian_agent").debug(
                            "CircadianAgent T-292: phase=%s → ANSAgent recovery_mode=False",
                            next_phase,
                        )
                except Exception:
                    pass
                # T-356：日间/黎明相位切换时联动 PainRewardAgent 记录清醒奖励
                # 日间清醒是轻度正向奖励（类似早晨醒来的清爽感）
                try:
                    from host_brain.tribe.pain_reward_agent import get_pain_reward_agent  # noqa: PLC0415
                    _pr = get_pain_reward_agent()
                    if hasattr(_pr, "record_reward"):
                        _wake_reward = 0.18 if next_phase == "day" else 0.12
                        _pr.record_reward(
                            source=f"circadian_phase:{next_phase}",
                            intensity=_wake_reward,
                            metadata={
                                "from_phase": previous_phase,
                                "to_phase": next_phase,
                                "local_hour": profile.get("local_hour"),
                            },
                        )
                except Exception:
                    pass
                # T-366：日间/黎明相位切换时联动 LimbicAgent 触发轻度活力情绪（日间清醒带来活力感）
                try:
                    from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415
                    _limbic = get_limbic_agent()
                    if hasattr(_limbic, "drive_emotion_metabolism"):
                        _wake_valence = 0.12 if next_phase == "day" else 0.08
                        _wake_arousal = 0.10 if next_phase == "day" else 0.06
                        _limbic.drive_emotion_metabolism(
                            valence=_wake_valence,
                            arousal=_wake_arousal,
                            dominance=0.04,
                            blend_weight=0.10,
                            event_kind=f"circadian_wake:{next_phase}",
                            source="circadian_agent",
                        )
                except Exception:
                    pass
                # T-377：日间/黎明相位切换时联动 DefaultModeAgent.tick_idle_cycle()
                # 日间清醒是自我叙事的重要节点（"新的一天开始了"），应更新连续性
                try:
                    from host_brain.tribe.default_mode_agent import get_default_mode_agent  # noqa: PLC0415
                    _dma = get_default_mode_agent()
                    if hasattr(_dma, "tick_idle_cycle"):
                        _dma.tick_idle_cycle(
                            source=f"circadian_wake:{next_phase}",
                            person_id="user_1",
                        )
                except Exception:
                    pass

            # T-313：夜间相位切换时联动 InteroceptionAgent 降低 recovery_urgency（夜间是恢复窗口）
            if next_phase == "night":
                try:
                    from host_brain.tribe.interoception_agent import get_interoception_agent  # noqa: PLC0415
                    _intero = get_interoception_agent()
                    if hasattr(_intero, "reduce_recovery_urgency"):
                        _intero.reduce_recovery_urgency(
                            source="circadian_night_phase",
                            relief=0.40,
                            ts_ms=int(profile.get("ts_ms") or now_ms()),
                        )
                except Exception:
                    pass
                # T-361：夜间高睡眠压力（sleep_pressure>=0.70）时联动 PainRewardAgent 记录疲惫痛苦
                # 极度睡眠压力是躯体痛苦（强烈的困倦感是一种身体预算透支的痛苦）
                _sleep_pressure = float(profile.get("sleep_pressure") or 0.0)
                if _sleep_pressure >= 0.70:
                    try:
                        from host_brain.tribe.pain_reward_agent import get_pain_reward_agent  # noqa: PLC0415
                        _pr = get_pain_reward_agent()
                        if hasattr(_pr, "record_pain"):
                            _sleep_pain = round(min(0.30, (_sleep_pressure - 0.70) * 1.00), 4)
                            _pr.record_pain(
                                source=f"circadian_high_sleep_pressure:{round(_sleep_pressure, 3)}",
                                intensity=_sleep_pain,
                                metadata={
                                    "sleep_pressure": round(_sleep_pressure, 4),
                                    "local_hour": profile.get("local_hour"),
                                    "phase": next_phase,
                                },
                            )
                    except Exception:
                        pass
                # T-376：夜间低睡眠压力（sleep_pressure<0.30，深度睡眠状态）时联动 InteroceptionAgent 降低 recovery_urgency
                # 深度睡眠是最强的身体预算恢复信号，应大幅释放内在压力
                if _sleep_pressure < 0.30:
                    try:
                        from host_brain.tribe.interoception_agent import get_interoception_agent  # noqa: PLC0415
                        _intero_deep = get_interoception_agent()
                        if hasattr(_intero_deep, "reduce_recovery_urgency"):
                            _deep_relief = round(min(0.55, (0.30 - _sleep_pressure) * 1.83), 4)
                            _intero_deep.reduce_recovery_urgency(
                                source=f"circadian_deep_sleep:{round(_sleep_pressure, 3)}",
                                relief=_deep_relief,
                                ts_ms=int(profile.get("ts_ms") or now_ms()),
                            )
                    except Exception:
                        pass

            # T-314：夜间/黄昏相位切换时联动 LimbicAgent 降低唤醒（夜间应降低情绪激活）
            if next_phase in {"night", "dusk"}:
                try:
                    from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415
                    _limbic = get_limbic_agent()
                    if hasattr(_limbic, "drive_emotion_metabolism"):
                        _arousal_drop = -0.18 if next_phase == "night" else -0.10
                        _limbic.drive_emotion_metabolism(
                            valence=0.0,
                            arousal=_arousal_drop,
                            dominance=0.0,
                            blend_weight=0.15,
                            event_kind=f"circadian_phase:{next_phase}",
                            source="circadian_agent",
                        )
                except Exception:
                    pass

        self._last_phase = next_phase
        return prev_state

    # ── 昼夜节律情绪提示（A 级升级）─────────────────────────────────────────

    def generate_circadian_mood_hint(
        self,
        *,
        circadian_phase: str = "day",
        energy_level: float = 0.8,
        sleep_pressure: float = 0.2,
        current_hour: int = 12,
    ) -> str | None:
        """根据昼夜相位和能量水平生成时段感知叙事，注入 Prompt Phase XIII 情绪层。

        触发条件：energy_level < 0.3 或 sleep_pressure > 0.6（状态显著）。
        调用：Flash 小模型。
        开关：CIRCADIAN_MOOD_HINT_ENABLED（默认 false）。

        Returns:
            时段感知描述字符串，或 None（未触发/失败）。
        """
        _significant = float(energy_level or 0.8) < 0.3 or float(sleep_pressure or 0.2) > 0.6
        if not _significant:
            return None

        import logging as _logging  # noqa: PLC0415
        _log = _logging.getLogger("Natsume.tribe.circadian_agent")
        try:
            from host_brain.app.prompt_template_store import resolve_prompt_template_json  # noqa: PLC0415
            from host_brain.app.provider_factory import build_llm_provider  # noqa: PLC0415

            _tpl = resolve_prompt_template_json("circadian_agent.circadian_mood_hint") or {}
            _system_prompt = str(_tpl.get("system_prompt") or "")
            _user_tpl = str(_tpl.get("user_prompt_template") or "")
            if not _system_prompt or not _user_tpl:
                raise ValueError("circadian_agent.circadian_mood_hint 模板未配置")

            _user_prompt = _user_tpl.format(
                circadian_phase=str(circadian_phase or "day"),
                energy_level=round(float(energy_level), 3),
                sleep_pressure=round(float(sleep_pressure), 3),
                current_hour=int(current_hour),
            )
            _provider = build_llm_provider(
                provider_name="gemini",
                model_id="gemini-2.0-flash",
                timeout_ms=1500,
                agent_role="circadian_narrator",
            )
            _raw = _provider.complete_sync(
                system_prompt=_system_prompt,
                user_prompt=_user_prompt,
                max_tokens=32,
                temperature=0.2,
            )
            import json as _json, re as _re  # noqa: PLC0415, E401
            _match = _re.search(r"\{.*\}", str(_raw or ""), _re.DOTALL)
            _parsed = _json.loads(_match.group()) if _match else {}
            _hint = str(_parsed.get("circadian_hint") or "").strip()[:40]
            if _hint:
                _log.debug("CircadianAgent.generate_circadian_mood_hint: %s", _hint)
                try:
                    self.state_registry.set_state(
                        "circadian_agent",
                        {"last_circadian_hint": _hint},
                        writer="circadian_agent",
                    )
                except Exception:
                    pass
            return _hint or None
        except Exception as exc:
            _log.debug("CircadianAgent.generate_circadian_mood_hint 失败（降级）: %s", exc)
            return None

    def export_runtime_state(self) -> dict[str, Any]:
        self.refresh_state(source="export_runtime_state")
        snap = self.state_registry.get_state("circadian_agent") or {}
        state = dict(snap.get("state") or {})
        return {
            "phase": str(state.get("phase") or "day"),
            "local_hour": int(state.get("local_hour") or 12),
            "phase_energy_baseline": float(state.get("phase_energy_baseline") or 0.8),
            "proactive_multiplier": float(state.get("proactive_multiplier") or 1.0),
            "recovery_bias": float(state.get("recovery_bias") or 0.3),
            "sleep_pressure": float(state.get("sleep_pressure") or 0.2),
            "response_latency_ms": int(state.get("response_latency_ms") or 400),
            "last_updated_ts_ms": int(state.get("ts_ms") or now_ms()),
        }

    async def _on_mode_transition(self, event: Any) -> None:
        payload = dict(getattr(event, "payload", None) or {})
        ts_ms = int(getattr(event, "ts_ms", 0) or now_ms())
        prev = await asyncio.to_thread(self.refresh_state, source="mode_transition", ts_ms=ts_ms)
        prev["last_mode_transition"] = {
            "from_mode": str(payload.get("from_mode") or "").strip().lower() or None,
            "to_mode": str(payload.get("to_mode") or "").strip().lower() or None,
            "reason": str(payload.get("reason") or "").strip() or None,
            "ts_ms": ts_ms,
        }
        await asyncio.to_thread(self.state_registry.set_state, "circadian_agent", prev, writer="circadian_agent")

    async def _on_channel_message_received(self, event: Any) -> None:
        ts_ms = int(getattr(event, "ts_ms", 0) or now_ms())
        prev = await asyncio.to_thread(self.state_registry.get_state, "circadian_agent") or {}
        state = dict(prev.get("state") or {})
        state["last_external_stimulus_ts_ms"] = ts_ms
        await asyncio.to_thread(self.state_registry.set_state, "circadian_agent", state, writer="circadian_agent")

    async def _on_voice_pipeline_started(self, event: Any) -> None:
        ts_ms = int(getattr(event, "ts_ms", 0) or now_ms())
        prev = await asyncio.to_thread(self.state_registry.get_state, "circadian_agent") or {}
        state = dict(prev.get("state") or {})
        state["voice_pipeline_active"] = True
        state["voice_pipeline_started_ts_ms"] = ts_ms
        await asyncio.to_thread(self.state_registry.set_state, "circadian_agent", state, writer="circadian_agent")

    async def _on_voice_pipeline_ended(self, event: Any) -> None:
        ts_ms = int(getattr(event, "ts_ms", 0) or now_ms())
        prev = await asyncio.to_thread(self.state_registry.get_state, "circadian_agent") or {}
        state = dict(prev.get("state") or {})
        state["voice_pipeline_active"] = False
        state["voice_pipeline_ended_ts_ms"] = ts_ms
        await asyncio.to_thread(self.state_registry.set_state, "circadian_agent", state, writer="circadian_agent")

    def tick_emotion_baseline(
        self,
        *,
        source: str = "circadian_scheduler",
    ) -> dict[str, Any]:
        """D6: 按昼夜四相位驱动 LimbicAgent 情绪基线调制。

        每次 emotion_decay_tick 时由 NatsumeScheduler 调用，
        将当前相位的情绪基线偏置注入 LimbicAgent.tick_emotion_decay()，
        实现昼夜节律对情绪的持续调制：
        - 日间（day）：活力基线，轻度正向偏置
        - 黄昏（dusk）：平静基线，轻度负向偏置
        - 夜间（night）：低唤醒基线，情绪趋向平静
        - 黎明（dawn）：恢复基线，轻度正向偏置

        Returns:
            {"phase": str, "decay_result": dict, "baseline_applied": bool}
        """
        import logging as _logging
        _log = _logging.getLogger("Natsume.tribe.circadian_agent")
        try:
            profile = self.export_runtime_state()
            phase = str(profile.get("phase") or "day").strip().lower()
            sleep_pressure = float(profile.get("sleep_pressure") or 0.2)

            # 调用 LimbicAgent.tick_emotion_decay()，传入昼夜相位信息
            try:
                from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: PLC0415
                _limbic = get_limbic_agent()
                if hasattr(_limbic, "tick_emotion_decay"):
                    decay_result = _limbic.tick_emotion_decay(
                        source=f"circadian:{phase}",
                        circadian_phase=phase,
                        sleep_pressure=sleep_pressure,
                    )
                    _log.debug(
                        "CircadianAgent.tick_emotion_baseline: phase=%s sleep_pressure=%.2f "
                        "decay_rate=%.4f tag=%s",
                        phase, sleep_pressure,
                        float(decay_result.get("effective_decay_rate") or 0.0),
                        decay_result.get("emotion_tag"),
                    )
                    return {
                        "phase": phase,
                        "sleep_pressure": round(sleep_pressure, 3),
                        "decay_result": decay_result,
                        "baseline_applied": bool(decay_result.get("decayed")),
                    }
            except Exception as _limbic_exc:
                _log.debug("CircadianAgent.tick_emotion_baseline: LimbicAgent 调用失败: %s", _limbic_exc)

            return {"phase": phase, "baseline_applied": False, "reason": "limbic_unavailable"}
        except Exception as exc:
            import logging  # noqa: PLC0415
            logging.getLogger("Natsume.tribe.circadian_agent").warning(
                "CircadianAgent.tick_emotion_baseline 失败（降级）: %s", exc
            )
            return {"baseline_applied": False, "error": str(exc)}


def get_circadian_agent() -> CircadianAgent:
    global _CIRCADIAN_AGENT
    if _CIRCADIAN_AGENT is None:
        _CIRCADIAN_AGENT = CircadianAgent()
    return _CIRCADIAN_AGENT


def set_circadian_agent(agent: CircadianAgent | None) -> None:
    global _CIRCADIAN_AGENT
    _CIRCADIAN_AGENT = agent


__all__ = ["CircadianAgent", "get_circadian_agent", "set_circadian_agent"]

# ── 跨模块引用（供测试 monkeypatch 使用）────────────────────────────────────
from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: E402, F401
from host_brain.tribe.pain_reward_agent import get_pain_reward_agent  # noqa: E402, F401
from host_brain.tribe.ans_agent import get_ans_agent  # noqa: E402, F401
from host_brain.tribe.interoception_agent import get_interoception_agent  # noqa: E402, F401
from host_brain.tribe.endocrine_agent import get_endocrine_agent  # noqa: E402, F401
from host_brain.tribe.glymphatic_agent import get_glymphatic_agent  # noqa: E402, F401
